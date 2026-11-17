"""Native OAuth account management; old friend-code files are never read."""
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message
from nonebot.params import CommandArg

# Keep this module: the shared policy owner already maps user_bind -> scores.
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from .services.oauth_runtime import command as _oauth_command
from .services.oauth_runtime import remember_authorization_message
from .services.error_notices import notice_call as _notice_call

oauth_authorize = on_command("落雪授权", priority=5, block=True)
oauth_confirm = on_command("落雪确认", priority=5, block=True)
oauth_status = on_command("落雪授权状态", priority=5, block=True)
oauth_unlink = on_command("落雪解绑", priority=5, block=True)


@oauth_authorize.handle()
async def authorize(bot: Bot, event: MessageEvent):
    message = await _oauth_command(bot, event, "begin")
    receipt = await _notice_call(oauth_authorize.send, message)
    await remember_authorization_message(bot, event, message, receipt)
    await oauth_authorize.finish()


@oauth_confirm.handle()
async def _(bot: Bot, event: MessageEvent, msg: Message = CommandArg()):
    await _notice_call(oauth_confirm.finish, await _oauth_command(bot, event, "confirm", msg.extract_plain_text()))


@oauth_status.handle()
async def _(bot: Bot, event: MessageEvent):
    await _notice_call(oauth_status.finish, await _oauth_command(bot, event, "status"))


@oauth_unlink.handle()
async def _(bot: Bot, event: MessageEvent, msg: Message = CommandArg()):
    await _notice_call(oauth_unlink.finish, await _oauth_command(bot, event, "unlink", msg.extract_plain_text()))
