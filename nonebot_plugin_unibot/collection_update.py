import asyncio
import json
from datetime import datetime
from pathlib import Path

import aiofiles
import httpx
from nonebot import on_command
from nonebot.log import logger
from nonebot.permission import SUPERUSER

API_BASE_URL = "https://maimai.lxns.net/api/v0/chunithm"
ASSETS_BASE_URL = "https://assets.lxns.net/chunithm"

PLUGIN_DIR = Path(__file__).parent
DATA_DIR = PLUGIN_DIR / "data"
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
async def _():
    await update_collection.send("开始获取并更新收藏品数据，请稍候...")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    versions_list = load_versions()
    semaphore = asyncio.Semaphore(15)

    async def fetch_and_save_asset(client: httpx.AsyncClient, col_type: str, item_id: int, save_dir: Path):
        file_path = save_dir / f"{item_id}.png"
        if file_path.exists() and file_path.stat().st_size > 8:
            return "skip"

        url = f"{ASSETS_BASE_URL}{COLLECTIONS[col_type]['path'].format(id=item_id)}"
        async with semaphore:
            try:
                response = await client.get(url, timeout=10.0)
                if response.status_code == 404:
                    return "404"
                if response.status_code == 200 and looks_like_png(response.content):
                    temp_file = file_path.with_suffix(".png.tmp")
                    async with aiofiles.open(temp_file, "wb") as f:
                        await f.write(response.content)
                    temp_file.replace(file_path)
                    return "success"
                logger.warning(f"下载 {col_type} (ID: {item_id}) 失败: status={response.status_code}, size={len(response.content)}")
                return "fail"
            except Exception as e:
                logger.error(f"下载 {col_type} (ID: {item_id}) 失败: {e}")
                return "fail"

    summary = []
    async with httpx.AsyncClient() as client:
        for col_type, info in COLLECTIONS.items():
            all_items = {}
            request_failures = 0

            for version in versions_list:
                api_url = f"{API_BASE_URL}/{col_type}/list?version={version}"
                try:
                    resp = await client.get(api_url, timeout=30.0)
                    resp.raise_for_status()
                    col_data = resp.json()
                    items = col_data.get(info["key"], []) if isinstance(col_data, dict) else []
                    if not isinstance(items, list):
                        request_failures += 1
                        logger.warning(f"获取 {col_type} (version: {version}) 返回格式异常")
                        continue
                    for item in items:
                        if isinstance(item, dict) and item.get("id") is not None:
                            all_items[item["id"]] = item
                except Exception as e:
                    request_failures += 1
                    logger.warning(f"获取 {col_type} (version: {version}) 列表失败: {e}")

            if not all_items:
                msg = f"{col_type}: 未获取到有效数据，保留本地旧文件"
                logger.error(msg)
                summary.append(msg)
                continue

            merged_data = {info["key"]: list(all_items.values())}
            json_path = DATA_DIR / f"{col_type}.json"
            save_json_atomic(json_path, merged_data)

            img_dir = DATA_DIR / col_type
            img_dir.mkdir(parents=True, exist_ok=True)

            tasks = [fetch_and_save_asset(client, col_type, item_id, img_dir) for item_id in all_items.keys()]
            results = await asyncio.gather(*tasks)

            success_c = results.count("success")
            skip_c = results.count("skip")
            fail_c = results.count("fail")
            not_found_c = results.count("404")

            logger.info(
                f"{col_type} 资源更新完毕！数据总量: {len(all_items)}, 下载成功: {success_c}, "
                f"跳过: {skip_c}, 不存在(或无图): {not_found_c}, 失败: {fail_c}, 接口失败: {request_failures}"
            )
            summary.append(
                f"{col_type}: 数据 {len(all_items)}，下载 {success_c}，跳过 {skip_c}，404 {not_found_c}，失败 {fail_c}，接口失败 {request_failures}"
            )

    await update_collection.send(
        "所有收藏品列表及资源已更新处理完毕！\n"
        + "\n".join(summary)
        + f"\n完成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
