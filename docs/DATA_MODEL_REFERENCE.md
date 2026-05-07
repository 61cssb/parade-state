# Data Model Reference (Quick)

## Entity Summary Table

| Entity | Purpose | Key Fields | Status |
|--------|---------|-----------|--------|
| **AccessLevel** | Access hierarchy | id, name, level_order | Reference vocab |
| **User** | Google auth + role | id, email, role, access_level_id | Accounts |
| **UserSubunitScope** | Multi-deployment scoping | user_id, deployment_id, unit, sub_unit_1-3 | Access control |
| **DeploymentUserAccess** | Deployment grants | user_id, deployment_id, granted_at | Access control |
| **Estab** | CSV roster (immutable) | id, caа, status, personnel_count | CSV source |
| **CsvUpload** | Raw file storage | id, raw_content, sha256_hash, estab_id | Audit trail |
| **ColumnMapping** | CSV → canonical names | raw_name, canonical_name, status | Ingestion config |
| **ColumnMetadata** | Per-CSV column info | estab_id, original_name, canonical_name, sensitivity_level_id | Ingestion metadata |
| **Personnel** | Roster entry | id (UUID), pers_no (external ref), rank, full_name, unit+subunits | Roster data |
| **Deployment** | Operational window | id, estab_id, status, valid_from, valid_until, name | Ops cycle |
| **DeploymentPersonnelOverride** | Remap assignments | deployment_id, personnel_id, unit, sub_unit_1-3 | Org flexibility |
| **DeploymentNotes** | Shared notes per person | deployment_id, personnel_id, notes | Shared context |
| **Session** | AM/PM window | id, deployment_id, date, session_type, status | Attendance ops |
| **AttendanceRecord** | Per-person per-session | session_id, personnel_id, status, remarks, *_snapshot fields | Attendance data |
| **AuditLog** | All writes | timestamp, user_id, entity_type, entity_id, action, changes | Audit trail |

---

## Inheritance / Cascading Closure

```
┌─ Deployment.status transitions
│  draft → active → inactive → archived
│           ↓                       ↓
│       (manual)             (manual or auto)
│           ↓                       ↓
│       closed (final)     finalized (cascades to sessions)
│
└─ Session.status ← cascades from Deployment
   open → closed → finalized (final)
```

**Cascade on Deployment closure:**
- Deployment.status = closed → all Session.status = closed (no further edits)
- Deployment.status = finalized → all Session.status = finalized (permanent archive)

---

## Access Control Rules

### Row Visibility (Parade State)
User sees personnel row if:
- User has DeploymentUserAccess for deployment, AND
- Personnel's (unit, sub_unit_1, sub_unit_2, sub_unit_3) matches at least one UserSubunitScope for that deployment, AND
- User.access_level_id.level_order ≥ ColumnMetadata.sensitivity_level_id.level_order (for each visible column)

(Admins bypass all checks)

### Column Visibility
Column visible in UI if:
- ColumnMetadata.sensitivity_level_id = null → admin-only
- ColumnMetadata.sensitivity_level_id != null → user.access_level_id.level_order ≥ sensitivity_level_id.level_order

---

## Personnel Identity Rules

**Internal ID:** Personnel.id (UUID auto-generated) is the system source of truth
**External Reference:** Personnel.pers_no (CSV-sourced, external ID)

### Cross-CSV Note Transfer
When new Estab (CSV) is confirmed:
1. New Personnel records created from new CSV
2. Each new Personnel.id is distinct from prior Estab's Personnel.id
3. DeploymentNotes linked to Personnel.id (not pers_no)
   - If person leaves: DeploymentNotes remain with old Deployment+old Personnel.id only
   - If person is "new" in new CSV: no automatic note linkage

**Implication:** To preserve notes across CSV versions, would need explicit pers_no→new Personnel.id mapping (future feature).

---

## Attendance Snapshot Rule

**Condition 1: Within deployment.valid_from to valid_until**
- On write, resolve effective unit+subunit: override ?? estab
- Populate: unit_snapshot, sub_unit_1_snapshot, sub_unit_2_snapshot, sub_unit_3_snapshot
- Populate: notes_snapshot from current DeploymentNotes
- Update: last_edit_at, last_edit_by (for display purposes)

**Condition 2: Outside validity range (retroactive edit)**
- Update: status, remarks, notes_snapshot only
- DO NOT update: any *_snapshot fields (preserve original snapshot)
- Update: last_edit_at, last_edit_by (for display purposes)
- **Note:** `last_edit_at` and `last_edit_by` are for UI display only (e.g., "last edited by Cpl Tan"). Detailed audit trail (timeline of all edits with reasons) captured separately in AuditLog (implementation deferred).

---

