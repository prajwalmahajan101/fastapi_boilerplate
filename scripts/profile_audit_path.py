#!/usr/bin/env python3
"""Profile the per-call overhead of ``capture_and_dispatch`` (the audit hot path).

Runs N synthetic captures against a no-op repository, measures the
wrapper's wall-clock overhead, and prints p50 / p95 / p99 in
microseconds. The numbers are a baseline + regression detector:
re-run after touching anything under ``src/core/api_log/`` and compare
against the recorded baseline at the bottom of this file.

Exits non-zero when p99 exceeds ``--max-p99-us`` (default ``5000`` =
5 ms) so the script doubles as a CI guard for accidental
slowdown-by-import.

Run modes::

    python scripts/profile_audit_path.py                       # default N=2000
    python scripts/profile_audit_path.py --iterations 10000    # tighter percentiles
    python scripts/profile_audit_path.py --max-p99-us 3000     # tighter regression bound

Baseline (recorded 2026-05-29 on a clean main with no contention,
Python 3.12, single thread, builder stub closes the persist coroutine):

    iterations=2000  p50=3.4us  p95=4.8us  p99=5.9us

Treat any p99 > 2x baseline as a regression worth investigating.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path
from statistics import quantiles


def _percentile(samples: list[float], pct: float) -> float:
    """Return the *pct*-th percentile of *samples* (e.g. ``99.0``)."""
    if not samples:
        return 0.0
    # ``quantiles(n=100)`` returns 99 cut-points (1st through 99th percentile).
    cuts = quantiles(samples, n=100, method="inclusive")
    idx = max(0, min(98, int(pct) - 1))
    return cuts[idx]


async def run_profile(iterations: int) -> list[float]:
    """Return per-call microsecond overhead samples from N synthetic captures.

    Args:
        iterations: Number of synthetic captures to time.

    Returns:
        List of ``iterations`` microsecond values.
    """
    # Bind settings just like the conftest does — required because
    # capture_and_dispatch eventually reads sanitiser config.
    from src.common.settings import settings  # noqa: PLC0415
    from src.core.api_log import dispatch as dispatch_mod  # noqa: PLC0415
    from src.core.runtime import configure  # noqa: PLC0415

    configure(settings)

    # Replace the queue submitter so we measure only capture overhead,
    # not the async event-loop scheduling of the persist task. Close
    # the unawaited coroutine to silence the RuntimeWarning.
    def _stub_submit(coro: object) -> None:
        try:
            coro.close()  # type: ignore[union-attr]
        except AttributeError:
            pass

    dispatch_mod.fire_and_forget = _stub_submit  # type: ignore[assignment]

    async def noop_handler() -> str:
        return "ok"

    def trivial_builder(_state: dispatch_mod.CaptureState) -> object:
        # Return a sentinel — fire_and_forget is stubbed so the value
        # never actually hits persist_log.
        return _state

    # Warm-up to amortise import & JIT cost.
    for _ in range(50):
        await dispatch_mod.capture_and_dispatch(
            noop_handler,
            (),
            {},
            trivial_builder,
        )

    samples_us: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        await dispatch_mod.capture_and_dispatch(
            noop_handler,
            (),
            {},
            trivial_builder,
        )
        samples_us.append((time.perf_counter() - start) * 1_000_000)
    return samples_us


def main() -> int:
    """Drive the profiler from the command line.

    Returns:
        ``0`` when p99 is within the bound, ``1`` otherwise.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--iterations", type=int, default=2000)
    parser.add_argument(
        "--max-p99-us",
        type=float,
        default=5_000.0,
        help="Fail (exit 1) when measured p99 exceeds this many microseconds.",
    )
    args = parser.parse_args()

    # Make ``src`` importable when run directly from the repo root.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    samples = asyncio.run(run_profile(args.iterations))
    p50 = _percentile(samples, 50)
    p95 = _percentile(samples, 95)
    p99 = _percentile(samples, 99)
    print(
        f"capture_and_dispatch overhead (iterations={args.iterations}):"
        f" p50={p50:.1f}us p95={p95:.1f}us p99={p99:.1f}us"
    )
    if p99 > args.max_p99_us:
        print(
            f"\nREGRESSION: p99={p99:.1f}us exceeds bound "
            f"({args.max_p99_us:.0f}us). Investigate recent changes "
            "under src/core/api_log/ or src/core/utils/.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
