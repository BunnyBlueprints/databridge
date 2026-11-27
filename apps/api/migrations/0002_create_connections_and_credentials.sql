-- Forward-only migration creating persistent connection metadata and encrypted credential vault tables.

CREATE TABLE IF NOT EXISTS connections (
    id UUID PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    connector_type VARCHAR(32) NOT NULL CHECK (
        connector_type IN (
            'PostgreSQL',
            'MySQL',
            'MongoDB',
            'REST API',
            'CSV',
            'JSON'
        )
    ),
    description TEXT NULL,
    endpoint VARCHAR(512) NOT NULL,
    config JSONB NOT NULL CHECK (jsonb_typeof(config) = 'object'),
    auth_configured BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(24) NOT NULL DEFAULT 'Disconnected' CHECK (
        status IN ('Connected', 'Disconnected', 'Error')
    ),
    last_tested_at TIMESTAMPTZ NULL,
    last_test_status VARCHAR(24) NULL CHECK (
        last_test_status IS NULL OR last_test_status IN ('connected', 'failed', 'timeout')
    ),
    last_error_message TEXT NULL,
    last_successful_sync TIMESTAMPTZ NULL,
    sync_frequency VARCHAR(64) NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_connections_lower_name
    ON connections (LOWER(name));

CREATE INDEX IF NOT EXISTS ix_connections_status
    ON connections (status);

CREATE INDEX IF NOT EXISTS ix_connections_connector_type
    ON connections (connector_type);

CREATE INDEX IF NOT EXISTS ix_connections_created_at_id
    ON connections (created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS connection_credentials (
    connection_id UUID PRIMARY KEY REFERENCES connections (id) ON DELETE CASCADE,
    ciphertext BYTEA NOT NULL CHECK (octet_length(ciphertext) >= 16),
    nonce BYTEA NOT NULL CHECK (octet_length(nonce) = 12),
    key_id VARCHAR(32) NOT NULL CHECK (char_length(key_id) > 0),
    algorithm VARCHAR(32) NOT NULL DEFAULT 'AES-256-GCM' CHECK (algorithm = 'AES-256-GCM'),
    format_version INTEGER NOT NULL DEFAULT 1 CHECK (format_version >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
