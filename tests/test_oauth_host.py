"""Optional FastAPI integration, real OneBot identity and existing shared policy."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class OAuthHostTests(unittest.TestCase):
    def test_native_group_private_identity_policy_and_llm_boundaries(self):
        script = r'''
import asyncio, json, shutil
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch, AsyncMock
from cryptography.fernet import Fernet
import httpx
import nonebot
root=Path.cwd()
shutil.copytree(SOURCE/'nonebot_plugin_unibot',root/'nonebot_plugin_unibot',ignore=shutil.ignore_patterns('data','__pycache__'))
nonebot.init(driver='~fastapi',lxns_token='',localstore_data_dir=str(root/'data'),localstore_cache_dir=str(root/'cache'),
    localstore_config_dir=str(root/'config'),lxns_oauth_enabled=True,lxns_oauth_client_id='fixture-client',
    lxns_oauth_client_secret='fixture-secret',lxns_oauth_redirect_uri='https://example.test/oauth/lxns/callback',
    lxns_oauth_encryption_key=Fernet.generate_key().decode())
plugin=nonebot.load_plugin('nonebot_plugin_unibot');assert plugin is not None
from nonebot_plugin_unibot import user_bind, llm_api
from nonebot_plugin_unibot.services import oauth_runtime as runtime, group_policy, paths
from nonebot_plugin_unibot.services.oauth import OAuthError, CALLBACK_PATH
assert runtime._service is not None
assert runtime.get_service() is runtime._service
assert runtime._service.store.path==paths.OAUTH_DIR/'credentials.sqlite3'
assert len([r for r in nonebot.get_driver().server_app.routes if getattr(r,'path',None)==CALLBACK_PATH])==1
assert len(llm_api.describe_capabilities())==27
assert all(not s['id'].startswith('oauth.') for s in llm_api.describe_capabilities())
assert 'fixture-secret' not in repr(nonebot.get_plugin_config(plugin.module.Config))
assert 'fixture-secret' not in repr(runtime._service.settings)
assert all(getattr(user_bind,n).module_name.endswith('.user_bind') for n in ['oauth_authorize','oauth_confirm','oauth_status','oauth_unlink'])
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, PrivateMessageEvent
class FakeBot(Bot):
    async def call_api(self, api, **kwargs):
        calls.append((api,kwargs))
        if fail_delivery:raise RuntimeError('private details must not be echoed')
        return {'message_id':123}
bot=FakeBot(NS(config=nonebot.get_driver().config,get_name=lambda:'OneBot V11'),'1')
private=PrivateMessageEvent(time=0,self_id=1,post_type='message',sub_type='friend',user_id=7,message_type='private',message_id=1,
    message=[],raw_message='',font=0,sender={'user_id':7})
group=GroupMessageEvent(time=0,self_id=1,post_type='message',sub_type='normal',user_id=7,group_id=8,message_type='group',message_id=2,
    message=[],raw_message='',font=0,sender={'user_id':7})
allowed=True;calls=[];fail_delivery=False
fm=NS(policy=NS(capability_feature=lambda name:'scores',MODULE_FEATURES={'user_bind':'scores'}),
      is_plugin_feature_enabled=lambda g,p,f:allowed)
legacy=paths.DATA_DIR/'user_bind_info.json';legacy.write_text('{"7":"123456789"}',encoding='utf-8')
score=paths.SCORE_DIR;score.mkdir(exist_ok=True);(score/'7.json').write_text('[{"id":10,"score":1000000}]')
before=legacy.read_bytes(),(score/'7.json').read_bytes()
async def run():
    global allowed,fail_delivery
    with patch.object(group_policy,'manager',return_value=fm):
        result=await runtime.command(bot,group,'begin')
        assert result=='已私发操作说明，请查看机器人私聊。'
        assert len(calls)==1 and calls[0][0]=='send_private_msg' and calls[0][1]['user_id']==7
        assert 'https://maimai.lxns.net/oauth/authorize?' in calls[0][1]['message']
        assert 'fixture-secret' not in calls[0][1]['message']
        assert '请在机器人私聊' in await runtime.command(bot,group,'confirm','browser-proof')
        allowed=False
        for action in ['begin','unlink','status','confirm']:
            denied=await runtime.command(bot,group,action,'确认')
            assert '关闭' in denied and getattr(denied,'_error_notice',False)
        assert len(calls)==1  # No underlying Bot API on denied operations.
        assert 'https://' in await runtime.command(bot,private,'begin') # Private not tied to group switch.
        mismatch=private.model_copy(update={'self_id':2})
        assert '无法核验' in await runtime.command(bot,mismatch,'begin')
        fake=NS(self_id=1,user_id=7)
        assert '无法核验' in await runtime.command(bot,fake,'begin')
        for method in [llm_api.prepare_capability,llm_api.invoke_capability]:
            result=await method('oauth.begin',{},dict(bot=bot,event=private,confirmed=True))
            assert result['code']=='NATIVE_ONLY'
        allowed=True;fail_delivery=True
        result=await runtime.command(bot,group,'begin')
        assert '私信送达未确认' in result and 'https://' not in result and 'private details' not in result
        fail_delivery=False
        assert '请发送 /落雪解绑 确认' in await runtime.command(bot,private,'unlink')
        assert '本地 OAuth 已解除' in await runtime.command(bot,private,'unlink','确认')
        assert await runtime._service.status('1','7')=='unbound'
    # Check real driver route, with bad state: no network and no provider echo.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=nonebot.get_driver().server_app),base_url='https://example.test') as client:
        result=await client.get(CALLBACK_PATH,params={'state':'x'*43,'code':'PRIVATE-UPSTREAM-CODE'})
        assert result.status_code==400 and 'PRIVATE-UPSTREAM-CODE' not in result.text
    assert (legacy.read_bytes(),(score/'7.json').read_bytes())==before
asyncio.run(run())
print('OAUTH_HOST_OK')
'''
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
            result = subprocess.run([sys.executable, "-c", "from pathlib import Path\nSOURCE=Path(" + repr(str(ROOT)) + ")\n" + script],
                                    cwd=directory, env=env, capture_output=True, text=True, encoding="utf-8", timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("OAUTH_HOST_OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
