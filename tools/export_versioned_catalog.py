"""Export the Versioned Catalog acceptance record without changing either package."""
import argparse
from collections import Counter, defaultdict
from contextlib import ExitStack
import json
from pathlib import Path

from build_history_catalog import atomic_write, format_json, private_root, validate_package
from export_history_constants import classification, csv_rows, jp_snapshots
from history_calendar import write_version_calendar
from history_import import identifier, sha
from _unibot_history.history import HistoryCatalog, catalog_state, snapshot_rows, trusted


B5_COVERAGE = (
    "observed_slots", "playable", "verified", "playable_unknown_constant", "estimated",
    "conflict", "availability_unknown", "not_released", "removed_or_suspended", "rule_derived",
    "revision_unverified", "newly_trusted", "lost_trust", "same_edition_new_trust",
    "otoge_witness_removed", "otoge_trust_lost", "ultima_false_gap_removed",
    "conflicts_closed", "conflicts_opened", "evidence_quality_changed", "availability_state_changed",
    "cross_edition_carry_lost",
)

JP_RATING_POOL_SOURCES = {
    "VERSE": "https://info-chunithm.sega.jp/8774/",
    "X-VERSE": "https://info-chunithm.sega.jp/10018/",
    "X-VERSE-X": "https://info-chunithm.sega.jp/10804/",
    "MATE": "https://info-chunithm.sega.jp/12026/",
}


def proof_index(conn):
    return {row["id"]: dict(row) for row in conn.execute("""
        SELECT o.id,o.artifact_id,o.raw_json,o.locator,o.effective_on,o.effective_before,
               o.quality,a.source_id,a.url,a.sha256,a.observed_on,a.upstream_version
        FROM observation o JOIN source_artifact a ON a.id=o.artifact_id
        WHERE o.region='JP' AND o.field IN ('constant','availability')
    """)}


def revision_keys(conn):
    # The current capture was reattached to its own region IDs after retiring
    # metadata anchors. Compare the unchanged pinned source keys and fingerprint.
    return {r[0]: (r[1], r[2], r[3], r[4]) for r in conn.execute("""
        SELECT e.revision_id,e.artifact_id,e.external_song_key,e.external_chart_key,c.fingerprint
        FROM external_id e JOIN chart_revision c ON c.id=e.revision_id
        JOIN source_artifact a ON a.id=e.artifact_id
        WHERE e.region='JP' AND a.source_id='chunisupport'
        ORDER BY e.artifact_id,e.external_song_key,e.external_chart_key
    """)}


def inventories(conn, stable):
    sources = proof_index(conn)
    data = {}
    days = {}
    for snap in jp_snapshots(conn):
        version, day = snap["version"], snap["on_date"]
        days[version] = day
        rows = {}
        for item in snapshot_rows(conn, "JP", day, snap["id"]):
            if item["difficulty"] == "WE":
                continue
            r = dict(item)
            r["state"] = catalog_state(item)
            r["category"] = classification(item)
            r["verified"] = trusted(item)
            r["proof"] = sources.get(r["constant_observation"], {})
            r["availability_proof"] = sources[r["chart_observation"]]
            key = stable.get(r["revision_id"], (r["revision_id"],))
            rows[key] = r
        data[version] = rows
    return days, data


def otoge_witness(row):
    proof = row.get("proof", {})
    raw = json.loads(proof.get("raw_json", "{}"))
    return proof.get("source_id") == "otoge" or (
        isinstance(raw, dict) and raw.get("method") == "frozen_match"
    )


def key_state(row):
    return tuple(row.get(name) for name in (
        "state", "category", "constant_tenths", "constant_quality", "constant_observation",
        "chart_quality", "song_quality",
    ))


def counts(row, *, active):
    state, category = row["state"], row["category"]
    out = Counter({
        "observed_slots": 1, "playable": state == "playable", "verified": row["verified"],
        "playable_unknown_constant": state == "playable" and category in ("unknown", "display_only", "evidence_insufficient"),
        "estimated": state == "playable" and category == "estimated",
        "conflict": state in ("playable", "unknown") and category == "conflict",
        "availability_unknown": state == "unknown",
        "not_released": state == "not_released",
        "removed_or_suspended": state in ("removed", "suspended", "disabled"),
        "rule_derived": row["verified"] and row["constant_evidence_kind"] == "rule_derived",
        "revision_unverified": row["verified"] and row["identity_basis"] != "verified_content",
    })
    if not active:
        out["b2_gaps"] = state in ("playable", "unknown") and not row["verified"]
    return out


