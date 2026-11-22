import asyncio
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))

from apply_hdd_notice_reviews import normal_launch_witnesses
from build_jp_wiki_dates import (
    SCHEMA, adopt, apply_reviewed_dates, choose_release, collect, detail_targets, import_pages,
    propagate, selected_release, song_index,
)
from jp_wiki_dates import DetailHTML, page_url, parse


class WikiParserTests(unittest.TestCase):
    def test_context_and_date_resolution(self):
        samples = [
            ('-2018/6/21追加、マップ5課題曲。', [('2018-06-21', 'first_release', None)]),
            ('|~解禁方法|2018/12/6 通常解禁|', [('2018-12-06', 'general_unlock', None)]),
            ('-2021/1/21～3/3イベント「コラボ」先行配信曲。',
             [('2021-01-21', 'advance_release', None), (None, 'period_end', None)]),
            ('-2026/3/19 ULTIMA追加 (Secret AREA解禁→2026/7/2 通常配信)',
             [('2026-03-19', 'first_release', 'ULT'), ('2026-07-02', 'general_unlock', 'ULT')]),
            ('-2020/4/1 WORLD’S END【狂】 譜面追加。', [('2020-04-01', 'first_release', 'WE')]),
            ('-10月3日新曲追加。', [(None, 'first_release', None)]),
            ('-2020/4/1 イベント開催。', [('2020-04-01', 'event_open', None)]),
            ('-2020/4/1 配信停止。', [('2020-04-01', 'removed', None)]),
            ('-2020/4/1 再配信。', [('2020-04-01', 'restored', None)]),
            ('-2020/4/1 再配信として追加。', [('2020-04-01', 'restored', None)]),
            ('-2020/4/1 マップにて復活 (先行解禁)。', [('2020-04-01', 'restored', None)]),
            ('-2020/4/1 海外版先行配信。', []),
            ('-2020/4/1 中国版で先行解禁。', []),
            ('-2020/4/1追加、「maimai でらっくす」エリア9課題曲。', []),
            ('なお、先行配信曲用の背景は2024/2/1にも差し替えられなかった。', []),
            ('|~追加日|2025/6/3|', []),
        ]
        for line, expected in samples:
            with self.subTest(line=line):
                _, _, records = parse({'1': '*情報 [#summary]', '2': line})
                self.assertEqual([(r['day'], r['kind'], r['difficulty']) for r in records], expected)

    def test_html_extracts_metadata_and_earlier_history(self):
        reader = DetailHTML()
        reader.feed("""<div id="body"><table><tr><th>タイトル</th><td>Song</td></tr>
          <tr><th>アーティスト</th><td>Artist</td></tr>
          <tr><th>解禁方法</th><td>2018/12/6 通常配信</td></tr></table>
          <h2>情報</h2><ul><li>2018/6/21追加、マップ5課題曲。</li></ul></div>
          <div>2017/1/1追加</div>""")
        title, artist, records = parse(dict(enumerate(reader.body)))
        self.assertEqual((title, artist), ('Song', 'Artist'))
        self.assertEqual([r['day'] for r in records], ['2018-12-06', '2018-06-21'])


