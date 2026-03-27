import json
import os
import random
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont
from nonebot import on_regex
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment
from nonebot.log import logger

from .song import get_font, get_version_name, wrap_text_with_height, draw_text, download_jacket
from .overpower_list_local import calculate_op

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PLUGIN_DIR, "data")
SONGLIST_PATH = os.path.join(DATA_DIR, "songlist.json")
SCORE_DIR = os.path.join(DATA_DIR, "score")

recommend_cmd = on_regex(r"^推什么\s*(.+)?$", priority=5, block=True)

def render_recommendation_image(song: dict, diff: dict, user_score: int, user_op: float, max_op: float, jacket_img: Image.Image, has_score: bool) -> bytes:
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
    font_diff = get_font(28, "Bold")
    font_op = get_font(26, "Bold")

    diff_idx = diff.get("difficulty", 0)
    color = DIFF_COLORS[diff_idx] if diff_idx < len(DIFF_COLORS) else (100, 100, 100)
    diff_name = DIFF_NAMES[diff_idx] if diff_idx < len(DIFF_NAMES) else f"DIFF {diff_idx}"

    card_width = 800
    temp_img = Image.new("RGBA", (card_width, 800), BG_COLOR)
    draw = ImageDraw.Draw(temp_img)

    jacket_size = 200
    jacket_img = jacket_img.resize((jacket_size, jacket_size), Image.Resampling.LANCZOS)
    temp_img.paste(jacket_img, (40, 40), jacket_img if jacket_img.mode == "RGBA" else None)

    info_x = 40 + jacket_size + 30
    current_y = 40
    
    title = song.get("title", "Unknown")
    artist = song.get("artist", "-")
    bpm = song.get("bpm", 0)
    genre = song.get("genre", "-")
    version_raw = song.get("version", "-")
    version = get_version_name(version_raw)

    title_wrapped, _, t_h = wrap_text_with_height(title, font_title, card_width - info_x - 40, draw)
    draw_text(draw, (info_x, current_y), title_wrapped, font_title, TEXT_MAIN)
    current_y += t_h + 15
    
    draw_text(draw, (info_x, current_y), f"Artist: {artist}", font_head, TEXT_SUB)
    current_y += 35

    draw_text(draw, (info_x, current_y), f"BPM: {bpm}   Genre: {genre}", font_body, TEXT_SUB)
    current_y += 30
    
    draw_text(draw, (info_x, current_y), f"Version: {version}", font_body, TEXT_SUB)
    current_y += 40

    start_y = max(current_y, 40 + jacket_size + 30)

    # 画谱面框
    box_h = 160 if has_score else 80
    draw.rounded_rectangle([40, start_y, card_width - 40, start_y + box_h], radius=8, fill=PANEL_COLOR)
    
    stripe_color = color if diff_idx != 4 else (20, 20, 20)
    draw.rounded_rectangle([40, start_y, 55, start_y + box_h], radius=8, fill=stripe_color)
    draw.rectangle([48, start_y, 55, start_y + box_h], fill=stripe_color)
    
    if diff_idx == 4:
        draw.rounded_rectangle([40, start_y, card_width - 40, start_y + box_h], radius=8, fill=None, outline=(220, 50, 50), width=2)
        text_color = (255, 255, 255)
    else:
        text_color = color

    level_value_str = f"{float(diff.get('level_value', 0)):.1f}"
    draw_text(draw, (70, start_y + 20), f"{diff_name} {diff.get('level', '')} ({level_value_str})", font_diff, text_color)

    if has_score:
        draw_text(draw, (70, start_y + 70), f"Current Score: {user_score}", font_body, TEXT_MAIN)
        op_diff = max_op - user_op
        draw_text(draw, (70, start_y + 110), f"OP: {user_op:.2f} / {max_op:.2f}  (可推: +{op_diff:.2f})", font_op, (255, 215, 0))

    final_img = temp_img.crop((0, 0, card_width, int(start_y + box_h + 40)))
    buf = BytesIO()
    final_img.convert("RGB").save(buf, format="JPEG", quality=90)
    return buf.getvalue()

