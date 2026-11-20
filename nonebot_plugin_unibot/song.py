from .services.error_notices import error_call as _error_call
from nonebot.adapters.onebot.v11 import Bot
import json
import os
import re
from difflib import SequenceMatcher
from functools import lru_cache
import unicodedata
from typing import Optional, Dict, Any, List, Tuple

from PIL import Image

from nonebot import on_regex
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment, Message
from nonebot.log import logger

from .songlist_update import SONGLIST_LOCK

from .services.paths import SONGLIST_PATH as _SONGLIST_PATH
from .services.resources import jacket_image as download_jacket
from .services.chat_cards import INK, LINE, MUTED, PAPER, TEAL, artwork, frame, lines
from .services.image_components import label, png, rounded, wrap
from .services.song_images import render_song_image

SONGLIST_PATH = str(_SONGLIST_PATH)

song_query = on_regex(r"^(.+)是什么歌$", priority=10, block=True)


try:
    import zhconv
except ImportError:
    zhconv = None

try:
    import jaconv
except ImportError:
    jaconv = None

from nonebot import on_command
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

def _raw_term(value):
    from .services.song_aliases import normalized
    return normalized(value)


def _compact(value):
    return "".join(char for char in value if char.isalnum())


@lru_cache(maxsize=8192)
def _term_forms(raw):
    """Cache conversions of literal terms, independent of any Catalog revision."""
    form = unicodedata.normalize("NFKC", raw)
    if zhconv is not None:
        form = zhconv.convert(form, "zh-cn")
    # Fold katakana and hiragana without stripping Japanese voicing marks.
    kana = "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in form)
    forms = {_compact(kana)}
    if jaconv is not None and any("ぁ" <= char <= "ゖ" for char in kana):
        roman = jaconv.kana2alphabet(kana)
        forms.add(_compact(roman))
        forms.add(_compact(re.sub(r"([aeiou])ー|([aeiou])-", lambda m: (m[1] or m[2]) * 2, roman)))
        forms.add(_compact(re.sub(r"ou|oo|uu", lambda m: m[0][0], roman)))
    # Romanized spellings may use circumflexes or macrons for long vowels.
    folded = "".join(c for c in unicodedata.normalize("NFD", kana) if c not in "\u0302\u0304")
    forms.add(_compact(unicodedata.normalize("NFC", folded)))
    return tuple(sorted(forms - {""}))


def _fuzzy_score(queries, terms):
    score = 0.0
    for query in queries:
        for term in terms:
            if len(query) >= 4 and min(len(query), len(term)) >= 3:
                minimum = 0.75 if len(query) < 6 else 0.70
                matcher = SequenceMatcher(None, query, term, autojunk=False)
                if matcher.real_quick_ratio() >= minimum and matcher.quick_ratio() >= minimum:
                    similarity = matcher.ratio()
                    if similarity >= minimum:
                        score = max(score, similarity * 0.69)
    return score


def _read_songlist_data() -> Optional[Dict[str, Any]]:
    if not os.path.exists(SONGLIST_PATH):
        return None
    try:
        with open(SONGLIST_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"读取曲目列表失败: {e}")
        return None


def _write_songlist_data(data: Dict[str, Any]) -> None:
    temp_path = SONGLIST_PATH + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        f.write("\n")
    os.replace(temp_path, SONGLIST_PATH)


def _edit_alias(song_id, alias_name, remove):
    from .services.song_aliases import edit_local
    with SONGLIST_LOCK:
        data = _read_songlist_data()
        if not data or not any(str(row["id"]) == str(song_id) and row.get("data_source") in
                               (None, "lx", "lxns", "lxns_cn") for row in data["songs"]):
            return False, False
        changed = edit_local(data, str(song_id), alias_name, remove)
        if changed:
            _write_songlist_data(data)
        return True, changed


def add_song_alias(song_id, alias_name):
    return _edit_alias(song_id, alias_name, False)


def remove_song_alias(song_id, alias_name):
    return _edit_alias(song_id, alias_name, True)


