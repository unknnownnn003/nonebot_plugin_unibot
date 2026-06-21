import asyncio
import json
import os
import time

import httpx
from nonebot import on_command
from nonebot.adapters import Message
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
            if os.path.exists(path):
                os.remove(path)
                removed += 1
    return removed


@update_jacket.handle()
async def _(msg: Message = CommandArg()):
    full_refresh = should_full_refresh(msg)
    mode_text = "完整重下" if full_refresh else "增量更新"
    await update_jacket.send(f"收到，正在处理（{mode_text}）...")

    os.makedirs(JACKET_DIR, exist_ok=True)

    try:
        songlist_path = os.path.join(DATA_DIR, "songlist.json")
        with open(songlist_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        songs = data.get("songs", [])
        if not isinstance(songs, list) or not songs:
            await update_jacket.send("曲目列表为空或格式异常，请先更新 songlist。")
            return
    except Exception as e:
        logger.error(f"读取曲目列表失败: {e}")
        await update_jacket.send("读取曲目列表失败，请先获取或更新曲目列表。")
        return

    targets = {}
    for song in songs:
        song_id = song.get("jacket_id") or song.get("rinnet_id") or song.get("id")
        for diff in song.get("difficulties", []) or []:
            if diff.get("difficulty") in (4, 5):
                song_id = diff.get("origin_id", song_id)
                break

        if song_id is not None:
            song_id_str = str(song_id)
            if not should_use_lxns_jacket(song_id_str):
                continue
            targets[song_id_str] = song

    jacket_status = load_jacket_status()
    purged_count = 0
    if full_refresh:
        purge_ids = set(targets.keys()) | collect_chunirec_jacket_ids(songs)
        purged_count = purge_cached_jackets(purge_ids)
        for sid in purge_ids:
            jacket_status.pop(sid, None)

    lxns_semaphore = asyncio.Semaphore(LXNS_CONCURRENCY)
    success_count = 0
    skip_count = 0
    missing_count = 0
    fail_count = 0
    deferred_count = 0
    request_count = 0
    rate_limited_count = 0
    last_request_at = 0.0

    async def fetch_and_save(client: httpx.AsyncClient, sid: str) -> str:
        nonlocal success_count, missing_count, fail_count, rate_limited_count, last_request_at
        file_path = os.path.join(JACKET_DIR, f"{sid}.png")
        lxns_url = LXNS_JACKET_URL.format(song_id=sid)

        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            try:
                async with lxns_semaphore:
                    wait_seconds = LXNS_REQUEST_INTERVAL - (time.monotonic() - last_request_at)
                    if wait_seconds > 0:
                        await asyncio.sleep(wait_seconds)
                    response = await client.get(lxns_url, timeout=10.0)
                    last_request_at = time.monotonic()

                if response.status_code == 429:
                    rate_limited_count += 1
                    retry_after = parse_retry_after(response)
                    logger.warning(f"落雪曲绘接口限流，等待 {retry_after}s 后重试: {lxns_url}")
                    if attempt < MAX_RATE_LIMIT_RETRIES:
                        await asyncio.sleep(retry_after)
                        continue
                    mark_jacket_status(jacket_status, sid, "rate_limited", response.status_code)
                    return "rate_limited"

                if response.status_code == 200 and looks_like_png(response.content):
                    await write_bytes_atomic(file_path, response.content)
                    jacket_status.pop(sid, None)
                    success_count += 1
                    return "success"

                if response.status_code == 404:
                    missing_count += 1
                    mark_jacket_status(jacket_status, sid, "missing", response.status_code)
                    return "missing"

                logger.warning(f"下载曲绘失败 (状态码 {response.status_code}, 大小 {len(response.content)}): {lxns_url}")
                fail_count += 1
                mark_jacket_status(jacket_status, sid, "failed", response.status_code)
                return "failed"
            except Exception as e:
                fail_count += 1
                mark_jacket_status(jacket_status, sid, "failed", 0)
                logger.warning(f"下载曲绘出错: {lxns_url} - {type(e).__name__}: {e!r}")
                return "failed"

        fail_count += 1
        mark_jacket_status(jacket_status, sid, "failed", 0)
        return "failed"

    async with httpx.AsyncClient() as client:
        target_items = sorted(targets.items(), key=lambda item: int(item[0]))
        now = time.time()
        for index, (sid, _song) in enumerate(target_items):
            file_path = os.path.join(JACKET_DIR, f"{sid}.png")
            if is_existing_jacket_valid(file_path):
                skip_count += 1
                jacket_status.pop(sid, None)
                continue

            if not full_refresh and should_defer_jacket(jacket_status, sid, now):
                deferred_count += 1
                continue

            if not full_refresh and request_count >= MAX_INCREMENTAL_REQUESTS:
                deferred_count += len(target_items) - index
                break

            request_count += 1
            result = await fetch_and_save(client, sid)
            if result == "rate_limited":
                deferred_count += len(target_items) - index - 1
                break

    write_jacket_status(jacket_status)

    await update_jacket.send(
        f"曲绘更新任务完成！（{mode_text}）\n"
        f"成功下载：{success_count}\n"
        f"跳过已有：{skip_count}\n"
        f"本次请求：{request_count}\n"
        f"暂缓跳过：{deferred_count}\n"
        f"删除旧缓存：{purged_count}\n"
        f"落雪缺失：{missing_count}\n"
        f"下载失败：{fail_count}\n"
        f"触发限流：{rate_limited_count}"
    )
