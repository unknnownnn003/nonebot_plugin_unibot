"""Run existing sibling contracts with explicit temporary fixture dependencies."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULES = ('test_provider_services', 'test_context_catalog', 'test_sync_runtime',
           'test_story_jobs', 'test_maintenance', 'test_command_receipts', 'test_error_notices')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tests-dir', type=Path,
                        default=ROOT.parent/'nonebot_plugin_llmchat/tests')
    args = parser.parse_args()
    if not args.tests_dir.is_dir():
        parser.error('Existing LLMChat tests directory required')
    logs = ROOT/'.tools/neighbor-regressions'
    logs.mkdir(parents=True, exist_ok=True)
    for name in MODULES:
        # These fixtures replace business modules without loading a host. Supply
        # the new upload cache, and explicit allowed policy for tests exercising
        # story/receipt behavior. This is not a policy authorization test.
        script = f'''
import unittest, tempfile, sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
policy=lambda: NS(checked=lambda f:f, enabled=lambda *a:True,
    scope=lambda *a:nullcontext(), check=lambda:None, is_disabled=lambda e:False)
with tempfile.TemporaryDirectory() as temp:
    with patch.dict(sys.modules, {{
        'v31_uni.services.paths':NS(UPLOAD_DIR=Path(temp)/'uploads'),
        'story_fixture.services.group_policy':policy(),
        'command_fixture.services.group_policy':policy()}}):
        result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName({name!r}))
        sys.exit(not result.wasSuccessful())
'''
        result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script],
            cwd=args.tests_dir, env=dict(os.environ, PYTHONPYCACHEPREFIX=str(logs.parent/'pycache')),
            capture_output=True, text=True, encoding='utf-8', timeout=60)
        output = result.stdout + result.stderr
        (logs/f'{name}.txt').write_text(output, encoding='utf-8')
        summary = next((line for line in output.splitlines() if line.startswith('Ran ')), 'No test summary')
        print(f'{name}: {summary}; exit={result.returncode}', flush=True)
        if result.returncode:
            raise SystemExit(f'Failure; inspect {logs/name}.txt')


if __name__ == '__main__':
    main()
