# Schema Design Notes

Decisions, non-obvious choices, and constraints an implementer needs to know.

---

## Access level ordering and column visibility query

`access_levels.level_order` is an integer where **higher = broader access** (e.g. `unit`=40 > `coy`=30 > `platoon`=20 > `section`=10). Gaps are intentional so new levels can be inserted without renumbering.

To check whether a user can see a column:
```sql
SELECT
    u.access_level_id,
    al_user.level_order AS user_order,
    cm.raw_name,
    al_col.level_order  AS col_order,
    (al_user.level_order >= al_col.level_order) AS can_see
FROM users u
JOIN access_levels al_user ON al_user.id = u.access_level_id
JOIN column_metadata cm ON cm.csv_upload_id = :current_csv_upload_id
LEFT JOIN access_levels al_col ON al_col.label = cm.sensitivity_label
WHERE u.id = :user_id;
```
`null` sensitivity_label on a column → admin-only (no `access_levels` row to join → `al_col.level_order` is null → `>=` fails for all non-admins).

---

## Row visibility query pattern

For a scoped user viewing a grouping:
```sql
-- Effective personnel assignment = override if present, else nominal roll
WITH effective_assignment AS (
    SELECT
        p.id            AS personnel_id,
        p.pers_no,
        COALESCE(dpo.unit,        p.unit)        AS unit,
        COALESCE(dpo.sub_unit_1,  p.sub_unit_1)  AS sub_unit_1,
        COALESCE(dpo.sub_unit_2,  p.sub_unit_2)  AS sub_unit_2,
        COALESCE(dpo.sub_unit_3,  p.sub_unit_3)  AS sub_unit_3
    FROM personnel p
    JOIN groupings d ON d.nominal_roll_id = p.nominal_roll_id
    LEFT JOIN grouping_personnel_overrides dpo
        ON dpo.grouping_id = d.id AND dpo.personnel_id = p.id
    WHERE d.id = :grouping_id
      AND p.status = 'active'
)
SELECT ea.*
FROM effective_assignment ea
WHERE EXISTS (
    SELECT 1
    FROM user_subunit_scope_grants g
    WHERE g.user_id = :user_id
      AND g.grouping_id = :grouping_id
      AND (g.unit       IS NULL OR g.unit       = ea.unit)
      AND (g.sub_unit_1 IS NULL OR g.sub_unit_1 = ea.sub_unit_1)
      AND (g.sub_unit_2 IS NULL OR g.sub_unit_2 = ea.sub_unit_2)
      AND (g.sub_unit_3 IS NULL OR g.sub_unit_3 = ea.sub_unit_3)
);
```

---

## Attendance unit+subunit snapshot rule (application logic, not DB constraint)

The DB does not enforce the validity-range rule — this is application-layer logic that MUST be implemented in the attendance write handler:

```
function writeAttendance(record, grouping):
    now = current_timestamp()
    update status, remarks, notes_snapshot always

    if now >= grouping.valid_from AND now <= grouping.valid_until:
        resolve effective assignment for the personnel row (override ?? nominal roll)
        update unit_snapshot, sub_unit_1_snapshot, sub_unit_2_snapshot, sub_unit_3_snapshot
        set snapshot_taken_at = now
    else:
        do NOT touch *_snapshot fields
```

This must hold even when an admin retroactively opens a session or edits attendance on an inactive grouping.

---

## Grouping non-overlap enforcement (application layer)

The DB has a partial unique index ensuring only one `active` grouping exists. Overlap prevention for `draft`/future groupings is enforced in the application layer:

On grouping create or valid_from/valid_until edit:
```sql
SELECT id, name, valid_from, valid_until
FROM groupings
WHERE status IN ('draft', 'active')
  AND id != :this_grouping_id
  AND valid_from  < :new_valid_until
  AND valid_until > :new_valid_from;
-- If any rows returned → reject with conflict error showing the offending grouping names
```

