PRAGMA user_version = 1;
PRAGMA foreign_keys = ON;

CREATE TABLE package (
    release_id TEXT PRIMARY KEY, tool_version TEXT NOT NULL,
    source_manifest TEXT NOT NULL, schema_sha256 TEXT NOT NULL
);
CREATE TABLE source (
    id TEXT PRIMARY KEY, homepage TEXT NOT NULL, reuse TEXT NOT NULL
);
CREATE TABLE source_dependency (
    source_id TEXT NOT NULL REFERENCES source, upstream_id TEXT NOT NULL REFERENCES source,
    scope TEXT NOT NULL, PRIMARY KEY (source_id, upstream_id, scope)
);
CREATE TABLE source_artifact (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES source, url TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64), byte_count INTEGER NOT NULL,
    observed_on TEXT, upstream_version TEXT, parser_version TEXT NOT NULL
);
CREATE TABLE song (id TEXT PRIMARY KEY, identity_basis TEXT NOT NULL);
CREATE TABLE song_name (
    song_id TEXT NOT NULL REFERENCES song, name TEXT NOT NULL, normalized TEXT NOT NULL,
    artifact_id TEXT NOT NULL REFERENCES source_artifact, locator TEXT NOT NULL,
    PRIMARY KEY (song_id, name, artifact_id)
);
CREATE INDEX names ON song_name (normalized);
CREATE TABLE chart (
    id TEXT PRIMARY KEY, song_id TEXT NOT NULL REFERENCES song,
    difficulty TEXT NOT NULL CHECK (difficulty IN ('BAS','ADV','EXP','MAS','ULT','WE')),
    variant TEXT NOT NULL, UNIQUE (song_id, difficulty, variant)
);
CREATE TABLE chart_revision (
    id TEXT PRIMARY KEY, chart_id TEXT NOT NULL REFERENCES chart,
    identity_basis TEXT NOT NULL, fingerprint TEXT, metadata TEXT NOT NULL
);
CREATE TABLE external_id (
    artifact_id TEXT NOT NULL REFERENCES source_artifact, region TEXT NOT NULL,
    external_song_key TEXT NOT NULL, external_chart_key TEXT NOT NULL,
    song_id TEXT NOT NULL REFERENCES song, revision_id TEXT REFERENCES chart_revision,
    observed_on TEXT, status TEXT NOT NULL, locator TEXT NOT NULL,
    PRIMARY KEY (artifact_id, region, external_song_key, external_chart_key, locator)
);
CREATE INDEX external_key ON external_id (region, external_song_key, external_chart_key);
CREATE TABLE chart_mapping (
    source_revision_id TEXT NOT NULL REFERENCES chart_revision,
    target_revision_id TEXT NOT NULL REFERENCES chart_revision,
    source_artifact_id TEXT NOT NULL REFERENCES source_artifact,
    target_artifact_id TEXT NOT NULL REFERENCES source_artifact,
    status TEXT NOT NULL CHECK (status IN ('candidate','conflict','legacy_pinned')),
    evidence TEXT NOT NULL,
    PRIMARY KEY (source_revision_id, target_revision_id, source_artifact_id, target_artifact_id)
);
CREATE TABLE ruleset (
    id TEXT PRIMARY KEY, format TEXT NOT NULL, status TEXT NOT NULL,
    parameters TEXT NOT NULL, artifact_id TEXT NOT NULL REFERENCES source_artifact
);
CREATE TABLE region_release (
    region TEXT NOT NULL CHECK (region IN ('JP','INTL','CN')), version TEXT NOT NULL,
    start_on TEXT NOT NULL, end_before TEXT, timezone TEXT NOT NULL,
    through_on TEXT, ruleset_id TEXT REFERENCES ruleset,
    kind TEXT NOT NULL CHECK (kind IN ('history','observation')),
    artifact_id TEXT NOT NULL REFERENCES source_artifact,
    locator TEXT NOT NULL,
    CHECK (end_before IS NULL OR start_on < end_before),
    CHECK (through_on IS NULL OR (through_on >= start_on AND (end_before IS NULL OR through_on < end_before))),
    PRIMARY KEY (region, version)
);
CREATE TABLE observation (
    id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL REFERENCES source_artifact,
    locator TEXT NOT NULL, region TEXT NOT NULL, song_id TEXT NOT NULL REFERENCES song,
    revision_id TEXT REFERENCES chart_revision, field TEXT NOT NULL,
    raw_json TEXT NOT NULL, value_json TEXT NOT NULL,
    effective_on TEXT, effective_before TEXT,
    grain TEXT NOT NULL CHECK (grain IN ('day','version','point','unknown')),
    quality TEXT NOT NULL CHECK (quality IN ('A','B','C','D','Missing')),
    value_kind TEXT NOT NULL,
    CHECK (effective_before IS NULL OR effective_on IS NULL OR effective_on < effective_before)
);
CREATE INDEX observation_entity ON observation (song_id, revision_id, region, field, effective_on);
CREATE TABLE conflict (
    id TEXT PRIMARY KEY, region TEXT NOT NULL, version TEXT NOT NULL,
    revision_id TEXT REFERENCES chart_revision, field TEXT NOT NULL,
    on_date TEXT, reason TEXT NOT NULL, status TEXT NOT NULL CHECK (status IN ('open','resolved'))
);
CREATE TABLE conflict_observation (
    conflict_id TEXT NOT NULL REFERENCES conflict, observation_id TEXT NOT NULL REFERENCES observation,
    PRIMARY KEY (conflict_id, observation_id)
);
CREATE TABLE conflict_ruleset (
    conflict_id TEXT NOT NULL REFERENCES conflict, ruleset_id TEXT NOT NULL REFERENCES ruleset,
    PRIMARY KEY (conflict_id, ruleset_id)
);
CREATE TABLE resolution (
    conflict_id TEXT PRIMARY KEY REFERENCES conflict,
    selected_observation TEXT REFERENCES observation,
    artifact_id TEXT NOT NULL REFERENCES source_artifact, reason TEXT NOT NULL
);
CREATE TABLE resolved_event (
    id TEXT PRIMARY KEY, observation_id TEXT NOT NULL REFERENCES observation,
    revision_id TEXT NOT NULL REFERENCES chart_revision, region TEXT NOT NULL,
    valid_from TEXT NOT NULL, valid_to TEXT,
    kind TEXT NOT NULL CHECK (kind IN ('exact','estimated','conflict')),
    constant_tenths INTEGER, conflict_id TEXT REFERENCES conflict,
    CHECK (valid_to IS NULL OR valid_from < valid_to),
    CHECK ((kind = 'conflict' AND constant_tenths IS NULL AND conflict_id IS NOT NULL)
        OR (kind <> 'conflict' AND constant_tenths IS NOT NULL
            AND constant_tenths BETWEEN 1 AND 200 AND conflict_id IS NULL))
);
CREATE INDEX chart_constant ON resolved_event (region, revision_id, valid_from);
CREATE TABLE region_song_state (
    region TEXT NOT NULL, song_id TEXT NOT NULL REFERENCES song,
    valid_from TEXT NOT NULL, valid_to TEXT,
    state TEXT NOT NULL CHECK (state IN ('playable','removed','suspended','unknown','not_released','disabled')),
    observation_id TEXT NOT NULL REFERENCES observation,
    CHECK (valid_to IS NULL OR valid_from < valid_to),
    PRIMARY KEY (region, song_id, valid_from)
);
CREATE TABLE region_chart_state (
    region TEXT NOT NULL, revision_id TEXT NOT NULL REFERENCES chart_revision,
    valid_from TEXT NOT NULL, valid_to TEXT,
    state TEXT NOT NULL CHECK (state IN ('playable','unknown','not_released','disabled','removed','suspended')),
    observation_id TEXT NOT NULL REFERENCES observation,
    level_display TEXT,
    CHECK (valid_to IS NULL OR valid_from < valid_to),
    PRIMARY KEY (region, revision_id, valid_from)
);
CREATE TABLE snapshot (
    id TEXT PRIMARY KEY, region TEXT NOT NULL, version TEXT NOT NULL, on_date TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('version_end','observation','as_of')),
    rating_status TEXT NOT NULL, coverage_json TEXT NOT NULL,
    FOREIGN KEY (region, version) REFERENCES region_release, UNIQUE (region, version, on_date)
);
CREATE TABLE snapshot_chart (
    snapshot_id TEXT NOT NULL REFERENCES snapshot, revision_id TEXT NOT NULL REFERENCES chart_revision,
    chart_from TEXT NOT NULL, song_from TEXT NOT NULL, event_id TEXT REFERENCES resolved_event,
    PRIMARY KEY (snapshot_id, revision_id)
) WITHOUT ROWID;
CREATE TABLE jacket_asset (
    id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL REFERENCES source_artifact,
    external_key TEXT NOT NULL, url TEXT, format TEXT, width INTEGER, height INTEGER,
    sha256 TEXT CHECK (sha256 IS NULL OR length(sha256) = 64),
    reuse TEXT NOT NULL, locator TEXT NOT NULL
);
CREATE TABLE song_jacket (
    song_id TEXT NOT NULL REFERENCES song, asset_id TEXT NOT NULL REFERENCES jacket_asset,
    region TEXT NOT NULL, observed_on TEXT, PRIMARY KEY (song_id, asset_id, region)
);
