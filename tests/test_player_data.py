"""Personal API with real OAuth/SQLite crypto; no actual account or network."""
import asyncio
import copy
import importlib
import types
import unittest
from unittest.mock import patch

import httpx
from nonebot.adapters.onebot.v11 import PrivateMessageEvent, GroupMessageEvent
import test_oauth as fixture_module
o = fixture_module.o

pd = importlib.import_module("oauth_fixture.player_data")
policy = importlib.import_module("oauth_fixture.group_policy")


def personal():
    player = dict(name="fixture-player", level=40, rating=15.2, rating_possession="rainbow",
        friend_code=1234567890, reborn_count=1, over_power=100, over_power_progress=12.5,
        currency=1000, total_currency=2000, total_play_count=30, class_emblem={"base": 1, "medal": 2},
        character={"id": 1, "name": "character", "level": 2}, trophy={"id": 1, "name": "trophy"},
        name_plate={"id": 1}, map_icon={"id": 1}, upload_time="2026-11-17T00:00:00Z", future_field={"keep": 1})
    def row(identifier, difficulty):
        return dict(id=identifier, song_name="fixture-song", level="14", level_index=difficulty,
            score=1009000, rating=16.35, over_power=82.5, clear="clear", full_combo="alljustice",
            full_chain=None, rank="sssp", play_time="2026-11-16T00:00:00Z", upload_time="2026-11-17T00:00:00Z",
            last_played_time="2026-11-16T00:00:00Z", future_score={"retained": True})
    return player, [row(10,3), row(10,4), row(8001,5), row(999999,3)]


def event(qq=7, bot=1, group=None):
    data=dict(time=0,self_id=bot,post_type="message",sub_type="normal" if group else "friend",user_id=qq,
        message_type="group" if group else "private",message_id=100+qq,message=[],raw_message="",font=0,sender={"user_id":qq})
    if group is not None:
        data["group_id"]=group
    return (GroupMessageEvent if group else PrivateMessageEvent)(**data)


class PersonalDataTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        fixture_module.OAuthTests.setUpClass.__func__(cls)

    setUp = fixture_module.OAuthTests.setUp
    response = fixture_module.OAuthTests.response
    provider = fixture_module.OAuthTests.provider
    begin = fixture_module.OAuthTests.begin
    bind = fixture_module.OAuthTests.bind

    async def asyncSetUp(self):
        await self.bind()
        self.bot=types.SimpleNamespace(self_id="1")
        self.event=event()
        self.payload=personal()
        self.personal_calls=[]
        self.personal_mode="ok"
        self.cached_time=0
        self.allowed=True
        self.started=asyncio.Event();self.release=asyncio.Event()
        self.reader=pd.PlayerData(self.service, transport=httpx.MockTransport(self.personal_provider),clock=lambda:self.cached_time)

    async def personal_provider(self, request):
        self.personal_calls.append(request)
        self.assertEqual(request.url.host,"maimai.lxns.net")
        self.assertNotIn("7",request.url.path)
        self.assertEqual(request.method,"GET")
        self.assertTrue(request.headers["Authorization"].startswith("Bearer access-subject"))
        if self.personal_mode=="late401":
            self.started.set();await self.release.wait()
            return httpx.Response(401,json={"detail":"SECRET-UPSTREAM"})
        if self.personal_mode=="envelope401":
            return httpx.Response(200,json={"success":False,"code":401,"data":None})
        if self.personal_mode=="401":
            return httpx.Response(401,json={"detail":"SECRET-UPSTREAM"})
        if self.personal_mode=="timeout":
            raise httpx.ReadTimeout("SECRET-UPSTREAM",request=request)
        if self.personal_mode=="429":
            return httpx.Response(429,json={"detail":"SECRET-UPSTREAM"})
        if self.personal_mode=="500":
            return httpx.Response(500,json={"detail":"SECRET-UPSTREAM"})
        if request.url.path.endswith("/scores"):
            if self.personal_mode=="pause":
                self.started.set();await self.release.wait()
            if self.personal_mode=="empty":
                return httpx.Response(200,json={"success":True,"code":200,"data":[]})
            if self.personal_mode=="bad":
                return httpx.Response(200,json={"success":False,"code":500,"data":self.payload[1]})
            return httpx.Response(200,json={"success":True,"code":200,"data":self.payload[1]})
        return httpx.Response(200,json={"success":True,"code":200,"data":self.payload[0]})

    async def get(self, **kwargs):
        return await self.reader.get(self.bot,self.event,"player.best",**kwargs)

    async def test_complete_fields_short_cache_and_copy_isolation(self):
        first=await self.get();second=await self.get()
        self.assertFalse(first.cache_hit);self.assertTrue(second.cache_hit)
        self.assertEqual(first.player,self.payload[0]);self.assertEqual(first.scores,self.payload[1])
        first.player["future_field"]["keep"]=999
        self.assertEqual((await self.get()).player["future_field"]["keep"],1)
        self.assertEqual(len(self.personal_calls),2)
        self.cached_time=61
        self.assertFalse((await self.get()).cache_hit)
        self.assertEqual(len(self.personal_calls),4)

    async def test_concurrent_reads_singleflight_and_user_bot_isolation(self):
        output=await asyncio.gather(*(self.get() for _ in range(12)))
        self.assertEqual(len(self.personal_calls),2)
        self.assertEqual(sum(not result.cache_hit for result in output),1)
        await self.bind("8")
        other=await self.reader.get(self.bot,event(8),"player.profile")
        self.assertNotEqual(other.binding,output[0].binding)
        self.assertEqual(len(self.personal_calls),4)
        with self.assertRaisesRegex(o.OAuthError,"NOT_AUTHORIZED"):
            await self.reader.get(types.SimpleNamespace(self_id="2"),event(bot=2),"player.best")
        with self.assertRaisesRegex(pd.PlayerError,"PERMISSION_DENIED"):
            await self.reader.get(types.SimpleNamespace(self_id="2"),self.event,"player.best")
        self.assertEqual(self.reader.locks,{})

    async def test_timeout_bad_empty_do_not_replace_last_valid_snapshot(self):
        await self.get();key=next(iter(self.reader.cache));valid=self.reader.cache[key]
        self.cached_time=61
        for mode,code in [("timeout","UPSTREAM_TIMEOUT"),("500","UPSTREAM_UNAVAILABLE"),("429","RATE_LIMITED"),("bad","INVALID_DATA"),("empty","NO_SCORES")]:
            self.personal_mode=mode
            with self.subTest(mode=mode),self.assertRaisesRegex(pd.PlayerError,"^"+code+"$"):
                await self.get()
            self.assertIs(self.reader.cache[key],valid)
        self.personal_mode="ok"
        await self.get()
        self.assertIsNot(self.reader.cache[key],valid)

    async def test_upstream_401_disables_credential_and_evicts_cache(self):
        await self.get();self.cached_time=61;self.personal_mode="401"
        with self.assertRaisesRegex(pd.PlayerError,"^UPSTREAM_UNAUTHORIZED$"):
            await self.get()
        self.assertEqual(self.reader.cache,{})
        self.assertEqual(await self.service.status("1","7"),"reauthorize")
        with self.assertRaisesRegex(o.OAuthError,"NOT_AUTHORIZED"):
            await self.get()

    async def test_late_401_cannot_revoke_new_token_or_erase_valid_snapshot(self):
        await self.get();self.cached_time=61
        key=next(iter(self.reader.cache));valid=self.reader.cache[key]
        self.personal_mode="late401"
        task=asyncio.create_task(self.get());await self.started.wait()
        self.now+=850
        await self.service.access_token("1","7")
        self.release.set()
        with self.assertRaisesRegex(pd.PlayerError,"AUTHORIZATION_CHANGED"):await task
        self.assertEqual(await self.service.status("1","7"),"active")
        self.assertIs(self.reader.cache[key],valid)

    async def test_unauthorized_response_envelope_also_invalidates_access(self):
        self.personal_mode="envelope401"
        with self.assertRaisesRegex(pd.PlayerError,"UPSTREAM_UNAUTHORIZED"):
            await self.get()
        self.assertEqual(await self.service.status("1","7"),"reauthorize")

    async def test_unlink_rebind_during_network_discards_late_old_response(self):
        self.personal_mode="pause"
        task=asyncio.create_task(self.get())
        await self.started.wait()
        await self.service.unlink("1","7")
        await self.bind()
        self.release.set()
        with self.assertRaisesRegex(pd.PlayerError,"AUTHORIZATION_CHANGED"):
            await task
        self.assertEqual(self.reader.cache,{})
        self.personal_mode="ok"
        new=await self.get()
        self.assertTrue(await self.service.binding_matches("1","7",new.binding))

    async def test_unlink_immediately_invalidates_cache_and_old_job_binding(self):
        old=await self.get()
        await self.service.unlink("1","7")
        self.assertEqual(self.reader.cache,{})
        await self.bind()
        with self.assertRaisesRegex(pd.PlayerError,"AUTHORIZATION_CHANGED"):
            await self.get(expected=old.binding)

    async def test_binding_stays_stable_across_refresh_but_not_rebinding(self):
        first=await self.get()
        self.now+=850
        again=await self.get()
        self.assertEqual(first.binding,again.binding)
        self.assertTrue(again.cache_hit)
        self.assertEqual(self.refresh_calls,1)
        await self.service.unlink("1","7");await self.bind()
        final=await self.get()
        self.assertNotEqual(final.binding,first.binding)

    async def test_cancellation_mid_network_does_not_cache_or_leave_lock(self):
        self.personal_mode="pause"
        task=asyncio.create_task(self.get())
        await self.started.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(self.reader.cache,{})
        self.assertEqual(self.reader.locks,{})

    async def test_checkpoint_cancel_before_cache_and_group_switch_during_fetch(self):
        self.personal_mode="pause"
        cancelled=False
        async def checkpoint():
            if cancelled:raise pd.PlayerError("JOB_CANCELLED")
        task=asyncio.create_task(self.get(checkpoint=checkpoint))
        await self.started.wait();cancelled=True;self.release.set()
        with self.assertRaisesRegex(pd.PlayerError,"JOB_CANCELLED"):await task
        self.assertEqual(self.reader.cache,{})
        self.event=event(group=123);self.personal_mode="ok"
        with patch.object(policy,"enabled",return_value=False):
            before=len(self.personal_calls)
            with self.assertRaisesRegex(pd.PlayerError,"FEATURE_DISABLED"):await self.get()
            self.assertEqual(len(self.personal_calls),before)
        self.personal_mode="pause";self.started.clear();self.release.clear()
        with patch.object(policy,"enabled",side_effect=lambda *a:self.allowed):
            task=asyncio.create_task(self.get());await self.started.wait();self.allowed=False;self.release.set()
            with self.assertRaisesRegex(pd.PlayerError,"FEATURE_DISABLED"):await task
        self.assertEqual(self.reader.cache,{})

    async def test_legacy_active_grant_gets_binding_without_losing_tokens(self):
        row=self.store.get("1:7");value=self.store.unpack(row["payload"]);value.pop("binding_id")
        with self.store.connection() as db:
            db.execute("UPDATE accounts SET payload=? WHERE owner='1:7'",(self.store.pack(value),))
        token,binding=await self.service.credential_lease("1","7")
        self.assertEqual(token,value["access_token"])
        self.assertEqual(len(binding),32)
        self.assertEqual((await self.service.credential_lease("1","7"))[1],binding)


