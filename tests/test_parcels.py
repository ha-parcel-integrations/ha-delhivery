"""Tests for the pure parcel-mapping helpers.

These need no Home Assistant instance — the whole point of keeping
``parcels.py`` free of I/O is that the carrier-specific mapping can be tested
as plain functions. Field *names* are first-party (as of a 2026-08-09
correction), but no populated Delhivery ``data[]`` entry has ever been observed
on the wire (see the module docstring in ``parcels.py``), so every payload
used here is **synthetic, not captured** — see ``tests/payloads.py``'s module
docstring.
"""
from datetime import datetime, timedelta, timezone

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.delhivery.parcels as parcels_module
from custom_components.delhivery.const import (
    CAPABILITIES,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DOMAIN,
    KNOWN_CAPABILITIES,
    ParcelStatus,
)
from custom_components.delhivery.parcels import (
    apply_delivered_filter,
    build_history,
    describe_structure,
    map_parcel_status,
    normalize_parcel,
    parse_iso,
    sort_parcels_by_ts,
    to_iso_timestamp,
)

from .payloads import (
    ACTIVE_CODE,
    DELIVERED_CODE,
    active_sample,
    delivered_sample,
    ladder_fallback_sample,
    unmapped_status_sample,
)


@pytest.fixture(autouse=True)
def _reset_one_shot_warnings():
    """Clear the module's one-shot set so warning tests do not mask each other.

    ``_warned`` is process-lifetime state by design (each surprise logs once
    per HA *session*) — without this reset, whichever test
    happens to touch a given status/shape first would silence every later
    test asserting on the same warning.
    """
    parcels_module._warned.clear()
    yield
    parcels_module._warned.clear()

# ---------------------------------------------------------------------------
# map_parcel_status — 16 first-party-named values, all still "uncertain"
# ---------------------------------------------------------------------------

# The two vocabularies tracking.md documents, mirrored here for the
# parametrized coverage test below. Casing is exactly what the app itself
# compares against — do not "fix" it.
_HQ_STATUS_MAP = {
    "Manifested": ParcelStatus.REGISTERED,
    "Pending": ParcelStatus.REGISTERED,
    "Scheduled": ParcelStatus.REGISTERED,
    "Picked Up": ParcelStatus.IN_TRANSIT,
    "Collected": ParcelStatus.IN_TRANSIT,
    "In Transit": ParcelStatus.IN_TRANSIT,
    "Dispatched": ParcelStatus.OUT_FOR_DELIVERY,
    "Delivered": ParcelStatus.DELIVERED,
    "RTO": ParcelStatus.RETURNING,
    "DTO": ParcelStatus.RETURNING,
    "LOST": ParcelStatus.PROBLEM,
}
_LADDER_STATUS_MAP = {
    "PICKUP": ParcelStatus.IN_TRANSIT,
    "IN TRANSIT": ParcelStatus.IN_TRANSIT,
    "OUT FOR DELIVERY": ParcelStatus.OUT_FOR_DELIVERY,
    "DELIVERED": ParcelStatus.DELIVERED,
    "CANCELLED": ParcelStatus.PROBLEM,
}


def test_map_parcel_status_missing_is_unknown_silently(caplog):
    assert map_parcel_status(None) == ParcelStatus.UNKNOWN
    assert map_parcel_status("") == ParcelStatus.UNKNOWN
    assert caplog.text == ""


def test_map_parcel_status_unmapped_value_is_unknown_with_warning(caplog):
    """Outside both known vocabularies -> the ordinary one-shot warning."""
    assert map_parcel_status("Frozen") == ParcelStatus.UNKNOWN
    assert "Unrecognised Delhivery status 'Frozen'" in caplog.text
    assert "issues/new" in caplog.text


def test_unmapped_status_warns_only_once(caplog):
    assert map_parcel_status("Frozen") == ParcelStatus.UNKNOWN
    assert map_parcel_status("Frozen") == ParcelStatus.UNKNOWN
    assert caplog.text.count("Frozen") == 1


@pytest.mark.parametrize(
    "raw_status, expected", {**_HQ_STATUS_MAP, **_LADDER_STATUS_MAP}.items()
)
def test_map_parcel_status_covers_all_16_known_values(raw_status, expected):
    assert map_parcel_status(raw_status) == expected


