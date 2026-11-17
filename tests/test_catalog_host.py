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
nonebot.init(driver='~none',lxns_token='',localstore_data_dir=str(root/'data'),
             localstore_cache_dir=str(root/'cache'),localstore_config_dir=str(root/'config'))
spec=importlib.util.spec_from_file_location('offline',package/'services/data_migration.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
target=root/'data/nonebot_plugin_unibot';m.migrate(legacy,target)
original=m.inventory(legacy)
plugin=nonebot.load_plugin('nonebot_plugin_unibot');assert plugin is not None
from nonebot_plugin_unibot import song as s, songlist_update as update, best_rating as b, chuguess, overpower_list_local as op
from nonebot_plugin_unibot.services import player, song as query, maintenance
from nonebot_plugin_unibot import get_player_score as scores, llm_api
assert len(chuguess.load_songlist())==5
assert len(op.load_songlist_full()[0])==5
assert {(c['song_id'],c['level_index']) for c in op.get_target_charts('new')}=={('10',4)}
assert {(c['song_id'],c['level_index']) for c in op.get_target_charts('old')}=={('10',3)}
assert 'foreign-hash' not in scores._load_song_meta()
assert '8001' in scores._load_song_meta()
assert s.find_song_matches('foreign-only')==[]
matches=s.find_song_matches('Same title')
assert len([row for row in matches if row['score']>=1])==3
assert s.pick_song_match(matches)[0] is None  # unrelated same-title songs stay ambiguous
base=s.find_song_matches('10')[0]['song']
assert base['id']==10 and len(base['difficulties'])==4
we=s.find_song_matches('8001')[0]['song']
assert we['id']==8001 and we['parent_song_id']==10
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
meta=b.build_chart_meta(saved['songs'],const_source='chunirec')
assert meta[('10',3)]['level_value']==14.2
assert meta[('10',4)]['version']==24000 and ('8001',5) not in meta
assert b.normalize_local_item({'id':'foreign-hash','level_index':3,'score':1009000,'chunirec_level_value':16},meta) is None
assert b.normalize_local_item({'id':'10','level_index':3,'score':1009000,'chunirec_level_value':16},meta)['level_value']==14.2
# Preserve style/layout; prove both WE rows and their own IDs reach drawing.
labels=[]; original_draw=s.draw_text
def record(draw,pos,text,*args,**kwargs):
    labels.append(str(text));return original_draw(draw,pos,text,*args,**kwargs)
with patch.object(s,'draw_text',side_effect=record):
    image=s.render_song_image(base,base,None)
assert image.startswith(b'\xff\xd8')
assert sum(text.startswith("WORLD'S END") for text in labels)==2
assert any('8001' in text and '21000' in text for text in labels)
assert any('8002' in text and '23000' in text for text in labels)
assert llm_api.CAPABILITY_VERSION=='3.3.0'
specs=llm_api.describe_capabilities()
assert 'maintenance.import_chunirec' not in {spec['id'] for spec in specs}
assert 'chunirec' not in llm_api.MaintenanceArgs.model_json_schema()['properties']['resource']['enum']
from nonebot.adapters.onebot.v11 import PrivateMessageEvent
event=PrivateMessageEvent(time=0,self_id=1,post_type='message',sub_type='friend',user_id=7,
    message_type='private',message_id=8,message=[],raw_message='',font=0,sender={'user_id':7})
async def run():
    with patch.object(s,'fetch_song_detail',AsyncMock(return_value=None)), patch.object(s,'download_jacket',AsyncMock(return_value=None)),patch.object(s,'render_song_image',return_value=b'same-renderer'):
        output=await query.search('10')
    assert output['status']=='ok' and len(output['data']['charts'])==4
    with patch.object(b,'load_b30_scores',return_value={('10',3):{'id':'10','level_index':3,'score':1009000},('foreign',3):{'id':'foreign','level_index':3,'score':1009000}}),patch.object(b,'prepare_jackets',AsyncMock(return_value={})),patch.object(b,'draw_b30_image',return_value=b'same-b30'):
        output=await player.best('7',mode='b30')
    assert output['status']=='ok' and output['data']['constant_source']=='lx' and output['data']['unmatched_scores']==1
    with patch.object(b,'load_b30_scores',return_value={('foreign',3):{'id':'foreign','level_index':3,'score':1009000}}):
        output=await player.best('7',mode='b30')
    assert output['code']=='NO_SCORES' and output['data']['unmatched_scores']==1 and '保留' in output['public_hint']
    from nonebot_plugin_unibot.services.scores import submit_manual
    assert (await submit_manual('7','10',5,1000000))['code']=='AMBIGUOUS_TARGET'
    output=await submit_manual('7','8001',5,1000000)
    assert output['status']=='ok' and output['data']['song_id']=='8001'
    assert json.loads((target/'score/7_manual.json').read_text(encoding='utf-8'))[0]['id']=='8001'
    for name,args in [('maintenance.import_chunirec',{}),('maintenance.refresh',{'resource':'chunirec'})]:
        context={'bot':NS(self_id='1'),'event':event,'confirmed':True}
        assert (await llm_api.prepare_capability(name,args,context))['code']=='SOURCE_RETIRED'
        assert (await llm_api.invoke_capability(name,args,context))['code']=='SOURCE_RETIRED'
    job=NS(job_id='old',payload={'arguments':{'resource':'chunirec_file'}})
    store=NS(get=AsyncMock(return_value=NS(status='queued')),update=AsyncMock())
    with patch.object(maintenance,'native_superuser',return_value=True):
        output=await maintenance.run(NS(),event,job,store)
    assert output['code']=='SOURCE_RETIRED' and output.get('effect')=='none'
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
