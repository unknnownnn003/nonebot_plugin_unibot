"""Apply reviewed, JP-only release facts without relying on numeric chart nodes."""
from dataclasses import dataclass

from _unibot_history.history import catalog_state, constant_kind, snapshot_rows, trusted
from history_import import identifier, offset, partition
from history_jp import FIRST_DAY, HISTORY_BASIS


ERIN_ORIGINAL = "Help me, ERINNNNNN!!"
ERIN_BAND = "Help me, ERINNNNNN!!（Band ver.）"
ERIN_RENAME = "2024-12-12"
ERIN_REARRANGE = "2025-05-21"


@dataclass(frozen=True)
class ReleaseBatch:
    artifact: str
    day: str
    locator: str
    keys: tuple[str, ...]
    eligibility: str = "normal_chart_unlock"
    quality: str = "A"


# These are reviewed additions. Each batch describes the release date of
# the ULTIMA chart itself, independently of the song and the constant table.
ULTIMA_RELEASES = (
    ReleaseBatch("sega-ult-newplus", "2022-04-14", "1820 / ULTIMA additions image", ("01027", "01058", "01063")),
    ReleaseBatch("sega-ult-luminous", "2023-12-14", "6208 / ULTIMA additions image", ("01056", "01123")),
    ReleaseBatch("sega-ult-luminousplus", "2024-06-20", "7346 / ULTIMA additions image",
                 ("01067", "01183", "05177")),
    ReleaseBatch("sega-ult-luminousplus", "2024-06-20", "7346 / national unlock",
                 ("01051",), eligibility="national_unlock_points"),
    ReleaseBatch("sega-ult-verse", "2024-12-12", "8704 / ULTIMA additions image",
                 ("01121", "02072", "10003", "10007", "10008", "10009", "10011")),
    ReleaseBatch("sega-ult-verse", "2024-12-12", "8704 / national unlock",
                 ("07024",), eligibility="national_unlock_points"),
    ReleaseBatch("sega-ult-xversex", "2025-12-11", "10941 / ULTIMA additions image", ("10179", "10185", "10186")),
    ReleaseBatch("sega-mate-start-ultima", "2026-07-02", "12155 / ULTIMA additions image", ("03122", "11006", "11007")),
    ReleaseBatch("sega-mate-0730", "2026-07-30", "ULTIMA additions", ("03132", "03133")),
    ReleaseBatch("sega-mate-0925", "2026-09-25", "ULTIMA additions", ("08067",)),
    ReleaseBatch("sega-mate-1022", "2026-10-22", "ULTIMA additions", ("07138",)),
    ReleaseBatch("sega-luminous-plus-sad", "2024-08-22", "ULTIMA additions", ("03130",)),
    ReleaseBatch("sega-sun-flan", "2022-10-27", "ULTIMA additions", ("05011",)),
    ReleaseBatch(
        "wiki-ult-xverse-0913", "2025-09-13", "ULTIMA additions / serial code rewards",
        ("01050", "01052", "01066", "01072", "01136", "02020", "02046", "02102", "03020", "06099"),
        eligibility="event_serial_code", quality="B",
    ),
)


def apply_release_batch(db, batch, horizon):
    season = next((version for start, end, _, version in db.editions.values()
                   if start <= batch.day and (end is None or batch.day < end)), None)
    if season is None or batch.day >= horizon:
        raise ValueError(f"Chart release outside the JP evidence timeline: {batch.artifact}")
    for key in batch.keys:
        song, row = db.baseline[key]
        title = row["title"]
        chart = identifier("chart", song, "ULT", "")
        revision = identifier("revision", chart, HISTORY_BASIS, None)
        if revision not in db.revisions:
            revision = db.chart(song, "ULT", HISTORY_BASIS)
        locator = f"{batch.locator} / {key} / ULTIMA"
        db.evidence(batch.artifact, locator, "JP", song, revision, "chart_release",
                    {"title": title, "song_key": key, "difficulty": "ULT"},
                    {"version": season, "first_release": batch.day, "unlock": batch.eligibility},
                    start=batch.day, end=offset(batch.day), grain="day", quality=batch.quality)
        intervals = db.chart_states["JP", revision]
        existing = partition(intervals)
        db.states(batch.artifact, locator, "JP", song, title,
                  [(FIRST_DAY, batch.day, "not_released", batch.quality, "day", 50)], revision=revision)
        # Extend the audited positive state only up to a known chart-level
        # restriction. Song removal and suspension remain separate intervals.
        first_stop = next((start for start, _, (state, _, _) in existing
                           if start >= batch.day and state in ("removed", "suspended", "disabled")), horizon)
        db.states(batch.artifact, locator, "JP", song, title,
                  [(batch.day, min(first_stop, horizon), "playable", batch.quality, "day", 15)],
                  revision=revision)


