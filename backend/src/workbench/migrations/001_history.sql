CREATE TABLE history_entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    language        TEXT NOT NULL CHECK (language IN ('fr', 'ja', 'en')),
    original_text   TEXT NOT NULL COLLATE BINARY
                    CHECK (length(original_text) BETWEEN 1 AND 500)
                    CHECK (instr(original_text, char(0)) = 0),
    created_at_ms   INTEGER NOT NULL CHECK (created_at_ms >= 0),
    UNIQUE (language, original_text)
) STRICT;

CREATE TABLE analysis_snapshots (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id              INTEGER NOT NULL
                          REFERENCES history_entries(id) ON DELETE CASCADE,
    analysis_id           TEXT NOT NULL UNIQUE CHECK (length(analysis_id) BETWEEN 1 AND 128),
    schema_version        TEXT NOT NULL CHECK (length(schema_version) BETWEEN 1 AND 32),
    status                TEXT NOT NULL CHECK (status IN ('complete', 'partial')),
    enrichment_requested  TEXT NOT NULL CHECK (enrichment_requested IN ('on', 'off')),
    origin                TEXT NOT NULL CHECK (origin IN ('app', 'browser_import')),
    observed_at_ms        INTEGER NOT NULL CHECK (observed_at_ms >= 0),
    stored_at_ms          INTEGER NOT NULL CHECK (stored_at_ms >= 0),
    result_json           TEXT NOT NULL
                          CHECK (length(CAST(result_json AS BLOB)) <= 262144)
                          CHECK (json_valid(result_json))
                          CHECK (json_type(result_json) IS 'object'),
    CHECK (json_extract(result_json, '$.analysis_id') IS analysis_id),
    CHECK (json_extract(result_json, '$.schema_version') IS schema_version),
    CHECK (json_extract(result_json, '$.status') IS status)
) STRICT;

-- Latest snapshot for one entry, and its version list.
CREATE INDEX snapshots_entry_recent
    ON analysis_snapshots(entry_id, observed_at_ms DESC, id DESC);

-- Global history ordering / keyset pagination.
CREATE INDEX snapshots_recent
    ON analysis_snapshots(observed_at_ms DESC, id DESC);

PRAGMA user_version = 1;
