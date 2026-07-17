"""Server-side token prices for the supported LLM model catalog.

Prices are USD per one million tokens and were reviewed against the official
provider price lists on 2026-07-17. Promotional, batch, cache-hit and priority
rates are intentionally excluded: normal synchronous gameplay requests use
standard uncached pricing, and a stable standard price is less surprising in
historical admin reports than a temporary promotion.

Unknown or custom legacy models deliberately return ``None`` so callers can
fall back to the operator-configured generic rate.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone


@dataclass(frozen=True)
class TokenPrice:
    input_per_million_usd: float
    output_per_million_usd: float


MODEL_PRICING: dict[tuple[str, str], TokenPrice] = {
    ("minimax", "MiniMax-M3"): TokenPrice(0.30, 1.20),
    ("minimax", "MiniMax-M2.7-highspeed"): TokenPrice(0.60, 2.40),
    ("openai", "gpt-5.4-mini"): TokenPrice(0.75, 4.50),
    ("openai", "gpt-5.4"): TokenPrice(2.50, 15.00),
    ("openai", "gpt-5.5"): TokenPrice(5.00, 30.00),
    ("anthropic", "claude-haiku-4-5"): TokenPrice(1.00, 5.00),
    ("anthropic", "claude-sonnet-5"): TokenPrice(3.00, 15.00),
    ("anthropic", "claude-opus-4-8"): TokenPrice(5.00, 25.00),
    ("gemini", "gemini-3.5-flash"): TokenPrice(1.50, 9.00),
    ("gemini", "gemini-3.1-flash-lite"): TokenPrice(0.25, 1.50),
    ("deepseek", "deepseek-v4-flash"): TokenPrice(0.14, 0.28),
    ("deepseek", "deepseek-v4-pro"): TokenPrice(0.435, 0.87),
}

_SONNET_5_INTRO_PRICE = TokenPrice(2.00, 10.00)
_SONNET_5_INTRO_END = date(2026, 8, 31)


def get_token_price(
    provider: str, model: str, *, as_of: date | None = None,
) -> TokenPrice | None:
    """Return the standard price active on ``as_of``.

    ``as_of`` primarily makes time-limited official pricing deterministic in
    tests. Normal callers use the current UTC date; each UsageEvent persists
    the resulting cost, so historical totals never change later.
    """
    key = ((provider or "").strip().lower(), (model or "").strip())
    effective_date = as_of or datetime.now(timezone.utc).date()
    if key == ("anthropic", "claude-sonnet-5") and effective_date <= _SONNET_5_INTRO_END:
        return _SONNET_5_INTRO_PRICE
    return MODEL_PRICING.get(key)
