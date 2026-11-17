"""Local configuration and identity gates for contextual tool discovery."""
from __future__ import annotations

import sys
from .job_store import _db_thread
from .permissions import native_superuser
from .group_policy import enabled


async def describe(specs, context):
    return await _db_thread(lambda: _describe(specs, context))


def _describe(specs, context):
    from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent
    bot, event = context["bot"], context["event"]
    valid = isinstance(event, (GroupMessageEvent, PrivateMessageEvent)) and str(bot.self_id) == str(event.self_id)
    group = isinstance(event, GroupMessageEvent)
    root = __package__.rsplit(".services", 1)[0]
    score_config = getattr(sys.modules.get(root + ".get_player_score"), "config", None)
    rating_config = getattr(sys.modules.get(root + ".best_rating"), "config", score_config)
    lxns = bool(getattr(score_config, "lxns_token", None))
    rating = bool(getattr(rating_config, "lxns_token", None))
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
        elif name == "score.sync" and not lxns:
            code, hint = "NOT_CONFIGURED", "未配置落雪开发者密钥，请管理员配置后再同步。"
        if name == "player.best":
            row["argument_requirements"] = {"mode=b": "需配置落雪开发者密钥", "mode=b30": "读取本地成绩；不要求在线密钥"}
            row["argument_availability"] = {"mode=b": rating, "mode=b30": True}
        elif name == "maintenance.refresh":
            row["argument_requirements"] = {"full_refresh=true": "仅曲绘可用"}
        if name == "score.import_file":
            row["input_requirements"] = ["当前真实事件的受控文件附件"]
        if code:
            row.update(available=False, unavailable_code=code, unavailable_hint=hint)
        output[name] = row
    return output
