-- =============================================================================
-- Parade State Management System
-- PostgreSQL Schema v0.1
--
-- Design notes:
--   - All timestamps are TIMESTAMPTZ (UTC stored, display in local TZ)
--   - Soft-delete pattern used for enums and column mappings (deprecated flag)
--   - Append-only tables: csv_uploads, audit_log (no UPDATE/DELETE in app code)
--   - JSONB used for extra_fields (non-canonical CSV columns) and audit payloads
--   - access_level_order: higher integer = broader access (unit > coy > platoon)
--   - RLS (Row-Level Security) is enforced in application layer, not Postgres RLS,
--     to keep query logic visible and testable. Postgres RLS may be added later.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- Extensions
-- ---------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "citext";     -- case-insensitive text for emails


-- ---------------------------------------------------------------------------
-- 1. ACCESS LEVELS
--    Admin-defined ordered vocabulary used for both row access scoping
--    and column sensitivity labelling.
--    level_order: higher = broader access. e.g. unit(40) > coy(30) > platoon(20) > section(10)
--    Gaps in ordering are intentional (allows insertion without renumbering).
-- ---------------------------------------------------------------------------

CREATE TABLE access_levels (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    label           TEXT NOT NULL UNIQUE,          -- e.g. 'unit', 'coy', 'platoon', 'section'
    level_order     INTEGER NOT NULL UNIQUE,        -- higher = broader access
    deprecated      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by      UUID,                           -- FK to users; null for bootstrap
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by      UUID                            -- FK to users; null for bootstrap
);

COMMENT ON TABLE access_levels IS
    'Ordered vocabulary of access level labels. Used for user row-access scoping and column sensitivity. '
    'level_order higher = broader access. Deprecated entries are hidden from UI but retained for FK integrity.';


-- ---------------------------------------------------------------------------
-- 2. SUBUNIT ENUM VALUES
--    Admin-managed valid values for each subunit hierarchy level.
--    Validated at CSV import time.
--    level: 0=unit, 1=sub_unit_1, 2=sub_unit_2, 3=sub_unit_3
-- ---------------------------------------------------------------------------

CREATE TABLE subunit_enum_values (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    level           SMALLINT NOT NULL CHECK (level BETWEEN 0 AND 3),
    value           TEXT NOT NULL,
    deprecated      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by      UUID,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by      UUID,
    UNIQUE (level, value)
);

COMMENT ON TABLE subunit_enum_values IS
    'Valid values for each subunit hierarchy level (0=unit, 1=sub_unit_1, 2=sub_unit_2, 3=sub_unit_3). '
    'Validated at CSV import. Deprecated values are retained for FK integrity.';


-- ---------------------------------------------------------------------------
-- 3. USERS
--    Google OAuth accounts. Preregistered by admin (status=pending).
--    Activated on first sign-in. Access level and scope assigned at preregistration.
-- ---------------------------------------------------------------------------

CREATE TABLE users (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email               CITEXT NOT NULL UNIQUE,
    display_name        TEXT,                          -- populated from Google profile on first sign-in
    google_sub          TEXT UNIQUE,                   -- Google subject ID; set on first sign-in
    role                TEXT NOT NULL DEFAULT 'scoped' CHECK (role IN ('super_admin', 'admin', 'scoped')),
    status              TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'active', 'suspended', 'unrecognised')),
    access_level_id     UUID REFERENCES access_levels(id),   -- null for admins (they bypass)
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by          UUID REFERENCES users(id),
    activated_at        TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by          UUID REFERENCES users(id)
);

COMMENT ON TABLE users IS
    'Google OAuth user accounts. Preregistered by admin (status=pending); activated on first sign-in. '
    'super_admin bootstrapped via SUPER_ADMIN_EMAIL env var. access_level_id null for admin/super_admin roles.';

COMMENT ON COLUMN users.google_sub IS
    'Google OAuth subject identifier. Null until first sign-in. Used as authoritative identity after activation.';


-- ---------------------------------------------------------------------------
-- 4. USER GROUPING GRANTS
--    Controls which groupings a user can see (separate from subunit scope).
--    A user must have a grant for a grouping to know it exists.
-- ---------------------------------------------------------------------------

CREATE TABLE user_grouping_grants (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    grouping_id     UUID NOT NULL,                     -- FK to groupings; defined below
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by      UUID REFERENCES users(id),
    UNIQUE (user_id, grouping_id)
);

COMMENT ON TABLE user_grouping_grants IS
    'Controls grouping visibility per user. A user without a grant for a grouping cannot see or know it exists.';


