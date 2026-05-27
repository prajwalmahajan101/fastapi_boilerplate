"""``AsyncAPIClient`` — async HTTP client with pooling, auth, SSRF, and error mapping.

Combines the ignosis ``APIClient`` shape (class-level shared ``aiohttp``
session, ``AuthType`` enum, JSON/text auto-parse, ``outbound_response_meta_ctx``
publication for the API audit log decorator) with the Django ``make_http_request``
SSRF defense and structured error mapping (``ExternalTimeoutError`` /
``TransientError`` / ``APIError``).

Pair with ``@resilient("svc")`` for retry + circuit breaker; pair with
``@log_outbound_request("svc")`` for persistent audit capture.

**Loop-ownership tracking.** The class-level ``_session`` / ``_connector``
/ ``_session_lock`` attributes are bound to the event loop on which they
were first created. :meth:`_get_session` records the loop id at creation
time and silently resets every class attribute when a different loop
asks for the session — this is the pytest-asyncio "new loop per test"
case. Production runs only ever see one loop so the check is a no-op.
Tests that explicitly want to drop the session can still call
:meth:`close_session` (async) or :meth:`reset` (sync) on teardown.
"""

from __future__ import annotations

import asyncio
import json as _json
from enum import StrEnum
from typing import Any, ClassVar

from src.core.api_log.context import outbound_response_meta_ctx
from src.core.exceptions.api import APIError
from src.core.exceptions.infrastructure import ExternalTimeoutError, TransientError
from src.core.exceptions.validation import ValidationError
from src.core.utils.logging import get_logger
from src.core.utils.ssrf import assert_public_url, safe_host

logger = get_logger(__name__)


class AuthType(StrEnum):
    """Outbound HTTP authentication style (not user authentication)."""

    BEARER = "Bearer"
    BASIC = "Basic"
    API_KEY = "ApiKey"
    NONE = "None"


def _serialize_error_body(body: Any) -> str | None:
    """Best-effort JSON-encode an upstream error body for ``APIError.response_body``.

    Falls back to ``str(body)`` if the value isn't JSON-serialisable so
    a malformed partner response never masks the real failure under a
    secondary ``TypeError``.

    Args:
        body: Parsed response body (dict, list, str, or anything).

    Returns:
        JSON string when possible, the original string when ``body`` is
        already a string, ``str(body)`` as a last resort, or ``None``
        when no body was captured.
    """
    if body is None:
        return None
    if isinstance(body, str):
        return body
    try:
        return _json.dumps(body, default=str)
    except Exception:  # noqa: BLE001
        return str(body)


