"""Compare JP B5 snapshots with a read-only Time Machine continuity preview."""

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from build_history_catalog import artifact_digest
from export_history_constants import classification, csv_rows, jp_snapshots
from history_import import decimal_tenths, js_array, offset, sha
from _unibot_history.history import DIFFICULTIES, HistoryCatalog, catalog_state, iso_day, snapshot_rows, trusted


LOCK = Path(__file__).with_name("history_inputs.json")
B5_RELEASE = "d6eb3f9ed6253253e879ae4a555b8032fe4ba928cea2584a54ff2273250f7ea0"
B5_SQLITE_SHA256 = "e95823f9e76c11f967336ab31676be11c0e1e784c4360957788235d9a45b10ab"
ORDINARY = DIFFICULTIES[:5]
UNKNOWN = ("unknown", "display_only", "evidence_insufficient")
STRONG = ("A", "B")
COUNTS = (
    "old_trusted", "preview_trusted", "old_unknown", "unknown_recovered_by_carry", "usable_unknown_carry",
    "directly_trustable", "needs_independent_verification", "availability_unknown_with_carry",
    "known_unavailable_with_carry",
    "identity_blocked_with_carry", "new_source_conflicts", "trusted_value_mismatch",
    "source_disagreements_with_unverified_lineage", "source_disagreements_with_independent_reference",
    "remaining_playable_gaps", "playable_gaps_without_numeric_claim", "availability_unknown",
    "existing_conflicts", "old_estimated", "unknown_with_any_timemachine_value",
    "unknown_without_timemachine_value",
)


@dataclass(frozen=True)
class Node:
    day: str
    version: str
    tenths: int
    locator: str


@dataclass(frozen=True)
class Timeline:
    key: str
    release: str | None
    last_playable: str | None
    gaps: tuple
    wiki: str
    nodes: tuple

    def at(self, day, through):
        if day > through:
            return None
        index = bisect_right(self.nodes, day, key=lambda node: node.day) - 1
        return self.nodes[index] if index >= 0 else None

    def song_available(self, day):
        if ((self.release and day < self.release) or (self.last_playable and day > self.last_playable)
                or any(start <= day < end for start, end in self.gaps)):
            return False
        return True if self.release else None


def timelines(raw, editions):
    histories = {}
    ids = set()
    for index, song in enumerate(js_array(raw, "APP_DATA")):
        key = song["sdvx_id"]
        if not key or key in ids:
            raise ValueError(f"Missing or duplicate Time Machine ID at APP_DATA[{index}]")
        ids.add(key)
        for diff in ("EXP", "MAS", "ULT"):
            nodes = []
            for position, entry in enumerate(song["charts"].get(diff, {}).get("timeline", ())):
                day = iso_day(entry["date"])
                start, stop, version = editions[entry["gen"]]
                locator = f"APP_DATA[{index}].charts.{diff}.timeline[{position}]"
                if not start <= day < stop or (nodes and nodes[-1].day >= day):
                    raise ValueError(f"Invalid node date or edition at {locator}")
                nodes.append(Node(day, version, decimal_tenths(entry["const"]), locator))
            histories[key, diff] = Timeline(
                key, song["releaseDate"], song["endDate"], tuple(song.get("gaps", ())),
                song["wiki_url"], tuple(nodes),
            )
    return histories


def locked_timelines(conn, source_dir, lock=LOCK):
    config = json.loads(lock.read_text(encoding="utf-8"))
    if "otoge" in json.dumps(config).casefold():
        raise ValueError("Retired source in audit lock")
    artifact = next(a for a in config["artifacts"] if a["id"] == "timemachine")
    db_pin = conn.execute(
        "SELECT sha256, observed_on, upstream_version FROM source_artifact WHERE id='timemachine'"
    ).fetchone()
    expected = tuple(artifact[name] for name in ("sha256", "observed_on", "upstream_version"))
    if db_pin is None or tuple(db_pin) != expected:
        raise ValueError("B5 package and locked Time Machine capture differ")
    root = source_dir.resolve()
    path = (root / artifact["file"]).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Time Machine capture escapes source directory")
    raw = path.read_bytes()
    if sha(raw) != artifact["sha256"]:
        raise ValueError("Time Machine SHA-256 mismatch")
    through = iso_day(artifact["observed_on"])
    releases = conn.execute(
        "SELECT version,start_on,end_before FROM region_release WHERE region='JP' ORDER BY start_on"
    )
    editions = {index: (start, end or offset(through), version)
                for index, (version, start, end) in enumerate(releases, 1)}
    return timelines(raw, editions), artifact


