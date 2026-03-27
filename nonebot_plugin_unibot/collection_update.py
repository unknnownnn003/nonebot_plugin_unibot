import json
import httpx
import os
import aiofiles
import asyncio
from pathlib import Path
from nonebot import on_command
from nonebot.adapters import Message
from nonebot.permission import SUPERUSER
from nonebot.log import logger

# Base URLs
API_BASE_URL = "https://maimai.lxns.net/api/v0/chunithm"
ASSETS_BASE_URL = "https://assets.lxns.net/chunithm"

# Define Paths
PLUGIN_DIR = Path(__file__).parent
DATA_DIR = PLUGIN_DIR / "data"
SONGLIST_FILE = DATA_DIR / "songlist.json"

COLLECTIONS = {
    "trophy": {
        "key": "trophies",
        "path": "/trophy/{id}.png"
    },
    "character": {
        "key": "characters",
        "path": "/character/{id}.png"
    },
    "plate": {
        "key": "plates",
        "path": "/plate/{id}.png"
    },
    "icon": {
        "key": "icons",
        "path": "/icon/{id}.png"
    }
}

update_collection = on_command("更新收藏品", permission=SUPERUSER, priority=5, block=True)

@update_collection.handle()
async def _():
    await update_collection.send("开始获取并更新收藏品数据，请稍候...")
    
    if not DATA_DIR.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        
    # Get all versions from songlist.json
    versions_list = [23000]
    try:
        if SONGLIST_FILE.exists():
            with open(SONGLIST_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                versions_data = data.get("versions", [])
                if versions_data:
                    versions_list = [v.get("version", 0) for v in versions_data]
                    # Deduplicate and sort versions
                    versions_list = sorted(list(set(versions_list)))
    except Exception as e:
        logger.warning(f"读取 songlist.json 时出错，将使用默认版本列表 {versions_list}: {e}")

    semaphore = asyncio.Semaphore(15)
    
    async def fetch_and_save_asset(client: httpx.AsyncClient, col_type: str, item_id: int, save_dir: Path):
        file_path = save_dir / f"{item_id}.png"
        if file_path.exists():
            return "skip"
            
        url = f"{ASSETS_BASE_URL}{COLLECTIONS[col_type]['path'].format(id=item_id)}"
        async with semaphore:
            try:
                response = await client.get(url, timeout=10.0)
                if response.status_code == 200:
                    async with aiofiles.open(file_path, "wb") as f:
                        await f.write(response.content)
                    return "success"
                elif response.status_code == 404:
                    return "404"
                else:
                    return "fail"
            except Exception as e:
                logger.error(f"下载 {col_type} (ID: {item_id}) 失败: {e}")
                return "fail"

    async with httpx.AsyncClient() as client:
        for col_type, info in COLLECTIONS.items():
            all_items = {}
            try:
                for version in versions_list:
                    api_url = f"{API_BASE_URL}/{col_type}/list?version={version}"
                    # 获取JSON
                    resp = await client.get(api_url, timeout=30.0)
                    if resp.status_code == 200:
                        col_data = resp.json()
                        items = col_data.get(info["key"], [])
                        for item in items:
                            all_items[item.get("id")] = item
                    else:
                        logger.warning(f"获取 {col_type} (version: {version}) 列表失败: {resp.status_code}")

                # 将合并后的数据保存
                merged_data = {info["key"]: list(all_items.values())}
                
                # 保存JSON到 data文件夹下
                json_path = DATA_DIR / f"{col_type}.json"
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(merged_data, f, ensure_ascii=False, indent=4)
                    
                # 创建对应的图片保存目录
                img_dir = DATA_DIR / col_type
                if not img_dir.exists():
                    img_dir.mkdir(parents=True, exist_ok=True)
                
                # 开始下载资源
                tasks = [fetch_and_save_asset(client, col_type, item_id, img_dir) for item_id in all_items.keys()]
                results = await asyncio.gather(*tasks)
                
                success_c = results.count("success")
                skip_c = results.count("skip")
                fail_c = results.count("fail")
                not_found_c = results.count("404")
                
                logger.info(f"{col_type} 资源更新完毕！数据总量: {len(all_items)}, 下载成功: {success_c}, 跳过: {skip_c}, 不存在(或无图): {not_found_c}, 失败: {fail_c}")
                
            except Exception as e:
                logger.error(f"处理 {col_type} 时出错: {e}")
                await update_collection.send(f"处理 {col_type} 时出错: {e}")
                
    await update_collection.send("所有收藏品列表及资源已更新处理完毕！")
