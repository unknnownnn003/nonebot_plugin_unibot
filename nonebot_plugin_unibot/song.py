import json
import os
import difflib
import httpx
import platform
import asyncio
from io import BytesIO
from typing import Optional, Dict, Any

from PIL import Image, ImageDraw, ImageFont, ImageFilter

from nonebot import on_regex, get_plugin_config
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment, Message
from nonebot.log import logger

from .config import Config

config = get_plugin_config(Config)

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PLUGIN_DIR, "data")
SONGLIST_PATH = os.path.join(DATA_DIR, "songlist.json")
JACKET_DIR = os.path.join(DATA_DIR, "jacket")

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

def normalize_str(s: str) -> str:
    s = str(s).strip().lower()
    if zhconv:
        try:
            s = zhconv.convert(s, "zh-cn")
        except:
            pass
    if jaconv:
        try:
            s = jaconv.kana2alphabet(jaconv.kata2hira(s))
        except:
            pass
    return "".join(c for c in s if c.isalnum())

def get_font(size: int, weight: str = "Normal") -> ImageFont.FreeTypeFont:
    try:
        if platform.system() == "Windows":
            return ImageFont.truetype("msyh.ttc", size)
        else:
            font_paths = [
                f"/usr/share/fonts/opentype/SourceHanSans/SourceHanSansSC-{weight}.otf",
                "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc",
                "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"
            ]
            for p in font_paths:
                if os.path.exists(p):
                    return ImageFont.truetype(p, size)
            return ImageFont.load_default()
    except Exception:
        return ImageFont.load_default()

def draw_text(draw: ImageDraw.ImageDraw, pos: tuple, text: str, font: ImageFont.FreeTypeFont, fill=(255, 255, 255), anchor="la"):
    draw.text(pos, text, font=font, fill=fill, anchor=anchor)

def get_version_name(version_num) -> str:
    try:
        with open(SONGLIST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            for v in data.get("versions", []):
                if v.get("version") == int(version_num):
                    return v.get("title", str(version_num))
    except Exception:
        pass
    return str(version_num)

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

def match_song_by_query(query: str) -> Optional[Dict[str, Any]]:
    if not os.path.exists(SONGLIST_PATH):
        return None
        
    try:
        with open(SONGLIST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f"读取曲目列表失败: {e}")
        return None
        
    songs = data.get("songs", [])
    query_exact = query.strip().lower()
    query_norm = normalize_str(query)
    
    # 1. 精确匹配
    for song in songs:
        if str(song.get("id")) == query_exact:
            return song
        
        candidates_exact = [song.get("title", "").strip().lower()] + [str(a).strip().lower() for a in song.get("aliases", [])]
        if query_exact in candidates_exact:
            return song
            
        candidates_norm = [normalize_str(c) for c in candidates_exact]
        if query_norm and query_norm in candidates_norm:
            return song
            
    # 2. 包含匹配与模糊匹配 (如果有多个取匹配度最高)
    best_match = None
    best_ratio = 0.0
    
    for song in songs:
        candidates = [song.get("title", "")] + [str(a) for a in song.get("aliases", [])]
        for cand in candidates:
            cand_norm = normalize_str(cand)
            if not cand_norm or not query_norm:
                continue
                
            ratio = difflib.SequenceMatcher(None, query_norm, cand_norm).ratio()
            if query_norm in cand_norm:
                ratio += 0.2
                
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = song
                
    if best_ratio > 0.4:
        return best_match
        
    return None

async def fetch_song_detail(song_id: int) -> Optional[Dict[str, Any]]:
    url = f"https://maimai.lxns.net/api/v0/chunithm/song/{song_id}"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, timeout=10.0)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.error(f"Failed to fetch song detail for ID {song_id}: {e}")
    return None

