"""Application settings — extends ``CoreSettings`` with project specifics
and exposes per-environment profile subclasses.

``CoreSettings`` (in ``src.core.settings``) owns every infrastructure knob:
database, AWS, Redis, logging, resilience defaults, the API audit log, and
the security/CORS toggles read by the app factory. :class:`Settings` is
where you add *your* application's fields — keep infra settings in core.

Per-environment profiles
------------------------

The runtime profile is selected by the ``APP_ENV`` environment variable
(``local`` / ``dev`` / ``uat`` / ``test`` / ``prod``). Each profile is a
subclass of :class:`Settings` that may:

* tighten defaults that should differ from local development
  (``cors_enabled``, debug flags, …);
* declare a ``@model_validator(mode="after")`` that **fails fast at boot**
  if a required secret or host setting is missing.

Adding application fields stays unchanged — add them to :class:`Settings`
(the base) and every profile inherits. Override a default only when the
profile genuinely needs a different value.

Settings priority (highest → lowest): AWS Secrets Manager → environment
variables → ``.env`` file → field defaults. See ``CoreSettings`` for the
source wiring.
"""

from __future__ import annotations

import logging
import os

from pydantic import model_validator

from src.core.settings import CoreSettings

logger = logging.getLogger(__name__)


class Settings(CoreSettings):
    """Project-level settings — base for every per-environment profile.

    All application fields live here; profile subclasses below override
    defaults and add fail-fast validators only.
    """

    # ── Application identity ──────────────────────────────────────────
    app_name: str = "FastAPI Boilerplate"
    app_version: str = "1.0.0"

    # Override the inherited default — every other ``db_*`` knob is inherited.
    db_name: str = "app"

    # ── CORS ──────────────────────────────────────────────────────────
    # Off by default: a server-to-server API never needs CORS. Deployments
    # that serve a browser front-end opt in by setting ``CORS_ENABLED=true``
    # and explicitly listing origins / methods / headers.
    cors_enabled: bool = False
    cors_allow_origins: list[str] = []
    cors_allow_methods: list[str] = []
    cors_allow_headers: list[str] = []
    cors_allow_credentials: bool = False
    cors_excluded_prefixes: list[str] = []

    # ── Add your application fields below ──────────────────────────────
    # e.g. third-party API base URLs, feature flags, per-tenant knobs.
    # Validate cross-field invariants with a ``@model_validator`` so the
    # app fails fast at boot rather than on first request.


# ── Per-environment profiles ───────────────────────────────────────────


class LocalSettings(Settings):
    """Developer workstation profile — permissive defaults, no validators."""

    log_level: str = "DEBUG"


class TestSettings(Settings):
    """Pytest profile — isolated DB name, deterministic toggles."""

    db_name: str = "app_test"
    cors_enabled: bool = False
    metrics_middleware_enabled: bool = False
    api_log_backend: str = "noop"  # type: ignore[assignment]


class DevSettings(Settings):
    """Remote development / shared staging profile."""

    @model_validator(mode="after")
    def _validate_dev_required(self) -> "DevSettings":
        missing: list[str] = []
        if not os.getenv("POSTGRES_HOST") and self.db_host == "localhost":
            missing.append("DB_HOST")
        if missing:
            raise ValueError(
                "Dev profile requires explicit settings — missing: "
                + ", ".join(missing)
            )
        return self


class UatSettings(DevSettings):
    """User-acceptance-testing profile — production-shaped, dev-grade auditing."""

    log_level: str = "INFO"


class ProdSettings(Settings):
    """Production profile — fail fast on every required secret."""

    log_level: str = "INFO"
    openapi_docs_enabled: bool = False
    security_headers_enabled: bool = True

    @model_validator(mode="after")
    def _validate_prod_required(self) -> "ProdSettings":
        missing: list[str] = []
        if self.db_host == "localhost":
            missing.append("DB_HOST (must not be localhost in prod)")
        if missing:
            raise ValueError(
                "Prod profile requires explicit settings — missing: "
                + ", ".join(missing)
            )
        return self


_PROFILES: dict[str, type[Settings]] = {
    "local": LocalSettings,
    "dev": DevSettings,
    "uat": UatSettings,
    "test": TestSettings,
    "prod": ProdSettings,
}


def _build() -> Settings:
    """Instantiate the profile class matching ``APP_ENV`` (default: local).

    Returns:
        The concrete profile instance — every other module reads it via
        the module-level :data:`settings` singleton.
    """
    env = (os.getenv("APP_ENV") or "local").strip().lower()
    profile_cls = _PROFILES.get(env)
    if profile_cls is None:
        logger.warning(
            "APP_ENV=%r is not a known profile (%s); falling back to local.",
            env,
            sorted(_PROFILES),
        )
        profile_cls = LocalSettings
    return profile_cls()


#: Process-wide instance. Importers should always read configuration via
#: this singleton (or via ``core.runtime.get_settings()`` if running inside
#: core itself, which must never import ``src.common``).
settings = _build()
