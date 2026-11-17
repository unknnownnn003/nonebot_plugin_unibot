from nonebot.adapters.onebot.v11 import Bot
import io
import os
import platform
from PIL import Image, ImageDraw, ImageFont

from nonebot import on_command
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.exception import FinishedException
from nonebot.log import logger
from nonebot.params import CommandArg

chuhelp_cmd = on_command("chuhelp", aliases={"中二帮助"}, priority=5, block=True)

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

HELP_SECTIONS = [
    ("账号与成绩", [
        ("/bind [好友码]", "绑定国服查分器好友码；QQ 已绑定国服侧时通常可跳过"),
        ("/落雪授权", "OAuth 网页授权；支持群内发起，链接私发，私聊确认；不替换现有查分"),
        ("/落雪授权状态", "查看本地 OAuth 绑定状态；群内发起时私发结果"),
        ("/落雪解绑 确认", "清除本地 OAuth，保留好友码与成绩；还需在落雪撤销授权"),
        ("/chuupdate", "上传国服 CSV 或 rin JSON，更新本地成绩归档"),
        ("/lxupdate", "从国服接口同步已绑定账号的国服成绩到本地"),
        ("/cf [歌名或ID] [难度] [分数] [fc/aj]", "手动传分，别名 /传分；难度为空时默认 MASTER"),
        ("/b", "查询国服 B30+N20：只使用国服成绩和国服定数，忽略手动传分"),
        ("/b30", "查询本地 B30：国服成绩与手动传分取最高分，使用落雪国服定数"),
        ("/chuinfo", "获取当前绑定账号信息；别名 /个人信息、/分数"),
        ("/chulist [条件] [--nocat]", "生成本地 Overpower 统计图；只统计国服曲库与国服成绩"),
    ]),
    ("曲库与推荐", [
        ("[曲名/别名/ID]是什么歌", "查询歌曲详情，支持别名、罗马音、简繁体和变体字匹配"),
        ("/添加别名 [ID] [别名]", "为歌曲添加自定义本地别名"),
        ("/删除别名 [ID] [别名]", "删除指定歌曲的本地别名，仅 SUPERUSER 可用"),
        ("/查看别名 [模糊查询/ID/别名]", "查看指定歌曲的本地及官方别名"),
        ("推什么 [条件]", "按条件随机推荐可推分曲目，建议范围限定在国服曲库"),
    ]),
    ("曲绘猜曲", [
        ("/chuguess [难度]", "开始曲绘猜曲游戏，难度可选 bas/adv/exp/mas/ult，默认 mas"),
        ("/chuguess <歌名>", "在游戏中猜歌，使用歌名/别名/ID 答题"),
        ("/猜 <歌名>", "同上，猜歌快捷方式"),
        ("/提示", "获取随机提示（曲名部分/曲师/定级/版本/截取位置/额外区域）"),
        ("/放弃", "放弃当前猜曲并公布答案"),
    ]),
    ("剧情", [
        ("/查剧情 [角色名/关键词]", "搜索并阅读指定角色或关键词的剧情"),
        ("/随机剧情", "随机抽选一段角色剧情阅读"),
        ("/剧情分类", "查看剧情分类；别名 /剧情列表"),
    ]),
    ("维护命令（SUPERUSER）", [
        ("/更新songlist", "从国服同步 songlist，按原ID保留本地别名"),
        ("/更新曲绘 [full]", "默认限速增量补缺；传 full/完整/重下 时完整重下"),
        ("/更新收藏品", "同步收藏品资源"),
        ("/更新剧情", "同步剧情索引与文本资源"),
    ]),
    ("任务控制", [
        ("/中二任务 [任务ID]", "查看当前 Bot、当前会话中自己的同步或资源维护任务"),
        ("/取消中二任务 <任务ID>", "请求取消；执行中的任务以最终状态为准"),
        ("/补发中二任务 <任务ID>", "只补发自己的任务缓存媒体，不重复生成"),
    ]),
]


def help_text(topic=""):
    aliases = {"成绩": "账号与成绩", "曲库": "曲库与推荐", "猜曲": "曲绘猜曲", "维护": "维护命令（SUPERUSER）", "任务": "任务控制"}
    topic = aliases.get(topic.strip(), topic.strip())
    selected = [(name, items) for name, items in HELP_SECTIONS if not topic or topic in name]
    if not selected:
        return "未找到分类。可选：" + "、".join(name for name, _ in HELP_SECTIONS)
    return "【UniBot 帮助】\n" + "\n\n".join("【" + name + "】\n" + "\n".join(cmd + "：" + desc for cmd, desc in items) for name, items in selected) + "\n\n/chuhelp 文字：完整文字版；/help：总帮助。日志群 /设置面板 可批量维护资源。命令前缀以宿主为准。"


def wrap_line(draw, text, font, width):
    lines, current = [], ""
    for character in text:
        if character == "\n":
            lines.append(current)
            current = ""
        elif current and draw.textlength(current + character, font=font) > width:
            lines.append(current)
            current = character
        else:
            current += character
    return lines + [current]


def draw_help():
    width = 980
    img = Image.new("RGB", (width, 10), (30, 30, 46))
    draw = ImageDraw.Draw(img)

    font_title = get_font(48, "Bold")
    font_section = get_font(30, "Bold")
    font_cmd = get_font(25, "Bold")
    font_desc = get_font(21, "Normal")

    commands = [(name, [("\n".join(wrap_line(draw, cmd, font_cmd, width - 116)),
                           "\n".join(wrap_line(draw, desc, font_desc, width - 156)))
                          for cmd, desc in items]) for name, items in HELP_SECTIONS]
    
    y = 135
    for _, items in commands:
        y += 48
        for cmd, desc in items:
            y += 38 * len(cmd.split("\n"))
            for _ in desc.split('\n'):
                y += 29
            y += 6
        y += 20
        
    height = y + 82
    img = Image.new("RGB", (width, height), (30, 30, 46))
    draw = ImageDraw.Draw(img)
    
    draw.text((40, 40), "Unibot 指令帮助", font=font_title, fill=(248, 248, 242))
    draw.line([(40, 110), (width - 40, 110)], fill=(98, 114, 164), width=2)

    y = 135
    for section, items in commands:
        draw.text((40, y), section, font=font_section, fill=(241, 250, 140))
        y += 48
        for cmd, desc in items:
            for line in cmd.split("\n"):
                draw.text((58, y), line, font=font_cmd, fill=(255, 121, 198))
                y += 38
            for line in desc.split('\n'):
                draw.text((78, y), line, font=font_desc, fill=(139, 233, 253))
                y += 29
            y += 6
        y += 20
        
    draw.line([(40, y + 10), (width - 40, y + 10)], fill=(98, 114, 164), width=2)
    font_footer = get_font(18, "Normal")
    draw.text((40, y + 30), "Generated by Robinbot | unibot", font=font_footer, fill=(191, 191, 191))
    
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

@chuhelp_cmd.handle()
async def _(bot: Bot, event: Event, args: Message = CommandArg()):
    topic = args.extract_plain_text().strip()
    if topic:
        await chuhelp_cmd.finish(MessageSegment.text(help_text("" if topic in {"文字", "全部", "text"} else topic)))
    from .services.media_jobs import submit
    result = await submit(bot, event, "help.read", {}, origin="command")
    from .services.results import send_command_result
    await send_command_result(chuhelp_cmd, result, finish=True)
