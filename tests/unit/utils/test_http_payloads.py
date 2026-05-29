"""Unit tests for ``summarise_body_for_audit`` / ``serialize_error_body``."""

from __future__ import annotations

import pytest

from src.core.utils.http_payloads import (
    aiohttp,
    serialize_error_body,
    summarise_body_for_audit,
)


def test_summarise_body_passes_through_none_and_dict() -> None:
    """Non-binary values are returned unchanged."""
    assert summarise_body_for_audit(None) is None
    assert summarise_body_for_audit({"k": "v"}) == {"k": "v"}


def test_summarise_body_summarises_bytes() -> None:
    """Raw bytes become a JSON-safe size summary."""
    out = summarise_body_for_audit(b"x" * 42)
    assert out == {"__bytes__": True, "size_bytes": 42}


@pytest.mark.skipif(aiohttp is None, reason="aiohttp not installed")
def test_summarise_body_summarises_formdata() -> None:
    """``aiohttp.FormData`` collapses to a multipart summary."""
    form = aiohttp.FormData()
    form.add_field("name", "alice")
    form.add_field("doc", b"PDFBYTES", filename="x.pdf", content_type="application/pdf")
    out = summarise_body_for_audit(form)
    assert isinstance(out, dict)
    assert out["__multipart__"] is True
    fields = {f["name"]: f for f in out["fields"]}
    assert fields["name"]["value"] == "alice"
    assert fields["doc"]["filename"] == "x.pdf"
    assert fields["doc"]["size_bytes"] == 8


def test_serialize_error_body_handles_dict_str_none() -> None:
    """Dicts json-encode; strings pass through; None stays None."""
    assert serialize_error_body(None) is None
    assert serialize_error_body("raw") == "raw"
    out = serialize_error_body({"err": 1})
    assert out is not None and '"err": 1' in out


def test_serialize_error_body_falls_back_to_str_on_failure() -> None:
    """Unserialisable objects degrade to ``str(body)`` rather than raise."""

    class _NotJSON:
        def __repr__(self) -> str:
            return "<not-json>"

    out = serialize_error_body(_NotJSON())
    assert out is not None
    # default=str in json.dumps will succeed by calling str() — so this
    # path actually returns the JSON-quoted form; verify it didn't raise.
    assert "not-json" in out
