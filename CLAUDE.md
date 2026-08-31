# Working in this repository

Home Assistant custom integration for **Delhivery** parcel tracking.
Distributed via HACS; not part of HA core. One carrier in the
[ha-parcel-integrations](https://github.com/ha-parcel-integrations) suite,
**generated from ha-carrier-template** — everything outside *Carrier-specific
notes* is suite-wide; when in doubt check the template or a sibling repo.
No DTO layer.

## Shared conventions — fetch when relevant

Suite-wide rules live in
[`.github/CONVENTIONS.md`](https://github.com/ha-parcel-integrations/.github/blob/main/CONVENTIONS.md)
and are **not** repeated here. Don't fetch it every session — fetch it **before**
you act in one of these areas:

| Before you … | Fetch `CONVENTIONS.md` § |
|---|---|
| touch entities, sensors, config/options flow, coordinator, diagnostics, translations | *Home Assistant developer docs* (its table points on to the canonical HA page — don't rely on memory) |
| add/rename a parcel field, a `ParcelStatus`, or a bus event; change the sort/first-refresh; touch unmapped-status logging | *Parcel contract* — exact key set, units, sort, events + suppression; `test_parcels.py::test_normalize_publishes_exactly_the_canonical_keys` guards the key set |
| ship anything while below 1.0.0 (unconfirmed data) | *Pre-1.0 releases* — one-shot WARNINGs for every guessed shape/code |
| consider "fixing" a lint/pattern the skill flags (poll interval, inline client, sync requests) | *Deliberate skill divergences* — likely intentional, don't re-flag |
| commit, bump, tag, release, or write release notes; add a feature without a test | *Workflow / Commits / Versioning / Testing* |

**Suite-wide tripwires, kept inline on purpose:**
- **First refresh in `__init__.py`, before `async_forward_entry_setups`** — from
  a forwarded platform HA can't catch `ConfigEntryNotReady` and half-sets-up the
  entry. Runtime-only; tests don't catch a regression.
- **Setup stale-entity sweep is scoped to `domain == "sensor"` and skips
  `non_parcel_unique_ids`** — else it deletes the refresh button / the
  summary+diagnostic sensors. Add a new non-parcel sensor's unique_id to the set.
- **Per-parcel sensors are removed by the summary sensor** via
  `entity_registry.async_remove` (self-removal races and leaves ghosts).

## Carrier-specific notes

**No `awaiting_pickup` sensor yet — unconfirmed, not structural.** Nothing in
either documented status ladder corresponds to `at_pickup_point`
(carrier-research's `delhivery.md`), so `pickup`/`pickup_point` stay
`False`/`None` in `parcels.py`. That is the best evidence on file, not a
proof the status can never occur — revisit if carrier-research settles it
either way. See `.github/CONVENTIONS.md`'s pickup-point convention.

**API mechanics live in `carrier-research/delhivery/api/` (private research
repo)** — the endpoint, the fixed Origin/Referer headers, the not-found
envelope, the first-party payload field names, and the payload→canonical
mapping. Do not duplicate them here.

### ⚠️ Read this before touching `parcels.py` — names are first-party, values are not

A 2026-08-09 correction (see `carrier-research/delhivery/api/`, private)
moved the payload's **field names** and **status vocabularies** from
third-party guesswork to first-party fact. What is **still** true: no real
AWB has ever been run through the
endpoint, so no *populated* `data[]` entry has ever actually been seen —
types, nesting and optionality of every field remain inference, and neither
status vocabulary (`hqStatus` nor the `trackingStates[].label` ladder) has
ever been observed on the wire. That keeps this integration's pre-1.0
posture narrower than a fully-reconstructed carrier but still stricter than
the suite norm on anything about *shape*:

- **`_STATUS_MAP` in `parcels.py` ships filled with all 16 first-party
  values** (11 `hqStatus` + 5 `trackingStates[].label` ladder). 15 of them
  still self-report via `_warn_uncertain_status` — **on every occurrence,
  not just once** — because neither vocabulary had been wire-confirmed. This
  mirrors `ha-nova-post`'s `_warn_uncertain_status` pattern (its StatusCode
  `2`), generalised to the whole map instead of one entry. Distinct from the
  ordinary one-shot "totally unmapped value" warning, which stays for
  anything outside these 16. **Do not case-fold before lookup** —
  `LOST`/`RTO`/`DTO` are upper case among title-case `hqStatus` siblings; the
  map is keyed on the literal, unnormalised string. Move a value out of
  `_UNCERTAIN_STATUSES` (not out of `_STATUS_MAP`) once a real capture
  confirms it — one entry at a time, never wholesale. **`hqStatus` `"LOST"`
  is the first one confirmed this way**, by the 2026-08-24 real capture (see
  below) — it is out of `_UNCERTAIN_STATUSES`, silent on every occurrence
  now. The `trackingStates[].label` value seen alongside it in that same
  capture (`"Lost"`, title case) is a *different* key from the ladder's own
  `"CANCELLED"` et al. and is still unconfirmed/unmapped.
- **Status resolution is `hqStatus` first, `trackingStates[].label` ladder
  as fallback** (`_resolve_raw_status`) — `hqStatus` is the finer-grained
  field. The ladder fallback (`_extract_ladder_label`) prefers the step
  marked `stepStatus: 'current'`; the *last* entry in the array is **not**
  necessarily current, since `trackingStates` lists every step of the whole
  journey regardless of progress.
- **`build_history` reads `trackingStates[].scans[]`, never a top-level
  `scans` key.** The earlier reconstruction had this backwards —
  `trackingStates` is a coarse step ladder, and the real event history hangs
  underneath each step in `scans[]` as `{scanDateTime, scanNslRemark,
  cityLocation}`. Each history entry's `raw_status`/`status` comes from its
  *containing step's* `label`, mapped through the same `_STATUS_MAP` — a
  confirmed field name, not an invented per-scan status.
- **`PromiseDeliveryDate` (capital P) maps to `planned_from` only** — never
  `planned_to`. A separate, lower-confidence `deliveryDate` key exists too
  but is not used pending stronger evidence; a single field name is no
  evidence of a window either way.
- **`sender`/`receiver`/`url` are populated now** — `sender` ← `clientName`,
  `receiver` ← `consignee` (first-party, `.get()`-guarded reads only, no
  further validation since types/optionality are unconfirmed), `url` ←
  `const.DELHIVERY_TRACKING_URL.format(awb=...)`, a literal template
  confirmed verbatim — always populated, the one high-confidence field in
  this whole mapping.
- **`delivered_at` comes from the last parseable `scans[]` timestamp**
  (`_last_scan_timestamp`), resolved once `status` maps to `delivered` —
  **independent of the `include_history` opt-in**, since the delivered-list
  sort (`sort_parcels_by_ts`) and the default "days" retention filter
  (`apply_delivered_filter`) both depend on it regardless of whether the
  fuller per-event history is switched on. Falls back to `None` (never a
  guess) when no scan parses.
- **`barcode` is always the AWB the coordinator requested**
  (`normalize_parcel(raw, awb=..., ...)`), never read off the payload — it
  is the only value guaranteed present even on an unknown/not-yet-scanned
  AWB. Do not add a `raw.get(...)` fallback for it.
- **Guard-and-report WARNINGs are still the entire mechanism by which this
  integration learns its own payload *shape***: an unexpected top-level key
  (against the now much larger `_KNOWN_TOP_LEVEL_KEYS`), a malformed
  `trackingStates`/`hqStatus`/`scans` shape, an unparseable
  `PromiseDeliveryDate`, and — once, ever — the full first populated
  `data[]` entry (redacted). All funnel through `parcels._warn_once`, except
  `_warn_uncertain_status` which is deliberately **not** deduped. **Do not
  quiet any of them without replacing the guess they guard with a real
  answer.**
- **`DIAGNOSTICS_REDACT_KEYS` in `const.py` is grounded but still
  pre-capture** — the 2026-08-09 correction named real fields
  (`addressDetails`, `orderDetails`, `fePhoneObj`, `ucid_consignor`/
  `ucid_consignee`, `referenceNo`, `cityLocation` nested inside each scan,
  `price_detail`, `orderAmount`, `paymentTerms`, `clientName`), but none of
  it has been checked against a real body — shared between
  `diagnostics.TO_REDACT` and the first-payload WARNING so there is exactly
  one list to update. **Must still be re-checked, leaf by leaf, against the
  first real capture** (the Dynalogic lesson: nested address blocks are the
  usual miss) — older placeholder guesses are kept rather than removed,
  over-redaction is cheap.
- **`weight`, `dimensions`, `pickup`, `pickup_point` stay `None`.** The
  research explicitly flags `weight` (`package_weight`) as low-confidence —
  the app buckets it to an icon, so it may not be a numeric kg value — and
  found nothing for the other three. Do not fill these in without new
  evidence in `carrier-research/delhivery/api/`. Reflected in `const.py`'s
  `CAPABILITIES` (feeds the docs site's comparison table) — keep the two in
  agreement if that ever changes.
- **`ref_id` (a documented alternate lookup key, merchant reference instead
  of AWB) is deliberately not implemented.** It changes the config-flow's
  tracking-code semantics (one field meaning two different things), which is
  a product decision, not a payload-mapping fix — see TODO.md.
- **Coverage is necessarily thinner than a carrier with a confirmed
  payload**, though less so since 2026-08-24: `tests/payloads.py`'s
  `LOST_CAPTURE_ENTRY` is a verbatim real `data[]` entry (redacted per
  `DIAGNOSTICS_REDACT_KEYS` — `awb`/`referenceNo`/nested `cityLocation`
  replaced with obvious placeholders, never fabricated). Everything else in
  that module stays **synthetic** — it proves the code does not crash and
  degrades gracefully on *a* plausible shape using first-party field
  *names*, not that this is *the* real shape for statuses other than
  `LOST`.
- **The 2026-08-24 capture also confirmed ten more top-level field names**
  (`currentFlow`, `currentTrackIndex`, `deliveryDate_v1`,
  `deliveryPillLabel`, `essential`, `isDirectPTL`, `isMaster`,
  `productType`, `subText`, `timelineModifications` — now in
  `_KNOWN_TOP_LEVEL_KEYS`) and surfaced a real gap worth knowing about
  before touching history/timestamp logic: in that capture, **every
  `scanDateTime`** — both the step-level one and the one inside `scans[]` —
  was an empty string, so `build_history`/`_last_scan_timestamp` extract
  nothing from it, even though a real timestamp exists at the (currently
  unmapped) top-level `status.statusDateTime`. `LOST` is a terminal/edge
  status; whether a normal in-transit/delivered capture behaves the same
  way is still unknown — do not start reading `status.statusDateTime` as a
  history/`delivered_at` source without a non-`LOST` capture to justify it.

### The Origin/Referer headers are not a credential

`api.py` hardcodes both as module-level constants (`const.DELHIVERY_ORIGIN`
/ `DELHIVERY_REFERER`) and sends them on every request — not
user-configurable, not a secret, just a same-origin check the endpoint
happens to enforce. Dropping them produces a uniform `401` on every request,
which reads like the endpoint moved rather than a missing header — the main
trap in the mechanics doc. Do not move them into config-flow input or a
`aiohttp.ClientSession` default-headers setup that could be bypassed by a
future refactor; keep them next to the endpoint URL.

## Options and reloads

The options flow is one sectioned form (`data_entry_flow.section`); changes apply
without a restart. Two models, **do not mix them**:
- **Account-less carriers** (the default, and what this repo is) apply changes
  live: an update listener calls `async_request_refresh()`, so added/removed
  parcel sensors appear immediately. This is also the resume path after
  dynamic polling has fully suspended (see below) — adding a parcel back
  triggers the same refresh, which re-arms scheduling.
- **Account-based carriers** call `async_schedule_reload` on submit and register
  **no** update listener. Combining a listener with a reload-on-update flow is
  deprecated, an error in HA 2026.12+.

## Polling

Polling is dynamic and status-driven, unconditionally — there is no
user-facing interval option and never has been one since the
2026-08-30 conversion (`carrier-research/dynamic-polling.md`). The
coordinator recomputes its own cadence at the end of every refresh: a quiet
window (00:00–06:00 local, with catch-up anchors at each end), a 15-minute
hot tier when a tracked parcel is `out_for_delivery` (immediately, or from an
hour before `planned_from`), a 45-minute mid tier otherwise, and a full stop
(`update_interval = None`) when nothing is tracked or everything tracked is
delivered. See `coordinator.py`'s `_hottest_tier_minutes` /
`_next_update_interval` and `ha-carrier-template`'s
`example_carrier/coordinator.py` for the canonical shape this mirrors.

## Module layout

| File | Carrier-specific? |
|---|---|
| `api.py` (HTTP client, error types) | **yes** |
| `const.py` (domain, URLs, `ParcelStatus`, option keys) | partly (URLs) |
| `parcels.py` (status map, `normalize_parcel`, history, sort, filters — pure, no I/O) | partly (`_STATUS_MAP`, `normalize_parcel`) |
| `coordinator.py` (fetch, cache, event firing) | mostly not |
| `config_flow.py` | partly (code validation) |
| `sensor.py` / `button.py` / `calendar.py` / `device_trigger.py` | no |
| `diagnostics.py` | partly (`TO_REDACT`) |
| `services.py` (`track_parcel` / `untrack_parcel`, account-less only) | no |

`parcels.py` is deliberately free of I/O and HA objects so the per-carrier part
stays unit-testable without Home Assistant. Config: `ConfigEntry.runtime_data`
(typed, no `hass.data`), `PARALLEL_UPDATES = 0`, coordinator takes
`config_entry=entry`. `aiohttp.ClientError` is caught **per parcel** in the gather
loop (one bad parcel doesn't fail the poll) but **not** around the whole update
(the coordinator wraps that). Entities: `has_entity_name` + `translation_key`,
`icons.json`, translated units, `_attr_attribution`, `_unrecorded_attributes` on
anything with a parcel list or `raw`. Over-redact diagnostics — they get pasted
into public issues.

## Running tests

```
python -m pytest tests/ --cov=custom_components.delhivery
```

Coverage must stay **above 95%** (silver `test-coverage` rule). Run before
committing. A code change updates the README + this file + `docs/` in the same
commit; the API reference lives in `carrier-research/delhivery/api/` (this
carrier's own directory in the private research repo), never in this repo.
