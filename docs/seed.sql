-- =============================================================================
-- Bootstrap Seed
-- Run once after schema creation, before first app startup.
-- Super-admin user is NOT seeded here — it is created on first Google sign-in
-- when the email matches SUPER_ADMIN_EMAIL env var.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- Default access levels
-- Adjust labels and ordering to match your unit's terminology before deploying.
-- level_order: higher = broader access.
-- ---------------------------------------------------------------------------

INSERT INTO access_levels (label, level_order) VALUES
    ('section',  10),
    ('platoon',  20),
    ('coy',      30),
    ('unit',     40)
ON CONFLICT (label) DO NOTHING;


-- ---------------------------------------------------------------------------
-- Default app settings
-- ---------------------------------------------------------------------------

INSERT INTO app_settings (key, value, description) VALUES
    ('default_session_type', 'AM',
     'Default attendance session type shown on load. AM or PM.'),
    ('deployment_overlap_strict', 'true',
     'Whether to hard-reject deployment date range edits that would overlap an existing deployment.')
ON CONFLICT (key) DO NOTHING;
