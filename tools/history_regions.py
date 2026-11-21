"""Official indexes and observed JP, INTL and CN catalogs."""
from collections import Counter

from _unibot_history.history import DIFFICULTIES, normalize_name
from history_import import absolute_url, covering, decimal_tenths, identifier, json_file, json_text, offset, sha
from history_jp import HISTORY_BASIS
from import_paradise_lost import legacy


JP_JACKETS = "https://new.chunithm-net.com/chuni-mobile/html/mobile/img/"
LXNS_JACKETS = "https://assets2.lxns.net/chunithm/jacket/"


def release_observations(db):
    db.release("INTL", "X-VERSE", "2025-11-20", "2026-04-16", None, "Asia/Singapore",
               "observation", "intl-xverse", "Introduction of the New Elements")
    db.release("INTL", "X-VERSE-X", "2026-04-16", "2026-10-22", None, "Asia/Singapore",
               "observation", "intl-xversex", "Important Information of the New Version")
    db.release("INTL", "MATE", "2026-10-22", None, None, "Asia/Singapore",
               "observation", "intl-mate", "Introduction of the New Elements")
    db.release("CN", "CN2027", "2026-09-10", None, None, "Asia/Shanghai",
               "observation", "cn-2027-evidence", "Ver.CN1.40 launch announcement recap")


def official_identity(db, artifact, region, key, title, locator):
    return db.song((f"{region}:official" if region in ("JP", "INTL") else "lxns-cn", key),
                   title, artifact, locator)


def source_point(db, artifact, region, song, title, locator, *, state="playable", revision=None, display=None):
    day = db.artifacts[artifact]["observed_on"]
    db.states(artifact, locator, region, song, title, [(day, offset(day), state, "B", "point", 30)],
              revision=revision, display=display)


def observed_identity(db, artifact, region, key, row, locator, *, external_key=None):
    """Keep prior identities; require review if a later capture reuses a key."""
    title, artist = row["title"], row["artist"]
    provider = db.artifacts[artifact]["source"]
    external = region, provider, str(external_key if external_key is not None else key)
    signature = normalize_name(title), normalize_name(artist)
    previous = db.current_songs.get(external)
    if previous:
        if previous[0] != signature:
            raise ValueError(f"Catalog identity requires review for {region}/{provider}/{external[2]}")
        return db.record_name(previous[1], title, provider, artifact, locator)
    song = official_identity(db, artifact, region, key, title, locator)
    db.current_songs[external] = (signature, song)
    return song


def official_chart_key(row):
    return f"WE:{row.get('we_kanji', '')}:{row.get('we_star', '')}"


def preserved_key(row, duplicates, variant=""):
    key = str(row["id"])
    if duplicates[key] == 1:
        return key
    return key + ":" + sha(json_text([row["title"], row.get("artist"), variant, row.get("image")]).encode("utf-8"))[:16]


def import_official(db, artifact, data, region, *, observed_snapshot=False):
    rows = json_file(data)
    duplicates = Counter(str(r["id"]) for r in rows)
    for i, row in enumerate(rows):
        title, locator = row["title"], f"$[{i}]"
        variant = official_chart_key(row) if row.get("we_kanji") else ""
        key = preserved_key(row, duplicates, variant)
        song = official_identity(db, artifact, region, key, title, locator)
        db.external(artifact, region, row["id"], "", song, locator,
                    status="ambiguous" if duplicates[str(row["id"])] > 1 else "observed")
        day = db.artifacts[artifact]["observed_on"]
        db.evidence(artifact, locator, region, song, None, "song_metadata", row, {
            "artist": row.get("artist"), "genre": row.get("catname"), "official_id": row["id"],
            "display_levels": {d: row.get(f"lev_{d.lower()}") for d in DIFFICULTIES[:5]},
        }, start=day, end=offset(day), grain="point", quality="A")
        image = row.get("image")
        if image:
            db.jacket(artifact, region, song, f"{locator}.image", image, absolute_url(JP_JACKETS, image))
        if observed_snapshot:
            source_point(db, artifact, region, song, title, locator)
            diffs = {"WE": row.get("we_star")} if variant else {
                d: row[f"lev_{d.lower()}"] for d in DIFFICULTIES[:5] if row.get(f"lev_{d.lower()}")
            }
            for diff, display in diffs.items():
                rev = db.chart(song, diff, f"{region}:official_display", variant=variant)
                chart_locator = f"{locator}/{diff}"
                db.external(artifact, region, row["id"], diff + variant, song, chart_locator, rev)
                source_point(db, artifact, region, song, title, chart_locator, revision=rev, display=display)
    if observed_snapshot:
        db.points.append((region, "X-VERSE-X", db.artifacts[artifact]["observed_on"], "observation"))


