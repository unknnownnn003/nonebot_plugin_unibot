"""Offline, read-only access to the public CHUNITHM historical catalog."""
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from itertools import groupby
import json
from pathlib import Path
import re
import sqlite3
import unicodedata


SCHEMA_VERSION = 1
RELEASE_ID = re.compile(r"[0-9a-f]{64}")
DIFFICULTIES = ("BAS", "ADV", "EXP", "MAS", "ULT", "WE")


class SnapshotUnavailable(ValueError):
    """There is no supported observation at the requested date."""


# This same selection is used when publishing and for uncached JP dates.
CURRENT_REVISIONS = """
SELECT cs.revision_id, cs.valid_from AS chart_from, ss.valid_from AS song_from,
       (SELECT e.id FROM resolved_event e
        WHERE e.region = cs.region AND e.revision_id = cs.revision_id
          AND e.valid_from <= :day AND (e.valid_to IS NULL OR :day < e.valid_to)
        ORDER BY e.valid_from DESC LIMIT 1) AS event_id
FROM region_chart_state cs
JOIN chart_revision r ON r.id = cs.revision_id
JOIN chart c ON c.id = r.chart_id
JOIN region_song_state ss ON ss.region = cs.region AND ss.song_id = c.song_id
WHERE cs.region = :region AND cs.valid_from <= :day
  AND (cs.valid_to IS NULL OR :day < cs.valid_to)
  AND ss.valid_from <= :day AND (ss.valid_to IS NULL OR :day < ss.valid_to)
"""

READ_CHARTS = """
SELECT c.song_id, n.name AS title, c.id AS chart_id, c.difficulty, c.variant,
       r.id AS revision_id, r.identity_basis, r.fingerprint, r.metadata,
       ss.state AS song_state, cs.state AS chart_state, cs.level_display,
       ss.observation_id AS song_observation, cs.observation_id AS chart_observation,
       co.quality AS chart_quality, so.quality AS song_quality,
       e.kind AS event_kind, e.constant_tenths, e.conflict_id,
       e.observation_id AS constant_observation, o.quality AS constant_quality,
       o.value_kind AS constant_evidence_kind
FROM selected a
JOIN chart_revision r ON r.id = a.revision_id
JOIN chart c ON c.id = r.chart_id
JOIN region_chart_state cs ON cs.region = :region AND cs.revision_id = r.id
    AND cs.valid_from = a.chart_from
JOIN region_song_state ss ON ss.region = :region AND ss.song_id = c.song_id
    AND ss.valid_from = a.song_from
JOIN observation so ON so.id = ss.observation_id
JOIN observation co ON co.id = cs.observation_id
JOIN song_name n ON n.song_id = c.song_id AND n.artifact_id = so.artifact_id
    AND n.name = json_extract(so.raw_json, '$.title')
LEFT JOIN resolved_event e ON e.id = a.event_id
LEFT JOIN observation o ON o.id = e.observation_id
ORDER BY c.song_id, c.difficulty, c.variant, r.id
"""


def snapshot_rows(conn, region, day, snapshot_id=None):
    if snapshot_id is None:
        selection = CURRENT_REVISIONS
    else:
        selection = "SELECT * FROM snapshot_chart WHERE snapshot_id = :snapshot"
    return conn.execute(
        f"WITH selected AS ({selection}) {READ_CHARTS}",
        {"region": region, "day": day, "snapshot": snapshot_id},
    )


def catalog_state(row):
    song, chart = row["song_state"], row["chart_state"]
    if song == "playable" or (song == "unknown" and chart == "not_released"):
        return chart
    return song


def constant_kind(row):
    if row["difficulty"] == "WE":
        return "not_applicable"
    # An unresolved conflict is useful even if the song is temporarily unavailable.
    if row["event_kind"]:
        return row["event_kind"]
    return "display_only" if row["level_display"] else "unknown"


