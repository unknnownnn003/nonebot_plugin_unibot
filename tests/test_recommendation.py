"""Offline recommendation tests against the real catalog and scoring service."""
import importlib
from pathlib import Path
from random import Random
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1] / "nonebot_plugin_unibot"


def chart(constant, difficulty=3, **attributes):
    level = str(int(constant)) + ("+" if round(constant * 10) % 10 >= 5 else "")
    return dict(level_value=constant, level=level, difficulty=difficulty, **attributes)


def song(sid, *charts, version=1, **attributes):
    return dict(id=sid, title=f"Chart {sid}", difficulties=list(charts), version=version, **attributes)


def score(sid, points, op, difficulty=3, **attributes):
    return dict(id=sid, score=points, over_power=op, level_index=difficulty,
                rating=attributes.get("rating", 0), full_combo=attributes.get("full_combo"))


class RecommendationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        package = ModuleType("recommendation_fixture")
        package.__path__ = [str(ROOT)]
        cls.module_patch = patch.dict(sys.modules, {package.__name__: package})
        cls.module_patch.start()
        cls.addClassCleanup(cls.module_patch.stop)
        cls.rules = importlib.import_module("recommendation_fixture.services.recommendation")
        cls.catalog = importlib.import_module("recommendation_fixture.services.song_model").Catalog

    def select(self, songs, scores, mode, level=None, *, seed=19, previous=()):
        return self.rules.select(SimpleNamespace(player={"rating": 13}, scores=scores),
                                 self.catalog({"songs": songs, "rating_version": 2,
                                               "versions": [{"version": 1}, {"version": 2}]}), mode, level,
                                 previous=previous, rng=Random(seed))

    def test_command_modes_and_strict_filters(self):
        for text, expected in {
            "": ("auto", None), "推分": ("score", None), "推OP": ("op", None),
            "综合": ("auto", None), "综合 14.8": ("auto", "14.8"), "14+": ("auto", "14+"), "14.8": ("auto", "14.8"),
            "１４＋　推ＯＰ": ("op", "14+"), "aj": ("aj", None), "AJC": ("ajc", None),
            "fc": ("fc", None),
        }.items():
            self.assertEqual(self.rules.parse_filter(text), expected)
        for text in ("14+ 14.8", "推分 推OP", "综合 推分", "综合 综合", "14.8xx", "ajc 14.88", "未认识的条件"):
            with self.assertRaises(ValueError):
                self.rules.parse_filter(text)
        songs = [song(1, chart(14.8)), song(2, chart(14.9)), song(3, chart(14.3))]
        scores = [score(i, 1_007_000, 80) for i in range(1, 4)]
        self.assertEqual([c.key for _, c in self.select(songs, scores, "op", "14.8")], [(1, 3)])
        self.assertEqual({c.key for _, c in self.select(songs, scores, "op", "14+")}, {(1, 3), (2, 3)})

    def test_two_slots_and_scarce_songs(self):
        songs = [song(1, chart(14.8), chart(14.9, 4)), song(2, chart(14.8)), song(3, chart(14.8))]
        scores = [score(1, 1_008_000, 84.75), score(1, 1_008_500, 86, 4),
                  score(2, 1_007_000, 89), score(3, 1_006_900, 89)]
        for seed in range(12):
            default = self.select(songs, scores, "auto", seed=seed)
            self.assertEqual([mode for mode, _ in default], ["score", "op"])
            self.assertIn(default[0][1].song.id, {2, 3})
            self.assertEqual(default[1][1].song.id, 1)
            self.assertEqual([mode for mode, _ in self.select(songs, scores, "score", seed=seed)], ["score", "score"])
            self.assertEqual([mode for mode, _ in self.select(songs, scores, "op", seed=seed)], ["op"])

    def test_per_song_op_and_unknown(self):
        songs = [song(1, chart(13.9, 2), chart(14.7), chart(15.0, 4), chart(19, 5)),
                 song(2, chart(14.7)), song(3, chart(14.8)), song(4, chart(14.6)),
                 song(5, chart(14.7)), song(6, chart(14.7), chart(15, 4)),
                 song(7, chart(14.8), disabled=True),
                 song(8, chart(14.8, release_date="2999-12-31"))]
        scores = [score(1, 1_008_000, 84.5, 2), score(1, 1_006_000, 80),
                  score(1, 1_005_000, 88, 4), score(1, 1_010_000, 999, 5),
                  score(2, 1_007_500, 84), score(3, 1_008_000, 85), score(4, 1_007_800, 84.5),
                  score(6, 1_007_500, 82, 4)]
        snapshot = SimpleNamespace(player={"rating": 16}, scores=scores)
        catalog = self.catalog({"songs": songs})
        choices = {c.key: c.data("auto") for c in self.rules.candidates(snapshot, catalog, "auto", None)}
        self.assertNotIn((7, 3), choices)
        self.assertNotIn((8, 3), choices)
        self.assertEqual(choices[(1, 3)]["current_chart_op"], 80)
        self.assertEqual(choices[(1, 3)]["current_song_op"], 88)
        self.assertEqual(choices[(1, 3)]["theoretical_chart_op"], 88.5)
        self.assertEqual(choices[(1, 3)]["theoretical_song_op"], 90)
        self.assertEqual(choices[(1, 3)]["theoretical_effective_room"], .5)
        self.assertEqual(choices[(1, 2)]["theoretical_effective_room"], 0)
        for key in ("score", "current_chart_op", "current_song_op", "theoretical_effective_room",
                    "predicted_effective_gain"):
            self.assertIsNone(choices[(5, 3)][key])
        self.assertIsNone(choices[(6, 3)]["current_chart_op"])
        self.assertEqual(choices[(6, 3)]["current_song_op"], 82)
        self.assertEqual(choices[(6, 3)]["theoretical_effective_room"], 6.5)
        for seed in range(12):
            picks = self.select(songs, scores, "op", seed=seed)
            self.assertEqual(len({c.song.id for _, c in picks}), len(picks))
            self.assertTrue(all(c.headroom for _, c in picks))

    def test_adapts_across_levels_and_rotates(self):
        songs = [song(group * 100 + i, chart(round(level + .1 * (i % 3), 1), difficulty))
                 for group, (level, difficulty) in enumerate(((9.2, 2), (12.5, 3), (14.7, 4)), start=1)
                 for i in range(12)]
        for group, (level, difficulty, points) in enumerate(((9.2, 2, 978_000), (12.5, 3, 998_500),
                                                           (14.7, 4, 1_008_200)), start=1):
            records = []
            for i in range(8):
                current = points + i * 100
                constant = round(level + .1 * (i % 3), 1)
                power = self.rules._target_power(constant, current)
                records.append(score(group * 100 + i, current, power, difficulty))
            previous = ()
            for seed in range(24):
                picks = self.select(songs, records, "auto", seed=seed, previous=previous)
                self.assertEqual([mode for mode, _ in picks], ["score", "op"])
                keys = tuple(c.key for _, c in picks)
                self.assertEqual(len({sid for sid, _ in keys}), 2)
                self.assertNotEqual(set(previous), set(keys))
                self.assertTrue(all(level <= c.chart.attributes["level_value"] <= level + .21
                                    for _, c in picks))
                self.assertTrue(all(c.target > c.record["score"] for mode, c in picks if mode == "score"))
                previous = keys

    def test_empty_support_theory_and_combo_modes(self):
        songs = [song(i, chart(14.8)) for i in range(1, 10)]
        self.assertEqual(self.select(songs, [], "auto"), [])
        self.assertEqual(self.select(songs, [score(1, 1_010_000, 89)], "auto"), [])
        scores = [score(1, 1_009_100, 85), score(2, 1_009_200, 85, full_combo="fullcombo"),
                  score(3, 1_009_300, 85, full_combo="alljustice"),
                  score(4, 1_010_000, 89, full_combo="alljusticecritical")]
        snapshot = SimpleNamespace(player={"rating": 16}, scores=scores)
        catalog = self.catalog({"songs": songs})
        for mode, eligible in {"fc": {1}, "aj": {1, 2}, "ajc": {1, 2, 3}}.items():
            candidates = list(self.rules.candidates(snapshot, catalog, mode, None))
            self.assertEqual({c.song.id for c in candidates if c.record}, eligible)
            self.assertTrue(all(c.objective == mode.upper() for c in candidates))
        self.assertEqual(self.select(songs, scores, "auto", "9"), [])
        self.assertEqual(len(self.select(songs, scores[:1], "auto")), 1)

    def test_lxns_op_calculator_boundaries(self):
        power = self.rules._target_power
        for points, expected in ((500_000, 0), (800_000, 24.5), (900_000, 49), (925_000, 59),
                                 (975_000, 74), (1_000_000, 79), (1_005_000, 81.5),
                                 (1_007_500, 84), (1_009_000, 86.25), (1_010_000, 88)):
            self.assertAlmostEqual(power(14.8, points), expected)
        self.assertAlmostEqual(power(14.8, 974_999, 2), power(14.8, 974_999))
        self.assertAlmostEqual(power(14.8, 1_007_500, 1), 84.5)
        self.assertAlmostEqual(power(14.8, 1_007_500, 2), 85)
        self.assertAlmostEqual(power(14.8, 1_010_000, 3), 89)

    def test_op_targets_require_a_song_wide_improvement(self):
        songs = [song(1, chart(14.8), chart(15.0, 4))]
        for points, existing, other_op, expected in (
            (1_007_300, None, 84, (1_007_300, "FC")),
            (1_007_300, None, 84.35, (1_007_500, "FC")),
            (1_007_300, "fullcombo", 84.6, (1_007_300, "AJ")),
            (1_007_300, "alljustice", 84.9, (1_007_500, "AJ")),
            (1_009_600, "alljustice", 88.95, (1_010_000, "AJC")),
            (1_007_300, "alljustice", 87, None),
        ):
            combo = self.rules.COMBOS[existing or ""]
            records = [score(1, points, self.rules._target_power(14.8, points, combo), full_combo=existing),
                       score(1, 1_009_500, other_op, 4)]
            picks = self.select(songs, records, "op", "14.8")
            target = (picks[0][1].target, picks[0][1].objective) if picks else None
            self.assertEqual(target, expected)
            self.assertEqual(len(self.select(songs, records, "score", "14.8")), int(points < 1_009_000))
        record = score(1, 1_007_300, 83.8)
        record.pop("full_combo")
        self.assertIsNone(self.rules._op_target(14.8, 84, record, 1_007_500, "SSS"))

    def test_score_target_must_move_a_full_board(self):
        filler = [song(i, chart(15.0), version=1) for i in range(2, 32)]
        records = [score(i, 1_009_000, 90, rating=17.15) for i in range(2, 32)]
        target = song(1, chart(14.8, version=2), version=1)
        record = score(1, 1_008_999, 84, rating=16.9499)
        self.assertEqual(self.select([target, *filler], [record, *records], "score", "14.8"), [])
        target["version"] = 2
        target["difficulties"][0]["version"] = 1
        catalog = self.catalog({"songs": [target, *filler], "versions": [{"version": 1}, {"version": 2}],
                                "rating_version": 2})
        snapshot = SimpleNamespace(player={"rating": 17}, scores=[record, *records])
        [(mode, pick)] = self.rules.select(snapshot, catalog, "score", "14.8", rng=Random(1))
        self.assertEqual(pick.target, 1_009_000)
        self.assertEqual(pick.data(mode)["rating_gain"], .000002)

    def test_score_target_crosses_cutoff_or_excludes_rating_plateau(self):
        records = [score(i, 1_007_500, 84, rating=16.82) for i in range(2, 32)]
        songs = [song(1, chart(14.8)), *(song(i, chart(14.9)) for i in range(2, 32))]
        picks = self.select(songs, [score(1, 1_007_400, 83, rating=16.78), *records], "score", "14.8")
        self.assertEqual(picks[0][1].target, 1_007_701)
        gain = picks[0][1].data("score")["rating_gain"]
        self.assertGreater(gain, 0)
        self.assertLess(gain, .01)
        self.assertEqual(self.select(songs, [score(1, 1_009_000, 86, rating=16.94), *records], "score", "14.8"), [])

    def test_score_target_requires_a_gain_in_the_correct_version_partition(self):
        target = song(1, chart(14.8), version=2)
        new = [song(i, chart(15), version=2) for i in range(2, 22)]
        old = [song(i, chart(12), version=1) for i in range(22, 52)]
        current = score(1, 1_007_400, 83, rating=16.78)
        records = [current, *(score(i, 1_009_000, 90, rating=17.15) for i in range(2, 22)),
                   *(score(i, 1_000_000, 65, rating=13) for i in range(22, 52))]
        self.assertEqual(self.select([target, *new, *old], records, "score", "14.8"), [])
        target["version"] = 1
        [(_, pick)] = self.select([target, *new, *old], records, "score", "14.8")
        self.assertEqual(pick.data("score")["rating_gain"], .0004)
        missing_version = self.catalog({"songs": [target]})
        snapshot = SimpleNamespace(player={"rating": 17}, scores=[current])
        self.assertEqual(self.rules.select(snapshot, missing_version, "score", "14.8"), [])
