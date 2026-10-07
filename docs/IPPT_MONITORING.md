# IPPT Monitoring — Requirements & Report Schema

Preparatory reference for the planned IPPT monitoring dashboard. The policy
rules in §1 come from the unit's IPPT requirements; the report schemas in §2–§4
were derived from the sample reports in `fixtures/ippt/` (snapshot dated
2026-09-11). Semantics marked **[confirmed]** were clarified with the unit.
**No IPPT feature is implemented yet** — this document exists so the dashboard
work can start from an agreed model.

---

## 1. IPPT requirements (policy)

### 1.1 The IPPT window

- IPPT is an **annual** requirement for all eligible NSmen.
- Each NSman has an **IPPT window** that **opens on his birthday** and
  **closes the day before his next birthday** (~1 year later).
- NSmen **may book and attempt IPPT multiple times** within the window; an
  award on *any* attempt meets the obligation.

### 1.2 Meeting the obligation

A window's IPPT obligation is met by any of:

1. **IPPT award** — achieving **Pass**, **Pass with Incentive**, **Silver**,
   or **Gold** on any attempt within the window; or
2. **10 NS FIT sessions** completed within the window, which may be:
   - **Mandatory NS FIT** — the NSman attempted IPPT at least once in the
     *previous* window but failed all attempts, so 10 FIT sessions become
     mandatory in the *current* window. Completing them discharges **both**
     the previous window's (failed) obligation and the current window's
     obligation — no further IPPT attempt is needed; or
   - **Voluntary NS FIT** — 10 sessions completed voluntarily within the
     window, with no prior failure required; or
3. **Special handling (exceptional)** — the unit may waive a window's
   obligation in exceptional cases, e.g. a medical review that restored
   IPPT eligibility mid-window; the waiver applies to that window only.
   These appear in `*_COMPLETED` with status `-` (§2.4) **[confirmed]**.

While on mandatory NS FIT, the NSman **may still book and attempt IPPT**
during that window; a pass discharges both the previous (failed) and current
window's obligations, exactly as completing the 10 sessions would.
**[confirmed]**

Display ordering of results (lowest → highest): `Pass` < `Pass with
Incentive` < `Silver` < `Gold`.

### 1.3 IPPT defaulters

An NSman is an **IPPT defaulter** if, at window close:

- he **did not attempt IPPT at least once** within the window (and did not
  complete 10 voluntary NS FIT sessions); **or**
- he **failed all attempts in the previous window** and **did not complete
  the 10 mandatory NS FIT sessions** (nor pass IPPT) within the next window.

Partial FIT counts (1–9) never meet the obligation, so defaulting is only
decidable at/after window close.

> **Monitoring scope [confirmed]:** the monitoring dashboard does **not**
> track defaulters. Defaulted servicemen are reported separately, outside
> these six files; defaulter handling may be added by a future feature.
> §1.3 is background policy only.

---

## 2. Monitoring report suite

### 2.1 File naming and populations

Files are named `{REPORT}_{STATE}_{YYYYMMDD}.csv`, e.g.
`IPPT_COMPLETED_20260911.csv`. The date suffix is the report generation date;
all relative values (notably `Window close`) are relative to it.

| Prefix | Population |
|---|---|
| `IPPT_` | Working towards the current window's obligation via IPPT attempts or **voluntary** NS FIT |
| `NSFIT_` | On **mandatory** NS FIT (the window after failing all attempts) |

Six files make up one snapshot **[confirmed]**:

| File | Population / meaning | Rows in sample |
|---|---|---|
| `IPPT_COMPLETED_YYYYMMDD.csv` | Current-window obligation closed — via IPPT award, completed voluntary NS FIT, or special-handling waiver | 49 |
| `IPPT_FAILED_YYYYMMDD.csv` | Current-window obligation outstanding — attempted but not passed, or voluntary NS FIT incomplete | 31 |
| `IPPT_NOT_ATTEMPTED_YYYYMMDD.csv` | No IPPT attempt yet and not on voluntary NS FIT | 132 |
| `NSFIT_COMPLETED_YYYYMMDD.csv` | Mandatory NS FIT obligation discharged — 10 sessions or an IPPT pass during the window | 5 |
| `NSFIT_IN_PROGRESS_YYYYMMDD.csv` | Mandatory NS FIT, 1–9 sessions done | 9 |
| `NSFIT_NOT_STARTED_YYYYMMDD.csv` | Mandatory NS FIT, 0 sessions done | 58 |

