"""Versioned Catalog launch boundaries and regional calendar isolation."""
import csv
from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
try:
    import import_paradise_lost  # noqa: F401 - registers the isolated services namespace
    from history_calendar import JP_RELEASES, JP_THROUGH, TIMEMACHINE_THROUGH
    from history_calendar import jp_releases, version_calendar, write_version_calendar
    from history_import import CatalogImport
    from history_regions import release_observations
finally:
    sys.path.remove(str(TOOLS))


def source_generations():
    rows = [{"short": version, "start": day} for version, day, _ in JP_RELEASES]
    # The real source gives the NEW launch date as the LOST end.
    rows[11]["end"] = "2021-11-04"
    return rows


def source_bytes(rows):
    return f"window.GENS={json.dumps(rows)};".encode("utf-8")


class VersionCalendarTests(unittest.TestCase):
    def test_all_22_verified_launches_and_end_boundaries(self):
        rows = list(version_calendar())
        self.assertEqual(len(rows), 22)
        self.assertEqual(len({row["release_id"] for row in rows}), 22)
        self.assertEqual(len({row["start_source_url"] for row in rows}), 22)
        self.assertEqual(rows[0]["start_on"], "2015-07-16")
        self.assertEqual(rows[11]["version"], "PARADISE LOST")
        self.assertEqual(rows[11]["end_on"], "2021-11-03")
        self.assertEqual(rows[-2]["end_on"], "2026-07-01")
        for current, following in zip(rows, rows[1:]):
            with self.subTest(version=current["version"]):
                self.assertEqual(current["end_before"], following["start_on"])
                self.assertEqual(date.fromisoformat(current["end_on"]) + timedelta(days=1),
                                 date.fromisoformat(following["start_on"]))
                self.assertEqual(current["next_start_source_url"], following["start_source_url"])
                self.assertEqual(current["start_evidence"], "official")
                self.assertEqual(current["timezone"], "Asia/Tokyo")
        self.assertEqual(rows[-1]["version"], "MATE")
        self.assertEqual(rows[-1]["start_on"], "2026-07-02")
        self.assertIsNone(rows[-1]["end_on"])
        self.assertIsNone(rows[-1]["end_before"])
        self.assertEqual(rows[-1]["observation_on"], "2026-11-21")

    def test_observation_cannot_be_in_future(self):
        with self.assertRaisesRegex(ValueError, "observation window"):
            list(version_calendar(observed_through="2026-11-22", today=date(2026, 11, 21)))

    def test_csv_uses_stable_ids_and_current_observation(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "historical_version_calendar.csv"
            write_version_calendar(path)
            with path.open(newline="", encoding="utf-8") as csv_file:
                rows = list(csv.DictReader(csv_file))
        self.assertEqual(rows[-1]["release_id"], list(version_calendar())[-1]["release_id"])
        self.assertEqual(rows[-1]["end_on"], "")
        self.assertEqual(rows[-1]["observation_on"], JP_THROUGH)

    def test_pinned_generation_index_and_region_boundaries(self):
        db = self.database()
        jp_releases(db, source_bytes(source_generations()))
        release_observations(db)
        bounds = {(region, version): (start, end, through, tz) for region, version, start, end, through, tz
                  in db.conn.execute("SELECT region, version, start_on, end_before, through_on, timezone FROM region_release")}
        self.assertEqual(bounds["JP", "PARADISE LOST"],
                         ("2021-05-13", "2021-11-04", "2021-11-03", "Asia/Tokyo"))
        self.assertEqual(bounds["JP", "MATE"], ("2026-07-02", None, JP_THROUGH, "Asia/Tokyo"))
        self.assertEqual(bounds["INTL", "MATE"], ("2026-10-22", None, None, "Asia/Singapore"))
        self.assertEqual(bounds["CN", "CN2027"], ("2026-09-10", None, None, "Asia/Shanghai"))
        self.assertIn(("JP", "MATE", TIMEMACHINE_THROUGH, "observation"), db.points)
        self.assertNotIn(("JP", "MATE", JP_THROUGH, "observation"), db.points)
        for region, version, point, _ in db.points:
            start, end, through, _ = bounds[region, version]
            self.assertLessEqual(start, point)
            self.assertLessEqual(point, through)
            if end is not None:
                self.assertLess(point, end)

    def test_wrong_generation_start_fails_without_creating_calendar(self):
        db = self.database()
        rows = source_generations()
        rows[4]["start"] = "2017-02-09"
        with self.assertRaisesRegex(ValueError, "disagrees"):
            jp_releases(db, source_bytes(rows))
        self.assertFalse(db.editions)
        self.assertFalse(db.points)
        self.assertFalse(db.conn.execute("SELECT 1 FROM region_release").fetchone())

    def database(self):
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.executescript((TOOLS / "history_schema.sql").read_text(encoding="utf-8-sig"))
        connection.execute("INSERT INTO source VALUES ('test','https://example.test/','fixture')")
        connection.executemany("INSERT INTO source_artifact VALUES (?, 'test', 'test', ?, 0, NULL, NULL, 'test')",
                               [(key, "0" * 64) for key in
                                ("date-jp", "intl-xverse", "intl-xversex", "intl-mate", "cn-2027-evidence")])
        connection.executemany("INSERT INTO ruleset VALUES (?, 'test', 'partial', '{}', 'date-jp')",
                               [(rule,) for rule in ("old-r10", "new-r10", "verse-n20")])
        return CatalogImport(connection, {})
