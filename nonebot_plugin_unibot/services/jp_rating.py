"""JP Rating simulation using matching raw song IDs and difficulty slots."""
from collections import Counter, defaultdict
from . import jp_history, legacy_rating, rating


def partition(edition, chart):
    # An earlier playable observation wins for old songs, including re-releases
    # and songs that later gain ULTIMA. An inferred package date alone cannot
    # qualify a song for NEW20.
    if chart["seen_before"]:
        return "old"
    day = chart["released_on"]
    if not day or chart["release_basis"] in {"package_date_assumption", "disk_presence_only",
                                               "tutorial_package_observation_day"}:
        return None
    if day > edition["end_on"]:
        return None
    return "old" if day < edition["start_on"] else "new"


def inventory(history, edition):
    return history._rows("""WITH prior AS (
            SELECT DISTINCT m.identity FROM versions v JOIN music_versions m USING(version)
            WHERE v.ordinal < ? AND m.state='present'
        ), releases AS (
            SELECT e.identity, min(e.effective_on) AS day FROM history_events e
            JOIN history_entities h USING(identity)
            WHERE h.kind='song' AND e.kind='released' AND e.effective_on IS NOT NULL GROUP BY e.identity
        )
        SELECT c.*,m.jacket_sha256,m.release_tag,r.revision,
            coalesce(f.exact_on,CASE WHEN f.identity IS NULL THEN releases.day END) AS released_on,
            coalesce(f.basis,'historical_release_event') AS release_basis, prior.identity IS NOT NULL AS seen_before
        FROM chart_versions c JOIN music_versions m USING(version,identity)
        JOIN versions v USING(version)
        JOIN history_observations o ON o.batch='end:'||c.version AND o.identity=c.chart_identity
        JOIN history_revisions r USING(revision)
        LEFT JOIN prior ON prior.identity=m.identity
        LEFT JOIN sdhd_first_release f ON f.identity=m.identity
        LEFT JOIN releases ON releases.identity=m.identity
        WHERE c.version=? AND c.slot BETWEEN 0 AND 4 AND c.enabled=1
          AND c.state='present' AND m.state='present' AND coalesce(m.disable_flag,0)=0
          AND c.constant_status IN ('hdd_observed','hdd_inherited') AND c.selected_hundredths IS NOT NULL
          AND EXISTS (SELECT 1 FROM history_windows w WHERE w.version=c.version AND w.identity=c.chart_identity
              AND v.end_on BETWEEN w.start_on AND w.end_on AND w.availability IN ('present','inferred_present')
              AND w.revision=o.revision)
        ORDER BY c.chart_identity""", (edition["ordinal"], edition["version"]))


def match_charts(charts):
    slots = defaultdict(list)
    for chart in charts:
        slots[chart["music_id"], chart["slot"]].append(chart)
    return slots


def select(history, edition, snapshot):
    from ..best_rating import normalize_fc, score_to_rank

    b50 = edition["ordinal"] >= history.by_name["VERSE"]["ordinal"]
    modern = edition["ordinal"] >= history.by_name["NEW"]["ordinal"]
    records = {}
    for record in snapshot.scores:
        key = record["id"], record["level_index"]
        if 0 <= key[1] <= 4 and (key not in records or record["score"] > records[key]["score"]):
            records[key] = record
    slots = match_charts(inventory(history, edition))
    coverage = Counter(id_matched=0, conflict=0, unknown=0, unclassified=0, high_unresolved=0)
    ranked = {"old": [], "new": []}
    for key, record in records.items():
        matches = slots.get(key, ())
        chart = matches[0] if len(matches) == 1 else None
        if chart:
            coverage["id_matched"] += 1
        else:
            coverage["conflict" if matches else "unknown"] += 1
        pool = partition(edition, chart) if b50 and chart else "old"
        score = record["score"]
        if chart and pool is None:
            coverage["unclassified"] += 1
        if chart is None or pool is None:
            coverage["high_unresolved"] += score >= 1_007_500
            continue
        constant = jp_history.hdd_constant(chart)
        ranked[pool].append(dict(
            id=chart["music_id"], chart_identity=chart["chart_identity"], revision=chart["revision"],
            jacket_id=chart["jacket_sha256"], song_name=chart["title"], level_index=chart["slot"],
            level_value=constant, score=score, clear=record.get("clear"),
            full_combo=normalize_fc(record.get("full_combo")),
            rank=score_to_rank(score) if modern else legacy_rating.score_rank(score),
            rating=(rating.chart_rating if modern else legacy_rating.chart_rating)(constant, score),
        ))
    for items in ranked.values():
        items.sort(key=lambda row: (-row["rating"], -row["score"], row["chart_identity"]))
    sample = len(ranked["old"]) + len(ranked["new"])
    return ranked["old"][:30], ranked["new"][:20], dict(coverage, score_count=len(records),
        sample=sample, unresolved=len(records) - sample)


def validate_request(history, version, *, b50):
    edition = history.parse_version(version)
    if not edition:
        return None, "未识别日服版本，请使用数据库中的正式版本名。"
    expected_b50 = edition["ordinal"] >= history.by_name["VERSE"]["ordinal"]
    if b50 != expected_b50:
        command = "b50" if expected_b50 else "b30"
        return None, f"{edition['version']} 请使用 /{command} {edition['version']}。"
    return edition, None


async def render(snapshot, history, edition, *, b50):
    from .. import best_rating
    from .image_components import player_images
    from .job_store import async_call, async_render
    from .paths import HISTORY_DIR
    from .results import image, outcome

    try:
        old, new, data = await async_call(select, history, edition, snapshot)
    except jp_history.HISTORY_ERRORS:
        return outcome("JP_HISTORY_UNAVAILABLE", jp_history.UNAVAILABLE, status="unavailable")
    data.update(version=edition["version"], cutoff=edition["end_on"], best30_count=len(old), new20_count=len(new))
    total = data["score_count"]
    percentage = data["id_matched"] / total if total else 0
    hint = (f"{edition['version']} · {edition['end_on']} · 可计算 {data['sample']}/{total} 张 · "
            f"同 ID/难度匹配 {percentage:.1%} · 冲突 {data['conflict']} / "
            f"未知 {data['unknown']} / 未分区 {data['unclassified']} · 未纳入 SSS 及以上 {data['high_unresolved']} 张")
    if data["sample"] < 10 or (b50 and data["unresolved"] and (not old or not new)):
        return outcome("JP_HISTORY_INSUFFICIENT", "日服历史 Rating 数据不足。\n" + hint,
                       status="needs_input", data=data)
    if data["unresolved"]:
        hint += " · 部分模拟，结果可能偏低"
    assets = await player_images(snapshot.player)
    jackets = await async_call(lambda: {digest: jp_history.load_jacket(HISTORY_DIR, digest)
                                      for digest in {row["jacket_id"] for row in old + new}})
    if b50:
        data.update(best30_average=rating.average(old, 30), new20_average=rating.average(new, 20),
                    rating_estimate=rating.average(old + new, 50))
        content = await async_render(best_rating.draw_jp_b50, snapshot.player, old, new, jackets, assets, data)
        hint = "当前最好成绩 × 指定历史定数的模拟 · 总值除以 50，缺位计 0\n" + hint
    else:
        summary = legacy_rating.simulate([row["rating"] for row in old])
        data.update(b30_average=str(summary.b30), repeat_best=str(summary.repeat_best), top_ten=str(summary.top_ten))
        content = await async_render(best_rating.draw_jp_b30, snapshot.player, old, jackets, assets, summary, data, hint)
    return outcome(hint=hint, data=data, artifacts=[image(content)])