class WikiDateAuditTests(unittest.TestCase):
    def setUp(self):
        self.db = self.enterContext(closing(sqlite3.connect(':memory:')))
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE versions(version TEXT PRIMARY KEY,ordinal,start_on,end_on);
            INSERT INTO versions VALUES ('OLD',12,'2021-01-01','2021-12-31');
            INSERT INTO versions VALUES ('NEW',13,'2022-01-01','2022-12-31');
            CREATE TABLE sources(path TEXT PRIMARY KEY,sha256,kind);
            INSERT INTO sources VALUES ('witness','123','music_xml'),('chart','456','music_xml');
            CREATE TABLE sdhd_first_release(identity TEXT PRIMARY KEY,earliest_on,latest_on,exact_on,basis);
            CREATE TABLE sdhd_release_evidence(identity,day,locator,reference,PRIMARY KEY(identity,day,locator));
            CREATE TABLE worlds_end_versions(chart_identity,identity,title,tag,stars);
        ''')
        self.db.executescript((TOOLS / 'old_hdd_timeline.sql').read_text(encoding='utf-8'))
        self.db.executescript(SCHEMA)
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executemany('INSERT INTO history_batches VALUES (?,?,?,?,?,?,?,?,?)', [
            ('song-pack', 'NEW', 'package', None, '2022-01-10', '2022-12-31', 'range', 3, ''),
            ('chart-pack', 'NEW', 'package', None, '2022-01-18', '2022-12-31', 'range', 4, ''),
            ('old-pack', 'OLD', 'package', None, '2021-01-10', '2021-12-31', 'range', 1, ''),
        ])
        self.identity('song', title='Song')

    def identity(self, key, *, parent=None, difficulty=None, title=None, artist='Artist',
                 version_pack='song-pack', exact=None, basis='disk_presence_only', kind=None):
        kind = kind or ('chart' if difficulty else 'song')
        slot = ('BAS','ADV','EXP','MAS','ULT','WE').index(difficulty) if difficulty else None
        self.db.execute('INSERT INTO history_entities VALUES (?,?,?,?,?,?)', (key, kind, parent, 1, slot, difficulty))
        payload = {'title': title, 'artist': artist, 'exType': '0'} if title else {'c2s_sha256': 'x'}
        self.db.execute('INSERT INTO history_revisions VALUES (?,?,?)', (key, key, json.dumps(payload)))
        self.db.execute('INSERT INTO history_observations VALUES (?,?,?,?,?,?)',
                        (version_pack, key, key, 'chart' if parent else 'witness', 'hdd_enabled', 'fixture'))
        self.db.execute('INSERT INTO sdhd_first_release VALUES (?,?,?,?,?)',
                        (key, exact or '2022-01-10', exact, exact, basis))

    def clauses(self, *lines):
        _, _, records = parse(dict(enumerate(('*情報', *lines))))
        return [r | {'url': page_url('Song'), 'status': 'candidate'} for r in records]

    def test_official_agrees_and_conflict_remains_reviewable(self):
        self.db.execute("UPDATE sdhd_first_release SET exact_on='2022-01-20',basis='official_release_date'")
        for day, status in [('2022-01-20', 'agrees_official'), ('2022-01-21', 'conflict_existing_date')]:
            with self.subTest(day=day):
                rows = self.clauses(f'-{day}追加。')
                choose_release(self.db, 'song', rows, {'first_source': 'witness'})
                self.assertEqual(rows[0]['status'], status)
                self.assertEqual(selected_release(self.db, 'song')[0], '2022-01-20')

    def test_same_title_different_artist_and_same_artist_collision(self):
        self.identity('other', title='Song', artist='Different')
        index = song_index(self.db)
        self.assertEqual(detail_targets(self.db, index, 'Song', 'Artist', None, ''), {'song'})
        self.identity('collision', title='Song')
        self.assertEqual(detail_targets(self.db, song_index(self.db), 'Song', 'Artist', None, ''), {'song', 'collision'})

    def test_conflicting_page_does_not_pick_an_exact_date(self):
        rows = self.clauses('-2022/1/20追加。', '-2022/1/27追加。')
        choose_release(self.db, 'song', rows, {'first_source': 'witness'})
        self.assertEqual({r['status'] for r in rows}, {'conflicting_wiki_dates'})
        self.assertIsNone(selected_release(self.db, 'song')[0])

    def test_direct_first_release_overrides_weak_date_and_retains_wiki_conflict(self):
        adopt(self.db, 'song', '2022-03-20', 'raw', 'dated_release_evidence', 'witness')
        common = {'title': 'Song', 'artist': 'Artist', 'kind': 'first_release', 'raw': 'Song 追加'}
        claims = [
            common | {'day': '2022-02-01', 'url': page_url('Song')},
            common | {'day': '2022-01-20', 'url': 'https://info-chunithm.sega.jp/1/'},
        ]
        result = apply_reviewed_dates(self.db, claims, {'song': {'first_source': 'witness'}})
        self.assertEqual(result, {'official_corrected': 1, 'wiki_conflicting_reviewed_dates': 1})
        self.assertEqual(selected_release(self.db, 'song')[0], '2022-01-20')
        rows = self.db.execute('SELECT status,evidence FROM jp_date_review ORDER BY day').fetchall()
        self.assertEqual(rows[0]['status'], 'corrected_official_date')
        self.assertEqual(json.loads(rows[0]['evidence'])['old_day'], '2022-03-20')
        self.assertEqual(json.loads(rows[0]['evidence'])['raw'], 'Song 追加')
        self.assertEqual(rows[1]['status'], 'conflicting_reviewed_dates')

    def test_page_evidence_reimport_does_not_duplicate_page_or_unmatched_rows(self):
        page = {'url': page_url('Song'), 'title': 'Song', 'obtained_at': '2026-11-22',
                'lines': {'0': '|~タイトル|Song|', '1': '|~アーティスト|Artist|',
                          '2': '*情報', '3': '-2022/1/20 ULTIMA追加。'}}
        for _ in range(2):
            _, stats = import_pages(self.db, [page], [])
            self.assertEqual(stats['unmatched_dated_clauses'], 1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM jp_wiki_pages').fetchone()[0], 1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM jp_wiki_unmatched').fetchone()[0], 1)

    def test_unlock_and_restoration_do_not_move_launch(self):
        self.identity('other', title='Other')
        rows = self.clauses('|~解禁方法|2022/3/20 通常配信|', '-2022/1/20追加、マップ曲。',
                            '-2022/4/1 配信停止。', '-2022/5/1 再配信。')
        choose_release(self.db, 'song', rows, {'first_source': 'witness'})
        self.assertEqual(selected_release(self.db, 'song')[0], '2022-01-20')
        self.assertEqual(rows[0]['kind'], 'general_unlock')
        self.assertIsNone(selected_release(self.db, 'other')[0])

    def test_correction_preserves_original_claim(self):
        self.assertEqual(adopt(self.db, 'song', '2022-03-20', 'raw', 'dated_release_evidence', 'witness'), 'adopted')
        rows = self.clauses('|~解禁方法|2022/3/20 通常配信|', '-2022/1/20追加、マップ曲。')
        choose_release(self.db, 'song', rows, {'first_source': 'witness'})
        self.assertEqual(selected_release(self.db, 'song')[0], '2022-01-20')
        self.assertEqual(self.db.execute("SELECT effective_on FROM history_events WHERE kind='superseded_release_claim'").fetchone()[0],
                         '2022-03-20')
        self.assertEqual(self.db.execute("SELECT day FROM sdhd_release_evidence WHERE reference='raw'").fetchone()[0], '2022-03-20')

    def test_propagation_is_route_independent_and_preserves_chart_identity(self):
        for suffix, mode in enumerate(('standard', 'map', 'event')):
            parent = f'song-{suffix}'
            self.identity(parent, title=parent, version_pack='old-pack' if suffix == 0 else 'song-pack')
            day = '2021-12-21' if suffix == 0 else f'2022-01-2{suffix}'
            adopt(self.db, parent, day, mode, 'official_release_date', 'witness')
            for difficulty in ('BAS','ADV','EXP','MAS','ULT','WE'):
                self.identity(parent+difficulty, parent=parent, difficulty=difficulty,
                              version_pack='old-pack' if suffix == 0 else 'chart-pack')
            self.assertEqual(len(normal_launch_witnesses(self.db, parent, day)), 4)
        parents = {row['identity']: row for row in self.db.execute("SELECT *,exact_on FROM history_entities JOIN sdhd_first_release USING(identity)")}
        self.assertEqual(propagate(self.db, parents)['adopted'], 12)
        self.assertEqual(propagate(self.db, parents)['adopted'], 0)
        self.assertEqual(selected_release(self.db, 'song-0MAS')[0], '2021-12-21')
        self.assertEqual(selected_release(self.db, 'song-1MAS')[0], '2022-01-21')
        self.assertEqual(selected_release(self.db, 'song-2MAS')[0], '2022-01-22')
        self.assertIsNone(selected_release(self.db, 'song-0ULT')[0])
        self.assertIsNone(selected_release(self.db, 'song-1WE')[0])
        self.assertEqual(self.db.execute("SELECT payload FROM history_revisions WHERE revision='song-1MAS'").fetchone()[0],
                         '{"c2s_sha256": "x"}')

    def test_preactivation_window_and_independent_ultima(self):
        self.db.execute("INSERT INTO history_windows VALUES ('NEW','song','2022-01-10','2022-12-31','possible',NULL,'[]','hdd')")
        adopt(self.db, 'song', '2022-02-03', 'notice', 'official_release_date', 'witness')
        self.identity('ultima', parent='song', difficulty='ULT', version_pack='chart-pack')
        adopt(self.db, 'ultima', '2022-05-01', 'wiki', 'wikiwiki_release_with_hdd_identity', 'chart')
        window = self.db.execute("SELECT start_on,availability FROM history_windows WHERE identity='song'").fetchone()
        self.assertEqual(tuple(window), ('2022-02-03','possible'))
        self.assertEqual(selected_release(self.db, 'ultima')[0], '2022-05-01')

    def test_cache_hit_does_not_open_network_client(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            catalog = root / 'catalog.sqlite'
            with closing(sqlite3.connect(catalog)) as db:
                self.db.commit()
                self.db.backup(db)
            cache = {'pages': [{'url': page_url('Song')}]}
            with patch('httpx.AsyncClient') as network:
                result = asyncio.run(collect(catalog, root/'cache.json', cache))
                network.assert_not_called()
            self.assertEqual(result, cache)


if __name__ == '__main__':
    unittest.main()
