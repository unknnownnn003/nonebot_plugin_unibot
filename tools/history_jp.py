"""Historical sources, checkpoint evidence and the frozen LOST bridge."""
from io import StringIO
import csv
import re

from _unibot_history.history import DIFFICULTIES, normalize_name
from history_calendar import FIRST_DAY, TIMEMACHINE_THROUGH
from history_import import covering, decimal_tenths, js_array, json_file, json_text, offset
from import_paradise_lost import TableParser, legacy


LOST = "PARADISE LOST"
LOST_OBSERVED = "2021-11-02"
LOST_END = "2021-11-03"
HISTORY_BASIS = "historical_slot_unverified_revision"


def seed_rules(db):
    for key, mode, status, parameters, artifact in (
        ("old-r10", "B30/R10", "partial", {"cap_score": 1007500, "cap_bonus_hundredths": 200,
                                          "best": 30, "recent": 10}, "date-jp"),
        ("new-r10", "B30/R10", "partial", {"cap_score": 1009000, "cap_bonus_hundredths": 215,
                                          "best": 30, "recent": 10}, "announcement-new"),
        ("verse-n20", "B30/N20", "partial", {"cap_score": 1009000, "cap_bonus_hundredths": 215,
                                            "best": 30, "new": 20, "disjoint": True,
                                            "pool_basis": "ordinary_song_introduction"}, "announcement-verse"),
    ):
        db.conn.execute("INSERT INTO ruleset VALUES (?,?,?,?,?)", (key, mode, status, json_text(parameters), artifact))
    for artifact, locator, value in (
        ("star-calculator", "function score_to_rate", {"constant_tenths": 140, "score": 925000,
                                                      "rating": "11.00", "below_900000": "zero",
                                                      "per_chart_rounding": "floor_2_digits"}),
        ("legacy-package", "services/legacy_rating.py:chart_rating",
         {"constant_tenths": 140, "score": 925000, "rating": "10.6666666667",
          "below_900000": "piecewise", "per_chart_rounding": "none"}),
    ):
        db.conn.execute("INSERT INTO ruleset VALUES (?,?,?,?,?)",
                        (artifact, "B30/R10", "disputed", json_text(value), artifact))
    conflict = db.dispute("JP", "STAR", None, "rating_formula", "2017-11-04",
                          "The calculator and the existing LOST simulation disagree at 925000 and below 900000", ())
    db.conn.executemany("INSERT INTO conflict_ruleset VALUES (?,?)",
                        [(conflict, "star-calculator"), (conflict, "legacy-package")])


def historical_song_periods(row, daydream):
    start = row["releaseDate"]
    terminal = row["endDate"]
    cursor = start or FIRST_DAY
    periods = [(FIRST_DAY, start, "not_released", "B", "day", 10)] if start else []
    for stop, resume in row.get("gaps", []):
        periods.extend(((cursor, stop, "playable" if start else "unknown", "B" if start else "C", "day", 10),
                        (stop, resume, "suspended", "B", "day", 10)))
        cursor = resume
    periods.append((cursor, terminal or offset(TIMEMACHINE_THROUGH), "playable" if start else "unknown",
                    "B" if start else "C", "day", 10))
    if terminal:
        removed = terminal if daydream else offset(terminal)
        if not daydream:
            periods.append((terminal, removed, "unknown", "C", "day", 10))
        periods.append((removed, offset(TIMEMACHINE_THROUGH), "removed", "B", "day", 10))
    return periods


def load_timemachine(db, data):
    artifact = "timemachine"
    seen = set()
    for i, row in enumerate(js_array(data, "APP_DATA")):
        key = row["sdvx_id"]
        if not key or key in seen:
            raise ValueError("Time machine requires a unique sdvx.in key")
        seen.add(key)
        title, locator = row["title"], f"APP_DATA[{i}]"
        song = db.song(("sdvx", key), title, artifact, locator)
        db.external(artifact, "JP", key, "", song, locator)
        db.evidence(artifact, locator, "JP", song, None, "song_metadata", row, {
            "release": row["releaseDate"], "terminal_source_date": row["endDate"], "wiki": row["wiki_url"],
            "generation": row["version"],
        }, start=row["releaseDate"], grain="day" if row["releaseDate"] else "unknown")
        db.baseline[key] = (song, row)
        db.ordinary[normalize_name(title)].add(song)
        daydream = key == "02091" and title == "Daydream café" and "daydream-evidence" in db.artifacts
        periods = historical_song_periods(row, daydream)
        if daydream:
            terminal = row["endDate"]
            periods = [p for p in periods if p[0] != terminal]
            db.record_name(song, title, "sdvx", "daydream-evidence", "配信再停止")
            db.states("daydream-evidence", "配信再停止", "JP", song, title,
                      [(terminal, offset(TIMEMACHINE_THROUGH), "removed", "B", "day", 20)])
        db.states(artifact, locator, "JP", song, title, periods)
        for difficulty in DIFFICULTIES[:5 if "ULT" in row["charts"] else 4]:
            import_historical_chart(db, row, locator, song, key, difficulty)


