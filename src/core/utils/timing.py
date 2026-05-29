"""``perf_timer`` — named wall-clock timer for audit ``elapsed_ms`` fields.

Several outbound call sites repeat the same idiom::

    start = time.perf_counter()
    try:
        ...
    except SomeError:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        ...
    elapsed_ms = int((time.perf_counter() - start) * 1000)

The shape is tiny but the duplication adds noise and is easy to get
subtly wrong (units, rounding, off-by-one). :func:`perf_timer` names the
intent and centralises the multiplier::

    with perf_timer() as t:
        ...
    audit(elapsed_ms=t.elapsed_ms)

Reads ``perf_counter`` (monotonic), so the value is safe to use as a
duration even across system-clock adjustments.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator


@dataclass
class PerfTimer:
    """Holder for the timer's running and final elapsed values.

    Attributes:
        elapsed_ms: Milliseconds elapsed from ``__enter__`` until either
            the current moment (while the block is open) or the moment
            the block exited (after it closes). Always an ``int`` —
            audit columns are integer milliseconds.
    """

    _start: float = field(default_factory=time.perf_counter)
    _end: float | None = None

    @property
    def elapsed_ms(self) -> float:
        """Milliseconds elapsed since the block entered.

        Returns:
            Floating-point milliseconds with sub-ms precision (matters
            for fast handlers — cache-hit reads, 304 paths, in-memory
            fallbacks). While the block is open, the value is the
            running elapsed; after it closes, it is frozen at the close
            time.
        """
        end = self._end if self._end is not None else time.perf_counter()
        return (end - self._start) * 1000

    def stop(self) -> None:
        """Freeze ``elapsed_ms`` at the current monotonic instant.

        Called by :func:`perf_timer`'s ``finally`` so the contextmanager
        does not have to reach into ``_end`` from outside the class.
        Idempotent: subsequent calls overwrite the freeze time, which
        is fine for the single-shot context-manager use case.
        """
        self._end = time.perf_counter()


@contextmanager
def perf_timer() -> Iterator[PerfTimer]:
    """Yield a :class:`PerfTimer` whose ``.elapsed_ms`` is valid mid-block and after.

    Yields:
        A :class:`PerfTimer` bound to the entry instant. The block can
        read ``.elapsed_ms`` at any point — useful for stamping the
        success-side audit row inside the try and the failure-side row
        in the except, both off the same timer.
    """
    timer = PerfTimer()
    try:
        yield timer
    finally:
        timer.stop()


__all__ = ["perf_timer"]
