"""Thin backward-compatible wrapper over the provider registry.

The registry (:mod:`auto_dm.llm.registry`) is now the source of truth for
providers. This function is kept so older callers (and the ``auto_dm.llm``
public API) that hand-build an :class:`LLMConfig` and ask for the matching
adapter keep working. It resolves the provider id via the registry and
delegates to the spec's factory.

Unlike :func:`auto_dm.llm.registry.build_provider`, this does **not**
validate the model against the allowlist or set ``base_url`` from the
spec — it passes the config through unchanged, preserving the legacy
``AUTO_DM_BASE_URL`` override path used by the global provider.
"""
from __future__ import annotations

from auto_dm.llm.base import LLMConfig
from auto_dm.llm.registry import get_spec


def get_provider(config: LLMConfig):
    """Return the adapter for the provider named in ``config``.

    Raises ``ValueError`` (pt-BR message) when the provider id is unknown.
    """
    spec = get_spec(config.name)
    return spec.factory(config)