def coverage(rows):
    result = {
        "songs_by_state": {},
        "charts": {difficulty: {} for difficulty in DIFFICULTIES},
        "available_chart_count": 0,
        "eligible_exact_count": 0,
        "unconfirmed_availability_count": 0,
        "unconfirmed_constant_count": 0,
        "playable_unconfirmed_constant_count": 0,
        "charts_by_state": {},
        "historical_population": None,
    }
    for _, charts in groupby(rows, key=lambda row: row["song_id"]):
        charts = list(charts)
        states = result["songs_by_state"]
        song_state = charts[0]["song_state"]
        states[song_state] = states.get(song_state, 0) + 1
        for row in charts:
            state = catalog_state(row)
            state_counts = result["charts_by_state"]
            state_counts[state] = state_counts.get(state, 0) + 1
            if state == "unknown":
                result["unconfirmed_availability_count"] += 1
            if state not in ("playable", "unknown"):
                continue
            kind = constant_kind(row)
            counts = result["charts"][row["difficulty"]]
            key = f"{state}:{kind}"
            counts[key] = counts.get(key, 0) + 1
            if state == "playable":
                result["available_chart_count"] += 1
            if row["difficulty"] != "WE":
                if trusted(row):
                    result["eligible_exact_count"] += 1
                else:
                    result["unconfirmed_constant_count"] += 1
                    result["playable_unconfirmed_constant_count"] += state == "playable"
    return result


def trusted(row):
    """Quality gate for a constant within its own observed catalog scope."""
    return (row["difficulty"] != "WE" and row["event_kind"] == "exact"
            and catalog_state(row) == "playable"
            and all(row[key] in ("A", "B") for key in ("song_quality", "chart_quality", "constant_quality")))


def public_chart(row):
    item = dict(row)
    item["metadata"] = json.loads(item["metadata"])
    item["state"] = catalog_state(row)
    item["constant_kind"] = constant_kind(row)
    if item["constant_kind"] == "exact" and row["constant_quality"] not in ("A", "B"):
        item["constant_kind"] = "evidence_insufficient"
    item["constant"] = (Decimal(row["constant_tenths"]).scaleb(-1)
                        if item["constant_kind"] == "exact" else None)
    item["trusted_constant"] = item["constant"] if trusted(row) else None
    item["constant_label"] = (str(item["trusted_constant"]) if item["trusted_constant"] is not None
                              else "CONFLICT" if item["constant_kind"] == "conflict"
                              else "UNKNOWN" if item["constant_kind"] != "not_applicable" else None)
    return item


def iso_day(value):
    if type(value) is date:
        return value.isoformat()
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Use an ISO YYYY-MM-DD date")
    return value


def resolve_release(root):
    root = Path(root)
    release = (root / "CURRENT").read_text(encoding="ascii").strip()
    if not RELEASE_ID.fullmatch(release):
        raise ValueError("Invalid history release ID")
    return root / "releases" / release


