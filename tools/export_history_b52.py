"""Compare B5 and B5.2 immutable JP checkpoints and export auditable CSVs."""
import argparse
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path

from build_history_catalog import format_json, private_root
from export_history_constants import csv_rows, jp_snapshots
from _unibot_history.history import DIFFICULTIES, HistoryCatalog, public_chart, snapshot_rows


VERIFIED = {"wiki_verified", "independently_verified", "timemachine_edition_verified"}
REFERENCE = {"timemachine_reference", "reference"}
TRACKED = ("playable", "verified", "reference", "estimated", "unknown", "conflict", "availability_unknown",
           "verified_revision_unproven", "newly_verified", "corrected", "lost_verified", "wiki_decisions",
           "new_conflicts", "resolved_conflicts")


def snapshots(conn):
    for point in jp_snapshots(conn):
        rows = (public_chart(row) for row in snapshot_rows(conn, "JP", point["on_date"], point["id"])
                if row["difficulty"] != "WE")
        yield point["version"], point["on_date"], {row["revision_id"]: row for row in rows}


def emit(output, name, rows, fields=None):
    fields = fields or (list(rows[0]) if rows else ["no_matching_records"])
    csv_rows(output / f"b52_{name}.csv", fields, (tuple(row.get(key) for key in fields) for row in rows))


def label(row):
    if row is None:
        return "absent"
    return row["constant_status"] if row["state"] == "playable" else "not_applicable"


def gap_action(status, difficulty, marker):
    if status == "conflict":
        return "核对同期原表、修订记录及精确值"
    if status == "estimated" or difficulty in ("BAS", "ADV"):
        return "寻找同期精确表或经验证的低难度定数规则"
    if marker == "source_unavailable":
        return "取得曲目页或当代定数表的可验证版本记录"
    return "核对当代精确测量、版本及谱面修订"


def observations(conn):
    return {row["id"]: dict(row) for row in conn.execute("""
        SELECT o.id, o.revision_id, o.value_json, o.locator, o.quality, o.effective_on, o.effective_before,
               o.value_kind, o.artifact_id, a.url, a.sha256
        FROM observation o JOIN source_artifact a ON a.id=o.artifact_id
        WHERE o.region='JP' AND o.field='constant'
    """)}


def disputes(conn, source):
    grouped = defaultdict(list)
    for row in conn.execute("""
        SELECT c.*, ch.id AS chart_id, ch.difficulty, p.observation_id, r.selected_observation,
               r.reason AS decision_reason
        FROM conflict c JOIN conflict_observation p ON p.conflict_id=c.id
        JOIN chart_revision rev ON rev.id=c.revision_id JOIN chart ch ON ch.id=rev.chart_id
        LEFT JOIN resolution r ON r.conflict_id=c.id
        WHERE c.region='JP' AND c.field='constant' ORDER BY c.on_date,c.id,p.observation_id
    """):
        data = dict(row)
        data.update({f"candidate_{k}": v for k, v in source[row["observation_id"]].items()})
        grouped[row["version"], row["revision_id"]].append(data)
    return grouped


def basis(row, source):
    proof = source.get(row["constant_observation"], {})
    return dict(source=proof.get("artifact_id"), url=proof.get("url"), locator=proof.get("locator"),
                sha256=proof.get("sha256"), quality=proof.get("quality"),
                effective_on=proof.get("effective_on"), effective_before=proof.get("effective_before"))


def count(stats, version, day, difficulty, entries):
    values = stats["verified"] + stats["reference"] + stats["estimated"]
    total = stats["playable"]
    return dict(version=version, date=day, difficulty=difficulty, **{key: stats[key] for key in TRACKED},
                unique_playable_charts=len(entries), numeric=values,
                numeric_coverage_pct=round(100 * values / total, 4) if total else None,
                verified_coverage_pct=round(100 * stats["verified"] / total, 4) if total else None)


