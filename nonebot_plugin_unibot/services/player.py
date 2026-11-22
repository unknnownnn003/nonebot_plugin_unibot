"""All player queries consume the same authorized personal-API snapshot."""
import asyncio
from decimal import Decimal

from .image_components import player_images
from .job_store import async_call, async_render
from .results import outcome, image
from .player_data import get_reader, failure, PlayerError
from .oauth import OAuthError

DIFFICULTIES = {"bas": 0, "basic": 0, "adv": 1, "advanced": 1, "exp": 2, "expert": 2,
                "mas": 3, "master": 3, "ult": 4, "ultima": 4, "we": 5, "worldsend": 5}
LEVELS = ["BASIC", "ADVANCED", "EXPERT", "MASTER", "ULTIMA", "WORLD'S END"]


async def query(bot, event, capability, arguments, *, expected=None, checkpoint=None):
    try:
        jp = capability in {"player.best", "player.b50"} and "jp_version" in arguments
        if jp:
            from . import jp_history, jp_rating
            try:
                history = await async_call(jp_history.load)
            except jp_history.HISTORY_ERRORS:
                return outcome("JP_HISTORY_UNAVAILABLE", jp_history.UNAVAILABLE, status="unavailable")
            edition, error = jp_rating.validate_request(history, arguments["jp_version"], b50=capability == "player.b50")
            if error:
                return outcome("INVALID_ARGUMENTS", error, status="needs_input")
        snapshot = await get_reader().get(bot, event, capability, expected=expected, checkpoint=checkpoint)
        from .song_model import read_catalog, Catalog
        from .paths import SONGLIST_PATH
        try:
            catalog = Catalog(await async_call(read_catalog, SONGLIST_PATH))
        except (ValueError, OSError):
            if capability != "player.profile":
                raise
            catalog = None
        unmatched = 0
        if catalog is not None:
            known = {(song.id, chart.difficulty) for song in catalog.songs.values() for chart in song.charts}
            unmatched = sum((row["id"], row["level_index"]) not in known for row in snapshot.scores)
        if jp:
            output = await jp_rating.render(snapshot, catalog, history, edition, b50=capability == "player.b50")
        elif capability in {"player.best", "player.b50"}:
            output = await best(snapshot, catalog, b50=capability == "player.b50")
        elif capability == "player.legacy_rating":
            output = await legacy_best(snapshot, catalog)
        elif capability == "player.profile":
            from ..get_player_songlist import query_player_profile
            output = await query_player_profile(snapshot, catalog)
        elif capability == "player.progress":
            from ..overpower_list_local import query_progress
            output = await query_progress(snapshot, arguments["query"], catalog)
        elif capability == "player.recommend":
            from ..recommendation import recommend_chart, remember
            from .recommendation import parse_filter
            try:
                mode, level = parse_filter(arguments.get("filter", ""))
            except ValueError as error:
                return outcome("INVALID_ARGUMENTS", str(error), status="needs_input")
            scope = (str(bot.self_id), str(event.user_id), getattr(event, "group_id", None), snapshot.binding)
            output = await recommend_chart(snapshot, catalog, mode, level, scope)
        elif capability == "player.song_scores":
            output = await song_scores(snapshot, catalog, **arguments)
        else:
            raise PlayerError("INVALID_DATA")
        if checkpoint is not None:
            await checkpoint()
        from . import group_policy
        if not group_policy.enabled(event, capability):
            raise PlayerError("FEATURE_DISABLED")
        if not await get_reader().oauth.binding_matches(bot.self_id, event.user_id, snapshot.binding):
            raise PlayerError("AUTHORIZATION_CHANGED")
        if not group_policy.enabled(event, capability):
            raise PlayerError("FEATURE_DISABLED")
        if capability == "player.recommend" and output["code"] == "OK":
            remember(scope, mode, level, output["data"]["recommendations"])
        output.setdefault("data", {}).update(source="lxns_oauth", cache_hit=snapshot.cache_hit,
            cache_ttl_seconds=60, unmatched_scores=unmatched)
        if unmatched and not jp:
            hint = f"另有 {unmatched} 条成绩未匹配当前国服谱面，未按标题映射或纳入计算；请更新曲库后重查。"
            output["public_hint"] = output.get("public_hint", "") + "\n" + hint
            output["fallback_text"] = output.get("fallback_text", "") + "\n" + hint
        return output
    except (PlayerError, OAuthError) as error:
        return failure(error)
    except (ValueError, OSError):
        return outcome("CATALOG_UNAVAILABLE", "当前国服曲库不可用，请管理员更新曲库后重试。", status="unavailable")


