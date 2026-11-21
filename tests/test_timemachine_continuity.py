"""Exercise the B5.1 preview without writing to an active catalog."""

import csv
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
try:
    from audit_timemachine_continuity import (
        HistoryCatalog, Node, Timeline, compare, export, revision_bindings, timelines,
    )
    from build_history_catalog import SCHEMA, publish_snapshots
    from history_import import CatalogImport, offset, sha
finally:
    sys.path.remove(str(TOOLS))


THROUGH = "2026-10-31"
EDITIONS = {
    1: ("2021-11-04", "2022-04-14", "NEW"),
    2: ("2022-04-14", "2022-10-13", "NEW PLUS"),
    3: ("2022-10-13", "2026-11-01", "SUN"),
}
FIRST = Node("2021-11-04", "NEW", 145, "first")
SECOND = Node("2022-10-13", "SUN", 144, "second")


def raw_capture():
    def song(key, value):
        return {"title": "Same title", "sdvx_id": key, "releaseDate": FIRST.day,
                "endDate": None, "wiki_url": "",
                "charts": {"MAS": {"timeline": [
                    {"gen": 1, "genName": "NEW", "date": FIRST.day, "const": value},
                ]}}}
    return ("window.APP_DATA=" + json.dumps([song("00001", 14.5), song("00002", 12.9)]) + ";").encode("utf-8")