def import_historical_chart(db, row, locator, song, key, difficulty):
    artifact = "timemachine"
    title = row["title"]
    rev = db.chart(song, difficulty, HISTORY_BASIS)
    chart_locator = f"{locator}.charts.{difficulty}"
    db.external(artifact, "JP", key, difficulty, song, chart_locator, rev,
                "observed" if difficulty in row["charts"] else "unknown")
    horizon = offset(TIMEMACHINE_THROUGH)
    if difficulty == "ULT" and "announcement-ultima" in db.artifacts:
        first_ultima_edition = next(edition[0] for edition in db.editions.values() if edition[3] == "NEW")
        db.states("announcement-ultima", "新難易度「ULTIMA」追加！", "JP", song, title,
                  [(FIRST_DAY, first_ultima_edition, "not_released", "B", "version", 20)], revision=rev)
    daydream_ult = (key, difficulty) == ("02091", "ULT") and "daydream-evidence" in db.artifacts
    genesis_first = key == "01145" and "genesis-evidence" in db.artifacts
    nodes = row["charts"].get(difficulty, {}).get("timeline", [])
    if not nodes:
        db.states(artifact, chart_locator, "JP", song, title,
                  [(FIRST_DAY, horizon, "unknown", "Missing", "unknown", 10)], revision=rev)
        return
    if any(a["date"] >= b["date"] for a, b in zip(nodes, nodes[1:])):
        raise ValueError(f"Disordered timeline at {chart_locator}")
    first = None
    for i, node in enumerate(nodes):
        start, end, checkpoint, _ = db.editions[node["gen"]]
        raw_day = node["date"]
        if not start <= raw_day <= checkpoint:
            raise ValueError(f"Out-of-version time machine node at {chart_locator}")
        # Keep a version-qualified numeric observation separate from availability.
        lo = max(raw_day, row["releaseDate"] or raw_day)
        effective = max(lo, checkpoint) if raw_day == start else lo
        grain, proof = ("version", artifact) if raw_day == start else ("day", artifact)
        if i == 0 and daydream_ult:
            lo = effective = "2022-01-20"
            grain, proof = "day", "daydream-evidence"
        elif i == 0 and genesis_first:
            lo = effective = row["releaseDate"]
            grain, proof = "day", "genesis-evidence"
        first = first or (lo, effective, grain, proof)
        change = nodes[i + 1]["date"] if i + 1 < len(nodes) else horizon
        next_bound = min(end or horizon, change)
        if effective >= next_bound:
            db.issues["constant_node_without_valid_window"] += 1
        locator = f"{chart_locator}.timeline[{i}]"
        number = decimal_tenths(node["const"])
        observation = db.constant(artifact, locator, "JP", song, rev, node, number,
                                  start=effective, end=next_bound, grain=grain,
                                  activate=effective < next_bound, observed_start=lo, observed_end=offset(effective))
        carry_start = max(next_bound, lo)
        if carry_start < change:
            db.constant(artifact, locator + "/continuity", "JP", song, rev,
                        {"method": "timemachine_reference", "node": node, "node_observation": observation,
                         "observed_through": TIMEMACHINE_THROUGH}, number,
                        start=carry_start, end=change, grain="version", quality="C", priority=5)
    lo, effective, grain, proof = first
    before = "not_released" if difficulty == "ULT" and daydream_ult else "unknown"
    db.states(artifact, chart_locator, "JP", song, title,
              [(FIRST_DAY, lo, before, "B" if daydream_ult else "Missing", "day" if daydream_ult else "unknown", 10),
               (lo, effective, "unknown", "C", "version", 10)], revision=rev)
    db.states(proof, chart_locator if proof == artifact else ("ULTIMA追加" if daydream_ult else "配信日"),
              "JP", song, title, [(effective, horizon, "playable", "B", grain, 10)], revision=rev)