def gap_reason(row):
    if row["state"] == "unknown":
        return "availability_unknown"
    if row["state"] != "playable":
        return None
    if row["category"] == "conflict":
        return "constant_conflict"
    if not row["verified"]:
        return "constant_unknown" if row["category"] in ("unknown", "display_only") else row["category"]
    if row["identity_basis"] != "verified_content":
        return "revision_unverified"


def conflicts(conn):
    dependencies = defaultdict(list)
    for source, parent, scope in conn.execute("SELECT * FROM source_dependency ORDER BY 1,2,3"):
        dependencies[source].append((parent, scope))
    groups = {}
    for r in conn.execute("""
        SELECT c.id,c.version,c.on_date,c.revision_id,c.field,c.reason,c.status,ch.id AS chart_id,
               ch.song_id,ch.difficulty,
               (SELECT min(name) FROM song_name WHERE song_id=ch.song_id) AS title,
               o.id AS observation_id,o.value_json,o.raw_json,o.quality,o.effective_on,
               a.id AS artifact_id,a.source_id,a.url,a.sha256,a.observed_on,a.upstream_version,
               a.parser_version,o.locator,z.selected_observation,z.reason AS resolution_reason,
               (SELECT version FROM region_release b WHERE b.region='JP' AND b.start_on<=o.effective_on
                AND (b.end_before IS NULL OR o.effective_on<b.end_before)) AS effective_edition
        FROM conflict c LEFT JOIN chart_revision rev ON rev.id=c.revision_id
        LEFT JOIN chart ch ON ch.id=rev.chart_id
        LEFT JOIN conflict_observation w ON w.conflict_id=c.id
        LEFT JOIN observation o ON o.id=w.observation_id
        LEFT JOIN source_artifact a ON a.id=o.artifact_id
        LEFT JOIN resolution z ON z.conflict_id=c.id
        WHERE c.region='JP' ORDER BY c.on_date,c.id,o.id
    """):
        row = dict(r)
        row["dependencies"] = json.dumps(dependencies[row["source_id"]], ensure_ascii=False)
        candidate = json.loads(row["raw_json"] or "{}")
        if row["source_id"] == "otoge":
            actual = "unverified; cross-version" if row["reason"] == "cross_version_contamination" else "unverified"
        else:
            actual = row["effective_edition"] if row["source_id"] else None
            if isinstance(candidate, dict) and candidate.get("genName"):
                actual = candidate["genName"]
        row["candidate_game_version"] = actual
        groups.setdefault(row["id"], []).append(row)
    return groups


def export_conflicts(b2, b5, output, tallies, old_keys, new_keys, updated):
    def key_for(row, keys):
        revision = row["revision_id"]
        return row["version"], keys.get(revision, (revision,)), row["field"]

    current = conflicts(b5)
    previous = conflicts(b2)
    open_now = {key_for(r[0], new_keys): r for r in current.values()
                if r[0]["status"] == "open"}
    fields = (
        "disposition", "version", "on_date", "song_id", "chart_id", "revision_id", "title", "difficulty",
        "id", "field", "reason", "status", "selected_observation", "active_decision", "observation_id",
        "value_json", "quality", "artifact_id", "source_id", "url", "sha256", "locator", "observed_on",
        "effective_on", "upstream_version", "candidate_game_version", "dependencies",
    )
    data = []
    b2_open = set()
    for rows in previous.values():
        r = rows[0]
        key = key_for(r, old_keys)
        involves_otoge = any(item["source_id"] == "otoge" for item in rows)
        if r["status"] == "open":
            b2_open.add(key)
            if key not in open_now:
                tallies[r["version"], r["difficulty"] or "ALL"]["conflicts_closed"] += 1
        active = updated.get(key[0], {}).get(key[1], {})
        if key in open_now:
            disposition = "baseline_dispute_still_open"
            decision = "CONFLICT remains in active package."
        else:
            result = f"{active['constant_tenths'] / 10:.1f}" if active.get("verified") else "UNKNOWN"
            if involves_otoge:
                disposition = "closed_retired_source" if r["status"] == "open" else "archived_retired_source"
                decision = f"{result}. Retired OTOGE evidence excluded."
            else:
                disposition = "baseline_dispute_reassessed"
                decision = f"{result}. Compare edition-qualified evidence and dates in the B5 delta."
        data.extend({**row, "disposition": disposition, "active_decision": decision} for row in rows)

    for rows in current.values():
        r = rows[0]
        key = key_for(r, new_keys)
        if r["status"] == "open" and key not in b2_open:
            tallies[r["version"], r["difficulty"] or "ALL"]["conflicts_opened"] += 1
        decision = "CONFLICT" if r["status"] == "open" else r["resolution_reason"]
        data.extend({**row, "disposition": "active_" + row["status"], "active_decision": decision} for row in rows)
    csv_rows(output / "versioned_constants_conflicts.csv", fields, (tuple(r.get(f) for f in fields) for r in data))
    return {
        state: len({r["id"] for r in data if r["disposition"] == state}) for state in (
            "closed_retired_source", "archived_retired_source", "baseline_dispute_still_open", "active_open"
        )
    }