async def download_jacket(song_id: int) -> Optional[Image.Image]:
    jacket_path = os.path.join(JACKET_DIR, f"{song_id}.png")
    if os.path.exists(jacket_path):
        try:
            return Image.open(jacket_path).convert("RGBA")
        except Exception:
            pass
            
    url = f"https://assets2.lxns.net/chunithm/jacket/{song_id}.png"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, timeout=10.0)
            if resp.status_code == 200:
                if not os.path.exists(JACKET_DIR):
                    os.makedirs(JACKET_DIR)
                with open(jacket_path, "wb") as f:
                    f.write(resp.content)
                return Image.open(BytesIO(resp.content)).convert("RGBA")
        except Exception as e:
            logger.error(f"Failed to download jacket for ID {song_id}: {e}")
            
    # 如果下载失败，返回一张纯色占位图
    img = Image.new("RGBA", (200, 200), (100, 100, 100, 255))
    return img

def render_song_image(local_song: Dict[str, Any], api_detail: Dict[str, Any], jacket_img: Image.Image) -> bytes:
    # 颜色常数
    BG_COLOR = (30, 30, 35, 255)
    PANEL_COLOR = (45, 45, 52, 255)
    TEXT_MAIN = (245, 245, 245, 255)
    TEXT_SUB = (180, 180, 180, 255)

    DIFF_COLORS = [
        (46, 204, 113),   # BASIC
        (241, 196, 15),   # ADVANCED
        (231, 76, 60),    # EXPERT
        (155, 89, 182),   # MASTER
        (30, 30, 30),     # ULTIMA
        (243, 156, 18)    # WORLD'S END
    ]
    DIFF_NAMES = ["BASIC", "ADVANCED", "EXPERT", "MASTER", "ULTIMA", "WORLD'S END"]

    font_title = get_font(36, "Bold")
    font_head = get_font(24, "Medium")
    font_body = get_font(20, "Normal")
    font_small = get_font(16, "Normal")
    font_diff = get_font(22, "Bold")

    song_id = local_song.get("id", api_detail.get("id"))
    title = api_detail.get("title") or local_song.get("title", "Unknown")
    artist = api_detail.get("artist") or local_song.get("artist", "-")
    genre = api_detail.get("genre") or local_song.get("genre", "-")
    bpm = api_detail.get("bpm") or local_song.get("bpm", 0)
    version_raw = api_detail.get("version") or local_song.get("version", "-")
    if isinstance(version_raw, int) or str(version_raw).isdigit():
        version = get_version_name(version_raw)
    else:
        version = str(version_raw)

    local_diffs = {d["difficulty"]: d for d in local_song.get("difficulties", [])}
    api_diffs = {d.get("difficulty", idx): d for idx, d in enumerate(api_detail.get("difficulties", []))}

    card_width = 800
    
    # 动态布局准备：先画在一张极长的大画布上，以便处理换行
    temp_img = Image.new("RGBA", (card_width, 4000), BG_COLOR)
    draw = ImageDraw.Draw(temp_img)

    jacket_size = 200
    jacket_img = jacket_img.resize((jacket_size, jacket_size), Image.Resampling.LANCZOS)
    temp_img.paste(jacket_img, (40, 40), jacket_img if jacket_img.mode == "RGBA" else None)

    info_x = 40 + jacket_size + 30
    max_text_width = card_width - info_x - 40

    current_y = 40
    
    title_wrapped, _, t_h = wrap_text_with_height(title, font_title, max_text_width, draw)
    draw_text(draw, (info_x, current_y), title_wrapped, font_title, TEXT_MAIN)
    current_y += t_h + 15
    
    artist_wrapped, _, a_h = wrap_text_with_height(f"Artist: {artist}", font_head, max_text_width, draw)
    draw_text(draw, (info_x, current_y), artist_wrapped, font_head, TEXT_SUB)
    current_y += a_h + 20

    draw_text(draw, (info_x, current_y), f"ID: {song_id}   BPM: {bpm}", font_body, TEXT_SUB)
    current_y += 30
    
    locked_val = api_detail.get("locked")
    if locked_val is None:
        locked_val = local_song.get("locked", False)
    locked_str = "Yes" if locked_val else "No"
    
    genre_wrapped, _, g_h = wrap_text_with_height(f"Genre: {genre}   Locked: {locked_str}", font_body, max_text_width, draw)
    draw_text(draw, (info_x, current_y), genre_wrapped, font_body, TEXT_SUB)
    current_y += g_h + 10
    
    ver_wrapped, _, v_h = wrap_text_with_height(f"Version: {version}", font_body, max_text_width, draw)
    draw_text(draw, (info_x, current_y), ver_wrapped, font_body, TEXT_SUB)
    current_y += v_h + 30

    start_y = max(current_y, 40 + jacket_size + 30)

    for diff_idx in sorted(local_diffs.keys()):
        ld = local_diffs[diff_idx]
        ad = api_diffs.get(diff_idx, {})

        color = DIFF_COLORS[diff_idx] if diff_idx < len(DIFF_COLORS) else (100, 100, 100)
        diff_name = DIFF_NAMES[diff_idx] if diff_idx < len(DIFF_NAMES) else f"DIFF {diff_idx}"
        
        designer = ld.get("note_designer", ad.get("note_designer", "-"))
        if str(designer) == "0": designer = "-"
        designer_str = f"Designer: {designer}"
        
        des_w, _, d_h = wrap_text_with_height(designer_str, font_small, card_width - 120, draw)
        
        total = ad.get("total") or ad.get("notes", {}).get("total", "?")
        tap = ad.get("tap") or ad.get("notes", {}).get("tap", "?")
        hold = ad.get("hold") or ad.get("notes", {}).get("hold", "?")
        slide = ad.get("slide") or ad.get("notes", {}).get("slide", "?")
        air = ad.get("air") or ad.get("notes", {}).get("air", "?")
        flick = ad.get("flick") or ad.get("notes", {}).get("flick", "?")
        
        notes_str = f"Total: {total} | Tap: {tap} | Hold: {hold} | Slide: {slide} | Air: {air} | Flick: {flick}"
        if total == "?" and "notes" in ad:
            n = ad["notes"]
            notes_str = f"Total: {n.get('total', '?')} | Tap: {n.get('tap', '?')} | Hold: {n.get('hold', '?')} | Slide: {n.get('slide', '?')} | Air: {n.get('air', '?')} | Flick: {n.get('flick', '?')}"
        
        notes_w, _, n_h = wrap_text_with_height(notes_str, font_small, card_width - 120, draw)
        
        content_h = 40 + d_h + n_h
        box_h = max(95, content_h + 30)
        diff_item_height = box_h + 15
        
        margin_top = (box_h - content_h) / 2

        draw.rounded_rectangle([40, start_y, card_width - 40, start_y + box_h], radius=8, fill=PANEL_COLOR)

        if diff_idx == 4:
            stripe_color = (20, 20, 20)
            text_color = (255, 255, 255)
        else:
            stripe_color = color
            text_color = color

        draw.rounded_rectangle([40, start_y, 55, start_y + box_h], radius=8, fill=stripe_color)
        draw.rectangle([48, start_y, 55, start_y + box_h], fill=stripe_color)

        if diff_idx == 4:
            draw.rounded_rectangle([40, start_y, card_width - 40, start_y + box_h], radius=8, fill=None, outline=(220, 50, 50), width=2)

        level_val = ld.get("level_value", ld.get("level", "?"))
        level_str = ""
        try:
            level_str = f"{float(level_val):.1f}"
        except:
            level_str = str(level_val)
        
        draw_text(draw, (70, start_y + margin_top), f"{diff_name} {level_str}", font_diff, text_color)

        draw_text(draw, (70, start_y + margin_top + 30), des_w, font_small, TEXT_SUB)
        draw_text(draw, (70, start_y + margin_top + 30 + d_h + 10), notes_w, font_small, TEXT_SUB)

        start_y += diff_item_height

    final_img = temp_img.crop((0, 0, card_width, int(start_y + 20)))
    buf = BytesIO()
    final_img = final_img.convert("RGB")
    final_img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