class SnapshotValidationTests(unittest.TestCase):
    def test_worldsend_can_omit_inapplicable_metrics_without_inventing_values(self):
        p,s=personal()
        for key in ("rating", "over_power"):
            s[2].pop(key)
        self.assertEqual(pd.validate(p,s),(p,s))
        s[0].pop("over_power")
        with self.assertRaisesRegex(pd.PlayerError,"INVALID_DATA"):
            pd.validate(p,s)

    def test_validation_retains_unknown_fields_and_rejects_invalid_or_duplicate_rows(self):
        p,s=personal();p["trophy"]["id"]=-1;self.assertEqual(pd.validate(p,s),(p,s))
        for change in [{"id":"foreign-hash"},{"level_index":True},{"level_index":6},{"score":1010001},{"rating":float("nan")},{"rating":10**400},{"over_power":-1}]:
            invalid=copy.deepcopy(s);invalid[0].update(change)
            with self.subTest(change=change),self.assertRaisesRegex(pd.PlayerError,"INVALID_DATA"):
                pd.validate(p,invalid)
        with self.assertRaisesRegex(pd.PlayerError,"INVALID_DATA"):pd.validate(p,s+[s[0]])
        with self.assertRaisesRegex(pd.PlayerError,"NO_SCORES"):pd.validate(p,[])
        with self.assertRaisesRegex(pd.PlayerError,"INVALID_DATA"):pd.validate({},s)


if __name__=="__main__":unittest.main()
