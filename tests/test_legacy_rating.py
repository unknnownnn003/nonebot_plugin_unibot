"""Historical formulas, chart identity guards and pinned table calibration."""
from decimal import Decimal as D
import importlib
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / "nonebot_plugin_unibot"


class LegacyRatingTests(unittest.TestCase):
    def setUp(self):
        package = ModuleType("legacy_fixture")
        package.__path__ = [str(ROOT)]
        modules = {package.__name__: package, "legacy_fixture.best_rating": NS(normalize_fc=lambda x: x)}
        scope = patch.dict(sys.modules, modules)
        scope.start()
        self.addCleanup(scope.stop)
        self.legacy = importlib.import_module("legacy_fixture.services.legacy_rating")
        self.model = importlib.import_module("legacy_fixture.services.song_model")

    def test_formula_boundaries_and_cap(self):
        l = self.legacy
        expected = {500_000: "0", 800_000: "4.5", 900_000: "9", 925_000: "10.6666666667",
                    975_000: "14", 990_000: "14.6", 1_000_000: "15", 1_005_000: "15.5",
                    1_007_499: "15.9998", 1_007_500: "16", 1_009_000: "16", 1_010_000: "16"}
        for score, value in expected.items():
            with self.subTest(score=score):
                self.assertEqual(l.chart_rating(14, score).quantize(D(".0000000001")), D(value))
                for constant in (D("1"), D("4.9"), D("5.1"), D("14.1")):
                    left, middle, right = [l.chart_rating(constant, score + delta) for delta in (-1, 0, 1)]
                    self.assertTrue(D(0) <= left <= middle <= right <= constant + 2)
                    self.assertLess(right - left, D(".001"))

    def test_historical_rank_and_color(self):
        l = self.legacy
        for rank, score in (("C", 500000), ("B", 600000), ("BB", 700000), ("BBB", 800000),
                            ("A", 900000), ("AA", 925000), ("AAA", 950000), ("S", 975000),
                            ("SS", 1000000), ("SSS", 1007500)):
            self.assertEqual(l.score_rank(score), rank)
            self.assertNotEqual(l.score_rank(score - 1), rank)
        self.assertEqual([l.score_rank(s) for s in (990000, 1005000, 1009000)], ["S", "SS", "SSS"])
        for frame, threshold in enumerate((4, 7, 10, 12, 13, 14, D("14.50"), 15), 2):
            self.assertEqual(l.rating_frame(threshold), frame)
            self.assertEqual(l.rating_frame(threshold - D(".01")), frame - 1)

    def test_pinned_snapshot_and_theoretical_limits(self):
        l = self.legacy
        data = json.loads(l.HISTORY_PATH.read_text(encoding="utf-8"))
        history = l.load_history()
        self.assertEqual(len(data["songs"]), 893)
        self.assertEqual(len(history), data["mapping"]["mapped_charts"])
        self.assertIn((1086, 2), history)
        self.assertNotIn((1086, 3), history)  # SEGA-confirmed layout revision.
        self.assertNotIn((2792, 3), history)  # Same title, post-LOST chart.
        self.assertTrue(all(0 <= difficulty < 4 for _, difficulty in history))
        ratings = [l.chart_rating(D(c) / 10, 1_007_500) for row in data["songs"] for c in row["constants"]]
        self.assertEqual(max(ratings), D("16.1"))
        self.assertEqual(l.simulate(ratings), l.Simulation(D("15.97"), D("16.0025"), D("15.9925"), D(".01")))
        data["songs"].append(data["songs"][0])
        with self.assertRaises(ValueError):
            l.parse_history(data)

    def test_fingerprint_rejects_changed_note_counts_but_accepts_key_order(self):
        l = self.legacy
        song = dict(title="Demo", artist="Author", version=15500)
        chart = dict(version=15500, note_designer="Fixture", notes={"total": 120, "tap": 100})
        signature = l.chart_identity(song, chart)
        self.assertEqual(signature, l.chart_identity(song, dict(chart, notes={"tap": 100, "total": 120})))
        self.assertNotEqual(signature, l.chart_identity(song, dict(chart, notes={"tap": 99, "total": 120})))
        self.assertNotEqual(signature, l.chart_identity(song, dict(chart, notes=None)))

    def test_id_difficulty_matching_deduplication_and_fingerprint(self):
        l = self.legacy
        def song(sid):
            return dict(id=sid, title=f"Song {sid}", version=15500, artist="Fixture",
                        difficulties=[dict(difficulty=i, version=15500, level_value=15.7, note_designer="A")
                                      for i in (2, 3, 4)])
        songs = [song(sid) for sid in range(1, 5)]
        history = {(s["id"], c["difficulty"]): l.HistoricalChart(s["title"], D("13.7"), l.chart_identity(s, c))
                   for s in songs for c in s["difficulties"] if c["difficulty"] < 4}
        songs[2]["difficulties"][1]["note_designer"] = "replacement"
        songs[3]["disabled"] = True
        catalog = self.model.Catalog({"songs": songs})
        records = [dict(id=sid, level_index=diff, score=score, rating=100, rank="SSS+")
                   for sid, diff, score in [(2, 3, 1_009_000), (1, 4, 1_010_000), (1, 3, 975_000),
                                            (1, 3, 1_009_000), (3, 3, 1_010_000), (4, 3, 1_009_000),
                                            (8, 3, 1_010_000), (1, 2, 1_009_000), (2, 5, 1_010_000)]]
        items = l.select(NS(scores=records), catalog, history)
        self.assertEqual([(r["id"], r["level_index"]) for r in items], [(1, 2), (1, 3), (2, 3)])
        self.assertTrue(all(r["rating"] == D("15.7") and r["rank"] == "SSS" and r["level_value"] == D("13.7") for r in items))
        self.assertEqual(items, l.select(NS(scores=list(reversed(records))), catalog, history))
        self.assertEqual(l.simulate(r["rating"] for r in items), l.Simulation(D("1.57"), D("5.1025"), D("2.355"), D("2.7475")))
        self.assertEqual(l.simulate([]), l.Simulation(D(0), D(0), D(0), D(0)))
