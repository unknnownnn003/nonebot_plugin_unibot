"""Constant promotion needs a chart identity and a matching observation horizon."""
from decimal import Decimal
import os
from pathlib import Path
import sqlite3
import sys
import unittest


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
try:
    from build_history_catalog import SCHEMA
    from history_constant_sources import mate_low_level_rows, survey_rows
    from history_enrichment import Candidate, known_display_constant, ordinary_slot, resolve_checkpoint
    from history_import import CatalogImport, covering, offset
    from history_jp import HISTORY_BASIS
    from _unibot_history.history import HistoryCatalog, resolve_release
finally:
    sys.path.remove(str(TOOLS))


class ConstantEvidenceTests(unittest.TestCase):
    day = "2021-01-20"

    def fixture(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.executescript(SCHEMA.read_text(encoding="utf-8-sig"))
        conn.execute("INSERT INTO source VALUES ('test','https://example.test/','fixture')")
        artifacts = dict.fromkeys(("old", "sheet", "other", "survey"), {"observed_on": None})
        conn.executemany("INSERT INTO source_artifact VALUES (?, 'test', 'test', ?, 0, NULL, NULL, 'test')",
                         [(key, "0" * 64) for key in artifacts])
        db = CatalogImport(conn, artifacts)
        song = db.song(("test", "first"), "Example", "old", "song")
        db.ordinary["example"].add(song)
        db.states("old", "song", "JP", song, "Example", [(self.day, offset(self.day), "playable", "B", "day", 10)])
        return db, song, db.chart(song, "MAS", HISTORY_BASIS)

    def test_checkpoint_and_survey_are_distinct(self):
        db, song, chart = self.fixture()
        window = [("2021-01-12", offset(self.day), "playable", "B", "day", 10)]
        db.states("old", "earlier-song", "JP", song, "Example", window)
        db.states("old", "earlier-chart", "JP", song, "Example", window, revision=chart)
        candidate = Candidate("survey", "row 10", "2021-01-12", "reference", "Example", "MAS", 130)
        resolve_checkpoint(db, "CRYSTAL PLUS", self.day, song, chart, [candidate])
        proof = covering(db.constants["JP", chart], self.day)
        self.assertEqual(db.conn.execute("SELECT quality FROM observation WHERE id=?", (proof[2],)).fetchone(), ("C",))
        self.assertIsNone(covering(db.constants["JP", chart], offset(self.day)))
        self.assertEqual(db.conn.execute("SELECT DISTINCT effective_on FROM observation WHERE field='constant' ORDER BY 1")
                         .fetchall(), [("2021-01-12",), ("2021-01-20",)])

    def test_prior_verified_is_preserved_and_conflict_retains_both_values(self):
        db, song, chart = self.fixture()
        proof = db.constant("old", "row", "JP", song, chart, "13.0", 130, start=self.day)
        candidate = Candidate("sheet", "comparison!E2", None, "checkpoint", "Example", "MAS", 132)
        resolve_checkpoint(db, "CRYSTAL PLUS", self.day, song, chart, [candidate])
        self.assertEqual(covering(db.constants["JP", chart], self.day), ("exact", 130, proof, None))
        self.assertEqual(db.conn.execute("SELECT status, reason FROM conflict").fetchone(),
                         ("open", "additional_evidence_prior_verified_preserved"))
        self.assertEqual(db.conn.execute("SELECT count(*) FROM conflict_observation").fetchone(), (2,))

    def test_two_strong_values_keep_the_slot_unverified(self):
        db, song, chart = self.fixture()
        values = [Candidate(src, "comparison!E2", None, "checkpoint", "Example", "MAS", val)
                  for src, val in (("sheet", 130), ("other", 132))]
        resolve_checkpoint(db, "CRYSTAL PLUS", self.day, song, chart, values)
        self.assertEqual(covering(db.constants["JP", chart], self.day)[:2], ("conflict", None))

    def test_title_collision_and_prelaunch_ultima_are_rejected(self):
        db, song, _ = self.fixture()
        chart = db.chart(song, "ULT", HISTORY_BASIS)
        db.chart_states["JP", chart].append((self.day, offset(self.day), 10, ("not_released", None, None)))
        self.assertIsNone(ordinary_slot(db, Candidate("sheet", "row", None, "checkpoint", "Example", "ULT", 140), self.day))
        db.ordinary["example"].add("second")
        self.assertIsNone(ordinary_slot(db, Candidate("sheet", "row", None, "checkpoint", "Example", "MAS", 130), self.day))

    def test_survey_labels_and_current_wiki_difficulty_groups(self):
        survey = "<table><tr><td>13.7</td><td>A (EXP) / B / <s>C</s></td></tr></table>".encode()
        self.assertEqual([r[:3] for r in survey_rows(survey)], [("A", "EXP", 137), ("B", "MAS", 137)])
        formatted = [
            ["難易度", "ジャンル", "タイトル", "ノーツ数", "旧定数"], ["11.9"],
            ["MAS", "P&A", "A", "1000", "-"], ["B", "1100", "-"],
            ["EXP", "VAR", "C", "900", "11.8"], ["11.8"], ["no preceding difficulty", "190", "-"],
        ]
        wiki = "".join("<tr>" + "".join(f"<td>{v}</td>" for v in row) + "</tr>" for row in formatted)
        self.assertEqual([r[:4] for r in mate_low_level_rows(wiki.encode())],
                         [("A", "MAS", 1000, 119), ("B", "MAS", 1100, 119), ("C", "EXP", 900, 119)])
        self.assertEqual([known_display_constant(level) for level in ("1", "6", "7+", "9", "9+", "10", "6+")],
                         [10, 60, 75, 90, 95, None, None])


@unittest.skipUnless(os.environ.get("UNIBOT_TEST_HISTORY"), "requires a locally built history package")
class PinnedConstantEvidenceTests(unittest.TestCase):
    def test_reviewed_values_are_traceable_to_the_correct_edition_and_date(self):
        catalog = HistoryCatalog(resolve_release(Path(os.environ["UNIBOT_TEST_HISTORY"])))
        with catalog._connect() as conn:
            if not conn.execute("SELECT 1 FROM source_artifact WHERE id='b2-mate-low-level'").fetchone():
                self.skipTest("requires the B2 source lock")

        for version, title, difficulty, expected, day, source in (
            ("CRYSTAL PLUS", "No Routine", "BAS", "2.0", "2021-01-20", "b2-max-crystal-plus"),
            ("PARADISE", "No Routine", "EXP", "9.3", "2021-05-12", "b2-max-paradise"),
            ("X-VERSE-X", "ENDYMION", "ADV", "12.5", "2026-07-01", "b2-sheet-mate"),
            ("MATE", "The wheel to the right", "EXP", "11.9", "2026-11-21", "b2-mate-low-level"),
        ):
            with self.subTest(version=version, difficulty=difficulty):
                snapshot = catalog.get_snapshot("JP", version)
                self.assertEqual(snapshot["date"], day)
                chart = next(row for row in snapshot["charts"] if row["title"] == title and row["difficulty"] == difficulty)
                self.assertEqual(chart["trusted_constant"], Decimal(expected))
                proof = catalog.get_observation(chart["constant_observation"])
                self.assertEqual(proof["artifact_id"], source)
                self.assertEqual(len(proof["sha256"]), 64)

        previous = catalog.get_snapshot("JP", "PARADISE LOST")
        self.assertFalse(any(row["difficulty"] == "ULT" and row["state"] == "playable" for row in previous["charts"]))