def revision_bindings(conn):
    by_revision = defaultdict(set)
    by_key = defaultdict(set)
    for revision, key, difficulty in conn.execute("""
        SELECT revision_id,external_song_key,external_chart_key FROM external_id
        WHERE region='JP' AND artifact_id='timemachine' AND revision_id IS NOT NULL
    """):
        by_revision[revision].add((key, difficulty))
        by_key[key, difficulty].add(revision)
    unique, ambiguous = {}, {}
    for revision, keys in by_revision.items():
        if len(keys) == 1 and len(by_key[next(iter(keys))]) == 1:
            unique[revision] = next(iter(keys))
        else:
            ambiguous[revision] = sorted(keys)
    return unique, ambiguous


def witnesses(conn):
    if conn.execute("""
        SELECT 1 FROM source WHERE lower(id) LIKE '%otoge%'
        UNION ALL SELECT 1 FROM source_dependency
            WHERE lower(source_id) LIKE '%otoge%' OR lower(upstream_id) LIKE '%otoge%'
        UNION ALL SELECT 1 FROM observation WHERE json_extract(raw_json, '$.method')='frozen_match'
        LIMIT 1
    """).fetchone():
        raise ValueError("Retired evidence in B5 package")
    found = defaultdict(list)
    for row in conn.execute("""
        SELECT o.revision_id,o.id AS observation_id,o.effective_on,o.quality,o.locator,
               o.artifact_id,a.source_id,a.url,a.sha256,o.value_json,
               json_extract(o.raw_json, '$.method') AS method
        FROM observation o JOIN source_artifact a ON a.id=o.artifact_id
        WHERE o.region='JP' AND o.field='constant' AND o.value_kind='exact'
          AND o.quality IN ('A','B','C') AND a.id <> 'timemachine'
          AND json_type(o.value_json)='integer'
        ORDER BY o.artifact_id,o.locator,o.id
    """):
        proof = dict(row)
        revision = proof.pop("revision_id")
        proof["tenths"] = json.loads(proof.pop("value_json"))
        found[revision].append(proof)
    return found


def numeric_lineage(source):
    # The JP history pipeline cites WikiWiki. Arcade Songs supplies the baseline
    # and can share low-level measurements with the surveys. The paired books
    # and rks surveys are treated as one family, never as two independent votes.
    if source == "wikiwiki":
        return "wiki_upstream"
    if source in ("arcade-songs", "chunirec", "chunimaru", "chuni-info"):
        return "current_baseline_or_unverified_lineage"
    if source in ("jp-paired-sheets", "rks-constants"):
        return "paired_sheet_survey_possible_overlap"
    if source in ("max-eipi", "legacy-package"):
        return "max_eipi_family"
    if source in ("risette", "verse-sheet"):
        return source
    return "unverified_lineage"


