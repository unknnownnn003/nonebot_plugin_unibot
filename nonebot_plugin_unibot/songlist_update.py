import json
import os
import httpx
from threading import RLock
from pathlib import Path
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote
from nonebot import get_plugin_config, on_command
from nonebot.adapters.onebot.v11 import MessageEvent, Bot
from nonebot.permission import SUPERUSER
from nonebot.log import logger
from nonebot.exception import FinishedException
from nonebot.typing import T_State

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
NAOMINET_MUSIC_URL = "https://portal.naominet.live/api/game/chuni/v2/data/music"
NAOMINET_DB_VERSION_URL = "https://portal.naominet.live/api/static/dbVersion"
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
SONGLIST_LOCK = RLock()

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
update_chunirec_upload = on_command(
    "上传chunirec",
    aliases={"上传chunirec数据", "上传日服songlist"},
    permission=SUPERUSER,
    priority=10,
    block=True,
)


def load_local_songlist() -> Dict[str, Any]:
    with SONGLIST_LOCK:
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
    with SONGLIST_LOCK:
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


def _parse_json_file(raw_bytes: bytes, file_path: str = "") -> Any:
    """解析 JSON 文件，支持多种编码、gzip 压缩和 ZIP 包装"""
    # 尝试直接解析（json.loads 支持 UTF-8/16/32 with BOM）
    try:
        return json.loads(raw_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass

    # 尝试 gzip 解压
    if raw_bytes[:2] == b"\x1f\x8b":
        import gzip
        try:
            decompressed = gzip.decompress(raw_bytes)
            return _parse_json_file(decompressed, file_path)
        except Exception:
            pass

    # 尝试 ZIP 解包（QQ 可能将文件包装为 ZIP）
    if raw_bytes[:2] == b"PK":
        import io
        import zipfile
        try:
            with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
                names = zf.namelist()
                # 优先找 JSON 文件
                json_names = [n for n in names if n.lower().endswith(".json")]
                for name in json_names + names:
                    try:
                        inner = zf.read(name)
                        return _parse_json_file(inner, f"{file_path}/{name}")
                    except Exception:
                        continue
            raise ValueError(f"ZIP 包中未找到可解析的 JSON 文件，内容: {names}")
        except Exception as e:
            raise ValueError(f"ZIP 包解析失败: {e}")

    # 尝试常见编码
    for enc in ["utf-8-sig", "utf-8", "gbk", "gb2312", "utf-16", "latin-1"]:
        try:
            return json.loads(raw_bytes.decode(enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue

    # 最后一次尝试：记录前 50 字节用于调试
    preview = raw_bytes[:50]
    logger.error(f"无法解析 JSON 文件 {file_path}，前 50 字节: {preview!r}")
    raise json.JSONDecodeError(
        f"无法解析文件，已尝试所有编码。前 50 字节: {preview!r}",
        preview.decode("latin-1", errors="replace"), 0,
    )


def song_match_key(song: Dict[str, Any]) -> Tuple[str, str]:
    return (normalize_title_key(song.get("title")), normalize_title_key(song.get("artist")))


def pick_first_value(data: Dict[str, Any], keys: List[str]) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return None


def extract_naominet_rows(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("data"), (dict, list)):
        payload = payload["data"]
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("content", "items", "records", "list", "songs", "result"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def get_naominet_page_total(payload: Any) -> Optional[int]:
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        payload = payload["data"]
    if not isinstance(payload, dict):
        return None
    for key in ("totalPages", "total_pages", "pages", "pageCount"):
        value = payload.get(key)
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def build_naominet_headers(token: str, session: Optional[str]) -> Dict[str, str]:
    auth_value = token if token.lower().startswith("bearer ") else f"Bearer {token}"
    headers = {
        "Authorization": auth_value,
        "Referer": "https://portal.naominet.live/",
        "Accept": "application/json",
    }
    if session:
        headers["Cookie"] = f"SESSION={session}"
    return headers


async def fetch_naominet_id_map(
    client: httpx.AsyncClient,
    token: str,
    session: Optional[str] = None,
) -> Tuple[Dict[Tuple[str, str], str], str]:
    headers = build_naominet_headers(token, session)
    result: Dict[Tuple[str, str], str] = {}

    probe = await client.get(
        NAOMINET_DB_VERSION_URL,
        headers=headers,
        timeout=20.0,
    )
    probe.raise_for_status()

    response = await client.get(
        NAOMINET_MUSIC_URL,
        headers=headers,
        timeout=20.0,
    )
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict):
        status = payload.get("status")
        code = status.get("code") if isinstance(status, dict) else None
        if code == 94011:
            raise PermissionError("naominet 返回 Unauthorized，需要可访问该接口的登录令牌")
    rows = extract_naominet_rows(payload)
    if not rows:
        raise ValueError("naominet data/music 未返回可用歌曲列表")
    for row in rows:
        numeric_id = pick_first_value(row, ["musicId", "music_id", "songId", "song_id", "id"])
        title = pick_first_value(row, ["title", "name", "songName", "song_name"])
        artist = pick_first_value(row, ["artist", "artistName", "artist_name"])
        if numeric_id is None or title is None:
            continue
        numeric_id_str = str(numeric_id).strip()
        if not numeric_id_str.isdigit():
            continue
        result[(normalize_title_key(title), normalize_title_key(artist))] = numeric_id_str
        result[(normalize_title_key(title), "")] = numeric_id_str

    status_text = f"naominet 匹配表：{len(result)} 条键，读取 {len(rows)} 首"
    if not result:
        status_text = "naominet 未返回可用歌曲数字 ID"
    return result, status_text


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


def convert_chunirec_song(
    item: Dict[str, Any],
    existing_aliases: Dict[str, List[str]],
    naominet_id_map: Optional[Dict[Tuple[str, str], str]] = None,
) -> Optional[Dict[str, Any]]:
    meta = item.get("meta")
    data = item.get("data")
    if not isinstance(meta, dict) or not isinstance(data, dict):
        return None

    chunirec_id = str(meta.get("id") or "").strip()
    title = str(meta.get("title") or "").strip()
    if not chunirec_id or not title:
        return None
    numeric_id = None
    if naominet_id_map:
        numeric_id = naominet_id_map.get((normalize_title_key(title), normalize_title_key(meta.get("artist"))))
        numeric_id = numeric_id or naominet_id_map.get((normalize_title_key(title), ""))
    song_id = numeric_id or chunirec_id

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
    song = {
        "id": song_id,
        "title": title,
        "artist": meta.get("artist") or "-",
        "genre": map_chunirec_genre(meta.get("genre")),
        "bpm": meta.get("bpm") or 0,
        "version": version,
        "release": meta.get("release"),
        "difficulties": sorted(difficulties, key=lambda d: d.get("difficulty", 99)),
        "aliases": existing_aliases.get(song_id, existing_aliases.get(chunirec_id, [])),
        "data_source": CHUNIREC_SOURCE,
        "chunirec_id": chunirec_id,
        "chunirec_url": CHUNIREC_DB_MUSIC_URL.format(title=quote(title, safe=""), song_id=chunirec_id),
    }
    if numeric_id:
        song["rinnet_id"] = numeric_id
        song["jacket_id"] = numeric_id
    return song


def merge_chunirec_songs(
    local_data: Dict[str, Any],
    chunirec_payload: List[Dict[str, Any]],
    naominet_id_map: Optional[Dict[Tuple[str, str], str]] = None,
) -> Tuple[int, int, int]:
    songs = local_data.setdefault("songs", [])
    if not isinstance(songs, list):
        raise ValueError("本地 songlist.json 中 songs 不是列表")

    existing_aliases = build_local_alias_map(local_data)
    existing_by_id = {str(song.get("id")): idx for idx, song in enumerate(songs) if isinstance(song, dict) and song.get("id") is not None}
    existing_by_chunirec_id = {
        str(song.get("chunirec_id") or song.get("id")): idx
        for idx, song in enumerate(songs)
        if isinstance(song, dict) and song.get("data_source") == CHUNIREC_SOURCE and (song.get("chunirec_id") or song.get("id"))
    }
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
        converted = convert_chunirec_song(raw_song, existing_aliases, naominet_id_map)
        if not converted:
            continue

        song_id = str(converted["id"])
        chunirec_id = str(converted.get("chunirec_id") or song_id)
        title_key = normalize_title_key(converted.get("title"))
        title_artist_key = (title_key, normalize_title_key(converted.get("artist")))

        existing_index = existing_by_id.get(song_id)
        if existing_index is None:
            existing_index = existing_by_chunirec_id.get(chunirec_id)
        if existing_index is not None:
            old_song = songs[existing_index]
            if isinstance(old_song, dict) and old_song.get("data_source") == CHUNIREC_SOURCE:
                converted["aliases"] = old_song.get("aliases", converted["aliases"])
                songs[existing_index] = converted
                refreshed += 1
                existing_by_id[song_id] = existing_index
                existing_by_chunirec_id[chunirec_id] = existing_index
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
        existing_by_chunirec_id[chunirec_id] = len(songs) - 1
        added += 1

    return added, refreshed, skipped_lx

def commit_lxns_songlist(songlist_data, alias_data):
    if not isinstance(songlist_data, dict) or not isinstance(songlist_data.get("songs"), list):
        raise ValueError("invalid songlist")
    aliases = alias_data.get("aliases", []) if isinstance(alias_data, dict) else alias_data
    if not isinstance(aliases, list):
        raise ValueError("invalid aliases")
    alias_map = {item["song_id"]: item.get("aliases", []) for item in aliases if isinstance(item, dict) and "song_id" in item}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with SONGLIST_LOCK:
        # 读取本地已存在的 songlist.json 保留用户自行添加的别名
        local_data = load_local_songlist()
        local_alias_map = build_local_alias_map(local_data)
        old_by_id, old_by_title = build_local_song_maps(local_data)

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
    return dict(song_count=len(songlist_data["songs"]), alias_count=sum(len(row.get("aliases", []) or []) for row in songlist_data["songs"]),
                mtime=datetime.fromtimestamp(SONGLIST_FILE.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"))


def commit_chunirec_songlist(payload, id_map):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with SONGLIST_LOCK:
        local = load_local_songlist()
        ensure_chunirec_versions(local)
        added, refreshed, skipped = merge_chunirec_songs(local, payload, id_map)
        write_songlist_atomic(local)
    return dict(added=added, refreshed=refreshed, skipped_lx=skipped,
                mtime=datetime.fromtimestamp(SONGLIST_FILE.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"))


@update_songlist.handle()
async def handle_update_songlist(bot: Bot, event: MessageEvent):
    from .services.maintenance import submit
    result = await submit(bot, event, "songlist", origin="command")
    from .services.results import send_command_result
    await send_command_result(update_songlist, result, finish=False)


@update_chunirec_songlist.handle()
async def handle_update_chunirec_songlist(bot: Bot, event: MessageEvent):
    from .services.maintenance import submit
    result = await submit(bot, event, "chunirec", origin="command")
    from .services.results import send_command_result
    await send_command_result(update_chunirec_songlist, result, finish=False)


@update_chunirec_upload.handle()
async def _(event: MessageEvent, state: T_State):
    """检查消息中是否附带文件，有则存入 state 跳过 prompt"""
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


@update_chunirec_upload.got("file_msg", prompt="请上传由 fetch_chunirec_data.py 生成的 JSON 数据文件")
async def handle_chunirec_upload(bot: Bot, event: MessageEvent):
    from .services.maintenance import submit_import
    result = await submit_import(bot, event, origin="command")
    from .services.results import send_command_result
    await send_command_result(update_chunirec_upload, result, finish=False)
