import argparse
from contextlib import closing
from hashlib import sha256
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from build_old_hdd_history import build, write_ordinary
from old_hdd_evidence import PublicEvidence
from old_hdd_scan import HDD_NAMES, STARS, c2s_creator, c2s_notation_hash, display_level, parse_music, raw_constant
from _unibot_history.old_hdd_history import OldHddHistory


def fumen(slot, label, name='', level=0, fraction=0, enabled=False):
    return f'''<MusicFumenData><type><id>{slot}</id><data>{label}</data></type>
<enable>{str(enabled).lower()}</enable><level>{level}</level><levelDecimal>{fraction}</levelDecimal>
<file><path>{name}</path></file></MusicFumenData>'''


def music(layer, music_id, *, title='Twin', artist='One', we=False, constant=1270, disabled=False,
          release='v1 1.00.00', c2s=b'chart'):
    path = layer / 'music' / f'music{music_id:04}'
    path.mkdir(parents=True)
    (path / 'exact.c2s').write_bytes(c2s)
    (path / 'different-id.dds').write_bytes(b'DDS ' + bytes(8) + b'\x08\0\0\0\x10\0\0\0' + bytes(108))
    rows = fumen(4, "WORLD'S END", 'exact.c2s', 0, 0, True) if we else fumen(
        3, 'MASTER', 'exact.c2s', *divmod(constant, 100), True)
    # The initial HDD can have a repeated type ID on an empty, disabled slot.
    rows += fumen(3, 'MASTER')
    body = f'''<MusicData><name><id>{music_id}</id><str>{title}</str></name>
<artistName><str>{artist}</str></artistName><exType>{2 if we else 0}</exType>
<releaseTagName><str>{release}</str></releaseTagName><disableFlag>{str(disabled).lower()}</disableFlag>
<worldsEndTagName><id>42</id><str>！</str></worldsEndTagName><starDifType>7</starDifType>
<jaketFile><path>different-id.dds</path></jaketFile><fumens>{rows}</fumens></MusicData>'''
    (path / 'Music.xml').write_text(body, encoding='utf-8')


