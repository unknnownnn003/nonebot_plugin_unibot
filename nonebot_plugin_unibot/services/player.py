"""All player queries consume the same authorized personal-API snapshot."""
from .job_store import async_call
from .results import outcome, image
from .player_data import get_reader, failure, PlayerError
from .oauth import OAuthError

DIFFICULTIES = {"bas": 0, "basic": 0, "adv": 1, "advanced": 1, "exp": 2, "expert": 2,
                "mas": 3, "master": 3, "ult": 4, "ultima": 4, "we": 5, "worldsend": 5}
LEVELS = ["BASIC", "ADVANCED", "EXPERT", "MASTER", "ULTIMA", "WORLD'S END"]


async def query(bot, event, capability, arguments, *, expected=None, checkpoint=None):
    try:
        snapshot = await get_reader().get(bot, event, capability, expected=expected, checkpoint=checkpoint)
        from .song_model import read_catalog, Catalog
        from .paths import SONGLIST_PATH
        catalog = Catalog(await async_call(read_catalog, SONGLIST_PATH)) if capability != "player.profile" else Catalog({"songs": []})
        known = {(str(song.id), chart.difficulty) for song in catalog.songs.values() for chart in song.charts}
        unmatched = sum((str(row["id"]), row["level_index"]) not in known for row in snapshot.scores) if capability != "player.profile" else 0
        if capability == "player.best":
            output = await best(snapshot, catalog)
        elif capability == "player.profile":
            from ..get_player_songlist import query_player_profile
            output = await query_player_profile(snapshot)
        elif capability == "player.progress":
            from ..overpower_list_local import query_progress
            output = await query_progress(snapshot, arguments["query"], catalog)
        elif capability == "player.recommend":
            from ..recommendation import recommend_chart
            output = await recommend_chart(snapshot, "推什么 " + arguments.get("filter", ""), catalog)
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
        output.setdefault("data", {}).update(source="lxns_oauth", cache_hit=snapshot.cache_hit,
            cache_ttl_seconds=60, unmatched_scores=unmatched)
        if unmatched:
            hint = f"另有 {unmatched} 条成绩未匹配当前国服谱面，未按标题映射或纳入计算；请更新曲库后重查。"
            output["public_hint"] = output.get("public_hint", "") + "\n" + hint
            output["fallback_text"] = output.get("fallback_text", "") + "\n" + hint
        return output
    except (PlayerError, OAuthError) as error:
        return failure(error)
    except (ValueError, OSError):
        return outcome("CATALOG_UNAVAILABLE", "当前国服曲库不可用，请管理员更新曲库后重试。", status="unavailable")


async def best(snapshot, catalog):
    from .. import best_rating as b
    meta = b.build_chart_meta([song.as_dict() for song in catalog.songs.values()])
    items = [b.build_item(meta[(str(r["id"]), r["level_index"])], r) for r in snapshot.scores
             if (str(r["id"]), r["level_index"]) in meta]
    items.sort(key=lambda row: (row["rating"], row["score"], row["id"], row["level_index"]), reverse=True)
    items = items[:30]
    if not items:
        return outcome("NO_MATCHED_SCORES", "没有匹配当前国服普通谱面的成绩；WORLD'S END 不参与标准 B30。", status="needs_input")
    jackets = await b.prepare_jackets(items)
    content = await async_call(b.draw_b30_image, snapshot.player, items, jackets)
    return outcome(data={"b30_average": b.section_average(items, 30), "chart_count": len(items)}, artifacts=[image(content)])


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
    query, level_index = split_query(query, level_index)
    prepared = await prepare(query)
    if not prepared["song"]:
        choices = [{"id": row["song"]["id"], "title": row["song"].get("title")} for row in prepared["ambiguous"][:8]]
        text = "同名曲目有多个候选，请指定原始歌曲ID：" + "、".join(f"{r['id']} {r['title']}" for r in choices) if choices else "未找到歌曲，请使用歌名、别名或原始ID。"
        return outcome("AMBIGUOUS_TARGET" if choices else "NOT_FOUND", text, status="needs_input", data={"candidates": choices})
    song_id = prepared["song"]["id"]
    if song_id not in catalog.songs:
        raise PlayerError("INVALID_DATA")
    family = catalog.query_song(catalog.parent_id(song_id))
    charts = {}
    for difficulty in family["difficulties"]:
        charts.setdefault((str(difficulty["song_id"]), difficulty["difficulty"]), []).append(difficulty)
    rows = [dict(r, charts=charts[(str(r["id"]), r["level_index"])]) for r in snapshot.scores
            if (str(r["id"]), r["level_index"]) in charts and (level_index is None or r["level_index"] == level_index)]
    rows.sort(key=lambda row: (row["level_index"], row["id"]))
    lines = [f"{family.get('title', '')}（原曲ID {family['id']}）"]
    for row in rows:
        metadata = row["charts"]
        chart = metadata[0]
        row["metadata_ambiguous"] = len(metadata) > 1
        label = LEVELS[row["level_index"]]
        if row["level_index"] == 5:
            label += " [" + " / ".join(f"{c.get('kanji', '')} ★{c.get('star', '')}" for c in metadata) + "]"
            if len(metadata) > 1:
                label += "（同ID多谱面，API未区分，保留全部元数据）"
        lines.append(f"{label} · ID {row['id']} · 谱面版本 {'/'.join(str(c.get('version', '')) for c in metadata)}\n"
                     f"{row['score']:,} | Rating {row['rating']:.4f} | OP {row['over_power']:.2f}\n"
                     f"{row.get('clear') or '-'} / {row.get('full_combo') or '-'} / {row.get('full_chain') or '-'}")
    if not rows:
        lines.append("落雪没有返回该曲所选难度的个人成绩；未将缺失记录当作零分。")
    return outcome("OK" if rows else "NO_SONG_SCORES", "\n".join(lines), status="ok" if rows else "needs_input",
                   data={"song_id": family["id"], "scores": rows})
