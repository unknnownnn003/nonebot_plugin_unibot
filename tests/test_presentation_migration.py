import asyncio
import importlib
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from test_data_migration import m


class PresentationMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_database_paths_replay_only_from_new_owned_copy(self):
        folder = Path(__file__).resolve().parents[1] / 'nonebot_plugin_unibot/services'
        package = ModuleType('presentation_fixture')
        package.__path__ = [str(folder)]
        with patch.dict(sys.modules, {'presentation_fixture': package}):
            service = importlib.import_module('presentation_fixture.presentation_store')
            from nonebot.adapters.onebot.v11 import MessageSegment
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source, target = root/'legacy', root/'localstore'
                old = service.PresentationStore(source/'native_presentations.sqlite3')
                await old.init()
                image = old.files / 'fixture.png'
                image.write_bytes(b'owned-local-image')
                bot = NS(self_id='1', send=AsyncMock(return_value={'message_id':1}))
                event = NS(user_id=7)
                identity = service.scope(bot, event)
                file_row = await old.stage('file-job', identity, 'key', MessageSegment.image(image.resolve()))
                row = await old.stage('job', 'scope', 'key', MessageSegment.image(b'fixture-image'))
                before = m.inventory(source)
                m.migrate(source, target)
                # Original snapshot can be taken offline: replay reads the copy.
                source.rename(root/'retained-originals')
                current = service.PresentationStore(target/'native_presentations.sqlite3')
                self.assertEqual(current._read(row).data['file'], 'base64://Zml4dHVyZS1pbWFnZQ==')
                self.assertEqual(await current.model_images('job', 'scope'), [b'fixture-image'])
                self.assertEqual(await current.model_images('file-job', identity), [b'owned-local-image'])
                self.assertEqual((await current.send(file_row, bot, event))['delivery'], 'sent')
                self.assertEqual(bot.send.call_args.args[1].data['file'], (current.files/'fixture.png').resolve().as_uri())
                self.assertEqual(m.inventory(root/'retained-originals'), before)
                with self.assertRaises(ValueError):
                    current._owned_path(root/'unrelated.json')
