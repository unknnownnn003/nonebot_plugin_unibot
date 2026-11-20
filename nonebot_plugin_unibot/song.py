from .services.error_notices import error_call as _error_call
from nonebot.adapters.onebot.v11 import Bot
import json
import os
import re
from difflib import SequenceMatcher
from functools import lru_cache
import platform
import unicodedata
from io import BytesIO
from typing import Optional, Dict, Any, List, Tuple

from PIL import Image, ImageDraw, ImageFont

from nonebot import on_regex
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment, Message
from nonebot.log import logger

from .songlist_update import SONGLIST_LOCK

from .services.paths import SONGLIST_PATH as _SONGLIST_PATH
from .services.resources import jacket_image as download_jacket
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


def get_font(size: int, weight: str = "Normal") -> ImageFont.FreeTypeFont:
    # Noto 使用 Regular 而非 Normal；常见发行版只安装 Regular/Bold。
    noto_weight = "Regular" if weight == "Normal" else weight
    if platform.system() == "Windows":
        font_paths = ["msyh.ttc", "msgothic.ttc"]
    else:
        font_paths = [
            f"/usr/share/fonts/opentype/SourceHanSans/SourceHanSansSC-{weight}.otf",
            f"/usr/share/fonts/truetype/custom/SourceHanSansSC-{weight}.otf",
            f"/usr/share/fonts/opentype/noto/NotoSansCJK-{noto_weight}.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/SourceHanSans/SourceHanSansSC-Normal.otf",
            "/usr/share/fonts/truetype/custom/SourceHanSansSC-Normal.otf",
            "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        ]
    for path in font_paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            # 一个字体不可用时继续尝试，不能提前退回不支持 CJK 的默认字体。
            continue
    return ImageFont.load_default()

def draw_text(draw: ImageDraw.ImageDraw, pos: tuple, text: str, font: ImageFont.FreeTypeFont, fill=(255, 255, 255), anchor="la"):
    draw.text(pos, text, font=font, fill=fill, anchor=anchor)

def get_version_name(version_num, versions) -> str:
    try:
        number = int(version_num)
    except (ValueError, TypeError):
        return str(version_num)
    return next((v.get("title", str(version_num)) for v in (versions or [])
                 if isinstance(v, dict) and v.get("version") == number),
                str(version_num))

def wrap_text_with_height(text: str, font: ImageFont.FreeTypeFont, max_width: int, draw: ImageDraw.ImageDraw) -> tuple[str, int, int]:
    lines = []
    if not text:
        return "", 0, 0
    current_line = ""
    for char in str(text):
        test_line = current_line + char
        if draw.textbbox((0, 0), test_line, font=font)[2] <= max_width:
            current_line = test_line
        else:
            if not current_line: 
                lines.append(test_line)
                current_line = ""
            else:
                lines.append(current_line)
                current_line = char
    if current_line:
        lines.append(current_line)
    
    result = "\n".join(lines)
    bbox = draw.multiline_textbbox((0, 0), result, font=font)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    return result, width, height

def clamp_wrapped_text(text: str, font: ImageFont.FreeTypeFont, max_width: int, max_lines: int, draw: ImageDraw.ImageDraw) -> str:
    wrapped, _, _ = wrap_text_with_height(text, font, max_width, draw)
    lines = wrapped.splitlines()
    if len(lines) <= max_lines:
        return wrapped
    lines = lines[:max_lines]
    tail = lines[-1]
    while tail and draw.textbbox((0, 0), tail + "...", font=font)[2] > max_width:
        tail = tail[:-1]
    lines[-1] = tail + "..."
    return "\n".join(lines)

def match_song_by_query(query: str) -> Optional[Dict[str, Any]]:
    match, _ = pick_song_match(find_song_matches(query))
    return match

def make_empty_jacket(size: int = 200) -> Image.Image:
    return Image.new("RGBA", (size, size), (0, 0, 0, 0))


def render_song_candidates_image(query: str, matches: List[Dict[str, Any]], jacket_imgs: List[Optional[Image.Image]]) -> bytes:
    BG_COLOR = (30, 30, 35, 255)
    PANEL_COLOR = (45, 45, 52, 255)
    TEXT_MAIN = (245, 245, 245, 255)
    TEXT_SUB = (180, 180, 180, 255)
    ACCENT = (98, 114, 164, 255)

    card_width = 800
    row_h = 118
    header_h = 112
    footer_h = 58
    font_head = get_font(22, "Medium")
    measure = ImageDraw.Draw(Image.new("L", (1, 1)))
    titles = [wrap_text_with_height(f"{i}. {m['song']['title']}", font_head, 426, measure)
              for i, m in enumerate(matches, 1)]
    heights = [max(row_h, h + 74) for _, _, h in titles]
    height = header_h + sum(heights) + footer_h

    img = Image.new("RGBA", (card_width, height), BG_COLOR)
    draw = ImageDraw.Draw(img)
    font_title = get_font(30, "Bold")
    font_head = get_font(22, "Medium")
    font_body = get_font(18, "Normal")
    font_small = get_font(15, "Normal")

    draw_text(draw, (40, 32), "歌曲候选", font_title, TEXT_MAIN)
    query_wrapped = clamp_wrapped_text(f"查询：{query}", font_body, card_width - 80, 1, draw)
    draw_text(draw, (40, 72), query_wrapped, font_body, TEXT_SUB)

    y = header_h
    for index, match in enumerate(matches, 1):
        row_h = heights[index - 1]
        song = match["song"]
        title = str(song.get("title", "Unknown"))
        artist = str(song.get("artist", "-"))
        song_id = song.get("id", "-")
        source = match.get("source") or "匹配"
        matched = str(match.get("matched") or "")

        draw.rounded_rectangle([32, y + 8, card_width - 32, y + row_h - 8], radius=8, fill=PANEL_COLOR)
        draw.rounded_rectangle([32, y + 8, 42, y + row_h - 8], radius=8, fill=ACCENT)
        draw.rectangle([38, y + 8, 42, y + row_h - 8], fill=ACCENT)

        jacket_img = jacket_imgs[index - 1] if index - 1 < len(jacket_imgs) else None
        jacket = (jacket_img or make_empty_jacket(82)).resize((82, 82), Image.Resampling.LANCZOS)
        img.paste(jacket, (58, y + 18), jacket if jacket.mode == "RGBA" else None)

        text_x = 158
        title_wrapped = titles[index - 1][0]
        title_h = draw.multiline_textbbox((0, 0), title_wrapped, font=font_head)[3]
        draw_text(draw, (text_x, y + 18), title_wrapped, font_head, TEXT_MAIN)
        meta_y = y + 18 + title_h + 6
        artist_wrapped, _, _ = wrap_text_with_height(f"ID: {song_id}   Artist: {artist}", font_small, 430, draw)
        draw_text(draw, (text_x, meta_y), artist_wrapped, font_small, TEXT_SUB)

        matched_text = f"{source}: {matched}" if matched else source
        matched_wrapped = clamp_wrapped_text(matched_text, font_small, 180, 2, draw)
        draw_text(draw, (card_width - 230, y + 28), matched_wrapped, font_small, TEXT_SUB)
        y += row_h

    draw.line([(40, height - 44), (card_width - 40, height - 44)], fill=ACCENT, width=2)
    draw_text(draw, (40, height - 28), "请使用更完整的曲名、ID 或添加别名后再查询", font_small, TEXT_SUB)

    buf = BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=90)
    return buf.getvalue()

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