-- ---------------------------------------------------------------------------
-- 5. USER SUBUNIT SCOPE GRANTS
--    Controls which personnel rows a user can see within a given grouping.
--    Each row scopes a user to a specific subunit within a grouping.
--    Nulls in subunit fields mean "any value at that level".
--    A unit-level user would have sub_unit_1/2/3 all null.
--    A coy-level user would have sub_unit_1 set, sub_unit_2/3 null.
--    A user's visible rows = UNION of all their subunit scope grants for that grouping.
-- ---------------------------------------------------------------------------

CREATE TABLE user_subunit_scope_grants (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    grouping_id     UUID NOT NULL,                     -- FK to groupings
    unit            TEXT,                              -- null = any
    sub_unit_1      TEXT,
    sub_unit_2      TEXT,
    sub_unit_3      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by      UUID REFERENCES users(id)
);

COMMENT ON TABLE user_subunit_scope_grants IS
    'Determines which personnel rows a user can see within a grouping. '
    'Null subunit fields act as wildcards. Row is visible if any grant matches. '
    'Admin role bypasses this table entirely.';


-- ---------------------------------------------------------------------------
-- 6. COLUMN MAPPINGS
--    Global mapping table: raw CSV column names → canonical app names.
--    Accumulates across CSV uploads. Admin-editable at any time.
--    Applies to future uploads only; not retroactive.
-- ---------------------------------------------------------------------------

CREATE TABLE column_mappings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    raw_name        TEXT NOT NULL,                     -- as it appeared in the CSV header
    canonical_name  TEXT NOT NULL,                     -- app canonical name (matches required_columns config)
    status          TEXT NOT NULL DEFAULT 'auto_detected'
                        CHECK (status IN ('auto_detected', 'admin_confirmed', 'deprecated')),
    deprecated      BOOLEAN NOT NULL DEFAULT FALSE,
    first_seen_in   UUID,                              -- FK to csv_uploads (informational)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    confirmed_at    TIMESTAMPTZ,
    confirmed_by    UUID REFERENCES users(id),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by      UUID REFERENCES users(id)
);

CREATE INDEX idx_column_mappings_raw_name ON column_mappings(raw_name) WHERE NOT deprecated;

COMMENT ON TABLE column_mappings IS
    'Global mapping of CSV raw column names to app canonical names. '
    'Accumulates over time. Admin-editable. Edits apply to future uploads only. '
    'Deprecated entries retained for provenance; excluded from active matching.';


-- ---------------------------------------------------------------------------
-- 7. CSV UPLOADS (ESTAB)
--    Immutable after insert. Raw CSV preserved verbatim.
--    Content hash for integrity verification.
--    Identified by CAA (correct-as-at) date.
-- ---------------------------------------------------------------------------

CREATE TABLE csv_uploads (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    caa_date        DATE NOT NULL UNIQUE,              -- correct-as-at date; must be unique
    raw_csv         TEXT NOT NULL,                     -- verbatim CSV text; never modified
    content_hash    TEXT NOT NULL,                     -- SHA-256 of raw_csv; for integrity checks
    original_filename TEXT,
    status          TEXT NOT NULL DEFAULT 'pending_mapping'
                        CHECK (status IN (
                            'pending_mapping',         -- awaiting column mapping confirmation
                            'pending_diff',            -- mapping confirmed; awaiting admin diff review
                            'confirmed',               -- committed; personnel_snapshots populated
                            'superseded'               -- a newer CSV has been confirmed
                        )),
    row_count       INTEGER,                           -- populated after parse
    uploaded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    uploaded_by     UUID NOT NULL REFERENCES users(id),
    confirmed_at    TIMESTAMPTZ,
    confirmed_by    UUID REFERENCES users(id)
);

COMMENT ON TABLE csv_uploads IS
    'Immutable store of raw CSV uploads (estab). Never updated after insert. '
    'content_hash is SHA-256 of raw_csv for integrity verification. '
    'status tracks the upload confirmation workflow.';


-- ---------------------------------------------------------------------------
-- 8. COLUMN METADATA
--    Per-CSV-version record of each column: original name, canonical name,
--    inferred type, and admin-assigned sensitivity label.
--    Sensitivity label is mutable (admin can change at any time; audit logged).
-- ---------------------------------------------------------------------------

