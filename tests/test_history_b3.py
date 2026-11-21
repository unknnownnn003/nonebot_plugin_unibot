from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest


TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
try:
    from compare_history_third_party import aqua_readings, sql_readings
    from history_music_xml import diff_observations, observation, parse_music_xml
finally:
    sys.path.remove(str(TOOLS))


FIXTURES = Path(__file__).with_name('fixtures') / 'history_music_xml'


class MusicXmlTests(unittest.TestCase):
    def test_dialect_slot_and_hundredths(self):
        before = parse_music_xml((FIXTURES / 'legacy.xml').read_bytes(), profile='legacy')
        after = parse_music_xml((FIXTURES / 'chusan.xml').read_bytes(), profile='chusan')
        self.assertEqual(before['charts'][1].constant, Decimal('10.40'))
        self.assertEqual(after['charts'][1].constant, Decimal('10.75'))
        self.assertIsNone(before['charts'][2].constant)
        self.assertIsNone(after['charts'][2].constant)
        self.assertEqual((before['charts'][3].difficulty, after['charts'][3].difficulty), ('WE', 'ULT'))

    def test_claims_stay_distinct_from_release_tag_and_diff(self):
        opts = dict(captured_at='2026-11-21T12:00:00+09:00', effective_date='2026-11-21')
        before = observation(FIXTURES / 'legacy.xml', profile='legacy', game_version='CRYSTAL', **opts)
        after = observation(FIXTURES / 'chusan.xml', profile='chusan', game_version='MATE', **opts)
        self.assertEqual(before['release_tag'], 'Example debut')
        self.assertEqual(before['claimed_game_version'], 'CRYSTAL')
        self.assertEqual(before['official_playability'], 'unverified')
        self.assertEqual(before['charts'][0]['chart_id'], '59001:0')
        self.assertNotEqual(before['sha256'], after['sha256'])
        changes = diff_observations(before, after)['changes']
        self.assertEqual(changes[0]['before']['constant'], Decimal('10.40'))
        self.assertEqual(changes[0]['after']['constant'], Decimal('10.75'))

    def test_reject_doctype_in_unicode_and_duplicate_slot(self):
        with self.assertRaisesRegex(ValueError, 'DTD'):
            parse_music_xml('<?xml version="1.0" encoding="utf-16"?><!DOCTYPE MusicData><MusicData/>'.encode('utf-16'))
        data = (FIXTURES / 'legacy.xml').read_bytes().replace(b'<id>4</id>', b'<id>3</id>')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            parse_music_xml(data)


class PublicMetadataAdapterTests(unittest.TestCase):
    def test_literal_sql_preserves_metadata_and_disable(self):
        sql = b"""INSERT INTO `chuni_music` (`music_id`, `name`, `artist_name`, `release_version`)
        VALUES (7, 'A, (B)', 'A\\'s Artist', 'v1'), (8, 'Other', 'Artist', 'v1');
        INSERT INTO chuni_music_level (music_id, diff, level, level_decimal, enable)
        VALUES (7, 3, 11, 20, b'1'), (7, 4, 11, 80, true), (8, 2, 10, 20, false);"""
        rows, count, songs = sql_readings({'demo':sql}, ['demo'], ['demo'], True)
        row, = rows
        self.assertEqual((row.title,row.artist,row.constant,row.difficulty), ('A, (B)', "A's Artist", Decimal('11.20'), 'MAS'))
        self.assertEqual((count, songs, row.difficulty_basis), (2, 2, 'explicit_sql_diff'))

    def test_aquanet_omits_we_and_requires_contiguous_shape(self):
        normal = {'name':'Example','composer':'Artist','ver':'v1','worldsEndTag':'Invalid','notes':[{'lv':3},{'lv':6},{'lv':10.4},{'lv':12.3}]}
        payload = {'1':normal, '2':dict(normal, worldsEndTag='招'), '3':dict(normal, notes=normal['notes'][:3])}
        rows, count = aqua_readings(json.dumps(payload).encode(), 'demo')
        self.assertEqual((len(rows),count), (4,7))
        self.assertEqual(rows[2].constant, Decimal('10.4'))
        self.assertEqual(rows[2].difficulty_basis, 'inferred_array_position')


if __name__ == '__main__':
    unittest.main()