class AsyncAPIClient:
    """Process-wide async HTTP client with auth, pooling, SSRF, and error mapping.

    All methods are static; state lives in class-level attributes so the
    same session is shared across every caller in the process. Close the
    session from the FastAPI lifespan shutdown via ``close_session()``.
    """

    DEFAULT_TIMEOUT: ClassVar[int] = 60

    _session: ClassVar[Any] = None
    _connector: ClassVar[Any] = None
    _session_lock: ClassVar[Any] = None  # lazily bound to the current loop
    _owner_loop_id: ClassVar[int | None] = None  # id() of the loop that owns _session

    # ── Session lifecycle ─────────────────────────────────────────────

    @classmethod
    def _reset_class_state(cls) -> None:
        """Drop session/connector/lock references without awaiting close.

        Called when a different event loop is detected — the prior loop
        is presumed already torn down (typical pytest-asyncio case), so
        the references are dropped on the floor and the underlying
        resources are released when their loop is garbage-collected.
        """
        cls._session = None
        cls._connector = None
        cls._session_lock = None
        cls._owner_loop_id = None

    @classmethod
    def _get_lock(cls) -> asyncio.Lock:
        """Return the session lock, creating it on first call.

        Creating the lock lazily (instead of at class-body eval time)
        means it binds to the event loop active when ``_get_session``
        first runs — tests that spin a new loop per case can ``reset``
        between cases without inheriting a lock from a closed loop.

        Returns:
            The shared ``asyncio.Lock`` guarding session creation.
        """
        if cls._session_lock is None:
            cls._session_lock = asyncio.Lock()
        return cls._session_lock

    @classmethod
    async def _get_session(cls) -> Any:
        """Return the shared ``aiohttp.ClientSession``, creating it lazily.

        On every call we compare the current event loop against the loop
        that owns the cached session; if they differ (pytest-asyncio
        per-test loop, REPL restart, etc.) we drop the class state and
        rebuild on the new loop. Production lifecycles only ever see one
        loop, so the check is a no-op there.

        Connection-pool limits are 100 total and 30 per host with a 5-min
        DNS cache. Subsequent callers on the same loop reuse the same
        session so partner clients share keep-alive connections.

        Returns:
            An ``aiohttp.ClientSession`` ready for HTTP calls.
        """
        import aiohttp

        current_loop_id = id(asyncio.get_running_loop())
        if cls._owner_loop_id is not None and cls._owner_loop_id != current_loop_id:
            cls._reset_class_state()

        async with cls._get_lock():
            if cls._session is None or cls._session.closed:
                cls._connector = aiohttp.TCPConnector(
                    limit=100,
                    limit_per_host=30,
                    ttl_dns_cache=300,
                    enable_cleanup_closed=True,
                )
                cls._session = aiohttp.ClientSession(
                    connector=cls._connector,
                    connector_owner=True,
                )
                cls._owner_loop_id = current_loop_id
                logger.info(
                    "Created HTTP session with pooling",
                    extra={
                        "total_limit": 100,
                        "per_host_limit": 30,
                        "dns_cache_ttl": 300,
                    },
                )
            return cls._session

    @classmethod
    async def close_session(cls) -> None:
        """Close the shared session and clear loop-bound class state.

        Paired with the FastAPI lifespan shutdown so a process restart
        does not leak the connector pool. ``_session_lock`` and
        ``_owner_loop_id`` are also cleared so the next loop creates a
        fresh lock — important for per-test loop fixtures.
        """
        if cls._session is not None and not cls._session.closed:
            await cls._session.close()
            logger.info("Closed HTTP session.")
        cls._reset_class_state()

    @classmethod
    def reset(cls) -> None:
        """Test helper — drop class state without awaiting session close.

        Use when a test fixture cannot ``await close_session`` (e.g. a
        sync teardown). The session/connector references are dropped on
        the floor; aiohttp will garbage-collect them when the
        ``event_loop`` they belong to is closed.
        """
        cls._reset_class_state()

    # ── Header builder ────────────────────────────────────────────────

    @staticmethod
    def _build_headers(
        auth_token: str | dict[str, str] | None,
        auth_type: AuthType,
        headers: dict[str, str] | None,
    ) -> dict[str, str]:
        """Compose the outbound ``Authorization`` header from ``auth_type``.

        ``BEARER`` and the catch-all branch produce ``Authorization``;
        ``API_KEY`` writes a configurable header name (default
        ``x-api-key``) from the ``auth_token`` dict; ``BASIC`` is
        delegated to aiohttp's ``auth=`` parameter so no header is
        added here; ``NONE`` leaves the input headers alone.

        Args:
            auth_token: String for ``BEARER`` / fallback; dict for
                ``API_KEY`` / ``BASIC``; ignored for ``NONE``.
            auth_type: Which authentication scheme to apply.
            headers: Caller-supplied headers; copied so the input is
                never mutated.

        Returns:
            The merged headers dict ready to pass to aiohttp.
        """
        final = headers.copy() if headers else {}
        if auth_type == AuthType.BEARER and isinstance(auth_token, str):
            final["Authorization"] = f"Bearer {auth_token}"
        elif auth_type == AuthType.API_KEY and isinstance(auth_token, dict):
            header_name = auth_token.get("header_name", "x-api-key")
            api_key = auth_token.get("api_key", "")
            if api_key:
                final[header_name] = api_key
        elif auth_type == AuthType.NONE:
            pass
        elif auth_type == AuthType.BASIC:
            # aiohttp handles Basic via the auth= parameter; no header here.
            pass
        elif auth_token:
            final["Authorization"] = f"{auth_type.value} {auth_token}"
        return final

    # ── Core request ──────────────────────────────────────────────────

    @staticmethod
    async def _request(
        method: str,
        url: str,
        *,
        auth_token: str | dict[str, str] | None = None,
        auth_type: AuthType = AuthType.BEARER,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        check_ssrf: bool = True,
    ) -> dict[str, Any] | list[Any] | str:
        """Issue a single HTTP request and map every failure to a typed exception.

        Publishes request + response metadata to
        ``outbound_response_meta_ctx`` right before returning so
        ``@log_outbound_request`` can capture every call without
        re-parsing arguments. 5xx responses become ``TransientError`` so
        the retry decorator re-fires; 4xx becomes ``APIError`` (no
        retry); network / DNS / SSL failures become ``TransientError``;
        timeouts become ``ExternalTimeoutError``.

        Args:
            method: HTTP verb (``"GET"``, ``"POST"``, …); upper-cased internally.
            url: Absolute target URL.
            auth_token: Token material — ``str`` for ``BEARER`` /
                fallback, ``dict`` for ``BASIC`` (username/password)
                and ``API_KEY`` (header_name/api_key).
            auth_type: Authentication scheme; see :class:`AuthType`.
            headers: Extra headers merged on top of the auth header.
            params: Query-string parameters.
            data: Form-encoded body (mutually exclusive with ``json``).
            json: JSON-serialised body.
            timeout: Total request timeout in seconds.
            check_ssrf: When ``True``, run :func:`assert_public_url` before
                dispatch; disable only for tests that hit localhost mocks.

        Returns:
            Parsed JSON body when ``Content-Type`` is ``application/json``;
            raw text otherwise.

        Raises:
            ValidationError: ``BASIC`` auth missing ``username`` / ``password``,
                or SSRF guard rejects the URL.
            ExternalTimeoutError: Request exceeded ``timeout`` seconds.
            APIError: Upstream returned a 4xx status.
            TransientError: Upstream returned 5xx, or aiohttp raised a
                transport-level error (DNS, SSL, connection reset).
        """
        import aiohttp
        from aiohttp import ClientError, ClientResponseError, ServerTimeoutError

        if check_ssrf:
            assert_public_url(url)

        timeout_cfg = aiohttp.ClientTimeout(total=timeout)
        final_headers = AsyncAPIClient._build_headers(auth_token, auth_type, headers)

        basic_auth = None
        if auth_type == AuthType.BASIC and isinstance(auth_token, dict):
            username = auth_token.get("username")
            password = auth_token.get("password")
            if not username or not password:
                raise ValidationError(
                    "Basic Auth requires both 'username' and 'password'",
                    details={"provided_keys": list(auth_token.keys())},
                )
            basic_auth = aiohttp.BasicAuth(username, password)

        response_body: Any = None
        try:
            session = await AsyncAPIClient._get_session()
            async with session.request(
                method=method.upper(),
                url=url,
                headers=final_headers,
                params=params,
                data=data,
                json=json,
                auth=basic_auth,
                timeout=timeout_cfg,
            ) as response:
                content_type = response.headers.get("Content-Type", "")
                if "application/json" in content_type:
                    response_body = await response.json()
                else:
                    response_body = await response.text()

                outbound_response_meta_ctx.set(
                    {
                        "method": method.upper(),
                        "url": url,
                        "params": params,
                        "request_headers": final_headers,
                        "request_body_json": json,
                        "request_body_data": data,
                        "status_code": response.status,
                        "response_headers": dict(response.headers),
                        "response_body": response_body,
                    }
                )

                if response.status >= 500:
                    raise TransientError(
                        f"Server error from {safe_host(url)}: HTTP {response.status}"
                    )
                if response.status >= 400:
                    response.raise_for_status()

                return response_body

        except (ServerTimeoutError, asyncio.TimeoutError) as exc:
            logger.error(
                "Request timeout",
                extra={"url": url, "method": method, "timeout": timeout},
            )
            raise ExternalTimeoutError(
                f"Request to {safe_host(url)} timed out after {timeout}s"
            ) from exc
        except ClientResponseError as exc:
            logger.error(
                "Client response error",
                extra={"status": exc.status, "url": url, "method": method},
            )
            raise APIError(
                f"HTTP {exc.status} error: {exc.message}",
                status_code=exc.status,
                response_body=_serialize_error_body(response_body),
                details={"url": url, "method": method, "message": exc.message},
            ) from exc
        except ClientError as exc:
            logger.error(
                "HTTP transport error",
                extra={"url": url, "method": method, "error_class": type(exc).__name__},
            )
            raise TransientError(
                f"Transport error contacting {safe_host(url)}"
            ) from exc

    # ── Method shortcuts ──────────────────────────────────────────────

    @staticmethod
    async def get(
        url: str,
        *,
        auth_token: str | dict[str, str] | None = None,
        auth_type: AuthType = AuthType.BEARER,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        check_ssrf: bool = True,
    ) -> dict[str, Any] | list[Any] | str:
        """Issue a ``GET`` request via :meth:`_request`.

        Args:
            url: Absolute target URL.
            auth_token: See :meth:`_request`.
            auth_type: See :meth:`_request`.
            headers: Extra headers merged on top of the auth header.
            params: Query-string parameters.
            timeout: Total request timeout in seconds.
            check_ssrf: When ``True``, run the SSRF guard before dispatch.

        Returns:
            See :meth:`_request`.
        """
        return await AsyncAPIClient._request(
            method="GET",
            url=url,
            auth_token=auth_token,
            auth_type=auth_type,
            headers=headers,
            params=params,
            timeout=timeout,
            check_ssrf=check_ssrf,
        )

    @staticmethod
    async def post(
        url: str,
        *,
        auth_token: str | dict[str, str] | None = None,
        auth_type: AuthType = AuthType.BEARER,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        check_ssrf: bool = True,
    ) -> dict[str, Any] | list[Any] | str:
        """Issue a ``POST`` request via :meth:`_request`.

        Args:
            url: Absolute target URL.
            auth_token: See :meth:`_request`.
            auth_type: See :meth:`_request`.
            headers: Extra headers merged on top of the auth header.
            json: JSON body (preferred — mutually exclusive with ``data``).
            data: Form-encoded body.
            timeout: Total request timeout in seconds.
            check_ssrf: When ``True``, run the SSRF guard before dispatch.

        Returns:
            See :meth:`_request`.
        """
        return await AsyncAPIClient._request(
            method="POST",
            url=url,
            auth_token=auth_token,
            auth_type=auth_type,
            headers=headers,
            json=json,
            data=data,
            timeout=timeout,
            check_ssrf=check_ssrf,
        )

    @staticmethod
    async def download_bytes(
        url: str,
        *,
        max_size: int,
        timeout: int = DEFAULT_TIMEOUT,
        check_ssrf: bool = True,
    ) -> tuple[bytes, str]:
        """Download *url* as raw bytes (e.g. a presigned object URL).

        Unlike :meth:`get`, the body is **not** JSON/text-decoded — the raw
        bytes are returned alongside the upstream ``Content-Type``. Used to
        fetch a document from a caller-supplied presigned URL before
        forwarding it to a partner as multipart. The body is streamed and
        rejected the moment it crosses ``max_size`` so an oversized object
        never buffers whole.

        Args:
            url: Absolute target URL (e.g. a presigned S3 GET URL).
            max_size: Maximum permitted body size in bytes.
            timeout: Total request timeout in seconds.
            check_ssrf: When ``True``, run the SSRF guard before dispatch.

        Returns:
            Tuple ``(body_bytes, content_type)``; ``content_type`` falls back
            to ``application/octet-stream`` when the response omits it.

        Raises:
            ExternalTimeoutError: The request exceeded ``timeout`` seconds.
            APIError: The upstream returned a 4xx status, or the body exceeded
                ``max_size``.
            TransientError: The upstream returned 5xx, or a transport-level
                error occurred.
        """
        import aiohttp
        from aiohttp import ClientError, ClientResponseError, ServerTimeoutError

        if check_ssrf:
            assert_public_url(url)

        timeout_cfg = aiohttp.ClientTimeout(total=timeout)
        try:
            session = await AsyncAPIClient._get_session()
            async with session.get(url, timeout=timeout_cfg) as response:
                if response.status >= 500:
                    raise TransientError(
                        f"Server error from {safe_host(url)}: HTTP {response.status}"
                    )
                if response.status >= 400:
                    response.raise_for_status()

                declared = response.content_length
                if declared is not None and declared > max_size:
                    raise APIError(
                        f"Download from {safe_host(url)} exceeds max_size "
                        f"({declared} > {max_size} bytes)",
                        status_code=response.status,
                    )

                buffer = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    buffer.extend(chunk)
                    if len(buffer) > max_size:
                        raise APIError(
                            f"Download from {safe_host(url)} exceeds max_size "
                            f"({max_size} bytes)",
                            status_code=response.status,
                        )
                content_type = response.headers.get(
                    "Content-Type", "application/octet-stream"
                )
                return bytes(buffer), content_type

        except (ServerTimeoutError, asyncio.TimeoutError) as exc:
            logger.error("Download timeout", extra={"url": url, "timeout": timeout})
            raise ExternalTimeoutError(
                f"Request to {safe_host(url)} timed out after {timeout}s"
            ) from exc
        except ClientResponseError as exc:
            logger.error(
                "Download client response error",
                extra={"status": exc.status, "url": url},
            )
            raise APIError(
                f"HTTP {exc.status} error: {exc.message}",
                status_code=exc.status,
                details={"url": url, "method": "GET", "message": exc.message},
            ) from exc
        except ClientError as exc:
            logger.error(
                "Download transport error",
                extra={"url": url, "error_class": type(exc).__name__},
            )
            raise TransientError(
                f"Transport error contacting {safe_host(url)}"
            ) from exc

    @staticmethod
    async def put(
        url: str,
        *,
        auth_token: str | dict[str, str] | None = None,
        auth_type: AuthType = AuthType.BEARER,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        check_ssrf: bool = True,
    ) -> dict[str, Any] | list[Any] | str:
        """Issue a ``PUT`` request via :meth:`_request`.

        Args:
            url: Absolute target URL.
            auth_token: See :meth:`_request`.
            auth_type: See :meth:`_request`.
            headers: Extra headers merged on top of the auth header.
            json: JSON body (preferred — mutually exclusive with ``data``).
            data: Form-encoded body.
            timeout: Total request timeout in seconds.
            check_ssrf: When ``True``, run the SSRF guard before dispatch.

        Returns:
            See :meth:`_request`.
        """
        return await AsyncAPIClient._request(
            method="PUT",
            url=url,
            auth_token=auth_token,
            auth_type=auth_type,
            headers=headers,
            json=json,
            data=data,
            timeout=timeout,
            check_ssrf=check_ssrf,
        )

    @staticmethod
    async def patch(
        url: str,
        *,
        auth_token: str | dict[str, str] | None = None,
        auth_type: AuthType = AuthType.BEARER,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        check_ssrf: bool = True,
    ) -> dict[str, Any] | list[Any] | str:
        """Issue a ``PATCH`` request via :meth:`_request`.

        Args:
            url: Absolute target URL.
            auth_token: See :meth:`_request`.
            auth_type: See :meth:`_request`.
            headers: Extra headers merged on top of the auth header.
            json: JSON body (preferred — mutually exclusive with ``data``).
            data: Form-encoded body.
            timeout: Total request timeout in seconds.
            check_ssrf: When ``True``, run the SSRF guard before dispatch.

        Returns:
            See :meth:`_request`.
        """
        return await AsyncAPIClient._request(
            method="PATCH",
            url=url,
            auth_token=auth_token,
            auth_type=auth_type,
            headers=headers,
            json=json,
            data=data,
            timeout=timeout,
            check_ssrf=check_ssrf,
        )

    @staticmethod
    async def delete(
        url: str,
        *,
        auth_token: str | dict[str, str] | None = None,
        auth_type: AuthType = AuthType.BEARER,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        check_ssrf: bool = True,
    ) -> dict[str, Any] | list[Any] | str:
        """Issue a ``DELETE`` request via :meth:`_request`.

        Args:
            url: Absolute target URL.
            auth_token: See :meth:`_request`.
            auth_type: See :meth:`_request`.
            headers: Extra headers merged on top of the auth header.
            params: Query-string parameters.
            timeout: Total request timeout in seconds.
            check_ssrf: When ``True``, run the SSRF guard before dispatch.

        Returns:
            See :meth:`_request`.
        """
        return await AsyncAPIClient._request(
            method="DELETE",
            url=url,
            auth_token=auth_token,
            auth_type=auth_type,
            headers=headers,
            params=params,
            timeout=timeout,
            check_ssrf=check_ssrf,
        )
