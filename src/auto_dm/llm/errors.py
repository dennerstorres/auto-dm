"""Normalized provider errors.

Provider SDKs each raise their own exception families. We translate the
ones the web layer needs to act on (auth failure, rate limiting, timeout,
transient unavailability) into a single small hierarchy defined here, so
callers (routes, the BYOK resolver) can branch on a provider-agnostic
type rather than importing every SDK.

Security rule: these exceptions carry a fixed, generic ``message`` and
the provider id only — never the original SDK payload, request body,
response body, or headers. SDK error strings frequently embed those, and
leaking them into API responses or logs is how credentials and PII
escape. The original exception is chained via ``__cause__`` for
debugging but is never rendered by default.
"""
from __future__ import annotations


class ProviderError(Exception):
    """Base class for all normalized provider errors.

    Attributes:
        provider: The provider id (e.g. ``"minimax"``) the call failed on.
    """

    #: A short, human-readable label for the failure category, used by the
    #: web layer to pick a status code / message. Subclasses override.
    code: str = "provider_error"

    def __init__(self, provider: str, message: str = "") -> None:
        self.provider = provider
        super().__init__(message or self._default_message(provider))

    @staticmethod
    def _default_message(provider: str) -> str:
        return f"{provider} request failed"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.args[0] if self.args else self._default_message(self.provider)


class ProviderAuthError(ProviderError):
    """The API key was rejected, revoked, or missing (HTTP 401/403).

    For BYOK this is the signal that the stored credential is bad: the
    resolver/endpoint marks it ``invalid`` and tells the user to update
    it. Crucially it must NOT fall back to the platform's global key.
    """

    code = "provider_auth"

    @staticmethod
    def _default_message(provider: str) -> str:
        return f"{provider} authentication failed"


class ProviderRateLimitError(ProviderError):
    """The provider is throttling requests (HTTP 429)."""

    code = "provider_rate_limit"

    @staticmethod
    def _default_message(_provider: str) -> str:
        return "provider rate limit reached"


class ProviderTimeoutError(ProviderError):
    """The request to the provider timed out."""

    code = "provider_timeout"

    @staticmethod
    def _default_message(_provider: str) -> str:
        return "provider request timed out"


class ProviderUnavailableError(ProviderError):
    """The provider could not be reached or returned a server error (5xx)."""

    code = "provider_unavailable"

    @staticmethod
    def _default_message(_provider: str) -> str:
        return "provider unavailable"
