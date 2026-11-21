PRAGMA application_id = 1431193672;
PRAGMA user_version = 1;
PRAGMA foreign_keys = ON;
CREATE TABLE complete (ready INTEGER PRIMARY KEY CHECK(ready=1));
CREATE TABLE versions (
    version TEXT PRIMARY KEY, ordinal INTEGER UNIQUE NOT NULL, hdd TEXT NOT NULL,
    start_on TEXT NOT NULL, end_on TEXT NOT NULL, start_url TEXT NOT NULL,
    end_boundary_url TEXT NOT NULL, basis TEXT NOT NULL
);
CREATE TABLE sources (
    path TEXT PRIMARY KEY, hdd TEXT NOT NULL, package TEXT, bytes INTEGER,
    sha256 TEXT, kind TEXT NOT NULL, issue TEXT
);
CREATE TABLE packages (
    hdd TEXT NOT NULL, package TEXT NOT NULL, path TEXT NOT NULL, data_version TEXT,
    xml_count INTEGER NOT NULL, music_count INTEGER NOT NULL, classification TEXT NOT NULL,
    evidence TEXT NOT NULL, PRIMARY KEY(hdd, package)
);
CREATE TABLE raw_xml (path TEXT PRIMARY KEY REFERENCES sources, xml BLOB NOT NULL);
CREATE TABLE raw_we (source TEXT PRIMARY KEY REFERENCES raw_xml, hdd TEXT NOT NULL, package TEXT NOT NULL,
    music_id INTEGER NOT NULL, title TEXT NOT NULL, tag TEXT, tag_id TEXT, star_raw TEXT, stars INTEGER);
CREATE TABLE raw_song_index (source TEXT PRIMARY KEY REFERENCES raw_xml, hdd TEXT NOT NULL, package TEXT NOT NULL,
    music_id INTEGER NOT NULL, title TEXT NOT NULL, ex_type INTEGER, disabled INTEGER);