- A serviceman appears in **at most one** of the six files per snapshot (true
  in the sample; do not rely on it without validation).
- Files that carry `Window close` are **sorted ascending** by it (most urgent
  first).
- Expected transitions across report dates: a `Fail` row in `IPPT_FAILED`
  whose window closes without a pass reappears under `NSFIT_*` in the next
  window (mandatory FIT); NSFIT rows that discharge reappear in
  `NSFIT_COMPLETED` and then leave the suite.

### 2.2 Schema families

There are **three distinct column layouts**. Two files share the same fields
**in a different order** (`IPPT_FAILED` vs `NSFIT_IN_PROGRESS`), so parsers
**must map by header name, never by position**.

**Family A — completed** (`IPPT_COMPLETED`, `NSFIT_COMPLETED`):

```
Rank, Name, Unit, Sub-unit, IPPT status, FIT sessions completed
```

No `Window close` — the obligation is already met, so remaining days are moot.

**Family B — outstanding** (`IPPT_FAILED`, `NSFIT_IN_PROGRESS`):

```
IPPT_FAILED:        Rank, Name, Unit, Sub-unit, Window close, IPPT status, FIT sessions completed, Reminder sent, Last sent
NSFIT_IN_PROGRESS:  Rank, Name, Unit, Sub-unit, Window close, FIT sessions completed, IPPT status, Reminder sent, Last sent
```

**Family C — not started** (`IPPT_NOT_ATTEMPTED`, `NSFIT_NOT_STARTED`):

```
Rank, Name, Unit, Sub-unit, Window close, Booked Status, FFI, Reminder sent, Last sent
```

### 2.3 Field reference

| Field | Type | Observed domain | Notes |
|---|---|---|---|
| `Rank` | string | 15 ranks: `1SG 2SG 3SG 3WO CFC CPL CPT LCP LTA MAJ ME1 ME2 MSG PTE SSG` | Fixed-width, space-padded to 6 chars (e.g. `CPL   `) — **strip before use** |
| `Name` | string | Uppercase full name | May contain commas (RFC 4180 quoted, e.g. `"TAN KAI KEONG, DERRICK"`), `S/O`, `BIN`, apostrophes. Not padded |
| `Unit` | string | `DK314` (constant in samples) | |
| `Sub-unit` | string | `BN HQ`, `FORWARD MAINT COY (LIGHT)`, `INF SUPPLY COY`, `MEDICAL COY`, `NON-ESTAB`, `S4 BR/HQ COY`, `SECURITY COY` | Same vocabulary as the nominal roll's sub-units; `NON-ESTAB` = non-establishment posting |
| `Window close` | int | 4–361 in samples | **Days remaining** until the window closes, relative to the report date (absolute close = report date + N). Sorted ascending within the file. Absent from Family A |
| `IPPT status` | string | `Pass`, `Pass with Incentive`, `Silver`, `Gold`, `Fail`, `Complete FIT`, `-` | `-` = none/no attempt this window; in `*_COMPLETED` it means waived (§2.4) |
| `FIT sessions completed` | int | 1–9 observed or `-` | **Never use this count to decide completion** — completion is designated by `IPPT status` / file membership (§2.4) **[confirmed]** |
| `Booked Status` | string | `Yes`, `No` | **Route-specific [confirmed]**: booked an IPPT slot (`IPPT_NOT_ATTEMPTED`) vs booked NS FIT session(s) (`NSFIT_NOT_STARTED`) |
| `FFI` | string | `NA`, `Fit`, `Pending` | Health-screening status **[confirmed]**: IPPT-eligible NSmen **aged 35+** must complete a health screening **before they can book IPPT**. `NA` = screening not required, `Fit` = screened fit, `Pending` = screening outstanding (cannot book IPPT yet). `Pending` only seen in `IPPT_NOT_ATTEMPTED`. Acronym expansion unconfirmed (§5) |
| `Reminder sent` | string | `-` only | Reminder tracking; unused in all samples (§5) |
| `Last sent` | string | `-` only | Reminder tracking; unused in all samples (§5) |

