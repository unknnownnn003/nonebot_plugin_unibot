"""Real plugin contracts with synthetic catalogs and mocked network/rendering."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_song_model import fixture

ROOT = Path(__file__).resolve().parents[1]


class CatalogHostTests(unittest.TestCase):
    def test_cn_consumers_refresh_search_rating_and_retired_tools(self):
        script = r'''
import asyncio, copy, importlib.util, json, shutil
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
import nonebot
root=Path.cwd()
package=root/'nonebot_plugin_unibot'
shutil.copytree(SOURCE/'nonebot_plugin_unibot', package, ignore=shutil.ignore_patterns('data','__pycache__'))
legacy=package/'data'; legacy.mkdir()
old=copy.deepcopy(FIXTURE)
old['songs'][0]['difficulties'][0].update(level_value=15.4,lx_level_value=14.2,chunirec_level_value=15.4)
old['songs'][0]['aliases']=['local alias']
old['songs'].append(dict(id='foreign-hash',title='foreign-only',data_source='chunirec',difficulties=[]))
(legacy/'songlist.json').write_text(json.dumps(old),encoding='utf-8')
nonebot.init(driver='~none',localstore_data_dir=str(root/'data'),
             localstore_cache_dir=str(root/'cache'),localstore_config_dir=str(root/'config'))
spec=importlib.util.spec_from_file_location('offline',package/'services/data_migration.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
target=root/'data/nonebot_plugin_unibot';m.migrate(legacy,target)
original=m.inventory(legacy)
plugin=nonebot.load_plugin('nonebot_plugin_unibot');assert plugin is not None
from nonebot_plugin_unibot import song as s, songlist_update as update, best_rating as b, chuguess, overpower_list_local as op
from nonebot_plugin_unibot.services import player, song as query, maintenance, song_images
from nonebot_plugin_unibot import llm_api, get_resource
from nonebot_plugin_unibot.services.rating import select
from PIL import Image
assert not get_resource.jacket_refresh_option('')
assert not get_resource.jacket_refresh_option(' 全部 ')
assert get_resource.jacket_refresh_option(' FULL ')
for value in ('notfull', 'full 全部', 'some force text'):
    try: get_resource.jacket_refresh_option(value)
    except ValueError: pass
    else: raise AssertionError('invalid refresh option was accepted')
assert s.song_query.rule
from nonebot_plugin_unibot.services.native_catalog import NATIVE_CATALOG
assert any(row.get('pattern') == '^(.+)是什么歌$' for row in NATIVE_CATALOG['song.search']['native_commands'])

assert len(chuguess.load_songlist())==5
from nonebot_plugin_unibot.services.song_model import Catalog,read_catalog
from nonebot_plugin_unibot.services.chart_progress import eligible_charts
catalog=Catalog(read_catalog(target/'songlist.json'))
assert len(catalog.songs)==5
def progress_matches(query, catalog):
    return op.matching_charts(eligible_charts(catalog, []), op.parse_filters(query, catalog))
assert {(c.song.id,c.chart.difficulty) for c in progress_matches('new',catalog)}=={(10,4)}
assert {(c.song.id,c.chart.difficulty) for c in progress_matches('old',catalog)}=={(10,3)}
assert s.find_song_matches('foreign-only')==[]
matches=s.find_song_matches('Same title')
assert len([row for row in matches if row['score']>=1])==4
assert s.pick_song_match(matches)[0] is None  # unrelated same-title songs stay ambiguous
base=s.find_song_matches('10')[0]['song']
assert base['id']==10 and len(base['difficulties'])==4
we=s.find_song_matches('8001')[0]['song']
assert we['id']==8001 and we['parent_song_id']==10

# Representative public titles and aliases, including an ID/alias collision.
search_rows = [
    dict(id=3, title='B.B.K.K.B.K.K.', aliases=['bk', 'bbkkbkk', '共享']),
    dict(id=24, title='ドーナツホール', aliases=['甜甜圈洞']),
    dict(id=38, title='天ノ弱', aliases=['共享']),
    dict(id=210, title='アスノヨゾラ哨戒班', aliases=['明日的夜空哨戒班']),
    dict(id=2439, title='夢と幻想の終点にて'),
    dict(id=2651, title='Acid God', aliases=['酸神', '3']),
]
search_catalog = Catalog({'songs': [dict(row, difficulties=[]) for row in reversed(search_rows)]})
for query_text, sid in [
    ('３', 3), ('B B K K B K K', 3), ('ＢＫ', 3), ('甜甜圈洞', 24), ('どーなつほーる', 24),
    ('dōnatsuhōru', 24), ('doonatsuhooru', 24), ('天の弱', 38), ('明日的夜空哨戒班', 210),
    ('梦と幻想の终点にて', 2439), ('Acid God', 2651), ('酸神', 2651),
]:
    selected, candidates = s.pick_song_match(s.find_song_matches(query_text, catalog=search_catalog))
    assert selected and selected['id']==sid and not candidates, (query_text, selected, candidates)
for query_text, first in [('2', 24), ('b', 3), ('acid', 2651), ('Acid Gdo', 2651), ('Aicd Gdo', 2651), ('アスノヨゾラ', 210)]:
    selected, candidates = s.pick_song_match(s.find_song_matches(query_text, catalog=search_catalog))
    if query_text in ('acid', 'アスノヨゾラ', 'b'):
        assert selected and selected['id']==first, query_text
    else:
        assert selected is None and candidates[0]['song']['id']==first, (query_text, candidates)
    assert not s.find_song_matches(query_text, catalog=search_catalog, exact_only=True)
for rows in (search_rows, reversed(search_rows)):
    alternate = Catalog({'songs': [dict(row, difficulties=[]) for row in rows]})
    selected, candidates = s.pick_song_match(s.find_song_matches('共享', catalog=alternate))
    assert not selected and [row['song']['id'] for row in candidates]==[3, 38]
with patch.object(s, '_read_songlist_data', return_value=search_catalog.as_dict()):
    assert chuguess.match_song_by_query('bk')['id']==3
    assert chuguess.match_song_by_query('Acid Gdo') is None
    assert chuguess.match_song_by_query('共享') is None

assert s.add_song_alias('11','isolated alias')==(True,True)
assert not s.add_song_alias('foreign-hash','forbidden')[0]
before=update.SONGLIST_FILE.read_bytes()
result=update.commit_lxns_songlist(FIXTURE,{'aliases':[{'song_id':10,'aliases':['remote alias']}]})
assert result['song_count']==5 and result['backup_created']
assert next((target/'catalog_backups').glob('*.json')).read_bytes()==before
saved=json.loads(update.SONGLIST_FILE.read_text(encoding='utf-8'))
assert saved['songs'][0]['aliases']==['remote alias','local alias']
assert saved['songs'][1]['aliases']==['isolated alias']
assert all(row['data_source']=='lxns_cn' for row in saved['songs'])
assert saved['songs'][0]['difficulties'][1]['version']==24000
snapshot=update.SONGLIST_FILE.read_bytes()
for bad in ({'songs':[]},{'songs':[{'id':10,'title':'bad','difficulties':[{'difficulty':4,'level_value':None}]}]}):
    try:update.commit_lxns_songlist(bad,{'aliases':[]})
    except ValueError:pass
    else:raise AssertionError('invalid refresh accepted')
    assert update.SONGLIST_FILE.read_bytes()==snapshot
meta=b.build_chart_meta(saved['songs'])
assert meta[('10',3)]['level_value']==14.2
assert meta[('10',4)]['version']==24000 and ('8001',5) not in meta
# Preserve metadata identities while presenting WE attributes and explicit IDs.
labels=[]; original_draw=song_images.label
display=copy.deepcopy(base)
next(d for d in display['difficulties'] if d['song_id']==8001).update(kanji='半',star=3)
def record(canvas,box,text,*args,**kwargs):
    labels.append(str(text));return original_draw(canvas,box,text,*args,**kwargs)
with patch.object(song_images,'label',side_effect=record),patch.object(song_images,'ui',return_value=None):
    image=s.render_song_image(display,None)
assert image.startswith(b'\x89PNG')
assert labels.count("WORLD’S END")==2
assert "半  ★3" in labels and "狂  ★5" in labels
assert "ID  10" in labels and "WE ID  8001" in labels and "WE ID  8002" in labels
assert {d['song_id'] for d in base['difficulties']}=={10,8001,8002}
assert llm_api.CAPABILITY_VERSION=='4.4.0'
specs=llm_api.describe_capabilities()
assert 'maintenance.import_chunirec' not in {spec['id'] for spec in specs}
assert 'chunirec' not in llm_api.MaintenanceArgs.model_json_schema()['properties']['resource']['enum']
from nonebot.adapters.onebot.v11 import PrivateMessageEvent
event=PrivateMessageEvent(time=0,self_id=1,post_type='message',sub_type='friend',user_id=7,
    message_type='private',message_id=8,message=[],raw_message='',font=0,sender={'user_id':7})
async def run():
    rows = [dict(id=i, title=("共通" if i == 1 else "星" * 90 + str(i)), difficulties=[],
                 aliases=["共通", "Ａ＋Ｂ"]) for i in range(1, 12)]
    case = Catalog({"songs": rows})
    result = await query.prepare("共通 --page 2", catalog=case)
    assert result["total"] == 11 and len(result["ambiguous"]) == 3 and result["page"] == 2
    assert len(s.find_song_matches("a+b", catalog=case)) == 11
    assert (await query.prepare("共通", catalog=case))["song"] is None
    with patch.object(s, "_read_songlist_data", return_value=case.as_dict()), patch.object(s, "download_match_jacket", AsyncMock(return_value=None)):
        native = await query.search("共通 --page 2")
        tools = await query.aliases("共通 --page 2")
        assert native["data"] == tools["data"] and native["data"]["total"] == 11
        assert native["data"]["candidates"][0]["title"] == rows[8]["title"]
    prepared = await query.prepare('local alias')
    updated = copy.deepcopy(FIXTURE)
    first = updated['songs'][0]
    first.update(title='New title', artist='New artist', genre='东方Project', release_date='2020-11-01')
    first['difficulties'][0].update(level_value=14.7, note_designer='New designer', notes={'tap': 1200, 'total': 1800})
    first['difficulties'][1]['disabled'] = True
    updated['songs'][1]['disabled'] = True
    updated['songs'] = [row for row in updated['songs'] if row['id'] != 8003]
    updated['songs'].append(dict(id=12, title='Added', genre='fixture genre', difficulties=[dict(difficulty=3, level_value=13)]))
    updated['versions'][0]['title'] = 'New version label'
    counts = update.commit_lxns_songlist(updated, {'aliases': []})
    assert (counts['songs_added'], counts['songs_changed'], counts['songs_removed'], counts['songs_disabled'])==(1,2,1,1),counts
    assert (counts['charts_added'], counts['charts_changed'], counts['charts_removed'], counts['charts_disabled'])==(1,2,1,2),counts
    current = Catalog(read_catalog(target/'songlist.json'))
    new_match = s.find_song_matches('local alias')[0]['song']
    assert new_match['title']=='New title' and new_match['artist']=='New artist'
    assert new_match['difficulties'][0]['notes']=={'tap':1200,'total':1800}
    assert new_match['difficulties'][0]['level_value']==14.7
    assert new_match['difficulties'][0]['note_designer']=='New designer'
    assert s.pick_song_match(s.find_song_matches('New title'))[0]['id']==10
    assert new_match['_versions'][0]['title']=='New version label'
    assert prepared['song']['title']=='Same title'
    assert prepared['song']['_versions'][0]['title']=='CHUNITHM OLD'
    assert {(c.song.id, float(c.constant)) for c in progress_matches('车万',current)}=={(10,14.7)}
    scores = NS(player={'name': 'Fixture', 'level': 10, 'rating': 15},
                scores=[dict(id=i, level_index=d, score=1009000, rating=15, over_power=80)
                       for i,d in ((10,3),(10,4),(11,3))])
    assert [row['level_value'] for row in select(scores,current)[0]]==[14.7]
    with patch.object(s,'download_jacket',AsyncMock(return_value=None)):
        assert (await player.song_scores(scores,current,'local alias'))['data']['song_id']==10
    jacket_dir = target/'jacket'
    jacket_dir.mkdir(exist_ok=True)
    with Image.new('RGBA',(16,16),'red') as sample:
        sample.save(jacket_dir/'10.png')
    (jacket_dir/'12.png').write_bytes(b'invalid')
    with patch.object(chuguess.random, 'shuffle', side_effect=lambda rows: rows.reverse()):
        picked = chuguess.pick_random_song_and_jacket()
    assert picked[2] == '10' and picked[0]['id'] == 10
    with Image.new('RGBA',(8,8)) as tiny:
        assert chuguess.crop_jacket(tiny, 8)[0].size == (8,8)
    with patch.object(s,'download_jacket',AsyncMock(return_value=None)),patch.object(s,'render_song_image',return_value=b'same-renderer'):
        output=await query.search('10')
    assert output['status']=='ok' and len(output['data']['charts'])==4
    broken = copy.deepcopy(updated)
    broken['songs'][0]['aliases'] = ['recovered alias']
    del broken['songs'][0]['difficulties'][0]['level_value']
    previous = json.dumps(broken).encode()
    update.SONGLIST_FILE.write_bytes(previous)
    counts = update.commit_lxns_songlist(updated, {'aliases': []})
    assert counts['backup_created'] and not counts['changes_available']
    assert any(p.read_bytes()==previous for p in (target/'catalog_backups').glob('*.json'))
    assert s.find_song_matches('recovered alias')[0]['song']['id']==10
    # Both the failed file and malformed/empty replacements retain their last good source.
    import httpx
    from nonebot_plugin_unibot.services import resource_tasks, song_aliases
    from contextlib import asynccontextmanager
    public = copy.deepcopy(updated)
    public["songs"][-1]["title"] = "Import target"
    previous_sources = {"marisabot": [["Import target", "preserved alias"]],
                        "gcm_ja": [["Import target", "old Japanese"]]}
    update.commit_lxns_songlist(public, {"aliases": [{"song_id": 12, "aliases": ["official"]}]}, previous_sources)
    payloads = {update.BASE_URL + "/api/v0/site/config":
                {"success": True, "code": 200, "data": {"resource_version": {"chunithm": 24000}}},
                update.BASE_URL + update.API_PATH: public,
                update.BASE_URL + "/api/v0/chunithm/alias/list": {"aliases": []}}
    def respond(request):
        url = str(request.url).split("?")[0]
        if url in payloads:
            return httpx.Response(200, json=payloads[url])
        if url == song_aliases.URLS["marisabot"]:
            raise httpx.ConnectError("fixture")
        if url == song_aliases.URLS["gcm_ja"]:
            return httpx.Response(200, content=b"")
        return httpx.Response(200, content=b"Import target\tnew English")
    @asynccontextmanager
    async def client():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as session:
            yield session
    async def network(value, step):
        return await value
    async def write(step, fn, *args):
        return fn(*args)
    with patch.object(resource_tasks.http_safety, "client", client):
        result = await resource_tasks.refresh_songlist(NS(network=network, write=write))
    assert set(result["data"]["unavailable_alias_sources"]) == {"marisabot", "gcm_ja", "lxns"}
    assert set(s.find_song_matches("12")[0]["song"]["aliases"]) == {"official", "preserved alias", "old Japanese", "new English"}

    # Cached retired invocations cannot write or resubmit old imports.
    for method in [llm_api.prepare_capability,llm_api.invoke_capability]:
        for name in ['player.bind','score.submit','score.sync','score.import_file']:
            result=await method(name,{},dict(bot=NS(self_id='1'),event=event,confirmed=True))
            assert result['code']=='CAPABILITY_RETIRED'
asyncio.run(run())
assert m.inventory(legacy)==original
print('CATALOG_HOST_OK')
'''
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run([sys.executable, '-X', 'utf8', '-c',
                f'SOURCE=__import__("pathlib").Path({str(ROOT)!r})\nFIXTURE={fixture()!r}\n'+script],
                cwd=directory, env=dict(os.environ, PYTHONPYCACHEPREFIX=str(Path(directory)/'pycache')),
                capture_output=True, text=True, encoding='utf-8', timeout=60)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertIn('CATALOG_HOST_OK', completed.stdout)
