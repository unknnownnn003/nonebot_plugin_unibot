"""JP HDD runtime contracts against the published local database when present."""
from contextlib import closing
from decimal import Decimal
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import import_paradise_lost  # Offline service bootstrap
from _unibot_history import jp_history, jp_rating, legacy_rating, rating
from _unibot_history.old_hdd_history import APPLICATION_ID

DATABASE = ROOT / ".tools/jp-hdd/compact/catalog.sqlite"


class JpFormulaTests(unittest.TestCase):
    def test_rating_caps_and_recent_slots(self):
        for old, new, score in (("16", "16", 1_007_500), ("16", "16.1499", 1_008_999),
                                ("16", "16.15", 1_009_000), ("16", "16.15", 1_010_000)):
            self.assertEqual(legacy_rating.chart_rating(14, score), Decimal(old))
            self.assertEqual(rating.chart_rating(14, score), Decimal(new))
        values = list(map(Decimal, (16, 14, 13)))
        result = legacy_rating.simulate(values)
        self.assertEqual(result.b30, sum(values) / 30)
        self.assertEqual(result.repeat_best, (43 + Decimal(160)) / 40)
        self.assertEqual(result.top_ten, Decimal(86) / 40)
        self.assertEqual(rating.average([{"rating": 16}], 50), .32)

    def test_corrupt_or_incomplete_database_fails_closed(self):
        with TemporaryDirectory() as root:
            path = Path(root) / "catalog.sqlite"
            with self.assertRaises(OSError):
                jp_history.JpHistory(path)
            with closing(sqlite3.connect(path)) as db, db:
                db.execute(f"PRAGMA application_id={APPLICATION_ID}")
                db.executescript("PRAGMA user_version=2; CREATE TABLE complete(ready); INSERT INTO complete VALUES(1);")
            with self.assertRaisesRegex(ValueError, "v3"):
                jp_history.JpHistory(path)
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("PRAGMA user_version=3")
            with self.assertRaisesRegex(ValueError, "Invalid"):
                jp_history.JpHistory(path)


