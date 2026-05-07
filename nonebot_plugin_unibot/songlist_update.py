import json
import httpx
from pathlib import Path
from datetime import datetime
from nonebot import on_command
from nonebot.permission import SUPERUSER
from nonebot.log import logger

# 请求的基本URL
BASE_URL = "https://maimai.lxns.net"
# 目标API路径
API_PATH = "/api/v0/chunithm/song/list"

# 定义插件的根目录
PLUGIN_DIR = Path(__file__).parent
# 定义数据所在目录
DATA_DIR = PLUGIN_DIR / "data"
# 定义 songlist.json 保存路径
SONGLIST_FILE = DATA_DIR / "songlist.json"

# 注册命令 `/更新songlist`
# permission=SUPERUSER 确保只有超级用户可以触发此命令
# priority=10 设定响应优先级
# block=True 阻止事件向低优先级继续传递
update_songlist = on_command("更新songlist", permission=SUPERUSER, priority=10, block=True)

@update_songlist.handle()
async def handle_update_songlist():
    """
    更新 songlist 列表的主处理函数。
    向 API 请求最新的 songlist，如果成功则覆盖写入本地 data/songlist.json 文件中。
    """
    # 发送提醒消息，表示已开始执行
    await update_songlist.send("开始从外部 API 获取 songlist，请稍候...")
    
    # 确保 data 目录存在，如果不存在则自动创建
    if not DATA_DIR.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        logger.info(f"已创建数据目录: {DATA_DIR}")
        
    # 完整的请求URL
    target_url = BASE_URL + API_PATH
    alias_url = BASE_URL + "/api/v0/chunithm/alias/list"

    try:
        # 使用 httpx 进行异步 HTTP 请求
        async with httpx.AsyncClient() as client:
            response = await client.get(target_url, timeout=30.0)
            # 检查响应状态码，如果不为 20x 则抛出 HTTPError 异常
            response.raise_for_status()
            # 解析获取到的 JSON 数据
            songlist_data = response.json()
            if not isinstance(songlist_data, dict) or not isinstance(songlist_data.get("songs"), list):
                raise ValueError("songlist API 返回格式异常，未发现 songs 列表")
            
            # 请求别名数据
            alias_response = await client.get(alias_url, timeout=30.0)
            alias_response.raise_for_status()
            alias_data = alias_response.json()
            
            # 建立 song_id 到 aliases 的映射字典以提高查找效率
            aliases_list = alias_data.get("aliases", []) if isinstance(alias_data, dict) else alias_data
            if not isinstance(aliases_list, list):
                aliases_list = []
            alias_map = {item["song_id"]: item.get("aliases", []) for item in aliases_list if "song_id" in item}
            
            # 读取本地已存在的 songlist.json 保留用户自行添加的别名
            local_alias_map = {}
            if SONGLIST_FILE.exists():
                try:
                    with open(SONGLIST_FILE, "r", encoding="utf-8") as local_f:
                        local_data = json.load(local_f)
                        for s in local_data.get("songs", []):
                            if "id" in s and "aliases" in s:
                                local_alias_map[s["id"]] = s["aliases"]
                except Exception:
                    pass

            # 将别名数据合并到 songlist_data 中对应的每首歌曲下
            if "songs" in songlist_data:
                for song in songlist_data["songs"]:
                    song_id = song.get("id")
                    new_aliases = alias_map.get(song_id, [])
                    local_aliases = local_alias_map.get(song_id, [])
                    
                    # 取并集，并保持顺序 (先网络后本地补充)
                    merged = []
                    for a in new_aliases + local_aliases:
                        if a not in merged:
                            merged.append(a)
                            
                    song["aliases"] = merged

            temp_file = SONGLIST_FILE.with_suffix(".json.tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(songlist_data, f, ensure_ascii=False, indent=4)
                f.write("\n")
            temp_file.replace(SONGLIST_FILE)

            song_count = len(songlist_data.get("songs", []))
            alias_count = sum(len(song.get("aliases", []) or []) for song in songlist_data.get("songs", []))
            
        # 记录日志，并回复用户更新成功
        mtime = datetime.fromtimestamp(SONGLIST_FILE.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        logger.success(f"songlist.json 和别名数据成功更新并合并: {SONGLIST_FILE}")
        await update_songlist.send(
            "更新成功！songlist.json 及歌曲别名已成功获取并覆盖。\n"
            f"写入路径：{SONGLIST_FILE}\n"
            f"曲目数：{song_count}，别名数：{alias_count}\n"
            f"文件修改时间：{mtime}"
        )
        
    except httpx.HTTPError as http_err:
        # 捕获 HTTP 网络请求相关的异常
        error_msg = f"更新失败，网络请求异常：{http_err}"
        logger.error(error_msg)
        await update_songlist.send(error_msg)
        
    except Exception as e:
        # 捕获其它的异常，例如 JSON 格式错误或文件写入失败等
        error_msg = f"更新失败，写入或解析发生了未知错误：{e}"
        logger.error(error_msg)
        await update_songlist.send(error_msg)
