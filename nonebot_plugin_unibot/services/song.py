import asyncio
from .results import outcome, image


async def search(query):
    from .. import song as s
    matches = await asyncio.to_thread(s.find_song_matches, query, limit=8)
    local_song, ambiguous = s.pick_song_match(matches)
    if ambiguous:
        jackets = await asyncio.gather(*(s.download_match_jacket(m) for m in ambiguous[:8]))
        content = await asyncio.to_thread(s.render_song_candidates_image, query, ambiguous[:8], list(jackets))
        return outcome("AMBIGUOUS_TARGET", "有多个候选，请用歌曲 ID 选择。", status="needs_input",
                       data={"candidates": [{"id": m["song"].get("id"), "title": m["song"].get("title")} for m in ambiguous[:8]]},
                       artifacts=[image(content)])
    if not local_song:
        return outcome("NOT_FOUND", "没有找到匹配的曲目。", status="needs_input")
    song_id = local_song.get("id")
    if not song_id:
        return outcome("INVALID_DATA", "匹配到的曲目数据中缺少ID。", status="unavailable")
    detail = await s.fetch_song_detail(song_id)
    if not detail:
        if local_song.get("data_source") == "chunirec":
            detail = local_song
        else:
            return outcome("UPSTREAM_UNAVAILABLE", "已匹配到曲目，但在获取详情时失败，可能网络连接有误或接口异常。", status="unavailable")
    jacket = await s.download_jacket(song_id)
    content = await asyncio.to_thread(s.render_song_image, local_song, detail, jacket)
    return outcome(data={"id": song_id, "title": local_song.get("title"),
                         "data_source": local_song.get("data_source")}, artifacts=[image(content)])