@unittest.skipUnless(DATABASE.is_file(), "published JP HDD database is a local asset")
class PublishedJpHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.history = jp_history.JpHistory(DATABASE)

    def search(self, value):
        term, edition = self.history.split_query(value)
        return self.history.search(term, edition)

    def test_all_version_names_and_cutoffs(self):
        self.assertEqual(len(self.history.editions), 22)
        for edition in self.history.editions:
            name = edition["version"]
            with self.subTest(version=name):
                term, parsed = self.history.split_query(f"96  {name.lower()}")
                self.assertEqual((term, parsed), ("96", edition))
                self.assertIs(self.history.parse_version(name.swapcase()), edition)
                self.assertGreater(len(self.history.songs(name)), 0)
                b50 = edition["ordinal"] >= self.history.by_name["VERSE"]["ordinal"]
                self.assertEqual(jp_rating.validate_request(self.history, name, b50=b50), (edition, None))
                self.assertIn("/b50" if b50 else "/b30", jp_rating.validate_request(self.history, name, b50=not b50)[1])
        with closing(self.history._connect()) as db:
            self.assertEqual(db.execute("PRAGMA query_only").fetchone()[0], 1)

    def test_lookup_versions_renames_and_distinct_identities(self):
        self.assertEqual(self.search("鳥の詩")[0]["row"]["version"], "MATE")
        self.assertEqual(self.search("鳥の詩 AIR")[0]["row"]["version"], "AIR")
        self.assertEqual(self.search("Jade Star")[0]["row"]["version"], "MATE")
        self.assertEqual(self.search("SON OF SUN")[0]["row"]["version"], "MATE")
        self.assertGreater(len(self.search("50")), 1)
        self.assertGreater(len(self.search("GO")), 1)
        renamed = self.search("Help me, ERINNNNNN!! VERSE")
        self.assertEqual(len(renamed), 2)
        chosen = self.search("#0:97:1 VERSE")[0]["row"]
        self.assertIn("Band ver.", chosen["title"])
        past = self.search("#0:14:1")[0]["row"]
        self.assertNotEqual(past["version"], "MATE")
        self.assertIsNone(self.search("#0:14:1 MATE")[0]["row"])
        self.assertIsNone(self.search("#0:306:1 PARADISE")[0]["row"])
        self.assertEqual(self.search("#0:306:1 NEW")[0]["row"]["state"], "present")

    def test_hdd_constants_we_and_versions_do_not_use_cn(self):
        for version, expected in (("AIR", "11.90"), ("MATE", "12.90")):
            row = self.search("96 " + version)[0]["row"]
            charts = self.history.song_charts(row)
            master = next(chart for chart in charts if chart["difficulty"] == 3)
            self.assertEqual(master["level_value"], Decimal(expected))
            self.assertTrue(master["c2s_sha256"] and master["revision"] and row["jacket_sha256"])
        row = self.search("FREEDOM DiVE PARADISE LOST")
        self.assertEqual(len(row), 1)
        self.assertTrue(any(chart["difficulty"] == 5 for chart in self.history.song_charts(row[0]["row"])))

    def test_candidate_mappings_and_real_rating_partitions(self):
        for version in ("AIR", "PARADISE LOST", "LUMINOUS PLUS", "VERSE", "X-VERSE-X", "MATE"):
            edition = self.history.by_name[version]
            charts = jp_rating.inventory(self.history, edition)
            index = jp_rating.MappingIndex(self.history, edition, charts, [])
            self.assertEqual(index.resolve((96, 3), []), ("candidate", None))
            self.assertEqual(index.resolve((2**31, 3), []), ("unknown", None))
            self.assertEqual(len(charts), len(index.charts))
            if version == "AIR":
                self.assertTrue(all(chart["slot"] < 4 for chart in charts))
            if edition["ordinal"] >= self.history.by_name["VERSE"]["ordinal"]:
                new = [chart for chart in charts if jp_rating.partition(edition, chart) == "new"]
                self.assertTrue(new)
                self.assertTrue(all(chart["released_on"] >= edition["start_on"] for chart in new))
                self.assertTrue(any(chart["slot"] == 4 and chart["seen_before"]
                                    and jp_rating.partition(edition, chart) == "old" for chart in charts))

    def test_mapping_requires_matching_revision_and_unique_bilateral_identity(self):
        edition = self.history.by_name["AIR"]
        charts = jp_rating.inventory(self.history, edition)
        old = next(row for row in charts if row["chart_identity"] == "0:96:1:3:MAS")
        mate = next(row for row in jp_rating.inventory(self.history, self.history.by_name["MATE"])
                    if row["chart_identity"] == old["chart_identity"])
        self.assertNotEqual(old["notation_sha256"], mate["notation_sha256"])
        pin = self.history.score_mappings("PARADISE LOST")
        cn = next(row["cn_fingerprint"] for row in pin if row["chart_identity"] == old["chart_identity"])
        # Simulate the manifest gate locally. No such approval is shipped.
        mapping = jp_rating.Mapping(96, 3, cn, old["chart_identity"], old["notation_sha256"], "test only")
        index = jp_rating.MappingIndex(self.history, edition, charts, [mapping])
        self.assertEqual(index.resolve((96, 3), [cn])[0], "verified")
        self.assertEqual(index.resolve((96, 3), [])[0], "candidate")
        self.assertEqual(index.resolve((96, 3), [cn, cn])[0], "conflict")
        incompatible = jp_rating.Mapping(96, 3, cn, old["chart_identity"], mate["notation_sha256"], "test only")
        self.assertEqual(jp_rating.MappingIndex(self.history, edition, charts, [incompatible])
                         .resolve((96, 3), [cn])[0], "candidate")