def test_status_map_is_case_sensitive_lost_rto_dto_trap(caplog):
    """LOST/RTO/DTO are upper case among title-case hqStatus siblings —
    tracking.md's casing trap. A mis-cased variant must NOT match."""
    assert map_parcel_status("Lost") == ParcelStatus.UNKNOWN
    assert map_parcel_status("Rto") == ParcelStatus.UNKNOWN
    assert map_parcel_status("dto") == ParcelStatus.UNKNOWN
    assert "Unrecognised Delhivery status 'Lost'" in caplog.text
    assert "Unrecognised Delhivery status 'Rto'" in caplog.text


def test_status_map_ladder_and_hqstatus_do_not_collide():
    """'Delivered' (hqStatus, title case) and 'DELIVERED' (ladder, upper
    case) are two distinct dict keys mapping to the same ParcelStatus, not a
    single normalised one — confirming no case-folding happens anywhere."""
    assert map_parcel_status("Delivered") == ParcelStatus.DELIVERED
    assert map_parcel_status("DELIVERED") == ParcelStatus.DELIVERED
    assert map_parcel_status("delivered") == ParcelStatus.UNKNOWN


def test_mapped_status_warns_on_every_occurrence_not_once(caplog):
    """Distinct from the unmapped-value one-shot warning: every one of the
    16 known values is still unconfirmed on the wire, so a mapped hit
    self-reports every time (mirrors ha-nova-post's `_warn_uncertain_status`,
    generalised to the whole map)."""
    map_parcel_status("Delivered")
    map_parcel_status("Delivered")
    map_parcel_status("Delivered")
    assert caplog.text.count("mapped to 'delivered'") == 3


def test_uncertain_status_warning_names_the_raw_value_and_mapping(caplog):
    map_parcel_status("RTO")
    assert "Delhivery reported status 'RTO'" in caplog.text
    assert "mapped to 'returning'" in caplog.text
    assert "issues/new" in caplog.text


def test_map_parcel_status_confirmed_entry_is_silent(monkeypatch, caplog):
    """Once a real capture confirms a value, removing it from
    `_UNCERTAIN_STATUSES` (not `_STATUS_MAP`) is what silences the
    every-occurrence warning — proven here since none of the shipped 16 are
    confirmed yet, so this path is otherwise never exercised."""
    monkeypatch.setattr(
        parcels_module,
        "_UNCERTAIN_STATUSES",
        frozenset(parcels_module._UNCERTAIN_STATUSES - {"Delivered"}),
    )
    assert map_parcel_status("Delivered") == ParcelStatus.DELIVERED
    assert caplog.text == ""


# ---------------------------------------------------------------------------
# status resolution — hqStatus first, trackingStates[].label ladder fallback
# ---------------------------------------------------------------------------


def test_normalize_uses_hqstatus_when_present():
    raw = {"hqStatus": "Dispatched"}
    parcel = normalize_parcel(raw, awb=ACTIVE_CODE)
    assert parcel["raw_status"] == "Dispatched"
    assert parcel["status"] == ParcelStatus.OUT_FOR_DELIVERY


def test_normalize_falls_back_to_ladder_when_hqstatus_absent():
    raw = {
        "trackingStates": [
            {"label": "PICKUP", "stepStatus": "finished"},
            {"label": "OUT FOR DELIVERY", "stepStatus": "current"},
        ]
    }
    parcel = normalize_parcel(raw, awb=ACTIVE_CODE)
    assert parcel["raw_status"] == "OUT FOR DELIVERY"
    assert parcel["status"] == ParcelStatus.OUT_FOR_DELIVERY


def test_ladder_fallback_sample_end_to_end():
    parcel = normalize_parcel(ladder_fallback_sample(), awb=ACTIVE_CODE)
    assert parcel["raw_status"] == "OUT FOR DELIVERY"
    assert parcel["status"] == ParcelStatus.OUT_FOR_DELIVERY


def test_ladder_fallback_prefers_current_over_a_later_unfinished_step():
    """`trackingStates` lists every step regardless of progress — the last
    array entry is not necessarily current. A step marked 'current' must win
    over a later 'unfinished' placeholder (tracking.md's step-ladder note)."""
    raw = {
        "trackingStates": [
            {"label": "PICKUP", "stepStatus": "finished"},
            {"label": "IN TRANSIT", "stepStatus": "current"},
            {"label": "OUT FOR DELIVERY", "stepStatus": "unfinished"},
            {"label": "DELIVERED", "stepStatus": "unfinished"},
        ]
    }
    parcel = normalize_parcel(raw, awb=ACTIVE_CODE)
    assert parcel["raw_status"] == "IN TRANSIT"
    assert parcel["status"] == ParcelStatus.IN_TRANSIT


