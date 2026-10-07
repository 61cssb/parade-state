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
    cm.original_name,
    al_col.level_order  AS col_order,
    (al_user.level_order >= al_col.level_order) AS can_see
FROM users u
JOIN access_levels al_user ON al_user.id = u.access_level_id
JOIN column_metadata cm ON cm.nominal_roll_id = :nominal_roll_id
LEFT JOIN access_levels al_col ON al_col.id = cm.sensitivity_level_id
WHERE u.id = :user_id;
```
`null` `sensitivity_level_id` on a column → admin-only (no `access_levels` row to join → `al_col.level_order` is null → `>=` fails for all non-admins).

---

## Row visibility query pattern

Write access is scoped per nominal roll by `UserSubunitAssignment` on the
effective `(unit, sub_unit_1)` pair (tagging-overlay-aware; super-admin
bypasses). Each grant column uses the explicit sentinel `*` for a
wildcard — `(U, *)`, `(U, S)`, `(*, S)` are legal; `(*, *)` is forbidden
by `ck_user_subunit_assignment_not_both_wildcard` (the whole-roll case
is expressed per unit, not as a blanket grant). A `null` effective value
never equals a concrete grant value, so it matches only `*` grants:
```sql
SELECT p.*
FROM personnel p
LEFT JOIN tagging_entries te
    ON te.tagging_id = :nr_tagging_id AND te.personnel_id = p.id
WHERE p.nominal_roll_id = :nominal_roll_id
  AND p.status = 'active'
  AND EXISTS (
      SELECT 1 FROM user_subunit_assignments usa
      WHERE usa.user_id = :user_id
        AND usa.nominal_roll_id = :nominal_roll_id
        AND (usa.unit = '*' OR usa.unit = COALESCE(te.to_unit, p.unit))
        AND (usa.sub_unit_1 = '*'
             OR usa.sub_unit_1 = COALESCE(te.to_sub_unit_1, p.sub_unit_1))
  );
```

---

## Attendance unit+subunit snapshot rule (application logic, not DB constraint)

Snapshots are populated by the attendance write handler — the DB does not
enforce them:

```
function writeAttendance(record):
    update status, remarks
    snapshot unit, sub_unit_1..3 from the personnel row's effective
      (tagging-overlaid) values into the *_snapshot columns
    update last_edit_at, last_edit_by
```

Groupings play no part in this (issue 26 redesign): there are no grouping
validity windows, overrides, or notes to resolve.

---

## Groupings schema (issue 26 redesign)

Four tables; the old grouping tables (`grouping_personnel_overrides`,
`grouping_notes`, `grouping_personnel_exclusions`,
`grouping_user_accesses`, `user_subunit_scopes`) were dropped in
migration `s9f0a1b2c3d4`:

- `groupings`: `label` (String 100), `nominal_roll_id`
  (FK RESTRICT — deleting an NR with groupings is refused),
  `multiple_membership` (default false), `allow_ungrouped`
  (default true), `created_at`/`created_by`. **UNIQUE
  (nominal_roll_id, label)** — labels are unique per roll, not globally,
  so a copy from a previous roll may keep its label while the source
  still exists on the old roll
- `grouping_groups`: `grouping_id` (FK CASCADE), `label`
  (UNIQUE per grouping), `position` (manual display order). Memberships
  reference the row, so a rename propagates to every member and a delete
  cascades their memberships away
- `grouping_memberships`: (`grouping_id`, `group_id`, `personnel_id`),
  UNIQUE on the triple
- `grouping_member_state`: (`grouping_id`, `personnel_id`) UNIQUE — one
  checkbox/remarks row per serviceman per grouping, independent of how
  many groups they hold

Two rules are **application-enforced** (they cannot be expressed as
plain constraints): at most one group per serviceman when
`multiple_membership=false`, and at least one when
`allow_ungrouped=false`. Both flags are immutable after creation, so the
rule a grouping follows never changes under live data. Groupings never
read or write attendance.

---

## Audit log — append-only enforcement

Consider a Postgres trigger or application-level policy to prevent UPDATE/DELETE on `audit_logs`. Example trigger:

```sql
CREATE OR REPLACE FUNCTION prevent_audit_modification()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_logs is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_logs_immutable
BEFORE UPDATE OR DELETE ON audit_logs
FOR EACH ROW EXECUTE FUNCTION prevent_audit_modification();
```

---

## Column mapping conflict detection on upload

On CSV upload, after parsing headers, check for conflicts:
```sql
SELECT cm.raw_name, cm.canonical_name AS existing_canonical
FROM column_mappings cm
WHERE cm.raw_name = ANY(:uploaded_column_names)
  AND cm.deprecated_at IS NULL
  AND cm.canonical_name != :suggested_canonical_for_that_raw_name;
```
Surface each conflict to the admin with: raw name / existing mapping / new suggested mapping / confirm button.
