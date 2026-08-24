"""Sample Delhivery API payloads shared by the test modules.

**Mostly synthetic.** No real Delhivery AWB was run through the endpoint
(`carrier-research/api/delhivery/tracking.md`) until 2026-08-24, when three
real "LOST" parcels came through a live poll — `LOST_CAPTURE_ENTRY` below is
that capture, verbatim except for `awb`/`referenceNo`/the nested
`cityLocation` (redacted at source per `const.DIAGNOSTICS_REDACT_KEYS`,
replaced here with obvious placeholders, never fabricated as if real).
`NOT_FOUND_ENVELOPE` is the other verbatim capture on file — the not-found
envelope on a bogus AWB. Everything else in this module uses the first-party
field *names* a 2026-08-09 correction confirmed (`hqStatus`,
`trackingStates[].{label,stepStatus,scans}`,
`scans[].{scanDateTime,scanNslRemark,cityLocation}`, `PromiseDeliveryDate`,
`clientName`, `consignee`), but the values, nesting and optionality are still
invented for test coverage only — it proves the code does not crash and
degrades gracefully on *a* plausible shape for statuses other than LOST, not
that it is *the* real shape.
"""
from __future__ import annotations

ACTIVE_CODE = "1234567890123"
DELIVERED_CODE = "9876543210987"

# The only verbatim capture in this repo — a bogus 13-digit AWB, control-
# tested 2026-08-06 and re-probed 2026-08-09
# (tracking.md#the-captured-probe-failure-response-not-success).
NOT_FOUND_ENVELOPE = {
    "statusCode": 200,
    "message": "invalid AWB or very old package",
    "data": [],
}


LOST_CAPTURE_CODE = "39777010000022"

# Verbatim first real data[] entry (2026-08-24), redacted per
# const.DIAGNOSTICS_REDACT_KEYS the same way diagnostics.py and parcels.py's
# first-payload WARNING redact it. Notable, and deliberately *not* patched
# around here: every scanDateTime in the capture — both the step-level one
# and the one inside scans[] — is an empty string, so this entry produces no
# parseable history/timestamp at all via the current
# trackingStates[].scans[].scanDateTime path, even though a real timestamp
# does exist at the (currently unmapped) top-level status.statusDateTime.
# LOST is a terminal/edge status; whether a normal in-transit or delivered
# capture behaves the same way is still unknown.
LOST_CAPTURE_ENTRY = {
    "awb": "REDACTED_AWB",
    "currentFlow": "Closed",
    "currentTrackIndex": 0,
    "deliveryDate": "",
    "deliveryDate_v1": "Order Lost",
    "deliveryPillLabel": "Lost",
    "essential": True,
    "hqStatus": "LOST",
    "isDirectPTL": False,
    "isInternational": False,
    "isMaster": False,
    "productType": "B2C",
    "referenceNo": "REDACTED_REFERENCE",
    "status": {
        "instructions": "Shipment LOST",
        "status": "LOST",
        "statusDateTime": "2026-07-14T15:52:45.307000",
    },
    "subText": (
        "We're sorry, we're unable to locate your package at the moment. "
        "Please contact our support team for assistance."
    ),
    "timelineModifications": {},
    "trackingStates": [
        {
            "date": "",
            "label": "Lost",
            "outOfStation": True,
            "scanDateTime": "",
            "scans": [
                {
                    "cityLocation": "REDACTED_CITY",
                    "scan": "LOST",
                    "scanDate": "",
                    "scanDateTime": "",
                    "scanNslRemark": "Package marked as lost",
                    "scanType": "LT",
                    "scannedLocation": "Faridabad_MathuraRoad_GW (Haryana)",
                    "setUndeliveredNsl": False,
                    "tsUpdateNsl": False,
                }
            ],
        }
    ],
}


def delivered_sample(code: str = DELIVERED_CODE) -> dict:
    """A synthetic ``data[0]`` entry for a "delivered" parcel.

    ``trackingStates`` is the confirmed step-ladder shape — a list of
    ``{label, stepStatus, scans}`` steps, the real events nested under
    ``scans``, not a top-level ``scans`` key (the earlier reconstruction's
    mistake).
    """
    return {
        "hqStatus": "Delivered",
        "trackingStates": [
            {
                "label": "PICKUP",
                "stepStatus": "finished",
                "scans": [
                    {
                        "scanDateTime": "2026-04-27T23:03:58Z",
                        "scanNslRemark": "Manifest uploaded",
                        "cityLocation": "Gurgaon",
                    }
                ],
            },
            {
                "label": "IN TRANSIT",
                "stepStatus": "finished",
                "scans": [
                    {
                        "scanDateTime": "2026-04-28T15:52:17Z",
                        "scanNslRemark": "In transit",
                        "cityLocation": "Delhi Hub",
                    }
                ],
            },
            {
                "label": "DELIVERED",
                "stepStatus": "current",
                "scans": [
                    {
                        "scanDateTime": "2026-04-29T13:12:42Z",
                        "scanNslRemark": "Delivered",
                        "cityLocation": "Mumbai",
                    }
                ],
            },
        ],
        "PromiseDeliveryDate": "2026-04-29T13:00:00Z",
        "clientName": "Example Merchant Pvt Ltd",
        "consignee": "A. Test Recipient",
    }


def active_sample(code: str = ACTIVE_CODE) -> dict:
    """A synthetic ``data[0]`` entry for a parcel still in transit."""
    return {
        "hqStatus": "In Transit",
        "trackingStates": [
            {
                "label": "PICKUP",
                "stepStatus": "finished",
                "scans": [
                    {
                        "scanDateTime": "2026-04-27T23:03:58Z",
                        "scanNslRemark": "Manifest uploaded",
                        "cityLocation": "Gurgaon",
                    }
                ],
            },
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
        ],
        "PromiseDeliveryDate": "2026-04-29T15:00:00Z",
        "clientName": "Example Merchant Pvt Ltd",
        "consignee": "A. Test Recipient",
    }


def unmapped_status_sample(code: str = ACTIVE_CODE) -> dict:
    """An ``hqStatus`` value nothing in ``_STATUS_MAP`` covers.

    Real values the app itself never leaked (e.g. a status added since the
    3.4.6 build this doc tore down) fall through to the ordinary one-shot
    "unrecognised" warning, distinct from the "mapped but unconfirmed"
    warning every one of the 16 known values gets.
    """
    return {
        "hqStatus": "Out for Delivery",  # not one of the 11 known hqStatus values
        "trackingStates": None,
        "PromiseDeliveryDate": None,
    }


def ladder_fallback_sample(code: str = ACTIVE_CODE) -> dict:
    """No ``hqStatus`` at all — exercises the ``trackingStates`` ladder fallback."""
    return {
        "trackingStates": [
            {"label": "PICKUP", "stepStatus": "finished", "scans": []},
            {"label": "OUT FOR DELIVERY", "stepStatus": "current", "scans": []},
        ],
        "PromiseDeliveryDate": None,
    }
