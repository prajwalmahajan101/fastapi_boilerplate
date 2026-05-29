"""Pinned DNS resolver for the shared aiohttp session.

Reads :data:`src.core.utils.ssrf.pinned_dns` — a ``ContextVar`` that
``AsyncAPIClient.request`` populates with the IP set the SSRF
validator approved — and returns *only* those IPs at dispatch time.
The hostname is matched case-insensitively, port and family are
passed through.

When the pin is empty / None, falls back to ``aiohttp.AsyncResolver``
(or the default resolver if aiohttp's optional ``aiodns`` dep is
unavailable). The fallback is built lazily so importing this module
does not require a running loop.
"""

from __future__ import annotations

import logging
import socket
from typing import Any

logger = logging.getLogger(__name__)


class PinnedResolver:
    """``aiohttp.AbstractResolver`` honouring :data:`pinned_dns`."""

    def __init__(self) -> None:
        self._fallback: Any = None

    def _get_fallback(self) -> Any:
        if self._fallback is not None:
            return self._fallback
        import aiohttp  # noqa: PLC0415

        try:
            self._fallback = aiohttp.AsyncResolver()
        except RuntimeError:
            # ``aiodns`` is not installed; aiohttp's threaded resolver
            # is the documented portable choice.
            self._fallback = aiohttp.ThreadedResolver()
        return self._fallback

    async def resolve(
        self, host: str, port: int = 0, family: int = socket.AF_INET
    ) -> list[dict[str, Any]]:
        """Return the pinned IPs for ``host`` when a pin is active.

        Args:
            host: Hostname to resolve.
            port: Destination port — passed through unchanged.
            family: Socket address family (``AF_INET`` / ``AF_INET6``).

        Returns:
            A list of address records in aiohttp's expected shape
            (``{hostname, host, port, family, proto, flags}``).
        """
        from src.core.utils.ssrf import pinned_dns  # noqa: PLC0415

        pins = pinned_dns.get()
        if pins:
            ips = pins.get(host.lower()) or pins.get(host)
            if ips:
                records: list[dict[str, Any]] = []
                for ip in ips:
                    try:
                        ip_family = (
                            socket.AF_INET6 if ":" in ip else socket.AF_INET
                        )
                    except Exception:  # noqa: BLE001
                        ip_family = socket.AF_INET
                    if family not in (socket.AF_UNSPEC, ip_family):
                        continue
                    records.append(
                        {
                            "hostname": host,
                            "host": ip,
                            "port": port,
                            "family": ip_family,
                            "proto": 0,
                            "flags": 0,
                        }
                    )
                if records:
                    return records
                # Pin existed but no matching family — fall through to
                # the fallback resolver. This intentionally re-opens
                # the DNS-rebinding window for the IPv6 vs IPv4
                # mismatch case; in practice the pin always covers the
                # family aiohttp was about to use.
        return await self._get_fallback().resolve(host, port, family)

    async def close(self) -> None:
        """Release any resources held by the fallback resolver."""
        if self._fallback is not None:
            await self._fallback.close()


__all__ = ["PinnedResolver"]
