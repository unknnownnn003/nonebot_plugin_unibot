"""Optional HTTPS callback and trusted native commands; no model-facing tokens."""
from __future__ import annotations

from urllib.parse import parse_qs

from .oauth_store import OAuthError
from .error_notices import error_text

_service = None


def get_service():
    """Internal phase 3B accessor; callers must validate identity and live policy."""
    if _service is None:
        raise OAuthError("NOT_CONFIGURED")
    return _service


class ScrubCallbackQuery:
    """Remove the query in-place before ASGI access/error logs can retain a code."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        from .oauth import CALLBACK_PATH
        if scope["type"] == "http" and scope["path"] == CALLBACK_PATH:
            scope["_unibot_oauth_query"] = scope.get("query_string", b"")
            scope["query_string"] = b""
            try:
                await self.app(scope, receive, send)
            finally:
                scope.pop("_unibot_oauth_query", None)
        else:
            await self.app(scope, receive, send)


def callback_endpoint(service):
    async def callback(request):
        from starlette.responses import HTMLResponse
        # Do not echo upstream values, exceptions, account claims or full URLs.
        raw = request.scope.pop("_unibot_oauth_query", b"")
        status = 400
        text = "授权未完成或已过期。请重新发起 /落雪授权。"
        try:
            if len(raw) > 8192:
                raise OAuthError("CALLBACK_INVALID")
            args = parse_qs(raw.decode("ascii"), keep_blank_values=True, max_num_fields=8)
            if any(len(v) != 1 for v in args.values()) or "state" not in args or ("code" in args) == ("error" in args):
                raise OAuthError("CALLBACK_INVALID")
            proof = await service.callback(args["state"][0], args.get("code", [None])[0], args.get("error", [None])[0])
            # proof is local random confirmation, not a provider authorization code.
            text = "网页验证成功。请回到机器人私聊，在5分钟内发送：<br><code>/落雪确认 " + proof + "</code><br>只确认本人刚刚登录的账号，请勿转发此页面。"
            status = 200
        except Exception:
            # No exception interpolation / traceback: libraries can embed secrets.
            pass
        return HTMLResponse("<!doctype html><meta charset=utf-8><title>落雪授权</title><p>" + text + "</p>", status_code=status,
            headers={"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer",
                     "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
                     "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY"})
    return callback


def configure():
    global _service
    from nonebot import get_driver, get_plugin_config
    from nonebot.log import logger
    from ..config import Config
    config = get_plugin_config(Config)
    if not config.lxns_oauth_enabled:
        return
    try:
        from fastapi import FastAPI
        from .oauth import CALLBACK_PATH, OAuthService, Settings
        from .oauth_store import OAuthStore
        from .paths import OAUTH_DIR
        from . import group_policy
        from types import SimpleNamespace
        app = get_driver().server_app
        if not isinstance(app, FastAPI) or any(getattr(route, "path", None) == CALLBACK_PATH for route in app.routes):
            raise OAuthError("ROUTE_UNAVAILABLE")
        settings = Settings(config.lxns_oauth_client_id, config.lxns_oauth_client_secret.get_secret_value(),
                            config.lxns_oauth_redirect_uri, config.lxns_oauth_encryption_key.get_secret_value())
        store = OAuthStore(OAUTH_DIR, settings.encryption_key)
        service = OAuthService(settings, store, group_allowed=lambda group: group_policy.enabled(SimpleNamespace(group_id=group), "player.bind"))
        app.add_middleware(ScrubCallbackQuery)
        app.add_route(CALLBACK_PATH, callback_endpoint(service), methods=["GET"])
        _service = service
    except Exception:
        logger.warning("UniBot OAuth 不可用：请检查配置、存储权限及 FastAPI 回调路由；未输出敏感详情。")


HINTS = {
    "ALREADY_BOUND": "已绑定 OAuth；如需换号，请先 /落雪解绑 确认。旧好友码不会被改写。",
    "CONFIRM_PENDING": "已有网页验证等待确认，请完成私聊确认或等待5分钟后重新授权。",
    "CONFIRM_INVALID": "确认无效或已过期，请核对网页提示，或重新 /落雪授权。",
    "FEATURE_DISABLED": "本群对应功能已关闭，未执行授权操作。",
    "BUSY": "授权操作正在进行，请稍后再试。",
}


async def command(bot, event, action, argument=""):
    """Identity comes only from the real OneBot event, never from arguments."""
    from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent
    from . import group_policy
    if not isinstance(event, (GroupMessageEvent, PrivateMessageEvent)) or str(event.self_id) != str(bot.self_id):
        return error_text("无法核验当前请求者。")
    group = getattr(event, "group_id", None)
    if group is not None and not group_policy.enabled(event, "player.bind"):
        return error_text(HINTS["FEATURE_DISABLED"])
    if action == "confirm" and group is not None:
        return "请在机器人私聊中完成网页确认。"
    if _service is None:
        return error_text("OAuth 尚未配置或回调不可用；原 /bind 和旧查分仍可使用。")
    try:
        if action == "begin":
            url = await _service.begin(bot.self_id, event.user_id, origin_group=group)
            message = "请登录本人落雪账号，仅申请 read_player 与 openid。网页完成后按提示私聊确认；链接10分钟有效，请勿转发。\n" + url
        elif action == "confirm":
            await _service.confirm(bot.self_id, event.user_id, argument.strip())
            return "OAuth 绑定成功。旧好友码、成绩和现有查分方式均保留。"
        elif action == "status":
            state = await _service.status(bot.self_id, event.user_id)
            message = {"unbound": "尚未绑定 OAuth。", "active": "OAuth 已绑定；本阶段尚不用于查分。",
                       "authorizing": "等待网页授权。", "awaiting_confirmation": "等待网页提示的私聊确认。"}.get(state, "OAuth 暂不可用，请重新授权；若正在刷新，请稍后重试。")
        elif action == "unlink":
            if argument.strip() != "确认":
                return "解除本地 OAuth 并保留旧好友码、成绩：请发送 /落雪解绑 确认。"
            await _service.unlink(bot.self_id, event.user_id)
            message = "本地 OAuth 已解除，凭据已清除，待完成授权已取消。旧好友码和成绩保留。落雪未公布撤销端点，请同时到落雪应用管理页撤销授权；本地解绑不代表远端已撤销。"
        else:
            return error_text("未知授权操作。")
        if group is None:
            return message
        # Do not fall back to publishing a link or account status into the group.
        if not group_policy.enabled(event, "player.bind"):
            return error_text(HINTS["FEATURE_DISABLED"])
        try:
            await bot.send_private_msg(user_id=event.user_id, message=message)
            return "已私发操作说明，请查看机器人私聊。"
        except Exception:
            return error_text("私信送达未确认。请添加机器人好友后私聊执行；已生成的授权链接会自动过期。")
    except OAuthError as error:
        return error_text(HINTS.get(str(error), "授权操作未完成，请稍后重试；原好友码和成绩不受影响。"))
    except Exception:
        return error_text("授权操作未完成，请检查 OAuth 配置与受保护存储。")
