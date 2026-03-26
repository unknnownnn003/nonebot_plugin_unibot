import os
import json
import csv
import asyncio
from datetime import timedelta
from typing import Dict, List, Any

import httpx
from nonebot import on_command, get_plugin_config
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import MessageEvent, Bot
from nonebot.log import logger
from nonebot.exception import FinishedException
from nonebot.params import CommandArg

from .config import Config
from .user_bind import get_bind_info

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PLUGIN_DIR, "data")
SCORE_DIR = os.path.join(DATA_DIR, "score")

if not os.path.exists(SCORE_DIR):
    os.makedirs(SCORE_DIR)

SONGLIST_PATH = os.path.join(DATA_DIR, "songlist.json")

API_REQUEST_INTERVAL = 0.12
API_RETRY_TIMES = 3
API_RETRY_DELAY = 0.6

update_score = on_command("chuupdate", priority=5, block=True, expire_time=timedelta(seconds=300))
config = get_plugin_config(Config)


def _as_int(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _pick_time_value(item: Dict[str, Any]) -> str:
    for key in ["last_played_time", "upload_time", "play_time", "updated_at", "time", "date"]:
        value = item.get(key)
        if value:
            return str(value)
    return ""


def _is_newer(new_item: Dict[str, Any], old_item: Dict[str, Any]) -> bool:
    return _pick_time_value(new_item) > _pick_time_value(old_item)


def _merge_score_items(old_items: List[Dict[str, Any]], new_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}

    def _merge_one(item: Dict[str, Any]):
        song_id = item.get("id")
        level_index = item.get("level_index")
        if song_id is None or level_index is None:
            return
        key = f"{song_id}_{level_index}"
        if key not in merged:
            merged[key] = item
            return

        existing = merged[key]
        score_new = _as_int(item.get("score"), -1)
        score_old = _as_int(existing.get("score"), -1)

        if score_new > score_old:
            merged[key] = item
        elif score_new == score_old and _is_newer(item, existing):
            merged[key] = item

    for it in old_items:
        if isinstance(it, dict):
            _merge_one(it)
    for it in new_items:
        if isinstance(it, dict):
            _merge_one(it)

    return list(merged.values())


def _load_song_meta() -> Dict[str, Dict[str, Any]]:
    if not os.path.exists(SONGLIST_PATH):
        return {}

    try:
        with open(SONGLIST_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        logger.error(f"读取 songlist.json 失败: {e}")
        return {}

    songs: List[Dict[str, Any]]
    if isinstance(raw, list):
        songs = raw
    elif isinstance(raw, dict):
        if isinstance(raw.get("songs"), list):
            songs = raw["songs"]
        elif isinstance(raw.get("data"), list):
            songs = raw["data"]
        else:
            songs = [v for v in raw.values() if isinstance(v, dict)]
    else:
        songs = []

    song_meta: Dict[str, Dict[str, Any]] = {}
    for song in songs:
        if not isinstance(song, dict):
            continue
        song_id = song.get("id")
        if song_id is None:
            continue

        raw_difficulties = song.get("difficulties")
        difficulties = raw_difficulties if isinstance(raw_difficulties, list) else []
        title = song.get("title") or song.get("song_name") or ""

        diff_map: Dict[str, Dict[str, Any]] = {}
        for diff in difficulties:
            if not isinstance(diff, dict):
                continue
            di = diff.get("difficulty")
            if di is None:
                continue
            diff_map[str(di)] = {
                "level": diff.get("level"),
                "level_value": diff.get("level_value"),
            }

        song_meta[str(song_id)] = {
            "song_name": title,
            "diff_map": diff_map,
        }

    return song_meta


def _inject_level_value(items: List[Dict[str, Any]], song_meta: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        new_item = dict(item)
        sid = str(new_item.get("id", ""))
        li = _as_int(new_item.get("level_index"), -1)
        meta = song_meta.get(sid, {})
        diff_map = meta.get("diff_map", {}) if isinstance(meta.get("diff_map"), dict) else {}
        diff_info = diff_map.get(str(li), {}) if isinstance(diff_map, dict) else {}

        if "level_value" in diff_info:
            new_item["level_value"] = diff_info.get("level_value")
        elif "level_value" not in new_item:
            new_item["level_value"] = None

        if (not new_item.get("level")) and diff_info.get("level") is not None:
            new_item["level"] = diff_info.get("level")

        if not new_item.get("song_name") and meta.get("song_name"):
            new_item["song_name"] = meta["song_name"]

        result.append(new_item)
    return result


def _read_score_json(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        return []
    except Exception as e:
        logger.error(f"读取分数 JSON 失败: {e}")
        return []


async def _fetch_best_for_chart(
    client: httpx.AsyncClient,
    headers: Dict[str, str],
    friend_code: str,
    song_id: int,
    level_index: int,
) -> Dict[str, Any] | None:
    url = f"https://maimai.lxns.net/api/v0/chunithm/player/{friend_code}/best"
    params = {
        "song_id": song_id,
        "level_index": level_index,
    }

    for attempt in range(API_RETRY_TIMES):
        try:
            resp = await client.get(url, headers=headers, params=params, timeout=15.0)
            if resp.status_code == 200:
                payload = resp.json()
                data = payload.get("data") if isinstance(payload, dict) else None
                if isinstance(data, dict) and data:
                    return data
            else:
                logger.warning(
                    f"best API 失败 song_id={song_id}, level_index={level_index}, status={resp.status_code}, attempt={attempt+1}"
                )
        except Exception as e:
            logger.warning(
                f"best API 异常 song_id={song_id}, level_index={level_index}, attempt={attempt+1}, err={e}"
            )

        if attempt < API_RETRY_TIMES - 1:
            await asyncio.sleep(API_RETRY_DELAY)

    return None


async def _sync_user_score_by_api(user_qq: str) -> tuple[bool, str]:
    if not config.lxns_token:
        return False, "未配置落雪咖啡屋(Lxns) Token，请在 .env.prod 中添加 lxns_token 配置！"

    friend_code = ""
    headers = {"Authorization": config.lxns_token}

    qq_lookup_url = f"https://maimai.lxns.net/api/v0/chunithm/player/qq/{user_qq}"
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(qq_lookup_url, headers=headers, timeout=10.0)
            if response.status_code == 200:
                payload = response.json()
                data = payload.get("data") if isinstance(payload, dict) else {}
                api_fc = data.get("friend_code") if isinstance(data, dict) else None
                if api_fc:
                    friend_code = str(api_fc).strip()
        except Exception as e:
            logger.warning(f"通过 QQ 盲查 friend_code 失败: {e}")

    if not friend_code:
        bind_data = get_bind_info()
        friend_code = str(bind_data.get(user_qq, "")).strip()

    if not friend_code:
        return False, "未绑定好友码，请先使用 /bind 绑定好友码"

    song_meta = _load_song_meta()
    if not song_meta:
        return False, "未找到曲库数据，请先更新 songlist.json"

    fetched_items: List[Dict[str, Any]] = []

    async with httpx.AsyncClient() as client:
        for sid, meta in song_meta.items():
            diff_map = meta.get("diff_map", {}) if isinstance(meta.get("diff_map"), dict) else {}
            if not diff_map:
                continue

            for diff_key, diff_info in diff_map.items():
                level_index = _as_int(diff_key, -1)
                if level_index < 0:
                    continue

                data = await _fetch_best_for_chart(
                    client=client,
                    headers=headers,
                    friend_code=friend_code,
                    song_id=_as_int(sid, -1),
                    level_index=level_index,
                )
                await asyncio.sleep(API_REQUEST_INTERVAL)

                if not data:
                    continue

                item = {
                    "id": data.get("id", _as_int(sid, -1)),
                    "song_name": data.get("song_name") or meta.get("song_name") or "",
                    "level": data.get("level") if data.get("level") is not None else diff_info.get("level"),
                    "level_index": data.get("level_index", level_index),
                    "score": data.get("score", 0),
                    "rating": data.get("rating"),
                    "over_power": data.get("over_power"),
                    "clear": data.get("clear"),
                    "full_combo": data.get("full_combo"),
                    "full_chain": data.get("full_chain"),
                    "rank": data.get("rank"),
                    "play_time": data.get("play_time"),
                    "upload_time": data.get("upload_time"),
                    "last_played_time": data.get("last_played_time"),
                }
                fetched_items.append(item)

    target_json_path = os.path.join(SCORE_DIR, f"{user_qq}.json")
    old_items = _read_score_json(target_json_path)
    merged = _merge_score_items(old_items, fetched_items)
    merged = _inject_level_value(merged, song_meta)

    try:
        with open(target_json_path, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"写入分数 JSON 失败: {e}")
        return False, "写入分数文件失败"

    return True, f"同步完成：新增/更新 {len(fetched_items)} 条，当前总谱面 {len(merged)} 条"

def _parse_csv_to_json(csv_path: str, json_path: str) -> bool:
    """
    读取并解析 cvs 文件，然后将其转存为指定路径下的 json 文件
    如果csv中同一id同一level_index的项目出现多次，请保留分数最高（其次最新）的一项
    """
    try:
        data_dict = {}
        with open(csv_path, mode="r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                song_id = row.get("id")
                level_index = row.get("level_index")
                
                # 如果缺少主键信息，则视为异常数据跳过
                if song_id is None or level_index is None:
                    continue
                
                key = f"{song_id}_{level_index}"
                
                try:
                    score = int(row.get("score", 0))
                except ValueError:
                    score = -1
                
                if key not in data_dict:
                    data_dict[key] = row
                else:
                    existing_row = data_dict[key]
                    try:
                        existing_score = int(existing_row.get("score", 0))
                    except ValueError:
                        existing_score = -1
                        
                    if score > existing_score:
                        data_dict[key] = row
                    elif score == existing_score:
                        # 分数相同时，尝试比较时间（以靠后的或时间字符串更大的为准）
                        time_field = None
                        for tf in ["play_time", "updated_at", "time", "date"]:
                            if tf in row:
                                time_field = tf
                                break
                                
                        if time_field and row.get(time_field) and existing_row.get(time_field):
                            if str(row.get(time_field)) > str(existing_row.get(time_field)):
                                data_dict[key] = row
                        else:
                            # 默认如果没找到时间字段，后来者居上（假设 CSV 靠后的较新）
                            data_dict[key] = row

        data_list = list(data_dict.values())

        with open(json_path, mode="w", encoding="utf-8") as f:
            json.dump(data_list, f, ensure_ascii=False, indent=4)
        return True
    except Exception as e:
        logger.error(f"解析 CSV 并保存为 JSON 时出错: {e}")
        return False

@update_score.handle()
async def _(bot: Bot, event: MessageEvent, msg=CommandArg()):
    """
    处理 /chuupdate 命令：统一走 CSV 上传流程
    """
    await update_score.send("请上传lxns查分器导出的分数csv文件")

# 使用 expire_time 让等待状态 5 分钟后超时
@update_score.got("file_msg", prompt="等待上传中...")
async def get_uploaded_file(bot: Bot, event: MessageEvent):
    try:
        user_qq = str(event.get_user_id())
        
        # 提取当前消息的纯文本或特殊段
        msg = event.get_message()
        file_url = ""
        local_file = ""
        base64_data = ""
        
        for seg in msg:
            seg_type = getattr(seg, "type", None)
            seg_data = getattr(seg, "data", {})
            if not seg_type and isinstance(seg, dict):
                seg_type = seg.get("type")
                seg_data = seg.get("data", {})
            elif not seg_type and isinstance(seg, tuple) and len(seg) >= 2:
                seg_type = seg[0]
                seg_data = seg[1] if isinstance(seg[1], dict) else {}
                
            if seg_type == "file":
                file_url = seg_data.get("url", "")
                file_id = seg_data.get("file_id")
                if not file_url and file_id:
                    try:
                        file_info = await bot.call_api("get_file", file_id=file_id)
                        
                        target_info = file_info.get("data", file_info) if isinstance(file_info, dict) else file_info
                        
                        file_url = target_info.get("url", "")
                        local_file = target_info.get("file", "")
                        base64_data = target_info.get("base64", "")
                    except Exception as e:
                        logger.error(f"获取文件信息失败: {e}")
                break

        if not file_url and not local_file and not base64_data:
            await update_score.finish("未检测到CSV文件，update停止")

        temp_csv_path = os.path.join(SCORE_DIR, f"{user_qq}_temp.csv")
        target_json_path = os.path.join(SCORE_DIR, f"{user_qq}.json")
        
        logger.info(f"开始处理用户 {user_qq} 提供的成绩文件...")

        if base64_data:
            import base64
            with open(temp_csv_path, "wb") as f:
                f.write(base64.b64decode(base64_data))
        elif local_file and os.path.exists(local_file):
            import shutil
            shutil.copy2(local_file, temp_csv_path)
        elif file_url:
            if isinstance(file_url, str):
                if file_url.startswith("//"):
                    file_url = f"http:{file_url}"
                elif file_url.startswith("/") and not os.path.exists(file_url):
                    file_url = f"http://{file_url}"

            if not isinstance(file_url, str) or not (file_url.startswith("http://") or file_url.startswith("https://")):
                await update_score.finish("接收到的不是有效的文件链接，更新操作已取消。")
                
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(file_url, timeout=30.0)
                    if resp.status_code == 200:
                        with open(temp_csv_path, "wb") as f:
                            f.write(resp.content)
                    else:
                        await update_score.finish("下载文件失败，请稍后重试！")
            except Exception as e:
                logger.error(f"下载文件发生错误: {e}")
                await update_score.finish("下载文件时发生错误，请检查网络连接或稍后重试。")
        else:
            # 兼容如果由于某些原因 file_url 是本地路径字符串但没被 local_file 捕获
            if isinstance(file_url, str) and os.path.exists(file_url):
                import shutil
                shutil.copy2(file_url, temp_csv_path)
            else:
                await update_score.finish("接收到的不是有效的文件，更新操作已取消。")
        
        # 解析与转存
        success = _parse_csv_to_json(temp_csv_path, target_json_path)
        
        # 无论成功与否，清理临时下载的 CSV 文件
        if os.path.exists(temp_csv_path):
            os.remove(temp_csv_path)
            
        if success:
            song_meta = _load_song_meta()
            old_items = _read_score_json(target_json_path)
            new_items = _inject_level_value(old_items, song_meta)
            with open(target_json_path, "w", encoding="utf-8") as f:
                json.dump(new_items, f, ensure_ascii=False, indent=4)
            await update_score.finish("保存成功")
        else:
            await update_score.finish("CSV 解析失败，请确认导出的格式是否正常并重试！")
            
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"处理上传成绩文件错误: {e}")
        await update_score.finish(f"处理文件时发生意外错误。")
