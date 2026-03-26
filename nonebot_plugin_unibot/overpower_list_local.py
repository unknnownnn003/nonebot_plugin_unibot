import json
import os
import time
import httpx
import math
import io
import platform
from PIL import Image, ImageDraw, ImageFont

from nonebot import on_command
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import MessageSegment, Message
from nonebot.params import CommandArg
from nonebot.log import logger
from nonebot.exception import FinishedException
from nonebot import get_plugin_config

from .config import Config

config = get_plugin_config(Config)

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PLUGIN_DIR, "data")
SONGLIST_PATH = os.path.join(DATA_DIR, "songlist.json")
JACKET_DIR = os.path.join(DATA_DIR, "jacket")

chulist_cmd = on_command("chulist", priority=5, block=True)

def load_songlist():
    if not os.path.exists(SONGLIST_PATH):
        return []
    with open(SONGLIST_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
        return data.get("songs", [])

def load_songlist_full():
    if not os.path.exists(SONGLIST_PATH):
        return [], [], []
    with open(SONGLIST_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
        return data.get("songs", []), data.get("versions", []), data.get("genres", [])


def parse_term_to_difficulty(term: str):
    is_exact = False
    exact_level = 0.0
    try:
        exact_level = float(term)
        if "." in term:
            is_exact = True
    except:
        pass
    return exact_level, is_exact

def get_target_charts(query_text: str):
    songs, versions, genres = load_songlist_full()
    
    # Normalize query for space-separated multi-word terms
    query = query_text.lower()
    replacements = {
        "origin plus": "originplus", "origin+": "originplus",
        "air plus": "airplus", "air+": "airplus",
        "star plus": "starplus", "star+": "starplus",
        "amazon plus": "amazonplus", "amazon+": "amazonplus",
        "crystal plus": "crystalplus", "crystal+": "crystalplus",
        "paradise lost": "paradiselost", "paradiser lost": "paradiselost",
        "new plus": "newplus", "new+": "newplus",
        "sun plus": "sunplus", "sun+": "sunplus",
        "luminous plus": "luminousplus", "luminous+": "luminousplus",
        "pops & anime": "pops_anime", "pops&animes": "pops_anime", "pops&anime": "pops_anime", "流行 & 动漫": "pops_anime", "流行动漫": "pops_anime",
        "touhou project": "touhou", "东方project": "touhou",
        "chunithm original": "original", "gekishu & maimai": "gekimai"
    }
    for k, v in replacements.items():
        query = query.replace(k, v)
        
    terms = [t for t in query.replace(',', ' ').replace('，', ' ').replace('|', ' ').split() if t]

    version_map = {}
    for v in versions:
        title = v['title'].lower()
        if title == "chunithm":
            mapped = "origin"
        elif title == "chunithm plus":
            mapped = "originplus"
        else:
            mapped = title.replace('chunithm', '').strip()
        version_map[v['version']] = mapped

    for k, v in version_map.items():
        for r_k, r_v in replacements.items():
            if r_k in v:
                v = v.replace(r_k, r_v)
        version_map[k] = v.replace(' ', '')

    # Build genre aliases dictionary
    genre_aliases = {
        "其他游戏": ["variety", "其他", "其它"],
        "东方project": ["touhou", "东方", "车万"],
        "原创": ["original", "ori"],
        "音击舞萌": ["gekimai", "音击", "舞萌"],
        "流行 & 动漫": ["pops_anime", "流行", "动漫", "pops", "anime"]
    }

    charts = {}

    for song in songs:
        song_id = str(song["id"])
        origin_id = song.get("origin_id", song_id)
        
        s_version = version_map.get(song.get("version"), "")
        s_genre_raw = song.get("genre", "").lower()
        s_genre_search = s_genre_raw
        for base_genre, aliases in genre_aliases.items():
            if base_genre in s_genre_raw or s_genre_raw in base_genre:
                s_genre_search += " " + " ".join(aliases)

        # Find the highest difficulty that is 3 or 4
        # difficulties usually ordered by level_index in the JSON or we can max it
        valid_diffs = [diff for diff in song.get("difficulties", []) if diff.get("difficulty") in (3, 4)]
        best_diff = None
        if valid_diffs:
            best_diff = max(valid_diffs, key=lambda d: d.get("level_value", 0.0))

        for term in terms:
            match_found = False
            
            # 1. Match Difficulty?
            exact_level, is_exact = parse_term_to_difficulty(term)
            # Check all diffs for difficulty match
            for diff in valid_diffs:
                d = diff.get("difficulty")
                c = diff.get("level_value", 0.0)
                l = diff.get("level", "")
                
                diff_match = False
                if is_exact:
                    if abs(float(c) - exact_level) < 0.01: diff_match = True
                else:
                    if l == term: diff_match = True
                
                if diff_match:
                    target_id = str(origin_id) if d == 4 else song_id
                    key = f"{song_id}_{d}"
                    charts[key] = {
                        "song_id": song_id,
                        "jacket_id": target_id,
                        "level_index": int(d),
                        "level_value": float(c),
                        "theoretical_op": (float(c) + 3) * 5,
                        "song_name": song.get("title", "Unknown")
                    }
                    match_found = True

            # 2. Match Version or Genre based on highest diff
            if term == s_version or term in s_genre_search:
                # Use best_diff
                if best_diff:
                    d = best_diff.get("difficulty")
                    c = best_diff.get("level_value", 0.0)
                    target_id = str(origin_id) if d == 4 else song_id
                    key = f"{song_id}_{d}"
                    charts[key] = {
                        "song_id": song_id,
                        "jacket_id": target_id,
                        "level_index": int(d),
                        "level_value": float(c),
                        "theoretical_op": (float(c) + 3) * 5,
                        "song_name": song.get("title", "Unknown")
                    }
                    match_found = True

    return list(charts.values())

def parse_user_data(qq: str):
    json_path = os.path.join(DATA_DIR, "score", f"{qq}.json")
    if not os.path.exists(json_path):
        return None, None
    mtime = os.path.getmtime(json_path)
    upload_date = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(mtime))
    
    with open(json_path, 'r', encoding='utf-8') as f:
        try:
            data = json.load(f)
        except:
            return {}, upload_date
        
    user_best = {}
    for row in data:
        s_id = str(row.get("id", ""))
        l_idx = str(row.get("level_index", ""))
        
        try:
            score = int(row.get("score", 0))
        except:
            score = 0
            
        fc = row.get("full_combo", "")
        
        key = f"{s_id}_{l_idx}"
        if key not in user_best:
            user_best[key] = {"score": score, "full_combo": fc}
        else:
            if score > user_best[key]["score"]:
                user_best[key] = {"score": score, "full_combo": fc}
                
    return user_best, upload_date

def calculate_op(level_value, score, fc_status):
    bonus = 0.0
    fc = str(fc_status).lower()
    if fc == "fullcombo": bonus = 0.5
    elif fc == "alljustice": bonus = 1.0
    elif fc == "alljusticecritical": bonus = 1.25
    
    if score >= 1007500:
        op = (level_value + 2.0) * 5.0 + bonus + (score - 1007500) * 0.0015
    elif score >= 1005000:
        op = (level_value + 1.5 + ((score - 1005000) // 50) * 0.01) * 5.0 + bonus
    elif score >= 1000000:
        op = (level_value + 1.0 + ((score - 1000000) // 100) * 0.01) * 5.0 + bonus
    elif score >= 990000:
        op = (level_value + 0.6 + ((score - 990000) // 250) * 0.01) * 5.0 + bonus
    elif score >= 975000:
        op = (level_value + 0.0 + ((score - 975000) // 250) * 0.01) * 5.0 + bonus
    elif score >= 950000:
        op = (level_value - 1.5 + ((score - 950000) // 150) * 0.01) * 5.0 + bonus
    elif score >= 925000:
        op = (level_value - 3.0 + ((score - 925000) // 150) * 0.01) * 5.0 + bonus
    elif score >= 900000:
        op = (level_value - 5.0 + ((score - 900000) // 250) * 0.01) * 5.0 + bonus
    else:
        op = 0.0
    
    return max(0.0, op)

async def get_player_info_api(user_qq: str):
    res = {"name": user_qq, "rating": 0.0, "level": 0, "title": "", "char_bytes": None}
    if not config.lxns_token:
        return res
    headers = {"Authorization": config.lxns_token}
    
    try:
        from .user_bind import get_bind_info
        bind_data = get_bind_info()
        friend_code = bind_data.get(user_qq)
    except Exception:
        friend_code = None
        
    url = f"https://maimai.lxns.net/api/v0/chunithm/player/qq/{user_qq}"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=headers, timeout=10.0)
            if response.status_code != 200 and friend_code:
                url = f"https://maimai.lxns.net/api/v0/chunithm/player/{friend_code}"
                response = await client.get(url, headers=headers, timeout=10.0)
            
            if response.status_code == 200:
                data = response.json().get('data', {})
                res["name"] = data.get('name', 'Unknown')
                res["rating"] = data.get('rating', 0.0)
                res["level"] = data.get('level', 0)
                reborn_count = data.get('reborn_count', data.get('rebornCount', 0))
                res["level"] += 100 * reborn_count
                
                title_obj = data.get('title', data.get('trophy', {}))
                if isinstance(title_obj, dict):
                    res["title"] = title_obj.get('name', '')
                else: res["title"] = str(title_obj)
                
                char_id = data.get('character', {}).get('id')
                if char_id:
                    char_url = f"https://assets2.lxns.net/chunithm/character/{char_id}.png"
                    char_res = await client.get(char_url, timeout=10.0)
                    if char_res.status_code == 200:
                        res["char_bytes"] = char_res.content
                return res
        except Exception as e:
            logger.error(f"获取玩家信息失败: {e}")
    return res

def get_font(size, weight="Normal"):
    try:
        if platform.system() == "Windows":
            return ImageFont.truetype("msyh.ttc", size)
        else:
            font_paths = [
                f"/usr/share/fonts/opentype/SourceHanSans/SourceHanSansSC-{weight}.otf",
                f"/usr/share/fonts/opentype/noto/NotoSansCJK-{weight}.ttc",
                "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc"
            ]
            for p in font_paths:
                if os.path.exists(p): return ImageFont.truetype(p, size)
            return ImageFont.load_default()
    except: return ImageFont.load_default()

def get_w(text, f, draw):
    if hasattr(draw, 'textbbox'): return draw.textbbox((0, 0), text, font=f)[2]
    return draw.textsize(text, font=f)[0]

def draw_chulist_image(draw_items, total_op, total_theoretical_op, req_level, player_info, upload_date):
    groups = {}
    for item in draw_items:
        lv = item["level_value"]
        lv_str = f"{lv:.1f}"
        if lv_str not in groups: groups[lv_str] = []
        groups[lv_str].append(item)
        
    sorted_lvs = sorted(groups.keys(), key=lambda x: float(x), reverse=True)
    for lv_str in sorted_lvs:
        groups[lv_str].sort(key=lambda x: x["score"], reverse=True)

    block_w = 160
    block_h = 250
    spacing_x = 20
    spacing_y = 10
    
    margin_top = 310
    margin_bottom = 60
    margin_side = 40
    
    num_cols = 10
    
    y_cursor = margin_top
    for lv_str in sorted_lvs:
        y_cursor += 100 # header space
        num_rows = math.ceil(len(groups[lv_str]) / num_cols)
        y_cursor += num_rows * (block_h + spacing_y) + 10
        
    height = y_cursor + margin_bottom
    
    actual_cols = num_cols
    width = margin_side * 2 + actual_cols * block_w + (actual_cols - 1) * spacing_x
    if width < 800: width = 800
        
    img = Image.new("RGB", (width, height), (30, 30, 46))
    draw = ImageDraw.Draw(img)
    
    font_xl = get_font(40, "Bold")
    font_large = get_font(36, "Bold")
    font_medium = get_font(24, "Medium")
    font_normal = get_font(20, "Normal")
    font_small = get_font(16, "Light")
    font_tiny = get_font(14, "Light")
    
    color_text = (248, 248, 242)
    color_sub = (191, 191, 191)
    
    avatar_bytes = player_info.get("char_bytes")
    if avatar_bytes:
        try:
            char_img = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
            char_img = char_img.resize((100, 100), Image.Resampling.LANCZOS)
            img.paste(char_img, (margin_side, 20), mask=char_img)
        except: pass
            
    name = player_info.get("name", "Unknown")
    rating = player_info.get("rating", 0.0)
    level = player_info.get("level", 0)
    title = player_info.get("title", "")
    
    text_x = margin_side + 120
    draw.text((text_x, 20), name, font=font_xl, fill=color_text)
    draw.text((text_x, 75), f"Lv.{level} | {title}", font=font_normal, fill=(139, 233, 253))
    draw.text((text_x, 105), f"Rating: {rating:.2f}", font=font_medium, fill=(80, 250, 123))
    
    percent = total_op / total_theoretical_op * 100 if total_theoretical_op > 0 else 0
    t_text = f"Filter: {req_level}  OP: {total_op:.2f} / {total_theoretical_op:.2f} ({percent:.2f}%)"
    wt = get_w(t_text, font_large, draw)
    draw.text(((width - wt) / 2, 140), t_text, font=font_large, fill=(255, 121, 198))
    
    tot_ajc = sum(1 for item in draw_items if item["full_combo"].lower() == "alljusticecritical")
    tot_aj = sum(1 for item in draw_items if item["full_combo"].lower() in ("alljusticecritical", "alljustice"))
    tot_fc = sum(1 for item in draw_items if item["full_combo"].lower() in ("alljusticecritical", "alljustice", "fullcombo"))
    
    tot_sss_plus = sum(1 for item in draw_items if item["score"] >= 1009000)
    tot_sss = sum(1 for item in draw_items if item["score"] >= 1007500)
    tot_ss = sum(1 for item in draw_items if item["score"] >= 1000000)
    
    tot_count = len(draw_items)
    t_text2 = f"AJC: {tot_ajc}/{tot_count}   AJ: {tot_aj}/{tot_count}   FC: {tot_fc}/{tot_count}"
    wt2 = get_w(t_text2, font_large, draw)
    draw.text(((width - wt2) / 2, 185), t_text2, font=font_large, fill=(139, 233, 253))
    
    t_text3 = f"SSS+: {tot_sss_plus}/{tot_count}   SSS: {tot_sss}/{tot_count}   SS: {tot_ss}/{tot_count}"
    wt3 = get_w(t_text3, font_large, draw)
    draw.text(((width - wt3) / 2, 230), t_text3, font=font_large, fill=(241, 250, 140))
    
    draw.line([(margin_side, 290), (width - margin_side, 290)], fill=(98, 114, 164), width=2)
    
    y_cursor = margin_top
    for lv_str in sorted_lvs:
        group_items = groups[lv_str]
        g_op = sum(x["over_power"] for x in group_items)
        g_th = sum(x["theoretical_op"] for x in group_items)
        g_pct = g_op / g_th * 100 if g_th > 0 else 0.0
        
        g_ajc = sum(1 for x in group_items if x["full_combo"].lower() == "alljusticecritical")
        g_aj = sum(1 for x in group_items if x["full_combo"].lower() in ("alljusticecritical", "alljustice"))
        g_fc = sum(1 for x in group_items if x["full_combo"].lower() in ("alljusticecritical", "alljustice", "fullcombo"))
        
        g_sss_plus = sum(1 for x in group_items if x["score"] >= 1009000)
        g_sss = sum(1 for x in group_items if x["score"] >= 1007500)
        g_ss = sum(1 for x in group_items if x["score"] >= 1000000)
        
        g_total = len(group_items)
        
        y_cursor += 15
        h_text = f"定数 {lv_str}   OP: {g_op:.2f} / {g_th:.2f} ({g_pct:.2f}%)   AJC: {g_ajc}/{g_total}  AJ: {g_aj}/{g_total}  FC: {g_fc}/{g_total}"
        draw.text((margin_side, y_cursor), h_text, font=font_large, fill=(189, 147, 249))
        y_cursor += 45
        
        h_text2 = f"SSS+: {g_sss_plus}/{g_total}   SSS: {g_sss}/{g_total}   SS: {g_ss}/{g_total}"
        draw.text((margin_side, y_cursor), h_text2, font=font_large, fill=(241, 250, 140))
        y_cursor += 40
        
        for i, item in enumerate(group_items):
            r = i // num_cols
            c = i % num_cols
            x = margin_side + c * (block_w + spacing_x)
            y = y_cursor + r * (block_h + spacing_y)
            
            jacket_path = os.path.join(JACKET_DIR, f"{item['jacket_id']}.png")
            jacket_size = 120
            j_x = x + (block_w - jacket_size) // 2
            j_y = y + 20
            
            fc_str = item["full_combo"].lower()
            if fc_str == "alljusticecritical":
                draw.rectangle([j_x - 12, j_y - 12, j_x + jacket_size + 11, j_y + jacket_size + 11], fill=(255, 85, 85))
                draw.rectangle([j_x - 9, j_y - 9, j_x + jacket_size + 8, j_y + jacket_size + 8], fill=(80, 250, 123))
                draw.rectangle([j_x - 6, j_y - 6, j_x + jacket_size + 5, j_y + jacket_size + 5], fill=(139, 233, 253))
                draw.rectangle([j_x - 3, j_y - 3, j_x + jacket_size + 2, j_y + jacket_size + 2], fill=(255, 121, 198))
            elif fc_str == "alljustice":
                draw.rectangle([j_x - 12, j_y - 12, j_x + jacket_size + 11, j_y + jacket_size + 11], fill=(241, 250, 140))
            elif fc_str == "fullcombo":
                draw.rectangle([j_x - 12, j_y - 12, j_x + jacket_size + 11, j_y + jacket_size + 11], fill=(80, 250, 123))
                
            idx_color = (180, 100, 255) if item["level_index"] == 3 else (0, 0, 0)
            draw.rectangle([j_x - 6, j_y - 6, j_x + jacket_size + 5, j_y + jacket_size + 5], fill=idx_color)
            
            draw.rectangle([j_x, j_y, j_x + jacket_size - 1, j_y + jacket_size - 1], fill=(50, 50, 50))
            if os.path.exists(jacket_path):
                try:
                    j_img = Image.open(jacket_path).convert("RGBA")
                    j_img = j_img.resize((jacket_size, jacket_size), Image.Resampling.LANCZOS)
                    img.paste(j_img, (j_x, j_y), mask=j_img)
                except: pass
                
            t_y = j_y + jacket_size + 10
            
            s_name = item['song_name']
            name_font_size = 16
            name_font = get_font(name_font_size, "Bold")
                
            while get_w(s_name, name_font, draw) > block_w - 5 and len(s_name) > 1:
                s_name = s_name[:-2] + "…"
            ws = get_w(s_name, name_font, draw)
            draw.text((x + (block_w - ws)/2, t_y), s_name, font=name_font, fill=(255, 255, 255))
            
            score_str = f"{item['score']:,}" if item['score'] > 0 else "0"
            wms = get_w(score_str, font_medium, draw)
            draw.text((x + (block_w - wms)/2, t_y + 25), score_str, font=font_medium, fill=(241, 250, 140))
            
            op_s = f"{item['over_power']:.2f} / {item['theoretical_op']:.2f}"
            wop = get_w(op_s, font_small, draw)
            draw.text((x + (block_w - wop)/2, t_y + 55), op_s, font=font_small, fill=(255, 121, 198))
            
            pct_s = f"{item['percent']:.2f}%"
            wpc = get_w(pct_s, font_small, draw)
            draw.text((x + (block_w - wpc)/2, t_y + 75), pct_s, font=font_small, fill=(139, 233, 253))
            
        nr = math.ceil(len(group_items) / num_cols)
        y_cursor += nr * (block_h + spacing_y)
        
    footer_y = height - margin_bottom + 15
    draw.line([(margin_side, footer_y - 10), (width - margin_side, footer_y - 10)], fill=(98, 114, 164), width=2)
    f_str = f"Data Upload: {upload_date} | generated by Robinbot | unibot"
    wf = get_w(f_str, font_normal, draw)
    draw.text(((width - wf) / 2, footer_y), f_str, font=font_normal, fill=(248, 248, 242))
    
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

@chulist_cmd.handle()
async def _(event: Event, msg: Message = CommandArg()):
    query_text = msg.extract_plain_text().strip()
    if not query_text:
        await chulist_cmd.finish("请提供查询条件（难度/定数/版本/分类等），例如: /chulist 13+ sun 车万")
        return
        
    user_qq = str(event.get_user_id())
    user_best, upload_date = parse_user_data(user_qq)
    if user_best is None:
        await chulist_cmd.finish("数据不存在，请发送/update")
        return
        
    target_charts = get_target_charts(query_text)
    if not target_charts:
        await chulist_cmd.finish(f"未找到符合条件【{query_text}】的谱面！")
        return
        
    await chulist_cmd.send(f"正在查询并生成，请稍候...")
    player_info = await get_player_info_api(user_qq)
    
    total_op = 0.0
    total_theoretical_op = 0.0
    draw_items = []
    
    for c in target_charts:
        key = f'{c["song_id"]}_{c["level_index"]}'
        theoretical_op = c["theoretical_op"]
        
        play = user_best.get(key)
        if play:
            score = play["score"]
            fc = play["full_combo"]
            op = calculate_op(c["level_value"], score, fc)
        else:
            op = 0.0
            score = 0
            fc = ""
            
        total_op += op
        total_theoretical_op += theoretical_op
        percent = op / theoretical_op * 100 if theoretical_op > 0 else 0.0
        
        draw_items.append({
            "song_name": c.get("song_name", "Unknown"),
            "song_id": c["song_id"],
            "jacket_id": c.get("jacket_id", c["song_id"]),
            "level_index": int(c["level_index"]),
            "level_value": c["level_value"],
            "score": score,
            "over_power": op,
            "percent": percent,
            "full_combo": fc,
            "theoretical_op": theoretical_op
        })
        
    try:
        img_bytes = draw_chulist_image(draw_items, total_op, total_theoretical_op, query_text, player_info, upload_date)
    except Exception as e:
        logger.error(f"Image gen failed: {e}")
        await chulist_cmd.finish("生成图片失败。")
        return
        
    await chulist_cmd.finish(MessageSegment.image(img_bytes))