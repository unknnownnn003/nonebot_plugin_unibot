"""Rating partition, eligibility, WE and progress semantic regression."""
import importlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import patch
from datetime import datetime, timezone, timedelta
import unittest

ROOT = Path(__file__).resolve().parents[1] / "nonebot_plugin_unibot"


class RatingTests(unittest.TestCase):
    def setUp(self):
        package = ModuleType("rating_fixture"); package.__path__ = [str(ROOT)]
        services = ModuleType("rating_fixture.services"); services.__path__ = [str(ROOT / "services")]
        def meta(songs):
            return {(str(s["id"]), c["difficulty"]): dict(id=str(s["id"]), level_index=c["difficulty"],
                song_version=s.get("version"), version=c.get("version"), level_value=c["level_value"])
                for s in songs for c in s["difficulties"] if c["difficulty"] < 5}
        modules = {"rating_fixture": package, "rating_fixture.services": services,
                   "rating_fixture.best_rating": NS(build_chart_meta=meta, build_item=lambda m, r: dict(m, rating=r["rating"], score=r["score"]))}
        self.patch = patch.dict(sys.modules, modules); self.patch.start(); self.addCleanup(self.patch.stop)
        self.model = importlib.import_module("rating_fixture.services.song_model")
        self.rating = importlib.import_module("rating_fixture.services.rating")
        self.now = datetime(2026, 11, 17, 8, tzinfo=timezone(timedelta(hours=8)))

    def catalog(self, songs, version=24000):
        return self.model.Catalog(dict(songs=songs, rating_version=version,
            versions=[dict(version=v) for v in [10000, 23000, 24000, 24500]]))

    @staticmethod
    def song(sid, version=10000, difficulty=3, **attrs):
        return dict(id=sid, title=str(sid), version=version, **attrs,
            difficulties=[dict(difficulty=difficulty, version=24000, level="14", level_value=14)])

    @staticmethod
    def snapshot(songs):
        return NS(scores=[dict(id=s["id"], level_index=d["difficulty"], rating=s["id"] / 100 + 12, score=1000000)
                          for s in songs for d in s["difficulties"]])

    def test_disjoint_b50_and_unrestricted_b30_with_old_ultima(self):
        songs = [self.song(i) for i in range(1, 36)] + [self.song(i, 24000) for i in range(100, 126)]
        songs += [self.song(900, 10000, 4), self.song(999, 24000, 5), self.song(998, 24500)]
        catalog = self.catalog(songs); snapshot = self.snapshot(songs)
        old, new, unknown, version = self.rating.select(snapshot, catalog, b50=True, now=self.now)
        self.assertEqual((len(old), len(new), unknown, version), (30, 20, 1, 24000))
        self.assertEqual(old[0]["id"], "900")  # Old song's new ULTIMA stays BEST.
        self.assertEqual(new[0]["id"], "125")
        self.assertFalse({(r["id"], r["level_index"]) for r in old} & {(r["id"], r["level_index"]) for r in new})
        all_best, _, _, _ = self.rating.select(snapshot, catalog, now=self.now)
        self.assertEqual(len(all_best), 30)
        self.assertIn("125", [r["id"] for r in all_best])
        self.assertNotIn("999", [r["id"] for r in all_best])

    def test_release_cutover_disabled_and_invalid_date_contract(self):
        songs = [self.song(1, disabled=True), self.song(2, release_date="2026-11-18"),
                 self.song(3, release_date="2026-11-17"), self.song(4, release_date="invalid"), self.song(5)]
        songs[-1]["difficulties"][0]["release_date"] = "2026-11-18"
        catalog = self.catalog(songs)
        items, *_ = self.rating.select(self.snapshot(songs), catalog, now=self.now)
        self.assertEqual({r["id"] for r in items}, {"3", "4"})
        before = self.now.replace(hour=6, minute=59)
        items, *_ = self.rating.select(self.snapshot(songs), catalog, now=before)
        self.assertEqual({r["id"] for r in items}, {"4"})

    def test_missing_version_does_not_guess_max_and_fixed_divisors(self):
        songs = [self.song(1), self.song(2, 24000)]
        catalog = self.catalog(songs); catalog.metadata.pop("rating_version")
        with self.assertRaises(ValueError): self.rating.select(self.snapshot(songs), catalog, b50=True)
        self.assertEqual(len(self.rating.select(self.snapshot(songs), catalog)[0]), 2)
        self.assertEqual(self.rating.average([dict(rating=15)], 30), .5)
        self.assertEqual(self.rating.average([dict(rating=15)], 50), .3)


if __name__ == "__main__": unittest.main()