**`-` is the null/NA sentinel in every column** — treat it as `NULL`, not as a
literal string or 0.

**`*_COMPLETED` membership is authoritative [confirmed]:** any row in a
completed file has that window's obligation closed — met via award, met via
FIT, or waived by special handling — regardless of the `IPPT status` value or
FIT count shown on the row.

### 2.4 Per-file observations

**`IPPT_COMPLETED` (49 rows)** — current-window obligation closed (met or
waived):
- 38 award rows: Pass with Incentive 15, Gold 9, Pass 7, Silver 7. Award rows
  may carry non-zero FIT counts (`Gold,6`, `Pass with Incentive,9`) — FIT
  sessions attended before subsequently passing; the award is what closed
  the obligation.
- 6 rows `Complete FIT` — obligation met via **completed voluntary NS FIT**
  (hence the `IPPT_` file). All six show exactly 9 sessions — a known quirk
  of the count column; the designation is authoritative **[confirmed]**.
- 5 rows with status `-` (four with no FIT count, one with 2) — exceptional
  cases granted special handling: the obligation was made non-mandatory for
  that window only, e.g. a mid-window medical review that restored IPPT
  eligibility with too little window left **[confirmed]**. A residual FIT
  count is incidental progress and carries no completion meaning.

**`IPPT_FAILED` (31 rows)** — current-window obligation outstanding, members
not in default-risk tracking (§1.3) **[confirmed]**:
- 13 rows `Fail` — attempted this window, no award yet; can still re-attempt
  before `Window close` (down to **4 days** — the most urgent rows in the
  suite). If the window closes without a pass → mandatory NS FIT next window.
- 18 rows status `-` with FIT 1–9 — **voluntary NS FIT in progress**, no
  current-window attempt **[confirmed]**.

**`IPPT_NOT_ATTEMPTED` (132 rows)** — no attempt, not on voluntary FIT:
- Booked Status: Yes 80 / No 52 (IPPT slot bookings).
- FFI: NA 116 / Fit 14 / Pending 2 (`Pending` = 35+ screening outstanding,
  cannot book IPPT until cleared).

**`NSFIT_COMPLETED` (5 rows)** — mandatory FIT obligation discharged:
- All five show `Pass` / `Pass with Incentive` (one with 1 FIT session) —
  discharged by **passing IPPT mid-programme**, which the unit confirmed is
  allowed and sufficient. No 10-session completion appears in the sample;
  what such a row looks like is unobserved (§5) — file membership is the
  authoritative completion signal **[confirmed]**.

**`NSFIT_IN_PROGRESS` (9 rows)**:
- 8 rows status `-` with FIT 1–9 — mandatory FIT in progress.
- 1 row `Fail` with no sessions — on mandatory FIT, attempted IPPT during
  the window and failed, no FIT sessions yet.
- Same fields as `IPPT_FAILED` but **column order differs** (§2.2).

**`NSFIT_NOT_STARTED` (58 rows)**:
- Booked Status: Yes 20 / No 38 (NS FIT session bookings).
- FFI: NA 53 / Fit 5 (whether the 35+ screening also gates NS FIT bookings
  is unconfirmed — §5).

---

## 3. Derived model for the dashboard

### 3.1 Unified obligation state

The dashboard should fold the six reports into one per-serviceman state
(implemented as the `state` column of `ippt_state_observations`, §6):