CREATE TABLE column_metadata (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    csv_upload_id       UUID NOT NULL REFERENCES csv_uploads(id),
    raw_name            TEXT NOT NULL,
    canonical_name      TEXT,                          -- null if not mapped to a canonical name
    is_required         BOOLEAN NOT NULL DEFAULT FALSE,
    inferred_type       TEXT CHECK (inferred_type IN ('text', 'integer', 'date', 'boolean', 'numeric')),
    confirmed_type      TEXT CHECK (confirmed_type IN ('text', 'integer', 'date', 'boolean', 'numeric')),
    sensitivity_label   TEXT REFERENCES access_levels(label),  -- null = admin-only by default
    display_order       INTEGER,                       -- column order for table display
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sensitivity_updated_at  TIMESTAMPTZ,
    sensitivity_updated_by  UUID REFERENCES users(id),
    UNIQUE (csv_upload_id, raw_name)
);

COMMENT ON TABLE column_metadata IS
    'Per-CSV-version column registry. sensitivity_label is mutable (admin-configurable). '
    'null sensitivity_label = admin-only visibility. '
    'confirmed_type overrides inferred_type when set.';


-- ---------------------------------------------------------------------------
-- 9. PERSONNEL SNAPSHOTS
--    Parsed personnel data per CSV version.
--    Required/canonical columns stored as typed fields.
--    All other columns stored in extra_fields JSONB.
--    Immutable after population (CSV is source of truth).
-- ---------------------------------------------------------------------------

CREATE TABLE personnel_snapshots (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    csv_upload_id   UUID NOT NULL REFERENCES csv_uploads(id),
    short_id        TEXT NOT NULL,                     -- 8-char base62 cross-estab person identity (shared across estabs for the same person)
    unit            TEXT NOT NULL,
    sub_unit_1      TEXT,
    sub_unit_2      TEXT,
    sub_unit_3      TEXT,
    rank            TEXT,
    full_name       TEXT NOT NULL,
    extra_fields    JSONB NOT NULL DEFAULT '{}',       -- all non-canonical columns (pers_no is NEVER stored here)
    archived        BOOLEAN NOT NULL DEFAULT FALSE,    -- true for leavers on new CSV import
    archived_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (csv_upload_id, short_id)
);

CREATE INDEX idx_personnel_snapshots_short_id ON personnel_snapshots(short_id);
CREATE INDEX idx_personnel_snapshots_csv_upload ON personnel_snapshots(csv_upload_id) WHERE NOT archived;
CREATE INDEX idx_personnel_snapshots_subunit ON personnel_snapshots(csv_upload_id, unit, sub_unit_1, sub_unit_2, sub_unit_3);
CREATE INDEX idx_personnel_extra_fields ON personnel_snapshots USING gin(extra_fields);

COMMENT ON TABLE personnel_snapshots IS
    'Parsed personnel data per CSV version. Immutable after population. '
    'extra_fields holds all columns not mapped to a required canonical field. '
    'short_id is the cross-estab person key (minted by the application, matched by name+rank). '
    'archived=true for leavers (present in prior estab, absent in new).';


-- ---------------------------------------------------------------------------
-- 10. GROUPINGS
--     Named, date-ranged assignment of personnel to subunits.
--     Based on a specific estab (csv_upload). Personnel assignment overrides
--     remap a subset of personnel; non-overridden inherit estab assignment.
--     Only one grouping may be active at any point in time.
--     Validity enforced by background job (activate at valid_from, deactivate at valid_until).
-- ---------------------------------------------------------------------------

CREATE TABLE groupings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    csv_upload_id   UUID NOT NULL REFERENCES csv_uploads(id),
    status          TEXT NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft', 'active', 'inactive', 'archived')),
    valid_from      TIMESTAMPTZ NOT NULL,
    valid_until     TIMESTAMPTZ NOT NULL,
    scheduled_activation TIMESTAMPTZ,                 -- if set, background job activates at this time
    cloned_from_id  UUID REFERENCES groupings(id),    -- if this was created via clone or migrate
    clone_type      TEXT CHECK (clone_type IN ('same_estab', 'cross_estab')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by      UUID NOT NULL REFERENCES users(id),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by      UUID REFERENCES users(id),
    CONSTRAINT valid_range_check CHECK (valid_until > valid_from)
);

CREATE UNIQUE INDEX idx_groupings_one_active
    ON groupings(status)
    WHERE status = 'active';

COMMENT ON TABLE groupings IS
    'Named date-ranged operational contexts. Based on a specific CSV estab. '
    'Only one active grouping at a time (enforced by partial unique index). '
    'Background job handles activation/deactivation at valid_from/valid_until. '
    'No overlapping valid_from/valid_until ranges permitted (enforced in application layer).';

