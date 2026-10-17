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

**Structure, options flow, dynamic polling and module layout are suite-wide**
and identical in every carrier — the authoritative spec is
[`ha-carrier-template/scaffold/CLAUDE.md`](https://github.com/ha-parcel-integrations/ha-carrier-template/blob/main/scaffold/CLAUDE.md).
This repo follows it exactly.

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

### ⚠️ Read this before touching `parcels.py`

A 2026-08-09 correction moved the payload's **field names** and **status
vocabularies** from third-party guesswork to first-party fact; a 2026-08-24
capture confirmed the first populated `data[]` entry; and a **2026-10-17
sweep of thirteen real AWBs** — covering `Delivered`, `RTO`, `DTO`, `LOST`
and `Closed` — confirmed the shape end to end. The mapping is no longer
inference. What that sweep settled, and the traps it exposed:

- **`data[]` always holds exactly one entry**, including for a master AWB
  carrying three bags (`isMaster: true`, `bag_count: 3`). A multi-leg journey
  is expressed *inside* that entry as the `trackingStates[]` ladder, not as
  several records, so `api.py` reading `data[0]` is correct and complete.
- **`scanDateTime` and `scanDate` are always empty strings** — on all
  eighteen scans across all thirteen parcels, not just the `LOST` edge case
  the 2026-08-24 note wondered about. `status.statusDateTime` is the only
  timestamp this endpoint populates, so `delivered_at` reads that first and
  falls back to a scan. Without it every delivered parcel had no delivery
  time, which left the delivered list unsortable and the "days" retention
  filter keeping everything.
- **`status.statusDateTime` is Indian local time, not UTC.** It arrives
  naive; read as IST it agrees with the carrier's own Morning / Afternoon /
  Evening label on ten of eleven parcels, read as UTC on two. Hence
  `const.CARRIER_TIMEZONE` and `_carrier_datetime`, which is deliberately
  separate from `parse_iso` — the latter still reads naive as UTC purely so a
  mixed list sorts without crashing.
- **`currentTrackIndex` marks ladder position, not `stepStatus`.** No live
  step has ever carried `stepStatus`; it is still honoured as a fallback, but
  the index is what works, with the last labelled step as a final resort.
- **`PromiseDeliveryDate` never appeared** on any captured parcel, so
  `planned_from`/`planned_to` came back `None` throughout. It stays declared
  in `CAPABILITIES` because the mapping is implemented and every captured
  parcel had already finished — this is "never seen populated", not proof the
  carrier omits it. The human `deliveryDate`
  ("24 Sep 2026, Afternoon") stays unused: on a finished parcel it is a
  record, not a prediction, and no in-flight capture exists to show which it
  is while a parcel is moving.

The pre-1.0 posture on *shape* still applies to everything the sweep did not
reach — an in-flight parcel, a pickup point, weight:

- **`_STATUS_MAP` carries both vocabularies plus the upper-case spelling
  `status.status` uses.** Eleven entries are listed in
  `_CONFIRMED_STATUSES` and are silent; the rest still self-report via
  `_warn_uncertain_status` — **on every occurrence, not just once** — because
  no parcel has reached them. This mirrors `ha-nova-post`'s pattern,
  generalised to the whole map. Distinct from the ordinary one-shot "totally
  unmapped value" warning, which stays for anything outside the map.
  **Do not case-fold before lookup**: `LOST`/`RTO`/`DTO` are upper case among
  title-case `hqStatus` siblings, the ladder spells its own labels in title
  case (`On the Way`, `Out for Delivery`, `Returned`, `Cancelled`, `Lost`),
  and `status.status` spells the same vocabulary in upper case — all three
  live in one dict keyed on the literal string. Add to `_CONFIRMED_STATUSES`
  (never remove from `_STATUS_MAP`) as captures confirm values.
  `hqStatus` `"Closed"` maps to `problem`: it rode alongside
  `deliveryPillLabel: "Cancelled"`, `deliveryDate_v1: "Cancelled on 10 Oct"`
  and a `Cancelled` ladder label, the same mutually-agreeing standard that
  confirmed `LOST`.
- **Status resolution is `hqStatus` first, `trackingStates[].label` ladder
  as fallback** (`_resolve_raw_status`) — `hqStatus` is the finer-grained
  field and was present on every captured parcel, so the ladder path is a
  safety net rather than a normal route. `_extract_ladder_label` positions
  itself on `currentTrackIndex`, then `stepStatus: 'current'`, then the last
  labelled step; the *last* entry is not automatically current, since
  `trackingStates` can list steps not yet reached.
- **`build_history` reads `trackingStates[].scans[]`, never a top-level
  `scans` key.** The earlier reconstruction had this backwards —
  `trackingStates` is a coarse step ladder, and the real event history hangs
  underneath each step in `scans[]` as `{scanDateTime, scanNslRemark,
  cityLocation}`. Each history entry's `raw_status`/`status` comes from its
  *containing step's* `label`, mapped through the same `_STATUS_MAP` — a
  confirmed field name, not an invented per-scan status.
- **`PromiseDeliveryDate` (capital P) maps to `planned_from` only** — never
  `planned_to`. No captured parcel has carried it yet. A separate,
  lower-confidence `deliveryDate` key exists too but is not used pending
  stronger evidence; a single field name is no evidence of a window either
  way.
- **`sender`/`receiver`/`url` are populated now** — `sender` ← `clientName`,
  `receiver` ← `consignee` (first-party, `.get()`-guarded reads only, no
  further validation since types/optionality are unconfirmed), `url` ←
  `const.DELHIVERY_TRACKING_URL.format(awb=...)`, a literal template
  confirmed verbatim — always populated, the one high-confidence field in
  this whole mapping.
- **`delivered_at` comes from `status.statusDateTime`**, falling back to the
  last parseable `scans[]` timestamp (`_last_scan_timestamp`), resolved once
  `status` maps to `delivered` —
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
- **`DIAGNOSTICS_REDACT_KEYS` in `const.py` was re-checked leaf by leaf
  against the 2026-10-17 captures**, which added `addressInfo` (recipient
  city/state/pincode), `scannedLocation`, and `dropUAID`/`pickupUAID` —
  the last two carry what reads as a delivery-executive handle after the
  facility code. The 2026-08-09 correction named real fields
  (`addressDetails`, `orderDetails`, `fePhoneObj`, `ucid_consignor`/
  `ucid_consignee`, `referenceNo`, `cityLocation` nested inside each scan,
  `price_detail`, `orderAmount`, `paymentTerms`, `clientName`); of those, the
  captures showed `referenceNo` and the nested `cityLocation` populated and
  the rest absent. The list is shared between `diagnostics.TO_REDACT` and the
  first-payload WARNING so there is exactly one list to update. Older
  placeholder guesses are kept rather than removed — over-redaction is cheap,
  and a field absent from a finished parcel may still appear on an in-flight
  one (the Dynalogic lesson: nested address blocks are the usual miss).
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

## Running tests

```
python -m pytest tests/ --cov=custom_components.delhivery
```

Coverage must stay **above 95%** (silver `test-coverage` rule). Run before
committing. A code change updates the README + this file + `docs/` in the same
commit; the API reference lives in `carrier-research/delhivery/api/` (this
carrier's own directory in the private research repo), never in this repo.
