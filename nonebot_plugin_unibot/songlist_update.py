import json
import httpx
from pathlib import Path
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote
from nonebot import get_plugin_config, on_command
from nonebot.permission import SUPERUSER
from nonebot.log import logger

from .config import Config

config = get_plugin_config(Config)

# 请求的基本URL
BASE_URL = "https://maimai.lxns.net"
# 目标API路径
API_PATH = "/api/v0/chunithm/song/list"
CHUNIREC_SHOWALL_URL = "https://api.chunirec.net/2.0/music/showall.json"
CHUNIREC_REGION = "jp2"
CHUNIREC_SOURCE = "chunirec"
CHUNIREC_DB_MUSIC_URL = "https://db.chunirec.net/music/{title}/{song_id}"
CHUNIREC_VERSION_RULES: List[Tuple[date, int, str]] = [
    (date(2025, 12, 11), 24000, "CHUNITHM X-VERSE-X"),
    (date(2025, 7, 16), 23500, "CHUNITHM X-VERSE"),
    (date(2024, 12, 12), 23000, "CHUNITHM VERSE"),
]
CHUNIREC_EXTRA_VERSIONS = [
    {"id": 19, "title": "CHUNITHM X-VERSE", "version": 23500},
    {"id": 20, "title": "CHUNITHM X-VERSE-X", "version": 24000},
]
DIFFICULTY_MAP = {
    "BAS": 0,
    "ADV": 1,
    "EXP": 2,
    "MAS": 3,
    "ULT": 4,
    "WE": 5,
}
CHUNIREC_GENRE_MAP = {
    "POPS&ANIME": "流行 & 动漫",
    "VARIETY": "其他游戏",
    "東方Project": "东方Project",
    "ORIGINAL": "原创",
    "ゲキマイ": "音击舞萌",
    "イロドリミドリ": "彩绿",
}

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
update_chunirec_songlist = on_command(
    "更新chunirec",
    aliases={"更新chunirecsonglist", "更新日服songlist"},
    permission=SUPERUSER,
    priority=10,
    block=True,
)