def find_song_matches(query: str, *, catalog=None, exact_only=False) -> List[Dict[str, Any]]:
    if not query.strip():
        return []
    if catalog is None:
        from .services.song_model import Catalog
        with SONGLIST_LOCK:
            data = _read_songlist_data()
        if not data:
            return []
        catalog = Catalog(data, legacy=True)

    raw = _raw_term(query)
    numeric = unicodedata.normalize("NFKC", raw)
    is_id = numeric.isascii() and numeric.isdecimal()
    versions = catalog.metadata.get("versions", [])

    def build_match(sid, score, source, value, full=True):
        view = catalog.query_song(sid) if full else catalog.songs[sid].as_dict()
        view["_versions"] = versions
        return {"song": view, "score": score, "source": source, "matched": value}

    if is_id:
        try:
            sid = int(numeric)
        except ValueError:
            return []
        if sid in catalog.songs:
            return [build_match(sid, 1.2, "ID", str(sid))]

    query_forms = {raw, *_term_forms(raw)}
    exact, searchable = {}, []
    for sid, song in catalog.songs.items():
        attrs = song.attributes
        title = attrs["title"]
        aliases = attrs.get("aliases")
        terms = [("曲名", title)]
        if isinstance(aliases, list):
            terms.extend(("别名", alias) for alias in aliases if isinstance(alias, str))
        for source, value in terms:
            term = _raw_term(value)
            if not term:
                continue
            forms = {term, *_term_forms(term)}
            if term == raw:
                score = 1.05
            elif query_forms.intersection(forms):
                score = 1.0
            elif not exact_only:
                searchable.append((sid, source, value, forms))
                continue
            else:
                continue
            key = (-score, source != "曲名", sid, value)
            if sid not in exact or key < exact[sid][0]:
                exact[sid] = (key, source)

    if exact:
        ranked = sorted(exact.values())
        # Keep collisions at the strongest exact tier. Never let the display
        # limit turn a collision into an automatic selection.
        ranked = [row for row in ranked if row[0][0] == ranked[0][0][0]]
        return [build_match(key[2], -key[0], source, key[3], len(ranked) == 1) for key, source in ranked]
    if exact_only:
        return []
    matches = {}
    if is_id:
        for sid in catalog.songs:
            if str(sid).startswith(numeric):
                matches[sid] = ((-0.75, False, sid, str(sid)), "ID")
    for fuzzy in (False, True):
        for sid, source, value, forms in searchable:
            if fuzzy:
                score = _fuzzy_score(query_forms, forms)
            else:
                score = max((0.70 + 0.25 * len(q) / len(term)
                             for q in query_forms for term in forms if q in term), default=0)
            if score:
                key = (-score, source != "曲名", sid, value)
                if sid not in matches or key < matches[sid][0]:
                    matches[sid] = (key, source)
        if matches:
            break
    return [build_match(key[2], -key[0], source, key[3], len(matches) == 1)
            for key, source in sorted(matches.values())]