def load_lost(db, html, pinned):
    artifact = "max-lost"
    parser = TableParser()
    parser.feed(html.decode("utf-8"))
    parser.close()
    payload = json_file(pinned)
    legacy.parse_history(payload)  # Validate the independent production contract.
    rows = payload["songs"]
    if [(r["title"], r["constants"]) for r in rows] != [(r["title"], r["constants"]) for r in parser.rows]:
        raise ValueError("MAX-eipi source and independent production snapshot differ")
    for i, row in enumerate(rows):
        title = row["title"]
        locator = f"song-table row {i + 1}"
        candidates = db.ordinary.get(normalize_name(title), set())
        if (title == "Help me, ERINNNNNN!!" and "01034" in db.baseline
                and "sega-verse-renames" in db.artifacts):
            # SEGA's VERSE title change applies to this original chart set.
            candidates = {db.baseline["01034"][0]}
        # A unique match in the same JP period can be attached to the catalog
        # slot. The slot itself carries no assertion of chart-content equality.
        same_period = [sid for sid in candidates
                       if (state := covering(db.song_states["JP", sid], LOST_END)) and state[0] == "playable"]
        if len(same_period) == 1 and len(candidates) == 1:
            song = db.record_name(same_period[0], title, "sdvx", artifact, locator)
        else:
            song = db.song(("max-lost", title), title, artifact, locator)
            db.ordinary[normalize_name(title)].add(song)
        db.external(artifact, "JP", title, "", song, locator,
                    status="same_period_candidate" if song in same_period else "observed")
        if song not in same_period:
            db.states(artifact, locator, "JP", song, title,
                      [(LOST_OBSERVED, LOST_END, "playable", "B", "point", 20),
                       (LOST_END, offset(LOST_END), "unknown", "C", "point", 20)])
        for difficulty, value in enumerate(row["constants"]):
            rev = db.chart(song, DIFFICULTIES[difficulty], HISTORY_BASIS)
            chart_locator = f"{locator}/{DIFFICULTIES[difficulty]}"
            db.external(artifact, "JP", title, DIFFICULTIES[difficulty], song, chart_locator, rev)
            db.states(artifact, chart_locator, "JP", song, title,
                      [(LOST_OBSERVED, LOST_END, "playable", "B", "point", 20),
                       (LOST_END, offset(LOST_END), "playable", "B", "version", 20)], revision=rev)
            db.constant(artifact, chart_locator, "JP", song, rev, f"{value // 10}.{value % 10}", value,
                        start=LOST_OBSERVED, end=LOST_END, priority=50)
            if "lxns_id" in row and (identity := row["identities"][difficulty]) is not None:
                db.legacy[row["lxns_id"], difficulty] = (song, rev, identity, value, title)
    if len(db.legacy) != 3048 or len({sid for sid, _ in db.legacy}) != 762:
        raise ValueError("Unexpected existing LOST mapping size")


def load_verse_sheet(db, data):
    artifact = "verse-sheet"
    rows = list(csv.reader(StringIO(data.decode("utf-8-sig"))))
    if rows[2][:5] != ["曲名", "譜面", "ジャンル", "LMN+", "VRS"]:
        raise ValueError("Unexpected VERSE comparison sheet columns")
    for i, row in enumerate(rows[3:], 4):
        for col in range(0, len(row) - 4, 6):
            title, difficulty, _, old, new = row[col:col + 5]
            if difficulty not in DIFFICULTIES[:5] or not re.fullmatch(r"\d+\.\d", new):
                continue
            names = db.ordinary.get(normalize_name(title), ())
            if len(names) != 1:
                db.issues["verse_sheet_unpaired_rows"] += 1
                continue
            song = next(iter(names))
            rev = db.chart(song, difficulty, HISTORY_BASIS)
            for column, raw, day in ((3, old, "2024-12-11"), (4, new, "2024-12-12")):
                if not re.fullmatch(r"\d+\.\d", raw):
                    continue
                value = decimal_tenths(raw)
                locator = f"row {i} column {col + column + 1}"
                db.constant(artifact, locator, "JP", song, rev, raw, value,
                            start=day, end=offset(day), quality="C", activate=False)
