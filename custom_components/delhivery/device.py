"""The device every entity of this integration belongs to.

One place, because sensors, the button and the calendar must all land on the
*same* device entry — and because the account-based variant only has to change
this file to name devices per account.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN

# Delhivery's own site. A confirmed per-parcel tracking URL template does
# exist now (see const.DELHIVERY_TRACKING_URL / parcels.normalize_parcel's
# ``url`` field) — this hub-level link deliberately still points at the
# general site rather than any one parcel's URL.
CONFIGURATION_URL = "https://www.delhivery.com"

ATTRIBUTION = "Data provided by Delhivery"


def build_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Return the DeviceInfo shared by every entity of this hub."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Delhivery",
        manufacturer="Delhivery",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=CONFIGURATION_URL,
    )
