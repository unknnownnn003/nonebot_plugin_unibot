"""Native command for the shared jacket maintenance task."""
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

update_jacket = on_command("更新曲绘", permission=SUPERUSER, priority=5, block=True)


def jacket_refresh_option(text):
    option = text.strip().lower()
    if option in {"", "全部", "all"}:
        return False
    if option in {"full", "force", "--full", "--force", "refresh", "完整", "重下", "全量", "重新下载"}:
        return True
    raise ValueError("invalid jacket refresh option")


@update_jacket.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    from .services.maintenance import submit
    from .services.results import outcome, send_command_result

    try:
        full = jacket_refresh_option(args.extract_plain_text())
    except ValueError:
        result = outcome("INVALID_ARGUMENT", "用法：/更新曲绘 [全部|full]。全部补齐缺失和损坏，full 逐张校验后刷新。", status="needs_input")
    else:
        result = await submit(bot, event, "jackets", full_refresh=full, origin="command")
    await send_command_result(update_jacket, result, finish=False)
