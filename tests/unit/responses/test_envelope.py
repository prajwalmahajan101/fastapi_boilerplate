"""Unit tests for the response envelope factories.

These exercise ``src.core.responses`` in isolation — no app, no I/O — so
they pin the wire contract every endpoint depends on: the ``success /
message / data / errors / request_id`` shape, the success/error field
locking, and the pagination arithmetic.
"""

from __future__ import annotations

import json

from src.core.responses import ErrorResponse, PaginatedResponse, SuccessResponse


def _body(response) -> dict:
    """Decode a ``JSONResponse`` body into a dict.

    Args:
        response: The ``JSONResponse`` returned by an envelope factory.

    Returns:
        The parsed JSON body.
    """
    return json.loads(response.body)


def test_success_response_shape() -> None:
    """A success envelope carries data, never an errors array."""
    response = SuccessResponse(data={"id": 1}, message="ok")
    assert response.status_code == 200
    body = _body(response)
    assert body["success"] is True
    assert body["message"] == "ok"
    assert body["data"] == {"id": 1}
    assert body["errors"] is None


def test_success_response_custom_status() -> None:
    """The factory honours an explicit status code (e.g. 201 Created)."""
    response = SuccessResponse(data=None, message="created", status_code=201)
    assert response.status_code == 201
    assert _body(response)["success"] is True


def test_error_response_shape() -> None:
    """An error envelope carries the errors array and never data."""
    response = ErrorResponse(
        "bad request",
        errors=[{"code": "VALIDATION_ERROR", "message": "name is required"}],
        status_code=422,
    )
    assert response.status_code == 422
    body = _body(response)
    assert body["success"] is False
    assert body["data"] is None
    assert body["errors"][0]["code"] == "VALIDATION_ERROR"
    assert body["errors"][0]["field"] is None


def test_paginated_response_math() -> None:
    """Pagination flags/totals derive from page, size, and total_count."""
    response = PaginatedResponse(
        [{"id": 2}],
        page=2,
        size=1,
        total_count=3,
    )
    data = _body(response)["data"]
    assert data["list"] == [{"id": 2}]
    assert data["current_page"] == 2
    assert data["total_pages"] == 3
    assert data["has_prev"] is True
    assert data["has_next"] is True
