from nonebot.adapters.onebot.v11 import Bot
from .services.chat_cards import INK, LINE, MUTED, PAPER, TEAL, frame, lines
from .services.image_components import label, png, rounded, wrap

from nonebot import on_command
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.params import CommandArg

chuhelp_cmd = on_command("chuhelp", aliases={"中二帮助"}, priority=5, block=True)

HELP_SECTIONS = [
    ("曲库与推荐", [
        ("/查歌 [曲名、别名或 ID]", "也可发送「[曲名或别名]是什么歌」。候选过多时加 --page 页码翻页。"),
        ("推什么", "授权后按成绩推荐三张不同谱面，分别练分数、OP 和综合表现。"),
        ("推什么 推分 / 推OP / 综合", "指定一种方向。可加显示等级或定数，例如「推什么 14+ 推OP」或「推什么 14.8」。"),
        ("推什么 fc / aj / ajc", "寻找有成绩依据、尚未达成的连击目标，可加等级或定数。"),
        ("/查看别名 [曲名、别名或 ID]", "查看本地与收录来源的别名，支持简繁、罗马音和变体字查找。"),
        ("/添加别名 [ID] [别名]", "为指定歌曲添加本地别名。"),
    ]),
    ("账号与成绩", [
        ("/落雪授权", "在当前聊天发起网页授权，绑定成功后 Bot 会尝试撤回原授权链接。"),
        ("/落雪确认 <网页确认串>", "由发起授权的本人提交，群内请自行撤回确认指令。"),
        ("/落雪授权状态", "在当前聊天查看本人 OAuth 授权状态"),
        ("/落雪解绑 确认", "解除授权、使缓存失效；还需在落雪应用管理撤销"),
        ("/b30", "全版本普通谱面Rating前30均值，非玩家总Rating；缺位计零"),
        ("/b50", "旧版BEST30 + 当前国服版NEW20，按歌曲版本分区；缺位计零"),
        ("/chu成绩 [歌名或ID] [难度]", "查询单曲全部已记录成绩，支持别名；WE归入明确原曲并保留ID"),
        ("/chuinfo", "读取本人完整 OAuth 玩家资料；别名 /个人信息、/分数"),
        ("/chulist <条件> [--nocat] [--page 页码]", "已配信 MASTER/ULTIMA 逐谱面统计；可按显示等级、定数、版本、分类组合筛选"),
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
        ("/删除别名 [ID] [别名]", "删除指定歌曲的本地别名。"),
        ("/更新songlist /更新曲库", "同步国服曲库及谱面，按原 ID 保留本地别名"),
        ("/更新曲绘 [全部|full]", "默认及全部补齐整库缺失与损坏曲绘；full 校验新图后替换旧图"),
        ("/更新收藏品", "同步收藏品资源"),
        ("/更新剧情", "同步剧情索引与文本资源"),
    ]),
    ("任务控制", [
        ("/中二任务 [任务ID]", "查看当前 Bot、当前会话中自己的查询或资源维护任务"),
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


def draw_help():
    sections = []
    for name, items in HELP_SECTIONS:
        commands = [(wrap(cmd, 27, 702), wrap(desc, 24, 702))
                    for cmd, desc in items]
        height = 54 + sum(len(cmd) * 36 + len(desc) * 32 + 10 for cmd, desc in commands)
        sections.append((name, commands, height))
    y = 83
    canvas = frame("中二帮助", y + sum(height + 12 for _, _, height in sections) + 46, width=760)
    for name, commands, height in sections:
        admin = "SUPERUSER" in name
        rounded(canvas, (14, y, 746, y + height), 14, PAPER, LINE)
        label(canvas, (28, y + 5, 732, y + 47), name, 31, "#915321" if admin else TEAL)
        top = y + 48
        for cmd, desc in commands:
            top = lines(canvas, cmd, 28, top, 732, 27, 36, INK)
            top = lines(canvas, desc, 28, top, 732, 24, 32, MUTED) + 10
        y += height + 12
    label(canvas, (26, y, 734, y + 40), "/chuhelp 文字  ·  完整文字版     /help  ·  总帮助", 23, MUTED)
    return png(canvas)


@chuhelp_cmd.handle()
async def _(bot: Bot, event: Event, args: Message = CommandArg()):
    topic = args.extract_plain_text().strip()
    if topic:
        await chuhelp_cmd.finish(MessageSegment.text(help_text("" if topic in {"文字", "全部", "text"} else topic)))
    from .services.media_jobs import submit
    result = await submit(bot, event, "help.read", {}, origin="command")
    from .services.results import send_command_result
    await send_command_result(chuhelp_cmd, result, finish=True)