def compare(row, version, start, day, through, timeline, unbound, observations, availability_proofs):
    state, old, was_trusted = catalog_state(row), classification(row), trusted(row)
    playable = state == "playable" and all(row[key] in STRONG for key in ("song_quality", "chart_quality"))
    node = timeline.at(day, through) if timeline else None
    carry = bool(node and node.version != version)
    same_edition = [{**proof, "lineage": numeric_lineage(proof["source_id"])}
                    for proof in observations if proof["effective_on"] and start <= proof["effective_on"] <= day]
    independent = [proof for proof in same_edition
                   if proof["lineage"] in ("max_eipi_family", "risette", "verse-sheet")]
    ambiguous = bool(unbound)
    raw_available = timeline.song_available(day) if timeline and day <= through else None
    # A missing raw release date is not evidence against an independently
    # documented B5 release. An explicit outage or last day is a blocker.
    availability_blocked = not playable or raw_available is False
    usable = not availability_blocked and not ambiguous
    agreeing = [p for p in independent if node and p["tenths"] == node.tenths]
    opposing = [p for p in independent if node and p["tenths"] != node.tenths]
    related = [proof for proof in same_edition if proof not in independent]
    related_agreement = any(node and proof["tenths"] == node.tenths for proof in related)
    related_disagreement = any(node and proof["tenths"] != node.tenths for proof in related)
    opposing_strong = any(proof["quality"] in STRONG for proof in opposing)
    new_conflict = carry and usable and opposing_strong and old != "conflict"
    upgrade = (carry and usable and not was_trusted and old != "conflict"
               and any(p["quality"] in STRONG for p in agreeing) and not opposing and not related_disagreement
               and all(p["tenths"] == node.tenths for p in same_edition if p["quality"] in STRONG))
    trusted_mismatch = carry and usable and was_trusted and node.tenths != row["constant_tenths"]
    if was_trusted:
        status, reason = "VERIFIED", "B5 trusted evidence retained"
    elif old == "conflict":
        status, reason = "CONFLICT", "B5 conflict requires manual resolution"
    elif upgrade:
        status, reason = "VERIFIED", "Agreeing target-edition exact A/B evidence"
    elif ambiguous:
        status, reason = "IDENTITY_BLOCKED", "Time Machine key does not identify a unique B5 revision"
    elif day > through:
        status, reason = "UNKNOWN", "Checkpoint exceeds Time Machine coverage"
    elif not timeline:
        status, reason = "UNKNOWN", "No Time Machine timeline for this difficulty or revision"
    elif not node:
        status, reason = "UNKNOWN", "First node is later than checkpoint" if timeline.nodes else "Empty Time Machine timeline"
    elif not usable:
        status = "AVAILABILITY_BLOCKED"
        reason = ("Time Machine release/removal range excludes this checkpoint" if raw_available is False
                  else "B5 song or chart availability lacks A/B evidence at this checkpoint")
    elif carry and (opposing or related_disagreement):
        status, reason = "CANDIDATE_CONFLICT", "Inherited claim disagrees with target-edition numeric evidence"
    elif carry:
        status, reason = "CANDIDATE", "Target-edition exact A/B confirmation missing"
    else:
        status, reason = "UNKNOWN", "Same-edition raw value already in scope of B5; its evidence gate remains unmet"
    return {
        "version": version, "date": day, "title": row["title"], "difficulty": row["difficulty"],
        "chart_id": row["chart_id"], "revision_id": row["revision_id"], "identity_basis": row["identity_basis"],
        "content_revision_verified": row["identity_basis"] == "verified_content",
        "identity_blocked": ambiguous, "identity_blocked_with_carry": any(
            lead["node_version"] not in (None, version) for lead in unbound
        ), "availability": state, "playable_confirmed": playable,
        "availability_blocks_claim": availability_blocked,
        "song_state": row["song_state"], "chart_state": row["chart_state"],
        "song_quality": row["song_quality"], "chart_quality": row["chart_quality"],
        "song_availability_artifact": availability_proofs.get(row["song_observation"]),
        "chart_availability_artifact": availability_proofs.get(row["chart_observation"]),
        "source_key": timeline.key if timeline else None, "wiki": timeline.wiki if timeline else None,
        "source_release": timeline.release if timeline else None,
        "source_last_playable": timeline.last_playable if timeline else None,
        "source_song_available": raw_available, "source_outages": json.dumps(timeline.gaps) if timeline else None,
        "unbound_timemachine_leads": json.dumps(unbound, sort_keys=True),
        "node_date": node.day if node else None, "node_version": node.version if node else None,
        "node_locator": node.locator if node else None, "node_tenths": node.tenths if node else None,
        "old_timemachine_edition_eligible": bool(node and not carry),
        "cross_version": carry, "independent_source_families": ", ".join(sorted({p["lineage"] for p in independent})),
        "independent_agreement": bool(agreeing), "independent_disagreement": bool(opposing),
        "agreement_with_unverified_lineage": related_agreement,
        "same_edition_evidence": json.dumps(same_edition, ensure_ascii=False, sort_keys=True),
        "old_status": old, "old_constant_tenths": row["constant_tenths"],
        "preview_status": status, "can_upgrade": upgrade, "reason": reason,
        "new_source_conflict": new_conflict, "trusted_value_mismatch": trusted_mismatch,
        "source_disagreement_with_unverified_lineage": carry and usable and related_disagreement,
        "source_disagreement_with_independent_reference": carry and usable and any(
            proof["quality"] == "C" for proof in opposing
        ),
    }


