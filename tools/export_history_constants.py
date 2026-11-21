"""Export traceable, disjoint JP constant coverage for each released checkpoint."""
import argparse
from collections import Counter
import csv
from hashlib import sha256
from pathlib import Path

from build_history_catalog import format_json, atomic_write, private_root
from _unibot_history.history import (
    DIFFICULTIES, HistoryCatalog, catalog_state, constant_kind, snapshot_rows, trusted,
)


CATEGORIES = ("verified", "unknown", "display_only", "estimated", "conflict", "evidence_insufficient")
CHART_FIELDS = (
    "version", "date", "song_id", "chart_id", "revision_id", "title", "difficulty",
    "category", "value_kind", "evidence_kind", "availability", "display_level", "constant_tenths",
    "constant_quality", "chart_quality", "song_quality", "observation_id", "artifact_id", "source_id",
    "source_url", "source_sha256", "source_location", "source_observed_on", "effective_on",
    "effective_before", "time_grain", "conflict_id", "candidate_observation_id", "candidate_tenths",
)


def classification(row):
    kind = constant_kind(row)
    if kind == "exact":
        return "verified" if trusted(row) else "evidence_insufficient"
    return kind


def jp_snapshots(conn):
    return conn.execute("""
        SELECT s.id, r.version, s.on_date
        FROM region_release r JOIN snapshot s ON s.region=r.region AND s.version=r.version
        WHERE r.region='JP' AND s.on_date=coalesce(date(r.end_before, '-1 day'), (
          SELECT max(t.on_date) FROM snapshot t WHERE t.region=r.region AND t.version=r.version
        ))
        ORDER BY r.start_on
    """).fetchall()


def checkpoint_rows(conn, snapshot):
    for row in snapshot_rows(conn, "JP", snapshot["on_date"], snapshot["id"]):
        if row["difficulty"] != "WE" and catalog_state(row) in ("playable", "unknown"):
            yield row


def verified_index(catalog):
    if catalog is None:
        return {}
    with catalog._connect() as conn:
        return {(s["version"], r["chart_id"]): (r["constant_tenths"], s["on_date"])
                for s in jp_snapshots(conn) for r in checkpoint_rows(conn, s) if trusted(r)}


def source_index(conn):
    return {r["id"]: r for r in conn.execute("""
        SELECT o.id, o.artifact_id, a.source_id, a.url, a.sha256, o.locator, a.observed_on,
               o.effective_on, o.effective_before, o.grain
        FROM observation o JOIN source_artifact a ON a.id=o.artifact_id
        WHERE o.region='JP' AND o.field IN ('constant','availability')
    """)}


def candidate_index(conn):
    return {(r["revision_id"], r["effective_on"]): (r["oid"], r["tenths"]) for r in conn.execute("""
        SELECT o.revision_id, o.effective_on, min(o.id) oid, min(json_extract(o.value_json, '$')) tenths
        FROM observation o
        WHERE o.region='JP' AND o.field='constant' AND o.artifact_id <> 'oto-luminousplus'
          AND o.quality IN ('A','B','C') AND o.value_kind='exact'
          AND json_type(o.value_json)='integer' AND o.grain='point'
        GROUP BY o.revision_id, o.effective_on
        HAVING count(DISTINCT json_extract(o.value_json, '$'))=1
    """)}


def sheet_cell(value):
    # Titles are public text, but spreadsheet applications may evaluate formula-like text.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def csv_rows(path, fields, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(fields)
        writer.writerows(tuple(sheet_cell(value) for value in row) for row in rows)


def conflict_rows(conn):
    return conn.execute("""
        SELECT c.version, c.on_date, ch.song_id, ch.id AS chart_id, c.revision_id, ch.difficulty,
               c.id AS conflict_id, c.reason, c.status, o.id AS observation_id, o.value_json,
               o.quality, o.grain, a.source_id, a.url, a.sha256, o.locator, a.observed_on, o.effective_on,
               o.effective_before
        FROM conflict c JOIN chart_revision r ON r.id=c.revision_id
        JOIN chart ch ON ch.id=r.chart_id JOIN conflict_observation p ON p.conflict_id=c.id
        JOIN observation o ON o.id=p.observation_id JOIN source_artifact a ON a.id=o.artifact_id
        WHERE c.region='JP' AND c.field='constant' AND c.status='open'
        ORDER BY c.on_date, ch.id, c.id, o.id
    """)