def test_ladder_fallback_uses_last_finished_when_nothing_current():
    raw = {
        "trackingStates": [
            {"label": "PICKUP", "stepStatus": "finished"},
            {"label": "IN TRANSIT", "stepStatus": "finished"},
            {"label": "OUT FOR DELIVERY", "stepStatus": "unfinished"},
        ]
    }
    parcel = normalize_parcel(raw, awb=ACTIVE_CODE)
    assert parcel["raw_status"] == "IN TRANSIT"


def test_ladder_fallback_skips_a_step_without_a_string_label(caplog):
    raw = {
        "trackingStates": [
            {"stepStatus": "current"},  # no "label" key at all
            {"label": "PICKUP", "stepStatus": "finished"},
        ]
    }
    parcel = normalize_parcel(raw, awb=ACTIVE_CODE)
    assert parcel["raw_status"] == "PICKUP"
    # a step missing a label is not the same as a malformed shape — no warning
    assert "trackingStates field has a shape" not in caplog.text


def test_normalize_hqstatus_wrong_type_warns_and_falls_back(caplog):
    raw = {
        "hqStatus": 123,
        "trackingStates": [{"label": "PICKUP", "stepStatus": "current"}],
    }
    parcel = normalize_parcel(raw, awb=ACTIVE_CODE)
    assert "hqStatus field has a shape" in caplog.text
    assert parcel["raw_status"] == "PICKUP"


def test_normalize_hqstatus_empty_string_falls_back_silently(caplog):
    raw = {
        "hqStatus": "",
        "trackingStates": [{"label": "PICKUP", "stepStatus": "current"}],
    }
    parcel = normalize_parcel(raw, awb=ACTIVE_CODE)
    assert parcel["raw_status"] == "PICKUP"
    assert "hqStatus field has a shape" not in caplog.text


def test_normalize_ignores_non_list_trackingstates(caplog):
    raw = {"trackingStates": "Delivered"}
    parcel = normalize_parcel(raw, awb=ACTIVE_CODE)
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["raw_status"] is None
    assert "trackingStates field has a shape we have never parsed" in caplog.text


def test_normalize_ignores_list_of_non_dict_steps(caplog):
    """A list of bare strings is exactly the earlier reconstruction's wrong
    guess for trackingStates — it must degrade to unknown, not crash."""
    raw = {"trackingStates": ["Manifested", "In Transit"]}
    parcel = normalize_parcel(raw, awb=ACTIVE_CODE)
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert "trackingStates field has a shape" in caplog.text


def test_normalize_skips_malformed_steps_but_uses_good_ones(caplog):
    raw = {
        "trackingStates": [
            {"label": "PICKUP", "stepStatus": "current"},
            "garbage",
        ]
    }
    parcel = normalize_parcel(raw, awb=ACTIVE_CODE)
    assert parcel["raw_status"] == "PICKUP"
    assert "trackingStates field has a shape" in caplog.text


def test_normalize_empty_trackingstates_is_silent(caplog):
    for value in (None, [], ""):
        parcel = normalize_parcel({"trackingStates": value}, awb=ACTIVE_CODE)
        assert parcel["raw_status"] is None
    assert "trackingStates field has a shape" not in caplog.text


# ---------------------------------------------------------------------------
# build_history — trackingStates[].scans[], not a top-level scans key
# ---------------------------------------------------------------------------


def test_build_history_empty_inputs():
    assert build_history(None) == []
    assert build_history([]) == []
    assert build_history("") == []


def test_build_history_flattens_scans_across_steps_oldest_to_newest():
    history = build_history(delivered_sample()["trackingStates"])
    assert [entry["raw_status"] for entry in history] == [
        "PICKUP",
        "IN TRANSIT",
        "DELIVERED",
    ]
    assert [entry["status"] for entry in history] == [
        ParcelStatus.IN_TRANSIT,
        ParcelStatus.IN_TRANSIT,
        ParcelStatus.DELIVERED,
    ]
    assert history[0]["timestamp"] == "2026-04-27T23:03:58Z"
    assert history == sorted(history, key=lambda entry: entry["timestamp"])


