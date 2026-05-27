"""Per-service resilience configuration registry.

Merges ``CoreSettings.resilience_defaults`` (process-wide defaults) with
optional per-service overrides registered via ``register_service``.
``get_breaker(name)`` lazily creates a breaker through the global
circuit-breaker provider.
"""

from __future__ import annotations

import asyncio
import copy
import importlib
import logging
from typing import Any

from src.core.resilience.circuit_breaker.base import (
    BaseCircuitBreaker,
    CircuitBreakerConfig,
)
from src.core.resilience.circuit_breaker.provider import get_registry
from src.core.runtime import get_settings

logger = logging.getLogger(__name__)


def _resolve_class(dotted_path: str) -> type:
    """Import a class from a ``"module.path.ClassName"`` string.

    Used when ``retry_on`` / ``excluded_exceptions`` are configured as
    dotted strings in settings — runtime resolution defers import-time
    side effects.

    Args:
        dotted_path: A ``module.sub.Class`` import string.

    Returns:
        The resolved class object.
    """
    module_path, class_name = dotted_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


class ResilienceRegistry:
    """Per-service circuit-breaker config + retry config."""

    def __init__(self) -> None:
        """Initialise the ResilienceRegistry."""
        self._breakers: dict[str, BaseCircuitBreaker] = {}
        self._services: dict[str, dict[str, Any]] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    def register_service(self, service_name: str, config: dict[str, Any]) -> None:
        """Override defaults for ``service_name``.

        Must be called before the breaker for that service is first
        used — once a breaker is materialised, its config is locked in.

        Args:
            service_name: Service tag (e.g. ``"bhn_api"``).
            config: Mapping with optional ``"circuit_breaker"`` and
                ``"retry"`` sub-dicts that override the defaults from
                settings.

        Raises:
            ValueError: A breaker for ``service_name`` already exists.
        """
        if service_name in self._breakers:
            raise ValueError(
                f"Cannot register '{service_name}': breaker already created. "
                "Register services before the first call."
            )
        self._services[service_name] = config

    def get_config(self, service_name: str) -> dict[str, Any]:
        """Return the effective resilience config for ``service_name``.

        Merges the global ``resilience_defaults`` with any per-service
        overrides registered via :meth:`register_service`. Resolves
        ``retry_on`` from dotted strings to actual exception classes.

        Args:
            service_name: Service tag (e.g. ``"bhn_api"``).

        Returns:
            Effective config dict ready to feed to retry / breaker.
        """
        defaults = copy.deepcopy(get_settings().resilience_defaults)
        overrides = self._services.get(service_name, {})
        for section in ("circuit_breaker", "retry"):
            if section in overrides:
                defaults.setdefault(section, {}).update(overrides[section])

        retry_on = defaults.get("retry", {}).get("retry_on")
        if retry_on and isinstance(retry_on[0], str):
            defaults["retry"]["retry_on"] = tuple(_resolve_class(p) for p in retry_on)
        return defaults

    async def get_breaker(self, service_name: str) -> BaseCircuitBreaker:
        """Get (or create) the circuit breaker for ``service_name``.

        Resolves the config on first call and locks it into the
        breaker so subsequent registrations for the same name fail
        loudly via :meth:`register_service`.

        Args:
            service_name: Service tag (e.g. ``"bhn_api"``).

        Returns:
            The materialised breaker.
        """
        existing = self._breakers.get(service_name)
        if existing is not None:
            return existing

        async with self._lock:
            if service_name in self._breakers:
                return self._breakers[service_name]

            cb_config = self.get_config(service_name).get("circuit_breaker", {})
            excluded = cb_config.get("excluded_exceptions", ())
            if excluded and isinstance(excluded[0], str):
                excluded = tuple(_resolve_class(p) for p in excluded)
            config = CircuitBreakerConfig(
                failure_threshold=cb_config.get("failure_threshold", 5),
                success_threshold=cb_config.get("success_threshold", 2),
                recovery_timeout=cb_config.get("recovery_timeout", 30.0),
                excluded_exceptions=tuple(excluded),
            )
            cb_registry = await get_registry()
            breaker = await cb_registry.get_or_create(service_name, config)
            self._breakers[service_name] = breaker
            return breaker


#: Process-wide singleton.
resilience_registry = ResilienceRegistry()