---

## Session creation and notes snapshotting

On session create, populate `attendance_records` for all non-archived personnel in the grouping, snapshotting current notes:

```sql
INSERT INTO attendance_records
    (session_id, grouping_id, personnel_id, status, notes_snapshot)
SELECT
    :session_id,
    :grouping_id,
    p.id,
    'absent',
    COALESCE(dn.notes, '')
FROM personnel p
JOIN groupings d ON d.nominal_roll_id = p.nominal_roll_id AND d.id = :grouping_id
LEFT JOIN grouping_notes dn ON dn.grouping_id = :grouping_id AND dn.personnel_id = p.id
WHERE p.status = 'active'
ON CONFLICT (session_id, personnel_id) DO NOTHING;
```

This means every session starts fully populated (all absent) and notes are frozen at open time in the snapshot. The canonical notes in `grouping_notes` remain live.

---

## Notes write-back from attendance view

When a user edits notes in the attendance session view:
1. UPSERT into `grouping_notes (grouping_id, personnel_id)` with new text.
2. UPDATE `attendance_records.notes_snapshot` for the current session's record.
3. Do NOT update `notes_snapshot` in other sessions' attendance records (those are historical).

---

## Notes transfer on new nominal roll confirmation

Notes follow the *person*, not the row. When a new nominal roll is confirmed, personnel are matched
to prior nominal rolls by `pers_no` (same person — see cross-roll matching in SPECIFICATION §3.2.1).
Notes from the prior active grouping are copied onto the matched personnel rows in the new
grouping:

```sql
INSERT INTO grouping_notes (grouping_id, personnel_id, notes, updated_at, updated_by)
SELECT
    :new_grouping_id,
    new_p.id,
    dn.notes,
    NOW(),
    :system_user_id
FROM grouping_notes dn
JOIN groupings old_d ON old_d.id = dn.grouping_id
JOIN personnel old_p ON old_p.id = dn.personnel_id
JOIN personnel new_p ON new_p.pers_no = old_p.pers_no     -- same person, cross-roll
WHERE old_d.id = :prior_grouping_id
  AND new_p.nominal_roll_id = :new_nominal_roll_id
  AND new_p.status = 'active'
ON CONFLICT (grouping_id, personnel_id) DO NOTHING;
```

Unmatched persons (no `pers_no` counterpart in the prior roll — including NULL `pers_no`)
start with no transferred notes.

---

## Audit log — append-only enforcement

Consider a Postgres trigger or application-level policy to prevent UPDATE/DELETE on `audit_log`. Example trigger:

```sql
CREATE OR REPLACE FUNCTION prevent_audit_modification()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_log_immutable
BEFORE UPDATE OR DELETE ON audit_log
FOR EACH ROW EXECUTE FUNCTION prevent_audit_modification();
```

---

## Column mapping conflict detection on upload

On CSV upload, after parsing headers, check for conflicts:
```sql
SELECT cm.raw_name, cm.canonical_name AS existing_canonical
FROM column_mappings cm
WHERE cm.raw_name = ANY(:uploaded_column_names)
  AND NOT cm.deprecated
  AND cm.canonical_name != :suggested_canonical_for_that_raw_name;
```
Surface each conflict to the admin with: raw name / existing mapping / new suggested mapping / confirm button.

---

## Background job — grouping scheduler

The background job (pg-boss or BullMQ) must handle two transitions:

1. **Activation:** At `valid_from` (or `scheduled_activation` if set), if grouping status is `draft`:
   - BEGIN transaction
   - UPDATE current `active` grouping → `inactive`
   - UPDATE this grouping → `active`
   - Write audit log entries for both
   - COMMIT

2. **Deactivation:** At `valid_until`, if grouping status is `active`:
   - UPDATE → `inactive`
   - Write audit log entry

Both transitions must be idempotent (safe to run twice if the job fires twice).