| Source row | Derived state |
|---|---|
| `*_COMPLETED`, IPPT status = award | `MET — IPPT award` (record best award) |
| `*_COMPLETED`, IPPT status = `Complete FIT` | `MET — voluntary FIT` |
| `*_COMPLETED`, IPPT status = `-` | `WAIVED — special handling (per-window exemption)` |
| `IPPT_FAILED`, status = `Fail` | `ATTEMPTED — FAILED (can re-attempt; window closing)` |
| `IPPT_FAILED`, status = `-`, FIT 1–9 | `VOLUNTARY FIT IN PROGRESS` |
| `NSFIT_IN_PROGRESS`, FIT 1–9 | `MANDATORY FIT IN PROGRESS` |
| `NSFIT_IN_PROGRESS`, status = `Fail`, FIT `-` | `MANDATORY FIT + failed in-window IPPT attempt` |
| `NSFIT_COMPLETED` | `MET — mandatory FIT discharged (sessions or mid-programme pass)` |
| `IPPT_NOT_ATTEMPTED` / `NSFIT_NOT_STARTED`, Booked = `Yes` | `OUTSTANDING — booked` |
| `IPPT_NOT_ATTEMPTED` / `NSFIT_NOT_STARTED`, Booked = `No` | `OUTSTANDING — not booked` |

There is deliberately **no defaulter state** — defaulters are outside the
monitoring feature's scope (§1.3) **[confirmed]**.

Escalation priority should key off `Window close` (sample floor: 4 days) and
`Booked Status = No`; for `IPPT_NOT_ATTEMPTED` rows with `FFI = Pending`,
chase the health screening first — IPPT cannot be booked until it clears.

### 3.2 Identity resolution

- The reports carry **no personnel ID**; the only natural key is
  `Rank + Name (+ Sub-unit)`, and names contain commas, `S/O`, and
  apostrophes.
- Sub-unit vocabulary matches the nominal roll, so rows can be matched against
  the establishment — plan for assisted/fuzzy matching with manual
  confirmation, since homonyms are possible and rank changes over time.

### 3.3 Snapshot history

Retaining per-date snapshots is still useful — to follow transitions
(`IPPT_FAILED` → `NSFIT_*` at window rollover, progress of FIT session
counts) and as an audit trail. Defaulter detection is *not* a reason:
it is out of scope (§1.3).

---

## 4. Ingestion notes

- Encoding: plain ASCII, RFC 4180; commas inside names are quoted.
- Map columns **by header name** — column order is not stable across files.
- Normalisation: strip `Rank` trailing spaces; convert `-` → `NULL`; parse
  `Window close` and `FIT sessions completed` as integers when present.
- Completion is decided by `IPPT status` / file membership, never by counting
  FIT sessions — do not reject `Complete FIT` rows for showing 9 **[confirmed]**.
- Cheap validation guards: `Window close ∈ [0, 366]`; `FIT ∈ [0, 10]`;
  `Sub-unit` ∈ known set; `Unit` = expected unit; flag anything else for
  manual review rather than rejecting the file.
- Expect all six files per report date; treat a missing file as an alert, not
  an empty population.

---

## 5. Open questions

1. **Does the 35+ health screening gate NS FIT bookings too?** Only the IPPT
   booking gate is confirmed; `FFI` appears in `NSFIT_NOT_STARTED` with
   `Fit` values, implying it is recorded (and may matter) there as well.
2. **`Reminder sent` / `Last sent`** — present in two families but always
   `-`; confirm the populated format (date? count?) before building reminder
   features on them.
3. **What does a 10-session `NSFIT_COMPLETED` row look like?** No such row in
   the sample (`IPPT status` value and session count unobserved); file
   membership is authoritative either way.
4. **`FFI` acronym expansion** — semantics are confirmed (§2.3) but the
   expansion of the initialism itself is not.

### Resolved in clarification (for the record)

- `Complete FIT` rows showing 9 sessions: designation authoritative over the
  count column.
- `NSFIT_COMPLETED` rows showing `Pass`/`Pass with Incentive`: passing IPPT
  during the mandatory-FIT window discharges the obligation (both windows).
- `IPPT_FAILED` vs `NSFIT_*` boundary: `IPPT_FAILED` = obligation outstanding
  via attempts or voluntary FIT; `NSFIT_*` = mandatory FIT only.
- `Booked Status` is route-specific; `FFI` is the 35+ health-screening gate.
- `IPPT_COMPLETED` rows with status `-`: special-handling waivers (e.g. a
  mid-window medical review restored eligibility; obligation non-mandatory
  for that window only). `*_COMPLETED` membership is authoritative.
- Defaulter tracking is out of monitoring scope; defaulters are reported
  separately.
