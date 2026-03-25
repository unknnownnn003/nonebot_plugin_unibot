from nonebot import on_command
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.log import logger
from nonebot.exception import FinishedException
from .config import Config
from nonebot import get_plugin_config
import httpx
import platform
import io
try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    pass

config = get_plugin_config(Config)

get_score = on_command("个人信息",aliases={"chuinfo"}, priority=5, block=True)

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
    width, height = 800, 480
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
    
    # footer watermark on the right
    watermark_text = "数据来自 lxns 落雪查分器"
    w_watermark = get_w(watermark_text, font_small)
    draw.text((760 - w_watermark, 430), watermark_text, font=font_small, fill=(98, 114, 164))

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

@get_score.handle()
async def _(event: Event):
    user_qq = event.get_user_id()
    
    if not config.lxns_token:
        await get_score.finish("未配置落雪咖啡屋(Lxns) Token，请在 .env.prod 中添加 lxns_token 配置！")
        return
        
    url = f"https://maimai.lxns.net/api/v0/chunithm/player/qq/{user_qq}"
    headers = {
        "Authorization": config.lxns_token
    }
    
    await get_score.send("正在获取分数信息，请稍候...")
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=headers, timeout=10.0)
        except FinishedException:
            raise
        except Exception as e:
            logger.error(f"获取分数失败: {e}")
            await get_score.finish(f"获取分数请求失败: {e}")
            
    status_code = response.status_code
    if status_code == 200:
        data = response.json().get('data', {})
        
        char_id = data.get('character', {}).get('id')
        char_img_bytes = None
        if char_id:
            try:
                char_url = f"https://assets2.lxns.net/chunithm/character/{char_id}.png"
                async with httpx.AsyncClient() as c2:
                    char_res = await c2.get(char_url, timeout=10.0)
                    if char_res.status_code == 200:
                        char_img_bytes = char_res.content
            except Exception as e:
                logger.error(f"获取角色图片失败: {e}")

        try:
            img_bytes = draw_player_info(data, char_img_bytes)
        except FinishedException:
            raise
        except Exception as e:
            logger.error(f"生成图片失败: {e}")
            await get_score.finish("生成图片失败。")
            return
            
        await get_score.finish(MessageSegment.image(img_bytes))
    else:
        await get_score.finish(f"获取个人信息失败，返回状态码: {status_code}\n信息: {response.text}")