def pick_song_match(matches: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    if len(matches) == 1 and matches[0]["score"] >= 0.7:
        return matches[0]["song"], []
    return None, matches


def get_version_name(version_num, versions) -> str:
    try:
        number = int(version_num)
    except (ValueError, TypeError):
        return str(version_num)
    return next((v.get("title", str(version_num)) for v in (versions or [])
                 if isinstance(v, dict) and v.get("version") == number),
                str(version_num))

def match_song_by_query(query: str) -> Optional[Dict[str, Any]]:
    match, _ = pick_song_match(find_song_matches(query))
    return match

def render_song_candidates_image(query: str, matches: List[Dict[str, Any]],
                                 jacket_imgs: List[Optional[Image.Image]], total: int, page: int) -> bytes:
    rows = []
    common_alias = matches[0].get("matched") if matches and all(
        "别名" in (match.get("source") or "") and match.get("matched") == matches[0].get("matched")
        for match in matches) else None
    for match in matches:
        song = match["song"]
        title = song.get("title") or "—"
        size = 30
        titles = wrap(title, size, 580)
        if len(titles) > 3:
            size = 27
            titles = wrap(title, size, 580)
        source = match.get("source") or "匹配"
        hit = match.get("matched")
        metadata = f"ID {song['id']}"
        if hit and hit != common_alias and ("别名" in source or hit not in (title, str(song["id"]))):
            metadata += f"  ·  {source}  {hit}"
        details = wrap(metadata, 23, 580)
        height = max(108, 22 + 40 * len(titles) + 32 * len(details))
        rows.append((titles, details, size, height))
    term = f"匹配别名 {common_alias}" if common_alias == query else query
    if common_alias and common_alias != query:
        term += f"  ·  匹配别名 {common_alias}"
    summary = wrap(f"{term}  ·  共 {total} 首  ·  第 {page}/{(total + 7) // 8} 页", 25, 736)
    y = 88 + 35 * len(summary)
    canvas = frame("歌曲候选", y + 44 + sum(row[-1] + 8 for row in rows))
    lines(canvas, summary, 28, 78, 772, 25, 35, MUTED)
    for index, (titles, details, size, height) in enumerate(rows):
        rounded(canvas, (20, y, 780, y + height), 14, PAPER, LINE)
        artwork(canvas, jacket_imgs[index] if index < len(jacket_imgs) else None,
                (32, y + 10, 120, y + 98))
        top = y + (height - 40 * len(titles) - 32 * len(details)) // 2
        top = lines(canvas, titles, 139, top, 760, size, 40, INK)
        lines(canvas, details, 139, top, 760, 23, 32, TEAL)
        y += height + 8
    label(canvas, (28, y, 772, y + 38), "按 ID 查询  ·  翻页请加 --page 页码", 22, MUTED)
    return png(canvas)


async def download_match_jacket(match: Dict[str, Any]) -> Optional[Image.Image]:
    song = match.get("song", {})
    try:
        from .services.song_model import jacket_id
        song_id = jacket_id(song)
    except (TypeError, ValueError):
        song_id = 0
    return await download_jacket(song_id)


lookup_cmd = on_command("查歌", priority=10, block=True)


async def _search_reply(matcher, bot, event, query):
    from .services.media_jobs import submit
    from .services.results import send_command_result
    if not query:
        await matcher.finish("请输入曲名、别名或歌曲 ID。")
    result = await submit(bot, event, "song.search", {"query": query}, origin="command")
    await send_command_result(matcher, result, finish=True)


@song_query.handle()
async def _(bot: Bot, event: MessageEvent):
    await _search_reply(song_query, bot, event, event.get_plaintext().strip()[:-4].strip())


@lookup_cmd.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    await _search_reply(lookup_cmd, bot, event, args.extract_plain_text().strip())


add_alias_cmd = on_command("添加别名", priority=10, block=True)
del_alias_cmd = on_command("删除别名", permission=SUPERUSER, priority=10, block=True)
view_alias_cmd = on_command("查看别名", priority=10, block=True)

@add_alias_cmd.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    msg_args = args.extract_plain_text().strip().split(maxsplit=1)
    if len(msg_args) < 2:
        await _error_call(add_alias_cmd.finish, "格式错误。请使用: /添加别名 <song_id> <别名>")
    
    song_id_str, alias_name = msg_args[0], msg_args[1].strip()
    song_id = song_id_str

    try:
        found, _added = add_song_alias(song_id, alias_name)
    except Exception:
        await _error_call(add_alias_cmd.finish, "保存曲目列表失败。")
        return
    if not found:
         await _error_call(add_alias_cmd.finish, f"未找到 ID 为 {song_id} 的曲目。")
         return

    await add_alias_cmd.finish(f"已成功为 ID {song_id} 添加别名。")

@del_alias_cmd.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    msg_args = args.extract_plain_text().strip().split(maxsplit=1)
    if len(msg_args) < 2:
        await _error_call(del_alias_cmd.finish, "格式错误。请使用: /删除别名 <song_id> <别名>")
    
    song_id_str, alias_name = msg_args[0], msg_args[1].strip()
    song_id = song_id_str

    try:
        found, removed = remove_song_alias(song_id, alias_name)
    except Exception:
        await _error_call(del_alias_cmd.finish, "保存曲目列表失败。")
        return
    if not found:
        await _error_call(del_alias_cmd.finish, f"未找到 ID 为 {song_id} 的曲目。")
        return
         
    if not removed:
        await _error_call(del_alias_cmd.finish, f"ID {song_id} 下不存在该别名。")
        return

    await del_alias_cmd.finish(f"成功为 ID {song_id} 删除别名。")

@view_alias_cmd.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    query = args.extract_plain_text().strip()
    if not query:
        await _error_call(view_alias_cmd.finish, "格式错误。请使用: /查看别名 <模糊搜索/名字/ID/别名>")
        
    from .services.song import prepare, page_hint
    result = await prepare(query)
    local_song = result["song"]
    if result["ambiguous"]:
        lines = [f"{m['song']['id']} · {m['song']['title']}" for m in result["ambiguous"]]
        await view_alias_cmd.finish(page_hint(result) + "\n" + "\n".join(lines))

    if not local_song:
        await _error_call(view_alias_cmd.finish, "未找到匹配的曲目。")
        
    aliases = local_song.get("aliases", [])
    title = local_song.get("title", "")
    song_id = local_song.get("id", "")
    
    if not aliases:
        await view_alias_cmd.finish(f"曲目【{title}】(ID: {song_id}) 目前没有别名记录。")
    
    alias_str = "、".join(map(str, aliases))
    await view_alias_cmd.finish(f"曲目【{title}】(ID: {song_id}) 的现有别名为：\n{alias_str}")