def load_local_songlist() -> Dict[str, Any]:
    if not SONGLIST_FILE.exists():
        return {"songs": [], "genres": [], "versions": []}
    with open(SONGLIST_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("本地 songlist.json 格式异常")
    data.setdefault("songs", [])
    data.setdefault("genres", [])
    data.setdefault("versions", [])
    return data


def write_songlist_atomic(songlist_data: Dict[str, Any]) -> None:
    temp_file = SONGLIST_FILE.with_suffix(".json.tmp")
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(songlist_data, f, ensure_ascii=False, indent=4)
        f.write("\n")
    temp_file.replace(SONGLIST_FILE)


def build_local_alias_map(local_data: Dict[str, Any]) -> Dict[str, List[str]]:
    local_alias_map: Dict[str, List[str]] = {}
    for song in local_data.get("songs", []) if isinstance(local_data, dict) else []:
        if not isinstance(song, dict) or "id" not in song:
            continue
        aliases = song.get("aliases", [])
        local_alias_map[str(song["id"])] = aliases if isinstance(aliases, list) else []
    return local_alias_map


def build_local_song_maps(local_data: Dict[str, Any]) -> Tuple[Dict[str, Dict[str, Any]], Dict[Tuple[str, str], Dict[str, Any]]]:
    by_id: Dict[str, Dict[str, Any]] = {}
    by_title: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for song in local_data.get("songs", []) if isinstance(local_data, dict) else []:
        if not isinstance(song, dict):
            continue
        if song.get("id") is not None:
            by_id[str(song["id"])] = song
        by_title[song_match_key(song)] = song
    return by_id, by_title


def normalize_title_key(value: Any) -> str:
    return str(value or "").strip().casefold()


def song_match_key(song: Dict[str, Any]) -> Tuple[str, str]:
    return (normalize_title_key(song.get("title")), normalize_title_key(song.get("artist")))


def map_chunirec_genre(genre: Any) -> str:
    raw = str(genre or "").strip()
    return CHUNIREC_GENRE_MAP.get(raw, raw or "-")


def preserve_local_chart_consts(song: Dict[str, Any], old_song: Optional[Dict[str, Any]]) -> None:
    old_diffs = {}
    if isinstance(old_song, dict):
        for diff in old_song.get("difficulties", []) or []:
            if not isinstance(diff, dict):
                continue
            try:
                old_diffs[int(diff.get("difficulty"))] = diff
            except (TypeError, ValueError):
                continue

    for diff in song.get("difficulties", []) or []:
        if not isinstance(diff, dict):
            continue
        diff["lx_level"] = diff.get("level")
        diff["lx_level_value"] = diff.get("level_value")
        try:
            old_diff = old_diffs.get(int(diff.get("difficulty")))
        except (TypeError, ValueError):
            old_diff = None
        if old_diff:
            if old_diff.get("chunirec_level") is not None:
                diff["chunirec_level"] = old_diff.get("chunirec_level")
            if old_diff.get("chunirec_level_value") is not None:
                diff["chunirec_level_value"] = old_diff.get("chunirec_level_value")


def format_chunirec_level(level: Any) -> str:
    try:
        value = float(level)
    except (TypeError, ValueError):
        return str(level or "")
    base = int(value)
    return f"{base}+" if abs(value - base - 0.5) < 0.001 else str(base)


def parse_release_date(value: Any) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        logger.warning(f"无法解析 chunirec 曲目发布日期: {value}")
        return None


def infer_chunirec_version(release: Any) -> int:
    release_date = parse_release_date(release)
    if not release_date:
        return 23000
    for start_date, version, _ in CHUNIREC_VERSION_RULES:
        if release_date >= start_date:
            return version
    return 23000


def ensure_chunirec_versions(local_data: Dict[str, Any]) -> None:
    versions = local_data.setdefault("versions", [])
    if not isinstance(versions, list):
        local_data["versions"] = versions = []

    existing = {v.get("version") for v in versions if isinstance(v, dict)}
    next_id = max((int(v.get("id", -1)) for v in versions if isinstance(v, dict) and str(v.get("id", "")).isdigit()), default=-1) + 1
    for version in CHUNIREC_EXTRA_VERSIONS:
        if version["version"] in existing:
            continue
        item = dict(version)
        if any(isinstance(v, dict) and v.get("id") == item["id"] for v in versions):
            item["id"] = next_id
            next_id += 1
        versions.append(item)
        existing.add(item["version"])


def convert_chunirec_song(item: Dict[str, Any], existing_aliases: Dict[str, List[str]]) -> Optional[Dict[str, Any]]:
    meta = item.get("meta")
    data = item.get("data")
    if not isinstance(meta, dict) or not isinstance(data, dict):
        return None

    song_id = str(meta.get("id") or "").strip()
    title = str(meta.get("title") or "").strip()
    if not song_id or not title:
        return None

    difficulties = []
    for diff_key, diff_data in data.items():
        if not isinstance(diff_data, dict) or diff_key not in DIFFICULTY_MAP:
            continue
        level = diff_data.get("level")
        const = diff_data.get("const")
        difficulty = {
            "difficulty": DIFFICULTY_MAP[diff_key],
            "level": format_chunirec_level(level),
            "level_value": const if const is not None else level,
            "chunirec_level": format_chunirec_level(level),
            "chunirec_level_value": const if const is not None else level,
            "note_designer": "-",
            "version": infer_chunirec_version(meta.get("release")),
            "is_const_unknown": bool(diff_data.get("is_const_unknown", False)),
        }
        if "maxcombo" in diff_data:
            difficulty["maxcombo"] = diff_data.get("maxcombo")
            difficulty["notes"] = {"total": diff_data.get("maxcombo")}
        difficulties.append(difficulty)

    version = infer_chunirec_version(meta.get("release"))
    return {
        "id": song_id,
        "title": title,
        "artist": meta.get("artist") or "-",
        "genre": map_chunirec_genre(meta.get("genre")),
        "bpm": meta.get("bpm") or 0,
        "version": version,
        "release": meta.get("release"),
        "difficulties": sorted(difficulties, key=lambda d: d.get("difficulty", 99)),
        "aliases": existing_aliases.get(song_id, []),
        "data_source": CHUNIREC_SOURCE,
        "chunirec_id": song_id,
        "chunirec_url": CHUNIREC_DB_MUSIC_URL.format(title=quote(title, safe=""), song_id=song_id),
    }


def merge_chunirec_songs(local_data: Dict[str, Any], chunirec_payload: List[Dict[str, Any]]) -> Tuple[int, int, int]:
    songs = local_data.setdefault("songs", [])
    if not isinstance(songs, list):
        raise ValueError("本地 songlist.json 中 songs 不是列表")

    existing_aliases = build_local_alias_map(local_data)
    existing_by_id = {str(song.get("id")): idx for idx, song in enumerate(songs) if isinstance(song, dict) and song.get("id") is not None}
    lx_title_keys = {song_match_key(song) for song in songs if isinstance(song, dict) and song.get("data_source") != CHUNIREC_SOURCE}
    lx_title_only = {
        normalize_title_key(song.get("title"))
        for song in songs
        if isinstance(song, dict) and song.get("data_source") != CHUNIREC_SOURCE
    }
    lx_by_title_key = {
        song_match_key(song): idx
        for idx, song in enumerate(songs)
        if isinstance(song, dict) and song.get("data_source") != CHUNIREC_SOURCE
    }

    def merge_chunirec_consts(target_song: Dict[str, Any], source_song: Dict[str, Any]) -> None:
        target_diffs = {
            int(diff.get("difficulty")): diff
            for diff in target_song.get("difficulties", []) or []
            if isinstance(diff, dict) and str(diff.get("difficulty", "")).lstrip("-").isdigit()
        }
        for source_diff in source_song.get("difficulties", []) or []:
            if not isinstance(source_diff, dict):
                continue
            diff_index = source_diff.get("difficulty")
            try:
                diff_index = int(diff_index)
            except (TypeError, ValueError):
                continue
            target_diff = target_diffs.get(diff_index)
            if not target_diff:
                continue
            target_diff["chunirec_level"] = source_diff.get("chunirec_level", source_diff.get("level"))
            target_diff["chunirec_level_value"] = source_diff.get("chunirec_level_value", source_diff.get("level_value"))

    added = 0
    refreshed = 0
    skipped_lx = 0
    for raw_song in chunirec_payload:
        if not isinstance(raw_song, dict):
            continue
        converted = convert_chunirec_song(raw_song, existing_aliases)
        if not converted:
            continue

        song_id = str(converted["id"])
        title_key = normalize_title_key(converted.get("title"))
        title_artist_key = (title_key, normalize_title_key(converted.get("artist")))

        existing_index = existing_by_id.get(song_id)
        if existing_index is not None:
            old_song = songs[existing_index]
            if isinstance(old_song, dict) and old_song.get("data_source") == CHUNIREC_SOURCE:
                converted["aliases"] = old_song.get("aliases", converted["aliases"])
                songs[existing_index] = converted
                refreshed += 1
            else:
                if isinstance(old_song, dict):
                    merge_chunirec_consts(old_song, converted)
                skipped_lx += 1
            continue

        if title_artist_key in lx_title_keys or title_key in lx_title_only:
            target_index = lx_by_title_key.get(title_artist_key)
            if target_index is not None and isinstance(songs[target_index], dict):
                merge_chunirec_consts(songs[target_index], converted)
            skipped_lx += 1
            continue

        songs.append(converted)
        existing_by_id[song_id] = len(songs) - 1
        added += 1

    return added, refreshed, skipped_lx

@update_songlist.handle()
async def handle_update_songlist():
    """
    更新 songlist 列表的主处理函数。
    向 API 请求最新的 songlist，如果成功则覆盖写入本地 data/songlist.json 文件中。
    """
    # 发送提醒消息，表示已开始执行
    await update_songlist.send("收到，正在处理...")
    
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
            try:
                local_data = load_local_songlist()
                local_alias_map = build_local_alias_map(local_data)
                old_by_id, old_by_title = build_local_song_maps(local_data)
            except Exception:
                local_alias_map = {}
                old_by_id, old_by_title = {}, {}

            # 将别名数据合并到 songlist_data 中对应的每首歌曲下
            if "songs" in songlist_data:
                for song in songlist_data["songs"]:
                    song_id = song.get("id")
                    new_aliases = alias_map.get(song_id, [])
                    local_aliases = local_alias_map.get(str(song_id), [])
                    
                    # 取并集，并保持顺序 (先网络后本地补充)
                    merged = []
                    for a in new_aliases + local_aliases:
                        if a not in merged:
                            merged.append(a)
                            
                    song["aliases"] = merged
                    old_song = old_by_id.get(str(song_id)) or old_by_title.get(song_match_key(song))
                    preserve_local_chart_consts(song, old_song)

                lx_keys = {song_match_key(song) for song in songlist_data["songs"] if isinstance(song, dict)}
                lx_titles = {normalize_title_key(song.get("title")) for song in songlist_data["songs"] if isinstance(song, dict)}
                for old_song in old_by_id.values():
                    if old_song.get("data_source") != CHUNIREC_SOURCE:
                        continue
                    if song_match_key(old_song) in lx_keys or normalize_title_key(old_song.get("title")) in lx_titles:
                        continue
                    songlist_data["songs"].append(old_song)

            write_songlist_atomic(songlist_data)

            song_count = len(songlist_data.get("songs", []))
            alias_count = sum(len(song.get("aliases", []) or []) for song in songlist_data.get("songs", []))
            
        # 记录日志，并回复用户更新成功
        mtime = datetime.fromtimestamp(SONGLIST_FILE.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        logger.success(f"songlist.json 和别名数据成功更新并合并: {SONGLIST_FILE}")
        await update_songlist.send(
            "更新成功！songlist.json 及歌曲别名已成功获取并覆盖。\n"
            f"曲目数：{song_count}，别名数：{alias_count}\n"
            f"文件修改时间：{mtime}"
        )
        
    except httpx.HTTPError as http_err:
        # 捕获 HTTP 网络请求相关的异常
        logger.error(f"更新 songlist 网络请求异常: {http_err}")
        await update_songlist.send("更新失败，网络请求异常，请稍后重试或查看控制台日志。")
        
    except Exception as e:
        # 捕获其它的异常，例如 JSON 格式错误或文件写入失败等
        logger.error(f"更新 songlist 写入或解析失败: {e}")
        await update_songlist.send("更新失败，写入或解析发生错误，请查看控制台日志。")


@update_chunirec_songlist.handle()
async def handle_update_chunirec_songlist():
    """
    从 chunirec jp2 全曲数据库同步日服新曲，转换为落雪 songlist 兼容格式后合并到本地。
    已存在的落雪曲目优先保留；本命令只新增或刷新 data_source=chunirec 的条目。
    """
    await update_chunirec_songlist.send("收到，正在从 chunirec 同步曲库...")

    token = config.chunirec_token
    if not token:
        await update_chunirec_songlist.send("同步失败：未配置 chunirec_token。")
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                CHUNIREC_SHOWALL_URL,
                params={"region": CHUNIREC_REGION, "token": token},
                timeout=30.0,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise ValueError("chunirec music/showall 返回格式异常，未得到列表")

        local_data = load_local_songlist()
        ensure_chunirec_versions(local_data)
        added, refreshed, skipped_lx = merge_chunirec_songs(local_data, payload)
        write_songlist_atomic(local_data)

        mtime = datetime.fromtimestamp(SONGLIST_FILE.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        logger.success(
            f"chunirec 曲库同步完成: added={added}, refreshed={refreshed}, skipped_lx={skipped_lx}, file={SONGLIST_FILE}"
        )
        await update_chunirec_songlist.send(
            "chunirec 曲库同步完成！\n"
            f"新增：{added}，刷新：{refreshed}，保留落雪已有：{skipped_lx}\n"
            f"文件修改时间：{mtime}"
        )
    except httpx.HTTPError as http_err:
        logger.error(f"chunirec 曲库同步网络请求异常: {http_err}")
        await update_chunirec_songlist.send("同步失败，网络请求异常，请稍后重试或查看控制台日志。")
    except Exception as e:
        logger.error(f"chunirec 曲库同步写入或解析失败: {e}")
        await update_chunirec_songlist.send("同步失败，写入或解析发生错误，请查看控制台日志。")