class HistoryCatalog:
    """Pin a read-only package for the lifetime of this catalog instance."""

    def __init__(self, path=None):
        if path is None:
            from .paths import HISTORY_DIR

            path = HISTORY_DIR
        path = Path(path)
        if path.is_dir() and (path / "CURRENT").is_file():
            path = resolve_release(path)
        self.path = (path / "catalog.sqlite" if path.is_dir() else path).resolve()
        with self._connect() as conn:
            if conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                raise ValueError("Unsupported history schema; rebuild from the pinned sources")
            self.release_id = conn.execute("SELECT release_id FROM package").fetchone()[0]

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path.as_uri() + "?mode=ro&immutable=1", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _cutoff(self, conn, region, version, day):
        release = conn.execute(
            "SELECT * FROM region_release WHERE region=? AND version=?", (region, version)
        ).fetchone()
        if release is None:
            raise SnapshotUnavailable(f"No recorded release for {region} {version}")
        if day is not None:
            day = iso_day(day)
        snapshot = conn.execute(
            """SELECT * FROM snapshot WHERE region=? AND version=?
               AND (? IS NULL OR on_date=?) ORDER BY on_date DESC LIMIT 1""",
            (region, version, day, day),
        ).fetchone()
        if snapshot:
            return release, snapshot["on_date"], snapshot
        if (day is None or release["kind"] != "history" or release["through_on"] is None
                or not release["start_on"] <= day <= release["through_on"]):
            raise SnapshotUnavailable(f"No supported observation for {region} {version} on {day}")
        if release["end_before"] is None:
            earlier_horizon = conn.execute(
                """SELECT min(on_date) FROM snapshot WHERE region=? AND version=? AND kind='observation'""",
                (region, version),
            ).fetchone()[0]
            if earlier_horizon is None or day > earlier_horizon:
                raise SnapshotUnavailable(f"No dated observation for {region} {version} on {day}")
        return release, day, None

    def get_snapshot(self, region, version, date=None):
        """Read the default published checkpoint or a known local calendar day."""
        with self._connect() as conn:
            release, day, snapshot = self._cutoff(conn, region, version, date)
            rows = list(snapshot_rows(conn, region, day, snapshot["id"] if snapshot else None))
            rule = conn.execute("SELECT * FROM ruleset WHERE id=?", (release["ruleset_id"],)).fetchone()
            rule = dict(rule) if rule else None
            if rule:
                rule["parameters"] = json.loads(rule["parameters"])
            return {
                "release_id": self.release_id, "region": region, "version": version, "date": day,
                "kind": snapshot["kind"] if snapshot else "as_of",
                "rating_status": snapshot["rating_status"] if snapshot else "blocked",
                "ruleset": rule,
                "coverage": json.loads(snapshot["coverage_json"]) if snapshot else coverage(rows),
                "charts": [public_chart(row) for row in rows],
            }

    def get_version_coverage(self, region, version):
        with self._connect() as conn:
            _, day, snapshot = self._cutoff(conn, region, version, None)
            return {"date": day, "rating_status": snapshot["rating_status"],
                    **json.loads(snapshot["coverage_json"])}

    def get_chart_history(self, chart_id, region):
        """Return per-revision intervals and source observations, including disputes."""
        with self._connect() as conn:
            def rows(table, alias, order, join):
                return [dict(row) for row in conn.execute(
                    f"""SELECT {alias}.* FROM {table} {alias}
                         JOIN chart_revision r ON r.id = {join}
                         WHERE r.chart_id=? AND {alias}.region=? ORDER BY {order}""",
                    (chart_id, region),
                )]

            return {
                "chart_id": chart_id,
                "revisions": [dict(row) for row in conn.execute(
                    "SELECT * FROM chart_revision WHERE chart_id=? ORDER BY id", (chart_id,)
                )],
                "states": rows("region_chart_state", "s", "s.valid_from, r.id", "s.revision_id"),
                "events": rows("resolved_event", "e", "e.valid_from, e.id", "e.revision_id"),
                "observations": rows("observation", "o", "o.effective_on, o.id", "o.revision_id"),
                "conflicts": rows("conflict", "c", "c.on_date, c.id", "c.revision_id"),
            }

    def get_song_history(self, song_id, region):
        with self._connect() as conn:
            return {
                "song_id": song_id,
                "states": [dict(row) for row in conn.execute(
                    """SELECT * FROM region_song_state WHERE song_id=? AND region=?
                       ORDER BY valid_from""", (song_id, region)
                )],
                "observations": [dict(row) for row in conn.execute(
                    """SELECT * FROM observation WHERE song_id=? AND region=? AND revision_id IS NULL
                       ORDER BY effective_on, id""", (song_id, region)
                )],
            }

    def get_constant_changes(self, region, old_version, new_version, *, include_unverified=False):
        """Compare trusted values, optionally including historically grouped slots."""
        old = self.get_snapshot(region, old_version)
        new = self.get_snapshot(region, new_version)
        before = {row["revision_id"]: row for row in old["charts"] if row["trusted_constant"] is not None}
        changes = []
        for row in new["charts"]:
            previous = before.get(row["revision_id"])
            verified = row["identity_basis"] == "verified_content"
            if ((verified or include_unverified) and previous is not None and row["trusted_constant"] is not None
                    and previous["trusted_constant"] != row["trusted_constant"]):
                changes.append({
                    "chart_id": row["chart_id"], "revision_id": row["revision_id"], "title": row["title"],
                    "difficulty": row["difficulty"], "identity_basis": row["identity_basis"],
                    "revision_verified": verified,
                    "before": previous["trusted_constant"], "after": row["trusted_constant"],
                    "before_observation": previous["constant_observation"],
                    "after_observation": row["constant_observation"],
                })
        return changes

    def find_charts(self, title):
        """Find all normalized-name candidates without merging same-name songs."""
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(
                """SELECT c.*, min(n.name) AS title FROM song_name n JOIN chart c ON c.song_id=n.song_id
                   WHERE n.normalized=? GROUP BY c.id ORDER BY c.song_id, c.difficulty, c.variant""",
                (normalize_name(title),),
            )]

    def get_external_ids(self, region, source, song_key):
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(
                """SELECT e.* FROM external_id e JOIN source_artifact a ON a.id=e.artifact_id
                   WHERE e.region=? AND a.source_id=? AND e.external_song_key=?
                   ORDER BY e.observed_on, e.artifact_id, e.external_chart_key""",
                (region, source, str(song_key)),
            )]

    def get_version_correspondences(self):
        """Read project-defined version mappings without implying chart equivalence."""
        with self._connect() as conn:
            row = conn.execute("SELECT source_manifest FROM package").fetchone()
            return json.loads(row[0]).get("regional_correspondences", [])

    def get_chart_mappings(self, revision_id):
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(
                """SELECT * FROM chart_mapping WHERE source_revision_id=?
                   ORDER BY status, target_revision_id, source_artifact_id, target_artifact_id""",
                (revision_id,),
            )]

    def get_observation(self, observation_id):
        with self._connect() as conn:
            row = conn.execute(
                """SELECT o.*, a.source_id, a.url, a.sha256, a.upstream_version, a.parser_version,
                          a.observed_on, s.reuse
                   FROM observation o JOIN source_artifact a ON a.id=o.artifact_id
                   JOIN source s ON s.id=a.source_id WHERE o.id=?""", (observation_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_conflicts(self, region, version):
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(
                """SELECT c.*, r.selected_observation, r.artifact_id AS resolution_artifact,
                          r.reason AS resolution_reason,
                          (SELECT json_group_array(observation_id) FROM conflict_observation
                           WHERE conflict_id=c.id) AS candidates,
                          (SELECT json_group_array(ruleset_id) FROM conflict_ruleset
                           WHERE conflict_id=c.id) AS ruleset_candidates
                   FROM conflict c LEFT JOIN resolution r ON r.conflict_id=c.id
                   WHERE c.region=? AND c.version=? ORDER BY c.on_date, c.field, c.id""",
                (region, version),
            )]

    def get_ruleset(self, ruleset_id):
        with self._connect() as conn:
            row = conn.execute(
                """SELECT r.*, a.url, a.sha256 FROM ruleset r
                   JOIN source_artifact a ON a.id=r.artifact_id WHERE r.id=?""", (ruleset_id,),
            ).fetchone()
            if not row:
                return None
            result = dict(row)
            result["parameters"] = json.loads(result["parameters"])
            return result

    def get_jackets(self, song_id):
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(
                """SELECT a.*, j.region, j.observed_on FROM song_jacket j
                   JOIN jacket_asset a ON a.id=j.asset_id WHERE j.song_id=?
                   ORDER BY j.region, a.id""", (song_id,),
            )]


def normalize_name(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())
