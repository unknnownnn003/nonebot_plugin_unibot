"""Original authoritative rating algorithms/renderers; no model-side arithmetic."""
from .job_store import async_call
import asyncio
import httpx
from .results import outcome, image


async def best(qq, mode="b", version="2026", *, migrate_legacy=False):
    from .. import best_rating as b
    if mode == "b":
        if version != b.SUPPORTED_VERSION:
            hint = (f"当前只能生成 {b.SUPPORTED_VERSION} 的 B30/N20。{version} 属于日服新版本，当前本地曲库与落雪中二数据还不支持。"
                    if version in b.KNOWN_FUTURE_VERSIONS else "版本参数暂不支持。可用：/b 或 /b 2026")
            return outcome("INVALID_ARGUMENT", hint, status="needs_input")
        if not b.config.lxns_token:
            return outcome("NOT_CONFIGURED", "未配置落雪开发者密钥，无法从落雪获取 B30/N20。", status="unavailable")
        async with httpx.AsyncClient() as client:
            friend_code, player = await b.resolve_player(client, qq)
        if not friend_code:
            return outcome("NOT_BOUND", "未找到落雪玩家信息，请先使用 /bind 绑定好友码，或确认落雪已绑定当前账号。", status="needs_input")
        raw_b30, raw_n20, err = await b.fetch_lxns_bests(friend_code)
        if err:
            return outcome("UPSTREAM_UNAVAILABLE", "获取落雪 B30/N20 失败，请稍后再试。", status="unavailable")
        songs, new_versions = await async_call(b.load_song_data)
        meta = b.build_chart_meta(songs, const_source="lx")
        b30 = [item for item in (b.normalize_lxns_item(r, meta) for r in raw_b30 if isinstance(r, dict)) if item]
        n20 = [item for item in (b.normalize_lxns_item(r, meta) for r in raw_n20 if isinstance(r, dict)) if item]
        b30.sort(key=lambda x: (x["rating"], x["score"]), reverse=True)
        n20.sort(key=lambda x: (x["rating"], x["score"]), reverse=True)
        if not b30 and not n20:
            return outcome("NO_SCORES", "落雪没有返回可展示的 B30/N20 数据。", status="needs_input")
        scores = await async_call(b.load_local_scores, qq)
        suggestions = b.build_recommendations(scores, meta, b30[:30], n20[:20], new_versions)
        player = dict(player)
        player["rating"] = b.total_rating_raw(b30, n20)
        try:
            jackets = await b.prepare_jackets(b30[:30] + n20[:20] + suggestions)
            content = await async_call(b.draw_best_image, player, b30[:30], n20[:20], suggestions, jackets, version)
        except Exception:
            return outcome("RENDER_FAILED", "生成 B30/N20 图片失败。", status="unavailable")
        return outcome(data={"mode": "b", "constant_source": "lx", "rating": player["rating"]}, artifacts=[image(content)])
    if migrate_legacy:
        await async_call(b.migrate_legacy_manual_scores, qq)
    songs, _ = await async_call(b.load_song_data)
    meta = b.build_chart_meta(songs, const_source="chunirec")
    scores = await async_call(b.load_b30_scores, qq, include_legacy=not migrate_legacy)
    if not scores:
        return outcome("NO_SCORES", "本地没有可用成绩。请先使用 /lxupdate、/chuupdate 或 /传分。", status="needs_input")
    items = [item for item in (b.normalize_local_item(r, meta, const_source="chunirec") for r in scores.values())
             if item and b.safe_float(item.get("level_value"), 0) > 0 and b.safe_int(item.get("score"), 0) > 0]
    items.sort(key=lambda x: (x["rating"], x["score"]), reverse=True)
    items = items[:30]
    if not items:
        return outcome("NO_SCORES", "本地成绩中没有可用于计算 B30 的谱面。", status="needs_input")
    player = {"UserName": "Local Player", "Level": 0}
    player.update(await async_call(b.load_player_info, qq))
    try:
        jackets = await b.prepare_jackets(items)
        content = await async_call(b.draw_b30_image, player, items, jackets)
    except Exception:
        return outcome("RENDER_FAILED", "生成 B30 图片失败。", status="unavailable")
    return outcome(data={"mode": "b30", "constant_source": "chunirec",
                         "b30_average": b.section_average(items, 30)}, artifacts=[image(content)])


async def profile(qq):
    from ..get_player_songlist import query_player_profile
    return await query_player_profile(qq)


async def progress(qq, query):
    from ..overpower_list_local import query_progress
    return await query_progress(qq, query)


async def recommend(qq, query=""):
    from ..recommendation import recommend_chart
    return await recommend_chart(qq, "推什么 " + query)