- NS FIT bookings are not visible in the reports; the FIT route earns tier
  credit only from attended sessions (zero observed sessions = flagged).
- Tier semantics: screening credit requires booking (`fit` + booked);
  incomplete FIT is highlighted at 6 months, mandatory included; "booked
  IPPT after FFI" = booked with FFI `fit`; `waived` counts as fulfilled in
  all tiers; highlights err on the side of caution (false positives can be
  relaxed later, false negatives are hard to detect).

---

## 6. Proposed common tracking schema (design — not yet implemented)

The six reports are projections of one underlying state machine, so the
common schema stores **what the source actually provides — periodic state
observations** — rather than inventing event-level history the reports don't
contain (no per-attempt results, no per-session dates; §6.5). Five tables
plus views, PostgreSQL to match the app stack, prefixed `ippt_` to
namespace them in the shared database.

### 6.1 Tables

```sql
-- Identity spine: one row per serviceman seen in any report.
create table ippt_servicemen (
    id            bigserial primary key,
    rank          varchar(16)  not null,           -- latest seen, stripped
    full_name     text         not null,
    unit          varchar(32)  not null,           -- 'DK314' in all samples
    sub_unit      varchar(64)  not null,           -- nominal-roll vocabulary
    personnel_id  bigint references personnel(id), -- NULL until resolved (§3.2)
    created_at    timestamptz  not null default now(),
    updated_at    timestamptz  not null default now()
);
-- No unique natural key: rank/sub-unit drift and homonyms make
-- (name, sub-unit) a match *hint*, not a constraint. Links to the nominal
-- roll are reviewed before being set (§3.2).

-- One row per ingested report file (provenance + missing-file alerting).
create table ippt_snapshots (
    id           bigserial primary key,
    report_date  date not null,          -- from the filename YYYYMMDD
    file_kind    text not null check (file_kind in (
                   'ippt_completed', 'ippt_failed', 'ippt_not_attempted',
                   'nsfit_completed', 'nsfit_in_progress', 'nsfit_not_started')),
    filename     text not null,
    row_count    int  not null,
    ingested_at  timestamptz not null default now(),
    unique (report_date, file_kind)      -- one authoritative copy per date
);

-- Birthday-anchored window per serviceman; derived at ingest, not input.
create table ippt_windows (
    id            bigserial primary key,
    serviceman_id bigint not null references ippt_servicemen(id),
    window_end    date   not null,       -- = report_date + window_close days
    window_start  date   not null,       -- birthday: (window_end + 1 day) − 1 year
    unique (serviceman_id, window_end)
);
-- Successive snapshots of the same window must agree on window_end — a
-- cheap consistency check during ingest. A change of window_end for a
-- serviceman = window rollover, which is how FAILED → mandatory NS FIT
-- transitions are recognised. *_COMPLETED rows carry no window close:
-- their observations keep window_id NULL, backfilled at ingest from the
-- serviceman's preceding outstanding observation when one exists.

-- The trajectory spine: one row per serviceman per snapshot.
create table ippt_state_observations (
    id                bigserial primary key,
    snapshot_id       bigint not null references ippt_snapshots(id),
    serviceman_id     bigint not null references ippt_servicemen(id),
    window_id         bigint references ippt_windows(id),  -- NULL: *_COMPLETED rows carry no window close
    rank              varchar(16) not null,   -- as seen (drift audit)
    sub_unit          varchar(64) not null,   -- as seen
    state             text not null,          -- §6.2 enum
    ippt_status       text,                   -- raw value; '-' → NULL (§2.3)
    fit_sessions      smallint,               -- informational; never decides completion (§2.3)
    window_close_days smallint,               -- raw; absolute deadline via window_id
    booked            boolean,                -- route-specific (§2.3): IPPT slot / NS FIT session
    ffi               text check (ffi in ('na','fit','pending')),
    reminder_sent     text,                   -- raw pass-through; unused in samples (§5)
    last_sent         text,
    unique (snapshot_id, serviceman_id)       -- also enforces the at-most-one-file rule (§2.1)
);

-- Health-screening history (FFI). status <> 'na' ⇔ 35+ screening applies;
-- 'pending' blocks IPPT booking until cleared (§2.3).
create table ippt_health_screenings (
    id            bigserial primary key,
    serviceman_id bigint not null references ippt_servicemen(id),
    observed_on   date   not null,        -- snapshot report_date
    status        text   not null check (status in ('na','fit','pending')),
    screened_on   date,                   -- screening date; NULL until the
                                          -- pending→fit flip is observed
    unique (serviceman_id, observed_on)
);
-- Mirrors observations.ffi as first-class history so reminder logic can
-- chase 'pending' transitions without scanning the observation log. The
-- reports carry no explicit screening date, so `screened_on` is
-- approximated at ingest as the report_date of the first snapshot showing
-- 'fit' after a 'pending'; the §6.4 tier logic keys off it.
```

