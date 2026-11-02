CREATE TABLE user_translations (
    entry_id INTEGER NOT NULL REFERENCES history_entries(id) ON DELETE CASCADE,
    span_start INTEGER NOT NULL CHECK (span_start >= 0),
    span_end INTEGER NOT NULL CHECK (span_end > span_start),
    text TEXT NOT NULL CHECK (length(text) BETWEEN 1 AND 4000),
    PRIMARY KEY (entry_id, span_start, span_end)
) STRICT;

PRAGMA user_version = 2;
