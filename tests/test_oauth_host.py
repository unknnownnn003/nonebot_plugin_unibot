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
import asyncio, json, shutil, hashlib, time
from urllib.parse import urlsplit,parse_qs
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch, AsyncMock
from cryptography.fernet import Fernet
import httpx
import nonebot
root=Path.cwd()
shutil.copytree(SOURCE/'nonebot_plugin_unibot',root/'nonebot_plugin_unibot',ignore=shutil.ignore_patterns('data','__pycache__'))
nonebot.init(driver='~fastapi',localstore_data_dir=str(root/'data'),localstore_cache_dir=str(root/'cache'),
    localstore_config_dir=str(root/'config'),lxns_oauth_enabled=True,lxns_oauth_client_id='fixture-client',
    lxns_oauth_client_secret='fixture-secret',lxns_oauth_redirect_uri='https://example.test/oauth/lxns/callback',
    lxns_oauth_encryption_key=Fernet.generate_key().decode())
plugin=nonebot.load_plugin('nonebot_plugin_unibot');assert plugin is not None
from nonebot_plugin_unibot import user_bind, llm_api
from nonebot_plugin_unibot.services import oauth_runtime as runtime, group_policy, paths
from nonebot_plugin_unibot.services.oauth import OAuthError, CALLBACK_PATH
assert runtime._service is not None
assert runtime.get_service() is runtime._service
from nonebot.log import logger
records=[]
sink=logger.add(lambda message: records.append(message.record),format='{message}')
logger.bind(component='host').info("Private message: /落雪确认 fixture-confirmation")
logger.debug("send_private_msg https://maimai.lxns.net/oauth/authorize?state=fixture-state")
logger.info("ordinary diagnostic")
logger.remove(sink)
assert all('fixture-confirmation' not in r['message'] and 'fixture-state' not in r['message'] for r in records)
assert records[0]['extra']['component']=='host'
assert records[-1]['message']=='ordinary diagnostic'
patcher=logger._core.patcher
runtime.protect_oauth_logs()
assert logger._core.patcher is patcher
assert runtime._service.store.path==paths.OAUTH_DIR/'credentials.sqlite3'
assert len([r for r in nonebot.get_driver().server_app.routes if getattr(r,'path',None)==CALLBACK_PATH])==1
assert len(llm_api.describe_capabilities())==26
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
score=paths.DATA_DIR/'score';score.mkdir(exist_ok=True);(score/'7.json').write_text('[{"id":10,"score":1000000}]')
before=legacy.read_bytes(),(score/'7.json').read_bytes()
async def run():
    global allowed,fail_delivery
    with patch.object(group_policy,'manager',return_value=fm):
        result=await runtime.command(bot,group,'begin')
        assert 'https://maimai.lxns.net/oauth/authorize?' in result and '撤回' in result
        assert 'fixture-secret' not in result and not calls
        link_message=result
        with patch.object(user_bind,'_oauth_command',AsyncMock(return_value=result)), patch.object(user_bind.oauth_authorize,'send',AsyncMock(return_value={'message_id':321})), patch.object(user_bind.oauth_authorize,'finish',AsyncMock()):
            await user_bind.authorize(bot,group)
        # The receipt survives a process/store reopen without keeping the URL.
        from nonebot_plugin_unibot.services.oauth_store import OAuthStore
        runtime._service.store=OAuthStore(paths.OAUTH_DIR,runtime._service.settings.encryption_key)
        # Real encrypted pending credentials: only the originating QQ/Bot can
        # finish in the group; no private Bot API is used at any point.
        url=next(line for line in result.splitlines() if line.startswith('https://'))
        state=parse_qs(urlsplit(url).query)['state'][0]
        service=runtime._service;store=service.store;now=time.time()
        request,_=store.claim(hashlib.sha256(state.encode()).hexdigest(),now)
        value=dict(access_token='fixture-access',refresh_token='fixture-refresh',expires_at=now+900,
                   subject='fixture-subject',issuer='https://maimai.lxns.net',config=service.settings.fingerprint,
                   confirm_hash=hashlib.sha256(b'browser-proof').hexdigest(),confirm_expires=now+300,origin_group=8)
        store.finish(request,value,store.subject_key('1','https://maimai.lxns.net','fixture-subject'),now)
        other=group.model_copy(update={'user_id':9})
        denied=await runtime.command(bot,other,'confirm','browser-proof')
        assert getattr(denied,'_error_notice',False) and '撤回' in denied
        assert await service.status('1','7')=='awaiting_confirmation'
        assert not calls
        assert '绑定成功' in await runtime.command(bot,group,'confirm','browser-proof')
        assert calls==[('delete_msg',{'message_id':321})]
        calls.clear()
        assert await service.status('1','7')=='active'
        assert 'OAuth 已绑定' in await runtime.command(bot,group,'status')
        repeated=await runtime.command(bot,group,'confirm','browser-proof')
        assert getattr(repeated,'_error_notice',False) and '撤回' in repeated
        assert not calls
        # A receipt arriving after successful confirmation is also retracted.
        await runtime.remember_authorization_message(bot,group,link_message,{'message_id':322})
        assert calls==[('delete_msg',{'message_id':322})]
        calls.clear()
        # Failed deletion never turns successful authorization into failure.
        fail_delivery=True
        await runtime.remember_authorization_message(bot,group,link_message,{'message_id':323})
        assert await service.status('1','7')=='active'
        assert calls==[('delete_msg',{'message_id':323})]
        calls.clear();fail_delivery=False
        allowed=False
        for action in ['begin','unlink','status','confirm']:
            denied=await runtime.command(bot,group,action,'确认')
            assert '关闭' in denied and getattr(denied,'_error_notice',False)
        assert not calls  # No private sends, including denied operations.
        assert '本地 OAuth 已解除' in await runtime.command(bot,private,'unlink','确认')
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
        assert 'https://' in result and '撤回' in result and not calls # No DM dependency.
        fail_delivery=False
        assert '请发送 /落雪解绑 确认' in await runtime.command(bot,group,'unlink')
        assert '本地 OAuth 已解除' in await runtime.command(bot,group,'unlink','确认')
        assert await runtime._service.status('1','7')=='unbound'
        assert '尚未绑定' in await runtime.command(bot,group,'status') and not calls
    # Check real driver route, with bad state: no network and no provider echo.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=nonebot.get_driver().server_app),base_url='https://example.test') as client:
        result=await client.get(CALLBACK_PATH,params={'state':'x'*43,'code':'PRIVATE-UPSTREAM-CODE'})
        assert result.status_code==400 and 'PRIVATE-UPSTREAM-CODE' not in result.text
        with patch.object(runtime._service,'callback',AsyncMock(return_value='fixture-local-proof')):
            result=await client.get(CALLBACK_PATH,params={'state':'x'*43,'code':'fixture-code'})
            assert result.status_code==200 and '/落雪确认 fixture-local-proof' in result.text
            assert '撤回确认指令' in result.text and '机器人私聊' not in result.text
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