### 6.2 Unified `state` enum

Derived at ingest by a pure function of `(file_kind, ippt_status,
fit_sessions)` — the §3.1 mapping with `booked` kept as a separate boolean:

| state | Source |
|---|---|
| `met_award` | any `*_COMPLETED`, status = award |
| `met_voluntary_fit` | `IPPT_COMPLETED`, status = `Complete FIT` |
| `met_mandatory_fit` | `NSFIT_COMPLETED` (10 sessions or mid-programme pass) |
| `waived` | `*_COMPLETED`, status = `-` (§2.4) |
| `failed_can_reattempt` | `IPPT_FAILED`, status = `Fail` |
| `voluntary_fit_in_progress` | `IPPT_FAILED`, status = `-`, FIT 1–9 |
| `mandatory_fit_in_progress` | `NSFIT_IN_PROGRESS`, FIT 1–9 |
| `mandatory_fit_failed_attempt` | `NSFIT_IN_PROGRESS`, status = `Fail` |
| `ippt_not_attempted` | `IPPT_NOT_ATTEMPTED` |
| `nsfit_not_started` | `NSFIT_NOT_STARTED` |

Completion is set only from these designations — never by counting FIT
sessions, and `*_COMPLETED` membership is authoritative (§2.3) **[confirmed]**.

### 6.3 Views

```sql
-- Dashboard landing state: latest observation per serviceman.
create view ippt_current_state as
select distinct on (o.serviceman_id)
       s.full_name, s.rank, s.sub_unit, o.state, o.fit_sessions,
       w.window_end, (w.window_end - current_date) as days_to_close,
       o.booked, o.ffi
from ippt_state_observations o
join ippt_snapshots snap on snap.id = o.snapshot_id
join ippt_servicemen  s   on s.id = o.serviceman_id
left join ippt_windows w  on w.id = o.window_id
order by o.serviceman_id, snap.report_date desc;

-- Trajectory: full history per serviceman, oldest → newest.
create view ippt_trajectory as
select s.full_name, snap.report_date, o.state, o.ippt_status,
       o.fit_sessions, o.booked, o.ffi, w.window_end
from ippt_state_observations o
join ippt_snapshots  snap on snap.id = o.snapshot_id
join ippt_servicemen s    on s.id = o.serviceman_id
left join ippt_windows w  on w.id = o.window_id
order by s.id, snap.report_date;
```

The booking → fulfilment → mandatory NS FIT arc reads as: `ippt_not_attempted`
(booked = No → Yes) → `failed_can_reattempt` on window *N* → new window
(`window_end` jumps ~a year) with `nsfit_not_started` →
`mandatory_fit_in_progress` with the FIT count climbing → `met_mandatory_fit`.
Health screening runs alongside: `ffi = 'pending'` rows gate IPPT booking
chases until they flip to `'fit'`.

Caveat: `FFI` is only reported in Family C files (§2.2), so the trajectory
shows screening values only for months where the serviceman sat in a
not-started file — once booked/attempted, screening stops being observable.
Months with gaps (missed uploads) simply have no row; the view never
interpolates.

### 6.4 Escalation tiers (3/6/9-month highlights)

Uploads arrive monthly — ideally on the 1st, with drift and occasional
missed months expected, so cadence is never load-bearing. Tiers are
therefore computed as **window-progress bands**: `months_elapsed` between
`window_start` and the observation's `report_date`, evaluated against each
serviceman's latest observation. A late or missing upload simply
re-evaluates everyone at the next upload; no tier is ever skipped.

