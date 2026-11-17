"""The only runtime path authority; LocalStore chooses the plugin namespace."""
from pathlib import Path

from nonebot import require

require("nonebot_plugin_localstore")
import nonebot_plugin_localstore as store

from .data_migration import require_migrated

LEGACY_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DATA_DIR = store.get_plugin_data_dir().resolve()
CACHE_DIR = store.get_plugin_cache_dir().resolve()
require_migrated(LEGACY_DATA_DIR, DATA_DIR)

# Keep assets durable: deleting LocalStore's disposable cache must never delete
# the only copy of a jacket, story, user score, or presentation awaiting replay.
SCORE_DIR = DATA_DIR / "score"
SONGLIST_PATH = DATA_DIR / "songlist.json"
JACKET_DIR = DATA_DIR / "jacket"
STORY_DIR = DATA_DIR / "story"
UPLOAD_DIR = CACHE_DIR / "uploads"