def apply_erin_rename(db):
    artifact = "sega-verse-renames"
    original, row = db.baseline["01034"]
    if row["title"] != ERIN_BAND:
        raise ValueError("Unexpected title for the original ERIN song")
    locator = "一部楽曲の曲名・称号名変更"
    db.record_name(original, ERIN_ORIGINAL, "sdvx", artifact, locator)
    db.evidence(artifact, locator, "JP", original, None, "song_name_change", ERIN_ORIGINAL, ERIN_BAND,
                start=ERIN_RENAME, end=offset(ERIN_RENAME), grain="day", quality="A")
    for start, end, (state, prior_observation, _) in partition(db.song_states["JP", original]):
        if start >= ERIN_RENAME:
            continue
        original_quality, original_grain = db.conn.execute(
            "SELECT quality, grain FROM observation WHERE id=?", (prior_observation,),
        ).fetchone()
        db.states(artifact, f"{locator} / availability {prior_observation}", "JP", original, ERIN_ORIGINAL,
                  [(start, min(end or ERIN_RENAME, ERIN_RENAME), state, original_quality, original_grain, 50)])

    artifact = "sega-verse-erin-2025"
    rearrange, row = db.baseline["10084"]
    if row["title"] != ERIN_ORIGINAL or rearrange == original:
        raise ValueError("Unexpected identity for the ERIN rearrangement")
    locator = "ビートまりお collaboration / new songs"
    db.record_name(rearrange, ERIN_ORIGINAL, "sdvx", artifact, locator)
    db.evidence(artifact, locator, "JP", rearrange, None, "song_release",
                ERIN_ORIGINAL, ERIN_REARRANGE, start=ERIN_REARRANGE, end=offset(ERIN_REARRANGE),
                grain="day", quality="A")
    db.states(artifact, locator, "JP", rearrange, ERIN_ORIGINAL,
              [(FIRST_DAY, ERIN_REARRANGE, "not_released", "A", "day", 50)])


def apply_jp_availability(db, raw):
    horizon = offset(max(day for region, _, day, _ in db.points if region == "JP"))
    reviewed = set()
    for batch in ULTIMA_RELEASES:
        if batch.artifact not in raw:
            raise ValueError(f"Missing reviewed ULTIMA evidence: {batch.artifact}")
        if reviewed.intersection(batch.keys):
            raise ValueError(f"Duplicate ULTIMA first release: {batch.artifact}")
        reviewed.update(batch.keys)
        apply_release_batch(db, batch, horizon)
    apply_erin_rename(db)


ULTIMA_AUDIT_FIELDS = (
    "version", "b2_date", "b5_date", "song_id", "chart_id", "revision_id", "b2_revision_id", "title",
    "b2_state", "b5_state", "b2_chart_state", "b5_chart_state",
    "b2_constant_status", "b5_constant_status", "change",
    "first_release_version", "first_release_date", "unlock",
    "first_release_evidence_grade", "first_release_source_url", "first_release_source_sha256",
    "state_evidence_grade", "state_source_url", "state_source_sha256", "state_source_locator",
)


def ultima_checkpoints(conn, stable):
    for snap in conn.execute("""
        SELECT s.id, r.version, s.on_date
        FROM region_release r JOIN snapshot s ON s.region=r.region AND s.version=r.version
        WHERE r.region='JP' AND s.on_date=coalesce(date(r.end_before, '-1 day'), (
            SELECT max(t.on_date) FROM snapshot t WHERE t.region=r.region AND t.version=r.version
        ))
        ORDER BY r.start_on
    """):
        rows = {stable.get(r["revision_id"], (r["revision_id"],)): r
                for r in snapshot_rows(conn, "JP", snap["on_date"], snap["id"])
                if r["difficulty"] == "ULT"}
        yield snap["version"], (snap["on_date"], rows)


def ultima_audit_rows(b2_conn, b5_conn, *, revision_keys=None):
    """Audit all B2 and B5 JP ULTIMA checkpoint revisions, including unavailable ones."""
    old = dict(ultima_checkpoints(b2_conn, revision_keys(b2_conn) if revision_keys else {}))
    releases = {r["revision_id"]: tuple(r)[1:] for r in b5_conn.execute("""
        SELECT o.revision_id, json_extract(o.value_json, '$.version'), o.effective_on,
               json_extract(o.value_json, '$.unlock'), o.quality, a.url, a.sha256
        FROM observation o JOIN source_artifact a ON a.id=o.artifact_id
        WHERE o.region='JP' AND o.field='chart_release'
    """)}
    observations = {r["id"]: tuple(r)[1:] for r in b5_conn.execute("""
        SELECT o.id, o.quality, a.url, a.sha256, o.locator
        FROM observation o JOIN source_artifact a ON a.id=o.artifact_id
        WHERE o.region='JP' AND o.field='availability'
    """)}
    for version, (day, current) in ultima_checkpoints(b5_conn, revision_keys(b5_conn) if revision_keys else {}):
        previous_day, previous = old.get(version, (None, {}))
        for key in sorted(previous.keys() | current.keys()):
            before, after = previous.get(key), current.get(key)
            identity = after or before
            old_state = catalog_state(before) if before else "absent"
            new_state = catalog_state(after) if after else "absent"
            before_constant = ("verified" if trusted(before) else constant_kind(before)) if before else None
            after_constant = ("verified" if trusted(after) else constant_kind(after)) if after else None
            change = "unchanged"
            if old_state != new_state:
                change = f"{old_state}_to_{new_state}"
            elif before_constant != after_constant:
                change = "constant_status_changed"
            evidence = None
            if after:
                state_field = "chart_observation" if new_state == after["chart_state"] else "song_observation"
                evidence = after[state_field]
            revision = identity["revision_id"]
            yield (
                version, previous_day, day, identity["song_id"], identity["chart_id"], revision,
                before["revision_id"] if before else None, identity["title"],
                old_state, new_state, before["chart_state"] if before else None, after["chart_state"] if after else None,
                before_constant, after_constant, change, *releases.get(revision, (None,) * 6),
                *observations.get(evidence, (None,) * 4),
            )


def write_ultima_audit(path, b2_conn, b5_conn, *, revision_keys=None):
    from export_history_constants import csv_rows

    csv_rows(path, ULTIMA_AUDIT_FIELDS, ultima_audit_rows(b2_conn, b5_conn, revision_keys=revision_keys))