async def best(snapshot, catalog, *, b50=False):
    from .. import best_rating as b
    from .rating import select, average
    try:
        old, new, unknown, version = select(snapshot, catalog, b50=b50)
    except ValueError:
        return outcome("RATING_VERSION_UNAVAILABLE", "曲库缺少落雪当前国服版本，请管理员 /更新songlist 后再查 B50；未猜测新曲范围。", status="unavailable")
    if not old and not new and unknown:
        return outcome("RATING_VERSION_UNCLASSIFIED", f"{unknown} 条成绩的歌曲版本未知或晚于当前国服版本，无法构成B50；请更新曲库核对。", status="unavailable", data={"unclassified_scores": unknown})
    if not old and not new:
        return outcome("NO_MATCHED_SCORES", "没有可纳入统计的国服普通谱面成绩；WORLD'S END、禁用和尚未配信谱面不参与。", status="needs_input")
    # Prioritize the player bar ahead of the larger batch on a cold CDN cache.
    assets = await player_images(snapshot.player)
    jackets = await b.prepare_jackets(old + new)
    if b50:
        data = {"rating_estimate": average(old + new, 50), "best30_average": average(old, 30),
                "new20_average": average(new, 20), "best30_count": len(old), "new20_count": len(new),
                "rating_version": version, "unclassified_scores": unknown}
        content = await async_render(b.draw_b50_image, snapshot.player, old, new, jackets, assets,
                                     data["best30_average"], data["new20_average"], data["rating_estimate"])
        hint = "B50：旧版本 BEST 30 + 当前版本 NEW 20，缺位按零计，合计除以50；个人资料 Rating 为落雪返回值。"
        if unknown:
            hint += f" {unknown} 条歌曲版本未知或晚于当前版本的成绩未分区，请更新曲库核对。"
    else:
        data = {"b30_average": average(old, 30), "chart_count": len(old)}
        content = await async_render(b.draw_b30_image, snapshot.player, old, jackets, assets, data["b30_average"])
        hint = "B30：所有版本普通谱面按落雪单谱面 Rating 排序取30，缺位按零计；此均值不是个人总 Rating。"
    return outcome(hint=hint, data=data, artifacts=[image(content)])


async def legacy_best(snapshot, catalog):
    from .. import best_rating as b
    from . import legacy_rating as legacy
    try:
        history = await async_call(legacy.load_history)
    except ValueError:
        return outcome("HISTORICAL_CATALOG_UNAVAILABLE", "旧框历史定数不可用，请管理员检查安装文件。", status="unavailable")
    items = legacy.select(snapshot, catalog, history)
    summary = legacy.simulate([item["rating"] for item in items])
    jackets, assets = await asyncio.gather(b.prepare_jackets(items), player_images(snapshot.player))
    content = await async_render(b.draw_legacy_rating_image, snapshot.player, items, jackets, assets, summary)
    data = {"version": legacy.VERSION, "chart_count": len(items), "b30_average": f"{summary.b30:.4f}",
            "repeat_best": f"{summary.repeat_best:.4f}", "top_ten": f"{summary.top_ten:.4f}",
            "difference": f"{summary.difference:.4f}"}
    hint = (f"旧版定数模拟｜非真实历史 Rating。B30 均值 {data['b30_average']}，"
            f"B1 × 10 {data['repeat_best']}，B1–B10 {data['top_ten']}，差值 {data['difference']}。"
            if items else "没有可纳入旧框模拟的历史谱面成绩。")
    return outcome(hint=hint, data=data, artifacts=[image(content)])


def split_query(query, level_index=None):
    query = query.strip()
    if level_index is None:
        head, separator, tail = query.rpartition(" ")
        code = tail.lower().replace("'", "").replace("_", "")
        if separator and (code in DIFFICULTIES or code in {str(i) for i in range(6)}):
            level_index = DIFFICULTIES.get(code, int(code) if code.isdigit() else None)
            query = head.strip()
    return query, level_index


