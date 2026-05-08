from nonebot import on_command
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.log import logger
from nonebot.exception import FinishedException
from .config import Config
from .user_bind import get_bind_info
from nonebot import get_plugin_config
import httpx
import platform
import io
try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    pass

config = get_plugin_config(Config)

get_score = on_command("个人信息", aliases={"分数", "chuinfo"}, priority=5, block=True)

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
            import os
            for p in font_paths:
                if os.path.exists(p):
                    return ImageFont.truetype(p, size)
            return ImageFont.load_default()
    except Exception as e:
        print(f"Font error: {e}")
        return ImageFont.load_default()

def draw_player_info(data: dict, char_bytes: bytes = None) -> bytes:
    width, height = 800, 520
    # Background color (Dark Theme)
    img = Image.new("RGB", (width, height), (30, 30, 46))
    draw = ImageDraw.Draw(img)
    
    font_title = get_font(42, "Bold")
    font_large = get_font(32, "Medium")
    font_normal = get_font(24, "Normal")
    font_small = get_font(18, "Light")
    
    color_text = (248, 248, 242)
    color_subtext = (191, 191, 191)
    
    name = data.get('name', 'Unknown')
    level = data.get('level', 0)
    reborn = data.get('reborn_count', 0)
    total_level = reborn * 100 + level
    rating = data.get('rating', 0.0)
    rating_color = data.get('rating_possession', 'Unknown')
    over_power = data.get('over_power', 0.0)
    op_progress = data.get('over_power_progress', 0.0)
    play_count = data.get('total_play_count', 0)
    
    trophy = data.get('trophy', {}).get('name', '无') if data.get('trophy') else '无'
    character = data.get('character', {}).get('name', '无') if data.get('character') else '无'
    char_level = data.get('character', {}).get('level', 0) if data.get('character') else 0
    
    upload_time = data.get('upload_time', 'Unknown')
    
    draw.text((40, 40), f"Player: {name}", font=font_title, fill=color_text)
    draw.text((40, 100), f"Lv.{total_level}", font=font_normal, fill=color_subtext)
    
    draw.line([(40, 140), (760, 140)], fill=(98, 114, 164), width=2)
    
    def get_w(text, f):
        if hasattr(draw, 'textbbox'):
            return draw.textbbox((0, 0), text, font=f)[2]
        return draw.textsize(text, font=f)[0]

    lxns_r = data.get("lxns_rating")
    rin_r = data.get("rin_rating")
    if lxns_r is not None and rin_r is not None and lxns_r != rin_r:
        rating_str = f"{lxns_r:.2f}(lxns) / {rin_r:.2f}(rin)"
    else:
        lxns_r = data.get("lxns_rating")
    rin_r = data.get("rin_rating")
    if lxns_r is not None and rin_r is not None and lxns_r != rin_r:
        rating_str = f"{lxns_r:.2f}(lxns) / {rin_r:.2f}(rin)"
    else:
        rating_str = f"{rating:.2f}"
    op_str = f"{over_power:.2f} ({op_progress}%)"
    play_str = f"{play_count}"

    w_rating = max(get_w("Rating", font_small), get_w(rating_str, font_large))
    w_op = max(get_w("Over Power", font_small), get_w(op_str, font_large))
    w_play = max(get_w("Play Count", font_small), get_w(play_str, font_large))

    spacing = (760 - 40 - w_rating - w_op - w_play) / 2.0
    if spacing < 20: 
        spacing = 20

    x_rating = 40
    x_op = x_rating + w_rating + spacing
    x_play = x_op + w_op + spacing

    # Rating Section
    draw.text((x_rating, 170), "Rating", font=font_small, fill=color_subtext)
    draw.text((x_rating, 200), rating_str, font=font_large, fill=(80, 250, 123))
    
    # OP Section
    draw.text((x_op, 170), "Over Power", font=font_small, fill=color_subtext)
    draw.text((x_op, 200), op_str, font=font_large, fill=(255, 121, 198))
    
    # Play Count Section
    draw.text((x_play, 170), "Play Count", font=font_small, fill=color_subtext)
    draw.text((x_play, 200), play_str, font=font_large, fill=(139, 233, 253))
    
    draw.line([(40, 260), (760, 260)], fill=(98, 114, 164), width=2)
    
    currency = data.get('currency', 0)
    total_currency = data.get('total_currency', 0)

    draw.text((40, 290), f"称号: {trophy}", font=font_normal, fill=color_text)
    draw.text((40, 330), f"角色: {character} (Lv.{char_level})", font=font_normal, fill=color_text)
    draw.text((40, 370), f"金币: {currency} (总计: {total_currency})", font=font_normal, fill=color_text)
    
    draw.text((40, 430), f"数据同步时间: {upload_time}", font=font_small, fill=(98, 114, 164))
    
    # footer watermark centered/right or on a new line
    watermark_text = "数据来自 lxns 落雪查分器 | Generated by Robinbot | unibot"
    w_watermark = get_w(watermark_text, font_small)
    draw.text(((width - w_watermark) / 2, 470), watermark_text, font=font_small, fill=(98, 114, 164))

    # Draw character image on top right if present
    if char_bytes:
        try:
            char_img = Image.open(io.BytesIO(char_bytes)).convert("RGBA")
            # Usually 128x128 but to ensure it fits well
            char_img = char_img.resize((128, 128), Image.Resampling.LANCZOS)
            img.paste(char_img, (600, 10), mask=char_img)
        except Exception as e:
            logger.error(f"渲染角色图片失败: {e}")
    
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

