# Data Model Specification

**Version:** 0.1  
**Status:** For review  
**Date:** 2026-05-07  

---

## Overview

This document defines the SQLAlchemy ORM models for Parade State Management System. Models are organized into logical layers: auth, CSV ingestion, personnel, deployments, sessions, and audit.

**Key design decisions:**
- `personnel_id` (UUID) is internal system identity; `pers_no` is external reference (read-only)
- Deployments cascade status changes (closed/finalized) to all child sessions
- Notes belong to deployments; shared across all sessions within that deployment
- Attendance snapshots capture unit/subunit state at time of write
- Column mapping enforces one-to-one (canonical ← raw) constraint

---

## 1. Authentication & Access Control

### 1.1 AccessLevel

Ordered vocabulary of access scopes (e.g., `unit`, `coy`, `platoon`, `section`).

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
- `name` must be unique
- `level_order` must be unique (no duplicate access heights)
- Relabelling (renaming `name`) auto-migrates all User/column references

---

### 1.2 User

Google-authenticated users with role and access scope.

```
User
├── id: UUID (PK)
├── email: str (unique)
├── name: str
├── status: str ENUM ['pending', 'active', 'suspended', 'unrecognised']
│   └── pending: preregistered, awaiting first sign-in
│   └── active: signed in at least once
│   └── suspended: access revoked by admin
│   └── unrecognised: attempted sign-in with no preregistration
├── role: str ENUM ['super_admin', 'admin', 'user']
├── access_level_id: UUID (FK AccessLevel; null for admins)
│   └── Determines row visibility (which personnel) & column visibility
│   └── Stable for duration of session; changes require re-login
├── first_sign_in_at: datetime (nullable)
├── last_sign_in_at: datetime (nullable)
├── created_at: datetime
└── updated_at: datetime
```

**Constraints:**
- `email` is unique
- Super-admin cannot be revoked via UI; bootstrapped via env var
- Non-admin users must have `access_level_id` set

---

### 1.3 UserSubunitScope

Links a user to specific subunit(s) within each deployment. Allows multi-deployment access with different scopes.

```
UserSubunitScope
├── id: UUID (PK)
├── user_id: UUID (FK User, on_delete=CASCADE)
├── deployment_id: UUID (FK Deployment, on_delete=CASCADE)
├── unit: str (nullable; part of scoped path)
├── sub_unit_1: str (nullable)
├── sub_unit_2: str (nullable)
├── sub_unit_3: str (nullable)
│   └── Together: scoped hierarchy path
│   └── NULL values = "include all at that level and below"
├── created_at: datetime
├── created_by: UUID (FK User)
└── updated_at: datetime
```

**Constraints:**
- UNIQUE(user_id, deployment_id, unit, sub_unit_1, sub_unit_2, sub_unit_3) to prevent duplicate scopes
- Deletion of a user cascades to all scopes

**Notes:**
- E.g., user scoped to (deployment_X, "Coy 1", null, null, null) sees all personnel under Coy 1
- E.g., user scoped to (deployment_X, "Coy 1", "Platoon 2", null, null) sees only Platoon 2 of Coy 1

---

### 1.4 DeploymentUserAccess

Grants a user access to a specific deployment.

```
DeploymentUserAccess
├── id: UUID (PK)
├── user_id: UUID (FK User, on_delete=CASCADE)
├── deployment_id: UUID (FK Deployment, on_delete=CASCADE)
├── granted_by: UUID (FK User; admin who granted)
├── granted_at: datetime
└── revoked_at: datetime (nullable; soft delete for audit trail)
```

**Constraints:**
- UNIQUE(user_id, deployment_id)
- When deployment is created: auto-insert DeploymentUserAccess for all active, non-suspended users

---

## 2. CSV Ingestion & Estab

### 2.1 Estab

Base personnel roster, sourced from CSV, pinned by CAA (Complement As At) date.

```
Estab
├── id: UUID (PK)
├── caа: date (Complement As At; must be unique among confirmed estalbs)
├── csv_hash: str (SHA-256 of raw CSV)
├── status: str ENUM ['draft', 'confirmed', 'archived']
│   └── draft: awaiting diff confirmation
│   └── confirmed: personnel_snapshots populated; ready for deployment
│   └── archived: superseded by newer CAA or marked obsolete
├── personnel_count: int (snapshot at confirmation)
├── uploaded_at: datetime
├── uploaded_by: UUID (FK User)
├── confirmed_at: datetime (nullable)
├── confirmed_by: UUID (FK User, nullable)
├── created_at: datetime
└── notes: str (nullable; admin notes on this estab)
```

**Constraints:**
- UNIQUE(caа) among non-archived estalbs
- `csv_hash` helps detect re-uploads of identical data
- Status transition: draft → confirmed → archived (one-way)

