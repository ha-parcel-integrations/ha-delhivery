"""Delhivery public tracking API client.

Keyless GET, keyed on the AWB (``wbn``) alone — but every request must carry
two fixed headers (``Origin``/``Referer``, see :mod:`.const`) or the endpoint
answers a uniform ``401``. See
``carrier-research/api/delhivery/tracking.md#endpoint--auth`` for the full
write-up; this module only implements it.

The envelope lies just like SunYou's and Cainiao's: ``HTTP 200`` +
``statusCode: 200`` + a ``message`` string all read as success even when the
AWB is bogus (control-tested 2026-08-06)::

    {"statusCode": 200, "message": "...", "data": [ {...} ]}
    {"statusCode": 200, "message": "invalid AWB or very old package", "data": []}

The only real signal is whether ``data`` is populated — **never** branch on
the HTTP status or the body's ``statusCode``. A populated ``data[]`` entry has
never actually been observed, so its internal shape is unconfirmed; see
``parcels.py`` for how that is handled.
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import DELHIVERY_ORIGIN, DELHIVERY_REFERER, TRACKING_API_URL

_LOGGER = logging.getLogger(__name__)

# Fixed, hardcoded per tracking.md#endpoint--auth — never derived from the
# tracking code or anything user-specific, so a single module-level dict is
# correct (not per-request state).
_HEADERS = {
    "Origin": DELHIVERY_ORIGIN,
    "Referer": DELHIVERY_REFERER,
}


class DelhiveryApiError(Exception):
    """Raised when a Delhivery API call returns an unexpected response."""

    def __init__(self, detail: str) -> None:
        """Store the status code that triggered the error."""
        super().__init__(f"Delhivery API request failed: {detail}")
        self.detail = detail


class DelhiveryApiClient:
    """Client for the keyless Delhivery unified-tracking endpoint.

    No authentication in the credential sense: the endpoint is keyed on the
    AWB alone, gated only by the fixed same-origin ``Origin``/``Referer``
    headers this client always sends.
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client with an aiohttp session."""
        self._session = session

    async def async_get_parcel(self, tracking_code: str) -> dict[str, Any] | None:
        """Fetch one parcel's tracking details.

        Returns the first (and presumably only) ``data[]`` entry for a known
        AWB, or ``None`` when the endpoint reports ``data: []`` — which is
        also what a not-yet-scanned parcel gets, per the one control-tested
        probe available (a bogus AWB). Any other failure — including a
        non-200 status, which is what a *missing* Origin/Referer header
        produces (401) — raises :class:`DelhiveryApiError` rather than being
        retried silently; network errors propagate as ``aiohttp.ClientError``.
        """
        url = TRACKING_API_URL.format(tracking_code=tracking_code)
        async with self._session.get(url, headers=_HEADERS) as response:
            if response.status != 200:
                raise DelhiveryApiError(f"HTTP {response.status}")
            try:
                # content_type=None: consumer endpoints routinely serve JSON as
                # text/plain, and aiohttp would otherwise refuse to parse it.
                payload = await response.json(content_type=None)
            except ValueError as err:
                raise DelhiveryApiError(f"unparseable body ({err})") from err

        if not isinstance(payload, dict):
            raise DelhiveryApiError("unexpected body (not a JSON object)")

        # The envelope's own success signalling (HTTP 200, statusCode: 200,
        # a message string) reads as success even for a bogus AWB — the only
        # thing that actually distinguishes a hit is whether `data` carries
        # anything. Never branch on `statusCode` or `message`.
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            return None

        entry = data[0]
        if not isinstance(entry, dict):
            # Never observed; guard rather than crash the whole poll.
            _LOGGER.warning(
                "Delhivery returned a non-object data[0] for %s (%s)",
                tracking_code,
                type(entry).__name__,
            )
            return None
        return entry
