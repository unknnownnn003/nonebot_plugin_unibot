"""Run existing sibling contracts with explicit temporary fixture dependencies."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULES = ('test_provider_services', 'test_context_catalog', 'test_sync_runtime',
           'test_story_jobs', 'test_maintenance', 'test_command_receipts', 'test_error_notices')
# These assertions describe superseded contracts. Their replacements live in
# test_catalog_host.py/test_song_model.py/test_player_*.py. Do not alter the neighboring tests.
SUPERSEDED = {
    'test_b30_read_matches_original_migration_without_writing': 'retired local/manual score model; replaced by OAuth host tests',
    'test_bind_write_is_bound_to_supplied_user_id': 'retired friend-code writer; OAuth identity tests replace it',
    'test_score_import_uses_current_attachment_and_temp_file': 'retired score file importer',
    'test_native_alias_provenance_and_search': 'expects removed manual-score command cf; current provenance tested in UniBot',
    'test_uni_config_and_argument_availability_never_expose_tokens': 'expects developer token and dual Best modes; OAuth context tested in UniBot',
    'test_restart_old_queued_request_cannot_execute_new_user_or_notify_new_destination': 'expects active score.sync execution; current OAuth queue tested in UniBot',
    'test_task_query_and_cancel_are_full_scope_owned': 'calls removed sync.submit API; OAuth queue ownership/cancel tested in UniBot',
    'test_get_file_role_revocation_prevents_followup_http_request': 'removed score attachment reader',
    'test_cancelled_attachment_decoder_is_observed_until_thread_ends': 'removed score attachment decoder',
    'test_capability_catalog_matches_loaded_provider_definitions': 'expects sibling v3.2 static snapshot',
    'test_import_parser_caps_gzip_zip_expansion_and_nested_wrappers': 'removed foreign catalog upload parser',
    'test_lxns_workflow_uses_original_alias_transaction_and_preserves_local_alias': 'old AST fixture lacks model/required chart fields',
    'test_attachment_bytes_are_bounded_for_local_base64_and_no_model_path': 'expects retired foreign upload preview',
    'test_refresh_preflight_requires_admin_and_valid_full_refresh_scope': 'expects retired foreign-source configuration',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tests-dir', type=Path,
                        default=ROOT.parent/'nonebot_plugin_llmchat/tests')
    parser.add_argument('--modules', nargs='+', choices=MODULES, default=MODULES)
    parser.add_argument('--legacy-contracts', action='store_true', help='Also run the superseded assertions against the old sibling snapshot')
    args = parser.parse_args()
    if not args.tests_dir.is_dir():
        parser.error('Existing LLMChat tests directory required')
    logs = ROOT/'.tools/neighbor-regressions'
    logs.mkdir(parents=True, exist_ok=True)
    for name in args.modules:
        # Existing resource/story fixtures supply their own temporary roots.
        # Bind the current paths dependency to those roots; never load real data.
        # Explicit allow-policy here tests transactions, not authorization.
        script = f'''
import unittest, tempfile, sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace as NS, ModuleType
from unittest.mock import patch
policy=lambda: NS(checked=lambda f:f, enabled=lambda *a:True,
    scope=lambda *a:nullcontext(), check=lambda:None, is_disabled=lambda e:False)
def fixture_paths(alias):
    module=ModuleType(alias+'.services.paths')
    def value(name):
        if name=='DATA_DIR':
            return Path(sys.modules[alias+'.get_player_score'].SCORE_DIR).parent
        raise AttributeError(name)
    module.__getattr__=value
    return module
with tempfile.TemporaryDirectory() as temp:
    with patch.dict(sys.modules, {{
        'maint_uni.services.paths':fixture_paths('maint_uni'),
        'story_fixture.services.paths':fixture_paths('story_fixture'),
        'story_fixture.services.group_policy':policy(),
        'command_fixture.services.group_policy':policy()}}):
        suite=unittest.defaultTestLoader.loadTestsFromName({name!r})
        excluded={SUPERSEDED if not args.legacy_contracts else {}!r}
        def select(suite):
            for test in suite:
                if isinstance(test,unittest.TestSuite):
                    yield from select(test)
                elif test.id().rsplit('.',1)[-1] in excluded:
                    print('SUPERSEDED: '+test.id()+' — '+excluded[test.id().rsplit('.',1)[-1]])
                else:
                    yield test
        result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(select(suite)))
        sys.exit(not result.wasSuccessful())
'''
        result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script],
            cwd=args.tests_dir, env=dict(os.environ, PYTHONPYCACHEPREFIX=str(logs.parent/'pycache')),
            capture_output=True, text=True, encoding='utf-8', timeout=60)
        output = result.stdout + result.stderr
        (logs/f'{name}.txt').write_text(output, encoding='utf-8')
        summary = next((line for line in output.splitlines() if line.startswith('Ran ')), 'No test summary')
        for line in output.splitlines():
            if line.startswith('SUPERSEDED:'):
                print(line)
        print(f'{name}: {summary}; exit={result.returncode}', flush=True)
        if result.returncode:
            raise SystemExit(f'Failure; inspect {logs/name}.txt')


if __name__ == '__main__':
    main()
