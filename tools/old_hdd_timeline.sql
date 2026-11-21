PRAGMA user_version = 2;

CREATE INDEX history_source_hash ON sources(sha256, kind);
CREATE TABLE history_entities (
    identity TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('song','chart','we')),
    parent TEXT REFERENCES history_entities, music_id INTEGER NOT NULL, slot INTEGER, difficulty TEXT
);
CREATE INDEX history_music ON history_entities(music_id, kind);
CREATE TABLE history_identity_links (
    identity TEXT NOT NULL REFERENCES history_entities,
    related TEXT NOT NULL REFERENCES history_entities,
    kind TEXT NOT NULL, source TEXT NOT NULL REFERENCES sources, related_source TEXT REFERENCES sources,
    evidence TEXT NOT NULL, PRIMARY KEY(identity,related,kind)
);
CREATE INDEX history_identity_related ON history_identity_links(related);
CREATE TABLE history_revisions (
    revision TEXT PRIMARY KEY, identity TEXT NOT NULL REFERENCES history_entities,
    payload TEXT NOT NULL
);
CREATE TABLE history_batches (
    batch TEXT PRIMARY KEY, version TEXT REFERENCES versions,
    kind TEXT NOT NULL CHECK(kind IN ('package','public_day','checkpoint')),
    effective_on TEXT, earliest_on TEXT, latest_on TEXT,
    timing TEXT NOT NULL CHECK(timing IN ('exact','range','batch_only','unknown')),
    sequence INTEGER, evidence TEXT NOT NULL
);
CREATE INDEX history_batch_date ON history_batches(effective_on);
CREATE TABLE history_observations (
    batch TEXT NOT NULL REFERENCES history_batches, identity TEXT NOT NULL REFERENCES history_entities,
    revision TEXT NOT NULL REFERENCES history_revisions, source TEXT NOT NULL REFERENCES sources,
    state TEXT NOT NULL, basis TEXT NOT NULL,
    PRIMARY KEY(batch,identity,source)
);
CREATE INDEX history_observed_entity ON history_observations(identity,batch);
CREATE TABLE history_events (
    event INTEGER PRIMARY KEY, identity TEXT NOT NULL REFERENCES history_entities,
    batch TEXT NOT NULL REFERENCES history_batches, kind TEXT NOT NULL, field TEXT NOT NULL,
    before TEXT, after TEXT, old_source TEXT REFERENCES sources, new_source TEXT REFERENCES sources,
    earliest_on TEXT, latest_on TEXT, effective_on TEXT, evidence TEXT NOT NULL,
    CHECK(effective_on IS NULL OR (effective_on = earliest_on AND effective_on = latest_on))
);
CREATE INDEX history_event_entity ON history_events(identity,event);
CREATE INDEX history_event_batch ON history_events(batch,event);
CREATE INDEX history_event_date ON history_events(effective_on,event);
CREATE INDEX history_event_range ON history_events(earliest_on,latest_on) WHERE effective_on IS NULL;
CREATE TABLE history_windows (
    version TEXT NOT NULL REFERENCES versions, identity TEXT NOT NULL REFERENCES history_entities,
    start_on TEXT NOT NULL, end_on TEXT NOT NULL, availability TEXT NOT NULL,
    revision TEXT REFERENCES history_revisions, candidates TEXT NOT NULL,
    basis TEXT NOT NULL, PRIMARY KEY(version, identity, start_on)
);
CREATE INDEX history_window_day ON history_windows(version,start_on,end_on);

CREATE TABLE history_game_events (
    source TEXT PRIMARY KEY REFERENCES sources, event_id INTEGER, title TEXT, type TEXT,
    always_open INTEGER, music_type TEXT, banner_id INTEGER
);
CREATE TABLE history_game_event_links (
    source TEXT NOT NULL REFERENCES history_game_events, role TEXT NOT NULL, target_id INTEGER NOT NULL,
    label TEXT, PRIMARY KEY(source,role,target_id)
);
CREATE INDEX history_game_link_target ON history_game_event_links(role,target_id);
CREATE TABLE history_game_event_changes (
    batch TEXT NOT NULL REFERENCES history_batches, event_id INTEGER NOT NULL,
    field TEXT NOT NULL, before TEXT, after TEXT,
    old_source TEXT REFERENCES history_game_events, new_source TEXT NOT NULL REFERENCES history_game_events,
    PRIMARY KEY(batch,event_id,field,new_source)
);
CREATE TABLE history_assets (
    asset TEXT PRIMARY KEY, kind TEXT NOT NULL,
    CHECK(kind IN ('hdd_game_banner','hdd_game_notice','official_publicity'))
);
CREATE TABLE history_asset_links (
    asset TEXT NOT NULL REFERENCES history_assets, location TEXT NOT NULL, relation TEXT NOT NULL,
    source TEXT NOT NULL, batch TEXT REFERENCES history_batches, published_on TEXT, title TEXT,
    PRIMARY KEY(asset,location,relation,source)
);
CREATE INDEX history_asset_batch ON history_asset_links(batch);
