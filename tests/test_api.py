"""Tests for the Delhivery API client."""
import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.delhivery.api import (
    DelhiveryApiClient,
    DelhiveryApiError,
)
from custom_components.delhivery.const import DELHIVERY_ORIGIN, DELHIVERY_REFERER

CODE = "1234567890123"

NOT_FOUND_ENVELOPE = {
    "statusCode": 200,
    "message": "invalid AWB or very old package",
    "data": [],
}


def _session_returning(status: int, body: object = None) -> MagicMock:
    response = AsyncMock()
    response.status = status
    if isinstance(body, str):
        response.json = AsyncMock(side_effect=json.JSONDecodeError("x", body, 0))
    else:
        response.json = AsyncMock(return_value=body)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=ctx)
    return session


async def test_get_parcel_returns_data_entry_on_success():
    session = _session_returning(
        200,
        {"statusCode": 200, "message": "ok", "data": [{"trackingStates": "X"}]},
    )
    client = DelhiveryApiClient(session)

    parcel = await client.async_get_parcel(CODE)

    assert parcel == {"trackingStates": "X"}
    # the AWB ends up in the URL
    assert CODE in session.get.call_args[0][0]


async def test_get_parcel_sends_fixed_origin_and_referer_headers():
    """Dropping these headers 401s every request (tracking.md) — verify
    the client always attaches them, on every call, unconditionally."""
    session = _session_returning(200, NOT_FOUND_ENVELOPE)
    client = DelhiveryApiClient(session)

    await client.async_get_parcel(CODE)

    headers = session.get.call_args.kwargs["headers"]
    assert headers["Origin"] == DELHIVERY_ORIGIN == "https://www.delhivery.com"
    assert headers["Referer"] == DELHIVERY_REFERER == "https://www.delhivery.com/"


async def test_get_parcel_returns_none_on_empty_data():
    """The not-found envelope: HTTP 200, statusCode 200, an empty `data`.

    This is the one verbatim capture in this repo (control-tested
    2026-08-06 on a bogus AWB) — `data: []` is the only real not-found
    signal; `statusCode`/`message` both still read as "200 OK".
    """
    client = DelhiveryApiClient(_session_returning(200, NOT_FOUND_ENVELOPE))
    assert await client.async_get_parcel("0000000000000") is None


async def test_get_parcel_ignores_statuscode_and_message_envelope_lies():
    """statusCode/message must never be branched on — only `data` matters."""
    session = _session_returning(
        200,
        {
            "statusCode": 200,
            "message": "invalid AWB or very old package",
            "data": [{"trackingStates": "Delivered"}],
        },
    )
    client = DelhiveryApiClient(session)
    assert await client.async_get_parcel(CODE) == {"trackingStates": "Delivered"}


async def test_get_parcel_returns_none_when_data_missing():
    client = DelhiveryApiClient(
        _session_returning(200, {"statusCode": 200, "message": "ok"})
    )
    assert await client.async_get_parcel(CODE) is None


async def test_get_parcel_returns_none_when_data_not_a_list():
    client = DelhiveryApiClient(
        _session_returning(200, {"statusCode": 200, "message": "ok", "data": {}})
    )
    assert await client.async_get_parcel(CODE) is None


async def test_get_parcel_returns_none_on_non_object_data_entry():
    """Never observed; guarded rather than crashing the whole poll."""
    client = DelhiveryApiClient(
        _session_returning(200, {"statusCode": 200, "message": "ok", "data": ["x"]})
    )
    assert await client.async_get_parcel(CODE) is None


async def test_get_parcel_raises_on_401_missing_origin():
    """A missing/incorrect Origin header 401s uniformly (tracking.md) —
    this must surface as a normal API error, never be retried silently."""
    client = DelhiveryApiClient(
        _session_returning(401, {"message": "ERROR: Invalid Origin"})
    )
    with pytest.raises(DelhiveryApiError) as err:
        await client.async_get_parcel(CODE)
    assert "401" in str(err.value)


async def test_get_parcel_raises_on_error_status():
    client = DelhiveryApiClient(_session_returning(500, {}))
    with pytest.raises(DelhiveryApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_unparseable_body():
    client = DelhiveryApiClient(_session_returning(200, "not json"))
    with pytest.raises(DelhiveryApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_non_object_body():
    client = DelhiveryApiClient(_session_returning(200, ["not", "a", "dict"]))
    with pytest.raises(DelhiveryApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_propagates_network_error():
    """ClientError is left alone — DataUpdateCoordinator already wraps it."""
    session = MagicMock()
    session.get = MagicMock(side_effect=aiohttp.ClientError("boom"))
    client = DelhiveryApiClient(session)
    with pytest.raises(aiohttp.ClientError):
        await client.async_get_parcel(CODE)