def export(new, old, markers, audit_path, output):
    output.mkdir(parents=True, exist_ok=True)
    counts, decisions, changes, gaps, conflicts, identity, readiness = ([] for _ in range(7))
    candidate_audit, x_audit, wiki_index = [], [], []
    all_playable, all_verified, all_ref, all_unknown, all_estimated = (set() for _ in range(5))
    with new._connect() as current, old._connect() as baseline:
        before = {version: rows for version, _, rows in snapshots(baseline)}
        proofs, prior_proofs = observations(current), observations(baseline)
        disputes_by_chart = disputes(current, proofs)
        releases = {row["revision_id"] for row in current.execute(
            "SELECT revision_id FROM observation WHERE region='JP' AND field='chart_release' AND quality IN ('A','B')"
        )}
        bound = {row["revision_id"]: row["song_key"] for row in current.execute("""
            SELECT revision_id, min(external_song_key) AS song_key FROM external_id
            WHERE region='JP' AND artifact_id='timemachine' AND revision_id IS NOT NULL
            GROUP BY revision_id HAVING count(DISTINCT external_song_key)=1
        """)}
        rules = {row["version"]: row["ruleset_id"] for row in current.execute(
            "SELECT version,ruleset_id FROM region_release WHERE region='JP'"
        )}
        mappings = {row[0]: row[1] for row in current.execute("SELECT status,count(*) FROM chart_mapping GROUP BY status")}
        mapped_candidates = current.execute("""
            SELECT count(*) FROM chart_mapping m JOIN source_artifact a ON a.id=m.source_artifact_id
            WHERE m.status='candidate' AND json_extract(m.evidence,'$.target_version')='X-VERSE-X'
        """).fetchone()[0]
        all_snapshots = {}
        for version, day, rows in snapshots(current):
            all_snapshots[version] = rows
            stats = defaultdict(Counter)
            playable = defaultdict(set)
            for rev, row in rows.items():
                diff = row["difficulty"]
                old_row = before[version].get(rev)
                status, old_status = label(row), label(old_row)
                number = row["constant_tenths"] if status != "not_applicable" else None
                old_number = old_row["constant_tenths"] if old_row and old_status != "not_applicable" else None
                constant_conflicts = disputes_by_chart.get((version, rev), [])
                main = dict(version=version, date=day, difficulty=diff, title=row["title"],
                            chart_id=row["chart_id"], revision_id=rev,
                            source_key=bound.get(rev), availability=row["state"],
                            identity_basis=row["identity_basis"], old_status=old_status, new_status=status,
                            old_tenths=old_number, new_tenths=number)
                proof = basis(row, proofs)
                marker = markers.get(bound.get(rev), "source_unavailable")
                for record in constant_conflicts:
                    if record["status"] == "open":
                        conflicts.append({**main, **record})
                if row["state"] == "playable":
                    stats[diff]["playable"] += 1
                    playable[diff].add(row["chart_id"])
                    all_playable.add(row["chart_id"])
                    if status not in VERIFIED:
                        gaps.append({**main, **proof, "wiki_check": marker, "eligible_for_rating": False,
                                     "missing_evidence": status, "next_evidence": gap_action(status, diff, marker)})
                    if status in VERIFIED:
                        stats[diff]["verified"] += 1
                        all_verified.add(row["chart_id"])
                    elif status in REFERENCE:
                        stats[diff]["reference"] += 1
                        all_ref.add(row["chart_id"])
                    else:
                        stats[diff][status] += 1
                        if status == "unknown":
                            all_unknown.add(row["chart_id"])
                        if status == "estimated":
                            all_estimated.add(row["chart_id"])
                unknown_availability = row["state"] == "unknown"
                identity_unproven = status in VERIFIED and row["identity_basis"] != "verified_content"
                ultima_unproven = (diff == "ULT" and row["state"] in ("unknown", "playable") and rev not in releases)
                stats[diff]["availability_unknown"] += unknown_availability
                stats[diff]["verified_revision_unproven"] += identity_unproven
                if unknown_availability or identity_unproven or ultima_unproven:
                    identity.append({**main, "availability_unknown": unknown_availability,
                                     "trusted_revision_unproven": identity_unproven,
                                     "ultima_exact_first_release_unproven": ultima_unproven,
                                     "chart_state": row["chart_state"], "song_state": row["song_state"],
                                     "song_availability_observation": row["song_observation"],
                                     "chart_availability_observation": row["chart_observation"]})
                stats[diff]["new_conflicts"] += status == "conflict" and old_status != "conflict"
                stats[diff]["resolved_conflicts"] += old_status == "conflict" and status in VERIFIED
                stats[diff]["newly_verified"] += status in VERIFIED and old_status not in VERIFIED
                stats[diff]["corrected"] += status in VERIFIED and old_status in VERIFIED and number != old_number
                stats[diff]["lost_verified"] += old_status in VERIFIED and status not in VERIFIED
                stats[diff]["wiki_decisions"] += status == "wiki_verified"
                if (old_status, old_number) != (status, number):
                    changes.append({**main, **proof, "old_source": prior_proofs.get(
                        old_row["constant_observation"] if old_row else None, {}).get("artifact_id"),
                                    "affects_trusted_rating_input": (status in VERIFIED, number if status in VERIFIED else None)
                                    != (old_status in VERIFIED, old_number if old_status in VERIFIED else None)})
                if status == "wiki_verified":
                    overruled = [r for r in constant_conflicts if r["status"] == "resolved"
                                 and r["observation_id"] != r["selected_observation"]]
                    decisions.append({**main, **proof, "decision": "explicit_wiki_edition_range",
                                      "overruled": json.dumps(overruled, ensure_ascii=False, sort_keys=True)})
            total = sum(stats.values(), Counter())
            combined = set().union(*playable.values())
            counts.append(count(total, version, day, "ALL", combined))
            counts.extend(count(stats[d], version, day, d, playable[d]) for d in DIFFICULTIES[:-1])
            top = stats["MAS"] + stats["ULT"]
            readiness.append({**counts[-6], "status": "PARTIAL" if version == "PARADISE LOST" else "BLOCKED",
                              "formula": "disputed" if version == "STAR" else "partial" if rules[version] else "unproven",
                              "historical_playability_complete": total["availability_unknown"] == 0,
                              "mas_ult_playable": top["playable"], "mas_ult_verified": top["verified"],
                              "mas_ult_unverified": top["playable"] - top["verified"],
                              "cn_jp_candidates": mapped_candidates if version == "X-VERSE-X" else None,
                              "cn_jp_approved": 0,
                              "readonly_simulation": "fixed_LOST_reference_only" if version == "PARADISE LOST"
                              else "offline_chart_only"})
        for proof in proofs.values():
            if proof["value_kind"] == "wiki_verified":
                wiki_index.append(proof)
        with audit_path.open(encoding="utf-8-sig", newline="") as handle:
            for item in csv.DictReader(handle):
                if item["preview_status"] not in ("CANDIDATE", "CANDIDATE_CONFLICT") and not (
                    item["trusted_value_mismatch"] == "True" or item["old_status"] == "conflict"
                ):
                    continue
                version, rev = item["version"], item["revision_id"]
                row = all_snapshots[version].get(rev)
                marker = markers.get(item["source_key"], "source_unavailable")
                record = {**item, "b52_status": label(row), "b52_tenths": row["constant_tenths"] if row else None,
                          "wiki_check": marker, **(basis(row, proofs) if row else {})}
                candidate_audit.append(record)
                if version == "X-VERSE-X" and item["preview_status"] == "CANDIDATE" and item["difficulty"] in ("EXP", "MAS"):
                    x_audit.append(record)
    for name, rows in (
        ("coverage_by_version", counts), ("wiki_decisions", decisions), ("constant_changes", changes),
        ("remaining_constant_gaps", gaps), ("unresolved_conflicts", conflicts),
        ("identity_availability_gaps", identity), ("rating_readiness", readiness),
        ("continuity_audit", candidate_audit), ("xversex_38_audit", x_audit),
        ("wiki_version_index", sorted(wiki_index, key=lambda r: (r["artifact_id"], r["revision_id"], r["effective_on"]))),
    ):
        emit(output, name, rows)
    summary = {key: sum(row[key] for row in readiness) for key in TRACKED}
    summary.update({"release": new.release_id, "baseline": old.release_id, "distinct_playable": len(all_playable),
                    "distinct_verified": len(all_verified), "distinct_reference": len(all_ref),
                    "distinct_unknown": len(all_unknown), "distinct_estimated": len(all_estimated),
                    "ultima_first_release_unproven": len({r["chart_id"] for r in identity
                                                         if r["ultima_exact_first_release_unproven"]}),
                    "wiki_pages": dict(Counter(markers.values())), "mapping_counts": mappings,
                    "continuity_audit": len(candidate_audit), "xversex_38": dict(Counter(r["b52_status"] for r in x_audit)),
                    "distinct_wiki_verified": len({r["chart_id"] for r in decisions}),
                    "unresolved_conflict_slots": len({r["id"] for r in conflicts})})
    (output / "summary.json").write_bytes(format_json(summary))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("catalog", "baseline", "wiki-audit", "b51-audit", "output"):
        parser.add_argument(f"--{key}", required=True, type=Path)
    args = parser.parse_args()
    export(HistoryCatalog(args.catalog), HistoryCatalog(args.baseline),
           json.loads(args.wiki_audit.read_bytes()), args.b51_audit, private_root(args.output))


if __name__ == "__main__":
    main()
