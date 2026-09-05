"""Diagnostics support for the Delhivery parcel tracker integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import DelhiveryConfigEntry
from .const import DIAGNOSTICS_REDACT_KEYS

# Diagnostics are pasted into public issues, so redact anything that
# identifies a person, an address or a specific parcel. Over-redacting is
# cheap; under-redacting leaks a user's home address into a GitHub thread.
#
# Field names are first-party, but no
# populated data[] entry has ever been captured, so this is still
# grounded-but-unverified, not a walked real payload. Shared with
# parcels.py's first-payload WARNING (one list to keep current); see
# const.DIAGNOSTICS_REDACT_KEYS for the full rationale and the re-check
# obligation.
TO_REDACT = DIAGNOSTICS_REDACT_KEYS


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: DelhiveryConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for the Delhivery config entry."""
    coordinator = entry.runtime_data.coordinator

    return {
        "entry_options": async_redact_data(dict(entry.options), TO_REDACT),
        "polling": {
            "current_tier_minutes": coordinator.current_tier_minutes,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
        },
        "counts": {
            "incoming_active": len(coordinator.data or []),
            "delivered": len(coordinator.delivered or []),
        },
        "incoming": async_redact_data(coordinator.data or [], TO_REDACT),
        "delivered": async_redact_data(coordinator.delivered or [], TO_REDACT),
    }