@song_query.handle()
async def _(event: MessageEvent):
    msg = event.get_plaintext().strip()
    match_str = msg[:-4].strip()  # Remove "是什么歌"
    if not match_str:
        return
        
    local_song = match_song_by_query(match_str)
    if not local_song:
        await song_query.finish(f"没有找到与 '{match_str}' 匹配的曲目。")
        return
        
    song_id = local_song.get("id")
    if not song_id:
        await song_query.finish("匹配到的曲目数据中缺少ID。")
        return
        
    detail = await fetch_song_detail(song_id)
    if not detail:
        await song_query.finish(f"找到了曲目 {local_song.get('title')} (ID: {song_id})，但在获取详情时失败，可能网络连接有误或接口异常。")
        return
        
    jacket_img = await download_jacket(song_id)
    image_bytes = render_song_image(local_song, detail, jacket_img)
    
    await song_query.finish(MessageSegment.image(image_bytes))

# ==== 别名管理功能 ====
add_alias_cmd = on_command("添加别名", priority=10, block=True)
del_alias_cmd = on_command("删除别名", permission=SUPERUSER, priority=10, block=True)
view_alias_cmd = on_command("查看别名", priority=10, block=True)

@add_alias_cmd.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    msg_args = args.extract_plain_text().strip().split(maxsplit=1)
    if len(msg_args) < 2:
        await add_alias_cmd.finish("格式错误。请使用: /添加别名 <song_id> <别名>")
    
    song_id_str, alias_name = msg_args[0], msg_args[1].strip()
    
    if not song_id_str.isdigit():
        await add_alias_cmd.finish("song_id必须为数字。")
         
    song_id = int(song_id_str)
    
    try:
        with open(SONGLIST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        await add_alias_cmd.finish("读取曲目列表失败。")
        return
        
    found = False
    for song in data.get("songs", []):
         if song.get("id") == song_id:
              found = True
              aliases = song.setdefault("aliases", [])
              if alias_name not in aliases:
                   aliases.append(alias_name)
              break
              
    if not found:
         await add_alias_cmd.finish(f"未找到 ID 为 {song_id} 的曲目。")
         return
         
    with open(SONGLIST_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        
    await add_alias_cmd.finish(f"已成功为 ID {song_id} 添加别名：{alias_name}。")

@del_alias_cmd.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    msg_args = args.extract_plain_text().strip().split(maxsplit=1)
    if len(msg_args) < 2:
        await del_alias_cmd.finish("格式错误。请使用: /删除别名 <song_id> <别名>")
    
    song_id_str, alias_name = msg_args[0], msg_args[1].strip()
    
    if not song_id_str.isdigit():
        await del_alias_cmd.finish("song_id必须为数字。")
         
    song_id = int(song_id_str)
    
    try:
        with open(SONGLIST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        await del_alias_cmd.finish("读取曲目列表失败。")
        return
        
    found = False
    removed = False
    for song in data.get("songs", []):
         if song.get("id") == song_id:
              found = True
              aliases = song.get("aliases", [])
              if alias_name in aliases:
                   aliases.remove(alias_name)
                   removed = True
              break
              
    if not found:
        await del_alias_cmd.finish(f"未找到 ID 为 {song_id} 的曲目。")
        return
         
    if not removed:
        await del_alias_cmd.finish(f"ID {song_id} 下不存在别名：{alias_name}。")
        return
         
    with open(SONGLIST_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        
    await del_alias_cmd.finish(f"成功为 ID {song_id} 删除别名：{alias_name}")

@view_alias_cmd.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    query = args.extract_plain_text().strip()
    if not query:
        await view_alias_cmd.finish("格式错误。请使用: /查看别名 <模糊搜索/名字/ID/别名>")
        
    local_song = match_song_by_query(query)
    if not local_song:
        await view_alias_cmd.finish(f"未找到与 '{query}' 匹配的曲目。")
        
    aliases = local_song.get("aliases", [])
    title = local_song.get("title", "")
    song_id = local_song.get("id", "")
    
    if not aliases:
        await view_alias_cmd.finish(f"曲目【{title}】(ID: {song_id}) 目前没有别名记录。")
    
    alias_str = "、".join(map(str, aliases))
    await view_alias_cmd.finish(f"曲目【{title}】(ID: {song_id}) 的现有别名为：\n{alias_str}")
