"""Isolated real plugin, queues, renderers and OneBot identity; mock personal API."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class PlayerHostTests(unittest.TestCase):
    def test_oauth_consumers_jobs_retirement_and_authorized_replay(self):
        script=r'''
import asyncio, copy, importlib.util, json, shutil, sys, time
from decimal import Decimal
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch, AsyncMock
from cryptography.fernet import Fernet
from PIL import Image, ImageColor
import httpx, nonebot
root=Path.cwd()
shutil.copytree(SOURCE/'nonebot_plugin_unibot',root/'nonebot_plugin_unibot',ignore=shutil.ignore_patterns('data','__pycache__'))
sys.path.append(str(SOURCE/'tests'))
from test_song_model import fixture
from test_player_data import personal, event
nonebot.init(driver='~none', localstore_data_dir=str(root/'data'),localstore_cache_dir=str(root/'cache'),localstore_config_dir=str(root/'config'))
assert nonebot.load_plugin('nonebot_plugin_unibot') is not None
from nonebot_plugin_unibot import llm_api,best_rating as b,get_player_songlist as profiles,overpower_list_local as op,recommendation as rec,song as native_song
from nonebot_plugin_unibot.services import image_components,oauth_runtime,paths,player,player_data as pd,sync,media_jobs,group_policy,song_images
from nonebot_plugin_unibot.services.oauth import OAuthService,Settings
from nonebot_plugin_unibot.services.oauth_store import OAuthStore
from nonebot_plugin_unibot.services.song_model import Catalog
from nonebot_plugin_unibot.services import chart_progress as cp, progress_images as pi
from nonebot.adapters.onebot.v11 import Bot,MessageSegment
from nonebot_plugin_unibot.services.presentation_store import scope
catalog=fixture();catalog['rating_version']=24000
catalog['versions'].append(dict(version=20000,title='CHUNITHM NEW'))
paths.SONGLIST_PATH.write_text(json.dumps(catalog),encoding='utf-8')
native_song.add_song_alias('10','family-alias')
legacy=paths.DATA_DIR/'score';legacy.mkdir(exist_ok=True)
(legacy/'7.json').write_text('[{"id":10,"score":999999999}]')
(legacy/'7_manual.json').write_text('OLD-MANUAL')
old_bind=paths.DATA_DIR/'user_bind_info.json';old_bind.write_text('{"7":"old-friend-code"}')
legacy_before={p.name:p.read_bytes() for p in legacy.iterdir() if p.is_file()}
bind_before=old_bind.read_bytes()
settings=Settings('fixture','secret','https://example.test/oauth/lxns/callback',Fernet.generate_key().decode())
store=OAuthStore(paths.OAUTH_DIR,settings.encryption_key)
auth=OAuthService(settings,store)
oauth_runtime._service=auth
api_calls=[];sent=[];allowed=True;mode='ok';started=asyncio.Event();release=asyncio.Event()
async def provider(request):
    api_calls.append(request)
    assert request.url.path in ['/api/v0/user/chunithm/player','/api/v0/user/chunithm/player/scores']
    p,rows=personal();rows[2].pop('over_power');rows[1]['full_combo']=None;rows[1]['full_chain']='fullchain';who=request.headers['Authorization'].removeprefix('Bearer access-')
    assert who in {'7','8'}
    p['name']='USER-'+who
    if request.url.path.endswith('/scores') and mode=='pause':
        started.set();await release.wait()
    if request.url.path.endswith('/scores'):
        return httpx.Response(200,json={'success':True,'code':200,'data':rows})
    return httpx.Response(200,json={'success':True,'code':200,'data':p})
reader=pd.PlayerData(auth,transport=httpx.MockTransport(provider));pd._reader=reader
class FakeBot(Bot):
    async def call_api(self,api,**kwargs):
        sent.append((api,kwargs));return {'message_id':len(sent)}
bot=FakeBot(NS(config=nonebot.get_driver().config,get_name=lambda:'OneBot V11'),'1')
e=event();g=event(group=123)
def bind(qq='7'):
    now=time.time();state='hash-'+qq
    store.begin('1:'+qq,'grant-'+qq,state,{},now)
    request,_=store.claim(state,now)
    value=dict(access_token='access-'+qq,refresh_token='refresh-'+qq,expires_at=now+900,subject='subject-'+qq,issuer='https://maimai.lxns.net',config=settings.fingerprint,confirm_hash='proofhash',confirm_expires=now+300)
    import hashlib
    value['confirm_hash']=hashlib.sha256(b'proof').hexdigest()
    store.finish(request,value,store.subject_key('1','https://maimai.lxns.net','subject-'+qq),now)
    store.confirm('1:'+qq,'proof',now)
async def wait(job_id):
    for _ in range(400):
        job=await sync._store.get(job_id)
        if job.status not in {'queued','running','cancel_requested'}:return job
        await asyncio.sleep(.01)
    raise AssertionError('queue did not finish')

async def check_progress(snapshot):
    def chart(difficulty=3, constant=14.9, level='14+', **attrs):
        return dict(difficulty=difficulty, level_value=constant, level=level, **attrs)

    base = Catalog(dict(
        versions=[dict(title='CHUNITHM SUN', version=21000),
                  dict(title='CHUNITHM SUN PLUS', version=21500)],
        songs=[
            dict(id=1, title='霜降り Ω', genre='东方Project', version=21000, difficulties=[
                chart(0), chart(1), chart(2), chart(), chart(4, 14.8, version=21500), chart(5)]),
            dict(id=2, title='Zero', genre='原创', version=21000, difficulties=[chart()]),
            dict(id=3, title='Unplayed', genre='原创', version=21000, difficulties=[chart()]),
            dict(id=4, title='Precise', genre='原创', version=21000, difficulties=[chart(constant=14.899)]),
            dict(id=5, title='Excluded', disabled=True, difficulties=[chart()]),
            dict(id=6, title='Future', release_date='2199-01-01', difficulties=[chart()]),
            dict(id=7, title='Disabled chart', difficulties=[chart(disabled=True)]),
            dict(id=8, title='Level', difficulties=[chart(constant=14.9, level='14')]),
        ]))
    scores = [
        dict(id=1, level_index=0, score=1009000, over_power=85, full_combo='alljusticecritical'),
        dict(id=1, level_index=2, score=1009000, over_power=85, full_combo='alljusticecritical'),
        dict(id=1, level_index=3, score=1008000, over_power=79, full_combo='alljustice'),
        dict(id=1, level_index=4, score=1009000, over_power=82, full_combo='alljusticecritical'),
        dict(id=2, level_index=3, score=0, over_power=0, clear='failed'),
        dict(id=8, level_index=3, score=999000, over_power=60, full_combo='fullcombo'),
    ]
    rows = cp.eligible_charts(base, scores)
    def matched(query):
        return op.matching_charts(rows, op.parse_filters(query, base))
    def keys(items):
        return [(row.song.id, row.chart.difficulty) for row in items]
    assert set(keys(matched('14+'))) == {(1,3), (1,4), (2,3), (3,3), (4,3)}
    assert keys(matched('14.9')) == [(1,3), (8,3), (2,3), (3,3)]
    assert keys(matched('14.899')) == [(4,3)]
    assert keys(matched('14+ sun 车万')) == [(1,3)]
    assert keys(matched('14+ sun plus touhou project')) == [(1,4)]
    assert keys(matched('14+ sun+ ori')) == []
    assert set(keys(matched('１４＋，ori'))) == {(2,3), (3,3), (4,3)}
    assert set(keys(matched('14+ --nocat'))) == set(keys(matched('14+')))
    assert cp.summarize(matched('14+ --nocat')) == cp.summarize(matched('14+'))
    assert op.expert_leaders(rows, matched('14+')) == set()  # EXP ties BASIC
    assert op.expert_leaders(cp.eligible_charts(base, scores[1:]), matched('14+')) == {(1,3)}

    chosen = {'name':'試験 Ω ♪ ' * 15, 'level':1, 'rating':2.4,
              'trophy':{'name':'称号・テスト ★ ' * 20}, 'class_emblem':{'base':1, 'medal':2},
              'over_power':321, 'over_power_progress':0.32}
    official, summary, levels = cp.profile_progress(chosen, rows)
    assert official == {'total_op':321, 'op_percent':0.32, 'estimated_catalog_ceiling':None}
    assert tuple(levels) == cp.LEVELS and len(levels) == 12
    assert levels['12']['chart_count'] == 0 and levels['12']['op_percent'] is None
    assert levels['14']['fc'] == 1
    assert (summary['chart_count'], summary['played'], summary['fc'], summary['aj'], summary['ajc']) == (6,4,3,2,1)
    assert summary['total_op'] == 221 and summary['theoretical_op'] == 536.495
    segments = Image.new('RGB', (602, 6))
    pi.achievement_track(segments, (0,0,602,6), summary)
    assert segments.getpixel((51,2)) == pi.rainbow(100,4).getpixel((50,1))
    for x,color in [(151,pi.BADGES['AJ'][0]), (251,pi.BADGES['FC'][0]), (451,'#d2dbdf')]:
        assert segments.getpixel((x,2)) == ImageColor.getrgb(color)

    small = replace(snapshot, player=chosen, scores=scores)
    with patch.object(pi, 'label', wraps=pi.label) as labels:
        result = await op.query_progress(small, '14+ ori', base)
    assert result['data']['played'] == 1 and result['data']['chart_count'] == 3
    assert any(call.args[2] == 0 for call in labels.call_args_list)
    assert sum(call.args[2] == '未游玩' for call in labels.call_args_list) == 2
    assert any(call.args[2] == 'D' for call in labels.call_args_list)
    assert any(call.args[2] == '缺曲绘' for call in labels.call_args_list)
    op.prefetch_jackets.assert_awaited_with({2:None, 3:None, 4:None})

    large = Catalog(dict(songs=[dict(id=i, title=f'Chart {i}', difficulties=[
        chart(constant=14.8 if i > 242 else 14.9)]) for i in range(1, 451)]))
    for invalid in ('', ',， |', '14+ --noca', 'ori--nocat', '14+ su', '14+ sun+ 東方', '14 14+', 'sun sun+',
                    'ori 车万', '14+ --page', '14+ --page -1', '14+ --page 0', '14+ --page 1 --page 2'):
        assert (await op.query_progress(small, invalid, base))['code'] == 'INVALID_ARGUMENT', invalid
    assert (await op.query_progress(small, '14.901', base))['code'] == 'NOT_FOUND'
    assert (await op.query_progress(small, '14 14.8', base))['code'] == 'NOT_FOUND'
    full = cp.eligible_charts(large, scores)
    pages = pi.paginate(full, grouped=True)
    assert [sum(len(group.rows) for group in page) for page in pages] == [200,200,50]
    assert [row for page in pages for group in page for row in group.rows] == full
    assert pages[0][0].stats == pages[1][0].stats
    many_groups = [replace(full[i], constant=Decimal(f'14.{i:02d}')) for i in range(40)]
    height_pages = pi.paginate(many_groups, grouped=True)
    assert len(height_pages) > 1
    assert [row for page in height_pages for group in page for row in group.rows] == many_groups
    for page_number in (1,2,3):
        with patch.object(op, 'draw_chulist_image', return_value=b'offline'):
            result = await op.query_progress(small, f'14+ --page {page_number}', large)
        assert result['data']['page'] == page_number and result['data']['page_count'] == 3
        assert result['data']['page_chart_count'] == (50 if page_number == 3 else 200)
        assert all(result['data'][key] == value for key,value in cp.summarize(full).items())
    result = await op.query_progress(small, '14+ --page 4', large)
    assert result['code']=='INVALID_ARGUMENT' and '1–3' in result['fallback_text']

async def run():
    global allowed,mode,started,release
    bind();bind('8')
    with patch.object(group_policy,'enabled',side_effect=lambda *a:allowed or getattr(a[0],'group_id',None) is None),patch.object(group_policy,'scope',side_effect=lambda *a:nullcontext()),patch.object(b,'prepare_jackets',AsyncMock(return_value={})),patch.object(rec,'download_jacket',AsyncMock(return_value=None)),patch.object(native_song,'download_jacket',AsyncMock(return_value=None)),patch.object(player,'player_images',AsyncMock(return_value={})),patch.object(profiles,'player_images',AsyncMock(return_value={})),patch.object(op,'player_images',AsyncMock(return_value={})),patch.object(op,'prefetch_jackets',AsyncMock()):
        snapshot=await reader.get(bot,e,'player.best')
        for cap,args in [('player.best',{}),('player.b50',{}),('player.profile',{}),('player.progress',{'query':'new'}),('player.recommend',{'filter':''}),('player.song_scores',{'query':'family-alias'})]:
            result=await player.query(bot,e,cap,args)
            assert result['status']=='ok',(cap,result)
            assert result['data']['unmatched_scores']==1
            if cap=='player.profile':
                assert result['data']['official_op']['op_percent']==12.5
                assert result['data']['master_ultima']['chart_count']==3
                assert result['artifacts'][0]['content'].startswith(b'\x89PNG')
            if cap=='player.progress':
                assert result['data']['page']==1
                assert result['artifacts'][0]['content'].startswith(b'\x89PNG')
            if cap=='player.best':
                assert result['data']['chart_count']==2
                assert abs(result['data']['b30_average']-1.09)<1e-9
                assert result['artifacts'][0]['content'].startswith(b'\x89PNG')
            if cap=='player.b50':
                assert result['data']['best30_count']==2 and result['data']['new20_count']==0
                assert abs(result['data']['rating_estimate']-.654)<1e-9
            if cap=='player.song_scores':
                assert {r['id'] for r in result['data']['scores']}=={10,8001}
                assert {r['level_index'] for r in result['data']['scores']}=={3,4,5}
                assert result['artifacts'][0]['content'].startswith(b'\x89PNG')
        assert len(api_calls)==2 # all consumers share one complete Player/Score[] snapshot.
        await check_progress(snapshot)
        with patch.object(paths, 'SONGLIST_PATH', root/'missing-catalog.json'):
            result = await player.query(bot,e,'player.profile',{})
            assert result['code']=='OK' and result['data']['master_ultima'] is None
            assert result['data']['official_op']['op_percent']==12.5
            assert result['artifacts'][0]['content'].startswith(b'\x89PNG')
        result=await player.query(bot,e,'player.song_scores',{'query':'8001 we'})
        assert [r['id'] for r in result['data']['scores']]==[8001]
        assert result['data']['song_id']==10
        assert '不计普通 Rating / OP' in result['public_hint']
        assert 'rating' not in result['data']['scores'][0] and result['data']['scores'][0]['standard_op_applicable'] is False
        assert 'over_power' not in result['data']['scores'][0]['upstream_metrics']
        assert [r['id'] for r in (await player.query(bot,e,'player.song_scores',{'query':'8001'}))['data']['scores']]==[8001]
        with patch.object(song_images, 'score_digits', wraps=song_images.score_digits) as digits:
            result=await player.query(bot,e,'player.song_scores',{'query':'10 basic'})
            digits.assert_not_called()
        assert result['code']=='NO_SONG_SCORES' and not result['data']['scores']
        assert result['artifacts'][0]['content'].startswith(b'\x89PNG')
        empty_event=e.model_copy(update={'message_id': 204})
        empty_job=await media_jobs.submit(bot,empty_event,'player.song_scores',{'query':'10 basic'},origin='command')
        empty_job=await wait(empty_job['data']['job_id'])
        assert empty_job.result['code']=='NO_SONG_SCORES'
        assert empty_job.result['data']['artifact_delivery']=='sent'

        # Empty assets and system fonts still preserve score 0, badge slots,
        # and the identities of simultaneously rendered OAuth players.
        with patch.object(paths, 'ASSETS_DIR', root/'absent-assets'), patch.object(image_components,'font_faces',return_value=()):
            image_components.ui.cache_clear()
            image_components._spacing.cache_clear()
            image_components._face.cache_clear()
            try:
                low = dict(snapshot.scores[0], score=0, rank='d', clear='failed', rating=0, over_power=0, full_combo=None)
                snapshot_low=replace(snapshot,scores=[low])
                with patch.object(song_images, 'score_digits', wraps=song_images.score_digits) as digits:
                    rendered = await player.song_scores(snapshot_low, Catalog(catalog), '10')
                    assert digits.call_args.args[-1]==0
                assert rendered['data']['scores'][0]['score']==0
                left,right=await asyncio.gather(
                    player.query(bot,e,'player.song_scores',{'query':'10'}),
                    player.query(bot,event(8),'player.song_scores',{'query':'10'}))
                assert left['artifacts'][0]['content']!=right['artifacts'][0]['content']
                for cap,args in [('player.profile',{}),('player.progress',{'query':'old'})]:
                    left,right=await asyncio.gather(player.query(bot,e,cap,args), player.query(bot,event(8),cap,args))
                    assert all(result['status']=='ok' for result in (left,right))
                    assert left['artifacts'][0]['content']!=right['artifacts'][0]['content']
                reader.invalidate('1','8')
            finally:
                image_components.ui.cache_clear()
                image_components._spacing.cache_clear()
                image_components._face.cache_clear()
        # Same-ID WE metadata remains a list, never last-write-wins by difficulty.
        saved_catalog=paths.SONGLIST_PATH.read_bytes()
        multiple=json.loads(saved_catalog)
        multiple['songs'][2]['difficulties'].append(dict(multiple['songs'][2]['difficulties'][0],kanji='別',version=24000))
        paths.SONGLIST_PATH.write_text(json.dumps(multiple),encoding='utf-8')
        result=await player.query(bot,e,'player.song_scores',{'query':'8001 we'})
        assert result['data']['scores'][0]['metadata_ambiguous']
        assert len(result['data']['scores'][0]['charts'])==2
        paths.SONGLIST_PATH.write_bytes(saved_catalog)
        raw=snapshot.scores[1]
        meta=b.build_chart_meta(fixture()['songs'])
        rendered=b.build_item(meta[('10',4)],raw)
        assert rendered['full_combo']=='' and rendered['full_chain']=='fullchain'
        result=await player.query(bot,e,'player.song_scores',{'query':'Same title'})
        assert result['code']=='AMBIGUOUS_TARGET'
        specs=llm_api.describe_capabilities();assert len(specs)==25 and llm_api.CAPABILITY_VERSION=='4.1.0'
        assert not {'player.bind','score.submit','score.sync','score.import_file'} & {s['id'] for s in specs}
        assert next(s for s in specs if s['id']=='player.best')['native_commands'][0].get('aliases',[])==[]
        for method in [llm_api.prepare_capability,llm_api.invoke_capability]:
            for retired in ['player.bind','score.submit','score.sync','score.import_file']:
                assert (await method(retired,{},dict(bot=bot,event=e,confirmed=True)))['code']=='CAPABILITY_RETIRED'
        assert (await llm_api.invoke_capability('player.best',{'mode':'b','version':'2026'},dict(bot=bot,event=e)))['code']=='INVALID_ARGUMENT'
        context=await llm_api.describe_context(dict(bot=bot,event=e))
        assert context['player.best']['available'] and context['player.song_scores']['available']
        assert 'score.sync' not in context
        assert not (SOURCE/'nonebot_plugin_unibot/get_player_score.py').exists()
        assert not hasattr(sync,'_run_sync')
        # A real query job sends a score image under its captured binding.
        output=await media_jobs.submit(bot,e,'player.song_scores',{'query':'10'},origin='command')
        job=await wait(output['data']['job_id'])
        assert job.status=='succeeded',job.result
        assert sent and sent[-1][1]['message'][0].type=='image'
        assert job.result['data']['artifact_delivery']=='sent'
        # A failed query is never staged as successful text media. Its original
        # failure is sent exactly once by the native task notifier.
        failure=pd.failure(pd.PlayerError('INVALID_DATA'))
        staged=AsyncMock();before=len(sent)
        with patch.object(media_jobs,'render',AsyncMock(return_value=failure)):
            failed=await media_jobs.execute(bot,e,job,sync._store,NS(stage=staged))
        assert failed['code']=='INVALID_DATA' and 'artifact_delivery' not in failed['data']
        staged.assert_not_awaited()
        assert len(sent)==before
        with patch('nonebot.get_bot',return_value=bot):
            await sync._notify(replace(job,result=failed,status='failed'))
        assert len(sent)==before+1 and '查询结果已发送' not in str(sent[-1][1])
        assert await sync.private_job_allowed(job)
        # Notifiers may wait in another plugin; they must not retain raw player data.
        original_callback=llm_api._job_notifier
        notices=[]
        async def notice(row):
            notices.append(row);return {'silent':True}
        llm_api.set_job_notifier(notice)
        await sync._notify(replace(job,response_owner='agent'))
        assert notices and 'scores' not in notices[0].result.get('data',{})
        llm_api.set_job_notifier(original_callback)
        own=await sync.query(bot,e,'get',job.job_id);assert own['code']=='OK'
        other=await sync.query(bot,event(8),'get',job.job_id);assert other['code']=='JOB_NOT_FOUND'
        materials=await llm_api.get_job_materials(job.job_id,dict(bot=bot,event=event(8)));assert materials==[]
        # Revocation during the SECOND list metadata await must hide earlier rows too.
        second_event=event().model_copy(update={'message_id':201})
        second=await media_jobs.submit(bot,second_event,'player.song_scores',{'query':'10'},origin='command')
        await wait(second['data']['job_id'])
        original_position=sync._store.queue_position
        positions=0
        async def unlink_during_second_position(identifier):
            nonlocal positions
            positions+=1
            if positions==2:await auth.unlink('1','7')
            return await original_position(identifier)
        with patch.object(sync._store,'queue_position',side_effect=unlink_during_second_position):
            listing=await sync.query(bot,e,'list')
        assert positions>=2
        assert all(row['result']['code']=='AUTHORIZATION_CHANGED' for row in listing['data']['jobs'])
        assert '"scores"' not in json.dumps(listing)
        # Unbind disables cached media replay and redacts saved result payloads.
        await auth.unlink('1','7')
        before=len(sent)
        assert (await sync.replay(bot,e,job.job_id))['code']=='AUTHORIZATION_CHANGED'
        hidden=await sync.query(bot,e,'get',job.job_id)
        assert hidden['data']['result']['code']=='AUTHORIZATION_CHANGED'
        context=await llm_api.describe_context(dict(bot=bot,event=e))
        assert context['player.best']['unavailable_code']=='NOT_AUTHORIZED'
        assert 'USER-7' not in json.dumps(hidden)
        assert await llm_api.get_job_materials(job.job_id,dict(bot=bot,event=e))==[]
        assert (await sync._notify(job))=={'silent':True}
        assert len(sent)==before
        # A single result also checks authorization AFTER its final metadata await.
        bind()
        single_event=event().model_copy(update={'message_id':202})
        single=await media_jobs.submit(bot,single_event,'player.song_scores',{'query':'10'},origin='command')
        await wait(single['data']['job_id'])
        async def unlink_during_position(identifier):
            await auth.unlink('1','7')
            return await original_position(identifier)
        with patch.object(sync._store,'queue_position',side_effect=unlink_during_position):
            single_result=await sync.query(bot,e,'get',single['data']['job_id'])
        assert single_result['data']['result']['code']=='AUTHORIZATION_CHANGED'
        # A task queued with an old binding cannot execute after relinking.
        bind();_,old_binding=await auth.credential_lease('1','7')
        await auth.unlink('1','7');bind()
        result=await player.query(bot,e,'player.best',{},expected=old_binding)
        assert result['code']=='AUTHORIZATION_CHANGED'
        # Revocation while a background renderer runs also discards completed image bytes.
        original_draw=b.draw_b30_image
        def revoke_during_render(*args):
            store.unlink('1:7')
            return original_draw(*args)
        with patch.object(b,'draw_b30_image',side_effect=revoke_during_render):
            revoked=await player.query(bot,e,'player.best',{})
        assert revoked['code']=='AUTHORIZATION_CHANGED' and not revoked['artifacts']
        bind()
        original_progress=op.draw_chulist_image
        def revoke_during_progress(*args):
            store.unlink('1:7')
            return original_progress(*args)
        with patch.object(op,'draw_chulist_image',side_effect=revoke_during_progress):
            revoked=await player.query(bot,e,'player.progress',{'query':'old'})
        assert revoked['code']=='AUTHORIZATION_CHANGED' and not revoked['artifacts']
        bind()
        # Cancel during a live network request; late response never enters cache or sends.
        reader.invalidate('1','7');mode='pause';started.clear();release.clear()
        e2=event().model_copy(update={'message_id':200})
        output=await media_jobs.submit(bot,e2,'player.profile',{},origin='command')
        job_id=output['data']['job_id'];await asyncio.wait_for(started.wait(),5)
        cancel=await sync.query(bot,e2,'cancel',job_id)
        assert cancel['data']['status'] in {'cancelled','cancel_requested'}
        before=len(sent);release.set();job2=await wait(job_id)
        assert job2.status in {'cancelled','failed'},job2
        assert not reader.cache and len(sent)==before
        mode='ok'
        # Cancel during the first card; the remaining cards and PNG encoding must stop.
        entered=asyncio.Event()
        loop=asyncio.get_running_loop()
        original_card=pi.card
        cards=[]
        encoded=[]
        def paused_progress(*args):
            def paused_card(*card_args):
                cards.append(1)
                loop.call_soon_threadsafe(entered.set)
                assert args[-1].wait(5)
                return original_card(*card_args)
            with patch.object(pi,'card',side_effect=paused_card), patch.object(pi,'png',side_effect=lambda *_: encoded.append(True)):
                return original_progress(*args)
        progress_event=event().model_copy(update={'message_id':203})
        before=len(sent)
        with patch.object(op,'draw_chulist_image',side_effect=paused_progress):
            output=await media_jobs.submit(bot,progress_event,'player.progress',{'query':'old'},origin='command')
            job_id=output['data']['job_id']
            await asyncio.wait_for(entered.wait(),5)
            cancel=await sync.query(bot,progress_event,'cancel',job_id)
            assert cancel['data']['status'] in {'cancelled','cancel_requested'}
            progress_job=await wait(job_id)
        assert progress_job.status in {'cancelled','failed'} and progress_job.result['code']=='JOB_CANCELLED'
        assert len(cards)==1 and not encoded
        assert all(segment.type != 'image' for _,kwargs in sent[before:] for segment in kwargs['message'])
        # Original group policy must also guard old player result materials.
        group_job=replace(job,context_type='group',context_id='123')
        allowed=False
        assert not await pd.job_authorized(group_job)
        before_calls=len(api_calls);before_sent=len(sent)
        result=await media_jobs.submit(bot,g,'player.best',{})
        assert result['code']=='FEATURE_DISABLED' and len(api_calls)==before_calls and len(sent)==before_sent
        # Historical retired queue entries do not import the deleted implementation.
        result=await sync.submit_operation(bot,e,'score.sync','retired',{})
        assert result['code']=='CAPABILITY_RETIRED'
    await sync.stop_workers()
    assert {p.name:p.read_bytes() for p in legacy.iterdir() if p.is_file()}==legacy_before
    assert old_bind.read_bytes()==bind_before
asyncio.run(run())
print('PLAYER_HOST_OK')
'''
        with tempfile.TemporaryDirectory() as directory:
            env=dict(os.environ,PYTHONIOENCODING='utf-8',PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1')
            result=subprocess.run([sys.executable,'-c','from pathlib import Path\nSOURCE=Path('+repr(str(ROOT))+')\n'+script],cwd=directory,env=env,capture_output=True,text=True,encoding='utf-8',timeout=90)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('PLAYER_HOST_OK',result.stdout)


if __name__=='__main__':unittest.main()
