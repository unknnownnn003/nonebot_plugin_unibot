"""Add reviewed JP constant evidence to the existing immutable catalog build."""
from collections import defaultdict
from dataclasses import dataclass
import re

from _unibot_history.history import DIFFICULTIES, normalize_name
from history_constant_sources import comparison_rows, current_info_rows, mate_low_level_rows, survey_rows
from history_import import covering, identifier, offset
from history_jp import HISTORY_BASIS
from import_paradise_lost import TableParser


@dataclass(frozen=True)
class Candidate:
    artifact: str
    locator: str
    observed: str | None
    tier: str
    title: str
    difficulty: str
    tenths: int
    context: str = ""


def ordinary_slot(db, candidate, day):
    names = db.ordinary.get(normalize_name(candidate.title), ())
    if len(names) != 1:
        return None
    song = next(iter(names))
    state = covering(db.song_states["JP", song], day)
    if state is None or state[0] != "playable":
        return None
    chart = identifier("chart", song, candidate.difficulty, "")
    revision = identifier("revision", chart, HISTORY_BASIS, None)
    if revision not in db.revisions:
        return None
    chart_state = covering(db.chart_states["JP", revision], day)
    if chart_state is None or chart_state[0] not in ("playable", "unknown"):
        return None
    if candidate.difficulty == "ULT" and chart_state[0] != "playable":
        return None
    return song, revision


def surveyed_candidates(data, artifact, observed, separator, provisional):
    for title, diff, value, locator in survey_rows(data, separator):
        if value is not None:
            yield Candidate(artifact, locator, observed, "estimate" if provisional else "reference", title, diff, value)


def dated_max_candidates(data, artifact, observed, version):
    parser = TableParser()
    parser.feed(data.decode("utf-8"))
    parser.close()
    marker = f">{version}<".encode("utf-8")
    if len(parser.rows) < 750 or marker not in data:
        raise ValueError(f"Unexpected MAX-eipi full table for {version}")
    for i, row in enumerate(parser.rows, 1):
        for difficulty, value in zip(DIFFICULTIES[:4], row["constants"], strict=True):
            yield Candidate(artifact, f"table row {i}/{difficulty}", observed, "checkpoint",
                            row["title"], difficulty, value)


def collect_checkpoint_evidence(db, raw, plan):
    editions = {edition[3]: edition for edition in db.editions.values()}
    grouped = defaultdict(list)
    for source in plan:
        artifact = source["artifact"]
        observed = db.artifacts[artifact]["observed_on"]
        adapter = source["adapter"]
        if adapter == "comparison":
            left, right = source["versions"]
            candidates = (
                (left if side == 0 else right,
                 Candidate(artifact, location, observed,
                           "checkpoint" if side == 0 else "frozen_match" if source.get("final_archive") else "reference",
                           title, difficulty, value, genre))
                for side, title, difficulty, genre, value, location in comparison_rows(
                    raw[artifact], tuple(source["columns"])
                )
                if side == 0 or right != "MATE"
            )
        elif adapter == "max":
            version = source["version"]
            if observed != editions[version][2]:
                raise ValueError("A full MAX-eipi release must be observed on the checkpoint day")
            candidates = ((version, row) for row in dated_max_candidates(raw[artifact], artifact, observed, version))
        elif adapter == "survey":
            candidates = ((source["version"], row) for row in surveyed_candidates(
                raw[artifact], artifact, observed, source.get("separator", "slash"), source.get("provisional", False)
            ))
        else:
            raise ValueError(f"Unsupported enrichment adapter {adapter}")

        for version, candidate in candidates:
            start, end, day, _ = editions[version]
            if not end:
                raise ValueError("An unfinished version requires a dated observation")
            if candidate.observed and adapter != "comparison" and not start <= candidate.observed <= day:
                raise ValueError(f"Survey date outside JP {version}")
            slot = ordinary_slot(db, candidate, day)
            if slot is None:
                db.issues[f"{artifact}_unlinked"] += 1
                continue
            grouped[version, day, *slot].append(candidate)
    return grouped


def archive_values(db, song, revision, day):
    return {row[0] for row in db.conn.execute("""
        SELECT json_extract(value_json, '$') FROM observation
        WHERE region='JP' AND song_id=? AND revision_id=? AND effective_on=? AND field='constant'
          AND artifact_id='oto-xversex' AND value_kind='exact'
    """, (song, revision, day))}


def classify_candidate(db, candidate, song, revision, day):
    if candidate.tier == "checkpoint":
        return "B"
    if candidate.tier == "frozen_match" and archive_values(db, song, revision, day) == {candidate.tenths}:
        return "B"
    return "C"


