"""Sample Delhivery API payloads shared by the test modules.

**Synthetic, not captured.** No real Delhivery AWB has ever been run through
the endpoint (`carrier-research/api/delhivery/tracking.md`) — the only real
request/response pair on file is the not-found envelope on a bogus AWB
(`NOT_FOUND_ENVELOPE` below, which *is* a verbatim capture). Everything else
in this module uses the first-party field *names* the 2026-08-09 consignee-app
teardown confirmed (`hqStatus`, `trackingStates[].{label,stepStatus,scans}`,
`scans[].{scanDateTime,scanNslRemark,cityLocation}`, `PromiseDeliveryDate`,
`clientName`, `consignee`), but the values, nesting and optionality below are
still invented for test coverage only — it proves the code does not crash and
degrades gracefully on *a* plausible shape, not that this is *the* real shape.
Replace with a real (redacted) response the moment one is captured (see
TODO.md).
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