async def get_user_friend_code(user_qq: str) -> str:
    """
    简易获取好友码函数：
    先通过 QQ 向落雪 API 盲查。如果查询成功并有 friend_code，则返回该码；
    如果 API 报错或未绑定，则在本地的 user_bind 文件中查询是否有绑定记录。
    如果全都没有，则返回空字符串。
    """
    if not config.lxns_token:
        return ""
        
    url = f"https://maimai.lxns.net/api/v0/chunithm/player/qq/{user_qq}"
    headers = {
        "Authorization": config.lxns_token
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=headers, timeout=10.0)
            if response.status_code == 200:
                data = response.json().get('data', {})
                fc = data.get('friend_code')
                if fc:
                    return str(fc)
        except Exception as e:
            logger.error(f"查询好友码失败: {e}")
            
    bind_data = get_bind_info()
    return bind_data.get(user_qq, "")

@get_score.handle()
async def _(event: Event):
    user_qq = str(event.get_user_id())

    import os, json
    PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
    SCORE_DIR = os.path.join(PLUGIN_DIR, "data", "score")
    rin_info_path = os.path.join(SCORE_DIR, f"{user_qq}_info.json")
    rin_info = {}
    if os.path.exists(rin_info_path):
        try:
            with open(rin_info_path, "r", encoding="utf-8") as f:
                rin_info = json.load(f)
        except:
            pass

    lxns_failed = False
    lxns_data = {}
    if not config.lxns_token:
        if not rin_info:
            await get_score.finish("未配置落雪咖啡屋(Lxns) Token，请在 .env.prod 中添加 lxns_token 配置，或者上传Rin的JSON文件！")
            return
        lxns_failed = True
    else:
        headers = {
            "Authorization": config.lxns_token
        }

        await get_score.send("收到，正在处理...")

        url = f"https://maimai.lxns.net/api/v0/chunithm/player/qq/{user_qq}"        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url, headers=headers, timeout=10.0)

                if response.status_code != 200:
                    bind_data = get_bind_info()
                    friend_code = bind_data.get(user_qq)

                    if friend_code:
                        url = f"https://maimai.lxns.net/api/v0/chunithm/player/{friend_code}"
                        response = await client.get(url, headers=headers, timeout=10.0)

                if response.status_code == 200:
                    lxns_data = response.json().get('data', {})
                else:
                    lxns_failed = True
            except FinishedException:
                raise
            except Exception as e:
                logger.error(f"获取分数失败: {e}")
                lxns_failed = True

    if lxns_failed and not rin_info:
        await get_score.finish("QQ号未绑定落雪查分器，且本地无JSON信息，建议在lxns查分器上绑定QQ号，或者发送/update更新。")
        return
    
    data = lxns_data
    if lxns_data:
        data["lxns_rating"] = lxns_data.get('rating', 0.0)
    
    if rin_info:
        data["rin_rating"] = rin_info.get("PlayerRating", 0.0)
        if lxns_failed:
            data["name"] = rin_info.get("UserName", "Unknown")
            data["level"] = rin_info.get("Level", 0)
            data["rating"] = data["rin_rating"]
            data["total_play_count"] = rin_info.get("PlayCount", 0)
        else:
            data["rating"] = data["lxns_rating"]

    char_img_bytes = None
    char_id = data.get('character', {}).get('id')
    if char_id is not None and not lxns_failed:
        char_path = os.path.join(PLUGIN_DIR, "data", "character", f"{char_id}.png")
        if os.path.exists(char_path):
            try:
                with open(char_path, "rb") as f:
                    char_img_bytes = f.read()
            except Exception as e:
                logger.error(f"读取本地角色图片失败: {e}")

    try:
        img_bytes = draw_player_info(data, char_img_bytes)
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"生成图片失败: {e}")
        await get_score.finish("生成图片失败。")
        return

    await get_score.finish(MessageSegment.image(img_bytes))
