"""One OAuth personal Player + complete Score[] snapshot; never merge old files."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
import json
import math
import time

import httpx

from .oauth import ISSUER, OAuthError, owner_key
from .results import outcome

PLAYER_CAPABILITIES = frozenset({"player.b50", "player.best", "player.profile", "player.progress", "player.recommend", "player.song_scores"})
RETIRED = frozenset({"player.bind", "score.sync", "score.submit", "score.import_file"})


class PlayerError(Exception):
    pass


HINTS = {
    "REFRESH_BUSY": "授权正在刷新，请稍后重试。",
    "NOT_AUTHORIZED": "请先使用 /落雪授权 完成本人 OAuth 绑定。",
    "NOT_CONFIGURED": "OAuth 尚未配置，请联系管理员。",
    "AUTHORIZATION_CHANGED": "授权已变化，本次结果未继续使用，请重新查询。",
    "UPSTREAM_UNAUTHORIZED": "落雪授权已失效，请先 /落雪解绑 确认 后重新授权。",
    "UPSTREAM_TIMEOUT": "落雪个人接口超时，本次未更新数据，请稍后重试。",
    "UPSTREAM_UNAVAILABLE": "落雪个人接口暂不可用，本次未更新数据。",
    "RATE_LIMITED": "落雪接口请求过于频繁，请稍后再试。",
    "INVALID_DATA": "落雪返回的数据不完整或无效，已有有效数据未被覆盖。",
    "NO_SCORES": "落雪尚未返回个人成绩，请先在落雪同步游戏数据；本次未覆盖有效缓存。",
    "FEATURE_DISABLED": "本群对应功能已关闭。",
    "PERMISSION_DENIED": "无法核验真实请求者。",
    "JOB_CANCELLED": "任务已取消，未继续查询或发送。",
}


def failure(error):
    code = str(error)
    if code == "BUSY":
        code = "REFRESH_BUSY"
    if code not in HINTS:
        code = "NOT_AUTHORIZED" if code in {"GRANT_INVALID", "BUSY", "OIDC_INVALID", "NETWORK_FAILED"} else "UPSTREAM_UNAVAILABLE"
    return outcome(code, HINTS[code], status="cancelled" if code == "JOB_CANCELLED" else "denied" if code in {"FEATURE_DISABLED", "PERMISSION_DENIED", "AUTHORIZATION_CHANGED"} else "unavailable")


def retired():
    return outcome("CAPABILITY_RETIRED", "旧绑定、同步、上传及手动成绩已退役；请 /落雪授权 后直接查询。", status="unavailable")


def _number(value, *, integer=False, minimum=0, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlayerError("INVALID_DATA")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite or value < minimum or (maximum is not None and value > maximum) or (integer and type(value) is not int):
        raise PlayerError("INVALID_DATA")
    return value


def validate(player, scores):
    if not isinstance(player, dict) or not isinstance(player.get("name"), str) or not player["name"] or len(player["name"]) > 256:
        raise PlayerError("INVALID_DATA")
    _number(player.get("rating")); _number(player.get("level"), integer=True)
    for key in ("over_power", "over_power_progress", "reborn_count", "total_play_count", "currency", "total_currency"):
        if key in player:
            _number(player[key])
    for key in ("character", "trophy", "name_plate", "map_icon", "class_emblem"):
        if player.get(key) is not None and not isinstance(player[key], dict):
            raise PlayerError("INVALID_DATA")
        if isinstance(player.get(key), dict):
            if "id" in player[key]:
                _number(player[key]["id"], integer=True, minimum=-(2**63))
            if "name" in player[key] and not isinstance(player[key]["name"], str):
                raise PlayerError("INVALID_DATA")
    if not isinstance(scores, list) or len(scores) > 30000:
        raise PlayerError("INVALID_DATA")
    if not scores:
        raise PlayerError("NO_SCORES")
    keys = set()
    for row in scores:
        if not isinstance(row, dict):
            raise PlayerError("INVALID_DATA")
        _number(row.get("id"), integer=True, minimum=1)
        _number(row.get("level_index"), integer=True, maximum=5)
        _number(row.get("score"), integer=True, maximum=1010000)
        for key in ("rating", "over_power"):
            # WORLD'S END has no standard metrics; the personal API may omit
            # them. Retain supplied upstream values without inventing zeros.
            if row["level_index"] < 5 or key in row:
                _number(row.get(key))
        if not isinstance(row.get("song_name"), str) or not isinstance(row.get("level"), str):
            raise PlayerError("INVALID_DATA")
        for key in ("clear", "full_combo", "full_chain", "rank", "play_time", "upload_time", "last_played_time"):
            if row.get(key) is not None and not isinstance(row[key], str):
                raise PlayerError("INVALID_DATA")
        key = (row["id"], row["level_index"])
        if key in keys:
            raise PlayerError("INVALID_DATA")  # Never overwrite ambiguously duplicated charts.
        keys.add(key)
    return deepcopy(player), deepcopy(scores)


@dataclass(repr=False)
class Snapshot:
    player: dict
    scores: list
    binding: str = field(repr=False)
    fetched_at: float
    cache_hit: bool = False



class PlayerData:
    def __init__(self, oauth, *, transport=None, clock=time.monotonic, ttl=60):
        self.oauth = oauth
        self.transport = transport
        self.clock = clock
        self.ttl = ttl
        self.cache = OrderedDict()
        self.locks = {}
        oauth._invalidators.append(self.invalidate)

    def invalidate(self, bot, qq):
        owner = owner_key(bot, qq)
        for key in list(self.cache):
            if key[0] == owner:
                self.cache.pop(key, None)

    async def get(self, bot, event, capability, *, expected=None, checkpoint=None):
        from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent
        from . import group_policy
        if not isinstance(event, (GroupMessageEvent, PrivateMessageEvent)) or str(event.self_id) != str(bot.self_id):
            raise PlayerError("PERMISSION_DENIED")
        owner = owner_key(bot.self_id, event.user_id)
        async def check(binding=None):
            if not group_policy.enabled(event, capability):
                raise PlayerError("FEATURE_DISABLED")
            if checkpoint is not None:
                await checkpoint()
            if binding and not await self.oauth.binding_matches(bot.self_id, event.user_id, binding):
                self.invalidate(bot.self_id, event.user_id)
                raise PlayerError("AUTHORIZATION_CHANGED")
            if not group_policy.enabled(event, capability):
                raise PlayerError("FEATURE_DISABLED")
        await check()
        lock, count = self.locks.get(owner, (asyncio.Lock(), 0))
        self.locks[owner] = (lock, count + 1)
        try:
            async with lock:
                try:
                    token, binding = await self.oauth.credential_lease(bot.self_id, event.user_id)
                except OAuthError as error:
                    if str(error) in {"NOT_AUTHORIZED", "GRANT_INVALID", "NOT_CONFIGURED"}:
                        self.invalidate(bot.self_id, event.user_id)
                    raise
                for cached_key in list(self.cache):
                    if cached_key[0] == owner and cached_key[1] != binding:
                        self.cache.pop(cached_key)
                if expected is not None and binding != expected:
                    raise PlayerError("AUTHORIZATION_CHANGED")
                await check(binding)
                key = (owner, binding)
                cached = self.cache.get(key)
                if cached and cached.fetched_at + self.ttl > self.clock():
                    self.cache.move_to_end(key)
                    result = deepcopy(cached); result.cache_hit = True
                    return result
                player = await self._get("/api/v0/user/chunithm/player", token, bot, event)
                await check(binding)
                scores = await self._get("/api/v0/user/chunithm/player/scores", token, bot, event)
                await check(binding)
                player, scores = validate(player, scores)
                snapshot = Snapshot(player, scores, binding, self.clock())
                # Publication is synchronous after the final live authorization/cancel gate.
                await check(binding)
                self.cache[key] = snapshot
                self.cache.move_to_end(key)
                while len(self.cache) > 64:
                    self.cache.popitem(last=False)
                return deepcopy(snapshot)
        finally:
            lock, count = self.locks[owner]
            if count == 1:
                self.locks.pop(owner)
            else:
                self.locks[owner] = (lock, count - 1)

    async def _get(self, path, token, bot, event):
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=12, trust_env=False, follow_redirects=False) as client:
                async with client.stream("GET", ISSUER + path, headers={"Authorization": "Bearer " + token}) as response:
                    if response.status_code == 401:
                        changed = await self.oauth.invalidate_access(bot.self_id, event.user_id, token)
                        raise PlayerError("UPSTREAM_UNAUTHORIZED" if changed else "AUTHORIZATION_CHANGED")
                    if response.status_code == 429:
                        raise PlayerError("RATE_LIMITED")
                    if response.status_code != 200:
                        raise PlayerError("UPSTREAM_UNAVAILABLE")
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 16 * 1024 * 1024:
                            raise PlayerError("INVALID_DATA")
                    body = json.loads(raw)
                    if not isinstance(body, dict):
                        raise PlayerError("INVALID_DATA")
                    if body.get("code") == 401:
                        changed = await self.oauth.invalidate_access(bot.self_id, event.user_id, token)
                        raise PlayerError("UPSTREAM_UNAUTHORIZED" if changed else "AUTHORIZATION_CHANGED")
                    if body.get("code") == 429:
                        raise PlayerError("RATE_LIMITED")
                    # Official OAuth response envelope; never treat an error body as a Player.
                    if body.get("success") is not True or body.get("code") != 200 or "data" not in body:
                        raise PlayerError("INVALID_DATA")
                    return body["data"]
        except httpx.TimeoutException:
            raise PlayerError("UPSTREAM_TIMEOUT") from None
        except httpx.HTTPError:
            raise PlayerError("UPSTREAM_UNAVAILABLE") from None
        except (ValueError, UnicodeError):
            raise PlayerError("INVALID_DATA") from None


_reader = None


def get_reader():
    global _reader
    from .oauth_runtime import get_service
    oauth = get_service()
    if _reader is None or _reader.oauth is not oauth:
        _reader = PlayerData(oauth)
    return _reader


async def job_authorized(job):
    if job.capability_id not in PLAYER_CAPABILITIES:
        return True
    binding = job.payload.get("oauth_binding")
    if not binding:
        return False
    try:
        allowed = await get_reader().oauth.binding_matches(job.bot_id, job.actor_id, binding)
    except OAuthError:
        return False
    return allowed and player_policy_allowed(job)


def player_policy_allowed(job):
    if job.context_type != "group":
        return True
    from types import SimpleNamespace
    from .group_policy import enabled
    return enabled(SimpleNamespace(group_id=int(job.context_id)), job.capability_id)
