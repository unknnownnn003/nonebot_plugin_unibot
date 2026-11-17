"""Load all three real plugins in an isolated NoneBot host with simulated OAuth/API."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

class ThreePluginHostTests(unittest.TestCase):
    def test_real_discovery_policy_oauth_and_jobs(self):
        script=r'''
import asyncio,hashlib,json,shutil,sys,time
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
import httpx,nonebot
from cryptography.fernet import Fernet
root=Path.cwd()
for name in ['nonebot_plugin_unibot','nonebot_plugin_unknnownnn','nonebot_plugin_llmchat']:
    source=SOURCE.parent/name/(name if name!='nonebot_plugin_llmchat' else '')
    shutil.copytree(source,root/name,ignore=shutil.ignore_patterns('.*','data','config','cache','__pycache__','tests','docs','previews','dist','tools','assets'))
nonebot.init(driver='~fastapi',llmchat={'api_presets':[{'name':'fixture','api_base':'https://fixture.invalid/v1','api_key':'fixture-key','model_name':'fixture','support_tools':True}]},localstore_data_dir=str(root/'data'),localstore_config_dir=str(root/'config'),localstore_cache_dir=str(root/'cache'))
from nonebot.adapters.onebot.v11 import Adapter,Bot
nonebot.get_driver().register_adapter(Adapter)
for name in ['nonebot_plugin_unknnownnn','nonebot_plugin_unibot','nonebot_plugin_llmchat']:
    assert nonebot.load_plugin(name) is not None,name
sys.path.insert(0,str(SOURCE/'tests'))
from test_song_model import fixture
from test_player_data import event,personal
from nonebot_plugin_unibot import llm_api,best_rating as b
from nonebot_plugin_unibot.services import player_data as pd,player,paths,oauth_runtime,sync,group_policy,media_jobs
from nonebot_plugin_unibot.services.oauth import OAuthService,Settings
from nonebot_plugin_unibot.services.oauth_store import OAuthStore
from nonebot_plugin_unknnownnn import feature_manager as fm,settings_flow
from nonebot_plugin_llmchat import agent_tools as tools
from nonebot_plugin_llmchat import init_plugin,cleanup_plugin

fm.config_data={'_policy_version':2,'123':{'_plugins':{'unibot':True,'llmchat':True,'unknnownnn':False},'_unibot':{key:True for key in fm.policy.UNIBOT_FEATURES}}}
original=json.dumps(fm.config_data,sort_keys=True)
assert fm.policy.capability_feature('oauth.manage')=='scores'
assert 'chunirec' not in dict(settings_flow.RESOURCES)
catalog=fixture();catalog['rating_version']=24000
catalog['versions'].append(dict(version=20000,title='CHUNITHM NEW'))
catalog['songs'].append(dict(id=12,title='Current CN song',version=24000,difficulties=[dict(difficulty=3,level='14',level_value=14,version=24000)]))
paths.SONGLIST_PATH.write_text(json.dumps(catalog),encoding='utf8')
settings=Settings('fixture','secret','https://example.test/oauth/lxns/callback',Fernet.generate_key().decode())
store=OAuthStore(paths.OAUTH_DIR,settings.encryption_key);auth=OAuthService(settings,store,group_allowed=lambda g:group_policy.enabled(NS(group_id=g),'oauth.manage'))
oauth_runtime._service=auth
now=time.time();store.begin('1:7','grant','state',{},now);request,_=store.claim('state',now)
value=dict(access_token='fixture-access',refresh_token='fixture-refresh',expires_at=now+900,subject='subject',issuer='https://maimai.lxns.net',config=settings.fingerprint,confirm_hash=hashlib.sha256(b'proof').hexdigest(),confirm_expires=now+300)
store.finish(request,value,store.subject_key('1',settings.issuer if hasattr(settings,'issuer') else 'https://maimai.lxns.net','subject'),now);store.confirm('1:7','proof',now)
calls=[]
async def personal_api(request):
    calls.append(request)
    assert request.headers['Authorization']=='Bearer fixture-access'
    assert request.url.path in ['/api/v0/user/chunithm/player','/api/v0/user/chunithm/player/scores']
    p,rows=personal();rows.append(dict(rows[0],id=12))
    return httpx.Response(200,json={'success':True,'code':200,'data':rows if request.url.path.endswith('/scores') else p})
pd._reader=pd.PlayerData(auth,transport=httpx.MockTransport(personal_api))
transport=AsyncMock(return_value={'message_id':200})
bot=Bot(NS(config=nonebot.get_driver().config,get_name=lambda:'OneBot V11',_call_api=transport),'1')
g=event(group=123);private=event()
async def terminal(identifier):
    for _ in range(500):
        job=await sync._store.get(identifier)
        if job.status not in {'queued','running','cancel_requested'}:return job
        await asyncio.sleep(.01)
    raise AssertionError('job did not finish')
async def run():
    import nonebot_plugin_orm as orm
    orm._init_orm()
    for key,engine in orm._engines.items():
        async with engine.begin() as connection:
            await connection.run_sync(orm._metadatas[key].create_all)
    await init_plugin()
    registry=tools.registry
    assert set(registry.providers)==set(tools.PROVIDERS),registry.unavailable
    assert {'nb_unibot_best','nb_unibot_b50','nb_unibot_song_scores'} <= set(registry.specs)
    assert not {'nb_unibot_bind','nb_unibot_score_sync','nb_unibot_score_submit','nb_unibot_import_scores'} & set(registry.specs)
    assert 'nb_unibot_b50' in registry.select('/b50')
    assert 'nb_unibot_song_scores' in registry.select('chu成绩')
    context=tools.ToolContext(bot,g,'explicit_mention','cross-plugin')
    view=await registry.context_catalog(context)
    assert view['nb_unibot_b50'][1]['available']
    assert '/b' not in [c.get('command') for s in llm_api.describe_capabilities() for c in s['native_commands']]
    assert all('b' not in c.get('aliases',[]) for s in llm_api.describe_capabilities() for c in s['native_commands'])
    with patch.object(b,'prepare_jackets',AsyncMock(return_value={})):
        result=await llm_api.invoke_capability('player.b50',{},dict(bot=bot,event=g))
        assert result['code']=='JOB_ACCEPTED',result
        job=await terminal(result['data']['job_id']);assert job.status=='succeeded',job.result
        assert (job.result['data']['best30_count'],job.result['data']['new20_count'])==(2,1)
        assert len(calls)==2 and transport.await_count==1 # Agent result: image only.
        native_event=g.model_copy(update={'message_id':905})
        native=await media_jobs.submit(bot,native_event,'player.b50',{},origin='command')
        await terminal(native['data']['job_id'])
        assert transport.await_count==3 # Native receipt, then image only.
        assert '正在生成，请稍候' in str(transport.await_args_list[1])
        duplicate=await media_jobs.submit(bot,native_event,'player.b50',{},origin='command')
        assert duplicate['code']=='JOB_EXISTS' and transport.await_count==3
        from nonebot_plugin_unibot import song as native_song
        search_event=g.model_copy(update={'message_id':906})
        with patch.object(native_song,'fetch_song_detail',AsyncMock(return_value=None)),patch.object(native_song,'download_jacket',AsyncMock(return_value=None)):
            search=await media_jobs.submit(bot,search_event,'song.search',{'query':'10'},origin='command')
            await terminal(search['data']['job_id'])
        assert transport.await_count==4 # Song lookup sends its image, no generation receipt.
        before=transport.await_count
        fm.config_data['123']['_unibot']['player']=False
        view=await registry.context_catalog(context)
        assert view['nb_unibot_b50'][1]['unavailable_code']=='FEATURE_DISABLED'
        output=await media_jobs.submit(bot,g.model_copy(update={'message_id':900}),'player.b50',{},origin='command')
        assert output['code']=='FEATURE_DISABLED' and len(calls)==2 and transport.await_count==before
        # Private query shares the same owner snapshot; group selection does not affect private scopes.
        output=await player.query(bot,private,'player.b50',{})
        assert output['status']=='ok' and len(calls)==2
        fm.config_data['123']['_unibot']['player']=True
        await auth.unlink('1','7')
        view=await registry.context_catalog(context)
        assert view['nb_unibot_b50'][1]['unavailable_code']=='NOT_AUTHORIZED'
        assert (await sync.replay(bot,g,job.job_id))['code']=='AUTHORIZATION_CHANGED'
        assert (await sync.query(bot,g,'get',job.job_id))['data']['result']['code']=='AUTHORIZATION_CHANGED'
        assert transport.await_count==before
    assert json.dumps(fm.config_data,sort_keys=True)==original
    await sync.stop_workers()
    await cleanup_plugin()
    await orm._scoped_sessions.remove()
    for engine in orm._engines.values():
        await engine.dispose()
asyncio.run(run())
print('THREE_PLUGIN_HOST_OK')
'''
        with tempfile.TemporaryDirectory() as temp:
            env=dict(os.environ,PYTHONIOENCODING='utf8',PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1')
            try:
                result=subprocess.run([sys.executable,'-u','-c','from pathlib import Path\nSOURCE=Path('+repr(str(ROOT))+')\n'+script],cwd=temp,env=env,capture_output=True,text=True,encoding='utf8',timeout=65)
            except subprocess.TimeoutExpired as error:
                self.fail(str(error.stdout)+str(error.stderr))
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('THREE_PLUGIN_HOST_OK',result.stdout)
            self.assertNotIn('[ERROR]',result.stdout+result.stderr)

if __name__=='__main__':unittest.main()