class ContinuityTests(unittest.TestCase):
    def test_active_node_on_same_and_later_editions(self):
        timeline = Timeline("00001", FIRST.day, None, (), "", (FIRST, SECOND))
        for day in (FIRST.day, "2022-04-13", "2022-04-14", "2022-10-12"):
            with self.subTest(day=day):
                self.assertEqual(timeline.at(day, THROUGH), FIRST)
        self.assertEqual(timeline.at(SECOND.day, THROUGH), SECOND)
        self.assertEqual(timeline.at(THROUGH, THROUGH), SECOND)
        self.assertIsNone(timeline.at("2021-11-03", THROUGH))
        self.assertIsNone(timeline.at("2026-11-01", THROUGH))
        self.assertIsNone(Timeline("00001", FIRST.day, None, (), "", ()).at(THROUGH, THROUGH))

    def test_release_suspension_restoration_and_removal_are_separate(self):
        timeline = Timeline("00001", "2021-11-18", "2024-06-19",
                            (("2022-01-01", "2022-02-01"),), "", (FIRST,))
        for day in ("2021-11-17", "2022-01-01", "2022-01-31", "2024-06-20"):
            with self.subTest(day=day):
                self.assertFalse(timeline.song_available(day))
                self.assertEqual(timeline.at(day, THROUGH), FIRST)
        for day in ("2021-11-18", "2022-02-01", "2024-06-19"):
            self.assertTrue(timeline.song_available(day))

    def classify(self, *, day="2022-10-12", version="NEW PLUS", state="playable",
                 trusted_tenths=None, observations=(), ambiguous=False):
        row = {"title": "Fixture", "difficulty": "ULT", "chart_id": "chart", "revision_id": "revision",
               "identity_basis": "historical_slot_unverified_revision", "song_state": "playable",
               "chart_state": state, "chart_quality": "B", "song_quality": "B",
               "event_kind": "exact" if trusted_tenths else None, "constant_quality": "B",
               "constant_tenths": trusted_tenths, "level_display": None,
               "song_observation": "song", "chart_observation": "chart"}
        timeline = Timeline("00001", FIRST.day, None, (), "", (FIRST,))
        unbound = [{"source_key": "00001", "node_version": FIRST.version}] if ambiguous else []
        return compare(row, version, EDITIONS[2][0], day, THROUGH, None if ambiguous else timeline,
                       unbound, observations, {})

    def test_ultima_anchor_does_not_enable_chart_before_actual_addition(self):
        before = self.classify(day="2022-05-10", state="not_released")
        after = self.classify(day="2022-05-11")
        self.assertEqual(before["node_tenths"], after["node_tenths"])
        self.assertEqual(before["preview_status"], "AVAILABILITY_BLOCKED")
        self.assertEqual(after["preview_status"], "CANDIDATE")
        self.assertFalse(after["content_revision_verified"])

    def test_independent_conflict_preserves_trusted_target_value(self):
        evidence = {"source_id": "max-eipi", "effective_on": "2022-09-01", "quality": "B", "tenths": 147}
        disagreement = self.classify(trusted_tenths=147, observations=[evidence])
        self.assertEqual(disagreement["preview_status"], "VERIFIED")
        self.assertEqual(disagreement["old_constant_tenths"], 147)
        self.assertTrue(disagreement["new_source_conflict"])
        self.assertTrue(disagreement["trusted_value_mismatch"])
        self.assertEqual(self.classify(observations=[evidence])["preview_status"], "CANDIDATE_CONFLICT")
        reference = self.classify(observations=[dict(evidence, quality="C")])
        self.assertFalse(reference["new_source_conflict"])
        self.assertTrue(reference["source_disagreement_with_independent_reference"])
        self.assertEqual(self.classify(observations=[dict(evidence, effective_on=SECOND.day)])["preview_status"],
                         "CANDIDATE")

    def test_agreement_requires_target_edition_exact_independent_ab(self):
        evidence = {"source_id": "max-eipi", "effective_on": "2022-09-01", "quality": "B", "tenths": 145}
        self.assertTrue(self.classify(observations=[evidence])["can_upgrade"])
        self.assertFalse(self.classify(observations=[dict(evidence, quality="C")])["can_upgrade"])
        self.assertFalse(self.classify(observations=[dict(evidence, source_id="wikiwiki")])["can_upgrade"])
        self.assertFalse(self.classify(observations=[dict(evidence, source_id="jp-paired-sheets")])["can_upgrade"])
        self.assertFalse(self.classify(observations=[evidence], ambiguous=True)["can_upgrade"])
        blocked = self.classify(ambiguous=True)
        self.assertTrue(blocked["identity_blocked_with_carry"])
        self.assertIsNone(blocked["node_tenths"])

    def test_parser_does_not_merge_titles_or_invent_lower_chart_nodes(self):
        histories = timelines(raw_capture(), EDITIONS)
        self.assertEqual(histories["00001", "MAS"].at(FIRST.day, THROUGH).tenths, 145)
        self.assertEqual(histories["00002", "MAS"].at(FIRST.day, THROUGH).tenths, 129)
        self.assertIsNone(histories["00001", "EXP"].at(FIRST.day, THROUGH))
        self.assertNotIn(("00001", "ADV"), histories)

    def test_ambiguous_source_identity_has_no_binding(self):
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.execute("CREATE TABLE external_id (region,artifact_id,revision_id,external_song_key,external_chart_key)")
            conn.executemany("INSERT INTO external_id VALUES ('JP','timemachine',?,?,'MAS')",
                             [("left", "00001"), ("right", "00001"), ("unrelated", "00002")])
            bindings, ambiguous = revision_bindings(conn)
        self.assertEqual(bindings, {"unrelated": ("00002", "MAS")})
        self.assertEqual(ambiguous, {"left": [("00001", "MAS")], "right": [("00001", "MAS")]})

    def test_read_only_preview_rebuild_and_filters(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog_path, sources, lock = self.fixture(root)
            initial = catalog_path.read_bytes()
            catalog = HistoryCatalog(catalog_path)
            first = export(catalog, sources, root / "first", lock=lock)
            repeat = export(catalog, sources, root / "repeat", lock=lock)
            self.assertEqual(first, repeat)
            self.assertEqual(first["counts"]["old_trusted"], 1)
            self.assertEqual(first["counts"]["preview_trusted"], 1)
            self.assertEqual(first["counts"]["unknown_recovered_by_carry"], 1)
            for path in (root / "first").iterdir():
                self.assertEqual(path.read_bytes(), (root / "repeat" / path.name).read_bytes())
            filtered = export(catalog, sources, root / "filtered", "NEW PLUS", "MAS", lock=lock)
            self.assertEqual(filtered["versions"], 1)
            self.assertEqual(filtered["counts"]["unknown_recovered_by_carry"], 1)
            with (root / "filtered" / "b51_continuity_candidates.csv").open(encoding="utf-8-sig", newline="") as handle:
                row, = list(csv.DictReader(handle))
            self.assertEqual(row["node_tenths"], "145")
            self.assertEqual(row["old_timemachine_edition_eligible"], "False")
            self.assertEqual(catalog_path.read_bytes(), initial)
            (sources / "data.js").write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                export(catalog, sources, root / "tampered", lock=lock)
            self.assertFalse((root / "tampered").exists())

    def fixture(self, root):
        raw = raw_capture()
        sources = root / "sources"
        sources.mkdir()
        (sources / "data.js").write_bytes(raw)
        artifact = {"id": "timemachine", "file": "data.js", "sha256": sha(raw),
                    "observed_on": THROUGH, "upstream_version": "fixture"}
        lock = root / "lock.json"
        lock.write_text(json.dumps({"artifacts": [artifact]}), encoding="utf-8")
        package = root / "package"
        package.mkdir()
        database = package / "catalog.sqlite"
        with closing(sqlite3.connect(database)) as conn, conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(SCHEMA.read_text(encoding="utf-8-sig"))
            conn.execute("INSERT INTO package VALUES ('fixture','test','{}','fixture')")
            conn.execute("INSERT INTO source VALUES ('timemachine','https://example.test','fixture')")
            conn.execute("INSERT INTO source_artifact VALUES (?,?,?,?,?,?,?,?)",
                         ("timemachine", "timemachine", "https://example.test/data.js", sha(raw),
                          len(raw), THROUGH, "fixture", "test"))
            db = CatalogImport(conn, {"timemachine": artifact})
            for start, stop, version in list(EDITIONS.values())[:2]:
                checkpoint = offset(stop, -1)
                db.release("JP", version, start, stop, checkpoint, "Asia/Tokyo", "history", "timemachine", version)
                db.points.append(("JP", version, checkpoint, "version_end"))
            song = db.song(("sdvx", "00001"), "Same title", "timemachine", "song")
            revision = db.chart(song, "MAS", "historical_slot_unverified_revision")
            db.external("timemachine", "JP", "00001", "MAS", song, "chart", revision)
            state = [(FIRST.day, offset(THROUGH), "playable", "B", "day", 10)]
            db.states("timemachine", "song", "JP", song, "Same title", state)
            db.states("timemachine", "chart", "JP", song, "Same title", state, revision=revision)
            db.constant("timemachine", "node", "JP", song, revision, "14.5", 145,
                        start=FIRST.day, end=EDITIONS[1][1])
            db.materialize_intervals()
            publish_snapshots(db, ())
        return database, sources, lock
