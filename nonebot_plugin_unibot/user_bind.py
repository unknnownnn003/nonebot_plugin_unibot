from nonebot import on_command
from nonebot.adapters import Event
from nonebot.params import CommandArg
from nonebot.adapters.onebot.v11 import Message
from nonebot.log import logger
import os
import json

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PLUGIN_DIR, "data")
BIND_FILE = os.path.join(DATA_DIR, "user_bind_info.json")

def _ensure_env():
    """
    确保存放用户绑定信息的目录和文件存在
    """
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
    if not os.path.exists(BIND_FILE):
        with open(BIND_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)

def get_bind_info() -> dict:
    """
    从本地 JSON 文件中读取用户的好友码绑定信息
    返回格式: { "QQ号": "好友码" }
    """
    _ensure_env()
    try:
        with open(BIND_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"读取用户绑定信息出错: {e}")
        return {}

def save_bind_info(qq: str, friend_code: str) -> bool:
    """
    将用户的 QQ 号及对应的好友码保存到本地 JSON 文件中
    :param qq: 用户的 QQ 号
    :param friend_code: 查分器好友码
    """
    _ensure_env()
    data = get_bind_info()
    data[str(qq)] = str(friend_code)
    try:
        with open(BIND_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        return True
    except Exception as e:
        logger.error(f"保存用户绑定信息出错: {e}")
        return False

bind_command = on_command("bind", priority=5, block=True)

@bind_command.handle()
async def _(event: Event, msg: Message = CommandArg()):
    """
    处理 /bind 指令，用于绑定用户的落雪查分器好友码
    """
    user_qq = str(event.get_user_id())
    friend_code = msg.extract_plain_text().strip()
    
    if not friend_code:
        await bind_command.finish("请输入需要绑定的好友码，例如：/bind <好友码>")
        
    if not friend_code.isdigit():
        await bind_command.finish("好友码格式错误，必须为全数字")
        
    if save_bind_info(user_qq, friend_code):
        await bind_command.finish("绑定成功！好友码已保存。")
    else:
        await bind_command.finish("绑定失败，内部发生错误，请查看控制台日志。")
