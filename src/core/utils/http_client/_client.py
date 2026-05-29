"""``AsyncAPIClient`` — orchestrator for the split HTTP-client seams.

The session lifecycle, auth-header assembly, and aiohttp-error mapping
each live in their own helper module so this file can stay focused on
the dispatch shape: SSRF guard, build headers, await ``session.request``,
publish the audit-meta dict, return the body. The method shortcuts
(``get`` / ``post`` / …) are thin wrappers over the one ``_request``
method.

Public surface (re-exported from the package ``__init__``):
``AsyncAPIClient`` and ``AuthType``.
"""

from __future__ import annotations

from typing import Any, ClassVar

from src.core.api_log.context import outbound_response_meta_ctx
from src.core.exceptions.api import APIError
from src.core.utils.http_client._auth import (
    AuthType,
    build_basic_auth,
    build_headers,
)
from src.core.utils.http_client._errors import (
    map_aiohttp_errors,
    raise_for_server_error,
)
from src.core.utils.http_client._session import SessionManager
from src.core.utils.http_payloads import (
    summarise_body_for_audit as _summarise_body_for_audit,
)
from src.core.utils.logging import get_logger
from src.core.utils.ssrf import (
    assert_allowed_url,
    assert_public_url,
    pinned_dns,
    resolve_and_validate,
)

logger = get_logger(__name__)


class AsyncAPIClient:
    """Process-wide async HTTP client with auth, pooling, SSRF, and error mapping.

    All methods are static; session state is owned by :class:`SessionManager`.
    Close the session from the FastAPI lifespan shutdown via
    :meth:`close_session`.
    """

    DEFAULT_TIMEOUT: ClassVar[int] = 60

    # ── Session lifecycle (delegates to SessionManager) ───────────────

    @classmethod
    async def close_session(cls) -> None:
        """Close the shared HTTP session — call from lifespan shutdown."""
        await SessionManager.close_session()

    @classmethod
    def reset(cls) -> None:
        """Drop the shared session state without awaiting close (test helper)."""
        SessionManager.reset()

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
            check_ssrf: When ``True``, run :func:`resolve_and_validate`
                + :func:`assert_allowed_url` and pin the resolved IP set
                via :data:`pinned_dns` before dispatch (closes the
                DNS-rebinding TOCTOU). Disable only for tests that hit
                localhost mocks.

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
        from urllib.parse import urlparse

        pin_token = None
        if check_ssrf:
            resolved_ips = resolve_and_validate(url)
            assert_allowed_url(url)
            if resolved_ips:
                # Pin the IP set the validator approved so the custom
                # aiohttp resolver returns the same answers at dispatch
                # time. Without the pin, a DNS-rebinding attacker can
                # swap a public IP for a private one between validate
                # and dispatch (the TOCTOU Django ISSUE-028 closed).
                host = (urlparse(url).hostname or "").lower()
                pin_token = pinned_dns.set({host: resolved_ips})
        else:
            assert_allowed_url(url)

        timeout_cfg = aiohttp.ClientTimeout(total=timeout)
        final_headers = build_headers(auth_token, auth_type, headers)
        basic_auth = build_basic_auth(auth_token, auth_type)

        response_ref: dict[str, Any] = {"body": None}
        try:
            with map_aiohttp_errors(
                url=url,
                method=method,
                timeout=timeout,
                response_body_ref=response_ref,
            ):
                session = await SessionManager.get_session()
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
                        response_body: Any = await response.json()
                    else:
                        response_body = await response.text()
                    response_ref["body"] = response_body

                    outbound_response_meta_ctx.set(
                        {
                            "method": method.upper(),
                            "url": url,
                            "params": params,
                            "request_headers": final_headers,
                            "request_body_json": json,
                            "request_body_data": _summarise_body_for_audit(data),
                            "status_code": response.status,
                            "response_headers": dict(response.headers),
                            "response_body": response_body,
                        }
                    )

                    raise_for_server_error(url, response.status)
                    if response.status >= 400:
                        response.raise_for_status()

                    return response_body
            # map_aiohttp_errors always raises on the unhappy path, but mypy
            # cannot infer that — the explicit raise keeps the return type honest.
            raise RuntimeError(
                "unreachable: map_aiohttp_errors must raise or return"
            )
        finally:
            if pin_token is not None:
                pinned_dns.reset(pin_token)

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
        from urllib.parse import urlparse

        pin_token = None
        if check_ssrf:
            resolved_ips = resolve_and_validate(url)
            assert_allowed_url(url)
            if resolved_ips:
                # Pin the IP set the validator approved so the custom
                # aiohttp resolver returns the same answers at dispatch
                # time — same DNS-rebinding TOCTOU closure as ``_request``.
                host = (urlparse(url).hostname or "").lower()
                pin_token = pinned_dns.set({host: resolved_ips})
        else:
            assert_allowed_url(url)

        timeout_cfg = aiohttp.ClientTimeout(total=timeout)
        try:
            with map_aiohttp_errors(
                url=url,
                method="GET",
                timeout=timeout,
                operation="Download",
            ):
                session = await SessionManager.get_session()
                async with session.get(url, timeout=timeout_cfg) as response:
                    raise_for_server_error(url, response.status)
                    if response.status >= 400:
                        response.raise_for_status()

                    declared = response.content_length
                    if declared is not None and declared > max_size:
                        raise APIError(
                            f"Download from {url} exceeds max_size "
                            f"({declared} > {max_size} bytes)",
                            status_code=response.status,
                        )

                    buffer = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        buffer.extend(chunk)
                        if len(buffer) > max_size:
                            raise APIError(
                                f"Download from {url} exceeds max_size "
                                f"({max_size} bytes)",
                                status_code=response.status,
                            )
                    content_type = response.headers.get(
                        "Content-Type", "application/octet-stream"
                    )
                    return bytes(buffer), content_type
            raise RuntimeError(
                "unreachable: map_aiohttp_errors must raise or return"
            )
        finally:
            if pin_token is not None:
                pinned_dns.reset(pin_token)


__all__ = ["AsyncAPIClient"]