| Tier | Band | Highlight a serviceman who has NOT … |
|---|---|---|
| 3-month | `3 ≤ months_elapsed < 6` | … taken any preparatory action: no IPPT booking, attempt or result, no attended NS FIT session, no cleared health screening |
| 6-month | `6 ≤ months_elapsed < 9` | … made real progress: no IPPT attempt (failed or completed result), no post-screening IPPT booking, no completed NS FIT (voluntary or mandatory) |
| 9-month | `months_elapsed ≥ 9` | … fulfilled the window's IPPT requirement |

Mapped onto the §6.2 state enum **[confirmed]**, governed by one principle:
**when in doubt, highlight** — false positives can be relaxed later, false
negatives go unnoticed.

- **Tier 3 satisfactory** — any state other than `ippt_not_attempted` (a
  result, any attended FIT session ≥ 1, or membership of the mandatory-FIT
  route all count), or `ippt_not_attempted` with `booked = true` and the
  FFI condition met (`fit`, or `na` — under 35, no screening required).
  Highlighted: `ippt_not_attempted` with `booked = false` (any FFI), or
  `booked = true` with `ffi = 'pending'`. NS FIT *bookings* are not visible
  in the reports, so the FIT route earns credit only for attended
  sessions — zero observed sessions, zero credit **[confirmed]**.
- **Tier 6 satisfactory** — `failed_can_reattempt` (attempted), any
  `met_*`, `waived` (counts as fulfilled **[confirmed]**), or
  `ippt_not_attempted` with `booked = true` **and** `ffi = 'fit'`
  ("booked IPPT after FFI" **[confirmed]**). Highlighted: everything else —
  incomplete FIT routes are chased at 6 months, mandatory included
  (`voluntary_fit_in_progress`, `mandatory_fit_in_progress`,
  `mandatory_fit_failed_attempt`, `nsfit_not_started`) **[confirmed]**.
  Booked-but-not-attempted no longer counts unless the FFI condition is
  met; `na` + booked without an attempt is highlighted — a cautious
  default, relaxable if it proves noisy.
- **Tier 9 satisfactory** — any `met_*` or `waived`; everything else is
  highlighted until fulfilled.

Observations whose window has already closed (`window_end <
report_date`) stay outside the tiers — post-close outcomes are defaulter
territory, which is out of monitoring scope (§1.3).

```sql
-- Tier assignment over each serviceman's latest in-window observation.
create view ippt_escalations as
with cur as (
    select distinct on (o.serviceman_id)
           o.*, snap.report_date
    from ippt_state_observations o
    join ippt_snapshots snap on snap.id = o.snapshot_id
    order by o.serviceman_id, snap.report_date desc
)
select c.serviceman_id,
       months_between(w.window_start, c.report_date) as months_elapsed,
       w.window_end, c.state, c.booked, c.ffi,
       case
         when c.state in ('met_award','met_voluntary_fit',
                          'met_mandatory_fit','waived') then null
         when months_between(w.window_start, c.report_date) >= 9
           then 'tier_9'
         when months_between(w.window_start, c.report_date) >= 6
           then 'tier_6'
         when months_between(w.window_start, c.report_date) >= 3
           then 'tier_3'
       end as tier
from cur c
join ippt_windows w on w.id = c.window_id
where w.window_end >= c.report_date;               -- in-window only
```

(`months_between` = floored calendar-month difference — in PostgreSQL,
`12 * (year(a) − year(b)) + (month(a) − month(b))`, minus 1 if the
day-of-month hasn't been reached yet.) The per-tier highlight lists then
filter this view on `state`, `booked`, and `ffi`/screening history per the
rules above.

### 6.5 Deliberately excluded (source fidelity)

- **`ippt_attempts`** — the reports carry only the latest aggregate status,
  not per-attempt results or dates.
- **`nsfit_sessions`** — only running counts (1–9), no session dates.
- **Booking details** — `Booked Status` is a Yes/No flag, not slot data.
- **Defaulter tracking** — out of monitoring scope (§1.3).

If a future source exposes any of these, they slot in alongside the
observation spine without changing it.
