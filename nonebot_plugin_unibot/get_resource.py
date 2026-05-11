import asyncio
import json
import os

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
BASE_URL = "https://assets.lxns.net/chunithm/jacket/{song_id}.png"
LXNS_CONCURRENCY = 15

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


async def write_bytes_atomic(path: str, content: bytes):
    temp_path = path + ".tmp"
    if aiofiles:
        async with aiofiles.open(temp_path, "wb") as f:
            await f.write(content)
    else:
        with open(temp_path, "wb") as f:
            f.write(content)
    os.replace(temp_path, path)


@update_jacket.handle()
async def _(msg: Message = CommandArg()):
    await update_jacket.send("收到，正在处理...")

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
        await update_jacket.send("读取曲目列表失败，请先获取或更新曲目列表！")
        return

    targets = {}
    skipped_chunirec_count = 0
    for song in songs:
        song_id = song.get("id")
        for diff in song.get("difficulties", []) or []:
            if diff.get("difficulty") in (4, 5):
                song_id = diff.get("origin_id", song_id)
                break

        if song_id is not None:
            song_id_str = str(song_id)
            if not should_use_lxns_jacket(song_id_str):
                skipped_chunirec_count += 1
                continue
            targets[song_id_str] = song

    total_count = len(targets)
    await update_jacket.send("收到，正在处理...")

    lxns_semaphore = asyncio.Semaphore(LXNS_CONCURRENCY)
    success_count = 0
    skip_count = 0
    fail_count = 0

    async def fetch_and_save(client: httpx.AsyncClient, sid: str, song: dict):
        nonlocal success_count, skip_count, fail_count
        file_path = os.path.join(JACKET_DIR, f"{sid}.png")
        if is_existing_jacket_valid(file_path):
            skip_count += 1
            return

        lxns_url = BASE_URL.format(song_id=sid)
        try:
            content = None
            async with lxns_semaphore:
                response = await client.get(lxns_url, timeout=10.0)
                if response.status_code == 200 and looks_like_png(response.content):
                    content = response.content
                else:
                    logger.warning(f"下载曲绘失败 (状态码 {response.status_code}, 大小 {len(response.content)}): {lxns_url}")

            if content and looks_like_png(content):
                await write_bytes_atomic(file_path, content)
                success_count += 1
            else:
                fail_count += 1
        except Exception as e:
            fail_count += 1
            logger.warning(f"下载曲绘出错: {lxns_url} - {type(e).__name__}: {e!r}")

    async with httpx.AsyncClient() as client:
        tasks = [fetch_and_save(client, sid, song) for sid, song in targets.items()]
        await asyncio.gather(*tasks)

    await update_jacket.send(
        f"曲绘更新任务完成！\n成功下载：{success_count}\n跳过已有：{skip_count}\n"
        f"跳过日服曲目：{skipped_chunirec_count}\n下载失败：{fail_count}"
    )
