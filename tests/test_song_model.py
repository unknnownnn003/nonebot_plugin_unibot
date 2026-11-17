from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import unittest

spec = importlib.util.spec_from_file_location('song_model_fixture', Path(__file__).resolve().parents[1]/'nonebot_plugin_unibot/services/song_model.py')
model = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = model
spec.loader.exec_module(model)


def fixture():
    return {'songs': [
        {'id': 10, 'title': 'Same title', 'artist': 'A', 'version': 10000, 'rights': 'rights',
         'difficulties': [dict(difficulty=3, level='14', level_value=14.2, version=10000),
             dict(difficulty=4, level='15', level_value=15.1, version=24000,
                  release_date='2026-11-01', notes={'total': 2000}, note_designer='fixture')]},
        {'id': 11, 'title': 'Same title', 'artist': 'A', 'version': 20000,
         'difficulties': [dict(difficulty=3, level='13', level_value=13.1, version=20000)]},
        {'id': 8001, 'title': 'Same title', 'version': 21000,
         'difficulties': [dict(difficulty=5, level='0', level_value=0, origin_id=10,
                              kanji='止', star=1, version=21000, notes={'total': 200})]},
        {'id': 8002, 'title': 'Different WE title', 'version': 23000,
         'difficulties': [dict(difficulty=5, level='0', level_value=0, origin_id=10,
                              kanji='狂', star=5, version=23000, release_date='2026-10-01')]},
        {'id': 8003, 'title': 'Same title', 'version': 23000,
         'difficulties': [dict(difficulty=5, level='0', level_value=0, origin_id=999, kanji='改', star=3, version=23000)]},
    ], 'versions': [dict(id=1, title='CHUNITHM OLD', version=10000), dict(id=2, title='CHUNITHM NEW', version=24000)], 'genres': [], 'upstream_extra': {'keep': True}}


class SongModelTests(unittest.TestCase):
    def test_identity_and_explicit_origin_only(self):
        catalog = model.Catalog(fixture())
        self.assertEqual(catalog.parent_id(8001), 10)
        self.assertEqual(catalog.parent_id(8002), 10)
        self.assertEqual(catalog.parent_id(11), 11)
        self.assertEqual(catalog.parent_id(8003), 8003)
        view = catalog.query_song(10)
        self.assertEqual([s['id'] for s in view['related_songs']], [8001, 8002])
        self.assertEqual([d['song_id'] for d in view['difficulties']], [10, 10, 8001, 8002])
        self.assertEqual(catalog.query_song(8001)['id'], 8001)
        self.assertEqual(catalog.query_song(8001)['parent_song_id'], 10)
        self.assertEqual(model.chart_owner(view, view['difficulties'][-1])['title'], 'Different WE title')

    def test_ultima_and_worldsend_properties_and_versions_survive(self):
        raw = fixture()
        original = deepcopy(raw)
        catalog = model.Catalog(raw)
        result = catalog.query_song(10)
        ult = result['difficulties'][1]
        self.assertEqual((ult['version'], ult['release_date'], ult['notes']['total']), (24000, '2026-11-01', 2000))
        ends = result['difficulties'][2:]
        self.assertEqual([(d['song_id'], d['origin_id'], d['kanji'], d['star'], d['version']) for d in ends],
                         [(8001, 10, '止', 1, 21000), (8002, 10, '狂', 5, 23000)])
        self.assertEqual(raw, original)
        self.assertEqual(catalog.as_dict()['upstream_extra'], {'keep': True})
        self.assertEqual(catalog.songs[8001].charts[0].jacket_id, 10)
        self.assertEqual(catalog.songs[10].charts[1].jacket_id, 10)

    def test_foreign_records_and_constants_are_read_only_legacy_projection(self):
        raw = fixture()
        raw['songs'][0]['difficulties'][0].update(level_value=15.4, lx_level_value=14.2, chunirec_level_value=15.4)
        raw['songs'].append(dict(id='hash-id', title='foreign', data_source='chunirec', difficulties=[]))
        original = deepcopy(raw)
        catalog = model.Catalog(raw, legacy=True)
        self.assertEqual(catalog.excluded, 1)
        self.assertEqual(catalog.songs[10].charts[0].attributes['level_value'], 14.2)
        self.assertNotIn('chunirec_level_value', catalog.songs[10].charts[0].attributes)
        self.assertEqual(raw, original)
        self.assertEqual(model.active_catalog(catalog.as_dict()), catalog.as_dict())

    def test_conflicting_or_chained_links_are_not_guessed(self):
        raw = fixture()
        raw['songs'][2]['difficulties'].append(dict(difficulty=5, level='0', level_value=0, origin_id=11))
        raw['songs'][3]['difficulties'][0]['origin_id'] = 8001
        catalog = model.Catalog(raw)
        self.assertEqual(catalog.parent_id(8001), 8001)
        self.assertEqual(catalog.parent_id(8002), 8002)
        self.assertEqual(len(catalog.songs[8001].charts), 2)

    def test_duplicate_ids_and_invalid_charts_fail_instead_of_overwriting(self):
        raw = fixture()
        raw['songs'].append(deepcopy(raw['songs'][0]))
        with self.assertRaises(ValueError):
            model.Catalog(raw)
        for invalid in (-1, 6, '4', True):
            raw = fixture()
            raw['songs'][0]['difficulties'][0]['difficulty'] = invalid
            with self.assertRaises(ValueError):
                model.Catalog(raw)
        for invalid in (float('nan'), float('inf'), -1, True, None):
            raw = fixture()
            raw['songs'][0]['difficulties'][0]['level_value'] = invalid
            with self.assertRaises(ValueError):
                model.Catalog(raw)

    def test_returned_views_do_not_mutate_the_catalog(self):
        catalog = model.Catalog(fixture())
        view = catalog.query_song(10)
        view['difficulties'][1]['notes']['total'] = -1
        view['related_songs'][0]['title'] = 'changed'
        self.assertEqual(catalog.query_song(10)['difficulties'][1]['notes']['total'], 2000)
        self.assertEqual(catalog.query_song(10)['related_songs'][0]['title'], 'Same title')

    def test_foreign_source_rejected_for_new_authoritative_updates(self):
        raw = fixture()
        raw['songs'][0]['data_source'] = 'chunirec'
        with self.assertRaises(ValueError):
            model.Catalog(raw)

    def test_standard_duplicate_difficulty_rejected_before_persistence(self):
        raw = fixture()
        raw['songs'][0]['difficulties'].append(deepcopy(raw['songs'][0]['difficulties'][0]))
        with self.assertRaises(ValueError):
            model.Catalog(raw)
