"""Tests for Delhivery diagnostics."""
from unittest.mock import MagicMock

from custom_components.delhivery.diagnostics import (
    async_get_config_entry_diagnostics,
)


async def test_diagnostics_redacts_and_counts(hass):
    """Diagnostics get pasted into public issues — nothing identifying may survive."""
    entry = MagicMock()
    entry.options = {"parcels": [{"tracking_code": "EXAMPLE123456"}]}
    entry.runtime_data.coordinator.data = [
        {
            "barcode": "EXAMPLE123456",
            "sender": "Example Shop",
            "receiver": "Jane Doe",
            "status": "out_for_delivery",
            "raw": {
                "trackingNumber": "EXAMPLE123456",
                "recipient": "Jane Doe",
                "deliveryAddress": {"city": "Rotterdam", "street": "Coolsingel 1"},
            },
        }
    ]
    entry.runtime_data.coordinator.delivered = []

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["counts"] == {"incoming_active": 1, "delivered": 0}
    # tracking codes and payload PII are redacted, at every nesting level
    assert result["entry_options"]["parcels"][0]["tracking_code"] == "**REDACTED**"
    assert result["incoming"][0]["barcode"] == "**REDACTED**"
    assert result["incoming"][0]["receiver"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["recipient"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["deliveryAddress"] == "**REDACTED**"
    # non-identifying fields survive, or the diagnostics would be useless
    assert result["incoming"][0]["status"] == "out_for_delivery"


async def test_diagnostics_redacts_first_party_delhivery_fields(hass):
    """The 2026-08-09 teardown's first-party field names — grounded but
    still pre-capture — must be redacted too, including the nested blocks
    the Dynalogic lesson warns are the usual miss."""
    entry = MagicMock()
    entry.options = {"parcels": [{"tracking_code": "EXAMPLE123456"}]}
    entry.runtime_data.coordinator.data = [
        {
            "barcode": "EXAMPLE123456",
            "sender": "Acme Traders",
            "receiver": "Jane Doe",
            "status": "in_transit",
            "raw": {
                "clientName": "Acme Traders",
                "consignee": "Jane Doe",
                "referenceNo": "REF-001",
                "ucid_consignor": "UC-1",
                "ucid_consignee": "UC-2",
                "addressDetails": {"line1": "Coolsingel 1"},
                "orderDetails": {
                    "origin": "Gurgaon",
                    "destination": {"city": "Rotterdam"},
                },
                "fePhoneObj": {"number": "0600000000"},
                "price_detail": {"total_charge": 100},
                "orderAmount": 100,
                "paymentTerms": "COD",
                "trackingStates": [
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
                    }
                ],
            },
        }
    ]
    entry.runtime_data.coordinator.delivered = []

    result = await async_get_config_entry_diagnostics(hass, entry)
    raw = result["incoming"][0]["raw"]

    assert raw["clientName"] == "**REDACTED**"
    assert raw["consignee"] == "**REDACTED**"
    assert raw["referenceNo"] == "**REDACTED**"
    assert raw["ucid_consignor"] == "**REDACTED**"
    assert raw["ucid_consignee"] == "**REDACTED**"
    assert raw["addressDetails"] == "**REDACTED**"
    assert raw["orderDetails"] == "**REDACTED**"
    assert raw["fePhoneObj"] == "**REDACTED**"
    assert raw["price_detail"] == "**REDACTED**"
    assert raw["orderAmount"] == "**REDACTED**"
    assert raw["paymentTerms"] == "**REDACTED**"
    # cityLocation is nested inside trackingStates[].scans[] — still caught
    assert (
        raw["trackingStates"][0]["scans"][0]["cityLocation"] == "**REDACTED**"
    )
    # non-identifying nested fields survive
    assert raw["trackingStates"][0]["label"] == "IN TRANSIT"
    assert raw["trackingStates"][0]["scans"][0]["scanNslRemark"] == "In transit"
