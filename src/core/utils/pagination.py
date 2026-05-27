"""``PageParams`` — FastAPI dependency carrying validated ``page``/``size``.

Single source of truth for the page-size bounds applied to every
paginated list endpoint. Routes depend on :func:`page_params`; the
returned :class:`PageParams` exposes ``offset`` and ``limit`` properties
so repositories (which already speak ``limit``/``offset``) need no
conversion logic at the call site.

Bounds:

* ``page`` — 1-indexed, default ``1`` (``ge=1``; no upper bound — the
  practical ceiling is ``total_pages`` from the paginated response).
* ``size`` — default ``20``, max ``200``, min ``1``. The max is chosen
  so a single response body never exceeds a few hundred KB even when
  every row carries deep nested payloads.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Query

DEFAULT_PAGE = 1
DEFAULT_SIZE = 20
MAX_SIZE = 200


@dataclass(frozen=True)
class PageParams:
    """Validated page / size pair with ``offset`` and ``limit`` accessors."""

    page: int
    size: int

    @property
    def offset(self) -> int:
        """Return the row offset for the current page.

        Returns:
            ``(page - 1) * size`` — used directly as ``OFFSET`` in
            SQL queries.
        """
        return (self.page - 1) * self.size

    @property
    def limit(self) -> int:
        """Return the row cap for the current page.

        Returns:
            ``size`` — alias for ``LIMIT`` in SQL queries.
        """
        return self.size


def page_params(
    page: int = Query(
        default=DEFAULT_PAGE,
        ge=1,
        description="1-indexed page number (default 1).",
    ),
    size: int = Query(
        default=DEFAULT_SIZE,
        ge=1,
        le=MAX_SIZE,
        description=f"Items per page (default {DEFAULT_SIZE}, max {MAX_SIZE}).",
    ),
) -> PageParams:
    """Build a validated :class:`PageParams` from query-string inputs.

    Designed for use as a FastAPI dependency via
    ``Depends(page_params)``; the ``Query(...)`` defaults enforce the
    documented bounds before the handler is called.

    Args:
        page: 1-indexed page number from the query string.
        size: Page size from the query string, capped at :data:`MAX_SIZE`.

    Returns:
        The matching ``PageParams`` instance with both fields bounded.
    """
    return PageParams(page=page, size=size)


__all__ = ["DEFAULT_PAGE", "DEFAULT_SIZE", "MAX_SIZE", "PageParams", "page_params"]