---

### 2.2 CsvUpload

Raw CSV file storage (immutable, append-only).

```
CsvUpload
├── id: UUID (PK)
├── estab_id: UUID (FK Estab, nullable; set after confirmation)
├── raw_content: bytes (raw CSV bytes or compressed text)
├── sha256_hash: str
├── line_count: int (number of rows including header)
├── uploaded_at: datetime
├── uploaded_by: UUID (FK User)
├── mapping_confirmed_at: datetime (nullable)
├── diff_confirmed_at: datetime (nullable)
├── created_at: datetime
└── status: str ENUM ['received', 'mapping_confirmed', 'diff_confirmed', 'failed']
```

**Constraints:**
- Soft-deleted (never hard-deleted; important for audit trail)
- Used to rollback or re-inspect CSV processing

---

### 2.3 ColumnMapping

Global mapping table: raw CSV column names → canonical app column names.

```
ColumnMapping
├── id: UUID (PK)
├── raw_name: str (header as appeared in CSV; case-preserved)
├── canonical_name: str (app column name; e.g., "pers_no", "full_name")
├── status: str ENUM ['auto_detected', 'admin_confirmed', 'deprecated']
├── created_at: datetime
├── created_by: UUID (FK User; null for system auto-detection)
├── confirmed_at: datetime (nullable; when admin confirmed)
├── confirmed_by: UUID (FK User, nullable)
├── deprecated_at: datetime (nullable; soft-delete marker)
└── notes: str (nullable; why deprecated, etc.)
```

**Constraints:**
- UNIQUE(canonical_name) among non-deprecated entries
  - Ensures each canonical maps from at most one raw CSV name
  - Multiple raw names can exist without a canonical (stored in extra_fields)
- `raw_name` is case-insensitive for matching; stored as-is for reference

**Notes:**
- Entries are soft-deleted (deprecated), never hard-deleted
- Global accumulation across all CSV uploads; guides future upload mapping

---

### 2.4 ColumnMetadata

Per-CSV column metadata: original headers, canonical mapping, inferred types, sensitivity.

```
ColumnMetadata
├── id: UUID (PK)
├── estab_id: UUID (FK Estab, on_delete=CASCADE)
├── csv_upload_id: UUID (FK CsvUpload, on_delete=CASCADE)
├── original_name: str (raw CSV header)
├── canonical_name: str (FK ColumnMapping.canonical_name, nullable)
├── inferred_type: str ENUM ['string', 'integer', 'date', 'boolean', 'json']
├── sensitivity_level_id: UUID (FK AccessLevel, nullable)
│   └── null = admin-only; non-null = user with that access level or higher can see
├── is_required: bool (true if in app.config.json required list)
├── created_at: datetime
└── updated_at: datetime
```

**Constraints:**
- UNIQUE(estab_id, original_name) to prevent duplicate tracking per CSV version
- Canonical names should match ColumnMapping entries where possible

---

## 3. Personnel (from Estab)

### 3.1 Personnel

Individual personnel record, sourced from CSV estab.

```
Personnel
├── id: UUID (PK)  ← Internal system identity (NOT pers_no)
├── estab_id: UUID (FK Estab, on_delete=CASCADE)
├── pers_no: str (external reference ID; unique within estab only)
│   └── Stored here for reference; never used for app logic
├── rank: str
├── full_name: str
├── unit: str
├── sub_unit_1: str
├── sub_unit_2: str
├── sub_unit_3: str
├── extra_fields: JSONB (other CSV columns not mapped to canonical names)
├── status: str ENUM ['active', 'archived']
├── created_at: datetime
└── created_by: UUID (FK User; typically system)
```

**Constraints:**
- UNIQUE(estab_id, pers_no) within an estab version
- `unit` + `sub_unit_*` form the organizational hierarchy (not all may be populated)

**Notes:**
- `pers_no` is external reference; internal `id` is the system identity
- Cross-CSV note linking uses `id` + estab context, NOT `pers_no`
- If a person leaves (not in new CSV), their record in prior estab remains; notes stay with old deployment

---

## 4. Deployments & Overrides

### 4.1 Deployment

Operational deployment based on an estab, with overrides and validity window.