def record_candidate(db, song, revision, day, candidate, quality):
    raw = {"title": candidate.title, "difficulty": candidate.difficulty, "source_date": candidate.observed,
           "method": candidate.tier, "context": candidate.context}
    # A near-end survey gives a precise observation at its own date. Its
    # applicability to the checkpoint remains a separate C-grade assertion.
    song_at_survey = covering(db.song_states["JP", song], candidate.observed) if candidate.observed else None
    chart_at_survey = covering(db.chart_states["JP", revision], candidate.observed) if candidate.observed else None
    if (candidate.tier == "reference" and candidate.observed and candidate.observed != day
            and song_at_survey and song_at_survey[0] == "playable"
            and chart_at_survey and chart_at_survey[0] == "playable"):
        witness = db.constant(candidate.artifact, candidate.locator, "JP", song, revision, raw, candidate.tenths,
                              start=candidate.observed, end=offset(candidate.observed), activate=False)
        raw["dated_observation_id"] = witness
    return db.constant(candidate.artifact, candidate.locator + "/checkpoint", "JP", song, revision, raw,
                       candidate.tenths, start=day, end=offset(day), quality=quality,
                       kind="estimated" if candidate.tier == "estimate" else "exact", activate=False)


def resolve_checkpoint(db, version, day, song, revision, candidates):
    unique = sorted(set(candidates), key=lambda c: (c.artifact, c.locator, c.tenths))
    proof = [(c, classify_candidate(db, c, song, revision, day)) for c in unique]
    rows = [(c, quality, record_candidate(db, song, revision, day, c, quality)) for c, quality in proof]
    strong = [(c, oid) for c, quality, oid in rows if quality == "B"]
    values = {c.tenths for c, _ in strong}
    current = covering(db.constants["JP", revision], day)
    if current and current[0] == "conflict":
        db.conn.executemany("INSERT OR IGNORE INTO conflict_observation VALUES (?,?)",
                            [(current[3], oid) for _, _, oid in rows])
        return
    existing_quality = (db.conn.execute("SELECT quality FROM observation WHERE id=?", (current[2],)).fetchone()[0]
                        if current else None)
    if current and current[0] == "exact" and existing_quality in ("A", "B"):
        different = [(c, oid) for c, oid in strong if c.tenths != current[1]]
        if different:
            db.dispute("JP", version, revision, "constant", day, "additional_evidence_prior_verified_preserved",
                       [current[2], *(oid for _, oid in different)])
        return
    if len(values) > 1:
        cid = db.dispute("JP", version, revision, "constant", day, "checkpoint_sources_disagree",
                         [oid for _, oid in strong])
        db.constants["JP", revision].append((day, offset(day), 100, ("conflict", None, strong[0][1], cid)))
        return
    if not strong:
        # Keep numeric leads inspectable without deriving availability or Rating eligibility.
        if current is None and len({c.tenths for c, _, _ in rows}) == 1:
            c, _, oid = min(rows, key=lambda row: row[0].tier == "estimate")
            kind = "estimated" if c.tier == "estimate" else "exact"
            db.constants["JP", revision].append((day, offset(day), 25, (kind, c.tenths, oid, None)))
        return
    candidate, oid = strong[0]
    if current and current[1] != candidate.tenths:
        # For example, the 11/02 LOST table disagrees with the later exact checkpoint.
        cid = db.dispute("JP", version, revision, "constant", day, "prior_candidate_disagrees",
                         (current[2], oid))
        db.constants["JP", revision].append((day, offset(day), 100, ("conflict", None, oid, cid)))
        return
    db.constants["JP", revision].append((day, offset(day), 40, ("exact", candidate.tenths, oid, None)))
    # The edition-qualified row proves this chart exists at the checkpoint,
    # including lower charts for which the time machine had no numeric node.
    chart_state = covering(db.chart_states["JP", revision], day)
    if chart_state and chart_state[0] == "unknown":
        db.states(candidate.artifact, candidate.locator + "/checkpoint", "JP", song, candidate.title,
                  [(day, offset(day), "playable", "B", "point", 40)], revision=revision, display=chart_state[2])


def known_display_constant(level):
    if not re.fullmatch(r"[1-9]\+?", level):
        return None
    whole = int(level[0])
    if level.endswith("+"):
        return whole * 10 + 5 if whole >= 7 else None
    return whole * 10