def metrics(item):
    old, status = item["old_status"], item["preview_status"]
    state = item["availability"]
    playable = state == "playable"
    eligible_state = state in ("playable", "unknown")
    unknown = playable and old in UNKNOWN
    gap = playable and status != "VERIFIED"
    has_number = item["node_tenths"] is not None
    return {
        "old_trusted": old == "verified", "preview_trusted": status == "VERIFIED",
        "old_unknown": unknown, "unknown_recovered_by_carry": unknown and item["cross_version"],
        "usable_unknown_carry": unknown and item["cross_version"] and not item["availability_blocks_claim"],
        "directly_trustable": item["can_upgrade"],
        "needs_independent_verification": unknown and status in ("CANDIDATE", "CANDIDATE_CONFLICT"),
        "availability_unknown_with_carry": eligible_state and item["availability_blocks_claim"] and item["cross_version"],
        "known_unavailable_with_carry": not eligible_state and item["cross_version"],
        "identity_blocked_with_carry": item["identity_blocked_with_carry"],
        "new_source_conflicts": item["new_source_conflict"],
        "trusted_value_mismatch": item["trusted_value_mismatch"],
        "source_disagreements_with_unverified_lineage": item["source_disagreement_with_unverified_lineage"],
        "source_disagreements_with_independent_reference": item["source_disagreement_with_independent_reference"],
        "remaining_playable_gaps": gap,
        "playable_gaps_without_numeric_claim": gap and not has_number and item["old_constant_tenths"] is None,
        "availability_unknown": item["availability"] == "unknown",
        "existing_conflicts": playable and old == "conflict",
        "old_estimated": playable and old == "estimated",
        "unknown_with_any_timemachine_value": unknown and has_number,
        "unknown_without_timemachine_value": unknown and not has_number,
    }


def scan(conn, histories, through, version=None, difficulty=None):
    starts = dict(conn.execute("SELECT version,start_on FROM region_release WHERE region='JP'"))
    if version is not None and version not in starts:
        raise ValueError(f"Unknown JP edition: {version}")
    bindings, ambiguous = revision_bindings(conn)
    proofs = witnesses(conn)
    availability = dict(conn.execute("SELECT id,artifact_id FROM observation WHERE region='JP' AND field='availability'"))
    for snapshot in jp_snapshots(conn):
        edition, day = snapshot["version"], snapshot["on_date"]
        if version and edition != version:
            continue
        counts = {diff: Counter() for diff in ((difficulty,) if difficulty else ORDINARY)}
        charts = defaultdict(set)
        details = []
        for row in snapshot_rows(conn, "JP", day, snapshot["id"]):
            diff, revision = row["difficulty"], row["revision_id"]
            if diff not in counts:
                continue
            binding = bindings.get(revision)
            if binding is not None and binding[1] != diff:
                raise ValueError("Time Machine mapping difficulty disagrees with catalog")
            if binding is None and revision not in ambiguous and catalog_state(row) not in ("playable", "unknown"):
                continue
            unbound = []
            for key in ambiguous.get(revision, ()):
                timeline = histories.get(key)
                node = timeline.at(day, through) if timeline else None
                unbound.append({"source_key": key[0], "difficulty": key[1],
                                "node_version": node.version if node else None,
                                "node_tenths": node.tenths if node else None,
                                "node_locator": node.locator if node else None})
            item = compare(row, edition, starts[edition], day, through, histories.get(binding),
                           unbound, proofs[revision], availability)
            flags = metrics(item)
            counts[diff].update(flags)
            for metric, applies in flags.items():
                if applies:
                    charts[metric].add(item["chart_id"])
            if (flags["remaining_playable_gaps"] or flags["new_source_conflicts"] or flags["trusted_value_mismatch"]
                    or flags["availability_unknown_with_carry"] or flags["directly_trustable"]
                    or flags["known_unavailable_with_carry"] or flags["identity_blocked_with_carry"]
                    or flags["source_disagreements_with_unverified_lineage"]
                    or flags["source_disagreements_with_independent_reference"]):
                details.append(item)
        yield edition, day, counts, details, charts


def write_details(path, details, fields):
    write_csv(path, fields, ([item[name] for name in fields] for item in details))