## CSV Upload Pipeline (State Machine)

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
  └─ CAA exists (confirmed Estab) → prompt admin for replacement
      ├─ Admin rejects → stop
      └─ Admin confirms replacement
          → Archive prior Estab+related entities
          → Proceed with new Estab
  ↓
Compute diff (current CSV vs prior confirmed CSV)
  ↓
[Admin reviews & confirms diff]
  ↓
[CsvUpload.status = 'diff_confirmed']
  ↓
Populate Estab.status = 'confirmed'
Populate Personnel records
Auto-create initial Deployment (status=draft)
Transfer notes from prior deployment (by Personnel.id match)
```

---

## Deployment Lifecycle Timeline

```
Deployment created (draft)
  ├─ valid_from, valid_until, optional scheduled_activation set
  ├─ Admin can edit overrides
  └─ PersonnelOverrides populated (initially mirrored from Estab)

              At valid_from time (or scheduled_activation, or manual):
              ↓
        status → active
        ├─ Only one deployment active at a time
        ├─ Sessions can be created/opened
        └─ Admin can still edit overrides (live reorg)

              At valid_until time:
              ↓
        status → inactive (auto)
        ├─ No new sessions (existing sessions still open)
        └─ Admin can manually transition → archived or closed or finalized

        [Manual admin actions at any status:]
        ├─ archived: retain for history, hide from active lists
        ├─ closed: no further edits allowed (deployment + all sessions locked)
        └─ finalized: permanent archive (all sessions finalized; immutable)
```

---

## Column Mapping Constraint

**Global Constraint:** Each canonical column name maps from at most ONE raw CSV column name

```
CSV1.raw_columns    ColumnMapping              App.canonical
─────────────────   ────────────────────────   ──────────────
"PersonalNumber" ──→ confirmed mapping ────→ pers_no
"Name" ────────────→ confirmed mapping ────→ full_name
"Rank" ────────────→ confirmed mapping ────→ rank
"Unit" ────────────→ confirmed mapping ────→ unit
(unmapped columns: stored in extra_fields JSONB)

CSV2 (later upload)
"Employee No" ────→ auto-detected mapping ─→ (conflicts with pers_no ← "PersonalNumber")
                     [admin confirms/rejects]

Result: canonical pers_no can only receive from ONE of "PersonalNumber" OR "Employee No"
        but different CSVs can have different raw names → same canonical
        (via ColumnMapping evolution)
```

---

## Key Constraints & Indices

| Table | Unique | Index | Purpose |
|-------|--------|-------|---------|
| User | (email) | (email) | Login |
| User | - | (access_level_id) | Access lookup |
| AccessLevel | (name), (level_order) | - | Vocab uniqueness |
| Estab | (caа) among non-archived | (caа) | CAA uniqueness |
| ColumnMapping | (canonical_name) among non-deprecated | (canonical_name) | Mapping uniqueness |
| Deployment | (status='active') single row | (status) | Active deployment |
| UserSubunitScope | (user_id, deployment_id, unit, sub_unit_1-3) | (user_id, deployment_id) | Scope lookup |
| DeploymentUserAccess | (user_id, deployment_id) | (deployment_id) | Access lookup |
| Session | (deployment_id, date) | (deployment_id, date) | Session lookup |
| AttendanceRecord | (session_id, personnel_id) | (deployment_id, personnel_id) | Attendance lookup |

---

## ER Diagram (Simplified)

```
                   ┌──────────────┐
                   │ AccessLevel  │
                   │ (id, name)   │
                   └──────┬───────┘
                          │
       ┌────────┬─────────┴──────────┬────────┐
       │        │                    │        │
   User         │            ColumnMetadata  │
(access_level)  │         (sensitivity_level)│
   │            │                    │       │
   ├─→ UserSubunitScope              │       │
   │   (user_id, deployment_id)      │       │
   │                                 │       │
   └─→ DeploymentUserAccess          │       │
       (user_id, deployment_id)      │       │
                                     │       │
                          Estab ◄────┴───────┘
                          │
       ┌──────────────────┼──────────────────┐
       │                  │                  │
   CsvUpload      ColumnMetadata       Personnel
       │                  │        (id, pers_no,
       │                  │         rank, name,
       │                  │         unit, sub_unit*)
       │                  │                  │
       │                  │                  │
       └──────────────────┴──────────────────┘
                  │
            Deployment ◄────────────────────┐
            │    │    │                     │
            │    │    └─→ Deployment        │
            │    │        PersonnelOverride │
            │    │                          │
            │    └─→ DeploymentNotes ◄──────┘
            │        (deployment_id,
            │         personnel_id)
            │
        Session
        │
        └─→ AttendanceRecord
            (session_id, personnel_id,
             status, remarks,
             *_snapshot fields)


AuditLog ◄─ (captures all writes)
```

---

*End of Data Model Reference*