def export(baseline, catalog, output):
    output.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        b2 = stack.enter_context(baseline._connect())
        b5 = stack.enter_context(catalog._connect())
        old_keys, new_keys = revision_keys(b2), revision_keys(b5)
        before_days, before = inventories(b2, old_keys)
        days, after = inventories(b5, new_keys)
        starts = dict(b5.execute("SELECT version,start_on FROM region_release WHERE region='JP'"))
        b2_counts, stats, gaps, delta, readiness = defaultdict(Counter), defaultdict(Counter), [], [], []
        conflict_counts = export_conflicts(b2, b5, output, stats, old_keys, new_keys, after)
        for version, day in days.items():
            original, updated = before[version], after[version]
            for key in sorted(original.keys() | updated.keys()):
                previous, row = original.get(key, {}), updated.get(key, {})
                item = row or previous
                diff = item["difficulty"]
                identity = (identifier("version", "JP", version), version, day, item["song_id"],
                            item["chart_id"], item["revision_id"], item["title"], diff)
                metrics = stats[version, diff]
                if previous:
                    b2_counts[version, diff].update(counts(previous, active=False))
                if row:
                    metrics.update(counts(row, active=True))
                gained = row.get("verified") and not previous.get("verified")
                lost = previous.get("verified") and not row.get("verified")
                proof = row.get("proof", {})
                inherited = gained and proof.get("effective_before") and proof["effective_before"] <= day
                old_proof = previous.get("proof", {})
                previous_node = old_proof.get("effective_on")
                cross_edition = (lost and old_proof.get("artifact_id") == "timemachine"
                                 and previous_node and previous_node < starts[version])
                retired = otoge_witness(previous)
                correction = (diff == "ULT" and previous.get("state") in ("playable", "unknown")
                              and not previous.get("verified") and row.get("state") == "not_released")
                quality_changed = previous.get("constant_quality") != row.get("constant_quality")
                metrics.update({
                    "newly_trusted": bool(gained), "lost_trust": bool(lost),
                    "same_edition_new_trust": bool(inherited), "otoge_witness_removed": retired,
                    "otoge_trust_lost": bool(retired and lost), "ultima_false_gap_removed": correction,
                    "evidence_quality_changed": bool(quality_changed),
                    "availability_state_changed": bool(previous and row and previous["state"] != row["state"]),
                    "cross_edition_carry_lost": bool(cross_edition),
                })
                if key_state(previous) != key_state(row):
                    delta.append((*identity, before_days[version], previous.get("chart_id"), previous.get("revision_id"),
                                  previous.get("state", "absent"), row.get("state", "absent"),
                                  previous.get("category"), row.get("category"), previous.get("constant_tenths"),
                                  row.get("constant_tenths"), old_proof.get("artifact_id"), proof.get("artifact_id"),
                                  old_proof.get("url"), proof.get("url"), old_proof.get("quality"), proof.get("quality"),
                                  previous.get("constant_observation"), row.get("constant_observation"),
                                  bool(gained), bool(lost), bool(inherited), retired, correction,
                                  bool(cross_edition)))
                reason = gap_reason(row) if row and diff in ("MAS", "ULT") else None
                if reason:
                    evidence = row["availability_proof"] if reason == "availability_unknown" else proof or row["availability_proof"]
                    gaps.append((*identity, row["state"], reason, row["category"], row["constant_tenths"],
                                 row["identity_basis"], row["conflict_id"], evidence["id"],
                                 evidence["url"], evidence["sha256"], evidence["observed_on"], evidence["effective_on"]))

        coverage = []
        for version, day in days.items():
            metrics = stats[version, "ALL"]
            previous = b2_counts[version, "ALL"]
            for diff in ("BAS", "ADV", "EXP", "MAS", "ULT"):
                metrics.update(stats[version, diff])
                previous.update(b2_counts[version, diff])
            for diff in ("ALL", "BAS", "ADV", "EXP", "MAS", "ULT"):
                old, new = b2_counts[version, diff], stats[version, diff]
                coverage.append((identifier("version", "JP", version), version, day, diff,
                                *(old[k] for k in ("verified", "playable_unknown_constant", "b2_gaps", "availability_unknown")),
                                *(new[k] for k in B5_COVERAGE)))

            rules = b5.execute("""
                SELECT rs.format,rs.status,a.url FROM region_release r
                LEFT JOIN ruleset rs ON rs.id=r.ruleset_id LEFT JOIN source_artifact a ON a.id=rs.artifact_id
                WHERE r.region='JP' AND r.version=?
            """, (version,)).fetchone()
            scope = "disputed" if version == "STAR" else ("partial" if rules[0] else "unconfirmed")
            legacy = version == "PARADISE LOST"
            pool_source = JP_RATING_POOL_SOURCES.get(version)
            recent = "observed R10 unavailable from best-score snapshot" if rules[0] == "B30/R10" else "not applicable"
            pools = ("official current-edition song pool and disjoint BEST pool; verify chart eligibility and arithmetic"
                     if pool_source else "not applicable")
            family = rules[0] or ("B30/N20" if pool_source else None)
            readiness.append((identifier("version", "JP", version), version, day, "PARTIAL" if legacy else "BLOCKED",
                              scope, family, rules[2], stats[version, "MAS"]["verified"], stats[version, "ULT"]["verified"],
                              stats[version, "MAS"]["playable"], stats[version, "ULT"]["playable"],
                              metrics["playable_unknown_constant"], metrics["availability_unknown"], "unverified",
                              "observed intervals and conditional unlocks; not a complete eligibility set",
                              recent, pools, pool_source, "closed",
                              "isolated legacy reference only" if legacy else "rating engine and mapping pending"))

        from history_availability import write_ultima_audit

        write_ultima_audit(output / "ultima_release_audit.csv", b2, b5, revision_keys=revision_keys)
        write_version_calendar(output / "historical_version_calendar.csv", observed_through=days["MATE"])

    common = ("version_id", "version", "date", "song_id", "chart_id", "revision_id", "title", "difficulty")
    csv_rows(output / "versioned_catalog_delta.csv", common + (
        "b2_date", "b2_chart_id", "b2_revision_id", "b2_state", "b5_state", "b2_category", "b5_category",
        "b2_tenths", "b5_tenths", "b2_artifact", "b5_artifact", "b2_url", "b5_url",
        "b2_quality", "b5_quality", "b2_observation", "b5_observation", "newly_trusted", "lost_trust",
        "same_edition_new_trust", "otoge_witness_removed", "ultima_false_gap_removed", "cross_edition_carry_lost",
    ), delta)
    csv_rows(output / "versioned_master_ultima_gaps.csv", common + (
        "state", "gap_reason", "numeric_category", "constant_tenths", "identity_basis", "conflict_id",
        "observation_id", "source_url", "source_sha256", "source_observed_on", "effective_on",
    ), gaps)
    csv_rows(output / "versioned_constants_coverage.csv", (
        "version_id", "version", "date", "difficulty", "b2_verified", "b2_playable_unknown_constant",
        "b2_gaps", "b2_availability_unknown", *B5_COVERAGE,
    ), coverage)
    csv_rows(output / "rating_readiness_by_version.csv", (
        "version_id", "version", "date", "status", "formula_status", "rule_family", "rule_source", "master_verified",
        "ultima_verified", "master_playable", "ultima_playable", "playable_unknown_constant",
        "availability_unknown", "cn_jp_revision_mapping", "chart_eligibility", "r10", "best30_new20", "best30_new20_source", "query_status", "blocker",
    ), readiness)
    totals = Counter()
    for version in days:
        totals.update(stats[version, "ALL"])
    atomic_write(output / "acceptance.json", format_json({
        "baseline": baseline.release_id, "release": catalog.release_id, "b2_verified": sum(
            b2_counts[version, "ALL"]["verified"] for version in days),
        "checkpoints": len(days), "totals": totals, "conflicts_by_disposition": conflict_counts,
        "master_ultima_gaps": dict(sorted(Counter(f"{row[7]}:{row[9]}" for row in gaps).items())),
        "sha256": {p.name: sha(p.read_bytes()) for p in sorted(output.glob("*.csv"))},
    }))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for path in (args.baseline, args.catalog):
        validate_package(path)
    export(HistoryCatalog(args.baseline), HistoryCatalog(args.catalog), private_root(args.output))


if __name__ == "__main__":
    main()
