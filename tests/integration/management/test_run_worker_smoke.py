"""Smoke test for the Celery worker entrypoint.

``src/management/run_worker.py`` is marked Dormant — it is a CLI entry
that the request path never imports. The dormant policy commits us to
exactly one verification: ``python -m src.management.run_worker
--help`` exits 0 and prints the Celery CLI usage banner. That proves
the module imports cleanly (settings bind, logging configures, the
Celery app constructs, task autodiscover does not blow up) without
needing a broker or worker pool.

Skips if Celery is not installed so the test does not turn red in
slim installs that omit ``requirements/celery.txt``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

celery = pytest.importorskip("celery")  # noqa: F841 — gate only

_REPO_ROOT: Path = Path(__file__).resolve().parents[3]


def test_run_worker_help_exits_clean() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "src.management.run_worker", "--help"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"run_worker --help exited {result.returncode}; "
        f"stdout={result.stdout!r}; stderr={result.stderr!r}"
    )
    # Celery's click-based CLI prints a "Usage:" banner to stdout.
    assert "Usage:" in result.stdout, (
        f"run_worker --help did not print a usage banner; "
        f"stdout={result.stdout!r}"
    )
