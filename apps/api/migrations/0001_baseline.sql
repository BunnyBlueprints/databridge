-- Baseline forward-only migration establishing schema_migrations metadata.
-- Subsequent migrations will create application domain tables.

COMMENT ON TABLE schema_migrations IS 'Tracks forward-only DataBridge database schema migrations.';
