"""LXNS CN catalog maintenance; aliases are matched only by original song ID."""
import json
from datetime import datetime
from pathlib import Path
from threading import RLock
from uuid import uuid4

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.permission import SUPERUSER

from .services.paths import DATA_DIR
from .services.song_model import Catalog

BASE_URL = "https://maimai.lxns.net"
API_PATH = "/api/v0/chunithm/song/list"
SONGLIST_FILE = DATA_DIR / "songlist.json"
SONGLIST_LOCK = RLock()
update_songlist = on_command("更新songlist", aliases={"更新曲库"}, permission=SUPERUSER, priority=10, block=True)


def load_local_songlist():
    with SONGLIST_LOCK:
        if not SONGLIST_FILE.exists():
            return {"songs": [], "genres": [], "versions": []}
        data = json.loads(SONGLIST_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("songs"), list):
            raise ValueError("本地 songlist.json 格式异常")
        return data


def write_songlist_atomic(data):
    from .services.storage import atomic_json
    atomic_json(SONGLIST_FILE, data)


def build_local_alias_map(data):
    from .services.song_model import numeric_id

    result = {}
    for row in data.get("songs", []):
        if (not isinstance(row, dict) or row.get("data_source") not in (None, "lx", "lxns", "lxns_cn")
                or not isinstance(row.get("aliases"), list)):
            continue
        try:
            sid = str(numeric_id(row.get("id")))
        except ValueError:
            continue
        result.setdefault(sid, []).extend(alias for alias in row["aliases"] if isinstance(alias, str))
    return result


def commit_lxns_songlist(songlist_data, alias_data):
    catalog = Catalog(songlist_data)
    if not catalog.songs:
        raise ValueError("拒绝用空曲库覆盖现有数据")
    aliases = alias_data.get("aliases") if isinstance(alias_data, dict) else alias_data
    if not isinstance(aliases, list):
        raise ValueError("invalid aliases")
    remote = {}
    for item in aliases:
        if not isinstance(item, dict) or "song_id" not in item or not isinstance(item.get("aliases"), list):
            raise ValueError("invalid alias record")
        if not all(isinstance(alias, str) for alias in item["aliases"]):
            raise ValueError("invalid alias value")
        remote.setdefault(str(item["song_id"]), []).extend(item["aliases"])
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with SONGLIST_LOCK:
        old = load_local_songlist()
        from .services.song_model import catalog_changes

        try:
            baseline = dict(old, songs=[dict(row, difficulties=row.get("difficulties", [])) for row in old["songs"]])
            changes = catalog_changes(Catalog(baseline, legacy=True), catalog)
        except (ValueError, TypeError, AttributeError):
            # A valid refresh can repair old metadata. The backup remains exact.
            changes = None
        local = build_local_alias_map(old)
        data = catalog.as_dict()
        for song in data["songs"]:
            sid = str(song["id"])
            song["aliases"] = list(dict.fromkeys(remote.get(sid, []) + song.get("aliases", []) + local.get(sid, [])))
        # The entire previous catalog (including removed songs/aliases/foreign
        # fields) survives every refresh. Never rewrite score or binding files.
        backup = None
        if SONGLIST_FILE.exists():
            folder = DATA_DIR / "catalog_backups"
            folder.mkdir(parents=True, exist_ok=True)
            backup = folder / (datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid4().hex + ".json")
            original = SONGLIST_FILE.read_bytes()
            with backup.open("xb") as stream:
                stream.write(original)
            if backup.read_bytes() != original:
                raise OSError("Catalog backup verification failed")
        write_songlist_atomic(data)
    return dict(song_count=len(data["songs"]), alias_count=sum(len(s["aliases"]) for s in data["songs"]),
                **(changes or {}), changes_available=changes is not None, backup_created=backup is not None,
                mtime=datetime.fromtimestamp(SONGLIST_FILE.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"))


@update_songlist.handle()
async def handle_update_songlist(bot: Bot, event: MessageEvent):
    from .services.maintenance import submit
    from .services.results import send_command_result
    result = await submit(bot, event, "songlist", origin="command")
    await send_command_result(update_songlist, result, finish=False)
