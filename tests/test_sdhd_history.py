from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from zipfile import ZipFile

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
from build_jp_hdd_history import Observation, save_windows
from sdhd_scan import File, Layer, discover, layer_paths, ordered_layers, parse_record
from sdhd_notices import add_official_notices, merge_availability
from old_hdd_timeline import batch, event
from _unibot_history.old_hdd_history import OldHddHistory


def fixture(db):
    db.row_factory = sqlite3.Row
    for filename in ('old_hdd_schema.sql', 'old_hdd_timeline.sql', 'sdhd_schema.sql'):
        db.executescript((TOOLS / filename).read_text(encoding='utf-8-sig'))


class SdhdTests(unittest.TestCase):
    def test_discovery_uses_nested_extraction_and_prunes_custom_packages(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            edition = root / 'SDHD - CHUNITHM LUMINOUS'
            base = edition / 'HDD/SDHD_2.20.00_20231020095843_0/data/A000'
            base.mkdir(parents=True)
            container = edition / 'option/option(A001~A181)'
            package = container / 'option/A001'
            custom = package / 'music/自制包'
            custom.mkdir(parents=True)
            (custom / 'Music.xml').write_bytes(b'custom')
            (package / 'data.conf').write_bytes(b'official')
            container.with_suffix('.zip').write_bytes(b'not read')
            (edition / 'option/自制包/option/A002').mkdir(parents=True)

            self.assertEqual([(row.package, row.path) for row in discover(root, 20)],
                             [('A000', base), ('A001', package)])
            self.assertEqual(list(layer_paths(package)), [package / 'data.conf'])

    def test_same_revision_orders_by_build_and_rejects_undated_competing_music(self):
        with TemporaryDirectory() as tmp, closing(sqlite3.connect(':memory:')) as db:
            fixture(db)
            root = Path(tmp)
            path = 'music/music0042/Music.xml'
            older = root / 'old'
            newer = root / 'new'
            older.write_bytes(b'old')
            newer.write_bytes(b'new')
            revision, day = (2, 10, 12), '2023-02-17'
            rows = [
                (Layer('A150', newer, '20230224101302'), revision, {path: File('new', newer)}, day),
                (Layer('A151', older, '20230217174024'), revision, {path: File('old', older)}, day),
            ]
            self.assertEqual([row[0].package for row in ordered_layers(db, None, rows)], ['A151', 'A150'])
            tied = [(Layer(layer.package, layer.path), rev, files, low) for layer, rev, files, low in rows]
            with self.assertRaisesRegex(ValueError, 'unresolved music overlay'):
                ordered_layers(db, None, tied)
            second = tied[1]
            disjoint = [tied[0], (second[0], revision, {'music/music0043/Music.xml': File('old', older)}, day)]
            self.assertEqual(len(ordered_layers(db, None, disjoint)), 2)

    def test_official_release_can_predate_local_copy_without_inventing_payload(self):
        with TemporaryDirectory() as tmp, closing(sqlite3.connect(':memory:')) as db:
            fixture(db)
            calendar = [('NEW', '2021-11-04', '2022-04-13')]
            db.execute("INSERT INTO versions VALUES ('NEW',13,'SDHD','2021-11-04','2022-04-13','','','')")
            db.execute("INSERT INTO history_entities VALUES ('a','song',NULL,42,NULL,NULL)")
            db.execute("INSERT INTO history_revisions VALUES ('revision','a','{}')")
            db.execute("INSERT INTO sources VALUES ('src','SDHD','A000',10,NULL,'music_xml',NULL)")
            batch(db, 'pkg', 'NEW', 'package', 'fixture', bounds=('2022-01-10', '2022-04-13'))
            row = Observation('a', 42, 'song', None, None, None, 'src', {'title': 'Track'}, True,
                              '2022-01-10', 'pkg', 'revision')
            histories = SimpleNamespace(first={'a': row}, traces={'NEW': ({'a': [row]}, {})})
            notice = {'url': 'https://x.com/chunithm/status/123', 'published': '2022-01-05',
                      'title': 'Release', 'sha256': 'a' * 64, 'channel': 'official_x_embed',
                      'claims': [{'day': '2022-01-06', 'identity': 'a', 'batch': 'pkg', 'kind': 'first',
                                  'mode': 'map', 'music_id': 42, 'title': 'Track', 'locator': 'Card',
                                  'detail': 'Map access'}]}
            manifest = Path(tmp) / 'notices.json'
            manifest.write_text(json.dumps({'notices': [notice]}), encoding='utf-8')
            first, revisions, availability, designer = add_official_notices(db, histories, calendar, manifest)
            self.assertEqual(first['a'][0], '2022-01-06')
            self.assertEqual((revisions, dict(availability), designer), ({}, {}, None))
            saved = db.execute('''SELECT n.published_on,c.effective_on,c.mode FROM sdhd_notice n
                                  JOIN sdhd_notice_claim c ON c.announcement=n.url''').fetchone()
            self.assertEqual(tuple(saved), ('2022-01-05', '2022-01-06', 'map'))
            save_windows(db, 'NEW', 'a', [row], first['a'][0], *calendar[0][1:],
                         None, {}, official_first=True)
            early = db.execute("SELECT availability,revision FROM history_windows WHERE start_on='2022-01-06'").fetchone()
            self.assertEqual(tuple(early), ('present', None))

    def test_superseded_removal_keeps_source_but_is_not_a_dated_candidate(self):
        with closing(sqlite3.connect(':memory:')) as db:
            fixture(db)
            db.execute("INSERT INTO history_entities VALUES ('a','song',NULL,42,NULL,NULL)")
            batch(db, 'day:2024-07-19', None, 'public_day', 'archive', day='2024-07-19')
            event(db, 'a', 'day:2024-07-19', 'removed', 'availability', None, 'removed',
                  None, None, 'Archive row', day='2024-07-19')
            archived = {'a': {'2024-07-19': 'removed'}}
            merge_availability(db, archived, {'a': {'2024-07-18': 'removed'}})
            self.assertEqual(archived, {'a': {'2024-07-18': 'removed'}})
            row = db.execute('SELECT kind,effective_on,earliest_on,latest_on,evidence FROM history_events').fetchone()
            self.assertEqual(tuple(row)[:4], ('superseded_availability_candidate', None, None, None))
            self.assertIn('Archive row', row['evidence'])
            self.assertIn('2024-07-19', row['evidence'])

    def test_ultima_flag_and_notation_overlay(self):
        xml = b"""<MusicData><name><id>42</id><str>Music</str></name>
        <artistName><str>Artist</str></artistName><exType>0</exType>
        <disableFlag>false</disableFlag><enableUltima>false</enableUltima>
        <fumens><MusicFumenData><type><id>4</id><data>ULTIMA</data></type>
        <enable>true</enable><file><path>Ult.c2s</path></file>
        <level>13</level><levelDecimal>50</levelDecimal></MusicFumenData></fumens></MusicData>"""
        notes = b'VERSION\t1\nCREATOR\tArtist\nTAP\t1\t0\t0\t4'
        with TemporaryDirectory() as tmp, closing(sqlite3.connect(':memory:')) as db:
            fixture(db)
            p = Path(tmp)
            (p / 'Music.xml').write_bytes(xml)
            (p / 'Ult.c2s').write_bytes(notes)
            files = {key: (File(path, p / path), 'SDHD_2.00', package)
                     for key, path, package in [('music.xml', 'Music.xml', 'A000'), ('ult.c2s', 'Ult.c2s', 'A001')]}
            _, _, charts = parse_record(db, None, files, 'music.xml', 50)
            shape, data = charts[0]
            self.assertEqual((shape.slot, shape.difficulty), (4, 'ULT'))
            self.assertFalse(data['enabled'])
            self.assertEqual(data['display_level'], '13+')
            self.assertEqual(data['hdd_hundredths'], 1350)
            self.assertEqual(data['notes_designer'], 'Artist')
            self.assertEqual(data['c2s_sha256'], sha256(notes).hexdigest())

    def test_uncertain_end_revision_and_confirmed_removal(self):
        with closing(sqlite3.connect(':memory:')) as db:
            fixture(db)
            db.execute("INSERT INTO versions VALUES ('NEW',13,'SDHD','2021-11-04','2022-04-13','','','')")
            db.execute("INSERT INTO history_entities VALUES ('a','song',NULL,42,NULL,NULL)")
            for revision in ('original', 'changed'):
                db.execute("INSERT INTO history_revisions VALUES (?,'a','{}')", (revision,))
            trace = [Observation('a', 42, 'song', None, None, None, 'src', {}, True, day, 'pkg', key)
                     for day, key in [('2021-11-04', 'original'), ('2022-01-01', 'changed')]]
            save_windows(db, 'NEW', 'a', trace, '2021-11-05', '2021-11-04', '2022-04-13',
                         None, {'2022-02-01': 'removed', '2022-03-01': 'restored'})
            self.assertIsNone(db.execute("SELECT revision FROM history_windows WHERE end_on='2022-04-13'").fetchone()[0])
            self.assertIsNone(db.execute("""SELECT * FROM history_windows
                WHERE '2022-02-15' BETWEEN start_on AND end_on""").fetchone())
            self.assertEqual(db.execute("SELECT min(start_on) FROM history_windows").fetchone()[0], '2021-11-05')

    def test_indexed_zip_resource_requires_matching_hash(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / 'option.zip'
            with ZipFile(p, 'w') as archive:
                archive.writestr('option/A001/test.dds', b'abc')
            pointer = 'option.zip!/option/A001/test.dds'
            self.assertEqual(OldHddHistory.resource_bytes(tmp, pointer, sha256(b'abc').hexdigest()), b'abc')
            base = Path(tmp) / 'NEW!!'
            base.mkdir()
            (base / 'test.dds').write_bytes(b'abc')
            self.assertEqual(OldHddHistory.resource_bytes(tmp, 'NEW!!/test.dds', sha256(b'abc').hexdigest()), b'abc')
            with self.assertRaisesRegex(ValueError, 'archived SHA-256'):
                OldHddHistory.resource_bytes(tmp, pointer, sha256(b'changed').hexdigest())


if __name__ == '__main__':
    unittest.main()