class OldHddTests(unittest.TestCase):
    def test_scale(self):
        self.assertEqual(raw_constant('12', '70'), 1270)
        self.assertEqual(display_level(1270), '12+')
        self.assertEqual(display_level(1260), '12')
        self.assertEqual(display_level(670), '6')
        for level, frac in [('0', '0'), ('12', '-1'), ('bad', '0'), ('12', '100'), ('12', None)]:
            self.assertIsNone(raw_constant(level, frac))
        self.assertEqual(STARS['7'], 4)
        self.assertIsNone(STARS.get('8'))

    def test_c2s_notes_and_timing_are_distinct_from_labels_and_statistics(self):
        before = b'VERSION\t1\r\nCREATOR\tOne\r\nTAP\t1\t0\t0\t4\r\nT_REC_TAP\t1'
        after = b'VERSION\t2\nCREATOR\tTwo\nTAP\t1\t0\t0\t4\nT_REC_TAP\t2'
        self.assertIsNotNone(c2s_notation_hash(before))
        self.assertEqual(c2s_creator(before), 'One')
        self.assertEqual(c2s_creator(after), 'Two')
        self.assertEqual(c2s_notation_hash(before), c2s_notation_hash(after))
        self.assertNotEqual(c2s_notation_hash(before), c2s_notation_hash(after.replace(b'\t0\t4', b'\t1\t4')))
        self.assertIsNone(c2s_notation_hash(b'unknown format'))

    def test_wiki_we_variant_removal(self):
        with closing(sqlite3.connect(':memory:')) as db:
            evidence = PublicEvidence(None, db)
            evidence._page('wiki-lost', [(1, ['', 'Twin', 'Artist', '！', '☆4'], 'locus')])
            self.assertEqual(evidence.removal('twin', '!', 4), ('2021-05-13', 'locus'))
            self.assertEqual(evidence.removal('twin', '!', 2), (None, None))

    def test_hdd_authority_retains_raw_value(self):
        with TemporaryDirectory() as tmp, closing(sqlite3.connect(':memory:')) as db:
            root = Path(tmp)
            music(root, 96, title='チルノのパーフェクトさんすう教室', constant=1190)
            relative = 'music/music0096/Music.xml'
            contents = (root / relative).read_bytes()
            song = parse_music(contents, relative, 'A000', sha256(contents).hexdigest())
            db.executescript((ROOT / 'tools/old_hdd_schema.sql').read_text(encoding='utf-8-sig'))
            db.execute('PRAGMA foreign_keys=OFF')
            public = PublicEvidence(None, db)
            write_ordinary(db, 'PLUS', '96', '96:3', song, song.charts[0], 'present', 'hdd', public,
                           {}, {})
            numeric = db.execute('SELECT hdd_hundredths,selected_hundredths,constant_status FROM chart_versions')
            self.assertEqual(numeric.fetchone(), (1190, 1190, 'hdd_observed'))

    def test_missing_directory_refused_before_output(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = argparse.Namespace(root=root, output=root / 'unused-hdd-output', evidence=None, b5=None, hash_all=False)
            with self.assertRaises(FileNotFoundError):
                build(args)

    def test_full_import_reproducibility(self):
        with TemporaryDirectory() as tmp:
            private = Path(tmp)
            root = private / 'input'
            for i, hdd in enumerate(HDD_NAMES):
                music(root / hdd / 'data/A000', 1, constant=1270 if i == 0 else 1290,
                      c2s=b'revision' if i >= 2 else b'original')
            music(root / HDD_NAMES[0] / 'data/A000', 110, title='Twin', artist='Other')
            music(root / HDD_NAMES[0] / 'data/A000', 40, title='Dated')
            music(root / HDD_NAMES[0] / 'data/A000', 41, title='Episodic')
            music(root / HDD_NAMES[2] / 'data/A000', 41, title='Episodic')
            music(root / HDD_NAMES[3] / 'data/A000', 110, title='Reused', artist='Different', c2s=b'other')
            first_option = root / HDD_NAMES[0] / 'bin/option/A009'
            music(first_option, 1, constant=1280, c2s=b'original')
            (first_option / 'data.conf').write_text('VerMajor = 1\nVerMinor = 0\nVerRelease = 2', encoding='utf8')
            second_option = root / HDD_NAMES[0] / 'bin/option/A004'
            music(second_option, 1, constant=1285, c2s=b'original')
            (second_option / 'data.conf').write_text('VerMajor = 1\nVerMinor = 0\nVerRelease = 3', encoding='utf8')
            layers = (root / HDD_NAMES[0] / 'data/A000', first_option)
            for enabled, layer in enumerate(layers):
                event = layer / 'event/event11/Event.xml'
                event.parent.mkdir(parents=True)
                event.write_text(f"""<EventData><name><id>11</id><str>Music opening</str></name>
                    <alwaysOpen>{str(bool(enabled)).lower()}</alwaysOpen><substances><type>3</type>
                    <music><musicType>0</musicType><musicNames><list><StringID><id>40</id>
                    <str>Dated</str></StringID></list></musicNames></music></substances></EventData>""",
                                 encoding='utf-8')
            disk = root / HDD_NAMES[-1]
            music(disk / 'data/A000', 8001, we=True)
            music(disk / 'data/A000', 8002, we=True)
            music(disk / 'bin/option/A032', 200, title='Lost Only', release='v1 1.55.00')
            music(disk / 'bin/option/A033', 201, title='Unknown Later')
            music(disk / 'bin/option/A910', 7000, title='Custom')
            music(disk / 'bin/option/A114', 7005, title='Restored in patch')
            evidence = private / 'evidence'
            evidence.mkdir()
            (evidence / 'wiki-dates-01.html').write_text(
                '<div id="content"><table><tr><th>2015/08/13 追加配信曲</th></tr>'
                '<tr><td>VARIETY</td><td>Dated</td><td>One</td><td>1</td><td>2</td><td>3</td><td>12+</td>'
                '</tr><tr><td>VARIETY</td><td>Episodic</td><td>One</td><td>1</td><td>2</td><td>3</td><td>12+</td>'
                '</tr></table></div>', encoding='utf-8')
            (evidence / 'wiki-deleted.html').write_text(
                '<div id="content"><table><tr><th>タイトル</th><th>アーティスト</th><th>BPM</th>'
                '<th>B</th><th>A</th><th>E</th><th>M</th></tr>'
                '<tr><th>▼ PLUS まで (～2016/2/4)</th></tr>'
                '<tr><td>Episodic</td><td>One</td><td>120</td><td>1</td><td>2</td><td>3</td><td>12+</td></tr>'
                '</table><table><tr><th>タイトル</th><th>アーティスト</th><th>BPM</th>'
                '<th>前配信停止ver.</th><th>復活日付</th></tr>'
                '<tr><td>Episodic</td><td>One</td><td>120</td><td>PLUS</td><td>2016/9/1</td></tr>'
                '</table></div>', encoding='utf-8')
            outputs = [private / name for name in ('first', 'second')]
            for output in outputs:
                build(argparse.Namespace(root=root, output=output, evidence=evidence, hash_all=True))
            self.assertEqual((outputs[0] / 'SHA256SUMS').read_bytes(), (outputs[1] / 'SHA256SUMS').read_bytes())
            reader = OldHddHistory(outputs[0] / 'catalog.sqlite')
            self.assertEqual(len(reader.versions()), 12)
            self.assertEqual(len({v['hdd'] for v in reader.versions()}), 11)
            before = reader.chart('CHUNITHM', 1, 'MAS')
            self.assertEqual(before['selected_hundredths'], 1285)
            self.assertEqual(before['package'], 'A004')
            self.assertEqual(reader.chart('CHUNITHM', 1, 'EXP')['state'], 'not_observed')
            self.assertEqual(len(reader.songs('CHUNITHM')), 4)
            self.assertEqual(len({r['identity'] for r in reader.song_history(110)}), 2)
            chart = reader.chart('PARADISE', 200, 'MAS')
            self.assertEqual(chart['state'], 'not_released')
            self.assertIsNone(chart['selected_hundredths'])
            self.assertEqual(reader.chart('PARADISE LOST', 201, 'MAS')['state'], 'unknown')
            self.assertEqual(reader.chart('PARADISE LOST', 7000, 'MAS')['state'], 'not_observed')
            self.assertEqual(reader.chart('PARADISE LOST', 7005, 'MAS')['state'], 'not_observed')
            we = reader.worlds_end('PARADISE LOST')
            self.assertEqual([r['stars'] for r in we], [4, 4])
            self.assertEqual([r['tag'] for r in we], ['！', '！'])
            self.assertEqual(len({r['chart_identity'] for r in we}), 2)
            self.assertTrue(all('selected_hundredths' not in r for r in we))
            jacket = reader.jackets('PARADISE LOST', 8001)[0]
            self.assertEqual(sha256(reader.jacket_path(root, jacket).read_bytes()).hexdigest(), jacket['sha256'])
            self.assertTrue(jacket['path'].endswith('different-id.dds'))
            events = reader.changes(before['chart_identity'])
            self.assertTrue(any(r['kind'] == 'constant_changed' and r['new_version'] == 'PLUS' for r in events))
            self.assertTrue(any(r['kind'] == 'chart_content_changed' and r['new_version'] == 'AIR' for r in events))
            self.assertFalse(any(r['kind'] == 'constant_changed' and r['new_version'] == 'AIR' for r in events))
            self.assertEqual(reader.catalog_on('2015-07-15')['coverage'], 'out_of_scope')
            self.assertFalse(any(row['music_id'] == 40 for row in
                                 reader.catalog_on('2015-08-12', include_possible=True)['rows']))
            self.assertEqual({row['kind'] for row in reader.catalog_on('2015-08-13')['rows']
                              if row['music_id'] == 40}, {'song','chart'})
            self.assertEqual(reader.batch_history('day:2015-08-13')['events'][0]['kind'], 'released')
            game_change = reader.batch_history(f'pkg:{HDD_NAMES[0]}:A009')['game_event_changes']
            self.assertIn(('always_open', 0, 1), {(row['field'],row['before'],row['after']) for row in game_change})
            self.assertEqual({row['always_open'] for row in reader.game_events(40)}, {0,1})
            self.assertEqual({r['identity'] for r in reader.checkpoint('CHUNITHM')},
                             {r['identity'] for r in reader.catalog_on('2016-02-03')['rows']})
            self.assertTrue(any(row['payload']['hdd_hundredths'] == 1280
                                for row in reader.lifecycle(before['chart_identity'])['observations']))
            self.assertEqual(len([r for r in reader.identities(8001) if r['kind'] == 'we']), 1)
            self.assertEqual(reader.lifecycle('0:110:1')['related'][0]['kind'], 'same_raw_id_distinct')
            for day in ('2016-03-01','2016-08-31'):
                self.assertFalse(any(row['music_id'] == 41 for row in reader.catalog_on(day)['rows']))
            self.assertEqual({row['kind'] for row in reader.catalog_on('2016-09-01')['rows']
                              if row['music_id'] == 41}, {'song','chart'})
            self.assertTrue(any(row['kind'] == 'removed' and row['effective_on'] == '2016-02-05'
                                for row in reader.lifecycle('0:41:1:3:MAS')['events']))
            with closing(sqlite3.connect(outputs[0] / 'catalog.sqlite')) as db:
                row = db.execute("""SELECT before_source,after_source FROM option_changes
                    WHERE hdd=? AND kind='observed_diff' AND field='MAS.constant' AND after_package='A004'""",
                                 (HDD_NAMES[0],)).fetchone()
                self.assertIsNotNone(row, db.execute('SELECT * FROM option_changes').fetchall())
                self.assertIn('/A009/', row[0])
                self.assertIn('/A004/', row[1])
                db.execute('DELETE FROM complete')
                db.commit()
            with self.assertRaisesRegex(ValueError, 'Incomplete'):
                OldHddHistory(outputs[0] / 'catalog.sqlite')


if __name__ == '__main__':
    unittest.main()
