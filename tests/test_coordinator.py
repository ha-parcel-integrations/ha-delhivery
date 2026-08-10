"""Tests for the Delhivery coordinator: fetching, caching and events.

The parcel mapping itself is covered by ``test_parcels.py``.
"""
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.delhivery.parcels as parcels_module
from custom_components.delhivery.api import DelhiveryApiError
from custom_components.delhivery.const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_PARCELS,
    CONF_TRACKING_CODE,
    DOMAIN,
    ParcelStatus,
)
from custom_components.delhivery.coordinator import DelhiveryCoordinator

from .payloads import ACTIVE_CODE, DELIVERED_CODE, active_sample, delivered_sample

OTHER_CODE = "1111111111111"


@pytest.fixture(autouse=True)
def _reset_one_shot_warnings():
    """Isolate the module's one-shot WARNING set across tests (see test_parcels.py)."""
    parcels_module._warned.clear()
    yield
    parcels_module._warned.clear()


def _entry_with(parcels: list[dict]) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        # Keep-most-recent-100 so the delivered-retention filter never trims
        # the (old, fixed-date) sample parcels these tests assert on.
        options={
            CONF_PARCELS: parcels,
            CONF_DELIVERED_FILTER_TYPE: "parcels",
            CONF_DELIVERED_FILTER_AMOUNT: 100,
        },
        unique_id=DOMAIN,
    )


# ---------------------------------------------------------------------------
# fetching
# ---------------------------------------------------------------------------


async def test_update_merges_multiple_parcels(hass):
    entry = _entry_with(
        [{CONF_TRACKING_CODE: ACTIVE_CODE}, {CONF_TRACKING_CODE: DELIVERED_CODE}]
    )
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.side_effect = lambda code: (
        active_sample() if code == ACTIVE_CODE else delivered_sample()
    )
    coordinator = DelhiveryCoordinator(hass, client, entry)

    data = await coordinator._async_update_data()

    # active_sample's hqStatus ("In Transit") maps to `in_transit`, so it
    # stays in the active list; delivered_sample's hqStatus ("Delivered")
    # maps to `delivered`, so it moves to coordinator.delivered instead.
    assert [p["barcode"] for p in data] == [ACTIVE_CODE]
    assert [p["barcode"] for p in coordinator.delivered] == [DELIVERED_CODE]
    assert coordinator.last_success_time is not None


