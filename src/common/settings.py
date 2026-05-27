"""Application settings — extends ``CoreSettings`` with project specifics.

``CoreSettings`` (in ``src.core.settings``) owns every infrastructure knob:
database, AWS, Redis, logging, resilience defaults, the API audit log, and
the security/CORS toggles read by the app factory. This subclass is where
you add *your* application's fields — keep infra settings in core.

Both the request-scoped ``AsyncSession`` and the ``api_log`` Postgres
backend resolve the same engine via
``core.utils.db.get_async_engine(settings.db_dsn)`` — one shared pool.

Settings priority (highest → lowest): AWS Secrets Manager → environment
variables → ``.env`` file → field defaults. See ``CoreSettings`` for the
source wiring.
"""

from __future__ import annotations

from src.core.settings import CoreSettings


class Settings(CoreSettings):
    """Project-level settings — the single instance is exported as ``settings``."""

    # ── Application identity ──────────────────────────────────────────
    app_name: str = "FastAPI Boilerplate"
    app_version: str = "0.1.0"

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


#: Process-wide instance. Importers should always read configuration via
#: this singleton (or via ``core.runtime.get_settings()`` if running inside
#: core itself, which must never import ``src.common``).
settings = Settings()
