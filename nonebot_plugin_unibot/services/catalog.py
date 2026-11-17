"""Local configuration and identity gates for contextual tool discovery."""
from __future__ import annotations

from .job_store import _db_thread
from .permissions import native_superuser
from .group_policy import enabled


async def describe(specs, context):
    result = await _db_thread(lambda: _describe(specs, context))
    from nonebot.adapters.onebot.v11 import PrivateMessageEvent, GroupMessageEvent
    from .oauth_runtime import get_service
    from .oauth import OAuthError
    bot, event = context["bot"], context["event"]
    if isinstance(event, (PrivateMessageEvent, GroupMessageEvent)) and str(bot.self_id) == str(event.self_id):
        try:
            state = await get_service().status(bot.self_id, event.user_id)
        except OAuthError:
            state = "unbound"
        if state not in {"active", "refreshing"}:
            for name, row in result.items():
                if name.startswith("player.") and row["available"]:
                    row.update(available=False, unavailable_code="NOT_AUTHORIZED", unavailable_hint="请本人先使用 /落雪授权。")
    return result


def _describe(specs, context):
    from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent
    bot, event = context["bot"], context["event"]
    valid = isinstance(event, (GroupMessageEvent, PrivateMessageEvent)) and str(bot.self_id) == str(event.self_id)
    group = isinstance(event, GroupMessageEvent)
    from .oauth_runtime import _service
    oauth = _service is not None
    output = {}
    for spec in specs:
        name = spec["id"]
        row = dict(available=True, execution_checks_required=True)
        code, hint = None, None
        if not valid:
            code, hint = "PERMISSION_DENIED", "无法核验当前Bot和真实发起者。"
        elif group and not enabled(event, name):
            code, hint = "FEATURE_DISABLED", "本群对应功能已关闭。"
        elif name.startswith("game.") and not group:
            code, hint = "GROUP_ONLY", "猜曲只在群聊中可用。"
        elif name in {"maintenance.refresh", "song.alias_delete"} and not native_superuser(bot, event):
            code, hint = "PERMISSION_DENIED", "此能力仅原SUPERUSER规则允许的用户可用。"
        elif name.startswith("player.") and not oauth:
            code, hint = "NOT_CONFIGURED", "OAuth尚未配置，请联系管理员。"
        if name.startswith("player."):
            row["input_requirements"] = ["真实发起人已完成落雪OAuth授权，无需手动同步"]
        if name == "maintenance.refresh":
            row["argument_requirements"] = {"full_refresh=true": "仅曲绘可用"}
        if code:
            row.update(available=False, unavailable_code=code, unavailable_hint=hint)
        output[name] = row
    return output