def chart_metadata(chart, *, intl=False):
    return {
        "note_count": chart.get("maxCombo" if intl else "notes"),
        "designer": chart.get("designer" if intl else "notes_designer"),
        "locally_measured_revision": False,
    }


def current_chart(db, artifact, locator, region, key, song, title, diff, chart, *, value=None, estimated=False,
                  display=None, state="playable", variant="", metadata=None, identity=None):
    # Fingerprints are scoped to the provider's observed metadata. They are
    # not content hashes and never automatically certify a cross-region match.
    fingerprint = sha(json_text(identity if identity is not None else metadata or {}).encode("utf-8"))
    rev = db.chart(song, diff, f"{region}:{db.artifacts[artifact]['source']}:metadata",
                   variant=variant, fingerprint=fingerprint, metadata=metadata)
    db.external(artifact, region, key, diff + variant, song, locator, rev)
    source_point(db, artifact, region, song, title, locator, state=state, revision=rev, display=display)
    day = db.artifacts[artifact]["observed_on"]
    db.evidence(artifact, locator, region, song, rev, "chart_metadata", chart, metadata or {},
                start=day, end=offset(day), grain="point")
    if value is not None and diff != "WE":
        db.constant(artifact, locator, region, song, rev, value, decimal_tenths(value),
                    start=day, end=offset(day), quality="D" if estimated else "B",
                    kind="estimated" if estimated else "exact")
    return rev


def import_jp_current(db, data, we_data, *, artifact="jp-current", we_artifact="jp-we", version="MATE"):
    if db.artifacts[artifact]["observed_on"] != db.artifacts[we_artifact]["observed_on"]:
        raise ValueError("JP ordinary and WORLD'S END observations must be captured on the same day")
    day = db.artifacts[artifact]["observed_on"]
    short = dict(zip(("BASIC", "ADVANCED", "EXPERT", "MASTER", "ULTIMA"), DIFFICULTIES))
    for i, row in enumerate(json_file(data)["songs"]):
        idx, title, key, locator = row["official_idx"], row["title"], row["id"], f"songs[{i}]"
        song = observed_identity(db, artifact, "JP", idx, row, locator)
        point_song(db, artifact, "JP", song, key, locator, row)
        for long, chart in row["charts"].items():
            if chart is None:
                continue
            diff = short[long]
            metadata = chart_metadata(chart)
            rev = current_chart(db, artifact, f"{locator}.charts.{long}", "JP", key, song, title, diff, chart,
                                value=chart["const"], estimated=chart["is_const_unknown"], metadata=metadata)
            db.current["JP"][str(idx), normalize_name(title), normalize_name(row["artist"]), diff] = (
                rev, metadata["note_count"], song, artifact)
    artifact = we_artifact
    for i, row in enumerate(json_file(we_data)):
        if "WE" not in row["data"]:
            continue
        meta, chart, locator = row["meta"], row["data"]["WE"], f"$[{i}]"
        title, key = meta["title"], meta["id"]
        song = db.song(("jp-we", key), title, artifact, locator)
        point_song(db, artifact, "JP", song, key, locator, meta)
        current_chart(db, artifact, f"{locator}.data.WE", "JP", key, song, title, "WE", chart,
                      variant=key, metadata={"note_count": chart.get("maxcombo"), "title_with_attribute": title})
    db.points.append(("JP", version, day, "observation"))


