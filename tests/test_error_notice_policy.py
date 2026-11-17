"""UniBot opts out without disabling the shared sibling recall mechanism."""
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock


class ErrorNoticePolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_unibot_errors_stay_visible_and_restore_shared_marker(self):
        path=Path(__file__).resolve().parents[1]/'nonebot_plugin_unibot/services/error_notices.py'
        spec=importlib.util.spec_from_file_location('unibot_notice_policy',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        class Bot:
            hooks=[]
            self_id='1'
            delete_msg=AsyncMock()
            @classmethod
            def on_called_api(cls,call):cls.hooks.append(call)
            async def send(self,**data):
                for hook in self.hooks:
                    await hook(self,None,'send_group_msg',data,{'message_id':17})
        bot=Bot();runtime=module.install(bot_class=Bot,driver=NS(on_shutdown=lambda call:None))
        token=runtime.marked.set(True)
        try:
            await module.notice_call(bot.send,group_id=9,message=module.error_text('错误'))
            await module.result_call({'status':'failed'},bot.send,group_id=9,message='失败')
            self.assertTrue(runtime.marked.get())
            self.assertFalse(runtime.pending)
            bot.delete_msg.assert_not_awaited()
            # A sibling's marker still schedules its own message deletion.
            runtime.delay=0
            await bot.send(group_id=9,message='sibling error')
            await asyncio.gather(*list(runtime.pending.values()))
            bot.delete_msg.assert_awaited_once_with(message_id=17)
        finally:
            runtime.marked.reset(token)
            await runtime.close()
