"""Run current sibling contracts in fresh processes; no retired assertion exclusions."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
MODULES=("test_provider_services","test_context_catalog","test_sync_runtime","test_story_jobs","test_maintenance","test_command_receipts","test_error_notices","test_read_services","test_query_media_jobs","test_command_feedback","test_agent_tools")

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tests-dir",type=Path,default=ROOT.parent/"nonebot_plugin_llmchat/tests")
    parser.add_argument("--modules",nargs="+",choices=MODULES,default=MODULES)
    args=parser.parse_args();logs=ROOT/".tools/neighbor-regressions";logs.mkdir(parents=True,exist_ok=True)
    for name in args.modules:
        result=subprocess.run([sys.executable,"-X","utf8","-m","unittest","discover","-s",str(args.tests_dir),"-p",name+".py","-v"],cwd=args.tests_dir.parent,
            env=dict(os.environ,PYTHONPYCACHEPREFIX=str(ROOT/".tools/pycache")),capture_output=True,text=True,encoding="utf8",timeout=90)
        output=result.stdout+result.stderr;(logs/f"{name}.txt").write_text(output,encoding="utf8")
        summary=next((line for line in output.splitlines() if line.startswith("Ran ")),"No test summary")
        print(f"{name}: {summary}; exit={result.returncode}",flush=True)
        if result.returncode:raise SystemExit(f"Failure; inspect {logs/name}.txt")

if __name__=="__main__":main()