def write_csv(path, fields, rows):
    with TemporaryDirectory(dir=path.parent) as temporary:
        staged = Path(temporary) / path.name
        csv_rows(staged, fields, rows)
        staged.replace(path)


def export(catalog, sources, output, version=None, difficulty=None, *, lock=LOCK):
    output = output.resolve()
    if output.is_relative_to(catalog.path.parent) or output.is_relative_to(sources.resolve()):
        raise ValueError("Use a separate audit output directory")
    with catalog._connect() as conn:
        histories, artifact = locked_timelines(conn, sources, lock)
        through = artifact["observed_on"]
        results = list(scan(conn, histories, through, version, difficulty))
    if not results:
        raise ValueError("No JP snapshots matched")
    entries = [item for _, _, _, details, _ in results for item in details]
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "b51_continuity_by_version.csv", ("version", "date", "difficulty", *COUNTS), (
        (edition, day, diff, *(counts[diff][metric] for metric in COUNTS))
        for edition, day, counts, _, _ in results for diff in counts
    ))
    write_details(output / "b51_continuity_candidates.csv",
                  (item for item in entries if item["node_tenths"] is not None
                   or item["identity_blocked_with_carry"]), DETAIL_FIELDS)
    write_details(output / "b51_xversex_38_audit.csv", (item for item in entries
                  if item["version"] == "X-VERSE-X" and item["difficulty"] in ("MAS", "EXP")
                  and item["availability"] == "playable" and item["old_status"] in UNKNOWN), DETAIL_FIELDS)
    write_details(output / "b51_new_conflicts.csv",
                  (item for item in entries if item["new_source_conflict"] or item["trusted_value_mismatch"]
                   or item["source_disagreement_with_unverified_lineage"]
                   or item["source_disagreement_with_independent_reference"]), DETAIL_FIELDS)
    write_details(output / "b51_remaining_gaps.csv",
                  (item for item in entries if metrics(item)["remaining_playable_gaps"]), DETAIL_FIELDS)
    totals = {name: sum(count[name] for _, _, counts, _, _ in results for count in counts.values()) for name in COUNTS}
    distinct = {name: len(set().union(*(charts[name] for _, _, _, _, charts in results))) for name in COUNTS}
    return {"baseline": catalog.release_id, "timemachine_sha256": artifact["sha256"],
            "timemachine_through": through, "versions": len(results), "unit": "edition × chart",
            "counts": totals, "distinct_charts": distinct}


DETAIL_FIELDS = (
    "version", "date", "title", "difficulty", "chart_id", "revision_id", "identity_basis",
    "content_revision_verified", "identity_blocked", "identity_blocked_with_carry", "availability", "playable_confirmed",
    "availability_blocks_claim", "song_state", "chart_state",
    "song_quality", "chart_quality", "song_availability_artifact", "chart_availability_artifact",
    "source_key", "wiki", "source_release", "source_last_playable", "source_song_available",
    "source_outages", "unbound_timemachine_leads", "node_date", "node_version", "node_locator", "node_tenths",
    "old_timemachine_edition_eligible", "cross_version",
    "independent_source_families", "independent_agreement", "independent_disagreement",
    "agreement_with_unverified_lineage",
    "same_edition_evidence", "old_status", "old_constant_tenths", "preview_status", "can_upgrade",
    "reason", "new_source_conflict", "trusted_value_mismatch", "source_disagreement_with_unverified_lineage",
    "source_disagreement_with_independent_reference",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, type=Path, help="B5 release folder or immutable catalog.sqlite")
    parser.add_argument("--sources", required=True, type=Path, help="directory of locked B5 source captures")
    parser.add_argument("--output", required=True, type=Path, help="separate folder for the five audit CSVs")
    parser.add_argument("--version", help="limit to a JP edition, e.g. X-VERSE-X")
    parser.add_argument("--difficulty", choices=ORDINARY)
    args = parser.parse_args()
    catalog = HistoryCatalog(args.catalog)
    if catalog.release_id != B5_RELEASE or artifact_digest(catalog.path) != B5_SQLITE_SHA256:
        parser.error("Expected the immutable B5 release and its original SQLite SHA-256")
    print(json.dumps(export(catalog, args.sources, args.output.resolve(),
                            args.version, args.difficulty), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
