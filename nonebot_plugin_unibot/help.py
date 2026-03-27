from nonebot import on_command
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import Message

chuhelp_cmd = on_command("chuhelp", priority=5, block=True)

@chuhelp_cmd.handle()
async def _(event: Event):
    help_text = (
        " Unibot 基础指令 \n"
        "----------------------\n"
        "1️ /bind [好友码] \n"
        " 绑定至lxns查分器\n\n"

        "2️ /chuupdate \n"
        " 上传 lxns 导出的分数 CSV 更新本地成绩\n\n"

        "3️ /chulist [查询条件] [--nocat]\n"
        " 生成由多种条件组合查询的 Overpower 统计图。\n"
        " 支持：定数、难度、版本、以及部分特殊指定分类。\n"
        " 多个条件间可用空格叠加取交集。\n"
        " 含 --nocat 标志时将不再按定数分组，而是汇总按分数排行。\n"
        " 示例: /chulist 13+ sun 车万 --nocat\n\n"

        "4️ /chuinfo \n"
        " 获取当前绑定账号信息\n\n"

        "5️ [曲名/别名/ID]是什么歌 \n"
        " 查询目标歌曲详情信息\n\n"
        
        "6️ /添加别名 [ID] [别名]\n"
        " 为歌曲添加自定义本地别名\n\n"
        "7️⃣ /查看别名 [模糊查询/id] \n"
        " 查看指定曲目的所有别名\n\n"
    )
    await chuhelp_cmd.finish(help_text)