COMMENT ON COLUMN groupings.scheduled_activation IS
    'If set, background job transitions status from draft to active at this datetime, '
    'and deactivates the currently active grouping.';


-- ---------------------------------------------------------------------------
-- 11. GROUPING PERSONNEL OVERRIDES
--     Subunit remappings for cross-attached personnel within a grouping.
--     Personnel without an override inherit their estab (csv_upload) subunit assignment.
--     Overrides are editable on draft/active/inactive groupings (no write-lock).
-- ---------------------------------------------------------------------------

CREATE TABLE grouping_personnel_overrides (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    grouping_id     UUID NOT NULL REFERENCES groupings(id) ON DELETE CASCADE,
    personnel_id    UUID NOT NULL REFERENCES personnel_snapshots(id),
    unit            TEXT NOT NULL,
    sub_unit_1      TEXT,
    sub_unit_2      TEXT,
    sub_unit_3      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by      UUID REFERENCES users(id),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by      UUID REFERENCES users(id),
    UNIQUE (grouping_id, personnel_id)
);

COMMENT ON TABLE grouping_personnel_overrides IS
    'Per-grouping subunit remappings. Takes precedence over estab assignment for the given personnel row. '
    'Absence of a row = inherit estab assignment. Editable at any time (no write-lock).';


-- ---------------------------------------------------------------------------
-- 12. GROUPING NOTES
--     Canonical notes store, keyed by (grouping_id, personnel_id).
--     Notes are grouping-scoped and persist across sessions.
--     Edited from both grouping view and attendance session view.
-- ---------------------------------------------------------------------------

CREATE TABLE grouping_notes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    grouping_id     UUID NOT NULL REFERENCES groupings(id) ON DELETE CASCADE,
    personnel_id    UUID NOT NULL REFERENCES personnel_snapshots(id),
    notes_text      TEXT NOT NULL DEFAULT '',
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by      UUID REFERENCES users(id),
    UNIQUE (grouping_id, personnel_id)
);

CREATE INDEX idx_grouping_notes_lookup ON grouping_notes(grouping_id, personnel_id);

COMMENT ON TABLE grouping_notes IS
    'Canonical grouping-scoped notes per personnel. '
    'Written from both grouping view and attendance session view (write-back from session). '
    'Transferred to a new grouping by following the person across estabs via short_id on new estab confirmation.';


-- ---------------------------------------------------------------------------
-- 13. SESSIONS
--     AM/PM attendance windows. Explicitly opened by admin.
--     May be created in advance (for a draft or active grouping).
--     On creation, notes are snapshotted into attendance records.
-- ---------------------------------------------------------------------------

CREATE TABLE sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    grouping_id     UUID NOT NULL REFERENCES groupings(id),
    session_date    DATE NOT NULL,
    session_type    TEXT NOT NULL CHECK (session_type IN ('AM', 'PM')),
    status          TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by      UUID NOT NULL REFERENCES users(id),
    closed_at       TIMESTAMPTZ,
    closed_by       UUID REFERENCES users(id),
    UNIQUE (grouping_id, session_date, session_type)
);

COMMENT ON TABLE sessions IS
    'AM/PM attendance windows, explicitly admin-opened. May be created in advance. '
    'On creation, current grouping_notes are snapshotted into all attendance records for this session. '
    'Unique constraint prevents duplicate AM/PM sessions per grouping per day.';


-- ---------------------------------------------------------------------------
-- 14. ATTENDANCE RECORDS
--     One record per personnel per session.
--     Stores status, remarks (session-scoped), and a notes snapshot.
--     Also stores a unit+subunit snapshot (grouping assignment at time of write),
--     subject to the validity-range rule:
--       - Write within grouping valid_from/valid_until: snapshot unit+subunit from active grouping.
--       - Write outside validity range (retroactive admin edit): do NOT update unit+subunit snapshots.
-- ---------------------------------------------------------------------------

CREATE TABLE attendance_records (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id          UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    grouping_id         UUID NOT NULL REFERENCES groupings(id),
    personnel_id        UUID NOT NULL REFERENCES personnel_snapshots(id),

    -- Attendance data
    status              TEXT NOT NULL DEFAULT 'absent' CHECK (status IN ('present', 'absent')),
    remarks             TEXT NOT NULL DEFAULT '',      -- session-scoped; not carried forward

    -- Notes snapshot (grouping-level notes at time of this write)
    notes_snapshot      TEXT NOT NULL DEFAULT '',

    -- Unit+subunit snapshot (grouping assignment at time of write, within validity period)
    unit_snapshot       TEXT,
    sub_unit_1_snapshot TEXT,
    sub_unit_2_snapshot TEXT,
    sub_unit_3_snapshot TEXT,

    -- Audit
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by          UUID REFERENCES users(id),
    snapshot_taken_at   TIMESTAMPTZ,                   -- when unit+subunit snapshot was last written

    UNIQUE (session_id, personnel_id)
);

