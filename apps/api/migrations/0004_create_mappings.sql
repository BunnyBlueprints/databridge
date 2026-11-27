-- Forward-only migration creating persistent field mapping configurations and ordered mapping rules.
-- Referenced connections are protected with ON DELETE RESTRICT; rules cascade on mapping delete.

CREATE TABLE IF NOT EXISTS mappings (
    id UUID PRIMARY KEY,
    name VARCHAR(120) NOT NULL CHECK (char_length(btrim(name)) > 0),
    description TEXT NULL,
    source_connection_id UUID NOT NULL REFERENCES connections (id) ON DELETE RESTRICT,
    source_object_key VARCHAR(512) NOT NULL CHECK (char_length(btrim(source_object_key)) > 0),
    destination_connection_id UUID NOT NULL REFERENCES connections (id) ON DELETE RESTRICT,
    destination_object_key VARCHAR(512) NOT NULL CHECK (
        char_length(btrim(destination_object_key)) > 0
    ),
    status VARCHAR(24) NOT NULL DEFAULT 'draft' CHECK (
        status IN ('draft', 'valid', 'invalid')
    ),
    mapping_version INTEGER NOT NULL DEFAULT 1 CHECK (mapping_version >= 1),
    source_schema_fingerprint VARCHAR(64) NULL CHECK (
        source_schema_fingerprint IS NULL
        OR (
            char_length(source_schema_fingerprint) = 64
            AND source_schema_fingerprint ~ '^[0-9a-f]{64}$'
        )
    ),
    destination_schema_fingerprint VARCHAR(64) NULL CHECK (
        destination_schema_fingerprint IS NULL
        OR (
            char_length(destination_schema_fingerprint) = 64
            AND destination_schema_fingerprint ~ '^[0-9a-f]{64}$'
        )
    ),
    validation_summary JSONB NULL CHECK (
        validation_summary IS NULL OR jsonb_typeof(validation_summary) = 'object'
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_mappings_lower_name
    ON mappings (LOWER(name));

CREATE INDEX IF NOT EXISTS ix_mappings_source_connection
    ON mappings (source_connection_id);

CREATE INDEX IF NOT EXISTS ix_mappings_destination_connection
    ON mappings (destination_connection_id);

CREATE INDEX IF NOT EXISTS ix_mappings_status
    ON mappings (status);

CREATE INDEX IF NOT EXISTS ix_mappings_created_at_id
    ON mappings (created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS mapping_rules (
    id UUID PRIMARY KEY,
    mapping_id UUID NOT NULL REFERENCES mappings (id) ON DELETE CASCADE,
    source_field_path VARCHAR(512) NULL,
    destination_field_path VARCHAR(512) NOT NULL CHECK (
        char_length(btrim(destination_field_path)) > 0
    ),
    mapping_type VARCHAR(24) NOT NULL CHECK (
        mapping_type IN ('direct', 'constant', 'transform')
    ),
    constant_value JSONB NULL,
    transform_ref JSONB NULL CHECK (
        transform_ref IS NULL OR jsonb_typeof(transform_ref) = 'object'
    ),
    ordinal_position INTEGER NOT NULL CHECK (ordinal_position >= 0),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_mapping_rules_destination UNIQUE (mapping_id, destination_field_path),
    CONSTRAINT uq_mapping_rules_ordinal UNIQUE (mapping_id, ordinal_position),
    CONSTRAINT ck_mapping_rules_shape CHECK (
        (
            mapping_type = 'direct'
            AND source_field_path IS NOT NULL
            AND char_length(btrim(source_field_path)) > 0
            AND constant_value IS NULL
            AND transform_ref IS NULL
        )
        OR (
            mapping_type = 'constant'
            AND source_field_path IS NULL
            AND constant_value IS NOT NULL
            AND transform_ref IS NULL
        )
        OR (
            mapping_type = 'transform'
            AND source_field_path IS NOT NULL
            AND char_length(btrim(source_field_path)) > 0
            AND transform_ref IS NOT NULL
        )
    )
);
