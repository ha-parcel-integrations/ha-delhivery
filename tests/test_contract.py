"""The parcel contract's checks, run over every normalised sample."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from custom_components.delhivery.calendar import DelhiveryDeliveriesCalendar
from custom_components.delhivery.const import ParcelStatus
from custom_components.delhivery.parcels import normalize_parcel, parse_iso
from custom_components.delhivery.sensor import DelhiveryNextDeliverySensor

from .payloads import active_sample, delivered_sample

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "contents",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]


def _with(**fields) -> dict:
    sample = active_sample()
    sample.update(fields)
    return sample


SAMPLES = {
    "delivered": delivered_sample,
    "active": active_sample,
    "returning": lambda: _with(hqStatus="RTO"),
    "returned": lambda: _with(hqStatus="DTO"),
    "problem": lambda: _with(hqStatus="LOST"),
    "moment_only": lambda: _with(PromiseDeliveryDate="2099-04-29T13:00:00Z"),
    "date_only": lambda: _with(PromiseDeliveryDate="2099-04-29"),
    "bare": dict,
}


@pytest.fixture(params=SAMPLES)
def parcel(request) -> dict:
    return normalize_parcel(SAMPLES[request.param](), awb="1234567890123")


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_delivered_follows_status(parcel):
    assert parcel["delivered"] is (parcel["status"] is ParcelStatus.DELIVERED)


def test_delivered_at_only_when_delivered(parcel):
    if not parcel["delivered"]:
        assert parcel["delivered_at"] is None


def test_planned_window_is_ordered_and_anchored(parcel):
    if parcel["planned_to"] is not None:
        assert parcel["planned_from"] is not None
        assert parse_iso(parcel["planned_to"]) > parse_iso(parcel["planned_from"])


def test_planned_is_empty_when_the_parcel_is_done_or_waiting(parcel):
    if parcel["status"] in (
        ParcelStatus.DELIVERED,
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
    ):
        assert parcel["planned_from"] is None
        assert parcel["planned_to"] is None


def test_pickup_is_a_bool_and_the_point_needs_it(parcel):
    assert isinstance(parcel["pickup"], bool)
    if not parcel["pickup"]:
        assert parcel["pickup_point"] is None


def test_a_delivered_parcel_keeps_its_own_delivery_time():
    parcel = normalize_parcel(delivered_sample(), awb="9876543210987")
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] is not None
    assert parcel["pickup"] is False


def test_a_delivery_promise_is_emptied_once_returning():
    parcel = normalize_parcel(
        _with(hqStatus="RTO", PromiseDeliveryDate="2099-04-29"), awb="1"
    )
    assert parcel["status"] is ParcelStatus.RETURNING
    assert parcel["delivered"] is False
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None


def test_a_moment_ends_at_the_end_of_that_local_day():
    parcel = normalize_parcel(
        _with(PromiseDeliveryDate="2099-04-29T13:00:00Z"), awb="1"
    )
    end = datetime.fromisoformat(parcel["planned_to"])
    assert (end.hour, end.minute, end.second, end.microsecond) == (23, 59, 59, 0)
    assert end.utcoffset() == timedelta(hours=5, minutes=30)
    assert end.date() == parse_iso(parcel["planned_from"]).date()


def test_a_naive_moment_is_dated_in_the_carrier_zone():
    parcel = normalize_parcel(_with(PromiseDeliveryDate="2099-04-29T10:00:00"), awb="1")
    assert parcel["planned_from"] == "2099-04-29T10:00:00+05:30"
    assert parcel["planned_to"] == "2099-04-29T23:59:59+05:30"


def test_a_date_covers_the_whole_day():
    parcel = normalize_parcel(_with(PromiseDeliveryDate="2099-04-29"), awb="1")
    start = datetime.fromisoformat(parcel["planned_from"])
    end = datetime.fromisoformat(parcel["planned_to"])
    assert (start.hour, start.minute, start.second) == (0, 0, 0)
    assert (end.hour, end.minute, end.second, end.microsecond) == (23, 59, 59, 0)
    assert start.date() == end.date()


def _coordinator(data: list[dict]) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = data
    return coordinator


def test_next_delivery_skips_an_ended_window():
    ended = normalize_parcel(_with(PromiseDeliveryDate="2000-01-01"), awb="OLD")
    ahead = normalize_parcel(_with(PromiseDeliveryDate="2099-01-01"), awb="NEW")
    sensor = DelhiveryNextDeliverySensor(_coordinator([ended, ahead]), MagicMock())
    assert sensor.native_value == datetime.fromisoformat("2099-01-01T00:00:00+05:30")
    assert sensor.extra_state_attributes["barcode"] == "NEW"
    assert (
        DelhiveryNextDeliverySensor(_coordinator([ended]), MagicMock()).native_value
        is None
    )


def test_a_whole_day_window_is_an_all_day_event():
    data = [normalize_parcel(_with(PromiseDeliveryDate="2099-01-01"), awb="DAY")]
    event = DelhiveryDeliveriesCalendar(_coordinator(data), MagicMock())._events()[0]
    assert event.start.isoformat() == "2099-01-01"
    assert event.end.isoformat() == "2099-01-02"


def test_a_moment_stays_a_timed_event():
    data = [
        normalize_parcel(_with(PromiseDeliveryDate="2099-01-01T10:00:00Z"), awb="M")
    ]
    event = DelhiveryDeliveriesCalendar(_coordinator(data), MagicMock())._events()[0]
    assert event.start == datetime(2099, 1, 1, 10, 0, tzinfo=UTC)
    assert isinstance(event.start, datetime)
