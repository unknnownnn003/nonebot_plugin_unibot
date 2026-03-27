import os
import json
import csv
import io
import gzip
from datetime import timedelta
from typing import Dict, List, Any

import httpx
from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent, Bot
from nonebot.log import logger
from nonebot.exception import FinishedException
from nonebot.typing import T_State

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PLUGIN_DIR, "data")
SCORE_DIR = os.path.join(DATA_DIR, "score")
SONGLIST_PATH = os.path.join(DATA_DIR, "songlist.json")

if not os.path.exists(SCORE_DIR):
    os.makedirs(SCORE_DIR)

update_score = on_command("chuupdate", priority=5, block=True)

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

        def _get_rating(d):
            try:
                return float(d.get("rating", 0))
            except (TypeError, ValueError):
                return -1.0

        rating_new = _get_rating(item)
        rating_old = _get_rating(existing)

        if score_new > score_old:
            merged[key] = item
        elif score_new == score_old:
            if rating_new > rating_old:
                merged[key] = item
            elif rating_new == rating_old:
                if _is_newer(item, existing):
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

    songs: List[Dict[str, Any]] = []
    if isinstance(raw, list):
        songs = raw
    elif isinstance(raw, dict):
        if isinstance(raw.get("songs"), list):
            songs = raw["songs"]
        elif isinstance(raw.get("data"), list):
            songs = raw["data"]
        else:
            songs = [v for v in raw.values() if isinstance(v, dict)]

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

def _parse_file(file_path: str) -> tuple[bool, str, list, dict]:
    """
    读取并解析上传的 csv 文件或 Rin json 文件，将其作为标准成绩格式返回
    """
    try:
        with open(file_path, "rb") as f:
            raw_data = f.read()
            
        if raw_data.startswith(b"\x1f\x8b"):
            try:
                raw_data = gzip.decompress(raw_data)
            except Exception:
                pass
                
        text = None
        for enc in ["utf-8-sig", "utf-8", "gbk", "shift-jis"]:
            try:
                text = raw_data.decode(enc)
                break
            except UnicodeDecodeError:
                pass
                
        if text is None:
            return False, "文件编码错误或格式不支持，请确保上传的是文本类型的 JSON/CSV 文件！", [], {}

        # 尝试作为 JSON 读取 (Rin 格式)
        try:
            data = json.loads(text)
            
            if isinstance(data, dict) and "userData" in data and "userMusicDetailList" in data:
                user_data = data.get("userData", {})
                records = data.get("userMusicDetailList", [])
                
                username = user_data.get("userName", "未知")
                rating = user_data.get("playerRating", 0) / 100
                level = user_data.get("level", 0) + 100 * user_data.get("reincarnationNum", 0)
                playcount = user_data.get("playCount", 0)
                
                info_msg = f"Rin 数据解析成功:\n玩家: {username} (Lv.{level})\nRating: {rating:.2f}\n总游玩: {playcount}"
                
                data_dict = {}
                for item in records:
                    song_id = item.get("musicId")
                    level_index = item.get("level")
                    if song_id is None or level_index is None:
                        continue
                    
                    key = f"{song_id}_{level_index}"
                    score = int(item.get("scoreMax", 0))
                    
                    row = {
                        "id": song_id,
                        "level_index": level_index,
                        "score": score,
                    }
                    
                    if item.get("isAllJustice"):
                        row["full_combo"] = "aj"
                    elif item.get("isFullCombo"):
                        row["full_combo"] = "fc"
                    else:
                        row["full_combo"] = ""
                        
                    if item.get("isSuccess"):
                        row["clear"] = "clear"
                    else:
                        row["clear"] = ""
                        
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
                            
                return True, info_msg, list(data_dict.values()), {"UserName": username, "PlayerRating": rating, "Level": level, "PlayCount": playcount}
        except json.JSONDecodeError:
            pass  # 不是 JSON，继续尝试 CSV
            
        data_dict = {}
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            song_id = row.get("id")
            level_index = row.get("level_index")
            
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
                    time_field = None
                    for tf in ["play_time", "updated_at", "time", "date"]:
                        if tf in row:
                            time_field = tf
                            break
                            
                    if time_field and row.get(time_field) and existing_row.get(time_field):
                        if str(row.get(time_field)) > str(existing_row.get(time_field)):
                            data_dict[key] = row
                    else:
                        data_dict[key] = row

        return True, "CSV 解析成功", list(data_dict.values()), {}
    except Exception as e:
        logger.error(f"解析文件发生意外错误: {e}")
        return False, f"解析失败：{e}", [], {}

