"""Date and identity regressions for reviewed JP chart availability."""
from pathlib import Path
import sqlite3
import sys
import unittest


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
try:
    from build_history_catalog import SCHEMA
    from history_availability import ERIN_BAND, ERIN_ORIGINAL, ReleaseBatch, apply_erin_rename, apply_release_batch
    from history_import import CatalogImport, covering
    from history_jp import HISTORY_BASIS
    from _unibot_history.history import snapshot_rows
finally:
    sys.path.remove(str(TOOLS))


class AvailabilityEvidenceTests(unittest.TestCase):
    def setUp(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA.read_text(encoding="utf-8-sig"))
        conn.execute("INSERT INTO source VALUES ('test','https://example.test/','fixture')")
        artifacts = dict.fromkeys(("baseline", "official", "sega-verse-renames", "sega-verse-erin-2025"),
                                  {"observed_on": None})
        conn.executemany("INSERT INTO source_artifact VALUES (?, 'test', 'test', ?, 0, NULL, NULL, 'test')",
                         [(key, "0" * 64) for key in artifacts])
        self.db = CatalogImport(conn, artifacts)

    def add_song(self, key, title, periods, diff):
        db = self.db
        song = db.song(("sdvx", key), title, "baseline", key)
        db.baseline[key] = (song, {"title": title})
        db.states("baseline", key, "JP", song, title, periods)
        return song, db.chart(song, diff, HISTORY_BASIS)

    def test_ultima_first_day_and_later_suspension(self):
        db = self.db
        song, revision = self.add_song("12345", "Example", [
            ("2024-01-01", "2025-07-16", "playable", "B", "day", 10),
        ], "ULT")
        db.editions[19] = ("2024-12-12", "2025-07-16", "2025-07-15", "VERSE")
        chart_periods = [
            ("2024-12-12", "2025-05-25", "unknown", "C", "version", 10),
            ("2025-05-25", "2025-06-02", "suspended", "B", "day", 30),
            ("2025-06-02", "2025-07-16", "playable", "B", "day", 30),
        ]
        db.states("baseline", "chart", "JP", song, "Example", chart_periods, revision=revision)
        db.states("baseline", "intl chart", "INTL", song, "Example", chart_periods, revision=revision)
        apply_release_batch(db, ReleaseBatch("official", "2025-05-20", "ULTIMA", ("12345",)), "2025-07-16")
        for day, expected in (("2024-12-12", "not_released"), ("2025-05-19", "not_released"),
                              ("2025-05-20", "playable"), ("2025-05-24", "playable"),
                              ("2025-05-25", "suspended"), ("2025-06-02", "playable")):
            with self.subTest(day=day):
                self.assertEqual(covering(db.chart_states["JP", revision], day)[0], expected)
        self.assertEqual(covering(db.chart_states["INTL", revision], "2025-05-20")[0], "unknown")
        release = db.conn.execute(
            """SELECT json_extract(value_json,'$.version'), effective_on, quality
               FROM observation WHERE field='chart_release' AND revision_id=?""", (revision,),
        ).fetchone()
        self.assertEqual(tuple(release), ("VERSE", "2025-05-20", "A"))

    def test_erin_renames_one_song_and_keeps_rearrangement_separate(self):
        db = self.db
        original, original_chart = self.add_song("01034", ERIN_BAND, [
            ("2015-07-16", None, "playable", "B", "day", 10),
        ], "MAS")
        rearrangement, rearranged_chart = self.add_song("10084", ERIN_ORIGINAL, [
            ("2015-07-16", "2025-05-20", "not_released", "B", "day", 10),
            ("2025-05-20", None, "playable", "B", "day", 10),
        ], "MAS")
        for song, revision, title in ((original, original_chart, ERIN_BAND),
                                      (rearrangement, rearranged_chart, ERIN_ORIGINAL)):
            db.states("baseline", "chart", "JP", song, title,
                      [("2015-07-16", None, "playable", "B", "day", 10)], revision=revision)
        apply_erin_rename(db)
        db.materialize_intervals()

        def songs(day):
            return {r["song_id"]: (r["title"], r["song_state"], r["song_quality"], r["revision_id"])
                    for r in snapshot_rows(db.conn, "JP", day)}

        self.assertEqual(songs("2024-12-11")[original], (ERIN_ORIGINAL, "playable", "B", original_chart))
        self.assertEqual(songs("2024-12-12")[original], (ERIN_BAND, "playable", "B", original_chart))
        self.assertEqual(songs("2025-05-20")[rearrangement][1], "not_released")
        self.assertEqual(songs("2025-05-21")[rearrangement], (ERIN_ORIGINAL, "playable", "B", rearranged_chart))
        self.assertNotEqual(original_chart, rearranged_chart)
