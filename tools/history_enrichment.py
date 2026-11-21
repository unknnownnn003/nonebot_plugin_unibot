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
                           "checkpoint" if side == 0 else "reference",
                           title, difficulty, value, genre))
                for side, title, difficulty, genre, value, location in comparison_rows(
                    raw[artifact], tuple(source["columns"])
                )
                if side == 0 or right != "MATE"
            )
        elif adapter == "max":
            version = source["version"]
            start, _, day, _ = editions[version]
            if not observed or not start <= observed <= day:
                raise ValueError("A MAX-eipi release must be observed within its recorded edition")
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


def record_candidate(db, song, revision, day, candidate, quality):
    raw = {"title": candidate.title, "difficulty": candidate.difficulty, "source_date": candidate.observed,
           "method": candidate.tier, "context": candidate.context}
    # Preserve the dated primary observation separately from an unqualified
    # reference. The edition resolver can extend the dated exact evidence.
    song_at_survey = covering(db.song_states["JP", song], candidate.observed) if candidate.observed else None
    chart_at_survey = covering(db.chart_states["JP", revision], candidate.observed) if candidate.observed else None
    if (candidate.tier == "reference" and candidate.observed and candidate.observed != day
            and song_at_survey and song_at_survey[0] == "playable"
            and chart_at_survey and chart_at_survey[0] == "playable"):
        witness = db.constant(candidate.artifact, candidate.locator, "JP", song, revision, raw, candidate.tenths,
                              start=candidate.observed, end=offset(candidate.observed),
                              grain="day", priority=30)
        raw["dated_observation_id"] = witness
    return db.constant(candidate.artifact, candidate.locator + "/checkpoint", "JP", song, revision, raw,
                       candidate.tenths, start=day, end=offset(day), quality=quality,
                       kind="estimated" if candidate.tier == "estimate" else "exact", activate=False)


def resolve_checkpoint(db, version, day, song, revision, candidates):
    unique = sorted(set(candidates), key=lambda c: (c.artifact, c.locator, c.tenths))
    rows = [(c, record_candidate(db, song, revision, day, c, "B" if c.tier == "checkpoint" else "C"))
            for c in unique]
    strong = [(c, oid) for c, oid in rows if c.tier == "checkpoint"]
    if not strong:
        # A survey is kept as a candidate and cannot overwrite an active fact.
        if covering(db.constants["JP", revision], day) is None and len({c.tenths for c, _ in rows}) == 1:
            c, oid = min(rows, key=lambda row: row[0].tier == "estimate")
            kind = "estimated" if c.tier == "estimate" else "exact"
            db.constants["JP", revision].append((day, offset(day), 25, (kind, c.tenths, oid, None)))
        return
    for candidate, oid in strong:
        db.constants["JP", revision].append((day, offset(day), 40, ("exact", candidate.tenths, oid, None)))
    candidate, _ = strong[0]
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
                for number, proof in proofs:
                    db.constants["JP", revision].append(
                        (observed, offset(observed), 40, ("exact", number, proof, None))
                    )
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


def resolve_version_constants(db):
    """Apply JP edition invariance to one chart revision at a time."""
    observations = {row[0]: row[1:] for row in db.conn.execute(
        "SELECT id,quality,song_id,effective_on FROM observation WHERE region='JP' AND field='constant'"
    )}
    through = db.conn.execute(
        "SELECT through_on FROM region_release WHERE region='JP' AND end_before IS NULL"
    ).fetchone()
    for (region, revision), periods in sorted(db.constants.items()):
        if region != "JP":
            continue
        revised = []
        for start, end, checkpoint, version in db.editions.values():
            stop = end or offset(through[0] if through else checkpoint)
            # Admit only evidence with its own placement within this edition.
            admitted = [p for p in periods if start <= p[0] < stop
                        and (observations[p[3][2]][2] is None or start <= observations[p[3][2]][2] < stop)]
            if not admitted:
                continue
            strong = [p for p in admitted if p[3][0] == "exact" and observations[p[3][2]][0] in ("A", "B")]
            disputed = [p for p in admitted if p[3][0] == "conflict"]
            values = {p[3][1] for p in strong}
            if len(values) > 1:
                witnesses = sorted({p[3][2] for p in strong})
                conflict = db.dispute(region, version, revision, "constant", checkpoint,
                                      "same_edition_exact_sources_disagree", witnesses)
                fact = ("conflict", None, witnesses[0], conflict)
            elif disputed:
                fact = max(disputed, key=lambda p: (p[2], p[0], p[3][2]))[3]
            elif strong:
                fact = max(strong, key=lambda p: (observations[p[3][2]][0] == "A", p[2], p[0], p[3][2]))[3]
            else:
                revised.extend((lo, min(hi or stop, stop), priority, value)
                               for lo, hi, priority, value in admitted)
                continue
            # An edition-wide number has no effect on availability. Exclude
            # dates explicitly outside the song or chart's playable lifespan.
            song = observations[fact[2]][1]
            song_periods = db.song_states[region, song]
            chart_periods = db.chart_states[region, revision]
            boundaries = sorted({start, stop} | {
                bound for lo, hi, _, _ in (*song_periods, *chart_periods)
                for bound in (lo, hi) if bound is not None and start < bound < stop
            })
            for lo, hi in zip(boundaries, boundaries[1:]):
                states = [covering(items, lo) for items in (song_periods, chart_periods)]
                if all(state and state[0] in ("playable", "unknown") for state in states):
                    revised.append((lo, hi, 40, fact))
        db.constants[region, revision] = revised