def point_song(db, artifact, region, song, key, locator, row, state="playable"):
    title = row["title"]
    db.external(artifact, region, key, "", song, locator)
    source_point(db, artifact, region, song, title, locator, state=state)
    day = db.artifacts[artifact]["observed_on"]
    db.evidence(artifact, locator, region, song, None, "song_metadata",
                {k: v for k, v in row.items() if k not in ("charts", "difficulties")},
                {k: row[k] for k in ("title", "artist", "bpm", "genre") if k in row},
                start=day, end=offset(day), grain="point")


def import_intl_current(db, data, *, artifact="intl-current", version="MATE"):
    payload = json_file(data)
    if payload["server"] != "intl" or payload["gameVersion"].upper() != version:
        raise ValueError("Unexpected ChuniMaru region or release")
    if not payload["generatedAt"].startswith(db.artifacts[artifact]["observed_on"]):
        raise ValueError("Unexpected INTL observation date")
    for i, row in enumerate(payload["songs"]):
        title, key, idx, locator = row["title"], row["id"], str(row["idx"]), f"songs[{i}]"
        artist = normalize_name(row["artist"])
        song = observed_identity(db, artifact, "INTL", idx, row, locator)
        point_song(db, artifact, "INTL", song, key, locator, row)
        url = row.get("jacket")
        if url:
            sample = (300, 300, "3015c4a7a549688df871047f8d2b86d72d517ef7ea272dc3036ad1c139136c22") if row["img"] == "d739ba44da6798a0" else (None, None, None)
            db.jacket(artifact, "INTL", song, f"{locator}.jacket", row["img"], url,
                      width=sample[0], height=sample[1], sha256=sample[2])
        for diff, chart in row["charts"].items():
            metadata = chart_metadata(chart, intl=True)
            rev = current_chart(db, artifact, f"{locator}.charts.{diff}", "INTL", key, song, title, diff, chart,
                                value=chart["const"], estimated=chart["isConstUnknown"],
                                display=chart["levelDisplay"], metadata=metadata)
            link_current(db, artifact, idx, title, artist, diff, rev, metadata["note_count"])
    db.points.append(("INTL", version, db.artifacts[artifact]["observed_on"], "observation"))


def import_cn(db, data, config, *, artifact="cn-current", config_artifact="cn-config",
              version="CN2027", baseline=True):
    payload = json_file(data)
    cn_version = json_file(config)["data"]["resource_version"]["chunithm"]
    if cn_version != int(db.artifacts[artifact]["upstream_version"]) or not any(
        v["version"] == cn_version for v in payload["versions"]
    ):
        raise ValueError("CN public catalog does not match its configured source resource version")
    day = db.artifacts[artifact]["observed_on"]
    if day != db.artifacts[config_artifact]["observed_on"]:
        raise ValueError("CN configuration and catalog must be captured on the same day")
    for i, row in enumerate(payload["songs"]):
        key, title, locator = str(row["id"]), row["title"], f"songs[{i}]"
        song = observed_identity(db, artifact, "CN", key, row, locator)
        song_state = cn_state(row, day)
        point_song(db, artifact, "CN", song, key, locator, row, song_state)
        db.jacket(artifact, "CN", song, locator, key, absolute_url(LXNS_JACKETS, key + ".png"))
        artist = normalize_name(row["artist"])
        for j, chart in enumerate(row["difficulties"]):
            index = chart["difficulty"]
            diff = DIFFICULTIES[index]
            metadata = {
                "note_count": chart.get("notes", {}).get("total"), "notes": chart.get("notes"),
                "designer": chart.get("note_designer"), "source_version": chart.get("version"),
                "we_kanji": chart.get("kanji"), "we_star": chart.get("star"), "origin_id": chart.get("origin_id"),
            }
            variant = (":" + sha(json_text([metadata["we_kanji"], metadata["we_star"], metadata["origin_id"],
                                            metadata["notes"]]).encode("utf-8"))[:16]) if diff == "WE" else ""
            identity = legacy.chart_identity(row, chart)
            rev = current_chart(db, artifact, f"{locator}.difficulties[{j}]", "CN", key, song, title, diff, chart,
                                value=chart.get("level_value"), display=chart["level"], state=cn_state(chart, day),
                                variant=variant, metadata=metadata, identity=identity)
            if diff == "WE":
                continue
            link_current(db, artifact, key, title, artist, diff, rev, metadata["note_count"])
            historical = db.legacy.get((row["id"], index))
            if baseline and historical:
                _, target, expected, value, _ = historical
                add_mapping(db, rev, target, artifact, "legacy-package",
                            "legacy_pinned" if identity == expected else "conflict",
                            {"cn_fingerprint": identity, "required_fingerprint": expected,
                             "legacy_constant_tenths": value, "checkpoint": "2021-11-03"})
    db.points.append(("CN", version, day, "observation"))


