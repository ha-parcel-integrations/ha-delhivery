"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options. That keeps the carrier-specific
mapping apart from the coordinator, and makes it unit-testable without
spinning up HA.

**Read this before changing the mapping.** As of 2026-08-09 the field names
below are **first-party fact**, read straight out of Delhivery's own
consignee Android app (a teardown of its React Native/Hermes bundle — see
``carrier-research/api/delhivery/tracking.md``), not a third-party client's
guesswork. What is still **not** confirmed is a *populated* ``data[]``
entry: no real AWB has ever been run through the endpoint, so field types,
nesting and optionality remain inference, and neither status vocabulary
(``hqStatus`` nor the ``trackingStates[].label`` ladder) has ever been
observed on the wire.

So the rule here stays stricter than the suite's usual pre-1.0 posture, just
narrowed to what is actually still unknown: guard every access, never assume
a key set inside a field whose *shape* (as opposed to *name*) is unconfirmed,
and make every guess announce itself — either once via :func:`_warn_once`
(an unmapped status, an unexpected key, an unparseable date, a malformed
shape) or on **every** occurrence via :func:`_warn_uncertain_status` (a
*mapped* status — the map itself is still first-party-names-only, not
wire-confirmed) — so the first real capture corrects it instead of passing
silently.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    DELHIVERY_TRACKING_URL,
    DIAGNOSTICS_REDACT_KEYS,
    HISTORY_MAX_EVENTS,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

# Where users report a status we do not map yet, or paste the shape/first-
# payload WARNINGs below. Rewritten by the bootstrap script; it must point at
# the carrier's own repo so the log line is copy-pasteable straight into a
# new issue.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-delhivery/issues/new"
    "?template=unrecognised_status.yml"
)

# Everything already reported, so each surprise is logged once per HA session
# instead of on every poll. Keyed by a short tag so the different kinds of
# surprise (a status, a shape, the first payload) cannot mask each other.
_warned: set[str] = set()


def _warn_once(key: str, message: str, *args: Any) -> None:
    """Log ``message`` once per session, with a copy-paste issue link.

    Every unverified assumption in this module funnels through here — the
    entire mechanism by which this integration ever learns its own payload.
    """
    if key in _warned:
        return
    _warned.add(key)
    _LOGGER.warning(
        message + "\n  Please report it — open an issue with this line: %s",
        *args,
        NEW_ISSUE_URL,
    )


# The first-party field inventory for a ``data[]`` entry
# (tracking.md#the-order-object-an-entry-in-data). These are now known field
# *names* — the app's own code reads every one of them — so they no longer
# trip the "unexpected key" WARNING even though most stay unused/``None`` in
# :func:`normalize_parcel` pending stronger evidence about what they contain.
# Note ``scans`` is deliberately absent: it is nested inside each
# ``trackingStates[]`` step, never a top-level key.
_KNOWN_TOP_LEVEL_KEYS = {
    "awb",
    "status",
    "hqStatus",
    "trackingStates",
    "deliveryDate",
    "deliveryDateText",
    "deliveryDateText_v1",
    "deliveryLabel",
    "PromiseDeliveryDate",
    "referenceNo",
    "clientName",
    "consignee",
    "productName",
    "packageType",
    "package_type",
    "category",
    "subcategory",
    "description",
    "package_weight",
    "paymentTerms",
    "price_detail",
    "orderAmount",
    "orderDetails",
    "order_type",
    "isInternational",
    "remarks",
    "selfHelp",
    "addressDetails",
    "reschedulePickupObj",
    "fePhoneObj",
    "dispatchId",
    "ucid_consignor",
    "ucid_consignee",
}

# How deep the structure report walks. Comfortably past anything the known
# field inventory suggests (``orderDetails`` nests one level); the cap exists
# so a pathological payload cannot turn a log line into a hang.
_MAX_STRUCTURE_DEPTH = 4


def _describe_lines(value: Any, path: str, depth: int) -> list[str]:
    """Return ``value``'s shape as ``path: type`` lines — values omitted.

    Mirrors ha-dynalogic's shape-logging pattern (this plan's reference
    model for "payload never seen"): a response we have never observed is
    worth more as a map than as a complaint, and types-only is always safe to
    paste into a public issue. A list is described by its first element —
    the shape is the point, not the count.
    """
    if depth >= _MAX_STRUCTURE_DEPTH:
        return [f"{path}: … (nested deeper than {_MAX_STRUCTURE_DEPTH})"]
    if isinstance(value, dict):
        if not value:
            return [f"{path}: empty object"]
        lines: list[str] = []
        for key in sorted(value, key=str):
            child = f"{path}.{key}" if path else str(key)
            lines += _describe_lines(value[key], child, depth + 1)
        return lines
    if isinstance(value, list):
        if not value:
            return [f"{path}[]: empty list"]
        return _describe_lines(value[0], f"{path}[]", depth + 1)
    return [f"{path}: {type(value).__name__}"]


