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
        
        "2️ /update \n"
        " 更新最新游玩数据\n\n"

        "3️ /chulist [难度/定数] \n"
        " 生成对应难度或定数的 Overpower 统计图。\n"
        " 示例: /chulist 13+ 或 /chulist 14.9 \n\n"
        
        "4️ /chuinfo \n"
        " 获取当前绑定账号信息\n\n"
    )
    await chuhelp_cmd.finish(help_text)