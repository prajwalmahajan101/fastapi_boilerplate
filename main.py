"""Root-level entrypoint — runs the FastAPI app via Uvicorn.

Debug mode (auto-reload + ``debug`` log level) is enabled when
``APP_ENVIRONMENT`` is ``dev`` or ``local``. Any other value runs the
server in production mode.
"""

from __future__ import annotations

import os

import uvicorn

from src.app import app  # noqa: F401 — surfaces import errors at startup

DEBUG_ENVIRONMENTS = {"dev", "local"}


def main() -> None:
    """Boot Uvicorn with environment-aware debug settings."""
    environment = os.getenv("APP_ENVIRONMENT", "dev").strip().lower()
    debug = environment in DEBUG_ENVIRONMENTS

    uvicorn.run(
        "src.app:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=debug,
        log_level="debug" if debug else "info",
    )


if __name__ == "__main__":
    main()
