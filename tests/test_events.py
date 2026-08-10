"""Tests for the Delhivery bus events.

``_fire_change_events`` works on *normalised* parcels, so the full event
contract — including the terminal hop to ``delivered`` — can be verified by
driving it directly with hand-built parcel dicts, independent of
``parcels.py``'s status mapping (covered separately by ``test_parcels.py``
and ``test_coordinator.py``'s hqStatus-driven transition tests).
"""
from unittest.mock import AsyncMock

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.delhivery.const import CONF_PARCELS, DOMAIN, ParcelStatus
from custom_components.delhivery.coordinator import DelhiveryCoordinator


def _coordinator(hass) -> DelhiveryCoordinator:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN, options={CONF_PARCELS: []}
    )
    entry.add_to_hass(hass)
    return DelhiveryCoordinator(hass, AsyncMock(), entry)


def _parcel(barcode="AWB1", status=ParcelStatus.IN_TRANSIT, **extra) -> dict:
    return {
        "barcode": barcode,
        "status": status,
        "planned_from": None,
        "planned_to": None,
        **extra,
    }


def _listen(hass, event: str) -> list:
    captured: list = []
    hass.bus.async_listen(f"{DOMAIN}_{event}", captured.append)
    return captured


async def test_status_change_fires_status_changed(hass):
    coordinator = _coordinator(hass)
    coordinator._known_state = {"AWB1": ParcelStatus.REGISTERED}
    events = _listen(hass, "parcel_status_changed")

    coordinator._fire_change_events([_parcel(status=ParcelStatus.IN_TRANSIT)])
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["old_status"] == ParcelStatus.REGISTERED
    assert events[0].data["new_status"] == ParcelStatus.IN_TRANSIT


async def test_delivery_fires_only_the_delivered_event(hass):
    """The terminal hop fires exactly one, dedicated event — never both."""
    coordinator = _coordinator(hass)
    coordinator._known_state = {"AWB1": ParcelStatus.OUT_FOR_DELIVERY}
    delivered = _listen(hass, "parcel_delivered")
    changed = _listen(hass, "parcel_status_changed")

    coordinator._fire_change_events([_parcel(status=ParcelStatus.DELIVERED)])
    await hass.async_block_till_done()

    assert len(delivered) == 1
    assert changed == []


async def test_a_barcode_first_seen_delivered_fires_nothing(hass):
    coordinator = _coordinator(hass)
    coordinator._known_state = {}
    registered = _listen(hass, "parcel_registered")
    delivered = _listen(hass, "parcel_delivered")

    coordinator._fire_change_events([_parcel(status=ParcelStatus.DELIVERED)])
    await hass.async_block_till_done()

    assert registered == []
    assert delivered == []


async def test_new_barcode_not_delivered_fires_registered(hass):
    coordinator = _coordinator(hass)
    coordinator._known_state = {}
    events = _listen(hass, "parcel_registered")

    coordinator._fire_change_events([_parcel(status=ParcelStatus.UNKNOWN)])
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["barcode"] == "AWB1"


async def test_unchanged_status_fires_nothing(hass):
    coordinator = _coordinator(hass)
    coordinator._known_state = {"AWB1": ParcelStatus.IN_TRANSIT}
    events = _listen(hass, "parcel_status_changed")

    coordinator._fire_change_events([_parcel(status=ParcelStatus.IN_TRANSIT)])
    await hass.async_block_till_done()

    assert events == []


async def test_parcels_without_a_barcode_are_skipped(hass):
    coordinator = _coordinator(hass)
    coordinator._known_state = {}
    events = _listen(hass, "parcel_registered")

    coordinator._fire_change_events([_parcel(barcode=None)])
    await hass.async_block_till_done()

    assert events == []


async def test_no_events_fire_before_the_first_refresh(hass):
    """``_known_state`` is ``None`` until the first ``_async_update_data``
    completes — silent by design, so a restart does not flood "registered"."""
    coordinator = _coordinator(hass)
    assert coordinator._known_state is None
    events = _listen(hass, "parcel_registered")

    coordinator._fire_change_events([_parcel()])
    await hass.async_block_till_done()

    assert events == []


async def test_new_eta_fires_delivery_time_changed(hass):
    coordinator = _coordinator(hass)
    coordinator._known_state = {"AWB1": ParcelStatus.IN_TRANSIT}
    coordinator._known_delivery_times = {"AWB1": (None, None)}
    events = _listen(hass, "parcel_delivery_time_changed")

    coordinator._fire_change_events(
        [_parcel(planned_from="2026-05-01T10:00:00Z", planned_to="2026-05-01T12:00:00Z")]
    )
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["new_planned_from"] == "2026-05-01T10:00:00Z"
    assert events[0].data["old_planned_from"] is None


async def test_dropping_an_eta_is_intentionally_silent(hass):
    """value -> null just means the carrier lost the window; not worth alerting."""
    coordinator = _coordinator(hass)
    coordinator._known_state = {"AWB1": ParcelStatus.IN_TRANSIT}
    coordinator._known_delivery_times = {"AWB1": ("2026-05-01T10:00:00Z", None)}
    events = _listen(hass, "parcel_delivery_time_changed")

    coordinator._fire_change_events([_parcel(planned_from=None)])
    await hass.async_block_till_done()

    assert events == []
