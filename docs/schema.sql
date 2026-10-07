-- =============================================================================
-- Parade State Management System — Schema Reference
--
-- This file is documentation, not a migration: it mirrors the SQLAlchemy
-- models, which are authoritative (src/parade_state/models/*.py, shared
-- Base in src/parade_state/db/__init__.py). The live schema is applied via
-- Alembic (src/parade_state/migrations/versions/, head y6f7a8b9c0d1) or
-- metadata.create_all for fresh dev databases.
--
-- Backends: SQLite (aiosqlite) for dev, PostgreSQL (asyncpg) for prod.
-- Where they differ, both are noted inline. Key conventions:
--
--   - Primary keys: VARCHAR(36) app-generated UUID-format strings
--     (Base.id, default ids.db_default). No UUID columns, no pgcrypto,
--     no gen_random_uuid(). Every table also carries a non-unique
--     ix_<table>_id index (inherited from Base).
--   - Timestamps: naive TIMESTAMP (no time zone), always UTC by
--     convention (utils/utc_dt.py: ensure_naive(utcnow())). No
--     TIMESTAMPTZ, no CITEXT.
--   - Enum columns: native enum types (CREATE TYPE below) on PostgreSQL;
--     plain VARCHAR columns on SQLite (the enum name is metadata-only,
--     no CHECK constraint is emitted).
--   - Defaults marked "(app)" are applied by SQLAlchemy client-side, not
--     by the database. The only server default in the schema is
--     user_subunit_assignments.unit ('*').
--   - extra_fields is JSON — not JSONB, no GIN index.
--   - ip_address is VARCHAR(45) — plain string, not INET.
--   - access_levels and users are mutually referential (created_by /
--     access_level_id). When executing DDL by hand, back-patch one
--     direction with ALTER TABLE ... ADD FOREIGN KEY.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- Enum vocabularies (native types on PostgreSQL; ignored on SQLite)
-- ---------------------------------------------------------------------------

CREATE TYPE user_status AS ENUM (
    'pending',        -- legacy value, unused (kept for the DB enum)
    'active',
    'suspended',
    'unrecognised'
);

CREATE TYPE user_role AS ENUM ('super_admin', 'admin', 'user');

CREATE TYPE attendance_status AS ENUM ('present', 'absent');

CREATE TYPE attendance_reason AS ENUM (
    'mc', 'off', 'early_outpro', 'other', 'awol'
);

CREATE TYPE audit_entity_type AS ENUM (
    'attendance', 'grouping', 'session', 'user', 'csv_upload',
    'nominal_roll', 'personnel', 'access_level', 'column_mapping',
    'database', 'discussion_post', 'feature_access'
);

CREATE TYPE audit_action AS ENUM (
    'create', 'update', 'delete', 'archive', 'close',
    'finalize', 'restore', 'attendance_freeze'
);

CREATE TYPE csv_upload_status AS ENUM (
    'received', 'mapping_confirmed', 'diff_confirmed', 'failed'
);

CREATE TYPE column_mapping_status AS ENUM (
    'auto_detected', 'admin_confirmed', 'deprecated'
);

CREATE TYPE column_data_type AS ENUM (
    'string', 'integer', 'date', 'boolean', 'json'
);

CREATE TYPE personnel_category AS ENUM ('Officer', 'WOSE');

CREATE TYPE personnel_status AS ENUM ('active', 'archived');

CREATE TYPE personnel_inpro_status AS ENUM (
    'inproed', 'yet_to_inpro', 'deferred'
);

CREATE TYPE deferment_reason AS ENUM (
    'Honeymoon', 'Work', 'Full-time studies', 'Other',
    'Medical Grounds', 'Examination', 'New employment',
    'Special employment', 'Compassionate', 'Childbirth',
    'Part-time studies', 'Newly Established Business (Local)'
);

CREATE TYPE deferment_status AS ENUM (
    'Approved', 'Withdrawn', 'Rejected', 'To Resubmit',
    'Time off arrangement', 'Pending action', 'Not called up',
    'Do not call up'
);

CREATE TYPE discussion_category AS ENUM ('requests', 'bugs');

CREATE TYPE discussion_post_status AS ENUM (
    'Open', 'Duplicate', 'Accepted', 'Implemented', 'Closed'
);


-- ---------------------------------------------------------------------------
-- 1. ACCESS LEVELS — src/parade_state/models/access.py (AccessLevel)
--    Ordered vocabulary of access scopes, used for user row-access
--    scoping and column sensitivity labelling.
--    level_order: higher = broader access (e.g. unit 40 > coy 30).
--    Gaps are intentional (insert without renumbering).
-- ---------------------------------------------------------------------------

CREATE TABLE access_levels (
    id              VARCHAR(36) PRIMARY KEY,
    name            VARCHAR(50) NOT NULL,          -- e.g. 'unit', 'coy', 'platoon', 'section'
    level_order     INTEGER NOT NULL,              -- higher = broader access
    created_at      TIMESTAMP NOT NULL,            -- (app) utcnow
    created_by      VARCHAR(36) REFERENCES users(id),   -- null for bootstrap
    updated_at      TIMESTAMP NOT NULL,            -- (app) utcnow
    updated_by      VARCHAR(36) REFERENCES users(id)
);

CREATE UNIQUE INDEX ix_access_levels_name ON access_levels (name);
CREATE UNIQUE INDEX ix_access_levels_level_order ON access_levels (level_order);
CREATE INDEX ix_access_levels_id ON access_levels (id);


-- ---------------------------------------------------------------------------
-- 2. USERS — src/parade_state/models/access.py (User)
--    Google-authenticated accounts. No preregistration state machine:
--    unknown sign-ins land as status='unrecognised'. No google_sub,
--    no activated_at, no created_by/updated_by self-FKs.
-- ---------------------------------------------------------------------------

CREATE TABLE users (
    id                  VARCHAR(36) PRIMARY KEY,
    email               VARCHAR(255) NOT NULL,     -- plain string, not CITEXT
    name                VARCHAR(255) NOT NULL,     -- from Google profile
    status              user_status NOT NULL DEFAULT 'unrecognised',  -- (app)
    role                user_role NOT NULL DEFAULT 'user',            -- (app)
    access_level_id     VARCHAR(36) REFERENCES access_levels(id),  -- null = no scope grant
    first_sign_in_at    TIMESTAMP,
    last_sign_in_at     TIMESTAMP,
    created_at          TIMESTAMP NOT NULL,        -- (app) utcnow
    updated_at          TIMESTAMP NOT NULL         -- (app) utcnow
);

CREATE UNIQUE INDEX ix_users_email ON users (email);
CREATE INDEX ix_users_id ON users (id);


-- ---------------------------------------------------------------------------
-- 3. USER SESSIONS — src/parade_state/models/auth_session.py (UserSession)
--    Server-side auth sessions. token is the natural key; because the
--    model also inherits Base.id (primary_key=True), the emitted PK is
--    composite (token, id).
-- ---------------------------------------------------------------------------

CREATE TABLE user_sessions (
    token               VARCHAR(255) NOT NULL,
    id                  VARCHAR(36) NOT NULL,
    user_id             VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email               VARCHAR(255) NOT NULL,     -- denormalised from users
    name                VARCHAR(255) NOT NULL,
    role                VARCHAR(50) NOT NULL,
    created_at          TIMESTAMP NOT NULL,        -- (app) utcnow
    expires_at          TIMESTAMP NOT NULL,
    last_accessed_at    TIMESTAMP NOT NULL,        -- (app) utcnow
    user_agent          VARCHAR(500),
    ip_address          VARCHAR(45),
    PRIMARY KEY (token, id)
);

CREATE INDEX ix_user_sessions_user_id ON user_sessions (user_id);
CREATE INDEX ix_user_sessions_expires_at ON user_sessions (expires_at);
CREATE INDEX ix_user_sessions_id ON user_sessions (id);


-- ---------------------------------------------------------------------------
-- 4. FEATURE ACCESS — src/parade_state/models/access.py (FeatureAccess)
--    Role-level feature visibility matrix (issue 37). One row per
--    (feature, role) explicitly configured. Absent row = enabled
--    (fail-open); super_admin is never configurable.
-- ---------------------------------------------------------------------------

CREATE TABLE feature_access (
    id              VARCHAR(36) PRIMARY KEY,
    feature_key     VARCHAR(50) NOT NULL,
    role            VARCHAR(20) NOT NULL,
    enabled         BOOLEAN NOT NULL,              -- (app) default true
    created_at      TIMESTAMP NOT NULL,            -- (app) utcnow
    updated_at      TIMESTAMP NOT NULL,            -- (app) utcnow
    CONSTRAINT uq_feature_access_key_role UNIQUE (feature_key, role)
);

CREATE INDEX ix_feature_access_id ON feature_access (id);


-- ---------------------------------------------------------------------------
-- 5. USER SUBUNIT ASSIGNMENTS — src/parade_state/models/access.py
--    (UserSubunitAssignment; issues #4 and #28)
--    Scope grants: one (unit, sub_unit_1) pair on one nominal roll.
--    Each column uses the explicit sentinel '*' for a wildcard:
--      (U, *)  every sub-unit of unit U
--      (U, S)  exactly U/S
--      (*, S)  S under any unit (pre-#28 rows via the column default)
--      (*, *)  forbidden by CHECK — the whole-roll case is expressed
--              per unit, not as a blanket grant
--    Deny-by-default: no grant on an NR = no access there.
--    super_admin bypasses entirely.
-- ---------------------------------------------------------------------------

CREATE TABLE user_subunit_assignments (
    id                  VARCHAR(36) PRIMARY KEY,
    user_id             VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    nominal_roll_id     VARCHAR(36) NOT NULL REFERENCES nominal_rolls(id) ON DELETE CASCADE,
    unit                VARCHAR(255) NOT NULL DEFAULT '*',   -- the only server default
    sub_unit_1          VARCHAR(255) NOT NULL,
    created_at          TIMESTAMP NOT NULL,        -- (app) utcnow
    created_by          VARCHAR(36) NOT NULL REFERENCES users(id),
    updated_at          TIMESTAMP NOT NULL,        -- (app) utcnow
    CONSTRAINT uq_user_subunit_assignment UNIQUE (user_id, nominal_roll_id, unit, sub_unit_1),
    CONSTRAINT ck_user_subunit_assignment_not_both_wildcard
        CHECK (unit <> '*' OR sub_unit_1 <> '*')
);

CREATE INDEX ix_user_subunit_assignments_user_id ON user_subunit_assignments (user_id);
CREATE INDEX ix_user_subunit_assignments_nominal_roll_id ON user_subunit_assignments (nominal_roll_id);
CREATE INDEX ix_user_subunit_assignments_id ON user_subunit_assignments (id);


-- ---------------------------------------------------------------------------
-- 6. NOMINAL ROLLS — src/parade_state/models/csv_ingestion.py (NominalRoll)
--    Base personnel roster, sourced from CSV, pinned by CAA date.
--    Exactly one NR is "active for attendance" at a time
--    (attendance_active; application-enforced on activate).
-- ---------------------------------------------------------------------------

CREATE TABLE nominal_rolls (
    id                      VARCHAR(36) PRIMARY KEY,
    caa                     DATE NOT NULL,             -- correct-as-at; unique
    csv_hash                VARCHAR(64) NOT NULL,      -- SHA-256 of source CSV
    attendance_active       BOOLEAN NOT NULL,          -- (app) default false
    attendance_activated_at TIMESTAMP,
    attendance_activated_by VARCHAR(36) REFERENCES users(id),
    personnel_count         INTEGER NOT NULL,          -- (app) default 0
    uploaded_at             TIMESTAMP NOT NULL,        -- (app) utcnow
    uploaded_by             VARCHAR(36) NOT NULL REFERENCES users(id),
    created_at              TIMESTAMP NOT NULL,        -- (app) utcnow
    notes                   TEXT,
    label                   VARCHAR(100),              -- optional display name; unique when set
    remarks                 TEXT
);

CREATE UNIQUE INDEX ix_nominal_rolls_caa ON nominal_rolls (caa);
CREATE UNIQUE INDEX ix_nominal_rolls_label ON nominal_rolls (label);
CREATE INDEX ix_nominal_rolls_csv_hash ON nominal_rolls (csv_hash);
CREATE INDEX ix_nominal_rolls_id ON nominal_rolls (id);


-- ---------------------------------------------------------------------------
-- 7. CSV UPLOADS — src/parade_state/models/csv_ingestion.py (CsvUpload)
--    Immutable, append-only raw CSV storage. sha256_hash dedupes
--    re-uploads. The pipeline currently only writes status='received'.
-- ---------------------------------------------------------------------------

CREATE TABLE csv_uploads (
    id                    VARCHAR(36) PRIMARY KEY,
    nominal_roll_id       VARCHAR(36) REFERENCES nominal_rolls(id),  -- null until NR created
    raw_content           BYTEA NOT NULL,          -- verbatim CSV bytes (SQLite: BLOB)
    sha256_hash           VARCHAR(64) NOT NULL,    -- unique
    original_filename     VARCHAR(255),
    line_count            INTEGER NOT NULL,
    uploaded_at           TIMESTAMP NOT NULL,      -- (app) utcnow
    uploaded_by           VARCHAR(36) NOT NULL REFERENCES users(id),
    mapping_confirmed_at  TIMESTAMP,
    diff_confirmed_at     TIMESTAMP,
    created_at            TIMESTAMP NOT NULL,      -- (app) utcnow
    status                csv_upload_status NOT NULL DEFAULT 'received'  -- (app)
);

CREATE UNIQUE INDEX ix_csv_uploads_sha256_hash ON csv_uploads (sha256_hash);
CREATE INDEX ix_csv_uploads_id ON csv_uploads (id);


-- ---------------------------------------------------------------------------
-- 8. COLUMN MAPPINGS — src/parade_state/models/csv_ingestion.py
--    (ColumnMapping)
--    Global mapping: raw CSV column names → canonical app names.
--    canonical_name is globally unique. Deprecation is a timestamp,
--    not a boolean flag.
-- ---------------------------------------------------------------------------

CREATE TABLE column_mappings (
    id              VARCHAR(36) PRIMARY KEY,
    raw_name        VARCHAR(255) NOT NULL,
    canonical_name  VARCHAR(255) NOT NULL,         -- globally unique
    status          column_mapping_status NOT NULL DEFAULT 'auto_detected',  -- (app)
    created_at      TIMESTAMP NOT NULL,            -- (app) utcnow
    created_by      VARCHAR(36) REFERENCES users(id),
    confirmed_at    TIMESTAMP,
    confirmed_by    VARCHAR(36) REFERENCES users(id),
    deprecated_at   TIMESTAMP,                     -- null = active mapping
    notes           TEXT
);

CREATE UNIQUE INDEX ix_column_mappings_canonical_name ON column_mappings (canonical_name);
CREATE INDEX ix_column_mappings_raw_name ON column_mappings (raw_name);
CREATE INDEX ix_column_mappings_id ON column_mappings (id);


-- ---------------------------------------------------------------------------
-- 9. COLUMN METADATA — src/parade_state/models/csv_ingestion.py
--    (ColumnMetadata)
--    Per-NR column registry: original headers, canonical mapping,
--    inferred types, admin-assigned sensitivity level.
-- ---------------------------------------------------------------------------

CREATE TABLE column_metadata (
    id                    VARCHAR(36) PRIMARY KEY,
    nominal_roll_id       VARCHAR(36) NOT NULL REFERENCES nominal_rolls(id) ON DELETE CASCADE,
    csv_upload_id         VARCHAR(36) NOT NULL REFERENCES csv_uploads(id) ON DELETE CASCADE,
    original_name         VARCHAR(255) NOT NULL,   -- as it appeared in the CSV header
    canonical_name        VARCHAR(255),            -- null if unmapped
    inferred_type         column_data_type NOT NULL DEFAULT 'string',  -- (app)
    sensitivity_level_id  VARCHAR(36) REFERENCES access_levels(id),  -- null = admin-only
    is_required           BOOLEAN NOT NULL,        -- (app) default false
    created_at            TIMESTAMP NOT NULL,      -- (app) utcnow
    updated_at            TIMESTAMP NOT NULL,      -- (app) utcnow
    CONSTRAINT unique_nominal_roll_column UNIQUE (nominal_roll_id, original_name)
);

CREATE INDEX ix_column_metadata_id ON column_metadata (id);


-- ---------------------------------------------------------------------------
-- 10. PERSONNEL — src/parade_state/models/personnel.py (Personnel)
--     Individual personnel record on a nominal roll (one row per
--     nominal-roll-person pairing). pers_no is the external personnel
--     number from the CSV Pers column — the cross-roll person identity;
--     NULL when the CSV row omitted it, never an empty string.
--     extra_fields holds all non-canonical CSV columns (pers_no is
--     never stored there). source: NULL = CSV row, 'manual' =
--     super-admin "Add Serviceman" creation.
-- ---------------------------------------------------------------------------

CREATE TABLE personnel (
    id              VARCHAR(36) PRIMARY KEY,
    nominal_roll_id VARCHAR(36) NOT NULL REFERENCES nominal_rolls(id) ON DELETE CASCADE,
    pers_no         VARCHAR(20),                   -- external canonical id; nullable
    rank            VARCHAR(50) NOT NULL,
    category        personnel_category NOT NULL,
    full_name       VARCHAR(255) NOT NULL,
    unit            VARCHAR(255) NOT NULL,
    sub_unit_1      VARCHAR(255),
    sub_unit_2      VARCHAR(255),
    sub_unit_3      VARCHAR(255),
    extra_fields    JSON NOT NULL,                 -- (app) default {}; SQLite: TEXT
    status          personnel_status NOT NULL DEFAULT 'active',      -- (app)
    inpro_status    personnel_inpro_status NOT NULL DEFAULT 'yet_to_inpro',  -- (app)
    remarks         TEXT,
    source          VARCHAR(16),                   -- null = CSV, 'manual' = UI-added
    created_at      TIMESTAMP NOT NULL,            -- (app) utcnow
    created_by      VARCHAR(36) NOT NULL REFERENCES users(id),
    updated_at      TIMESTAMP,
    updated_by      VARCHAR(36) REFERENCES users(id),
    CONSTRAINT uq_personnel_nominal_roll_pers_no UNIQUE (nominal_roll_id, pers_no)
);

CREATE INDEX ix_personnel_nominal_roll_id ON personnel (nominal_roll_id);
CREATE INDEX ix_personnel_pers_no ON personnel (pers_no);
CREATE INDEX ix_personnel_rank ON personnel (rank);
CREATE INDEX ix_personnel_category ON personnel (category);
CREATE INDEX ix_personnel_full_name ON personnel (full_name);
CREATE INDEX ix_personnel_unit ON personnel (unit);
CREATE INDEX ix_personnel_status ON personnel (status);
CREATE INDEX ix_personnel_inpro_status ON personnel (inpro_status);
CREATE INDEX ix_personnel_updated_at ON personnel (updated_at);
CREATE INDEX ix_personnel_id ON personnel (id);


-- ---------------------------------------------------------------------------
-- 11. TAGGINGS + TAGGING ENTRIES — src/parade_state/models/tagging.py
--     The single overlay of person → subunit remappings on a nominal
--     roll (1:1, auto-created on ingestion, cascades with the NR).
--     Taggings never mutate the underlying NR; downstream consumers
--     (attendance / groupings / NR browser) apply the overlay to render
--     effective structure. from_* is an optional snapshot of the
--     person's canonical subunit; to_* is the remap target (to_unit
--     always required). One remap per person per tagging.
-- ---------------------------------------------------------------------------

CREATE TABLE taggings (
    id              VARCHAR(36) PRIMARY KEY,
    label           VARCHAR(100),                  -- optional, informational
    nominal_roll_id VARCHAR(36) NOT NULL REFERENCES nominal_rolls(id) ON DELETE CASCADE,
    remarks         TEXT,
    created_at      TIMESTAMP NOT NULL,            -- (app) utcnow
    created_by      VARCHAR(36) NOT NULL REFERENCES users(id),
    updated_at      TIMESTAMP,
    updated_by      VARCHAR(36) REFERENCES users(id),
    CONSTRAINT uq_taggings_nominal_roll_id UNIQUE (nominal_roll_id)  -- 1:1 with NR
);

CREATE UNIQUE INDEX ix_taggings_nominal_roll_id ON taggings (nominal_roll_id);
CREATE INDEX ix_taggings_label ON taggings (label);
CREATE INDEX ix_taggings_updated_at ON taggings (updated_at);
CREATE INDEX ix_taggings_id ON taggings (id);

CREATE TABLE tagging_entries (
    id              VARCHAR(36) PRIMARY KEY,
    tagging_id      VARCHAR(36) NOT NULL REFERENCES taggings(id) ON DELETE CASCADE,
    personnel_id    VARCHAR(36) NOT NULL REFERENCES personnel(id) ON DELETE CASCADE,
    from_unit       VARCHAR(255),
    from_sub_unit_1 VARCHAR(255),
    from_sub_unit_2 VARCHAR(255),
    from_sub_unit_3 VARCHAR(255),
    to_unit         VARCHAR(255) NOT NULL,
    to_sub_unit_1   VARCHAR(255),
    to_sub_unit_2   VARCHAR(255),
    to_sub_unit_3   VARCHAR(255),
    created_at      TIMESTAMP NOT NULL,            -- (app) utcnow
    CONSTRAINT uq_tagging_entry_person UNIQUE (tagging_id, personnel_id)
);

CREATE INDEX ix_tagging_entries_tagging_id ON tagging_entries (tagging_id);
CREATE INDEX ix_tagging_entries_personnel_id ON tagging_entries (personnel_id);
CREATE INDEX ix_tagging_entries_id ON tagging_entries (id);


-- ---------------------------------------------------------------------------
-- 12. ATTENDANCE — src/parade_state/models/attendance.py (Attendance,
--     AttendanceFreeze; issues 33/35)
--     Taken once daily against the NR active for attendance, always
--     with its tagging overlay applied. One row per (personnel, date);
--     no session table, no grouping coupling. reason never feeds
--     present/absent aggregation — status only.
--     attendance_freezes: row presence = the (NR, date) is frozen
--     (super-admin-writable, read-only for admins); unfreezing deletes
--     the row.
-- ---------------------------------------------------------------------------

CREATE TABLE attendance (
    id                   VARCHAR(36) PRIMARY KEY,
    personnel_id         VARCHAR(36) NOT NULL REFERENCES personnel(id) ON DELETE CASCADE,
    nominal_roll_id      VARCHAR(36) NOT NULL REFERENCES nominal_rolls(id) ON DELETE CASCADE,
    date                 DATE NOT NULL,
    status               attendance_status NOT NULL DEFAULT 'absent',  -- (app)
    reason               attendance_reason,            -- nullable; classifies remarks only
    remarks              TEXT,
    notes_snapshot       TEXT,                         -- roster snapshot at write time
    unit_snapshot        VARCHAR(255),
    sub_unit_1_snapshot  VARCHAR(255),
    sub_unit_2_snapshot  VARCHAR(255),
    sub_unit_3_snapshot  VARCHAR(255),
    created_at           TIMESTAMP NOT NULL,           -- (app) utcnow
    created_by           VARCHAR(36) NOT NULL REFERENCES users(id),
    updated_at           TIMESTAMP NOT NULL,           -- (app) utcnow
    updated_by           VARCHAR(36) NOT NULL REFERENCES users(id),
    last_edit_at         TIMESTAMP,
    last_edit_by         VARCHAR(36) REFERENCES users(id),
    is_retroactive_edit  BOOLEAN NOT NULL,             -- (app) default false
    CONSTRAINT uq_attendance_personnel_date UNIQUE (personnel_id, date)
);

CREATE INDEX ix_attendance_personnel_id ON attendance (personnel_id);
CREATE INDEX ix_attendance_nominal_roll_id ON attendance (nominal_roll_id);
CREATE INDEX ix_attendance_date ON attendance (date);
CREATE INDEX ix_attendance_id ON attendance (id);

CREATE TABLE attendance_freezes (
    id              VARCHAR(36) PRIMARY KEY,
    nominal_roll_id VARCHAR(36) NOT NULL REFERENCES nominal_rolls(id) ON DELETE CASCADE,
    date            DATE NOT NULL,
    created_at      TIMESTAMP NOT NULL,            -- (app) utcnow
    created_by      VARCHAR(36) NOT NULL REFERENCES users(id),
    CONSTRAINT uq_attendance_freezes_nr_date UNIQUE (nominal_roll_id, date)
);

CREATE INDEX ix_attendance_freezes_nominal_roll_id ON attendance_freezes (nominal_roll_id);
CREATE INDEX ix_attendance_freezes_date ON attendance_freezes (date);
CREATE INDEX ix_attendance_freezes_id ON attendance_freezes (id);


-- ---------------------------------------------------------------------------
-- 13. DEFERMENTS — src/parade_state/models/deferments.py (Deferment)
--     Deferment requests linked to personnel rows. rank_name and
--     sub_unit are snapshotted at creation so the record stays accurate
--     if the personnel row is later edited or the NR is superseded.
-- ---------------------------------------------------------------------------

CREATE TABLE deferments (
    id              VARCHAR(36) PRIMARY KEY,
    personnel_id    VARCHAR(36) NOT NULL REFERENCES personnel(id) ON DELETE CASCADE,
    rank_name       VARCHAR(255) NOT NULL,         -- snapshot
    sub_unit        VARCHAR(255),                  -- snapshot
    reason          deferment_reason NOT NULL,
    status          deferment_status NOT NULL DEFAULT 'Pending action',  -- (app)
    remarks         TEXT,
    oc_updates      TEXT,
    created_at      TIMESTAMP NOT NULL,            -- (app) utcnow
    created_by      VARCHAR(36) NOT NULL REFERENCES users(id),
    updated_at      TIMESTAMP,
    updated_by      VARCHAR(36) REFERENCES users(id)
);

CREATE INDEX ix_deferments_personnel_id ON deferments (personnel_id);
CREATE INDEX ix_deferments_status ON deferments (status);
CREATE INDEX ix_deferments_updated_at ON deferments (updated_at);
CREATE INDEX ix_deferments_id ON deferments (id);


-- ---------------------------------------------------------------------------
-- 14. GROUPINGS — src/parade_state/models/grouping.py (issue 26 redesign)
--     A Grouping is a labelled, closed vocabulary of groups
--     (grouping_groups) on one nominal roll. Servicemen hold
--     memberships plus a per-grouping checkbox/remarks row
--     (grouping_member_state). Groupings never read or write
--     attendance. multiple_membership / allow_ungrouped are immutable
--     after creation; the single-membership and no-ungrouped rules are
--     application-enforced (not expressible as plain constraints).
--     groupings.nominal_roll_id is FK RESTRICT: deleting an NR with
--     groupings is refused. Labels are unique per roll, not globally.
-- ---------------------------------------------------------------------------

CREATE TABLE groupings (
    id                  VARCHAR(36) PRIMARY KEY,
    label               VARCHAR(100) NOT NULL,
    nominal_roll_id     VARCHAR(36) NOT NULL REFERENCES nominal_rolls(id) ON DELETE RESTRICT,
    multiple_membership BOOLEAN NOT NULL,          -- (app) default false
    allow_ungrouped     BOOLEAN NOT NULL,          -- (app) default true
    created_at          TIMESTAMP NOT NULL,        -- (app) utcnow
    created_by          VARCHAR(36) NOT NULL REFERENCES users(id),
    CONSTRAINT uq_groupings_nr_label UNIQUE (nominal_roll_id, label)
);

CREATE INDEX ix_groupings_label ON groupings (label);
CREATE INDEX ix_groupings_nominal_roll_id ON groupings (nominal_roll_id);
CREATE INDEX ix_groupings_id ON groupings (id);

CREATE TABLE grouping_groups (
    id              VARCHAR(36) PRIMARY KEY,
    grouping_id     VARCHAR(36) NOT NULL REFERENCES groupings(id) ON DELETE CASCADE,
    label           VARCHAR(100) NOT NULL,
    position        INTEGER NOT NULL,              -- (app) default 0; manual display order
    CONSTRAINT uq_grouping_group_label UNIQUE (grouping_id, label)
);

CREATE INDEX ix_grouping_groups_grouping_id ON grouping_groups (grouping_id);
CREATE INDEX ix_grouping_groups_id ON grouping_groups (id);

CREATE TABLE grouping_memberships (
    id              VARCHAR(36) PRIMARY KEY,
    grouping_id     VARCHAR(36) NOT NULL REFERENCES groupings(id) ON DELETE CASCADE,
    group_id        VARCHAR(36) NOT NULL REFERENCES grouping_groups(id) ON DELETE CASCADE,
    personnel_id    VARCHAR(36) NOT NULL REFERENCES personnel(id) ON DELETE CASCADE,
    CONSTRAINT uq_grouping_membership UNIQUE (grouping_id, personnel_id, group_id)
);

CREATE INDEX ix_grouping_memberships_grouping_id ON grouping_memberships (grouping_id);
CREATE INDEX ix_grouping_memberships_group_id ON grouping_memberships (group_id);
CREATE INDEX ix_grouping_memberships_personnel_id ON grouping_memberships (personnel_id);
CREATE INDEX ix_grouping_memberships_id ON grouping_memberships (id);

CREATE TABLE grouping_member_state (
    id              VARCHAR(36) PRIMARY KEY,
    grouping_id     VARCHAR(36) NOT NULL REFERENCES groupings(id) ON DELETE CASCADE,
    personnel_id    VARCHAR(36) NOT NULL REFERENCES personnel(id) ON DELETE CASCADE,
    checkbox        BOOLEAN NOT NULL,              -- (app) default false
    remarks         TEXT,
    updated_at      TIMESTAMP NOT NULL,            -- (app) utcnow
    updated_by      VARCHAR(36) NOT NULL REFERENCES users(id),
    CONSTRAINT uq_grouping_member_state UNIQUE (grouping_id, personnel_id)
);

CREATE INDEX ix_grouping_member_state_grouping_id ON grouping_member_state (grouping_id);
CREATE INDEX ix_grouping_member_state_personnel_id ON grouping_member_state (personnel_id);
CREATE INDEX ix_grouping_member_state_id ON grouping_member_state (id);


-- ---------------------------------------------------------------------------
-- 15. DISCUSSIONS — src/parade_state/models/discussions.py (issue 24)
--     In-app board: admins/super-admins post requests/bugs items and
--     discuss them in markdown comments. Super-admins triage posts
--     (status). Visible to admins only, never regular users.
--     edited_at flips from NULL on the author's first edit and then
--     tracks the last edit; full edit history is intentionally not kept.
-- ---------------------------------------------------------------------------

CREATE TABLE discussion_posts (
    id              VARCHAR(36) PRIMARY KEY,
    title           VARCHAR(200) NOT NULL,
    body            TEXT NOT NULL,
    author_id       VARCHAR(36) NOT NULL REFERENCES users(id),
    category        discussion_category NOT NULL,
    status          discussion_post_status NOT NULL DEFAULT 'Open',  -- (app)
    created_at      TIMESTAMP NOT NULL,            -- (app) utcnow
    edited_at       TIMESTAMP
);

CREATE INDEX ix_discussion_posts_author_id ON discussion_posts (author_id);
CREATE INDEX ix_discussion_posts_category ON discussion_posts (category);
CREATE INDEX ix_discussion_posts_status ON discussion_posts (status);
CREATE INDEX ix_discussion_posts_id ON discussion_posts (id);

CREATE TABLE discussion_comments (
    id              VARCHAR(36) PRIMARY KEY,
    post_id         VARCHAR(36) NOT NULL REFERENCES discussion_posts(id) ON DELETE CASCADE,
    author_id       VARCHAR(36) NOT NULL REFERENCES users(id),
    body            TEXT NOT NULL,
    created_at      TIMESTAMP NOT NULL,            -- (app) utcnow
    edited_at       TIMESTAMP
);

CREATE INDEX ix_discussion_comments_post_id ON discussion_comments (post_id);
CREATE INDEX ix_discussion_comments_author_id ON discussion_comments (author_id);
CREATE INDEX ix_discussion_comments_id ON discussion_comments (id);


-- ---------------------------------------------------------------------------
-- 16. AUDIT LOGS — src/parade_state/models/audit.py (AuditLog)
--     Sequential append-only log of all system changes. user_id null
--     for system/background actions. changes holds a diff (null when
--     not applicable); description is always present. ip_address is a
--     plain string, not INET.
-- ---------------------------------------------------------------------------

CREATE TABLE audit_logs (
    id              VARCHAR(36) PRIMARY KEY,
    timestamp       TIMESTAMP NOT NULL,            -- (app) utcnow
    user_id         VARCHAR(36) REFERENCES users(id),
    entity_type     audit_entity_type NOT NULL,
    entity_id       VARCHAR(36) NOT NULL,
    action          audit_action NOT NULL,
    changes         TEXT,
    description     TEXT NOT NULL,
    ip_address      VARCHAR(45)
);

CREATE INDEX ix_audit_logs_timestamp ON audit_logs (timestamp);
CREATE INDEX ix_audit_logs_entity_type ON audit_logs (entity_type);
CREATE INDEX ix_audit_logs_entity_id ON audit_logs (entity_id);
CREATE INDEX ix_audit_logs_id ON audit_logs (id);