async def song_scores(snapshot, catalog, query, level_index=None):
    from .song import prepare
    from .rating import eligible
    from .song_model import jacket_id
    from .song_images import render_song_scores
    from .. import song as native_song

    query, level_index = split_query(query, level_index)
    prepared = await prepare(query, catalog=catalog)
    if not prepared["song"]:
        choices = [{"id": row["song"]["id"], "title": row["song"].get("title")} for row in prepared["ambiguous"][:8]]
        from .song import page_hint
        text = page_hint(prepared) + " " + "、".join(f"{r['id']} {r['title']}" for r in choices) if choices else "未找到歌曲，请使用歌名、别名或原始ID。"
        return outcome("AMBIGUOUS_TARGET" if choices else "NOT_FOUND", text, status="needs_input", data={"candidates": choices, "total": prepared["total"], "page": prepared["page"]})
    song_id = prepared["song"]["id"]
    if song_id not in catalog.songs:
        raise PlayerError("INVALID_DATA")
    family_id = catalog.parent_id(song_id)
    selected = prepared["song"]
    family = selected if song_id == family_id else catalog.query_song(family_id)
    charts = {}
    for difficulty in selected["difficulties"]:
        charts.setdefault((difficulty["song_id"], difficulty["difficulty"]), []).append(difficulty)
    rows = [dict(r, charts=charts[(r["id"], r["level_index"])]) for r in snapshot.scores
            if (r["id"], r["level_index"]) in charts and (level_index is None or r["level_index"] == level_index)]
    rows.sort(key=lambda row: (row["level_index"], row["id"]))
    lines = [f"{family.get('title', '')}（原曲ID {family['id']}）"]
    for row in rows:
        metadata = row["charts"]
        row["metadata_ambiguous"] = len(metadata) > 1
        row["standard_rating_applicable"] = row["level_index"] < 5
        row["standard_op_applicable"] = row["level_index"] < 5
        label = LEVELS[row["level_index"]]
        if row["level_index"] == 5:
            row["upstream_metrics"] = {key: row.pop(key) for key in ("rating", "over_power") if key in row}
            label += " [" + " / ".join(f"{c.get('kanji', '')} ★{c.get('star', '')}" for c in metadata) + "]"
            if len(metadata) > 1:
                label += "（同ID多谱面，API未区分，保留全部元数据）"
        lines.append(f"{label} · ID {row['id']} · 谱面版本 {'/'.join(str(c.get('version', '')) for c in metadata)}\n"
                     + (f"{row['score']:,} | WORLD'S END（不计普通 Rating / OP）\n" if row["level_index"] == 5 else
                      f"{row['score']:,} | Rating {row['rating']:.4f} | OP {row['over_power']:.2f}\n")
                     + f"{row.get('clear') or '-'} / {row.get('full_combo') or '-'} / {row.get('full_chain') or '-'}")
    if not rows:
        lines.append("落雪没有返回该曲所选难度的个人成绩；未将缺失记录当作零分。")

    # Use eligible ordinary charts for the 4A song-wide OP strip, independent
    # of the selected difficulty. The API supplies all earned values.
    maxima = {}
    for sid in {sid for sid, _ in charts}:
        owner = catalog.songs[sid]
        for chart in owner.charts:
            key = (sid, chart.difficulty)
            if key in charts and eligible(owner, chart):
                maxima[key] = (Decimal(str(chart.attributes["level_value"])) + 3) * 5
    song_op = None
    if maxima:
        actual = max((Decimal(str(r["over_power"])) for r in snapshot.scores
                      if (r["id"], r["level_index"]) in maxima), default=None)
        song_op = actual, max(maxima.values())
    jacket, assets = await asyncio.gather(native_song.download_jacket(jacket_id(selected)),
                                          player_images(snapshot.player, portrait=True))
    content = await async_call(render_song_scores, selected, jacket, snapshot.player, assets, rows, song_op, maxima)
    return outcome("OK" if rows else "NO_SONG_SCORES", "\n".join(lines), status="ok" if rows else "needs_input",
                   data={"song_id": family["id"], "scores": rows}, artifacts=[image(content)])
