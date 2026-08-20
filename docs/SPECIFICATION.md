# Parade State Management System - Technical Specification

**Version:** 1.0  
**Date:** 2026-05-08  
**Status:** Implementation Specification  

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Entity Hierarchy](#2-entity-hierarchy)
3. [Data Model Specification](#3-data-model-specification)
4. [Business Rules & Constraints](#4-business-rules--constraints)
5. [Access Control & Security](#5-access-control--security)
6. [API & Integration Patterns](#6-api--integration-patterns)
7. [Technical Decisions](#7-technical-decisions)

---

## 1. System Overview

### 1.1 Problem Statement

The personnel branch currently manages battalion parade state through a manual mapreduce process — aggregating attendance from subunits by hand. This system replaces that process with a structured, access-controlled, grouping-aware web application suitable for field use.

### 1.2 Core Entity Hierarchy

```
Nominal Roll (CAA-pinned, CSV-sourced, read-only; one NR is active for attendance)
 ├── Tagging (1:1 with NR; the overlay of person → subunit remaps; never mutates the NR)
 └── Attendance (one row per personnel/day on the active NR; AM and PM status + remarks)

Grouping (remaps personnel unit+subunit; has date+time validity range) —
  a separate feature, not linked to attendance.
```

**Key Concepts:**
- **Nominal Roll**: Base source of truth, uploaded from CSV, pinned by CAA date, read-only — unit/subunit edits are recorded on the NR's Tagging. Exactly one NR is **active for attendance** at a time (super-admin toggles "Use for Attendance" / "Deactivate Attendance" in the /nominal-roll view's Roll management panel; activating another NR auto-switches).
- **Tagging**: 1:1 with an NR; the overlay of person → subunit remaps; never mutates the NR; always applied when attendance is taken against the active NR.
- **Attendance**: One row per `(personnel, date)`, carrying `status_am`/`remarks_am` and `status_pm`/`remarks_pm` (statuses from the nine-value operational enum). AM and PM are hardcoded — there is no longer a user-managed Session model. Writes are only permitted against the active NR.
- **Grouping**: Based on a nominal roll, remaps personnel assignments, valid for date+time range. A separate feature — the grouping view's checkbox/remarks never interact with attendance.

### 1.3 Scope

**In Scope (v1):**
- CSV ingestion with CAA versioning, column mapping, diff detection
- Grouping management: create, clone (same-roll), migrate (cross-roll), scheduled activation
- Attendance taking: AM/PM (hardcoded), nine-status operational reporting enum, NR-scoped with the 1:1 Tagging overlay applied, active-NR gating
- Row access control (access level + subunit scope) and column sensitivity control
- Parade state table view scoped to user access with inline editing
- Admin UI: enums, users, column sensitivity, column mapping, grouping/tagging/attendance management
- Mobile-friendly static HTML/JS attendance frontend
- Service worker + IndexedDB read-only cache (24hr TTL, stale indicator)
- SSE stale-detection signal on attendance view

**Out of Scope (deferred):**
- Serviceman self-service access
- View projections / aggregated dashboards / export
- Automated push notifications or HQ reporting
- Vue SFC refactor of mobile frontend (revisit after MVP)

---

## 2. Entity Hierarchy

### 2.1 Nominal Roll (CSV Ingestion)

**Base personnel roster, sourced from CSV, pinned by CAA (Complement As At) date.**

```
Nominal Roll
├── id: UUID (PK)
├── caa: date (Complement As At; UNIQUE)
├── csv_hash: str (SHA-256 of raw CSV)
├── attendance_active: bool (exactly one NR active at a time; application-enforced)
├── attendance_activated_at: datetime (nullable; last activation, kept as history)
├── attendance_activated_by: UUID (FK User, nullable)
├── personnel_count: int
├── uploaded_at: datetime
├── uploaded_by: UUID (FK User)
├── created_at: datetime
├── notes: str (nullable; admin notes on this nominal roll)
└── label: str (nullable; UNIQUE; human-readable name, max 100 chars)
```

**Constraints:**
- UNIQUE(caa)
- UNIQUE(label) across all nominal rolls (NULLs allowed; enforced on non-null values)
- **No status workflow** — all NRs are equal; the confirm/unconfirm lifecycle is removed
- **Attendance activation**: `POST /nominal-rolls/{id}/activate-attendance`
  (super-admin; auto-deactivates the previously active NR) and
  `POST /nominal-rolls/{id}/deactivate-attendance`. With no active NR,
  the attendance view shows an inactive message and writes are refused.
- Raw CSV stored immutably in csv_uploads (append-only; SHA-256 hash recorded)
- Parsed personnel in personnel_snapshots: required columns as typed fields; all others in extra_fields JSON
- **Read-only after ingestion** — unit/subunit edits are recorded on the NR's 1:1 Tagging, never on personnel rows

**CSV → NR processing flow (app-side):**
1. `POST /api/v1/csv/upload` — stores raw bytes in `CsvUpload` (SHA-256 dedup)
2. `POST /api/v1/csv/{upload_id}/process` — parses the stored upload (CAA date
   from the `caaYYMMDD` filename token) and inserts NominalRoll +
   ColumnMetadata + Personnel + an auto-created empty Tagging; links the
   upload via `CsvUpload.nominal_roll_id`. Optional
   `source_nominal_roll_id` copies the source NR's tagging entries across
   by `pers_no` matching instead of starting empty (unmatched source
   personnel are surfaced in the response).

### 2.2 Grouping

**Operational grouping based on an nominal roll, with overrides and validity window.**

*Not yet shipped: hidden behind the `FEATURE_GROUPING` env-var flag (see
[4.8 Feature Flags](#48-feature-flags-env-var-kill-switches)).*

```
Grouping
├── id: UUID (PK)
├── name: str
├── nominal_roll_id: UUID (FK Nominal Roll, on_delete=RESTRICT)
├── status: str ENUM ['draft', 'active', 'inactive', 'archived', 'closed', 'finalized']
│   └── draft: not yet active
│   └── active: currently operational (only one per system, application-enforced)
│   └── inactive: was active, now past validity window
│   └── archived: retained for history but no longer operational
│   └── closed: no further edits permitted
│   └── finalized: permanent archive; cascades closure to all sessions
├── valid_from: datetime (when grouping becomes active)
├── valid_until: datetime (when grouping expires)
├── scheduled_activation: datetime (nullable; explicit scheduled time)
├── personnel_count: int (snapshot; non-archived personnel in this grouping)
├── created_at: datetime
├── created_by: UUID (FK User)
├── activated_at: datetime (nullable; when actually transitioned to active)
├── deactivated_at: datetime (nullable; when transitioned away from active)
└── notes: str (nullable; admin notes)
```

**Constraints:**
- Only one grouping can have status = 'active' (enforced at application layer)
- Validity range overlaps with existing draft/active grouping → hard reject

### 2.3 Attendance (AM/PM hardcoded, active-NR model)

**AM and PM are hardcoded; there is no user-managed Session model and no
separate scope table.** Attendance is taken against the one Nominal Roll
currently **active for attendance** (`NominalRoll.attendance_active`), with
the NR's 1:1 Tagging overlay always applied.

```
Attendance (one row per personnel/day)
├── id: UUID (PK)
├── personnel_id: UUID (FK Personnel, on_delete=CASCADE)
├── nominal_roll_id: UUID (FK NominalRoll, on_delete=CASCADE)
├── date: date
├── status_am / remarks_am: attendance_status enum + text
├── status_pm / remarks_pm: attendance_status enum + text
├── notes_snapshot, unit_snapshot, sub_unit_{1,2,3}_snapshot: text
└── audit: created_at/by, updated_at/by, last_edit_at/by, is_retroactive_edit
```

**Constraints:**
- UNIQUE(personnel_id, date) — one attendance row per person per day
- Attendance writes (upsert / copy-remarks) are refused (400) unless the
  target NR is the one active for attendance
- A Tagging whose NR has any attendance rows cannot be deleted (409) —
  deleting would orphan the recorded history
- **Attendance is always taken against a Nominal Roll** (with its Tagging
  applied) — never against a Grouping. Groupings are a separate feature and
  play no part in attendance access or scoping. The user-facing marking view
  (`/attendance`) defaults to the active NR and shows the tagging-overlaid
  roster — Unit, Sub-unit 1, Sub-unit 2, and Sub-unit 3 columns all display
  effective (overlay) values, and tagged rows are highlighted; with no
  active NR it shows an inactive message instead of the marking table. Write
  access is gated per-NR by `UserSubunitAssignment` on the effective
  `sub_unit_1`.

**"Copy Remarks" semantics (issue #4 Q3):**
- Before 12pm: copy previous day's `remarks_pm` → today's `remarks_am`
- After 12pm: copy today's `remarks_am` → today's `remarks_pm`
- On the NR's first day (no prior-day rows) the AM copy is a no-op

---

## 3. Data Model Specification

### 3.1 Authentication & Access Control

#### 3.1.1 AccessLevel

**Ordered vocabulary of access scopes (e.g., unit, coy, platoon, section).**

```
AccessLevel
├── id: UUID (PK)
├── name: str (unique, e.g., "platoon")
├── level_order: int (higher = broader access; used for column visibility)
├── created_at: datetime
├── created_by: UUID (FK User; null for system)
├── updated_at: datetime
└── updated_by: UUID (FK User; null for system)
```

**Constraints:**
- name must be unique
- level_order must be unique (no duplicate access heights)
- Relabelling (renaming name) auto-migrates all User/column references

#### 3.1.2 User

**Google-authenticated users with role and access scope.**

```
User
├── id: UUID (PK)
├── email: str (unique)
├── name: str
├── status: str ENUM ['pending', 'active', 'suspended', 'unrecognised']
│    ('pending' is a legacy value — unused by application logic; new sign-ins
│     register as 'unrecognised'. Kept in the enum because Postgres cannot
│     drop enum values without a type rebuild.)
├── role: str ENUM ['super_admin', 'admin', 'user']
├── access_level_id: UUID (FK AccessLevel; null for admins)
├── first_sign_in_at: datetime (nullable)
├── last_sign_in_at: datetime (nullable)
├── created_at: datetime
└── updated_at: datetime
```

**Constraints:**
- email is unique
- Super-admin cannot be revoked via UI; bootstrapped via env var
- Non-admin users must have access_level_id set

#### 3.1.3 UserSubunitScope

**Links a user to specific subunit(s) within each grouping.**

```
UserSubunitScope
├── id: UUID (PK)
├── user_id: UUID (FK User, on_delete=CASCADE)
├── grouping_id: UUID (FK Grouping, on_delete=CASCADE)
├── unit: str (nullable; part of scoped path)
├── sub_unit_1: str (nullable)
├── sub_unit_2: str (nullable)
├── sub_unit_3: str (nullable)
├── created_at: datetime
├── created_by: UUID (FK User)
└── updated_at: datetime
```

**Constraints:**
- UNIQUE(user_id, grouping_id, unit, sub_unit_1, sub_unit_2, sub_unit_3)
- NULL values = "include all at that level and below"

### 3.2 Personnel & CSV Ingestion

#### 3.2.1 Personnel

**Individual personnel record, sourced from CSV nominal roll.**

```
Personnel
├── id: UUID (PK) ← Row identity; one row per (nominal roll, person)
├── pers_no: str | null (max 20 chars) ← Cross-roll PERSON identity
│   └── The external personnel number from the CSV `Pers` column. Shared by
│       every row belonging to the same individual, across all nominal rolls.
│       One pers_no is one person globally — no two distinct persons ever share
│       one (guaranteed by the external system that mints the numbers). NULL
│       when the CSV row omitted it; never an empty string. Human-facing identifier.
├── nominal_roll_id: UUID (FK Nominal Roll, on_delete=CASCADE)
├── rank: str
├── category: str ENUM ['Officer', 'WOSE']  ← inferred from rank at ingest; never manually set
│   └── Officer ranks: 2LT LTA CPT MAJ LTC SLTC COL (and ME4+)
│       WOSE ranks: REC PTE LCP CPL CFC 3SG 2SG 1SG SSG MSG 3WO 2WO 1WO MWO (and ME1-ME3)
│       Derived via `parade_state.utils.ranks.category_for_rank()`.
├── full_name: str
├── unit: str
├── sub_unit_1: str
├── sub_unit_2: str
├── sub_unit_3: str
├── extra_fields: JSON (other CSV columns not mapped to canonical names; JSONB in PostgreSQL)
├── status: str ENUM ['active', 'archived']
├── callup_status: str ENUM ['Called Up', 'Deferred', 'Disrupted', 'MR', 'Age Limit', 'Other']  (default: 'Called Up')
├── remarks: text (nullable; per-person remarks, distinct from the roll-level NominalRoll.remarks)
├── created_at: datetime
└── created_by: UUID (FK User; typically system)
```

**Constraints:**
- UNIQUE(nominal_roll_id, pers_no): at most one row per person per nominal roll
- `pers_no` is globally unique per *person*: all rows for the same individual share one
  `pers_no`; no two distinct persons ever share one (a property of the external numbering
  system, not DB-enforced across rolls).
- NULL `pers_no` (blank CSV `Pers` cell): multiple NULL rows coexist — a NULL never matches
  another NULL in cross-roll flows. Re-issuing pers_nos for such rows is a separate
  data-quality concern.
- Cross-roll person recognition on ingest matches on `pers_no`.
- A duplicate `pers_no` within one CSV violates UNIQUE(nominal_roll_id, pers_no) and fails
  the process request (IntegrityError).
- Notes, overrides, and attendance link to `Personnel.id` (the row PK). Cross-roll continuity
  (tagging transfer, history) follows the person via `pers_no`.

**Callup status & remarks (issue 06):**
- On ingest, `callup_status` is parsed from the CSV `Callup Decision` column:
  exact (case-insensitive) match against the enum passes through; blank →
  `Called Up`; any other non-blank value → `Other` (raw value preserved in
  `extra_fields.callup_decision`).
- `remarks` joins the non-empty CSV `Reason` + first `Remarks` columns with
  `"; "`; NULL when both are empty.
- **Attendance visibility:** the attendance roster/view includes only
  personnel with `callup_status = 'Called Up'`. All other statuses are hidden.
- **Post-hoc changes are non-destructive:** changing a person's status away
  from `Called Up` never deletes or alters existing attendance records — it
  only hides the person from the attendance view, with no distinct rendering
  of hidden rows anywhere.
- Admins (admin + super_admin) can edit `callup_status` and `remarks` inline
  in the NR management table (PATCH `/personnel/{id}`; enum-invalid values
  are rejected with 422).

### 3.3 Deferments

*Not yet shipped: hidden behind the `FEATURE_DEFERMENTS` env-var flag (see
[4.8 Feature Flags](#48-feature-flags-env-var-kill-switches)).*

#### 3.3.1 Deferment

**A personnel's deferral request, linked to a single nominal roll personnel record.**

```
Deferment
├── id: UUID (PK)
├── personnel_id: UUID (FK Personnel, on_delete=CASCADE)
├── rank_name: str (snapshot of "{rank} {full_name}" at creation)
├── sub_unit: str (nullable; snapshot of first non-null of sub_unit_1/2/3 at creation)
├── reason: str ENUM [
│     'Honeymoon', 'Work', 'Full-time studies', 'Other', 'Medical Grounds',
│     'Examination', 'New employment', 'Special employment', 'Compassionate',
│     'Childbirth', 'Part-time studies', 'Newly Established Business (Local)'
│   ]
├── status: str ENUM [
│     'Approved', 'Withdrawn', 'Rejected', 'To Resubmit', 'Time off arrangement',
│     'Pending action', 'Not called up', 'Do not call up'
│   ]  (default: 'Pending action')
├── remarks: text (nullable; long-text admin remarks)
├── oc_updates: text (nullable; long-text OC updates, overwrite-on-edit)
├── created_at: datetime
├── created_by: UUID (FK User)
├── updated_at: datetime (nullable)
└── updated_by: UUID (FK User; nullable)
```

**Constraints:**
- Linked to exactly one Personnel record (and implicitly that personnel's nominal roll).
- `rank_name` and `sub_unit` are **snapshotted at creation** — denormalized so the
  deferment remains an accurate record even if the personnel row is later edited
  or the nominal roll is superseded by a new CAA.
- Visible to **super_admin only** (admin role gets 403). UI and user-type
  scoping to be tightened in a later phase.
- See §4.6 for the callup_status transition rule driven by `status` changes.

### 3.4 Tagging Overlay (1:1 with Nominal Roll)

#### 3.4.1 Tagging

**The single overlay of person → subunit remappings on a Nominal Roll — strictly 1:1.**

CSV-sourced NRs are read-only: all unit/subunit edits land on the NR's
Tagging as TaggingEntry rows. A Tagging is auto-created (empty) on NR
ingestion; downstream views overlay `to_*` values on top of the canonical
personnel rows.

```
Tagging
├── id: UUID (PK)
├── label: str (nullable; informational — the NR identity is the natural key)
├── nominal_roll_id: UUID (FK NominalRoll, on_delete=CASCADE; UNIQUE — 1:1)
├── remarks: text (nullable)
├── created_at: datetime
├── created_by: UUID (FK User)
├── updated_at: datetime (nullable)
└── updated_by: UUID (FK User; nullable)
```

**Constraints:**
- UNIQUE(nominal_roll_id) — exactly one tagging per NR (server-enforced).
- `label` is optional and no longer globally unique.
- Deleting the NR cascades to its tagging.
- Visible to **super_admin only** (admin role gets 403).
- **Overlay semantics:** creating, editing, or deleting a Tagging must not
  mutate the underlying NR's personnel or their canonical subunit. Downstream
  consumers (attendance / groupings / the NR browser) read the remapped
  structure from the tagging without modifying the NR.

#### 3.4.2 TaggingEntry

**A single person → subunit remap within a Tagging.**

```
TaggingEntry
├── id: UUID (PK)
├── tagging_id: UUID (FK Tagging, on_delete=CASCADE)
├── personnel_id: UUID (FK Personnel, on_delete=CASCADE)
├── from_unit: str (nullable; snapshot of personnel.unit at entry creation)
├── from_sub_unit_1: str (nullable; snapshot)
├── from_sub_unit_2: str (nullable; snapshot)
├── from_sub_unit_3: str (nullable; snapshot)
├── to_unit: str (the remap target; required)
├── to_sub_unit_1: str (nullable)
├── to_sub_unit_2: str (nullable)
├── to_sub_unit_3: str (nullable)
└── created_at: datetime
```

**Constraints:**
- UNIQUE(tagging_id, personnel_id) — one remap per person per tagging.
- `personnel_id` must belong to the parent Tagging's `nominal_roll_id`
  (server-enforced — 400 on cross-NR contamination).
- `from_*` is optional. When omitted at create/edit time, the server
  snapshots the linked personnel's canonical subunit (mirrors Deferment's
  `rank_name`/`sub_unit` snapshot pattern).
- On merge/import, `from_*` is re-snapshotted from the **target NR** personnel
  (the source NR's subunit layout may differ).

#### 3.4.3 Merge Semantics (clone endpoint)

`POST /api/v1/taggings/{id}/clone` merges the source tagging's entries into
the target NR's **existing** tagging (under 1:1 the target always has one):

- For each source TaggingEntry, look up the target-NR Personnel row by
  `Personnel.pers_no` (the cross-roll person identifier — `Personnel.id` is
  per-roll and will not match across NRs).
- Matched: a new TaggingEntry is appended to the target tagging pointing at
  the target-NR personnel row, preserving the source's `to_*` and
  re-snapshotting `from_*` from the target personnel.
- Personnel already on the target tagging are **skipped** (no clobber).
- Unmatched (source `pers_no` not present on target NR — includes NULL
  `pers_no`, which never matches): skipped and surfaced in the response as
  `{pers_no, name}`.
- Target NR must differ from the source NR (400 otherwise).

#### 3.4.4 Read-Only NR + PATCH Redirect

The NR personnel row is immutable for unit/subunit fields:

- `PATCH /api/v1/personnel/{id}` with `unit`/`sub_unit_1/2/3` upserts a
  TaggingEntry on the personnel's NR tagging (existing entry values are
  merged — unmentioned fields preserved). The personnel row is not mutated.
- `PATCH` with identity fields (`rank`, `name`) → 409 (NR is read-only).
- `PATCH` with `status` alone → still mutates the personnel row.
- The response returns **effective** values (`to_*` if tagged, else canonical).
- The public NR browser (`/nominal-roll`) shows effective values with a
  yellow row background (`.changed-row`) for tagged personnel.

#### 3.4.5 Staged Cell Edits (super-admin NR browser)

Cell edits in the NR browser are **not** saved instantly — they are staged
client-side and applied in a batch, so a misclick costs nothing:

- Editing a cell stages the value locally (no API call): the cell shows the
  staged text on a darker-yellow background (`.cell-edit.pending`,
  `#fcd34d`) — visually distinct from the amber-100 saved-changes row.
- A floating bar (fixed bottom-center, `.pending-bar`) shows
  `N unapplied changes · M personnel` with **Apply** and **Discard**
  (Discard confirms first). Editing a cell back to its server value
  unstages it.
- **Apply** sends one `PATCH /api/v1/personnel/{id}` per person carrying all
  their staged fields (merged server-side into a single TaggingEntry per
  person). Each person-level success is removed from the draft before the
  next request, so failures stay staged and are reported in an alert.
- **Persistence:** the draft lives in `localStorage` under
  `ps:nr-edits:{nominal_roll_id}` (per roll, per browser) and survives page
  refreshes and roll switching. On load, staged fields that now match the
  server's effective value (applied elsewhere) are silently dropped;
  personnel filtered out of the current view keep their staged edits.
  Known limitation: concurrent tabs are last-writer-wins.
- Non-super-admins get no editor and no staging machinery at all.

### 3.5 Attendance Tracking

#### 3.5.1 Attendance (NR/Tagging-scoped, AM/PM)

**Per-personnel per-day attendance with hardcoded AM and PM slots.**

```
Attendance
├── id: UUID (PK)
├── personnel_id: UUID (FK Personnel, on_delete=CASCADE)
├── nominal_roll_id: UUID (FK NominalRoll, on_delete=CASCADE)
├── date: date
├── status_am / remarks_am: attendance_status enum + text (default 'absent')
├── status_pm / remarks_pm: attendance_status enum + text (default 'absent')
├── notes_snapshot: str (snapshot of grouping notes at row creation)
├── unit_snapshot, sub_unit_{1,2,3}_snapshot: str (personnel's effective hierarchy)
├── created_at/by, updated_at/by, last_edit_at/by, is_retroactive_edit: audit
```

**Status enum** (`attendance_status`): `present`, `absent`, `time_off`, `mc`,
`yet_to_inpro`, `outpro`, `reporting_sick`, `late`, `att_out`.
`present` and `late` count as "present-like" when aggregating.

**Constraints:**
- UNIQUE(personnel_id, date) — one row per person per day
- Writes are refused (400) unless the NR is the one active for attendance
- AM/PM slots are counted independently toward attendance-rate totals

#### 3.5.2 Unit Strength Report

**The parade state aggregated into the strength reporting format** — page
at `/admin` (it replaced the old admin dashboard), gated by
`FEATURE_STRENGTH`.

Rows group by **effective sub_unit_1** (tagging-overlay-aware; shown once
per section) and **effective sub_unit_2**, with a SUBTOTAL per sub_unit_1
and a unit-wide TOTAL. `unit` and `sub_unit_3` are ignored — attached
personnel from other units report with the unit; personnel without
subunits fall into a `(none)` bucket. Columns: **Officer / WOSE / Total**
(`Personnel.category`), each **In / Out / Current / %**:

- **In** — personnel on the NR active for attendance with
  `callup_status = Called Up` (active personnel rows only)
- **Current** — slot status `present` or `late` (present-like)
- **Out** — every other status; unmarked personnel count as `absent`
- **%** — `Current ÷ In`, whole percent; 0% when In is 0

The date and AM/PM slot are URL params (server default: today, AM); a
first-visit script re-defaults them from the browser's local datetime.
Super-admins see the whole unit; regular admins see only their assigned
sub_unit_1 sections (deny-by-default, the same `UserSubunitAssignment`
machinery as attendance marking), with TOTAL summing the visible rows and
a guidance message for admins with no assignments.

---

## 4. Business Rules & Constraints

### 4.1 Attendance Snapshot Rule

**Condition 1: Within grouping.valid_from to valid_until**
- On write, resolve effective unit+subunit: override ?? nominal roll
- Populate: unit_snapshot, sub_unit_1_snapshot, sub_unit_2_snapshot, sub_unit_3_snapshot
- Populate: notes_snapshot from current GroupingNotes
- Update: last_edit_at, last_edit_by (for display purposes)

**Condition 2: Outside validity range (retroactive edit)**
- Update: status, remarks, notes_snapshot only
- DO NOT update: any *_snapshot fields (preserve original snapshot)
- Update: last_edit_at, last_edit_by (for display purposes)

### 4.2 Grouping Lifecycle

```
Grouping created (draft)
  ├─ valid_from, valid_until, optional scheduled_activation set
  ├─ Admin can edit overrides
  ├─ PersonnelOverrides populated (initially mirrored from Nominal Roll)
  └─ Session creation BLOCKED — see §4.3

              At valid_from time (or scheduled_activation, or manual):
              ↓
        status → active
        ├─ Only one grouping active at a time (application-enforced)
        ├─ Sessions can be created/opened
        └─ Admin can still edit overrides (live reorg)

              At valid_until time:
              ↓
        status → inactive (auto)
        ├─ No new attendance activation
        └─ Admin can manually transition → archived or closed or finalized

        [Manual admin actions at any status:]
        ├─ archived: retain for history, hide from active lists
        ├─ closed: no further edits allowed (grouping locked)
        └─ finalized: permanent archive (immutable)
```

### 4.3 Attendance Activation & Editability

> **Removed in issue #4:** the user-managed Session model (open/closed/finalized).
> AM and PM are now hardcoded. The `/api/v1/sessions/*` routes return 410 Gone.
>
> **Removed in the active-NR model:** the per-NR `AttendanceScope` table and
> the NR confirm/unconfirm workflow. Exactly one NR is active for attendance
> at a time; attendance is taken against it with its 1:1 tagging applied.

**Activation gate** — Attendance writes are refused (HTTP 400) unless the
target NR is the one currently active for attendance. A super-admin toggles
this in the /nominal-roll view's Roll management panel (or via
`POST /api/v1/nominal-rolls/{id}/activate-attendance` /
`deactivate-attendance`); activating another NR auto-switches activity.
Deactivating leaves attendance inactive — the user-facing `/attendance` view
then shows an inactive message instead of the marking table.

**Attendance editability** — With the NR active, attendance rows can
be created and updated (upsert semantics on `(personnel_id, date)`).
Retroactive edits (target date in the past) set `is_retroactive_edit = true`.

**Subunit-1 access (issue #4 PR 2)** — Attendance writes are gated per NR by
the caller's `UserSubunitAssignment` rows. A user may only upsert attendance
for personnel whose **effective** `sub_unit_1` matches one of their
assignments on that NR. The effective `sub_unit_1` is the NR's 1:1 Tagging
overlay's `to_sub_unit_1` when an entry exists for that person (taggings are
"remappings already applied"), falling back to the personnel's canonical
`sub_unit_1`. `super_admin` bypasses entirely. **Deny-by-default**: a user
with no assignments on an NR has no attendance-write access there (HTTP 403,
listing the offending sub_unit_1s). `copy-remarks` only affects personnel in
assigned subunits and 403s if the caller has no assignments on the NR.
Assignments are managed by super-admin via
`/api/v1/access-control/{nominal-rolls/{nr_id}/..., users/{user_id}/...}/subunit-assignments`.

### 4.4 Column Mapping Constraint

**Global Constraint:** Each canonical column name maps from at most ONE raw CSV column name

```
CSV1.raw_columns    ColumnMapping              App.canonical
─────────────────   ────────────────────────   ──────────────
"Name" ────────────→ confirmed mapping ────→ full_name
"Rank" ────────────→ confirmed mapping ────→ rank
"Unit" ────────────→ confirmed mapping ────→ unit
(unmapped columns: stored in extra_fields JSON)

"PersonalNumber" ──→ mapped to the canonical `pers_no` column (the external
                     personnel number; the cross-roll person identity, see §3.2.1).
                     Blank cells are stored as NULL, never empty strings.

CSV2 (later upload)
"Employee No" ────→ auto-detected mapping ─→ (conflicts with full_name ← "Name")
                     [admin confirms/rejects]

Result: each canonical name receives from at most ONE raw column per CSV,
        but different CSVs can use different raw names for the same canonical.
```

**Person identity is sourced from the CSV.** The canonical `pers_no` column carries the
external personnel number; the cross-roll person key is `pers_no` (see §3.2.1).

### 4.5 CSV Upload Pipeline

```
Upload File
  ↓
[CsvUpload.status = 'received']
  ↓
Auto-match headers against ColumnMapping
  ↓
[User resolves unmapped required columns & conflicts]
  ↓
[CsvUpload.status = 'mapping_confirmed']
  ↓
Check CAA uniqueness
  ├─ CAA new → proceed
  └─ CAA exists (existing Nominal Roll) → prompt admin for replacement
      ├─ Admin rejects → stop
      └─ Admin confirms replacement
          → Archive prior Nominal Roll+related entities
          → Proceed with new Nominal Roll
  ↓
Compute diff (current CSV vs prior CSV)
  ↓
[Admin reviews & confirms diff]
  ↓
[CsvUpload.status = 'diff_confirmed']
  ↓
[Admin processes the upload into a Nominal Roll ("Process into Nominal Roll"
 in the admin CSV upload view)]
  ↓
Create NominalRoll (CAA parsed from the filename, e.g. caaYYMMDD; no status
workflow — every NR is equal)
Populate Personnel records (callup_status from the CSV 'Callup Decision'
column — blank → 'Called Up', unrecognised → 'Other'; remarks from
'Reason' + first 'Remarks' joined with '; ')
Persist ColumnMetadata for the source columns
Auto-create the NR's empty 1:1 Tagging
Optionally import taggings from another NR (chosen by the admin; entries
copied across by `pers_no` match, no-clobber)
```

### 4.6 Deferment Callup-Status Transition

When a Deferment's `status` changes, the linked Personnel's `callup_status`
follows this rule:

| Deferment new status                                 | Personnel.callup_status                |
|------------------------------------------------------|----------------------------------------|
| `Approved`                                           | → `Deferred`                           |
| Any non-Approved status, **previous** was Approved   | → `Called Up` (revert)                 |
| `Not called up` / `Do not call up` (any prior)       | **No change** (neutral; later phase)   |
| Other transitions (no Approved involvement)          | **No change**                          |
| Deferment deleted, previous status was Approved      | → `Called Up` (revert)                 |
| Deferment deleted, previous status was not Approved  | **No change**                          |

`Pending action` (the initial status) is never Approved, so creating a new
deferment does not affect `callup_status`. `Not called up` and `Do not call up`
belong to a later workflow phase and are explicitly excluded from driving
callup transitions.

### 4.7 Data Purge (Testing-Only)

Super-admin action (Admin → Settings) that deletes **every Nominal Roll and
all downstream data** in one transaction: personnel, attendance,
deferments, taggings, groupings (with their overrides, exclusions, notes,
and access grants), column metadata, CSV uploads, and NR-bound subunit
assignments.

**Preserved:** users, access levels, sessions, global column mappings, and
the audit log. The purge itself is audit-logged (`entity_type=database`,
`action=delete`) with per-table deleted-row counts.

**Guards:**

- Super-admin only (403 otherwise)
- Type-to-confirm: the request must carry `confirmation=PURGE`
- Gated by `PURGE_ENABLED` (default: off in production, on elsewhere)

**Purpose:** easy re-testing of CSV upload from a clean slate. Deleting
`csv_uploads` too is deliberate — its unique `sha256_hash` would otherwise
reject re-uploading the same test file. The feature is testing-only and
may be disabled (`PURGE_ENABLED=false`) or removed entirely before
production use.

### 4.8 Feature Flags (Env-Var Kill Switches)

Env-var booleans (`FEATURE_<NAME>`, default off) hide not-yet-ready
features from a deployment **entirely**:

- **Nav/templates:** the feature's sidebar entry and every other entry
  point (e.g. the NR-browser *Create Grouping* button) are not rendered.
- **Routes:** page and API routes return 404 for **every role, including
  super admins** — the gate (`parade_state.features.require_feature`)
  sits above role checks, so direct URLs are unreachable. Pages answer
  with a styled HTML 404 ("switched off on this deployment"); APIs keep
  a JSON 404 naming the env var.

Current flags: `FEATURE_DEFERMENTS` (Deferments page + API),
`FEATURE_GROUPING` (Grouping pages + API), and `FEATURE_STRENGTH` (Unit
Strength report at `/admin`). Development enables them via Railway env
vars; production leaves them unset until each feature ships.
Toggling is an env-var change plus service restart — no deploy. Railway's
managed feature-flag offering was rejected (paid early access with
breaking-change risk, TypeScript-only SDK, rollout targeting this
coarse on/off switch does not need).

### 4.9 Key Constraints Summary

| Table | Unique | Index | Purpose |
|-------|--------|-------|---------|
| User | (email) | (email) | Login |
| User | - | (access_level_id) | Access lookup |
| AccessLevel | (name), (level_order) | - | Vocab uniqueness |
| Nominal Roll | (caa) | (caa) | CAA uniqueness; application-level: only one `attendance_active` |
| ColumnMapping | (canonical_name) among non-deprecated | (canonical_name) | Mapping uniqueness |
| Grouping | Application-level: only one active | (status) | Active grouping enforcement |
| UserSubunitScope | (user_id, grouping_id, unit, sub_unit_1-3) | (user_id, grouping_id) | Scope lookup |
| Attendance | (personnel_id, date) | (personnel_id, date) | One row per person per day |
| UserSubunitAssignment | (user_id, nominal_roll_id, sub_unit_1) | (user_id, nominal_roll_id, sub_unit_1) | One grant per user/NR/subunit |
| Personnel | — | (callup_status) | Callup status filter |
| Deferment | — | (personnel_id), (status), (updated_at) | Deferment lookup |

---

## 5. Access Control & Security

### 5.1 Users and Roles

**Super-Admin:**
- Bootstrapped via SUPER_ADMIN_EMAIL env var
- Auto-granted on first Google sign-in
- Cannot be revoked via UI

**App Admin:**
- Granted by super-admin
- Full read/write access to all entities, all columns, all groupings
- Access to audit log
- All structural operations are admin-only

**Scoped User:**
- Deferred (planned viewer role — see future issues). Not currently usable: non-admin sign-ins get the no-access page and viewer-facing routes are gated on admin role.
- Google-authenticated
- Has access level: single admin-assigned label from ordered vocabulary
- Has subunit scope: one or more (grouping, subunit) pairs
- Write scope: attendance status, Notes, Remarks — for rows within scope only

### 5.2 Account Lifecycle

The system is admin-only: only `super_admin` and `admin` accounts can sign in and use it. The non-admin viewer role is deferred to a future issue.

1. On first Google sign-in, an unknown email is auto-registered as `unrecognised` with role `user`; the visitor sees a "no access" page and receives no session. The SUPER_ADMIN_EMAIL bootstrap account is created `active`/`super_admin` instead.
2. A super-admin promotes `unrecognised` users to `admin` (and `active`) via `/admin/users`; the user can then sign in normally. Promotion to `super_admin`/`admin` automatically sets `status=active` for `unrecognised` (or legacy `pending`) accounts; explicitly suspended accounts stay suspended.
3. A super-admin may also pre-provision an account via the Add User form on `/admin/users` (`POST /api/v1/users`): the row is created `active` with the chosen role (email lowercased to match the Google sign-in), so the person's first sign-in works immediately without the unrecognised holding state.
4. Admin may suspend at any time (403 at sign-in). Suspension immediately invalidates active sessions.

### 5.3 Row Visibility Rules

**User sees personnel row if:**
- User has GroupingUserAccess for grouping, AND
- Personnel's (unit, sub_unit_1, sub_unit_2, sub_unit_3) matches at least one UserSubunitScope for that grouping, AND
- User.access_level_id.level_order ≥ ColumnMetadata.sensitivity_level_id.level_order (for each visible column)

*(Admins bypass all checks)*

### 5.4 Column Visibility Rules

**Column visible in UI if:**
- ColumnMetadata.sensitivity_level_id = null → admin-only
- ColumnMetadata.sensitivity_level_id != null → user.access_level_id.level_order ≥ sensitivity_level_id.level_order

### 5.5 Access Level Vocabulary

Admin-defined ordered string labels (e.g. unit, coy, platoon, section). Linear hierarchy (total ordering; higher level_order integer = broader access). Used for both row visibility and column sensitivity. Relabelling auto-migrates all references.

**Access level stability:** A user's access level is determined at login and remains stable for the duration of the session. Changes to a user's access level require re-login to take effect.

---

## 6. API & Integration Patterns

### 6.1 API Design Principles

**Column manifest pattern:** All data endpoints return columns (user-visible column manifest) + rows (objects containing only manifest keys). Clients render headers from manifest; never hardcode column names.

**SSE stale detection:** GET /api/v1/events/attendance?groupingId=&sessionId= emits data_changed signal events (no payload data) when any record in the user's scope is modified. Client fetches on user confirmation. 30s keep-alive ping.

**Auth:** session cookie (HttpOnly, Secure, SameSite=Strict). Google OAuth via Authlib.

### 6.2 Required Columns (App Config)

Declared in app.config.json (deployment-time change, not admin UI):

| Canonical name | Purpose |
|---|---|
| unit | Top-level unit identifier |
| sub_unit_1 | Subunit level 1 |
| sub_unit_2 | Subunit level 2 |
| sub_unit_3 | Subunit level 3 |
| rank | Display; used to infer `category` |
| full_name | Display |
| pers_no | The external personnel number; the cross-roll person identity |

**Note:** `pers_no` is imported from the CSV `Pers` column and is the canonical personnel
identity (see §3.2.1). Blank cells store NULL.

### 6.3 Grouping Operations

**Clone (Same-Nominal Roll):**
- Admin-only
- Copies overrides, prefixes name "Copy of …", resets validity range to blank
- Admin chooses whether to transfer grouping notes

**Migrate (Cross-Nominal Roll):**
- Admin-only
- Two-step: compute diff between source nominal roll and target nominal roll
- Present leavers (must be individually dismissed) and joiners (must each receive a unit+subunit assignment)
- On confirm, create new draft grouping against target nominal roll

---

## 7. Technical Decisions

### 7.1 Database Architecture

**Production:** PostgreSQL with native UUID support and JSONB types  
**Testing:** SQLite (in-memory) with async support via aiosqlite

**Rationale:**
- SQLite provides fast, isolated test execution
- PostgreSQL offers production-grade features (partial indexes, JSONB, native UUIDs)
- Application code abstracts database differences through SQLAlchemy

### 7.2 UUID Storage Strategy

**Decision:** Store UUIDs as String(36) instead of native UUID types

**Implementation:**
```python
# Base class provides String-based UUID storage
class Base(DeclarativeBase):
    id: Mapped[uuid.UUID] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True,
    )
```

**Rationale:**
- SQLite doesn't support native UUID types
- String storage provides cross-database compatibility
- Application layer still uses Python uuid.UUID type for type safety
- PostgreSQL can still use UUID functions when needed via migrations

### 7.3 JSON Field Handling

**Personnel.extra_fields:** Use SQLAlchemy JSON type instead of Text

**Implementation:**
```python
extra_fields: Mapped[dict] = mapped_column(JSON, default=dict)
```

**Rationale:**
- JSON type provides automatic serialization/deserialization
- Works with both SQLite (JSON as text) and PostgreSQL (JSONB)
- Allows Python dict manipulation without manual JSON encoding

### 7.4 Constraint Enforcement Strategy

**Decision:** Enforce certain business rules at application level rather than database level

**Active Grouping Constraint:**
- Original Spec: Only one grouping can have status = 'active' (database constraint)
- Implementation: Application-level validation only

**Rationale:**
- SQLite doesn't support partial unique indexes (e.g., WHERE status = 'active')
- PostgreSQL supports this, but maintaining divergent constraints increases complexity
- Application layer can provide better error messages and validation logic
- Allows multiple "draft" or "inactive" groupings without constraint violations

### 7.5 Test Architecture

**Test Isolation Strategy:** Fresh database for each test (function-scoped fixtures)

**Benefits:**
- Complete isolation: No state leakage between tests
- Reproducible results: Tests can run in any order
- Easy debugging: Failures are self-contained
- Parallel execution ready: Safe to run tests in parallel

**Test Results:**
- 26/26 tests passing (100% pass rate)
- 93.77% code coverage (exceeds 80% requirement)

### 7.6 Static Analysis Tooling

**Switch from mypy to ruff:** Use ruff for both linting and type checking

**Rationale:**
- Performance: ruff is 10-100x faster than mypy
- Unified tooling: Single tool for linting, formatting, and type checking
- Active development: ruff has rapid development and Python 3.12+ support
- Compatibility: Works well with SQLAlchemy async patterns

### 7.7 Tech Stack Summary

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.12+ | |
| API framework | FastAPI | Async; OpenAPI generation; SSE via StreamingResponse |
| Admin UI | NiceGUI | Mounted on FastAPI app at /admin; Quasar components |
| Mobile UI (MVP) | Static HTML + vanilla JS | Served by FastAPI; no build step |
| ORM | SQLAlchemy 2.x async | asyncpg driver; shared pool across FastAPI and NiceGUI |
| Auth | Authlib | Google OAuth 2.0; session middleware |
| Background jobs | APScheduler AsyncIOScheduler | SQLAlchemy job store (Postgres) for multi-instance safety |
| Database | PostgreSQL 15+ | Production database |
| Testing Database | SQLite (in-memory) | Complete test isolation |
| Package management | uv | Fast resolver; pyproject.toml |
| Static Analysis | ruff | Linting, formatting, and type checking |

---

*End of Technical Specification v1.0*
