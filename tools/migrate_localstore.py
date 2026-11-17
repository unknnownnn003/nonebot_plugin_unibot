"""Offline migration without importing UniBot or connecting an adapter."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1] / "nonebot_plugin_unibot/data")
    parser.add_argument("--apply", action="store_true", help="Copy, verify and publish; default is read-only plan")
    parser.add_argument("--offline", action="store_true", help="Confirm no Bot/other process is writing source or target")
    parser.add_argument("--data-root", type=Path, help="Explicit LocalStore base data directory for this host")
    args = parser.parse_args()
    if args.apply and not args.offline:
        parser.error("--apply requires --offline; stop all writers first")
    # Use the installed LocalStore configuration, including plugin override.
    # No UniBot module is loaded, no startup hook or adapter is executed.
    import nonebot
    options = {"localstore_data_dir": str(args.data_root.resolve())} if args.data_root else {}
    nonebot.init(driver="~none", **options)
    nonebot.require("nonebot_plugin_localstore")
    import nonebot_plugin_localstore as store
    target = store.plugin_config.localstore_plugin_data_dir.get("nonebot_plugin_unibot")
    if target is None:
        target = store.BASE_DATA_DIR / "nonebot_plugin_unibot"
    spec = importlib.util.spec_from_file_location("unibot_offline_migration", Path(__file__).resolve().parents[1] / "nonebot_plugin_unibot/services/data_migration.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    report = migration.migrate(args.source, target) if args.apply else migration.plan(args.source, target)
    print(json.dumps({key: report.get(key) for key in ("state", "source", "target", "file_count", "total_bytes", "already_complete", "previous_target")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