@update_score.handle()
async def _(bot: Bot, event: MessageEvent, state: T_State):
    """
    检查首条消息是否直接附带了文件，如果有，则直接存入 state 中跳过 prompt
    """
    msg = event.get_message()
    for seg in msg:
        seg_type = getattr(seg, "type", None)
        if not seg_type and isinstance(seg, dict):
            seg_type = seg.get("type")
        elif not seg_type and isinstance(seg, tuple) and len(seg) >= 2:
            seg_type = seg[0]
            
        if seg_type == "file":
            state["file_msg"] = msg
            break

@update_score.got("file_msg", prompt="请上传lxns查分器导出的分数csv文件或Rin导出的json文件")
async def get_uploaded_file(bot: Bot, event: MessageEvent):
    try:
        user_qq = str(event.get_user_id())
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
            await update_score.finish("未检测到有效的文件，请在使用命令后上传有效文件。")

        temp_file_path = os.path.join(SCORE_DIR, f"{user_qq}_temp.file")
        target_json_path = os.path.join(SCORE_DIR, f"{user_qq}.json")
        
        logger.info(f"开始处理用户 {user_qq} 提供的成绩文件...")

        if base64_data:
            import base64
            with open(temp_file_path, "wb") as f:
                f.write(base64.b64decode(base64_data))
        elif local_file and os.path.exists(local_file):
            import shutil
            shutil.copy2(local_file, temp_file_path)
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
                    async with client.stream("GET", file_url, timeout=30.0) as resp:
                        if resp.status_code == 200:
                            with open(temp_file_path, "wb") as f:
                                size = 0
                                async for chunk in resp.aiter_bytes():
                                    size += len(chunk)
                                    if size > 10 * 1024 * 1024:
                                        raise ValueError("文件超过10MB大小限制")
                                    f.write(chunk)
                        else:
                            await update_score.finish("下载文件失败，请稍后重试！")
            except ValueError as ve:
                await update_score.finish(f"报错: {ve}")
            except Exception as e:
                logger.error(f"下载文件发生错误: {e}")
                await update_score.finish("下载文件时发生网络错误。")
        else:
            if isinstance(file_url, str) and os.path.exists(file_url):
                import shutil
                shutil.copy2(file_url, temp_file_path)
            else:
                await update_score.finish("未能成功提取文件，更新操作已取消。")
        
        success, info_msg, parsed_items, player_info_rin = _parse_file(temp_file_path)
        
        if os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except Exception:
                pass
            
        if success:
            song_meta = _load_song_meta()
            old_items = _read_score_json(target_json_path)
            merged = _merge_score_items(old_items, parsed_items)
            new_items = _inject_level_value(merged, song_meta)
            with open(target_json_path, "w", encoding="utf-8") as f:
                json.dump(new_items, f, ensure_ascii=False, indent=4)
            
            if player_info_rin:
                info_path = os.path.join(SCORE_DIR, f"{user_qq}_info.json")
                with open(info_path, "w", encoding="utf-8") as f:
                    json.dump(player_info_rin, f, ensure_ascii=False, indent=4)
            
            if player_info_rin:
                info_path = os.path.join(SCORE_DIR, f"{user_qq}_info.json")
                with open(info_path, "w", encoding="utf-8") as f:
                    json.dump(player_info_rin, f, ensure_ascii=False, indent=4)
            await update_score.finish(f"保存成功\n{info_msg}\n当前总谱面数: {len(new_items)}")
        else:
            await update_score.finish("解析失败，请确认文件名和格式(CSV或Rin JSON)是否正确！")
            
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"处理上传成绩文件错误: {e}")
        await update_score.finish("处理文件时发生意外错误。")
