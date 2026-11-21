"""Offline Wiki precedence and time-machine continuity regressions."""
import os
from pathlib import Path
import sys
import unittest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
try:
    from build_history_catalog import SCHEMA
    from history_enrichment import resolve_version_constants
    from history_import import CatalogImport, covering
    from history_jp import HISTORY_BASIS, import_historical_chart
    from history_wiki import parse_history_lines
    from _unibot_history.history import HistoryCatalog
finally:
    sys.path.remove(str(TOOLS))

import sqlite3


class HistoricalEvidenceTests(unittest.TestCase):
    def fixture(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.executescript(SCHEMA.read_text(encoding="utf-8-sig"))
        conn.execute("INSERT INTO source VALUES ('fixture', 'https://example.test', 'fixture')")
        artifacts = dict.fromkeys(("timemachine", "wiki-a", "wiki-b", "workbook"), {})
        conn.executemany("INSERT INTO source_artifact VALUES (?, 'fixture', 'https://example.test', ?, 0, NULL, NULL, 'test')",
                         [(key, "0" * 64) for key in artifacts])
        db = CatalogImport(conn, artifacts)
        db.editions.update({1: ("2021-11-04", "2022-04-14", "2022-04-13", "NEW"),
                            2: ("2022-04-14", "2022-10-13", "2022-10-12", "NEW PLUS"),
                            3: ("2022-10-13", "2023-05-11", "2023-05-10", "SUN")})
        song = db.song(("fixture", "1"), "Chart", "timemachine", "song")
        revision = db.chart(song, "MAS", HISTORY_BASIS)
        periods = [("2021-11-04", "2023-05-11", "playable", "B", "day", 10)]
        db.states("timemachine", "song", "JP", song, "Chart", periods)
        db.states("timemachine", "chart", "JP", song, "Chart", periods, revision=revision)
        return db, song, revision

    def wiki(self, db, song, revision, source, tenths):
        return db.constant(source, "history", "JP", song, revision, {}, tenths,
                           start="2022-04-14", end="2023-05-11", grain="version", quality="A",
                           value_kind="wiki_verified", priority=90)

    def test_wiki_does_not_need_second_source_and_retains_losing_evidence(self):
        db, song, rev = self.fixture()
        wiki = self.wiki(db, song, rev, "wiki-a", 132)
        sheet = db.constant("workbook", "cell", "JP", song, rev, {}, 133,
                            start="2022-10-12", end="2022-10-13")
        upstream = db.constant("timemachine", "node", "JP", song, rev, {}, 131,
                               start="2022-10-13", end="2023-05-11")
        resolve_version_constants(db)
        for day in ("2022-04-14", "2022-10-12", "2023-05-10"):
            self.assertEqual(covering(db.constants["JP", rev], day)[:3], ("exact", 132, wiki))
        self.assertEqual(db.conn.execute("SELECT DISTINCT status FROM conflict").fetchall(), [("resolved",)])
        self.assertEqual({row[0] for row in db.conn.execute("SELECT observation_id FROM conflict_observation")},
                         {wiki, sheet, upstream})

    def test_internal_disagreement_is_open_and_has_no_selected_value(self):
        db, song, rev = self.fixture()
        self.wiki(db, song, rev, "wiki-a", 132)
        self.wiki(db, song, rev, "wiki-b", 133)
        resolve_version_constants(db)
        self.assertEqual(covering(db.constants["JP", rev], "2022-10-12")[:2], ("conflict", None))
        self.assertEqual(db.conn.execute("SELECT DISTINCT reason,status FROM conflict").fetchall(),
                         [("wiki_internal_disagreement", "open")])

    def test_inherits_through_editions_until_next_node(self):
        db, song, rev = self.fixture()
        row = {"title": "Chart", "releaseDate": "2021-12-02", "charts": {"MAS": {"timeline": [
            {"gen": 1, "date": "2021-12-02", "const": "14.5"},
            {"gen": 3, "date": "2022-11-01", "const": "14.4"},
        ]}}}
        import_historical_chart(db, row, "collection", song, "test", "MAS")
        # An additional same-version work sheet agreeing with the new node
        # still cannot place it before the known change date.
        db.constant("workbook", "cell", "JP", song, rev, {}, 144, start="2023-05-10", end="2023-05-11")
        resolve_version_constants(db)
        intervals = db.constants["JP", rev]
        self.assertIsNone(covering(intervals, "2021-12-01"))
        for day in ("2021-12-02", "2022-04-14", "2022-10-31"):
            self.assertEqual(covering(intervals, day)[1], 145)
        self.assertEqual(covering(intervals, "2022-11-01")[1], 144)
        proof = covering(intervals, "2022-04-14")[2]
        self.assertEqual(db.conn.execute("SELECT quality FROM observation WHERE id=?", (proof,)).fetchone(), ("C",))

    def test_parser_ignores_current_values_comments_and_display_only(self):
        rows = parse_history_lines([
            "L10: 譜面定数〖MAS…13.9〗",
            "L11: |~NEW～|3|7|10+|13+|",
            "L12: //譜面定数MAS〖13.5(NEW～)〗",
            "L13: 譜面定数MAS〖12.7(AMAZON+～PARADISE×)、13.7(SUN～)〗",
        ])
        self.assertEqual([(r["difficulty"], r["first"], r["last"], r["tenths"]) for r in rows],
                         [("MAS", "AMAZON PLUS", "PARADISE LOST", 127), ("MAS", "SUN", "X-VERSE-X", 137)])


@unittest.skipUnless(os.environ.get("UNIBOT_TEST_HISTORY_B52"), "requires the staged B5.2 package")
class PublishedB52Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = HistoryCatalog(Path(os.environ["UNIBOT_TEST_HISTORY_B52"]))

    def chart(self, version, title, difficulty, date=None):
        return next(row for row in self.catalog.get_snapshot("JP", version, date)["charts"]
                    if row["title"] == title and row["difficulty"] == difficulty)

    def test_precise_addition_removal_and_same_title_case(self):
        with self.catalog._connect() as conn:
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(self.chart("NEW", "Redo", "ULT", "2022-03-16")["constant_status"], "not_applicable")
        self.assertEqual(self.chart("NEW", "Redo", "ULT", "2022-03-17")["constant_tenths"], 137)
        self.assertEqual(self.chart("SUN", "Redo", "ULT", "2022-10-13")["constant_status"], "not_applicable")
        self.assertEqual(self.chart("X-VERSE-X", "FLOWER", "MAS")["constant_tenths"], 130)
        self.assertEqual(self.chart("X-VERSE-X", "Flower", "MAS")["constant_tenths"], 126)

    def test_wiki_corrections_and_unresolved_conflicts(self):
        for version in ("PARADISE", "PARADISE LOST"):
            for title, value in (("Sparking Revolver", 114), ("パームカルマ", 115)):
                with self.subTest(version=version, title=title):
                    row = self.chart(version, title, "EXP")
                    self.assertEqual((row["constant_status"], row["constant_tenths"]), ("wiki_verified", value))
        for version in ("SUN", "SUN PLUS"):
            self.assertEqual(self.chart(version, "地獄の端にて君を待つ", "MAS")["constant_status"], "conflict")
        for version in ("NEW", "NEW PLUS"):
            self.assertEqual(self.chart(version, "Axium Crisis", "MAS")["constant_status"], "conflict")
        self.assertEqual(self.chart("SUN", "Axium Crisis", "MAS")["constant_tenths"], 137)
