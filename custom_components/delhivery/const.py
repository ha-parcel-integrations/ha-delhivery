"""Constants for the Delhivery parcel tracker integration."""
from enum import StrEnum

from homeassistant.const import Platform

DOMAIN = "delhivery"


class ParcelStatus(StrEnum):
    """Carrier-agnostic parcel status.

    **Do not extend or rename these members.** Every integration in the parcel
    suite publishes exactly this vocabulary on the ``status`` field of each
    normalised parcel, so cross-carrier automations and the aggregator can
    target ``status: out_for_delivery`` regardless of carrier. Listed in
    roughly the order a parcel moves through.
    """

    REGISTERED = "registered"               # Sender announced the parcel; not handed over yet
    IN_TRANSIT = "in_transit"               # In the carrier's network
    OUT_FOR_DELIVERY = "out_for_delivery"   # On a delivery vehicle today
    AT_PICKUP_POINT = "at_pickup_point"     # Ready to collect at a pickup location
    DELIVERED = "delivered"                 # Handed over
    RETURNING = "returning"                 # Failed delivery, going back to sender
    PROBLEM = "problem"                     # Carrier reports an exception/issue
    UNKNOWN = "unknown"                     # Raw status we have not mapped yet


PLATFORMS = [Platform.BUTTON, Platform.CALENDAR, Platform.SENSOR]

# Keyless GET, keyed on the AWB (``wbn``) alone — see
# carrier-research/api/delhivery/tracking.md#endpoint--auth. Live
# control-tested 2026-08-06 and re-probed 2026-08-09: a bogus AWB answers
# HTTP 200 with a clean ``{"data": []}`` envelope; a *populated* ``data[]``
# entry has never been observed. Since 2026-08-09 the field names below are
# first-party (read out of Delhivery's own consignee app), but their types
# and nesting are still inference — ``payload: reconstructed`` stays until a
# real AWB is captured.
TRACKING_API_URL = (
    "https://dlv-api.delhivery.com/v3/unified-tracking-new?wbn={tracking_code}"
)

# Fixed same-origin headers the endpoint requires on every request. This is
# NOT a credential — nothing is issued, nothing can be revoked, both values
# are constant strings never derived from anything user-specific — but drop
# them and every request 401s with ``{"message": "ERROR: Invalid Origin"}``,
# which reads like the endpoint moved rather than a missing header. This is
# the main trap in the mechanics doc (tracking.md): hardcode both,
# never make them user-configurable.
DELHIVERY_ORIGIN = "https://www.delhivery.com"
DELHIVERY_REFERER = "https://www.delhivery.com/"

# Consumer tracking-page deep-link, confirmed verbatim (an interpolation
# template) in the consignee app's Hermes bundle — see
# tracking.md#payload--first-party-field-names-still-not-seen-on-the-wire.
# High confidence (unlike almost everything else in this module): unlike a
# payload field, a hardcoded URL template can't have the wrong shape.
# normalize_parcel always populates ``url`` from this, same style as
# TRACKING_API_URL above.
DELHIVERY_TRACKING_URL = "https://www.delhivery.com/track-v2/package/{awb}"

# Diagnostics / first-payload-log redaction. Field **names** are first-party
# now (read out of the consignee app's own Constants/screens — see
# tracking.md#diagnostics-redaction--revised-still-pre-capture), but no
# populated ``data[]`` entry has ever been captured, so this is still
# grounded-but-unverified: types, nesting and whether a name really shows up
# under this exact key remain inference. MUST be re-checked leaf by leaf the
# moment a real ``data[]`` entry is captured — nested blocks
# (``orderDetails.*``, ``fePhoneObj``, ``scans[].cityLocation``) are the usual
# miss (the Dynalogic lesson), which is why they're listed individually below
# even though ``async_redact_data`` matches a key at any depth, not just
# top-level. Shared by diagnostics.py's ``TO_REDACT`` and parcels.py's
# first-payload WARNING so there is exactly one list to keep current. Older
# placeholder guesses that never got a first-party name are kept rather than
# removed — over-redaction is cheap.
DIAGNOSTICS_REDACT_KEYS = {
    # canonical fields we publish ourselves
    "barcode",
    "sender",
    "receiver",
    "url",
    # our own stored config — note "parcels" (the list) is deliberately NOT
    # included: it would redact the whole tracked-codes list at once, hiding
    # everything but the per-item "tracking_code" leaf is the actual PII.
    "tracking_code",
    # first-party Delhivery field names (tracking.md "The order object" /
    # "Diagnostics redaction") — grounded in the app's own code, still never
    # checked against a real body
    "consignee",
    "clientName",
    "addressDetails",
    "orderDetails",  # whole block: nested origin/destination are addresses
    "fePhoneObj",  # whole block: delivery-executive phone/contact details
    "fePhoneMasked",
    "fePhoneNumber",
    "secondaryPhoneNumber",
    "ucid_consignor",
    "ucid_consignee",
    "referenceNo",
    "cityLocation",  # nested inside each trackingStates[].scans[] entry
    "price_detail",
    "orderAmount",
    "paymentTerms",
    # placeholder Delhivery payload field names — guessed, never confirmed by
    # anything first-party; kept rather than removed
    "consigneeName",
    "receiverName",
    "recipient",
    "name",
    "address",
    "deliveryAddress",
    "shippingAddress",
    "pincode",
    "postalCode",
    "postal_code",
    "phone",
    "phoneNumber",
    "mobile",
    "wbn",
    "awb",
    "trackingNumber",
    "waybillNumber",
}

# Tracked parcels live in the config entry options as a list of
# ``{tracking_code}`` dicts — this carrier has no account or parcel feed, so the
# user enters the codes themselves. Kept as dicts so future per-parcel fields
# slot in without an options migration.
CONF_PARCELS = "parcels"
CONF_TRACKING_CODE = "tracking_code"

# Delivered-parcels retention: keep delivered parcels visible for the last N
# days, or keep only the N most recent — identical across the suite.
CONF_DELIVERED_FILTER_TYPE = "delivered_filter_type"
CONF_DELIVERED_FILTER_AMOUNT = "delivered_filter_amount"
DEFAULT_DELIVERED_FILTER_TYPE = "days"
DEFAULT_DELIVERED_FILTER_AMOUNT = 7

# Refresh interval (minutes) controls how often the coordinator polls the
# carrier. Default 30 min keeps the load on a consumer endpoint gentle; the
# minimum is 15 min for the same reason.
#
# Deliberate divergence from the HA Core rule that polling intervals are not
# user-configurable: that rule targets core integrations, and in a HACS parcel
# tracker a tunable cadence is a wanted feature. Generate with
# ``--interval fixed`` instead when the carrier throttles or soft-bans unusual
# traffic — that drops the option entirely and hard-codes the cadence, so users
# cannot dial it down to something that gets them blocked.
CONF_REFRESH_INTERVAL = "refresh_interval"
REFRESH_INTERVAL_OPTIONS = (15, 30, 60, 120, 240)
DEFAULT_REFRESH_INTERVAL = 30

# Per-parcel status history is opt-in and off by default, identical across the
# suite. Keep it off by default even when — as here — the timeline arrives in
# the same response and costs no extra request: it is a large attribute, and on
# carriers that need a second call per parcel the cost is real.
CONF_INCLUDE_HISTORY = "include_history"
DEFAULT_INCLUDE_HISTORY = False

# Cap each parcel's history to the most recent N events so the attribute stays
# well under HA's ~16 KB state-attribute limit.
HISTORY_MAX_EVENTS = 20
