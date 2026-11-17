import asyncio
import json
from datetime import datetime
from pathlib import Path

import aiofiles
import httpx
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot import on_command
from nonebot.log import logger
from nonebot.permission import SUPERUSER

API_BASE_URL = "https://maimai.lxns.net/api/v0/chunithm"
ASSETS_BASE_URL = "https://assets.lxns.net/chunithm"

from .services.paths import DATA_DIR
# LocalStore owns the runtime root; preserve the existing file layout.
SONGLIST_FILE = DATA_DIR / "songlist.json"

COLLECTIONS = {
    "trophy": {
        "key": "trophies",
        "path": "/trophy/{id}.png",
    },
    "character": {
        "key": "characters",
        "path": "/character/{id}.png",
    },
    "plate": {
        "key": "plates",
        "path": "/plate/{id}.png",
    },
    "icon": {
        "key": "icons",
        "path": "/icon/{id}.png",
    },
}

update_collection = on_command("更新收藏品", permission=SUPERUSER, priority=5, block=True)


def save_json_atomic(path: Path, data: dict):
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        f.write("\n")
    temp_path.replace(path)


def looks_like_png(content: bytes) -> bool:
    return len(content) > 8 and content.startswith(b"\x89PNG\r\n\x1a\n")


def load_versions() -> list[int]:
    versions_list = [23000]
    try:
        if SONGLIST_FILE.exists():
            with open(SONGLIST_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            versions_data = data.get("versions", [])
            if versions_data:
                versions = [v.get("version") for v in versions_data if isinstance(v, dict)]
                versions_list = sorted({int(v) for v in versions if v is not None})
    except Exception as e:
        logger.warning(f"读取 songlist.json 时出错，将使用默认版本列表 {versions_list}: {e}")
    return versions_list


@update_collection.handle()
async def handle_update_collection(bot: Bot, event: MessageEvent):
    from .services.maintenance import submit
    result = await submit(bot, event, "collections", origin="command")
    from .services.results import send_command_result
    await send_command_result(update_collection, result, finish=False)
