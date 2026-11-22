PRAGMA user_version = 3;
CREATE TABLE sdhd_containers (
    version TEXT REFERENCES versions, path TEXT, bytes INTEGER NOT NULL, sha256 TEXT,
    PRIMARY KEY(version,path)
);
CREATE TABLE sdhd_layers (
    version TEXT REFERENCES versions, package TEXT, container TEXT, data_version TEXT,
    build_on TEXT, files INTEGER, music_count INTEGER, status TEXT NOT NULL,
    PRIMARY KEY(version,package,container)
);
CREATE TABLE sdhd_issues (
    version TEXT REFERENCES versions, source TEXT, kind TEXT NOT NULL, detail TEXT NOT NULL
);
CREATE TABLE sdhd_observed_ids (
    batch TEXT REFERENCES history_batches, music_id INTEGER, identity TEXT REFERENCES history_entities,
    source TEXT REFERENCES sources, PRIMARY KEY(batch,music_id,identity,source)
);
CREATE INDEX sdhd_alias_lookup ON sdhd_observed_ids(music_id,identity);
CREATE TABLE sdhd_release_evidence (
    identity TEXT REFERENCES history_entities, day TEXT, locator TEXT, reference TEXT,
    PRIMARY KEY(identity,day,locator)
);
CREATE TABLE sdhd_first_release (
    identity TEXT PRIMARY KEY REFERENCES history_entities, earliest_on TEXT, latest_on TEXT,
    exact_on TEXT, basis TEXT NOT NULL
);

CREATE TABLE sdhd_observation_resources (
    batch TEXT NOT NULL, identity TEXT NOT NULL, source TEXT NOT NULL,
    path TEXT NOT NULL REFERENCES sources,
    PRIMARY KEY(batch,identity,source),
    FOREIGN KEY(batch,identity,source) REFERENCES history_observations
);
