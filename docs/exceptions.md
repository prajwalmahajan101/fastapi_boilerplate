# Exceptions

Domain code raises typed exceptions; one handler maps them to the
standard response envelope with the right HTTP status. Adding a new
family is two lines.

## The family

Every typed exception extends `BaseCustomError`
(`src/core/base/exception.py`). Each subclass declares:

- `default_message` — what the envelope shows to the client.
- `error_code` — stable, machine-readable; clients pattern-match on this.
- `status_code` — HTTP status; pulled from a registry that handles
  MRO so subclasses inherit by default.

```python
class TokenRevokedError(AuthenticationFailedError):
    """The supplied JWT's `jti` is blacklisted."""

    default_message = "Token has been revoked."
    error_code = "TOKEN_REVOKED"
```

## The registry

`src/core/exceptions/handlers.py` keeps an ordered list of
`(exc_class, status_code)` pairs. The handler walks the list and
uses the first `isinstance` match; subclasses inherit their parent's
mapping unless they register their own.

Register at import time near the class definition (or once at app
startup):

```python
register_exception_mapping(MyError, status.HTTP_409_CONFLICT)
```

`scripts/check_dead_utils.py` will flag a registered exception that
no one raises; the ADR below covers why the registry exists at all.

## Built-in families

| Family | Status | Where raised |
|---|---|---|
| `EntityNotFoundError` | 404 | Repositories — every lookup that may miss. |
| `ValidationError` | 400 | Domain validation (vs Pydantic 422). |
| `AuthenticationFailedError` (+ subclasses) | 401 | Auth providers. |
| `PermissionDeniedError` | 403 | RBAC `RequireResource` denial. |
| `RateLimitError` | 429 | Throttle / rate-limit headers. |
| `ServiceUnavailableError` | 503 | Open circuit breaker. |
| `ExternalServiceError` | 502 | Upstream non-timeout failure. |
| `ExternalTimeoutError` | 502 | Upstream timeout. |
| `RepositoryError` / `InfrastructureError` | 500 | Catch-all surface for plumbing bugs. |

## Adding a new family

1. Subclass `BaseCustomError` (or a closer parent for status inheritance).
2. Set `default_message`, `error_code`, optional `status_code`.
3. Add one `register_exception_mapping(...)` call beside the existing
   registrations in `src/core/exceptions/handlers.py`.

The ADR — [`decisions/0002-exception-http-registry.md`](decisions/0002-exception-http-registry.md) —
explains why the registry beats per-route `responses=` declarations.

## OpenAPI

Route decorators still list the responses set
(`src/common/openapi_metadata.py:RESPONSES_*`). Keep these in sync
with the registered status codes — `scripts/check_openapi_metadata.py`
enforces it.
