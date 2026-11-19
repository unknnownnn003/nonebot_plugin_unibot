"""Legacy collection inventory command; image downloads share the asset cache."""
import json

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.log import logger
from nonebot.permission import SUPERUSER

from .services.paths import DATA_DIR, SONGLIST_PATH

API_BASE_URL = "https://maimai.lxns.net/api/v0/chunithm"
COLLECTIONS = {"trophy": "trophies", "character": "characters", "plate": "plates", "icon": "icons"}

update_collection = on_command("更新收藏品", permission=SUPERUSER, priority=5, block=True)


def load_versions() -> list[int]:
    versions_list = [23000]
    try:
        if SONGLIST_PATH.exists():
            with open(SONGLIST_PATH, "r", encoding="utf-8") as f:
                from .services.song_model import active_catalog
                data = active_catalog(json.load(f))
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