async def test_update_not_found_shows_pending_placeholder(hass):
    entry = _entry_with([{CONF_TRACKING_CODE: OTHER_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.return_value = None  # data: [] envelope
    coordinator = DelhiveryCoordinator(hass, client, entry)

    data = await coordinator._async_update_data()

    assert len(data) == 1
    assert data[0]["barcode"] == OTHER_CODE
    assert data[0]["status"] == ParcelStatus.UNKNOWN
    assert data[0]["raw"] == {}


async def test_update_keeps_cached_payload_on_error(hass):
    entry = _entry_with([{CONF_TRACKING_CODE: DELIVERED_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.return_value = delivered_sample()
    coordinator = DelhiveryCoordinator(hass, client, entry)
    await coordinator._async_update_data()  # populates the cache

    client.async_get_parcel.side_effect = DelhiveryApiError("HTTP 500")
    await coordinator._async_update_data()  # error -> cached raw reused
    # delivered_sample maps to `delivered`, so it lands in coordinator.delivered
    # (built from cached raw), not in the active list _async_update_data returns.
    assert coordinator.delivered[0]["barcode"] == DELIVERED_CODE


async def test_update_raises_when_every_parcel_fails(hass):
    from homeassistant.helpers.update_coordinator import UpdateFailed

    entry = _entry_with([{CONF_TRACKING_CODE: DELIVERED_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.side_effect = DelhiveryApiError("HTTP 500")
    coordinator = DelhiveryCoordinator(hass, client, entry)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_update_raises_on_401_missing_origin_header(hass):
    """A 401 (missing/incorrect Origin) must surface as a normal per-parcel
    error, never be retried silently forever (tracking.md)."""
    from homeassistant.helpers.update_coordinator import UpdateFailed

    entry = _entry_with([{CONF_TRACKING_CODE: DELIVERED_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.side_effect = DelhiveryApiError("HTTP 401")
    coordinator = DelhiveryCoordinator(hass, client, entry)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    assert client.async_get_parcel.await_count == 1  # not retried in a loop


async def test_update_reraises_unexpected_exceptions(hass):
    """Only API and network errors are tolerated; a bug must not be swallowed."""
    entry = _entry_with([{CONF_TRACKING_CODE: DELIVERED_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.side_effect = ValueError("boom")
    coordinator = DelhiveryCoordinator(hass, client, entry)

    with pytest.raises(ValueError):
        await coordinator._async_update_data()


async def test_update_skips_items_missing_a_tracking_code(hass):
    entry = _entry_with(
        [{CONF_TRACKING_CODE: ""}, {CONF_TRACKING_CODE: DELIVERED_CODE}]
    )
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.return_value = delivered_sample()
    coordinator = DelhiveryCoordinator(hass, client, entry)

    await coordinator._async_update_data()
    assert client.async_get_parcel.await_count == 1  # empty item never fetched


async def test_update_barcode_is_always_the_requested_code(hass):
    """barcode never comes from the payload — it is the AWB the coordinator
    asked for, even when the response is a hollow/edge payload."""
    entry = _entry_with([{CONF_TRACKING_CODE: OTHER_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.return_value = {"trackingStates": "In Transit"}
    coordinator = DelhiveryCoordinator(hass, client, entry)

    data = await coordinator._async_update_data()
    assert data[0]["barcode"] == OTHER_CODE


async def test_update_prunes_cache_for_untracked_parcels(hass):
    entry = _entry_with([{CONF_TRACKING_CODE: DELIVERED_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.return_value = delivered_sample()
    coordinator = DelhiveryCoordinator(hass, client, entry)
    coordinator._raw_cache["GONE"] = {"trackingStates": "In Transit"}

    await coordinator._async_update_data()

    assert "GONE" not in coordinator._raw_cache
    assert DELIVERED_CODE in coordinator._raw_cache


async def test_update_fetches_parcels_concurrently(hass):
    """All tracked parcels go out in one gather, not one-by-one."""
    import asyncio

    entry = _entry_with(
        [{CONF_TRACKING_CODE: ACTIVE_CODE}, {CONF_TRACKING_CODE: DELIVERED_CODE}]
    )
    entry.add_to_hass(hass)
    in_flight = 0
    peak = 0

    async def _slow_fetch(code):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0)
        in_flight -= 1
        return active_sample(code)

    client = AsyncMock()
    client.async_get_parcel.side_effect = _slow_fetch
    coordinator = DelhiveryCoordinator(hass, client, entry)

    await coordinator._async_update_data()
    assert peak == 2


async def test_cache_only_poll_does_not_stamp_last_success(hass):
    """A poll served entirely from cache must not look like a success."""
    entry = _entry_with([{CONF_TRACKING_CODE: DELIVERED_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.return_value = delivered_sample()
    coordinator = DelhiveryCoordinator(hass, client, entry)
    await coordinator._async_update_data()
    stamp = coordinator.last_success_time
    assert stamp is not None

    client.async_get_parcel.side_effect = DelhiveryApiError("HTTP 500")
    await coordinator._async_update_data()  # served from cache
    assert coordinator.last_success_time == stamp


# ---------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------


async def test_first_refresh_fires_nothing(hass):
    """Otherwise every restart floods the user with "registered" events."""
    entry = _entry_with([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.return_value = active_sample()
    coordinator = DelhiveryCoordinator(hass, client, entry)

    fired = []
    for suffix in (
        "parcel_registered",
        "parcel_status_changed",
        "parcel_delivered",
        "parcel_delivery_time_changed",
    ):
        hass.bus.async_listen(f"{DOMAIN}_{suffix}", lambda e: fired.append(e))

    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert fired == []


async def test_event_carries_device_id(hass):
    """The status map ships empty, so a status-changed transition never
    naturally occurs between two non-empty payloads — a new barcode
    appearing (``registered``) is the reliable way to exercise device_id."""
    from homeassistant.helpers import device_registry as dr

    entry = _entry_with([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
    )
    client = AsyncMock()
    client.async_get_parcel.return_value = active_sample()
    coordinator = DelhiveryCoordinator(hass, client, entry)

    events = []
    hass.bus.async_listen(
        f"{DOMAIN}_parcel_registered", lambda e: events.append(e)
    )

    await coordinator._async_update_data()  # first refresh: suppressed

    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_PARCELS: [
                {CONF_TRACKING_CODE: ACTIVE_CODE},
                {CONF_TRACKING_CODE: OTHER_CODE},
            ],
        },
    )
    client.async_get_parcel.side_effect = lambda code: active_sample(code)
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert events[0].data["device_id"] == device.id


async def test_fires_registered_event_for_new_parcel(hass):
    entry = _entry_with([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.return_value = active_sample(ACTIVE_CODE)
    coordinator = DelhiveryCoordinator(hass, client, entry)

    events = []
    hass.bus.async_listen(f"{DOMAIN}_parcel_registered", lambda e: events.append(e))

    await coordinator._async_update_data()  # first refresh: suppressed

    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_PARCELS: [
                {CONF_TRACKING_CODE: ACTIVE_CODE},
                {CONF_TRACKING_CODE: OTHER_CODE},
            ],
        },
    )
    client.async_get_parcel.side_effect = lambda code: active_sample(code)
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["barcode"] == OTHER_CODE


async def test_transition_to_delivered_fires_delivered_not_status_changed(hass):
    """The hop to `delivered` fires only `_parcel_delivered`, never also
    `_parcel_status_changed` — the suite-wide event contract, now actually
    exercised by a real transition since `_STATUS_MAP` is filled."""
    entry = _entry_with([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    coordinator = DelhiveryCoordinator(hass, client, entry)

    status_changed = []
    delivered = []
    hass.bus.async_listen(
        f"{DOMAIN}_parcel_status_changed", lambda e: status_changed.append(e)
    )
    hass.bus.async_listen(
        f"{DOMAIN}_parcel_delivered", lambda e: delivered.append(e)
    )

    client.async_get_parcel.return_value = active_sample()
    await coordinator._async_update_data()  # first refresh: suppressed

    client.async_get_parcel.return_value = delivered_sample()
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert status_changed == []
    assert len(delivered) == 1
    assert delivered[0].data["barcode"] == ACTIVE_CODE


async def test_fires_status_changed_event_for_a_non_terminal_transition(hass):
    """A genuine intermediate transition (in_transit -> out_for_delivery)
    fires `_parcel_status_changed` with old/new status, now that
    `_STATUS_MAP` is filled from the first-party `hqStatus` vocabulary."""
    entry = _entry_with([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    coordinator = DelhiveryCoordinator(hass, client, entry)

    events = []
    hass.bus.async_listen(
        f"{DOMAIN}_parcel_status_changed", lambda e: events.append(e)
    )

    client.async_get_parcel.return_value = active_sample()
    await coordinator._async_update_data()  # first refresh: suppressed

    client.async_get_parcel.return_value = {
        **active_sample(),
        "hqStatus": "Dispatched",
    }
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["old_status"] == ParcelStatus.IN_TRANSIT
    assert events[0].data["new_status"] == ParcelStatus.OUT_FOR_DELIVERY


async def test_fires_delivery_time_changed_event(hass):
    entry = _entry_with([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    coordinator = DelhiveryCoordinator(hass, client, entry)

    events = []
    hass.bus.async_listen(
        f"{DOMAIN}_parcel_delivery_time_changed", lambda e: events.append(e)
    )

    client.async_get_parcel.return_value = active_sample()
    await coordinator._async_update_data()  # first refresh: suppressed

    moved = {"hqStatus": "In Transit", "PromiseDeliveryDate": "2026-04-29T18:00:00Z"}
    client.async_get_parcel.return_value = moved
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["old_planned_from"] == "2026-04-29T15:00:00Z"
    assert events[0].data["new_planned_from"] == "2026-04-29T18:00:00Z"


async def test_losing_the_eta_is_silent(hass):
    """value -> null just means the carrier lost the window; not worth an alert."""
    entry = _entry_with([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    coordinator = DelhiveryCoordinator(hass, client, entry)

    events = []
    hass.bus.async_listen(
        f"{DOMAIN}_parcel_delivery_time_changed", lambda e: events.append(e)
    )

    client.async_get_parcel.return_value = active_sample()
    await coordinator._async_update_data()

    dropped = {"hqStatus": "In Transit", "PromiseDeliveryDate": None}
    client.async_get_parcel.return_value = dropped
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert events == []
