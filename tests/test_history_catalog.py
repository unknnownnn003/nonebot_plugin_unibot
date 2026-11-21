"""History storage contracts and optional checks against the pinned local package."""
from datetime import date
from contextlib import closing
from decimal import Decimal
import json
import os
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
try:
    from build_history_catalog import SCHEMA, check_integrity, publish_snapshots, validate_package, load_inputs
    from export_versioned_catalog import counts, gap_reason
    from history_enrichment import resolve_version_constants
    from history_import import CatalogImport, decimal_tenths, identifier, offset, partition, sha
    from _unibot_history.history import HistoryCatalog, SnapshotUnavailable, resolve_release
finally:
    sys.path.remove(str(TOOLS))


class HistoryStorageTests(unittest.TestCase):
    def create_fixture(self, path):
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(SCHEMA.read_text(encoding="utf-8-sig"))
            conn.execute("INSERT INTO package VALUES ('demo','test','{}',?)", (sha(SCHEMA.read_bytes()),))
            conn.execute("INSERT INTO source VALUES ('fixture','https://example.test/','fixture')")
            for source in ("fixture", "alternative"):
                conn.execute("INSERT INTO source_artifact VALUES (?,?,?, ?,?,?,?,?)",
                             (source, "fixture", f"https://example.test/{source}", "0" * 64, 0,
                              "2024-12-12", "fixture", "test"))
            artifacts = {key: {"observed_on": "2024-12-12"} for key in ("fixture", "alternative")}
            db = CatalogImport(conn, artifacts)
            db.release("JP", "VERSE", "2024-12-12", "2025-07-16", "2025-07-15",
                       "Asia/Tokyo", "history", "fixture", "version")
            db.points.append(("JP", "VERSE", "2025-07-15", "version_end"))
            day = "2025-07-15"
            db.editions[19] = ("2024-12-12", offset(day), day, "VERSE")
            single_day = [(day, offset(day), "playable", "B", "point", 10)]
            for key in ("first", "second"):
                song = db.song(("fixture", key), "Collision", "fixture", key)
                db.states("fixture", key, "JP", song, "Collision", single_day)
                for diff in ("BAS", "ADV", "EXP", "WE"):
                    rev = db.chart(song, diff, "fixture")
                    locator = f"{key}.{diff}"
                    db.states("fixture", locator, "JP", song, "Collision", single_day, revision=rev,
                              display="4" if diff == "BAS" else None)
                    if diff == "ADV":
                        db.constant("fixture", locator, "JP", song, rev, "7.5", 75, start=day,
                                    end=offset(day), kind="estimated", quality="D")
                    if diff == "EXP":
                        db.constant("fixture", locator, "JP", song, rev, "10.0", 100, start=day, end=offset(day))
                        db.constant("alternative", locator, "JP", song, rev, "10.2", 102,
                                    start=day, end=offset(day))
            resolve_version_constants(db)
            db.materialize_intervals()
            publish_snapshots(db, ())
            check_integrity(conn)

    def test_roundtrip_readonly_conflicts_and_determinism(self):
        with TemporaryDirectory() as temporary:
            first, second = (Path(temporary) / name for name in ("first.sqlite", "second.sqlite"))
            self.create_fixture(first)
            self.create_fixture(second)
            self.assertEqual(sha(first.read_bytes()), sha(second.read_bytes()))
            catalog = HistoryCatalog(first)
            result = catalog.get_snapshot("JP", "VERSE", date(2025, 7, 15))
            self.assertEqual(result["coverage"]["available_chart_count"], 8)
            self.assertIsNone(result["coverage"]["historical_population"])
            self.assertEqual(len({r["song_id"] for r in catalog.find_charts("collision")}), 2)
            kinds = {c["difficulty"]: c["constant_kind"] for c in result["charts"]}
            self.assertEqual(kinds, {"BAS": "display_only", "ADV": "estimated",
                                     "EXP": "conflict", "WE": "not_applicable"})
            self.assertTrue(all(c["trusted_constant"] is None for c in result["charts"]))
            conflict = catalog.get_conflicts("JP", "VERSE")[0]
            values = [catalog.get_observation(oid) for oid in json.loads(conflict["candidates"])]
            self.assertEqual({json.loads(row["value_json"]) for row in values}, {100, 102})
            self.assertEqual({row["sha256"] for row in values}, {"0" * 64})
            with self.assertRaises(SnapshotUnavailable):
                catalog.get_snapshot("JP", "VERSE", "2025-07-16")
            with self.assertRaises(SnapshotUnavailable):
                catalog.get_snapshot("CN", "VERSE")
            with self.assertRaises(sqlite3.OperationalError), catalog._connect() as conn:
                conn.execute("DELETE FROM song")

    def test_retired_lineage_fails_before_sources_are_opened(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            lock = root / "sources.json"
            lock.write_text(json.dumps({"sources": {}, "artifacts": [],
                                        "dependencies": [["workbook", "otoge", "indirect validation"]]}),
                            encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Retired OTOGE"):
                load_inputs(lock, root)

    def test_gap_accounting_excludes_unavailable_and_separates_unknown_availability(self):
        base = {"category": "unknown", "verified": False, "constant_evidence_kind": None}
        for state in ("not_released", "removed", "suspended", "disabled"):
            record = {**base, "state": state}
            self.assertIsNone(gap_reason(record))
            self.assertEqual(counts(record, active=True)["playable_unknown_constant"], 0)
        unknown = {**base, "state": "unknown"}
        self.assertEqual(gap_reason(unknown), "availability_unknown")
        self.assertEqual(counts(unknown, active=True)["playable_unknown_constant"], 0)
        playable = {**base, "state": "playable"}
        self.assertEqual(gap_reason(playable), "constant_unknown")
        self.assertEqual(counts(playable, active=True)["playable_unknown_constant"], 1)

    def test_half_open_and_exact_tenths(self):
        values = [
            ("2024-12-11", "2024-12-13", 10, "playable"),
            ("2024-12-12", "2024-12-13", 20, "suspended"),
            ("2024-12-13", None, 10, "playable"),
        ]
        self.assertEqual(partition(values), [
            ("2024-12-11", "2024-12-12", "playable"),
            ("2024-12-12", "2024-12-13", "suspended"),
            ("2024-12-13", None, "playable"),
        ])
        self.assertNotEqual(identifier("chart", "12", "3"), identifier("chart", "1", "23"))
        self.assertEqual(decimal_tenths(Decimal("13.7")), 137)
        for value in (True, "13.75", "0"):
            with self.assertRaises((ValueError, ArithmeticError)):
                decimal_tenths(value)


@unittest.skipUnless(os.environ.get("UNIBOT_TEST_HISTORY"), "build the private locked history package first")
class PinnedHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = resolve_release(Path(os.environ["UNIBOT_TEST_HISTORY"]))
        cls.manifest = validate_package(cls.folder)
        cls.catalog = HistoryCatalog(cls.folder)

    def chart(self, release, title, difficulty, day=None, region="JP"):
        return next(row for row in self.catalog.get_snapshot(region, release, day)["charts"]
                    if row["title"] == title and row["difficulty"] == difficulty
                    )

    def test_lost_new_and_two_day_provenance(self):
        c = self.catalog
        lost = c.get_snapshot("JP", "PARADISE LOST")
        self.assertEqual(lost["date"], "2021-11-03")
        self.assertEqual(lost["rating_status"], "legacy_reference_only")
        self.assertEqual(lost["ruleset"]["parameters"]["cap_bonus_hundredths"], 200)
        self.assertEqual(c.get_snapshot("JP", "NEW", "2021-11-04")["ruleset"]["parameters"]["cap_bonus_hundredths"], 215)
        title = "B.B.K.K.B.K.K."
        self.assertEqual(self.chart("PARADISE LOST", title, "MAS")["constant"], Decimal("11.8"))
        self.assertNotEqual(self.chart("NEW", title, "MAS", "2021-11-04")["constant"], Decimal("11.8"))
        self.assertEqual(self.chart("NEW", title, "MAS")["constant"], Decimal("12.4"))
        bas_end = self.chart("PARADISE LOST", title, "BAS")
        bas_observed = self.chart("PARADISE LOST", title, "BAS", "2021-11-02")
        self.assertEqual(bas_end["trusted_constant"], bas_observed["trusted_constant"])
        self.assertIsNotNone(bas_end["trusted_constant"])
        self.assertEqual(c.get_observation(bas_end["constant_observation"])["effective_on"], "2021-11-02")
        with self.assertRaises(SnapshotUnavailable):
            c.get_snapshot("JP", "PARADISE LOST", "2021-11-04")

    def test_luminous_verse_rules_and_pollution(self):
        c = self.catalog
        old = c.get_snapshot("JP", "LUMINOUS PLUS")
        new = c.get_snapshot("JP", "VERSE", "2024-12-12")
        self.assertEqual(old["ruleset"]["format"], "B30/R10")
        self.assertEqual(new["ruleset"]["format"], "B30/N20")
        self.assertTrue(new["ruleset"]["parameters"]["disjoint"])
        self.assertEqual(self.chart("LUMINOUS PLUS", "Acid God", "MAS")["constant"], Decimal("15.2"))
        self.assertEqual(self.chart("VERSE", "Acid God", "MAS")["constant"], Decimal("15.4"))
        sources = self.manifest["inputs"]
        self.assertNotIn("otoge", json.dumps(sources).casefold())
        with c._connect() as conn:
            for column in ("source_id", "url"):
                self.assertFalse(conn.execute(
                    f"SELECT 1 FROM source_artifact WHERE lower({column}) LIKE '%otoge%' LIMIT 1"
                ).fetchone())
        self.assertEqual(c.get_constant_changes("JP", "LUMINOUS PLUS", "VERSE"), [])
        changes = c.get_constant_changes("JP", "LUMINOUS PLUS", "VERSE", include_unverified=True)
        self.assertTrue(any(r["title"] == "Acid God" and not r["revision_verified"] for r in changes))
        self.assertEqual(self.chart("LUMINOUS PLUS", "Acid God", "MAS")["constant"],
                         self.chart("LUMINOUS PLUS", "Acid God", "MAS", "2024-12-10")["constant"])

    def test_star_arithmetic_remains_disputed(self):
        star = [c for c in self.catalog.get_conflicts("JP", "STAR") if c["field"] == "rating_formula"][0]
        rules = [self.catalog.get_ruleset(key) for key in json.loads(star["ruleset_candidates"])]
        self.assertEqual({r["parameters"]["rating"] for r in rules}, {"11.00", "10.6666666667"})
        self.assertEqual(star["status"], "open")

    def test_genesis_and_daydream_temporal_evidence(self):
        self.assertEqual(self.chart("PLUS", "Genesis", "MAS", "2016-02-04")["state"], "not_released")
        genesis = self.chart("PLUS", "Genesis", "MAS", "2016-03-03")
        self.assertEqual(genesis["constant"], Decimal("13.0"))
        self.assertEqual(self.catalog.get_observation(genesis["constant_observation"])["grain"], "day")
        title = "Daydream café"
        self.assertEqual(self.chart("CRYSTAL", title, "MAS", "2019-10-24")["state"], "suspended")
        before = self.chart("NEW", title, "ULT", "2022-01-19")
        after = self.chart("NEW", title, "ULT", "2022-01-20")
        self.assertEqual(before["chart_state"], "not_released")
        self.assertEqual(after["constant"], Decimal("12.7"))
        self.assertEqual(self.chart("LUMINOUS PLUS", title, "ULT", "2024-06-20")["state"], "removed")
        self.assertIn("suspended", {r["state"] for r in self.catalog.get_song_history(after["song_id"], "JP")["states"]})

    def test_named_identity_legacy_and_region_separation(self):
        c = self.catalog
        flower = [r for key in ("01039", "09167") for r in c.get_external_ids("JP", "timemachine", key)
                  if not r["external_chart_key"]]
        self.assertEqual(len({r["song_id"] for r in flower}), 2)
        cn_help = [r for r in c.get_external_ids("CN", "lxns", "2792") if r["external_chart_key"] == "MAS"]
        self.assertEqual(len(cn_help), 1)
        self.assertNotIn("legacy_pinned", {r["status"] for r in c.get_chart_mappings(cn_help[0]["revision_id"])})
        with c._connect() as conn:
            self.assertEqual(conn.execute("""SELECT count(*) FROM chart_mapping
                                             WHERE status='legacy_pinned'""").fetchone()[0], 3048)
        cn = c.get_snapshot("CN", "CN2027")
        intl = c.get_snapshot("INTL", "MATE")
        jp = c.get_snapshot("JP", "MATE")
        self.assertEqual(cn["date"], "2026-11-21")
        self.assertEqual(intl["date"], "2026-11-02")
        self.assertTrue(any(row["title"] == "ALIVE" for row in jp["charts"]))
        self.assertTrue(all(row["title"] != "ALIVE" for row in cn["charts"] + intl["charts"]))
        self.assertTrue(any(row["difficulty"] == "WE" for row in cn["charts"]))
        self.assertTrue(all(row["constant"] is None for row in cn["charts"] if row["difficulty"] == "WE"))
        with self.assertRaises(SnapshotUnavailable):
            c.get_snapshot("INTL", "MATE", "2026-11-03")
        with self.assertRaises(SnapshotUnavailable):
            c.get_snapshot("CN", "CN2027", jp["date"][:8] + "20")

    def test_cn2027_end_state_mapping_is_explicit_and_unapproved(self):
        correspondence, = self.catalog.get_version_correspondences()
        self.assertEqual((correspondence["source_version"], correspondence["target_version"],
                          correspondence["target_checkpoint"]), ("CN2027", "X-VERSE-X", "2026-07-01"))
        with self.catalog._connect() as conn:
            self.assertEqual(conn.execute("""
                SELECT start_on,end_before FROM region_release WHERE region='CN' AND version='CN2027'
            """).fetchone()[:], ("2026-09-10", None))
            mapped = conn.execute("""
                SELECT count(*) FROM chart_mapping
                WHERE json_extract(evidence,'$.target_version')='X-VERSE-X'
                  AND status='candidate' AND json_extract(evidence,'$.historical_revision_verified')=0
            """).fetchone()[0]
            self.assertGreater(mapped, 0)
        with self.assertRaises(SnapshotUnavailable):
            self.catalog.get_snapshot("JP", "MATE", "2026-11-20")

    def test_build_inventory_and_readiness(self):
        c = self.catalog
        with c._connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM region_release WHERE region='JP'").fetchone()[0], 22)
            self.assertEqual(conn.execute("SELECT count(*) FROM snapshot").fetchone()[0], 26)
        self.assertEqual(c.get_version_coverage("JP", "VERSE")["rating_status"], "blocked")
        self.assertEqual(c.get_version_coverage("JP", "PARADISE LOST")["rating_status"], "legacy_reference_only")
        self.assertEqual(c.get_version_coverage("INTL", "X-VERSE-X")["eligible_exact_count"], 0)
        self.assertEqual(self.manifest["inputs"]["distribution"], "local-only")


if __name__ == "__main__":
    unittest.main()