CREATE TABLE music_versions (
    version TEXT REFERENCES versions, identity TEXT, music_id INTEGER, directory_id TEXT,
    title TEXT, artist TEXT, sort_name TEXT, genre TEXT, works TEXT, rights TEXT,
    release_tag TEXT, net_open TEXT, disable_flag INTEGER, first_lock INTEGER, ex_type INTEGER,
    cue TEXT, jacket_ref TEXT, jacket_sha256 TEXT, source TEXT REFERENCES sources, package TEXT,
    state TEXT NOT NULL, basis TEXT NOT NULL, origin_identity TEXT, origin_basis TEXT,
    PRIMARY KEY(version, identity)
);
CREATE INDEX music_lookup ON music_versions(version, music_id);
CREATE TABLE chart_versions (
    version TEXT, identity TEXT, chart_identity TEXT, music_id INTEGER, title TEXT,
    slot INTEGER, difficulty TEXT, enabled INTEGER, level_raw TEXT, decimal_raw TEXT,
    hdd_hundredths INTEGER, selected_hundredths INTEGER, display_level TEXT,
    c2s_ref TEXT, c2s_path TEXT, c2s_sha256 TEXT, notes_designer TEXT, source TEXT REFERENCES sources,
    package TEXT, state TEXT NOT NULL, constant_status TEXT NOT NULL, basis TEXT NOT NULL, b5_revision TEXT,
    PRIMARY KEY(version, chart_identity), FOREIGN KEY(version, identity) REFERENCES music_versions
);
CREATE INDEX chart_lookup ON chart_versions(version, music_id, difficulty);
CREATE TABLE worlds_end_versions (
    version TEXT, identity TEXT, chart_identity TEXT, music_id INTEGER, title TEXT,
    slot INTEGER, enabled INTEGER, tag TEXT, tag_id TEXT, star_raw TEXT, stars INTEGER,
    c2s_ref TEXT, c2s_path TEXT, c2s_sha256 TEXT, notes_designer TEXT, jacket_sha256 TEXT,
    source TEXT REFERENCES sources, package TEXT, state TEXT NOT NULL, basis TEXT NOT NULL,
    PRIMARY KEY(version, chart_identity), FOREIGN KEY(version, identity) REFERENCES music_versions
);
CREATE INDEX we_lookup ON worlds_end_versions(version, music_id);
CREATE TABLE jackets (
    sha256 TEXT PRIMARY KEY, format TEXT NOT NULL, width INTEGER, height INTEGER,
    pixel_format TEXT, representative_path TEXT NOT NULL REFERENCES sources
);
CREATE TABLE jacket_manifest (
    version TEXT, identity TEXT, music_id INTEGER, jacket_ref TEXT, source TEXT NOT NULL REFERENCES sources,
    path TEXT REFERENCES sources, sha256 TEXT REFERENCES jackets, status TEXT NOT NULL,
    PRIMARY KEY(version, identity), FOREIGN KEY(version, identity) REFERENCES music_versions
);
CREATE TABLE changes (
    old_version TEXT, new_version TEXT NOT NULL, identity TEXT NOT NULL, music_id INTEGER, title TEXT,
    difficulty TEXT, kind TEXT NOT NULL, field TEXT, before TEXT, after TEXT,
    old_source TEXT REFERENCES sources, new_source TEXT REFERENCES sources, confidence TEXT NOT NULL
);
CREATE TABLE option_changes (
    hdd TEXT NOT NULL, before_package TEXT, after_package TEXT NOT NULL, music_id INTEGER, title TEXT,
    kind TEXT NOT NULL, field TEXT, before TEXT, after TEXT,
    before_source TEXT REFERENCES sources, after_source TEXT REFERENCES sources,
    effective_on TEXT, evidence TEXT NOT NULL
);
CREATE TABLE option_pairs (
    hdd TEXT, left_package TEXT, right_package TEXT, shared INTEGER, identical_xml INTEGER,
    left_only INTEGER, right_only INTEGER, comparison TEXT
);
CREATE TABLE conflicts (version TEXT, music_id INTEGER, difficulty TEXT, kind TEXT NOT NULL,
    source TEXT, other_source TEXT, detail TEXT NOT NULL);
CREATE TABLE unmatched_records (version TEXT, music_id INTEGER, title TEXT, difficulty TEXT,
    source TEXT, kind TEXT NOT NULL, candidates TEXT);
CREATE TABLE paradise_split_evidence (version TEXT, music_id INTEGER, title TEXT, difficulty TEXT,
    state TEXT, decision TEXT, source TEXT REFERENCES sources, evidence TEXT NOT NULL, unresolved INTEGER NOT NULL);
CREATE TABLE comparison (version TEXT, music_id INTEGER, title TEXT, difficulty TEXT, source TEXT,
    revision_id TEXT, hdd_hundredths INTEGER, b5_hundredths INTEGER, b5_state TEXT, b5_status TEXT,
    result TEXT NOT NULL, wiki_hundredths INTEGER, wiki_url TEXT, wiki_locator TEXT, identity_basis TEXT);
CREATE TABLE score_mapping (version TEXT, chart_identity TEXT, cn_music_id INTEGER, cn_slot INTEGER,
    cn_fingerprint TEXT, status TEXT NOT NULL, basis TEXT NOT NULL, PRIMARY KEY(version, chart_identity));
CREATE TABLE references_used (id TEXT PRIMARY KEY, url TEXT, sha256 TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TABLE wiki_we_checks (version TEXT, music_id INTEGER, title TEXT, tag TEXT, star_raw TEXT, stars INTEGER,
    reference TEXT, locator TEXT, wiki_stars INTEGER, result TEXT NOT NULL);
CREATE TABLE wiki_level_checks (version TEXT, music_id INTEGER, title TEXT, difficulty TEXT, raw_constant INTEGER,
    display_level TEXT, wiki_level TEXT, reference TEXT, locator TEXT, result TEXT NOT NULL);