def enrich_mate(db, raw, source):
    artifact = source["artifact"]
    observed = db.artifacts[artifact]["observed_on"]
    if not any(p[:3] == ("JP", "MATE", observed) for p in db.points):
        raise ValueError("MATE source needs a same-day JP catalog")
    rule = source.get("rule_artifact")
    if rule and not all(token.encode("utf-8") in raw[rule] for token in ("Lv.1～Lv.6", "Lv.7/8/9", "n.5")):
        raise ValueError("Pinned low-level rule text does not match")
    wiki = source.get("survey_artifact")
    survey = defaultdict(list)
    if wiki:
        if db.artifacts[wiki]["observed_on"] != observed:
            raise ValueError("MATE Wiki table must match the source observation date")
        for title, diff, notes, value, locator in mate_low_level_rows(raw[wiki]):
            survey[normalize_name(title), diff, notes].append((value, locator))
        if not survey:
            raise ValueError("No MATE Wiki table data")
    fingerprints = defaultdict(set)
    for (_, name, _, diff), (revision, notes, _, _) in db.current["JP"].items():
        fingerprints[name, diff, notes].add(revision)
    comparisons = defaultdict(list)
    mate_book = source.get("comparison_artifact")
    if mate_book:
        if db.artifacts[mate_book]["observed_on"] != observed:
            raise ValueError("MATE workbook must match the source observation date")
        for side, title, diff, genre, value, locator in comparison_rows(raw[mate_book], ("XVRSX", "Mate")):
            if side == 1:
                comparisons[normalize_name(title), diff, normalize_name(genre).replace(" ", "")].append((value, locator))
    genres = {(artifact, song, normalize_name(title)): normalize_name(genre).replace(" ", "")
              for artifact, song, title, genre in db.conn.execute("""
                  SELECT artifact_id, song_id, json_extract(value_json, '$.title'), json_extract(value_json, '$.genre')
                  FROM observation WHERE region='JP' AND field='song_metadata' AND effective_on=?
              """, (observed,)) if title and genre}
    named_charts = defaultdict(set)
    for (_, name, _, diff), (revision, _, song, origin) in db.current["JP"].items():
        genre = genres.get((origin, song, name))
        named_charts[name, diff, genre].add(revision)
    for key, meta, diff, display, value, locator in current_info_rows(raw[artifact]):
        match = db.current["JP"].get((key, normalize_name(meta["title"]), normalize_name(meta["artist"]), diff))
        if not match or db.artifacts[match[3]]["observed_on"] != observed:
            continue
        revision, notes, song, _ = match
        title = meta["title"]
        # Current-site origin edition tags are metadata only. Restrict all
        # display/constant observations to their actual MATE observation date.
        raw_row = {"title": title, "display": display, "constant_tenths": value, "official_id": key}
        oid = db.evidence(artifact, locator, "JP", song, revision, "display_level", raw_row, display,
                          start=observed, end=offset(observed), grain="point", quality="C")
        current = covering(db.constants["JP", revision], observed)
        fingerprint = normalize_name(title), diff, notes
        wiki_rows = survey.get(fingerprint, ()) if fingerprints[fingerprint] == {revision} else ()
        genre = genres.get((match[3], song, fingerprint[0]))
        sheet_identity = fingerprint[0], diff, genre
        sheet_rows = comparisons.get(sheet_identity, ()) if value is not None and named_charts[sheet_identity] == {revision} else ()
        if wiki_rows or sheet_rows:
            rows = [(wiki, candidate, position) for candidate, position in wiki_rows]
            rows.extend((mate_book, candidate, position) for candidate, position in sheet_rows)
            proofs = [(candidate, db.constant(
                witness, position, "JP", song, revision,
                {"title": title, "difficulty": diff, "notes": notes, "display_observation_id": oid}, candidate,
                start=observed, end=offset(observed), activate=False,
            )) for witness, candidate, position in rows]
            if current and current[0] == "exact":
                different = [proof for candidate, proof in proofs if candidate != current[1]]
                if different:
                    db.dispute("JP", "MATE", revision, "constant", observed,
                               "additional_evidence_prior_verified_preserved", [current[2], *different])
                continue
            values = {number for number, _ in proofs}
            if len(values) == 1 and (value is None or value in values):
                number, proof = proofs[0]
                db.constants["JP", revision].append((observed, offset(observed), 40, ("exact", number, proof, None)))
            else:
                # Info does not label estimates. Record its disagreement without publishing either value.
                candidates = [proof for _, proof in proofs]
                if value is not None and value not in values:
                    candidates.append(db.constant(artifact, locator, "JP", song, revision, raw_row, value,
                                                  start=observed, end=offset(observed), quality="C", activate=False))
                conflict = db.dispute("JP", "MATE", revision, "constant", observed, "current_constant_disagreement",
                                      candidates)
                db.constants["JP", revision].append((observed, offset(observed), 100,
                                                     ("conflict", None, candidates[0], conflict)))
        elif (current and current[0] == "estimated" and value is not None and rule
              and known_display_constant(display) == value == current[1]):
            derived = db.evidence(rule, f"Low-level rule / {artifact} {locator}", "JP", song, revision,
                                  "constant", {"level": display, "display_observation_id": oid,
                                               "agrees_with": current[2]}, value,
                                  start=observed, end=offset(observed), grain="point",
                                  quality="B", kind="rule_derived")
            db.constants["JP", revision].append((observed, offset(observed), 40, ("exact", value, derived, None)))
        elif value is not None and (current is None or current[0] == "estimated"):
            db.constant(artifact, locator, "JP", song, revision, raw_row, value,
                        start=observed, end=offset(observed), quality="C", activate=False)


def enrich(db, raw, plan):
    for source in plan:
        if source["adapter"] == "mate_info":
            enrich_mate(db, raw, source)
    older = [row for row in plan if row["adapter"] != "mate_info"]
    for (version, day, song, revision), rows in sorted(collect_checkpoint_evidence(db, raw, older).items()):
        resolve_checkpoint(db, version, day, song, revision, rows)