def export(catalog, baseline, output):
    output.mkdir(parents=True, exist_ok=True)
    before = verified_index(baseline)
    verified, gaps, added, counts, changes = [], [], [], [], []
    with catalog._connect() as conn:
        sources = source_index(conn)
        candidates = candidate_index(conn)
        for snap in jp_snapshots(conn):
            version, day = snap["version"], snap["on_date"]
            tallies = {diff: Counter() for diff in DIFFICULTIES[:-1]}
            for r in checkpoint_rows(conn, snap):
                category = classification(r)
                oid = r["constant_observation"] or r["chart_observation"]
                candidate = candidates.get((r["revision_id"], day))
                if category in ("unknown", "display_only") and candidate is not None:
                    category = "evidence_insufficient"
                proof = sources[oid]
                relevant = category != "verified" and candidate is not None
                data = (version, day, r["song_id"], r["chart_id"], r["revision_id"], r["title"],
                        r["difficulty"], category, constant_kind(r), r["constant_evidence_kind"],
                        catalog_state(r), r["level_display"], r["constant_tenths"], r["constant_quality"],
                        r["chart_quality"], r["song_quality"], oid, *tuple(proof)[1:], r["conflict_id"],
                        *(candidate if relevant else (None, None)))
                stats = tallies[r["difficulty"]]
                stats[category] += 1
                stats["known_chart"] += catalog_state(r) == "playable"
                stats["candidate_on_checkpoint"] += relevant
                stats["rule_derived"] += r["constant_evidence_kind"] == "rule_derived"
                previous = before.pop((version, r["chart_id"]), None)
                if category == "verified":
                    verified.append(data)
                    if baseline and previous is None:
                        added.append(data)
                        stats["added"] += 1
                else:
                    gaps.append(data)
                if previous is not None and (category != "verified" or previous[0] != r["constant_tenths"]):
                    changes.append(dict(version=version, date=day, chart_id=r["chart_id"],
                                        before=previous[0], after=r["constant_tenths"], category=category))
            for diff, stats in tallies.items():
                total = sum(stats[k] for k in CATEGORIES)
                percentage = f"{stats['verified'] * 100 / stats['known_chart']:.2f}" if stats["known_chart"] else None
                counts.append((version, day, diff, stats["known_chart"], total,
                               *(stats[k] for k in CATEGORIES), stats["rule_derived"], stats["added"],
                               stats["candidate_on_checkpoint"], percentage, None))
        conflicts = conflict_rows(conn)
        csv_rows(output / "constant_conflicts.csv", [col[0] for col in conflicts.description], conflicts)
    changes.extend(dict(version=version, chart_id=chart, before=v[0], category="absent")
                   for (version, chart), v in sorted(before.items()))
    for name, rows in (("verified_constants", verified), ("missing_constants", gaps),
                       ("newly_verified_constants", added)):
        csv_rows(output / f"{name}.csv", CHART_FIELDS, rows)
    csv_rows(output / "coverage_by_version.csv", (
        "version", "date", "difficulty", "known_chart", "observed_or_candidate_slots",
        *CATEGORIES, "rule_derived", "newly_verified", "candidate_on_checkpoint",
        "observed_known_chart_verified_pct", "historical_population",
    ), counts)
    atomic_write(output / "check.json", format_json({
        "release": catalog.release_id, "baseline": baseline.release_id if baseline else None,
        "checkpoints": len(counts) // 5, "verified": len(verified), "gaps": len(gaps), "newly_verified": len(added),
        "prior_verified_changes": changes,
        "sha256": {file.name: sha256(file.read_bytes()).hexdigest() for file in sorted(output.glob("*.csv"))},
    }))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    export(HistoryCatalog(args.catalog), HistoryCatalog(args.baseline) if args.baseline else None,
           private_root(args.output))


if __name__ == "__main__":
    main()