def describe_structure(value: Any, path: str) -> str:
    """Return ``value``'s shape as one ``; ``-joined ``path: type`` line."""
    return "; ".join(_describe_lines(value, path, 0))


def _warn_unexpected_keys(entry: dict) -> None:
    """Report, once per key, a ``data[]`` field this module does not map.

    ha-dynalogic's shape-logging pattern recovered ten keys a reconstruction
    never saw this way. The full first-party field inventory
    (``_KNOWN_TOP_LEVEL_KEYS``) makes this rarer for Delhivery than it used
    to be, but it is still the primary route by which the integration learns
    a field the app teardown missed entirely.
    """
    for key in sorted(set(entry) - _KNOWN_TOP_LEVEL_KEYS):
        _warn_once(
            f"unexpected_key:{key}",
            "Delhivery's data[] entry carries a field we do not map yet "
            "(type only — safe to paste): %s",
            describe_structure(entry[key], key),
        )


def _warn_first_payload(entry: dict) -> None:
    """Log the first populated ``data[]`` entry ever seen, once, redacted.

    Not overlogging — this is the only way a future maintainer learns what a
    real Delhivery response looks like at all, since no populated entry has
    ever been captured. Redacted per :data:`~.const.DIAGNOSTICS_REDACT_KEYS`,
    itself grounded in first-party field names but still unverified against a
    real body.
    """
    _warn_once(
        "first_payload",
        "First real Delhivery data[] entry ever captured (redacted per "
        "const.DIAGNOSTICS_REDACT_KEYS) — this is the single most useful "
        "thing you can send us: %s",
        async_redact_data(entry, DIAGNOSTICS_REDACT_KEYS),
    )


def _warn_scan_shape(value: Any) -> None:
    """Report, once, that a step's ``scans`` entry has a shape not parsed.

    ``trackingStates[].scans[]`` is the real event history
    (tracking.md#scans--the-actual-event-history) — a shape mismatch here
    means that step's events are skipped, not that the whole parcel fails.
    """
    _warn_once(
        "scans_shape",
        "Delhivery's trackingStates[].scans field has a shape we have "
        "never parsed (type only — safe to paste): %s. That step's events "
        "are skipped until a real capture confirms the field names.",
        describe_structure(value, "scans"),
    )


def _warn_trackingstates_shape(value: Any) -> None:
    """Report, once, that ``trackingStates`` (or one of its steps) has an unparsed shape.

    Shared between status resolution (:func:`_extract_ladder_label`) and
    :func:`build_history` — both read the same field, so one WARNING for a
    malformed payload is enough.
    """
    _warn_once(
        "tracking_states_shape",
        "Delhivery's trackingStates field has a shape we have never "
        "parsed (type only — safe to paste): %s. Status falls back to the "
        "hqStatus-less default and history stays incomplete until a real "
        "capture teaches us the field names.",
        describe_structure(value, "trackingStates"),
    )


def _warn_hqstatus_shape(value: Any) -> None:
    """Report, once, that ``hqStatus`` has a shape this module does not parse.

    Only a non-empty string is handled — anything else falls back to the
    ``trackingStates`` ladder.
    """
    _warn_once(
        "hqStatus_shape",
        "Delhivery's hqStatus field has a shape we have never parsed (type "
        "only — safe to paste): %s. Falling back to the trackingStates "
        "ladder.",
        describe_structure(value, "hqStatus"),
    )


def _warn_unparseable_promise_date(value: Any) -> None:
    """Report, once per distinct value, an unparseable ``PromiseDeliveryDate``."""
    _warn_once(
        f"promise_date:{value!r}",
        "Delhivery's PromiseDeliveryDate did not parse as a date/time: %r "
        "— whether this field is even the ETA, and whether it is a point "
        "estimate or a window, has never been confirmed either.",
        value,
    )