def test_normalize_history_uses_trackingstates_when_opted_in():
    parcel = normalize_parcel(
        delivered_sample(), awb=DELIVERED_CODE, include_history=True
    )
    assert len(parcel["history"]) == 3


def test_normalize_history_is_none_when_option_off():
    parcel = normalize_parcel(delivered_sample(), awb=DELIVERED_CODE)
    assert parcel["history"] is None


def test_build_history_skips_steps_missing_a_scans_key():
    tracking_states = [
        {"label": "PICKUP", "stepStatus": "finished"},  # no "scans" at all
        {
            "label": "IN TRANSIT",
            "stepStatus": "current",
            "scans": [
                {
                    "scanDateTime": "2026-04-28T15:52:17Z",
                    "scanNslRemark": "In transit",
                    "cityLocation": "Delhi Hub",
                }
            ],
        },
    ]
    history = build_history(tracking_states)
    assert len(history) == 1
    assert history[0]["raw_status"] == "IN TRANSIT"


def test_build_history_warns_on_malformed_scans_shape(caplog):
    tracking_states = [
        {"label": "PICKUP", "stepStatus": "finished", "scans": "not-a-list"}
    ]
    assert build_history(tracking_states) == []
    assert "trackingStates[].scans field has a shape" in caplog.text


def test_build_history_warns_on_non_dict_scan_entry(caplog):
    tracking_states = [
        {"label": "PICKUP", "stepStatus": "finished", "scans": ["not-a-dict"]}
    ]
    assert build_history(tracking_states) == []
    assert "trackingStates[].scans field has a shape" in caplog.text


def test_build_history_warns_on_non_list_top_level(caplog):
    assert build_history("garbage") == []
    assert "trackingStates field has a shape" in caplog.text


def test_build_history_warns_on_malformed_step(caplog):
    tracking_states = [
        {
            "label": "PICKUP",
            "stepStatus": "finished",
            "scans": [
                {
                    "scanDateTime": "2026-04-27T23:03:58Z",
                    "scanNslRemark": "x",
                    "cityLocation": "y",
                }
            ],
        },
        "garbage",
    ]
    history = build_history(tracking_states)
    assert len(history) == 1
    assert "trackingStates field has a shape" in caplog.text


def test_build_history_skips_scan_missing_a_timestamp():
    tracking_states = [
        {
            "label": "PICKUP",
            "stepStatus": "finished",
            "scans": [{"scanNslRemark": "no timestamp", "cityLocation": "y"}],
        }
    ]
    assert build_history(tracking_states) == []


def test_build_history_converts_epoch_millis_scan_datetime():
    tracking_states = [
        {
            "label": "DELIVERED",
            "stepStatus": "current",
            "scans": [
                {
                    "scanDateTime": 1784203767167,
                    "scanNslRemark": "x",
                    "cityLocation": "y",
                }
            ],
        }
    ]
    history = build_history(tracking_states)
    assert history[0]["timestamp"] == "2026-07-16T12:09:27.167000+00:00"


def test_build_history_keeps_unparseable_timestamps_at_the_end():
    tracking_states = [
        {
            "label": "PICKUP",
            "stepStatus": "finished",
            "scans": [
                {
                    "scanDateTime": "garbage-timestamp",
                    "scanNslRemark": "x",
                    "cityLocation": "y",
                },
                {
                    "scanDateTime": "2026-04-27T23:03:58Z",
                    "scanNslRemark": "x",
                    "cityLocation": "y",
                },
            ],
        }
    ]
    history = build_history(tracking_states)
    assert history[0]["timestamp"] == "2026-04-27T23:03:58Z"
    assert history[-1]["timestamp"] == "garbage-timestamp"


def test_build_history_caps_to_max_events():
    scans = [
        {
            "scanDateTime": f"2026-01-{day:02d}T00:00:00Z",
            "scanNslRemark": "x",
            "cityLocation": "y",
        }
        for day in range(1, 26)
    ]
    tracking_states = [{"label": "PICKUP", "stepStatus": "finished", "scans": scans}]
    history = build_history(tracking_states, max_events=20)
    assert len(history) == 20
    assert history[0]["timestamp"].startswith("2026-01-06")
    assert history[-1]["timestamp"].startswith("2026-01-25")