@recommend_cmd.handle()
async def _(event: MessageEvent):
    user_qq = str(event.get_user_id())
    msg_raw = event.get_plaintext().strip()
    
    # 解析可能附带的等级
    prefix = "推什么"
    level_filter = None
    if msg_raw.startswith(prefix):
        level_filter = msg_raw[len(prefix):].strip()
        if not level_filter:
            level_filter = None

    if not os.path.exists(SONGLIST_PATH):
        await recommend_cmd.finish("未找到曲库数据，请先更新 songlist.json。")
        return

    try:
        with open(SONGLIST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        songs = data.get("songs", [])
    except Exception as e:
        logger.error(f"读取 songlist.json 出错: {e}")
        await recommend_cmd.finish("读取曲库数据失败。")
        return

    score_path = os.path.join(SCORE_DIR, f"{user_qq}.json")
    has_score = os.path.exists(score_path)
    
    score_dict = {}
    if has_score:
        try:
            with open(score_path, "r", encoding="utf-8") as f:
                user_scores = json.load(f)
            if isinstance(user_scores, list):
                for s in user_scores:
                    s_id = str(s.get("id"))
                    s_lv = str(s.get("level_index"))
                    if not s_id or not s_lv:
                        continue
                    key = f"{s_id}_{s_lv}"
                    old_s = score_dict.get(key, {}).get("score", 0)
                    new_s = int(s.get("score", 0))
                    if new_s >= old_s:
                        score_dict[key] = s
        except Exception as e:
            logger.error(f"读取用户分数记录失败 {user_qq}: {e}")
            has_score = False

    candidates = []
    
    for song in songs:
        song_id = str(song.get("id"))
        for diff in song.get("difficulties", []):
            level_index = str(diff.get("difficulty", ""))
            
            # 只随机 Master (3) 和 Ultima (4) 难度
            if level_index not in ("3", "4"):
                continue

            level_str = str(diff.get("level", ""))
            level_value = float(diff.get("level_value", 0.0))
            
            # 过滤指定等级
            if level_filter and level_str != level_filter:
                continue
                
            max_op = (level_value + 3.0) * 5.0
            key = f"{song_id}_{level_index}"
            
            if has_score:
                s_info = score_dict.get(key)
                user_sc = 0
                user_op = 0.0
                if s_info:
                    user_sc = int(s_info.get("score", 0))
                    fc = str(s_info.get("full_combo", ""))
                    user_op = calculate_op(level_value, user_sc, fc)
                
                # 只有 overpower 还没推满的（不是100%）才加入候选
                if max_op - user_op > 0.001:
                    candidates.append((song, diff, user_sc, user_op, max_op))
            else:
                candidates.append((song, diff, 0, 0.0, max_op))
                
    if not candidates:
        if level_filter:
            await recommend_cmd.finish(f"未找到符合条件或需要推的 {level_filter} 级谱面，可能你已经把这个等级推满啦！")
        else:
            await recommend_cmd.finish("未找到符合条件可推的谱面。")
        return

    # 随机选择一首
    chosen_song, chosen_diff, user_score, user_op, max_op = random.choice(candidates)
    
    # 尝试下载/获取曲绘
    song_id = int(chosen_song.get("id", 0))
    jacket_id = song_id
    # 如果是WE谱面，jacket_id可能等于origin_id，此处若需要可做额外处理
    if chosen_diff.get("difficulty") == 4:
        jacket_id = chosen_song.get("origin_id", song_id)

    jacket_img = await download_jacket(jacket_id)
    
    # 生成图片
    img_bytes = render_recommendation_image(chosen_song, chosen_diff, user_score, user_op, max_op, jacket_img, has_score)
    
    await recommend_cmd.finish(MessageSegment.image(img_bytes))
