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

The personnel branch currently manages battalion parade state through a manual mapreduce process — aggregating attendance from subunits by hand. This system replaces that process with a structured, access-controlled, deployment-aware web application suitable for field use.

### 1.2 Core Entity Hierarchy

```
Nominal Roll (CAA-pinned, CSV-sourced, immutable)
 ├── Tagging (named overlay of person → subunit remaps; never mutates the NR)
 └── Attendance Scope (1:1 with NR; the active NR-or-Tagging scope)
      └── Attendance (one row per personnel/day; carries AM and PM status + remarks)

Deployment (remaps personnel unit+subunit; has date+time validity range) —
  retained for overrides/exclusions/notes but is no longer the attendance anchor.
```

**Key Concepts:**
- **Nominal Roll**: Base source of truth, uploaded from CSV, pinned by CAA date, immutable after confirmation
- **Tagging**: A named overlay of person → subunit remaps on a single NR; never mutates the NR; consumed by attendance to render a remapped structure
- **Attendance Scope**: 1:1 with an NR; the active scope (NR itself or a Tagging) that attendance is taken against. A super-admin must activate a scope before attendance can be recorded.
- **Attendance**: One row per `(personnel, date)`, carrying `status_am`/`remarks_am` and `status_pm`/`remarks_pm` (statuses from the nine-value operational enum). AM and PM are hardcoded — there is no longer a user-managed Session model.
- **Deployment**: Based on a nominal roll, remaps personnel assignments, valid for date+time range. Retained for overrides/exclusions/notes but no longer anchors attendance.

### 1.3 Scope

**In Scope (v1):**
- CSV ingestion with CAA versioning, column mapping, diff detection
- Deployment management: create, clone (same-roll), migrate (cross-roll), scheduled activation
- Attendance taking: AM/PM (hardcoded), nine-status operational reporting enum, NR/Tagging-scoped, active-scope gating
- Row access control (access level + subunit scope) and column sensitivity control
- Parade state table view scoped to user access with inline editing
- Admin UI: enums, users, column sensitivity, column mapping, deployment/tagging/attendance management
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
├── caa: date (Complement As At; must be unique among confirmed nominal rolls)
├── csv_hash: str (SHA-256 of raw CSV)
├── status: str ENUM ['draft', 'confirmed', 'archived']
├── personnel_count: int (snapshot at confirmation)
├── uploaded_at: datetime
├── uploaded_by: UUID (FK User)
├── confirmed_at: datetime (nullable)
├── confirmed_by: UUID (FK User, nullable)
├── created_at: datetime
├── notes: str (nullable; admin notes on this nominal roll)
└── label: str (nullable; UNIQUE; human-readable name, max 100 chars)
```

**Constraints:**
- UNIQUE(caa) among non-archived nominal rolls
- UNIQUE(label) across all nominal rolls (NULLs allowed; enforced on non-null values)
- Status transition: draft → confirmed → archived (one-way)
- Raw CSV stored immutably in csv_uploads (append-only; SHA-256 hash recorded)
- Parsed personnel in personnel_snapshots: required columns as typed fields; all others in extra_fields JSON

### 2.2 Deployment

**Operational deployment based on an nominal roll, with overrides and validity window.**

```
Deployment
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
├── valid_from: datetime (when deployment becomes active)
├── valid_until: datetime (when deployment expires)
├── scheduled_activation: datetime (nullable; explicit scheduled time)
├── personnel_count: int (snapshot; non-archived personnel in this deployment)
├── created_at: datetime
├── created_by: UUID (FK User)
├── activated_at: datetime (nullable; when actually transitioned to active)
├── deactivated_at: datetime (nullable; when transitioned away from active)
└── notes: str (nullable; admin notes)
```

**Constraints:**
- Only one deployment can have status = 'active' (enforced at application layer)
- Validity range overlaps with existing draft/active deployment → hard reject

### 2.3 Attendance Scope & Attendance (AM/PM hardcoded)

**AM and PM are hardcoded; there is no longer a user-managed Session model.**
Attendance attaches to a Nominal Roll / Tagging scope (see issue #4).

```
AttendanceScope (1:1 with NR)
├── id: UUID (PK)
├── nominal_roll_id: UUID (FK NominalRoll, UNIQUE — one row per NR)
├── tagging_id: UUID (FK Tagging, nullable; null → the NR itself is the scope)
├── activated_at: datetime
└── activated_by: UUID (FK User)