```
Deployment
├── id: UUID (PK)
├── name: str
├── estab_id: UUID (FK Estab, on_delete=RESTRICT)
├── status: str ENUM ['draft', 'active', 'inactive', 'archived', 'closed', 'finalized']
│   └── draft: not yet active
│   └── active: currently operational (only one per system)
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
- Only one deployment can have `status = 'active'` (enforced by DB partial unique index + app layer)
- Validity range overlaps with existing draft/active deployment → hard reject
- Status transitions:
  - draft → active (auto at valid_from or manual + scheduled_activation)
  - active → inactive (auto at valid_until)
  - Any status → archived (manual)
  - Any status → closed (manual; no edits allowed)
  - Any status → finalized (manual; cascades to all sessions; permanent)
- Closure/finalization cascades to all child sessions

---

### 4.2 DeploymentPersonnelOverride

Per-deployment personnel assignment remap (override estab hierarchy).

```
DeploymentPersonnelOverride
├── id: UUID (PK)
├── deployment_id: UUID (FK Deployment, on_delete=CASCADE)
├── personnel_id: UUID (FK Personnel, on_delete=CASCADE)
├── unit: str (override value)
├── sub_unit_1: str (override value)
├── sub_unit_2: str (override value)
├── sub_unit_3: str (override value)
├── created_at: datetime
├── created_by: UUID (FK User)
└── updated_at: datetime
```

**Constraints:**
- UNIQUE(deployment_id, personnel_id) per override
- Editable at any deployment status (draft, active, inactive, archived, closed)
- `closed`/`finalized` deployments cannot be edited (enforced at app layer)
- Allows flexible reorgs without creating new deployments

---

### 4.3 DeploymentNotes

Canonical store for personnel notes, scoped to deployment. Shared across all sessions.

```
DeploymentNotes
├── id: UUID (PK)
├── deployment_id: UUID (FK Deployment, on_delete=CASCADE)
├── personnel_id: UUID (FK Personnel, on_delete=CASCADE)
├── notes: str (text notes)
├── created_at: datetime
├── created_by: UUID (FK User)
├── updated_at: datetime
├── updated_by: UUID (FK User)
└── notes_version: int (incrementing counter for change tracking)
```

**Constraints:**
- UNIQUE(deployment_id, personnel_id)
- Editable via deployment view (writes to canonical only) or attendance session view (writes to canonical + updates `notes_snapshot` on current session)
- Transferred to new deployment by matching personnel ID (not pers_no)
- Archived deployment notes remain in place (not transferred)

---

## 5. Sessions & Attendance

### 5.1 Session

AM or PM attendance window, explicitly created by admin, linked to deployment.

```
Session
├── id: UUID (PK)
├── deployment_id: UUID (FK Deployment, on_delete=CASCADE)
├── date: date
├── session_type: str ENUM ['AM', 'PM']
├── status: str ENUM ['open', 'closed', 'finalized']
│   └── open: attendance can be recorded
│   └── closed: no further edits (cascade from deployment.closed)
│   └── finalized: permanent archive (cascade from deployment.finalized)
├── created_at: datetime
├── created_by: UUID (FK User)
├── opened_at: datetime
├── closed_at: datetime (nullable; when marked closed)
└── closed_by: UUID (FK User, nullable)
```

**Constraints:**
- UNIQUE(deployment_id, date)
  - Prevents duplicate sessions for same day (max one AM+PM per day)
- No retroactive session creation on inactive/closed deployments
- Closure/finalization cascades from parent deployment
- Status changes:
  - open → closed (manual or via deployment closure)
  - open/closed → finalized (manual or via deployment finalization)

---

### 5.2 AttendanceRecord

Per-personnel per-session attendance status, remarks, and snapshots.

```
AttendanceRecord
├── id: UUID (PK)
├── session_id: UUID (FK Session, on_delete=CASCADE)
├── personnel_id: UUID (FK Personnel, on_delete=CASCADE)
├── deployment_id: UUID (FK Deployment, on_delete=CASCADE)
│   └── Denormalized for query efficiency
├── status: str ENUM ['present', 'absent', 'excused', 'unknown']
│   └── (potentially expandable; MVP uses 'present'/'absent')
├── remarks: str (session-scoped; e.g., "on leave", "TDY")
├── notes_snapshot: str (snapshot of deployment_notes at session open)
├── unit_snapshot: str (personnel's effective unit at time of write)
├── sub_unit_1_snapshot: str
├── sub_unit_2_snapshot: str
├── sub_unit_3_snapshot: str
├── created_at: datetime
├── created_by: UUID (FK User; typically system)
├── updated_at: datetime (last *any* change to record)
├── updated_by: UUID (FK User; last editor for any field)
├── last_edit_at: datetime (last edit to status/remarks/notes_snapshot; for display only)
└── last_edit_by: UUID (FK User; editor of status/remarks/notes_snapshot; for display only)
```

**Display vs Audit:** `last_edit_at` and `last_edit_by` are for UI display (e.g., "last edited by Cpl Tan at 14:30"). They do NOT constitute the audit log. Detailed timeline of all edits will be captured separately in AuditLog table (implementation deferred).

**Constraints:**
- UNIQUE(session_id, personnel_id)
- Pre-populated as 'absent' on session creation
- Snapshot rule:
  - **Within validity range:** resolve override ?? estab; populate all four `*_snapshot` fields. Update `last_edit_at` and `last_edit_by` for display purposes.
  - **Outside validity range (retroactive edit):** update only `status`, `remarks`, `notes_snapshot`; leave snapshots untouched. Update `last_edit_at` and `last_edit_by` for display purposes. Detailed audit trail (timeline of all edits with reasons) captured separately in AuditLog (implementation deferred).
- Accessible via two views:
  - Parade state (scoped to user access level + subunit scope)
  - Deployment notes view (show notes only for user's scope)

---

## 6. Audit Log

### 6.1 AuditLog

Sequential append-only log of all system changes.

```
AuditLog
├── id: UUID (PK)
├── timestamp: datetime
├── user_id: UUID (FK User, nullable; null for system)
├── entity_type: str (e.g., 'attendance', 'deployment', 'session', 'user', 'csv_upload')
├── entity_id: UUID (ID of affected entity; may not exist if soft-deleted)
├── action: str ENUM ['create', 'update', 'delete', 'archive', 'close', 'finalize']
├── changes: JSONB (old/new values for significant fields; detailed pattern TBD)
├── description: str (human-readable summary)
└── ip_address: str (nullable; client IP)
```

**Constraints:**
- Never updated after creation (append-only)
- Query pattern TBD (current scope: sequential viewing)
- Eventually captures: attendance writes, deployment/session creation/status changes, user access changes, CSV uploads

**Notes:**
- Unrecognised sign-in attempts are logged but not surfaced to admin UI (deferred feature)
- Concurrent edit race conditions logged (e.g., relabel during in-flight request)

---

## 7. Key Relationships & Cascades

```
User
 ├── [1..n] UserSubunitScope → Deployment
 ├── [1..n] DeploymentUserAccess → Deployment
 └── [1..n] AuditLog (user_id)

AccessLevel
 ├── [1..n] User (access_level_id)
 ├── [1..n] ColumnMetadata (sensitivity_level_id)
 └── [1..n] AuditLog (created_by, updated_by)

Estab
 ├── [1..n] CsvUpload
 ├── [1..n] ColumnMetadata
 ├── [1..n] Personnel
 └── [1..n] Deployment

Deployment
 ├── [1..n] DeploymentPersonnelOverride
 ├── [1..n] DeploymentNotes
 ├── [1..n] Session
 ├── [1..n] DeploymentUserAccess
 └── [1..n] AttendanceRecord (denormalized)

Personnel
 ├── [1..n] DeploymentPersonnelOverride
 ├── [1..n] DeploymentNotes
 └── [1..n] AttendanceRecord

Session
 └── [1..n] AttendanceRecord
```

**On Delete Cascades:**
- User deleted → all UserSubunitScopes, DeploymentUserAccesses soft-deleted
- Deployment deleted → all child sessions, overrides, notes deleted
- Session deleted → all attendance records deleted
- Personnel deleted → all overrides, notes, attendance records deleted

---

## 8. Indexes (Performance)

Key indexes for query efficiency:

| Table | Index | Purpose |
|-------|-------|---------|
| User | (email) | Login lookup |
| Personnel | (estab_id, pers_no) | CSV reconciliation |
| Estab | (caа) | Uniqueness check on upload |
| Deployment | (status) | Find active deployment |
| Deployment | (estab_id, status) | Find deployments for estab |
| UserSubunitScope | (user_id, deployment_id) | User scope lookups |
| DeploymentUserAccess | (user_id, deployment_id) | User access checks |
| Session | (deployment_id, date) | Find session by date |
| AttendanceRecord | (session_id, personnel_id) | Record lookup |
| AttendanceRecord | (deployment_id, personnel_id) | Deployment scope queries |
| ColumnMapping | (canonical_name) | Mapping lookup |
| AuditLog | (entity_type, entity_id) | Entity history |

---

## 9. Open Questions / Future Refinements

1. **notes_snapshot capture:** Should `notes_snapshot` be captured on session open, or on first attendance write?
2. **Attendance status enum:** MVP uses 'present'/'absent'; future may add 'excused', 'medical', etc. Schema allows enum expansion.
3. **Audit log implementation:** Detailed timeline of all attendance edits with action reasons (e.g., "corrected due to...") will be captured in AuditLog table. Design TBD.
4. **Column manifest in API response:** Should column manifest include all candidate canonicals, or just those present in current data?
5. **Concurrent admin operations:** Locking strategy for CSV diff confirmation, deployment activation, user grant changes? (TBD.)

---

*End of Data Model v0.1*
