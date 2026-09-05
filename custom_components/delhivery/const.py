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

# Every optional key the parcel contract defines. CAPABILITIES below must be a
# subset of this — it exists so a typo in CAPABILITIES fails a test instead of
# silently dropping this carrier off a table on the docs site.
KNOWN_CAPABILITIES = frozenset(
    {"weight", "dimensions", "delivery_window", "pickup_point", "url", "history"}
)

# Which optional contract fields this carrier's API actually populates — feeds
# the comparison table on the docs site. Keep in lockstep with
# normalize_parcel() in parcels.py: everything not listed here comes back as a
# literal None there. Delhivery has never had a populated payload observed on
# the wire, so weight/dimensions/pickup_point stay unmapped, but a confirmed
# ``PromiseDeliveryDate``, tracking URL template, and scan-based history exist.
CAPABILITIES = frozenset({"delivery_window", "url", "history"})

# Keyless GET, keyed on the AWB (``wbn``) alone. Live
# control-tested 2026-08-06 and re-probed 2026-08-09: a bogus AWB answers
# HTTP 200 with a clean ``{"data": []}`` envelope; a *populated* ``data[]``
# entry has never been observed. Since 2026-08-09 the field names below are
# first-party fact, but their types and nesting are still inference —
# ``payload: reconstructed`` stays until a
# real AWB is captured.
TRACKING_API_URL = (
    "https://dlv-api.delhivery.com/v3/unified-tracking-new?wbn={tracking_code}"
)

# Fixed same-origin headers the endpoint requires on every request. This is
# NOT a credential — nothing is issued, nothing can be revoked, both values
# are constant strings never derived from anything user-specific — but drop
# them and every request 401s with ``{"message": "ERROR: Invalid Origin"}``,
# which reads like the endpoint moved rather than a missing header. This is
# the main trap in the mechanics doc: hardcode both,
# never make them user-configurable.
DELHIVERY_ORIGIN = "https://www.delhivery.com"
DELHIVERY_REFERER = "https://www.delhivery.com/"

# Consumer tracking-page deep-link, confirmed verbatim (an interpolation
# template) in the research.
# High confidence (unlike almost everything else in this module): unlike a
# payload field, a hardcoded URL template can't have the wrong shape.
# normalize_parcel always populates ``url`` from this, same style as
# TRACKING_API_URL above.
DELHIVERY_TRACKING_URL = "https://www.delhivery.com/track-v2/package/{awb}"

# Diagnostics / first-payload-log redaction. Field **names** are first-party
# now, but no
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
    # first-party Delhivery field names — grounded in the research, still
    # never checked against a real body
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

# Dynamic, status-driven polling — unconditional, no user-facing interval
# option.
#
# Quiet window: no polling between these local hours except the two anchors
# below, for overnight / end-of-day catch-up.
QUIET_WINDOW_START_HOUR = 0
QUIET_WINDOW_END_HOUR = 6

# Cadence while polling is active (minutes). Hot = at least one tracked,
# not-yet-delivered parcel is out_for_delivery within HOT_LOOKAHEAD_HOURS of
# its planned_from (or has no planned_from at all); mid = anything else still
# in flight. This is a barcode-based coordinator (Section 2.1): when every
# tracked parcel is delivered, or nothing is tracked, polling stops entirely
# instead of falling to the mid tier — see coordinator.py's
# ``_hottest_tier_minutes``.
HOT_INTERVAL_MINUTES = 15
MID_INTERVAL_MINUTES = 45
HOT_LOOKAHEAD_HOURS = 1

# Small, stable per-install offset added to every computed interval so
# different installs don't all hit an anchor or tier boundary at the same
# second. Deterministic (hash of the config entry id), not random.
STAGGER_MINUTES = 7

# Per-parcel status history is opt-in and off by default, identical across the
# suite. Keep it off by default even when — as here — the timeline arrives in
# the same response and costs no extra request: it is a large attribute, and on
# carriers that need a second call per parcel the cost is real.
CONF_INCLUDE_HISTORY = "include_history"
DEFAULT_INCLUDE_HISTORY = False

# Cap each parcel's history to the most recent N events so the attribute stays
# well under HA's ~16 KB state-attribute limit.
HISTORY_MAX_EVENTS = 20
