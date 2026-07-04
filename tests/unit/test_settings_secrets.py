"""Unit tests that endpoint secrets are masked at rest (ISSUE-044).

`metrics_auth_token` — the shared secret for the `/metrics` scrape — must be a
`SecretStr` like the other endpoint secrets (`jwt_signing_key`,
`google_oauth_client_secret`), so it never leaks into a settings repr or a log
dump. Only `.get_secret_value()` returns the plaintext.
"""

from __future__ import annotations

from pydantic import SecretStr

from src.core.settings import CoreSettings


def test_metrics_auth_token_is_secretstr() -> None:
    """The field is declared as a masked secret, not a plain str."""
    annotation = CoreSettings.model_fields["metrics_auth_token"].annotation
    # ``SecretStr | None`` — SecretStr must be one of the union members.
    assert SecretStr in getattr(annotation, "__args__", (annotation,))


def test_metrics_auth_token_value_is_masked_in_repr() -> None:
    """A set token is masked in str/repr; only get_secret_value() reveals it."""
    raw = "super-secret-scrape-token"  # noqa: S105 — test fixture
    settings = CoreSettings(metrics_auth_token=raw)

    token = settings.metrics_auth_token
    assert token is not None
    assert token.get_secret_value() == raw
    assert raw not in str(token)
    assert raw not in repr(settings)