def test_build_history_events_also_warn_uncertain_status(caplog):
    """Each history entry's status is resolved through the same
    (still-unconfirmed) `_STATUS_MAP`, so it self-reports too."""
    tracking_states = [
        {
            "label": "PICKUP",
            "stepStatus": "finished",
            "scans": [
                {
                    "scanDateTime": "2026-04-27T23:03:58Z",
                    "scanNslRemark": "x",
                    "cityLocation": "y",
                }
            ],
        }
    ]
    build_history(tracking_states)
    assert "mapped to 'in_transit'" in caplog.text


# ---------------------------------------------------------------------------
# describe_structure — types only, safe to paste
# ---------------------------------------------------------------------------


def test_describe_structure_reports_types_not_values():
    shape = describe_structure({"a": "secret", "b": [1, 2]}, "root")
    assert "str" in shape
    assert "int" in shape
    assert "secret" not in shape


def test_describe_structure_handles_empty_and_deep_values():
    assert describe_structure({}, "root") == "root: empty object"
    assert describe_structure([], "root") == "root[]: empty list"
    nested = {"a": {"b": {"c": {"d": {"e": "deep"}}}}}
    assert "nested deeper than" in describe_structure(nested, "root")


# ---------------------------------------------------------------------------
# timestamp helpers
# ---------------------------------------------------------------------------


def test_parse_iso_handles_z_naive_and_garbage():
    assert parse_iso("2026-04-29T13:12:42Z").tzinfo is not None
    assert parse_iso("2026-04-29T13:12:42").tzinfo == timezone.utc
    assert parse_iso("not-a-date") is None
    assert parse_iso(None) is None


def test_to_iso_timestamp_converts_epoch_milliseconds():
    assert to_iso_timestamp(1784203767167) == "2026-07-16T12:09:27.167000+00:00"
    assert to_iso_timestamp("2026-04-29T13:12:42Z") == "2026-04-29T13:12:42Z"
    assert to_iso_timestamp(None) is None
    assert to_iso_timestamp(10**20) is None  # out of range -> None, never raises


# ---------------------------------------------------------------------------
# normalize_parcel — the canonical contract
# ---------------------------------------------------------------------------

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
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


def test_normalize_publishes_exactly_the_canonical_keys():
    """The aggregator and cross-carrier dashboards depend on this key set."""
    assert list(normalize_parcel(delivered_sample(), awb=DELIVERED_CODE)) == CANONICAL_KEYS


def test_normalize_barcode_is_the_requested_awb_never_the_payload():
    """barcode is the highest-confidence field in the whole mapping — it is
    the value passed as `wbn`, never read off the payload."""
    raw = {"hqStatus": "In Transit"}
    parcel = normalize_parcel(raw, awb="9999999999999")
    assert parcel["barcode"] == "9999999999999"


def test_normalize_delivered_sample_reports_delivered():
    parcel = normalize_parcel(delivered_sample(), awb=DELIVERED_CODE)
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["delivered"] is True
    # the DELIVERED step's own scan, resolved regardless of include_history
    assert parcel["delivered_at"] == "2026-04-29T13:12:42Z"
    # suppressed once delivered — an ETA is moot after the fact
    assert parcel["planned_from"] is None


def test_normalize_delivered_at_resolves_without_include_history():
    """`delivered_at` must not depend on the history opt-in."""
    parcel = normalize_parcel(
        delivered_sample(), awb=DELIVERED_CODE, include_history=False
    )
    assert parcel["history"] is None
    assert parcel["delivered_at"] == "2026-04-29T13:12:42Z"


def test_normalize_not_delivered_has_no_delivered_at():
    parcel = normalize_parcel(active_sample(), awb=ACTIVE_CODE)
    assert parcel["delivered"] is False
    assert parcel["delivered_at"] is None


def test_normalize_delivered_with_no_parseable_scan_is_none(caplog):
    """A DELIVERED status with no usable scans[] timestamp stays None rather
    than guessing — never invented from an unparseable entry."""
    raw = {"hqStatus": "Delivered"}
    parcel = normalize_parcel(raw, awb=DELIVERED_CODE)
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] is None


def test_normalize_active_sample_reports_in_transit():
    parcel = normalize_parcel(active_sample(), awb=ACTIVE_CODE)
    assert parcel["status"] == ParcelStatus.IN_TRANSIT
    assert parcel["delivered"] is False


def test_normalize_promise_date_field_is_capital_p():
    raw = {"hqStatus": "In Transit", "PromiseDeliveryDate": "2026-04-29T15:00:00Z"}
    parcel = normalize_parcel(raw, awb=ACTIVE_CODE)
    assert parcel["planned_from"] == "2026-04-29T15:00:00Z"
    assert parcel["planned_to"] is None


