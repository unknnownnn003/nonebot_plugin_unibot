import asyncio
import json
import os
import time

import httpx
from nonebot import on_command
from pathlib import Path
from nonebot.adapters.onebot.v11 import Message, Bot, MessageEvent
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

try:
    import aiofiles
except ImportError:
    aiofiles = None

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PLUGIN_DIR, "data")
JACKET_DIR = os.path.join(DATA_DIR, "jacket")
JACKET_STATUS_PATH = os.path.join(DATA_DIR, "jacket_status.json")
LXNS_JACKET_URL = "https://assets.lxns.net/chunithm/jacket/{song_id}.png"
LXNS_CONCURRENCY = 1
LXNS_REQUEST_INTERVAL = 1.0
MAX_INCREMENTAL_REQUESTS = 80
MISSING_RECHECK_SECONDS = 7 * 24 * 60 * 60
FAILED_RECHECK_SECONDS = 24 * 60 * 60
MAX_RATE_LIMIT_RETRIES = 2
MAX_RATE_LIMIT_SLEEP = 60
FULL_REFRESH_KEYWORDS = {
    "full",
    "force",
    "--full",
    "--force",
    "all",
    "refresh",
    "完整",
    "重下",
    "全量",
    "重新下载",
}

update_jacket = on_command("更新曲绘", permission=SUPERUSER, priority=5, block=True)


def looks_like_png(content: bytes) -> bool:
    return len(content) >= 8 and content.startswith(b"\x89PNG\r\n\x1a\n")


def is_existing_jacket_valid(path: str) -> bool:
    if not os.path.exists(path) or os.path.getsize(path) <= 8:
        return False
    try:
        with open(path, "rb") as f:
            return looks_like_png(f.read(8))
    except Exception:
        return False


def should_use_lxns_jacket(song_id: str) -> bool:
    return str(song_id).isdigit()


def should_full_refresh(msg: Message) -> bool:
    arg_text = msg.extract_plain_text().strip().lower()
    if not arg_text:
        return False
    return any(keyword in arg_text for keyword in FULL_REFRESH_KEYWORDS)


def load_jacket_status() -> dict:
    if not os.path.exists(JACKET_STATUS_PATH):
        return {}
    try:
        with open(JACKET_STATUS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"读取曲绘状态缓存失败: {e}")
        return {}


def write_jacket_status(status: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    temp_path = JACKET_STATUS_PATH + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(temp_path, JACKET_STATUS_PATH)


def mark_jacket_status(status: dict, sid: str, state: str, http_status: int = 0) -> None:
    status[sid] = {
        "state": state,
        "http_status": http_status,
        "checked_at": int(time.time()),
    }


def should_defer_jacket(status: dict, sid: str, now: float) -> bool:
    record = status.get(sid)
    if not isinstance(record, dict):
        return False
    state = record.get("state")
    try:
        checked_at = float(record.get("checked_at", 0))
    except (TypeError, ValueError):
        return False
    if state == "missing":
        return now - checked_at < MISSING_RECHECK_SECONDS
    if state in {"failed", "rate_limited"}:
        return now - checked_at < FAILED_RECHECK_SECONDS
    return False


def parse_retry_after(response: httpx.Response) -> int:
    value = response.headers.get("Retry-After", "")
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return MAX_RATE_LIMIT_SLEEP
    return max(1, min(seconds, MAX_RATE_LIMIT_SLEEP))


async def write_bytes_atomic(path: str, content: bytes) -> None:
    temp_path = path + ".tmp"
    if aiofiles:
        async with aiofiles.open(temp_path, "wb") as f:
            await f.write(content)
    else:
        with open(temp_path, "wb") as f:
            f.write(content)
    os.replace(temp_path, path)


def collect_chunirec_jacket_ids(songs: list) -> set[str]:
    ids: set[str] = set()
    for song in songs:
        if not isinstance(song, dict) or song.get("data_source") != "chunirec":
            continue
        for key in ("id", "jacket_id", "rinnet_id", "chunirec_id"):
            value = song.get(key)
            if value not in (None, ""):
                ids.add(str(value))
        for diff in song.get("difficulties", []) or []:
            if isinstance(diff, dict) and diff.get("origin_id") not in (None, ""):
                ids.add(str(diff["origin_id"]))
    return ids


def purge_cached_jackets(ids: set[str]) -> int:
    removed = 0
    for sid in ids:
        for ext in (".png", ".webp", ".jpg", ".jpeg"):
            path = os.path.join(JACKET_DIR, f"{sid}{ext}")
            if Path(path).resolve().parent != Path(JACKET_DIR).resolve():
                continue
            if os.path.exists(path):
                os.remove(path)
                removed += 1
    return removed


@update_jacket.handle()
async def handle_update_jacket(bot: Bot, event: MessageEvent, msg: Message = CommandArg()):
    from .services.maintenance import submit
    result = await submit(bot, event, "jackets", should_full_refresh(msg), origin="command")
    await update_jacket.send(result["public_hint"] + (f"\n任务ID：{result['data']['job_id']}" if result["data"].get("job_id") else ""))