def cn_state(row, day):
    if row.get("disabled"):
        return "disabled"
    released = row.get("release_date")
    return "not_released" if released and released > day else "playable"


def link_current(db, artifact, key, title, artist, diff, revision, notes):
    lookup = key, normalize_name(title), artist, diff
    match = db.current["JP"].get(lookup)
    if not match:
        return
    jp, jp_notes, _, jp_artifact = match
    if notes is None or jp_notes is None:
        return
    status = "candidate" if notes == jp_notes else "conflict"
    add_mapping(db, revision, jp, artifact, jp_artifact, status,
                {"official_index": key, "title_artist_match": True, "source_note_total": notes,
                 "jp_note_total": jp_notes, "normalized_title": lookup[1], "difficulty": diff, "scope": "current cross-region observation"})

def review_cn_mapping_candidates(db, correspondences):
    """Record version-end leads from the current identity bridge, with no equivalence claim."""
    for link in correspondences:
        day = link["target_checkpoint"]
        for source, target, artifact, jp_artifact, status, evidence in db.conn.execute("""
            SELECT m.* FROM chart_mapping m
            JOIN source_artifact sa ON sa.id=m.source_artifact_id
            JOIN source_artifact ja ON ja.id=m.target_artifact_id
            WHERE sa.source_id='lxns' AND ja.source_id='chunisupport' AND m.status IN ('candidate','conflict')
            ORDER BY m.source_revision_id,m.target_revision_id,m.source_artifact_id,m.target_artifact_id
        """).fetchall():
            evidence = json_file(evidence.encode("utf-8"))
            title, diff = evidence["normalized_title"], evidence["difficulty"]
            names = db.ordinary.get(title, ())
            if len(names) != 1:
                continue
            song = next(iter(names))
            revision = identifier("revision", identifier("chart", song, diff, ""), HISTORY_BASIS, None)
            if revision not in db.revisions:
                continue
            song_state = covering(db.song_states["JP", song], day)
            chart_state = covering(db.chart_states["JP", revision], day)
            if not (song_state and song_state[0] == "playable" and chart_state and chart_state[0] == "playable"):
                continue
            add_mapping(db, source, revision, artifact, "timemachine", status,
                        {**evidence, "via_current_jp_revision": target, "via_artifact": jp_artifact,
                         "target_version": link["target_version"], "target_checkpoint": day,
                         "historical_revision_verified": False, "scope": "version-end review candidate"})


def add_mapping(db, source, target, artifact, target_artifact, status, evidence):
    db.conn.execute("INSERT INTO chart_mapping VALUES (?,?,?,?,?,?)",
                    (source, target, artifact, target_artifact, status, json_text(evidence)))