# Both first-party vocabularies (tracking.md#status-vocabulary--first-party-
# not-yet-observed), lifted verbatim from the consignee app's ``Constants``
# literal. ``hqStatus`` is the finer-grained field and takes priority; the
# ``trackingStates[].label`` ladder (upper case, closed 5-value set) is the
# fallback used only when ``hqStatus`` is absent — see
# :func:`_resolve_raw_status`. Casing is preserved exactly as the app itself
# compares it (``LOST``/``RTO``/``DTO`` are upper case among title-case
# siblings — do not normalise case before lookup, key on the literal).
_STATUS_MAP: dict[str, ParcelStatus] = {
    # hqStatus — title case except LOST/RTO/DTO
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
    # trackingStates[].label ladder — upper case, closed set
    "PICKUP": ParcelStatus.IN_TRANSIT,
    "IN TRANSIT": ParcelStatus.IN_TRANSIT,
    "OUT FOR DELIVERY": ParcelStatus.OUT_FOR_DELIVERY,
    "DELIVERED": ParcelStatus.DELIVERED,
    "CANCELLED": ParcelStatus.PROBLEM,
}

# Every entry above is first-party in *name* only — neither vocabulary has
# ever been observed on the wire (tracking.md's own framing: "not yet
# observed"), so unlike ha-nova-post's single contested StatusCode, the
# *whole* map is uncertain here. Mirrors ha-nova-post's
# ``_warn_uncertain_status`` pattern, generalised to every key: a mapped hit
# still self-reports on every occurrence (not just once) via
# :func:`_warn_uncertain_status`, distinct from the one-shot "totally
# unmapped value" warning below, which stays for anything outside this set.
# Remove entries here one at a time as real captures confirm them — do not
# clear it wholesale.
_UNCERTAIN_STATUSES = frozenset(_STATUS_MAP)


def _warn_uncertain_status(raw_status: str) -> None:
    """Warn on *every* occurrence of a mapped-but-unconfirmed status.

    Unlike :func:`_warn_once`'s per-session dedup, every occurrence
    self-reports — the mapping is first-party field *names*, not a
    wire-confirmed vocabulary, so a mapped hit is still a guess until a real
    capture proves it right.
    """
    _LOGGER.warning(
        "Delhivery reported status %r, mapped to '%s' — but neither "
        "vocabulary has ever been observed on the wire (only the field "
        "names are first-party), so this mapping is still unconfirmed. "
        "Please help us verify it by opening an issue and pasting this "
        "line, along with what the parcel was actually doing: %s",
        raw_status,
        _STATUS_MAP[raw_status].value,
        NEW_ISSUE_URL,
    )


def _extract_ladder_label(tracking_states: Any) -> str | None:
    """Return the current step's ``label`` from the ``trackingStates`` ladder.

    ``trackingStates`` lists every step of the whole journey regardless of
    progress (tracking.md#trackingstates--the-step-list) — the *last* entry
    is not necessarily the current one. The confirmed ``stepStatus`` field
    (``'unfinished' | 'finished' | 'current'``) is what actually marks
    position: prefer the step marked ``'current'``; fall back to the most
    advanced step marked ``'finished'`` when none is current. Only a list of
    dicts, each carrying a string ``label``, is handled — anything else
    reports the shape WARNING instead of guessing a key.
    """
    if not tracking_states:
        return None
    if not isinstance(tracking_states, list):
        _warn_trackingstates_shape(tracking_states)
        return None

    current_label: str | None = None
    last_finished_label: str | None = None
    saw_malformed_step = False
    for step in tracking_states:
        if not isinstance(step, dict):
            saw_malformed_step = True
            continue
        label = step.get("label")
        if not isinstance(label, str) or not label:
            continue
        step_status = step.get("stepStatus")
        if step_status == "current":
            current_label = label
        elif step_status == "finished":
            last_finished_label = label
    if saw_malformed_step:
        _warn_trackingstates_shape(tracking_states)
    return current_label or last_finished_label


def _resolve_raw_status(raw: dict) -> str | None:
    """Return the raw status string to map onto :class:`ParcelStatus`.

    ``hqStatus`` is the finer-grained, higher-priority field
    (tracking.md "Which to map from"); the ``trackingStates[].label`` ladder
    is only consulted when ``hqStatus`` is absent, empty or not a string.
    """
    hq_status = raw.get("hqStatus")
    if isinstance(hq_status, str) and hq_status:
        return hq_status
    if hq_status not in (None, ""):
        _warn_hqstatus_shape(hq_status)
    return _extract_ladder_label(raw.get("trackingStates"))