def test_normalize_lowercase_promise_date_key_is_ignored():
    """`promiseDeliveryDate` (lower case) was the earlier reconstruction's
    mistake — the real field is `PromiseDeliveryDate` (tracking.md)."""
    raw = {"hqStatus": "In Transit", "promiseDeliveryDate": "2026-04-29T15:00:00Z"}
    parcel = normalize_parcel(raw, awb=ACTIVE_CODE)
    assert parcel["planned_from"] is None


def test_normalize_unparseable_promise_date_warns_and_is_dropped(caplog):
    raw = {"hqStatus": "In Transit", "PromiseDeliveryDate": "not-a-date"}
    parcel = normalize_parcel(raw, awb=ACTIVE_CODE)
    assert parcel["planned_from"] is None
    assert "PromiseDeliveryDate did not parse" in caplog.text


def test_normalize_missing_promise_date_is_silent(caplog):
    for value in (None, ""):
        parcel = normalize_parcel(
            {"hqStatus": "In Transit", "PromiseDeliveryDate": value},
            awb=ACTIVE_CODE,
        )
        assert parcel["planned_from"] is None
    assert "PromiseDeliveryDate did not parse" not in caplog.text


def test_normalize_pending_placeholder():
    """A tracked-but-not-yet-scanned/unknown AWB still yields a full parcel
    dict, from an empty raw payload — no shape warnings fire for it."""
    parcel = normalize_parcel({}, awb="0000000000000")
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["delivered"] is False
    assert parcel["raw_status"] is None
    assert parcel["weight"] is None
    assert parcel["dimensions"] is None
    assert parcel["history"] is None
    assert parcel["sender"] is None
    assert parcel["receiver"] is None
    # url is always populated, even for a not-yet-scanned/pending placeholder
    assert parcel["url"] == "https://www.delhivery.com/track-v2/package/0000000000000"


def test_normalize_sender_receiver_from_first_party_fields():
    raw = {
        "hqStatus": "In Transit",
        "clientName": "Acme Traders",
        "consignee": "Jane Doe",
    }
    parcel = normalize_parcel(raw, awb=ACTIVE_CODE)
    assert parcel["sender"] == "Acme Traders"
    assert parcel["receiver"] == "Jane Doe"


def test_normalize_sender_receiver_absent_is_none():
    parcel = normalize_parcel({"hqStatus": "In Transit"}, awb=ACTIVE_CODE)
    assert parcel["sender"] is None
    assert parcel["receiver"] is None


def test_normalize_url_always_populated_from_the_confirmed_template():
    parcel = normalize_parcel({"hqStatus": "In Transit"}, awb="9999999999999")
    assert (
        parcel["url"]
        == "https://www.delhivery.com/track-v2/package/9999999999999"
    )


def test_normalize_still_unconfirmed_fields_are_always_none():
    """pickup/pickup_point/weight/dimensions stay None — out of scope for
    this pass per tracking.md (weight is bucketed to an icon; no field found
    for the rest)."""
    parcel = normalize_parcel(delivered_sample(), awb=DELIVERED_CODE, include_history=True)
    assert parcel["pickup"] is False
    assert parcel["pickup_point"] is None
    assert parcel["weight"] is None
    assert parcel["dimensions"] is None


def test_capabilities_are_known_values():
    """A typo here would silently misreport this carrier on the docs site."""
    assert CAPABILITIES <= KNOWN_CAPABILITIES


def test_capabilities_match_the_unconfirmed_field_gap():
    """CAPABILITIES must agree with test_normalize_still_unconfirmed_fields_are_always_none
    and test_normalize_promise_date_field_is_capital_p / test_normalize_history_uses_trackingstates_when_opted_in."""
    assert CAPABILITIES == {"delivery_window", "url", "history"}


def test_normalize_keeps_raw_payload():
    raw = active_sample()
    assert normalize_parcel(raw, awb=ACTIVE_CODE)["raw"] is raw


def test_normalize_unmapped_status_sample_end_to_end(caplog):
    parcel = normalize_parcel(unmapped_status_sample(), awb=ACTIVE_CODE)
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["raw_status"] == "Out for Delivery"
    assert parcel["planned_from"] is None
    assert "Unrecognised Delhivery status 'Out for Delivery'" in caplog.text