Attendance (one row per personnel/day)
├── id: UUID (PK)
├── personnel_id: UUID (FK Personnel, on_delete=CASCADE)
├── nominal_roll_id: UUID (FK NominalRoll, on_delete=CASCADE)
├── tagging_id: UUID (FK Tagging, nullable; snapshots the active scope at creation)
├── date: date
├── status_am / remarks_am: attendance_status enum + text
├── status_pm / remarks_pm: attendance_status enum + text
├── notes_snapshot, unit_snapshot, sub_unit_{1,2,3}_snapshot: text
└── audit: created_at/by, updated_at/by, last_edit_at/by, is_retroactive_edit
```

**Constraints:**
- UNIQUE(personnel_id, date) — one attendance row per person per day
- Attendance writes are refused (400) until the NR's scope is activated
- A Tagging linked to any attendance row, or set as an NR's active scope, cannot be deleted (409)

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

**Links a user to specific subunit(s) within each deployment.**

```
UserSubunitScope
├── id: UUID (PK)
├── user_id: UUID (FK User, on_delete=CASCADE)
├── deployment_id: UUID (FK Deployment, on_delete=CASCADE)
├── unit: str (nullable; part of scoped path)
├── sub_unit_1: str (nullable)
├── sub_unit_2: str (nullable)
├── sub_unit_3: str (nullable)
├── created_at: datetime
├── created_by: UUID (FK User)
└── updated_at: datetime
```

**Constraints:**
- UNIQUE(user_id, deployment_id, unit, sub_unit_1, sub_unit_2, sub_unit_3)
- NULL values = "include all at that level and below"

### 3.2 Personnel & CSV Ingestion

#### 3.2.1 Personnel

**Individual personnel record, sourced from CSV nominal roll.**

```
Personnel
├── id: UUID (PK) ← Row identity; one row per (nominal roll, person)
├── short_id: str (8-char base62) ← Cross-roll PERSON identity
│   └── Shared by every row belonging to the same individual, across all nominal rolls.
│       Generated server-side; globally unique per person (application-enforced
│       via match-then-generate with collision retry). Human-facing identifier.
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
├── callup_status: str ENUM ['Called Up', 'Not Called Up', 'Deferred']  (default: 'Called Up')
├── created_at: datetime
└── created_by: UUID (FK User; typically system)
```

**Constraints:**
- UNIQUE(nominal_roll_id, short_id): at most one row per person per nominal roll
- `short_id` is globally unique per *person* (application-enforced). All rows for the same
  individual share one `short_id`; no two distinct persons ever share one.
- Cross-roll person recognition on ingest matches on `full_name` (rank disambiguates duplicate
  names); matches are confirmed by the admin during CSV diff review.
- `pers_no` is **never imported or stored**. It is an opaque, sensitive primary key from an
  external system. If present in an uploaded CSV/XLSX it is silently dropped during parsing —
  it is not mapped to a canonical column and is not written to `extra_fields`.
- Notes, overrides, and attendance link to `Personnel.id` (the row PK). Cross-roll continuity
  (notes transfer, history) follows the person via `short_id`.

### 3.3 Deferments

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

### 3.4 Tagging Overlay

#### 3.4.1 Tagging

**A named overlay of person → subunit remappings on a single nominal roll.**

```
Tagging
├── id: UUID (PK)
├── label: str (globally unique; user-specified)
├── nominal_roll_id: UUID (FK NominalRoll, on_delete=CASCADE)
├── remarks: text (nullable)
├── created_at: datetime
├── created_by: UUID (FK User)
├── updated_at: datetime (nullable)
└── updated_by: UUID (FK User; nullable)
```

**Constraints:**
- `label` is **globally unique** (server-enforced — 409 on duplicate).
- Linked to exactly one NominalRoll; deleting the NR cascades to its taggings.
- Visible to **super_admin only** (admin role gets 403).
- **Overlay semantics:** creating, editing, or deleting a Tagging must not
  mutate the underlying NR's personnel or their canonical subunit. Downstream
  consumers (attendance / groupings, issues #4/#5) read the remapped structure
  from the tagging without modifying the NR.

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
- On clone, `from_*` is re-snapshotted from the **target NR** personnel (the
  source NR's subunit layout may differ).

#### 3.4.3 Clone Semantics

`POST /api/v1/taggings/{id}/clone` clones a tagging to a different NR:

- For each source TaggingEntry, look up the target-NR Personnel row by
  `Personnel.short_id` (the cross-roll person identifier — `Personnel.id` is
  per-roll and will not match across NRs).
- Matched: a new TaggingEntry is created on the target NR pointing at the
  target-NR personnel row, preserving the source's `to_*` and re-snapshotting
  `from_*` from the target personnel.
- Unmatched (source `short_id` not present on target NR): skipped and
  surfaced in the response as `{short_id, name}`.
- Target NR must differ from the source NR (400 otherwise).
- Always creates a new tagging; up to the user to delete the old one.

### 3.5 Attendance Tracking

#### 3.5.1 Attendance (NR/Tagging-scoped, AM/PM)

**Per-personnel per-day attendance with hardcoded AM and PM slots.**

```
Attendance
├── id: UUID (PK)
├── personnel_id: UUID (FK Personnel, on_delete=CASCADE)
├── nominal_roll_id: UUID (FK NominalRoll, on_delete=CASCADE)
├── tagging_id: UUID (FK Tagging, nullable; snapshots the active scope)
├── date: date
├── status_am / remarks_am: attendance_status enum + text (default 'absent')
├── status_pm / remarks_pm: attendance_status enum + text (default 'absent')
├── notes_snapshot: str (snapshot of deployment notes at row creation)
├── unit_snapshot, sub_unit_{1,2,3}_snapshot: str (personnel's effective hierarchy)
├── created_at/by, updated_at/by, last_edit_at/by, is_retroactive_edit: audit
```

**Status enum** (`attendance_status`): `present`, `absent`, `time_off`, `mc`,
`yet_to_inpro`, `outpro`, `reporting_sick`, `late`, `att_out`.
`present` and `late` count as "present-like" when aggregating.

**Constraints:**
- UNIQUE(personnel_id, date) — one row per person per day
- Writes are refused (400) until the NR's `AttendanceScope` is activated
- AM/PM slots are counted independently toward attendance-rate totals

---

## 4. Business Rules & Constraints

### 4.1 Attendance Snapshot Rule

**Condition 1: Within deployment.valid_from to valid_until**
- On write, resolve effective unit+subunit: override ?? nominal roll
- Populate: unit_snapshot, sub_unit_1_snapshot, sub_unit_2_snapshot, sub_unit_3_snapshot
- Populate: notes_snapshot from current DeploymentNotes
- Update: last_edit_at, last_edit_by (for display purposes)

**Condition 2: Outside validity range (retroactive edit)**
- Update: status, remarks, notes_snapshot only
- DO NOT update: any *_snapshot fields (preserve original snapshot)
- Update: last_edit_at, last_edit_by (for display purposes)

### 4.2 Deployment Lifecycle

```
Deployment created (draft)
  ├─ valid_from, valid_until, optional scheduled_activation set
  ├─ Admin can edit overrides
  ├─ PersonnelOverrides populated (initially mirrored from Nominal Roll)
  └─ Session creation BLOCKED — see §4.3

              At valid_from time (or scheduled_activation, or manual):
              ↓
        status → active
        ├─ Only one deployment active at a time (application-enforced)
        ├─ Sessions can be created/opened
        └─ Admin can still edit overrides (live reorg)

              At valid_until time:
              ↓
        status → inactive (auto)
        ├─ No new attendance activation
        └─ Admin can manually transition → archived or closed or finalized

        [Manual admin actions at any status:]
        ├─ archived: retain for history, hide from active lists
        ├─ closed: no further edits allowed (deployment locked)
        └─ finalized: permanent archive (immutable)
```

### 4.3 Attendance Activation & Editability

> **Removed in issue #4:** the user-managed Session model (open/closed/finalized).
> AM and PM are now hardcoded. The `/api/v1/sessions/*` routes return 410 Gone.

**Activation gate** — Attendance writes are refused (HTTP 400) until a
super-admin activates the NR's `AttendanceScope`. The scope is either the NR
itself (`tagging_id = null`) or a Tagging overlay on it. The active scope is
shown at the top of the attendance view. There is exactly one active scope per
NR.

**Attendance editability** — Once the scope is activated, attendance rows can
be created and updated (upsert semantics on `(personnel_id, date)`).
Retroactive edits (target date in the past) set `is_retroactive_edit = true`.

**Subunit-1 access (issue #4 PR 2)** — Attendance writes are gated per NR by
the caller's `UserSubunitAssignment` rows. A user may only upsert attendance
for personnel whose **effective** `sub_unit_1` matches one of their
assignments on that NR. The effective `sub_unit_1` is the active Tagging
overlay's `to_sub_unit_1` when a tagging is the active scope (taggings are
"remappings already in use"), falling back to the personnel's canonical
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

"PersonalNumber" ──→ NEVER MAPPED. pers_no is an opaque external primary key and is
                     dropped during parsing. It is not a canonical column and is never
                     stored (not even in extra_fields).

CSV2 (later upload)
"Employee No" ────→ auto-detected mapping ─→ (conflicts with full_name ← "Name")
                     [admin confirms/rejects]

Result: each canonical name receives from at most ONE raw column per CSV,
        but different CSVs can use different raw names for the same canonical.
```

**Person identity is not sourced from the CSV.** There is no canonical column for personnel
identity. The cross-roll person key (`short_id`) is minted server-side and attached during
ingest via name+rank matching (see §3.2.1).

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
  └─ CAA exists (confirmed Nominal Roll) → prompt admin for replacement
      ├─ Admin rejects → stop
      └─ Admin confirms replacement
          → Archive prior Nominal Roll+related entities
          → Proceed with new Nominal Roll
  ↓
Compute diff (current CSV vs prior confirmed CSV)
  ↓
[Admin reviews & confirms diff]
  ↓
[CsvUpload.status = 'diff_confirmed']
  ↓
Populate NominalRoll.status = 'confirmed'
Populate Personnel records (callup_status defaults to 'Called Up')
Auto-create initial Deployment (status=draft)
Transfer notes from prior deployment (by Personnel.id match)
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

### 4.7 Key Constraints Summary

| Table | Unique | Index | Purpose |
|-------|--------|-------|---------|
| User | (email) | (email) | Login |
| User | - | (access_level_id) | Access lookup |
| AccessLevel | (name), (level_order) | - | Vocab uniqueness |
| Nominal Roll | (caa) among non-archived | (caa) | CAA uniqueness |
| ColumnMapping | (canonical_name) among non-deprecated | (canonical_name) | Mapping uniqueness |
| Deployment | Application-level: only one active | (status) | Active deployment enforcement |
| UserSubunitScope | (user_id, deployment_id, unit, sub_unit_1-3) | (user_id, deployment_id) | Scope lookup |
| AttendanceScope | (nominal_roll_id) | (nominal_roll_id) | One active scope per NR |
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
- Full read/write access to all entities, all columns, all deployments
- Access to audit log
- All structural operations are admin-only

**Scoped User:**
- Google-authenticated
- Has access level: single admin-assigned label from ordered vocabulary
- Has subunit scope: one or more (deployment, subunit) pairs
- Write scope: attendance status, Notes, Remarks — for rows within scope only

### 5.2 Account Lifecycle

1. Admin preregisters account by email with access level, subunit scope, and deployment grants assigned upfront. Account created in pending state.
2. On first Google sign-in, if email matches a pending account → activated. If no match → held as unrecognised (no access); auth event written to audit log.
3. Admin may suspend at any time. Suspension immediately invalidates active sessions.

### 5.3 Row Visibility Rules

**User sees personnel row if:**
- User has DeploymentUserAccess for deployment, AND
- Personnel's (unit, sub_unit_1, sub_unit_2, sub_unit_3) matches at least one UserSubunitScope for that deployment, AND
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

**SSE stale detection:** GET /api/v1/events/attendance?deploymentId=&sessionId= emits data_changed signal events (no payload data) when any record in the user's scope is modified. Client fetches on user confirmation. 30s keep-alive ping.

**Auth:** session cookie (HttpOnly, Secure, SameSite=Strict). Google OAuth via Authlib.

### 6.2 Required Columns (App Config)

Declared in app.config.json (deployment-time change, not admin UI):

| Canonical name | Purpose |
|---|---|
| unit | Top-level unit identifier |
| sub_unit_1 | Subunit level 1 |
| sub_unit_2 | Subunit level 2 |
| sub_unit_3 | Subunit level 3 |
| rank | Display; also used to disambiguate duplicate names during cross-roll matching |
| full_name | Display; primary key for cross-roll person matching |

**Note:** There is **no** `pers_no` canonical column. Personnel identity (`short_id`) is generated
by the application, not imported from the CSV.

### 6.3 Deployment Operations

**Clone (Same-Nominal Roll):**
- Admin-only
- Copies overrides, prefixes name "Copy of …", resets validity range to blank
- Admin chooses whether to transfer deployment notes

**Migrate (Cross-Nominal Roll):**
- Admin-only
- Two-step: compute diff between source nominal roll and target nominal roll
- Present leavers (must be individually dismissed) and joiners (must each receive a unit+subunit assignment)
- On confirm, create new draft deployment against target nominal roll

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

**Active Deployment Constraint:**
- Original Spec: Only one deployment can have status = 'active' (database constraint)
- Implementation: Application-level validation only

**Rationale:**
- SQLite doesn't support partial unique indexes (e.g., WHERE status = 'active')
- PostgreSQL supports this, but maintaining divergent constraints increases complexity
- Application layer can provide better error messages and validation logic
- Allows multiple "draft" or "inactive" deployments without constraint violations

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