def map_parcel_status(raw_status: str | None) -> ParcelStatus:
    """Map a raw Delhivery status value to a canonical :class:`ParcelStatus`.

    ``None`` (nothing extracted, or a not-yet-scanned parcel) reports
    ``unknown`` silently. A recognised value is mapped, but — because neither
    vocabulary has ever been observed on the wire — *every* occurrence also
    self-reports via :func:`_warn_uncertain_status`. Anything outside the map
    reports ``unknown`` with the ordinary one-shot warning.
    """
    if not raw_status:
        return ParcelStatus.UNKNOWN
    mapped = _STATUS_MAP.get(raw_status)
    if mapped is not None:
        if raw_status in _UNCERTAIN_STATUSES:
            _warn_uncertain_status(raw_status)
        return mapped
    _warn_once(
        f"status:{raw_status}",
        "Unrecognised Delhivery status %r — reported as 'unknown'. Both "
        "known vocabularies (hqStatus and the trackingStates ladder) are "
        "first-party field names; this value is outside both, so this "
        "report is exactly what grows the map.",
        raw_status,
    )
    return ParcelStatus.UNKNOWN


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    Naive values are treated as UTC so a list always sorts without crashing
    on a mixed set.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def to_iso_timestamp(value: Any) -> str | None:
    """Return an ISO 8601 string for an API timestamp field.

    Numbers are treated as **epoch milliseconds** — unconfirmed for
    Delhivery specifically, but the common case across this suite's
    consumer APIs. Strings pass through untouched; their consumers are
    guarded by :func:`parse_iso`.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return str(value)


def _collect_scan_entries(
    tracking_states: Any,
) -> tuple[list[tuple[datetime, dict]], list[dict]]:
    """Flatten ``trackingStates[].scans[]`` into ``{timestamp, status, raw_status}`` entries.

    Guarded at every level. Shared by :func:`build_history` (which orders/caps the result for
    display) and :func:`normalize_parcel`'s ``delivered_at`` derivation
    (which only needs the single most recent parseable entry) so the guarded
    walk and its shape WARNINGs live in exactly one place. Returns
    ``(parseable sorted oldest -> newest, unparseable)`` — see
    :func:`build_history` for what each field means.
    """
    if not tracking_states:
        return [], []
    if not isinstance(tracking_states, list):
        _warn_trackingstates_shape(tracking_states)
        return [], []

    parseable: list[tuple[datetime, dict]] = []
    unparseable: list[dict] = []
    saw_malformed_step = False
    for step in tracking_states:
        if not isinstance(step, dict):
            saw_malformed_step = True
            continue
        label = step.get("label")
        raw_status = label if isinstance(label, str) and label else None
        scans = step.get("scans")
        if scans in (None, []):
            continue
        if not isinstance(scans, list):
            _warn_scan_shape(scans)
            continue
        for scan in scans:
            if not isinstance(scan, dict):
                _warn_scan_shape(scan)
                continue
            timestamp = scan.get("scanDateTime")
            if not timestamp:
                continue
            entry = {
                "timestamp": to_iso_timestamp(timestamp) or str(timestamp),
                "status": map_parcel_status(raw_status),
                "raw_status": raw_status,
            }
            parsed = parse_iso(entry["timestamp"])
            if parsed is None:
                unparseable.append(entry)
            else:
                parseable.append((parsed, entry))
    if saw_malformed_step:
        _warn_trackingstates_shape(tracking_states)

    parseable.sort(key=lambda item: item[0])
    return parseable, unparseable


def build_history(
    tracking_states: Any, *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    """Build the canonical ``history`` from ``trackingStates[].scans[]``.

    ``trackingStates`` is a coarse step ladder; the real event history hangs
    underneath each step in ``scans[]`` as ``{scanDateTime, scanNslRemark,
    cityLocation}`` (tracking.md#scans--the-actual-event-history) — **not**
    at a top-level ``scans`` key, which was the earlier reconstruction's
    mistake. Each scan is mapped to ``{timestamp, status, raw_status}`` using
    its *containing step's* ``label`` — a confirmed field name — rather than
    inventing a per-scan status the app itself never exposes. Guarded at
    every level: a step, or a step's ``scans``, that does not match the
    expected shape is skipped (with a shape WARNING) rather than crashing the
    whole history. Sorted oldest → newest and capped to the most recent
    ``max_events``, the same contract as every other carrier's
    ``build_history``.
    """
    parseable, unparseable = _collect_scan_entries(tracking_states)
    ordered = [entry for _, entry in parseable] + unparseable
    return ordered[-max_events:]


def _last_scan_timestamp(tracking_states: Any) -> str | None:
    """Return the most recent parseable ``scans[]`` timestamp, or ``None``.

    Used for ``delivered_at``, independent of the ``include_history``
    opt-in — a delivered parcel's completion time is worth resolving even
    when the fuller per-event history stays switched off. An unparseable-only
    result yields ``None`` rather than an unreliable value; it is never
    guessed from an unparseable entry.
    """
    parseable, _unparseable = _collect_scan_entries(tracking_states)
    if not parseable:
        return None
    return parseable[-1][1]["timestamp"]


def normalize_parcel(raw: dict, *, awb: str, include_history: bool = False) -> dict:
    """Return a carrier-agnostic parcel dict with the raw payload under ``raw``.

    ``raw`` is the ``data[]`` entry (or ``{}`` for a tracked-but-not-yet-
    scanned/unknown AWB — the pending placeholder); every access below is
    guarded and every guess is reported via the WARNINGs above, because
    nothing about a *populated* Delhivery ``data[]`` entry has ever actually
    been seen on the wire — only its field *names* are first-party fact.

    * ``barcode`` is the AWB the caller requested (the value sent as
      ``wbn``), **never** read from the payload — it is the only value
      guaranteed present even on a not-yet-scanned parcel, and the
      highest-confidence field in the whole mapping.
    * ``sender``/``receiver`` come directly from ``clientName``/``consignee``
      — first-party field names, ``.get()``-guarded only (types/optionality
      unconfirmed), not otherwise validated.
    * ``status``/``raw_status`` come from ``hqStatus``, falling back to the
      ``trackingStates[].label`` ladder — see :func:`_resolve_raw_status`.
      Both vocabularies are mapped (see ``_STATUS_MAP``), but every mapped
      hit still self-reports as unconfirmed via
      :func:`_warn_uncertain_status`.
    * ``planned_from`` comes from ``PromiseDeliveryDate`` (capital P — a
      separate, lower-confidence ``deliveryDate`` key exists too but is not
      used here pending stronger evidence); whether it is a point estimate
      or a window has never been confirmed, so ``planned_to`` always stays
      ``None``.
    * ``url`` is always the confirmed tracking-page template
      (``const.DELHIVERY_TRACKING_URL``) formatted with the AWB — the one
      high-confidence field in this whole mapping, a literal found verbatim
      in the app bundle.
    * ``history`` comes from ``trackingStates[].scans[]`` — see
      :func:`build_history`.
    * ``delivered_at`` is the most recent parseable ``scans[]`` timestamp
      (see :func:`_last_scan_timestamp`) once ``delivered`` is ``True``,
      resolved regardless of ``include_history`` — otherwise ``None``.
    * every other canonical field (``pickup``, ``pickup_point``, ``weight``,
      ``dimensions``) is ``None`` — not seen in any field name so far, or
      (``weight``) too low-confidence to use (bucketed to an icon, may not
      be a number).
    """
    if raw:
        _warn_unexpected_keys(raw)
        _warn_first_payload(raw)

    raw_status = _resolve_raw_status(raw)
    status = map_parcel_status(raw_status)
    delivered = status is ParcelStatus.DELIVERED
    delivered_at = _last_scan_timestamp(raw.get("trackingStates")) if delivered else None

    planned_from: str | None = None
    promise_date = raw.get("PromiseDeliveryDate")
    if promise_date not in (None, ""):
        candidate = to_iso_timestamp(promise_date)
        if candidate is not None and parse_iso(candidate) is not None:
            planned_from = candidate
        else:
            _warn_unparseable_promise_date(promise_date)

    return {
        "carrier": "Delhivery",
        "barcode": awb,
        "sender": raw.get("clientName"),
        "receiver": raw.get("consignee"),
        "status": status,
        "raw_status": raw_status,
        "delivered": delivered,
        "delivered_at": delivered_at,
        "planned_from": None if delivered else planned_from,
        "planned_to": None,
        "pickup": False,
        "pickup_point": None,
        "url": DELHIVERY_TRACKING_URL.format(awb=awb),
        "weight": None,
        "dimensions": None,
        "history": build_history(raw.get("trackingStates")) if include_history else None,
        "raw": raw,
    }


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    The suite's sort contract: incoming/outgoing ascending on ``planned_from``,
    delivered descending on ``delivered_at``. Parcels whose value is missing or
    unparseable always sort to the end, regardless of ``descending``.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        parsed = parse_iso(parcel.get(key_field))
        if parsed is None:
            without_ts.append(parcel)
        else:
            with_ts.append((parsed, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [parcel for _, parcel in with_ts] + without_ts


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Trim the delivered list per the entry's retention option.

    ``parcels`` must already be sorted newest-first. ``days`` keeps deliveries
    from the last N days (an unparseable ``delivered_at`` is kept rather than
    silently dropped); the ``parcels`` type keeps the N most recent. Parcels
    stay *tracked* either way — this only controls what the delivered sensor
    shows.
    """
    options = entry.options
    filter_type = options.get(
        CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
    )
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(timezone.utc) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
