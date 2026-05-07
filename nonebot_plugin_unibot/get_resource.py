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

update_jacket = on_command("更新曲绘", permission=SUPERUSER, priority=5, block=True)


def looks_like_png(content: bytes) -> bool:
    return len(content) > 8 and content.startswith(b"\x89PNG\r\n\x1a\n")


def is_existing_jacket_valid(path: str) -> bool:
    if not os.path.exists(path) or os.path.getsize(path) <= 8:
        return False
    try:
        with open(path, "rb") as f:
            return looks_like_png(f.read(8))
    except Exception:
        return False


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
    await update_jacket.send("开始获取更新曲绘，请稍候...")

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

    target_ids = set()
    for song in songs:
        song_id = song.get("id")
        for diff in song.get("difficulties", []) or []:
            if diff.get("difficulty") in (4, 5):
                song_id = diff.get("origin_id", song_id)
                break

        if song_id is not None:
            target_ids.add(song_id)

    total_count = len(target_ids)
    await update_jacket.send(f"共扫描到带有曲绘的曲目 {total_count} 首，正在检查并下载...")

    semaphore = asyncio.Semaphore(15)
    success_count = 0
    skip_count = 0
    fail_count = 0

    async def fetch_and_save(client: httpx.AsyncClient, sid: int):
        nonlocal success_count, skip_count, fail_count
        file_path = os.path.join(JACKET_DIR, f"{sid}.png")
        if is_existing_jacket_valid(file_path):
            skip_count += 1
            return

        url = BASE_URL.format(song_id=sid)
        async with semaphore:
            try:
                response = await client.get(url, timeout=10.0)
                if response.status_code == 200 and looks_like_png(response.content):
                    await write_bytes_atomic(file_path, response.content)
                    success_count += 1
                else:
                    fail_count += 1
                    logger.warning(f"下载曲绘失败 (状态码 {response.status_code}, 大小 {len(response.content)}): {url}")
            except Exception as e:
                fail_count += 1
                logger.error(f"下载曲绘出错: {url} - {str(e)}")

    async with httpx.AsyncClient() as client:
        tasks = [fetch_and_save(client, sid) for sid in target_ids]
        await asyncio.gather(*tasks)

    await update_jacket.send(
        f"曲绘更新任务完成！\n成功下载：{success_count}\n跳过已有：{skip_count}\n下载失败：{fail_count}"
    )