CREATE INDEX idx_attendance_session ON attendance_records(session_id);
CREATE INDEX idx_attendance_personnel ON attendance_records(personnel_id);
CREATE INDEX idx_attendance_grouping ON attendance_records(grouping_id);

COMMENT ON TABLE attendance_records IS
    'One record per personnel per session. '
    'notes_snapshot: copy of grouping_notes.notes_text at time of this write (updated on every write). '
    'unit+subunit snapshots: updated only when NOW() is within the session grouping validity range. '
    'Retroactive admin edits outside validity range may update status/remarks/notes_snapshot '
    'but must NOT update unit/sub_unit snapshots.';

COMMENT ON COLUMN attendance_records.snapshot_taken_at IS
    'Timestamp when unit+subunit snapshot was last written. '
    'If null, snapshot was never taken (session created but no attendance yet recorded during valid period).';


-- ---------------------------------------------------------------------------
-- 15. AUDIT LOG
--     Append-only. Records all writes: attendance, admin config, access changes,
--     CSV uploads, sign-in events.
--     Admin-only access.
-- ---------------------------------------------------------------------------

CREATE TABLE audit_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_id        UUID REFERENCES users(id),         -- null for system/background job actions
    actor_email     TEXT,                              -- denormalised; preserved even if user is deleted
    action          TEXT NOT NULL,                     -- e.g. 'attendance.update', 'grouping.activate'
    entity_type     TEXT NOT NULL,                     -- e.g. 'attendance_record', 'grouping'
    entity_id       UUID,
    payload         JSONB NOT NULL DEFAULT '{}',       -- before/after values or relevant context
    ip_address      INET,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_log_entity ON audit_log(entity_type, entity_id);
CREATE INDEX idx_audit_log_actor ON audit_log(actor_id);
CREATE INDEX idx_audit_log_occurred ON audit_log(occurred_at DESC);

COMMENT ON TABLE audit_log IS
    'Append-only audit trail. No UPDATE or DELETE permitted in application code. '
    'actor_email denormalised to preserve history if user account is removed. '
    'payload stores relevant before/after context as JSONB.';


-- ---------------------------------------------------------------------------
-- 16. APP SETTINGS
--     Key-value store for runtime-mutable app settings managed via admin UI.
--     Non-sensitive only. Sensitive config (credentials, super-admin) lives in env vars.
-- ---------------------------------------------------------------------------

CREATE TABLE app_settings (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    description     TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by      UUID REFERENCES users(id)
);

COMMENT ON TABLE app_settings IS
    'Runtime app settings managed via admin UI. '
    'Sensitive config (DB credentials, OAuth secrets, SUPER_ADMIN_EMAIL) must be in env vars, not here.';


-- ---------------------------------------------------------------------------
-- FOREIGN KEY BACK-PATCHES
--    Some FKs could not be declared inline due to forward references.
-- ---------------------------------------------------------------------------

ALTER TABLE access_levels
    ADD CONSTRAINT fk_access_levels_created_by FOREIGN KEY (created_by) REFERENCES users(id),
    ADD CONSTRAINT fk_access_levels_updated_by FOREIGN KEY (updated_by) REFERENCES users(id);

ALTER TABLE subunit_enum_values
    ADD CONSTRAINT fk_subunit_enum_created_by FOREIGN KEY (created_by) REFERENCES users(id),
    ADD CONSTRAINT fk_subunit_enum_updated_by FOREIGN KEY (updated_by) REFERENCES users(id);

ALTER TABLE column_mappings
    ADD CONSTRAINT fk_column_mappings_first_seen FOREIGN KEY (first_seen_in) REFERENCES csv_uploads(id);

ALTER TABLE user_grouping_grants
    ADD CONSTRAINT fk_user_grp_grants_grouping FOREIGN KEY (grouping_id) REFERENCES groupings(id) ON DELETE CASCADE;

ALTER TABLE user_subunit_scope_grants
    ADD CONSTRAINT fk_user_scope_grants_grouping FOREIGN KEY (grouping_id) REFERENCES groupings(id) ON DELETE CASCADE;