def test_normalize_warns_on_unexpected_top_level_key(caplog):
    raw = {"hqStatus": "In Transit", "someNewField": {"a": 1}}
    normalize_parcel(raw, awb=ACTIVE_CODE)
    assert "someNewField" in caplog.text
    assert "field we do not map yet" in caplog.text


def test_normalize_known_keys_never_warn_as_unexpected(caplog):
    normalize_parcel(delivered_sample(), awb=DELIVERED_CODE)
    assert "field we do not map yet" not in caplog.text


def test_normalize_full_known_key_inventory_never_warns(caplog):
    """Every field name tracking.md's "order object" table lists — the full
    first-party inventory — must be silent, even though most stay unused."""
    raw = {key: None for key in parcels_module._KNOWN_TOP_LEVEL_KEYS}
    raw["hqStatus"] = "In Transit"
    normalize_parcel(raw, awb=ACTIVE_CODE)
    assert "field we do not map yet" not in caplog.text


def test_top_level_scans_key_now_warns_as_unexpected(caplog):
    """`scans` moved under `trackingStates[]` — a genuine top-level `scans`
    key is now a surprise worth reporting, not a known field (the earlier
    reconstruction's mistake)."""
    raw = {"hqStatus": "In Transit", "scans": []}
    normalize_parcel(raw, awb=ACTIVE_CODE)
    assert "scans" in caplog.text
    assert "field we do not map yet" in caplog.text


def test_normalize_logs_first_populated_payload_once_ever(caplog):
    normalize_parcel({"hqStatus": "In Transit"}, awb="1")
    normalize_parcel({"hqStatus": "Dispatched"}, awb="2")
    assert caplog.text.count("First real Delhivery data[] entry ever captured") == 1


def test_normalize_pending_placeholder_never_triggers_first_payload_log(caplog):
    normalize_parcel({}, awb="1")
    assert "First real Delhivery data[] entry ever captured" not in caplog.text


def test_normalize_first_payload_log_is_redacted(caplog):
    # clientName is one of the first-party redaction keys (const.DIAGNOSTICS_REDACT_KEYS)
    raw = {"hqStatus": "In Transit", "clientName": "Secret Corp"}
    normalize_parcel(raw, awb=ACTIVE_CODE)
    assert "Secret Corp" not in caplog.text
    assert "**REDACTED**" in caplog.text


# ---------------------------------------------------------------------------
# sort_parcels_by_ts
# ---------------------------------------------------------------------------


def test_sort_parcels_ascending_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "planned_from": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "planned_from": None},
        {"barcode": "c", "planned_from": "2026-05-01T10:00:00Z"},
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "planned_from")]
    assert ordered == ["c", "a", "b"]


def test_sort_parcels_descending_still_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "delivered_at": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "delivered_at": "nonsense"},
        {"barcode": "c", "delivered_at": "2026-05-01T10:00:00Z"},
    ]
    ordered = [
        p["barcode"]
        for p in sort_parcels_by_ts(parcels, "delivered_at", descending=True)
    ]
    assert ordered == ["a", "c", "b"]


# ---------------------------------------------------------------------------
# apply_delivered_filter
# ---------------------------------------------------------------------------


def _entry(filter_type: str, amount: int) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_DELIVERED_FILTER_TYPE: filter_type,
            CONF_DELIVERED_FILTER_AMOUNT: amount,
        },
        unique_id=DOMAIN,
    )


def _delivered_pair() -> list[dict]:
    now = datetime.now(timezone.utc)
    return [
        {"barcode": "RECENT", "delivered_at": (now - timedelta(days=1)).isoformat()},
        {"barcode": "OLD", "delivered_at": (now - timedelta(days=30)).isoformat()},
    ]


def test_delivered_filter_by_days():
    kept = apply_delivered_filter(_delivered_pair(), _entry("days", 7))
    assert [p["barcode"] for p in kept] == ["RECENT"]


def test_delivered_filter_by_count():
    parcels = _delivered_pair()
    assert apply_delivered_filter(parcels, _entry("parcels", 1)) == parcels[:1]


def test_delivered_filter_keeps_unparseable_timestamp():
    """Better to show a parcel with a broken date than to silently drop it."""
    parcels = [{"barcode": "WEIRD", "delivered_at": "nonsense"}]
    assert apply_delivered_filter(parcels, _entry("days", 7)) == parcels
