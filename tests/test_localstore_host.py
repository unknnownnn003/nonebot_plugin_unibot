"""Real NoneBot + LocalStore, copied plugin and synthetic data; no connection."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LocalStoreHostTests(unittest.TestCase):
    def test_real_plugin_load_paths_tasks_and_policy(self):
        script = r'''
import asyncio, importlib.util, json, shutil, sqlite3
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
import nonebot
root = Path.cwd()
package = root / 'nonebot_plugin_unibot'
shutil.copytree(SOURCE / 'nonebot_plugin_unibot', package, ignore=shutil.ignore_patterns('data', '__pycache__'))
legacy = package / 'data'
(legacy / 'score').mkdir(parents=True)
(legacy / 'songlist.json').write_text('{"songs":[],"versions":[]}', encoding='utf-8')
(legacy / 'user_bind_info.json').write_text('{}')
nonebot.init(driver='~none',  localstore_data_dir=str(root/'persistent'),
             localstore_cache_dir=str(root/'cache'), localstore_config_dir=str(root/'config'),
             localstore_plugin_data_dir={'nonebot_plugin_unibot':str(root/'overridden-data')},
             localstore_plugin_cache_dir={'nonebot_plugin_unibot':str(root/'overridden-cache')})
spec=importlib.util.spec_from_file_location('offline',package/'services/data_migration.py')
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
target=root/'overridden-data'
m.migrate(legacy,target)
before=m.inventory(legacy)
plugin=nonebot.load_plugin('nonebot_plugin_unibot')
assert plugin is not None
from nonebot_plugin_unibot.services import paths, sync, group_policy
from nonebot_plugin_unibot import llm_api
assert paths.DATA_DIR==target
assert paths.CACHE_DIR==root/'overridden-cache'
for name in plugin.module.__all__:
    module=getattr(plugin.module,name)
    if hasattr(module,'DATA_DIR'):
        assert Path(module.DATA_DIR)==(target/'story' if name=='story' else target),name
assert llm_api.API_VERSION==1 and llm_api.CAPABILITY_VERSION=='4.3.0'
assert len(llm_api.describe_capabilities()) > 20
assert not group_policy.enabled(NS(group_id=7),'score.sync')
assert group_policy.enabled(NS(),'score.sync')
async def run():
    from nonebot_plugin_unibot.services.command_receipts import _receipts
    assert _receipts.path==target/'native_commands.sqlite3'
    await _receipts.init()
    store=await sync.ensure_started()
    assert Path(store.path)==target/'native_jobs.sqlite3'
    assert Path(sync._presentations.path)==target/'native_presentations.sqlite3'
    await sync.stop_workers()
asyncio.run(run())
assert (target/'native_commands.sqlite3').is_file()
assert m.inventory(legacy)==before
assert not (legacy/'native_jobs.sqlite3').exists()
print('LOCALSTORE_HOST_OK')
'''
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run([sys.executable, '-X', 'utf8', '-c',
                f'SOURCE=__import__("pathlib").Path({str(ROOT)!r})\n' + script],
                cwd=directory, env=dict(os.environ, PYTHONPYCACHEPREFIX=str(Path(directory)/'pycache')),
                capture_output=True, text=True, encoding='utf-8', timeout=60)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertIn('LOCALSTORE_HOST_OK', completed.stdout)
