import os
import json
import csv
import io
import gzip
import asyncio
from datetime import timedelta
from typing import Dict, List, Any

import httpx
from nonebot import get_plugin_config, on_command
from nonebot.adapters.onebot.v11 import MessageEvent, Bot
from nonebot.log import logger
from nonebot.exception import FinishedException
from nonebot.typing import T_State

from .config import Config
from .user_bind import get_bind_info

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PLUGIN_DIR, "data")
SCORE_DIR = os.path.join(DATA_DIR, "score")
SONGLIST_PATH = os.path.join(DATA_DIR, "songlist.json")
LXNS_BASE_URL = "https://maimai.lxns.net/api/v0/chunithm"
LXNS_REQUIRED_LEVELS = {3, 4}

if not os.path.exists(SCORE_DIR):
    os.makedirs(SCORE_DIR)

config = get_plugin_config(Config)

update_score = on_command("chuupdate", priority=5, block=True)
lxupdate_score = on_command("lxupdate", priority=5, block=True)

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

def _extract_lxns_data(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload:
        return payload.get("data")
    return payload

def _flatten_lxns_score_items(scores: Any) -> List[Dict[str, Any]]:
    if isinstance(scores, list):
        return [item for item in scores if isinstance(item, dict)]

    if isinstance(scores, dict):
        items: List[Dict[str, Any]] = []
        for key in ["scores", "bests", "selections", "new_bests", "data"]:
            value = scores.get(key)
            if isinstance(value, list):
                items.extend(_flatten_lxns_score_items(value))
            elif isinstance(value, dict):
                items.extend(_flatten_lxns_score_items(value))
        return items

    return []

def _normalize_lxns_score_items(scores: Any) -> tuple[List[Dict[str, Any]], int]:
    score_items = _flatten_lxns_score_items(scores)
    if not score_items:
        return [], 0

    normalized: List[Dict[str, Any]] = []
    missing_score = 0
    for item in score_items:
        song_id = item.get("id")
        level_index = item.get("level_index")
        if song_id is None or level_index is None:
            continue

        score_value = item.get("score")
        if score_value is None:
            missing_score += 1
            continue

        row: Dict[str, Any] = {
            "id": song_id,
            "level_index": level_index,
            "score": _as_int(score_value, 0),
        }

        for key in [
            "song_name",
            "level",
            "rating",
            "over_power",
            "clear",
            "full_combo",
            "full_chain",
            "rank",
            "play_time",
            "upload_time",
            "last_played_time",
        ]:
            if key in item and item.get(key) is not None:
                row[key] = item.get(key)

        normalized.append(row)

    return normalized, missing_score

def _normalize_lxns_player_info(player: Dict[str, Any]) -> Dict[str, Any]:
    level = _as_int(player.get("level"), 0) + 100 * _as_int(player.get("reborn_count"), 0)
    return {
        "UserName": player.get("name", "Unknown"),
        "PlayerRating": player.get("rating", 0.0),
        "Level": level,
        "PlayCount": player.get("total_play_count", 0),
        "OverPower": player.get("over_power", 0.0),
        "OverPowerProgress": player.get("over_power_progress", 0.0),
        "UploadTime": player.get("upload_time", ""),
        "Source": "lxns",
    }

async def _get_lxns_json(client: httpx.AsyncClient, path: str, token: str) -> tuple[int, Any]:
    response = await client.get(
        f"{LXNS_BASE_URL}{path}",
        headers={"Authorization": token},
        timeout=20.0,
    )
    try:
        payload = response.json()
    except Exception:
        payload = response.text
    return response.status_code, payload

async def _resolve_friend_code(client: httpx.AsyncClient, user_qq: str, token: str) -> tuple[str, Dict[str, Any], str]:
    status, payload = await _get_lxns_json(client, f"/player/qq/{user_qq}", token)
    if status == 200:
        data = _extract_lxns_data(payload)
        if isinstance(data, dict):
            friend_code = data.get("friend_code")
            if friend_code:
                return str(friend_code), data, "qq"

    bind_data = get_bind_info()
    friend_code = bind_data.get(user_qq, "")
    if friend_code:
        status, payload = await _get_lxns_json(client, f"/player/{friend_code}", token)
        data = _extract_lxns_data(payload)
        return str(friend_code), data if status == 200 and isinstance(data, dict) else {}, "bind"

    return "", {}, "none"

async def _fetch_lxns_all_scores(client: httpx.AsyncClient, friend_code: str, token: str) -> tuple[List[Dict[str, Any]], int, int, int, int, int]:
    status, payload = await _get_lxns_json(client, f"/player/{friend_code}/scores", token)
    if status != 200:
        return [], status, 0, 0, 0, 0

    cached_items = _flatten_lxns_score_items(_extract_lxns_data(payload))
    levels_by_song: Dict[int, List[int]] = {}
    for item in cached_items:
        try:
            song_id = int(item.get("id"))
            level_index = int(item.get("level_index"))
        except (TypeError, ValueError):
            continue
        levels_by_song.setdefault(song_id, [])
        if level_index not in levels_by_song[song_id]:
            levels_by_song[song_id].append(level_index)

    if not levels_by_song:
        return [], status, 0, 0, 0, 0

    semaphore = asyncio.Semaphore(2)
    failed_required = 0
    unavailable_required = 0
    failed_optional = 0

    def count_failed(level_index: int, status_code: int) -> None:
        nonlocal failed_required, unavailable_required, failed_optional
        if level_index in LXNS_REQUIRED_LEVELS:
            if status_code == 404:
                unavailable_required += 1
            else:
                failed_required += 1
        else:
            failed_optional += 1

    async def get_with_retry(path: str, label: str) -> tuple[int, Any]:
        last_status = 0
        last_payload: Any = None
        for attempt in range(6):
            try:
                last_status, last_payload = await _get_lxns_json(client, path, token)
                if last_status == 200:
                    return last_status, last_payload
                if last_status in (429, 500, 502, 503, 504):
                    await asyncio.sleep(min(30.0, 2.0 * (attempt + 1)))
                    continue
                return last_status, last_payload
            except Exception as e:
                last_payload = str(e)
                if attempt < 5:
                    await asyncio.sleep(min(15.0, 1.5 * (attempt + 1)))
                    continue
                logger.warning(f"lxns request failed: {label}, error={e}")
                return 0, last_payload
        return last_status, last_payload

    async def fetch_song_bests(song_id: int, level_indexes: List[int]) -> List[Dict[str, Any]]:
        async with semaphore:
            best_status, best_payload = await get_with_retry(
                f"/player/{friend_code}/bests?song_id={song_id}",
                f"song_id={song_id}",
            )
            fallback_levels = list(level_indexes)
            best_items: List[Dict[str, Any]] = []
            if best_status == 200:
                best_items = _flatten_lxns_score_items(_extract_lxns_data(best_payload))
                found_levels = {
                    _as_int(item.get("level_index"), -1)
                    for item in best_items
                    if item.get("score") is not None
                }
                fallback_levels = [level_index for level_index in level_indexes if level_index not in found_levels]

            fallback_items: List[Dict[str, Any]] = []
            for level_index in fallback_levels:
                one_status, one_payload = await get_with_retry(
                    f"/player/{friend_code}/best?song_id={song_id}&level_index={level_index}",
                    f"song_id={song_id}, level_index={level_index}",
                )
                if one_status == 200:
                    data = _extract_lxns_data(one_payload)
                    if isinstance(data, dict):
                        fallback_items.append(data)
                    else:
                        fallback_items.extend(_flatten_lxns_score_items(data))
                else:
                    count_failed(level_index, one_status)

            return best_items + fallback_items

    all_items: List[Dict[str, Any]] = []
    fetch_targets = sorted(
        levels_by_song.items(),
        key=lambda item: (not any(level in LXNS_REQUIRED_LEVELS for level in item[1]), item[0]),
    )
    chunks = await asyncio.gather(
        *(fetch_song_bests(song_id, levels) for song_id, levels in fetch_targets)
    )
    for chunk in chunks:
        all_items.extend(chunk)

    return all_items, status, len(levels_by_song), failed_required, unavailable_required, failed_optional

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

@lxupdate_score.handle()
async def _(event: MessageEvent):
    user_qq = str(event.get_user_id())
    token = (config.lxns_token or "").strip()
    if not token:
        await lxupdate_score.finish("未配置落雪开发者密钥，请在 .env 中配置 lxns_token 后再使用 /lxupdate。")

    await lxupdate_score.send("收到，正在处理...")

    target_json_path = os.path.join(SCORE_DIR, f"{user_qq}.json")
    info_path = os.path.join(SCORE_DIR, f"{user_qq}_info.json")

    try:
        async with httpx.AsyncClient() as client:
            friend_code, player_data, source = await _resolve_friend_code(client, user_qq, token)
            if not friend_code:
                await lxupdate_score.finish("未找到落雪好友码。请先在落雪绑定 QQ，或使用 /bind 好友码 后重试。")

            score_items, status, song_count, failed_required_count, unavailable_required_count, failed_optional_count = await _fetch_lxns_all_scores(client, friend_code, token)
            if status == 401:
                await lxupdate_score.finish("落雪开发者密钥无效或权限不足，请检查 lxns_token。")
            if status == 404:
                await lxupdate_score.finish("落雪没有找到该玩家成绩，请确认好友码是否正确或数据是否已上传到落雪。")
            if status != 200:
                logger.error(f"lxns score api failed: status={status}")
                await lxupdate_score.finish(f"落雪成绩接口请求失败，HTTP {status}。")

            parsed_items, missing_score = _normalize_lxns_score_items(score_items)
            if not parsed_items:
                if missing_score:
                    await lxupdate_score.finish("落雪接口返回了成绩列表，但缺少 score 字段，暂时无法写入本地成绩。")
                await lxupdate_score.finish("落雪接口没有返回可用成绩，暂未更新本地数据。")
            if failed_required_count:
                await lxupdate_score.finish(
                    f"落雪同步未完成：MASTER/ULTIMA 谱面中有 {failed_required_count} 个请求失败，已取消写入，避免关键成绩变成不完整数据。请稍后重试。"
                )

            song_meta = _load_song_meta()
            old_items = _read_score_json(target_json_path)
            merged = _merge_score_items(old_items, parsed_items)
            new_items = _inject_level_value(merged, song_meta)
            with open(target_json_path, "w", encoding="utf-8") as f:
                json.dump(new_items, f, ensure_ascii=False, indent=4)

            if player_data:
                with open(info_path, "w", encoding="utf-8") as f:
                    json.dump(_normalize_lxns_player_info(player_data), f, ensure_ascii=False, indent=4)

            source_text = "QQ直查" if source == "qq" else "本地绑定好友码"
            failed_parts = []
            if unavailable_required_count:
                failed_parts.append(f"落雪未提供分数的 MASTER/ULTIMA 谱面数: {unavailable_required_count}")
            if failed_optional_count:
                failed_parts.append(f"低优先级谱面失败数: {failed_optional_count}")
            failed_text = ("\n" + "\n".join(failed_parts)) if failed_parts else ""
            await lxupdate_score.finish(
                f"落雪同步成功\n来源: {source_text}\n好友码: {friend_code}\n本次查询曲目数: {song_count}\n本次获取谱面数: {len(parsed_items)}\n当前总谱面数: {len(new_items)}{failed_text}"
            )
    except FinishedException:
        raise
    except httpx.TimeoutException:
        await lxupdate_score.finish("请求落雪接口超时，请稍后重试。")
    except Exception as e:
        logger.error(f"lxupdate failed: {e}")
        await lxupdate_score.finish("从落雪同步成绩时发生意外错误，请查看控制台日志。")

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
