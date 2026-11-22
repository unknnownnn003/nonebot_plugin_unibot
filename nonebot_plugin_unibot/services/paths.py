"""The only runtime path authority; LocalStore chooses the plugin namespace."""
import os
from pathlib import Path

from nonebot import require

require("nonebot_plugin_localstore")
import nonebot_plugin_localstore as store

from .data_migration import require_migrated

PACKAGE_DIR = Path(__file__).resolve().parents[1]
LEGACY_DATA_DIR = PACKAGE_DIR / "data"
DATA_DIR = store.get_plugin_data_dir().resolve()
CACHE_DIR = store.get_plugin_cache_dir().resolve()
require_migrated(LEGACY_DATA_DIR, DATA_DIR)

# Keep assets durable: deleting LocalStore's disposable cache must never delete
# the only copy of a jacket, story, user score, or presentation awaiting replay.
SONGLIST_PATH = DATA_DIR / "songlist.json"
JACKET_DIR = DATA_DIR / "jacket"
ASSETS_DIR = PACKAGE_DIR / "assets"
STORY_DIR = DATA_DIR / "story"
OAUTH_DIR = DATA_DIR / "oauth"
HISTORY_DIR = DATA_DIR / "historical_catalog"


def jp_history_database():
    configured = os.getenv("UNIBOT_JP_HISTORY_DB")
    if configured:
        path = Path(configured)
        if not path.is_absolute():
            raise ValueError("JP HDD database override must be absolute")
        return path
    return HISTORY_DIR / "catalog.sqlite"
