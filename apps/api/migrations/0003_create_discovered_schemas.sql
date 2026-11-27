-- Forward-only migration creating persistent discovered schema snapshot, object, and field tables.
-- Stores non-secret structural metadata only; uploaded file contents and sampled PII rows are never persisted.

CREATE TABLE IF NOT EXISTS discovered_schemas (
    id UUID PRIMARY KEY,
    connection_id UUID NOT NULL UNIQUE REFERENCES connections (id) ON DELETE CASCADE,
    discovery_status VARCHAR(24) NOT NULL CHECK (
        discovery_status IN ('completed', 'partial')
    ),
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    schema_fingerprint VARCHAR(64) NOT NULL CHECK (
        char_length(schema_fingerprint) = 64
        AND schema_fingerprint ~ '^[0-9a-f]{64}$'
    ),
    discovery_source VARCHAR(32) NOT NULL CHECK (
        discovery_source IN ('catalog', 'openapi', 'inferred_sample', 'manual', 'mixed')
    ),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_discovered_schemas_fingerprint
    ON discovered_schemas (schema_fingerprint);

CREATE TABLE IF NOT EXISTS schema_objects (
    id UUID PRIMARY KEY,
    discovered_schema_id UUID NOT NULL REFERENCES discovered_schemas (id) ON DELETE CASCADE,
    object_key VARCHAR(512) NOT NULL CHECK (char_length(btrim(object_key)) > 0),
    object_name VARCHAR(255) NOT NULL CHECK (char_length(btrim(object_name)) > 0),
    object_type VARCHAR(32) NOT NULL CHECK (
        object_type IN ('table', 'view', 'collection', 'endpoint', 'file', 'record_set')
    ),
    discovery_source VARCHAR(32) NOT NULL CHECK (
        discovery_source IN ('catalog', 'openapi', 'inferred_sample', 'manual')
    ),
    native_metadata JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (
        jsonb_typeof(native_metadata) = 'object'
    ),
    canonical_metadata JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (
        jsonb_typeof(canonical_metadata) = 'object'
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_schema_objects_schema_object_key UNIQUE (discovered_schema_id, object_key)
);

CREATE INDEX IF NOT EXISTS ix_schema_objects_schema_type
    ON schema_objects (discovered_schema_id, object_type);

CREATE INDEX IF NOT EXISTS ix_schema_objects_schema_name
    ON schema_objects (discovered_schema_id, object_name);

CREATE TABLE IF NOT EXISTS schema_fields (
    id UUID PRIMARY KEY,
    schema_object_id UUID NOT NULL REFERENCES schema_objects (id) ON DELETE CASCADE,
    field_path VARCHAR(512) NOT NULL CHECK (char_length(btrim(field_path)) > 0),
    field_name VARCHAR(255) NOT NULL CHECK (char_length(btrim(field_name)) > 0),
    native_type VARCHAR(128) NOT NULL CHECK (char_length(btrim(native_type)) > 0),
    canonical_type VARCHAR(32) NOT NULL CHECK (
        canonical_type IN (
            'string',
            'integer',
            'number',
            'boolean',
            'date',
            'datetime',
            'object',
            'array',
            'binary',
            'json',
            'null',
            'mixed',
            'unknown'
        )
    ),
    nullable BOOLEAN NOT NULL DEFAULT TRUE,
    is_required BOOLEAN NOT NULL DEFAULT FALSE,
    primary_key BOOLEAN NOT NULL DEFAULT FALSE,
    unique_key BOOLEAN NOT NULL DEFAULT FALSE,
    default_metadata JSONB NULL CHECK (
        default_metadata IS NULL OR jsonb_typeof(default_metadata) = 'object'
    ),
    ordinal_position INTEGER NOT NULL CHECK (ordinal_position >= 0),
    discovery_source VARCHAR(32) NOT NULL CHECK (
        discovery_source IN ('catalog', 'openapi', 'inferred_sample', 'manual')
    ),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_schema_fields_object_path UNIQUE (schema_object_id, field_path)
);

CREATE INDEX IF NOT EXISTS ix_schema_fields_object_ordinal
    ON schema_fields (schema_object_id, ordinal_position);

CREATE INDEX IF NOT EXISTS ix_schema_fields_object_canonical_type
    ON schema_fields (schema_object_id, canonical_type);
