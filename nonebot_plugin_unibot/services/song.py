from .job_store import async_call
import asyncio
from .results import outcome, image


async def change_alias(song_id, alias, *, delete=False):
    """Use the original locked writer shared with native alias commands."""
    from .. import song as s
    alias = alias.strip()
    if not alias:
        return outcome("INVALID_ARGUMENT", "别名不能为空。", status="needs_input")
    try:
        found, changed = await async_call(
            s.remove_song_alias if delete else s.add_song_alias, song_id, alias)
    except (OSError, ValueError):
        return outcome("STORAGE_FAILED", "保存曲目列表失败，请检查数据文件。", status="unavailable")
    if not found:
        return outcome("NOT_FOUND", "没有找到这个歌曲ID，请先查歌选择明确ID。", status="needs_input")
    return outcome(data={"song_id": song_id, "alias": alias, "changed": changed,
                         "action": "delete" if delete else "add"})


async def prepare(query, *, catalog=None):
    from .. import song as s
    options = {"catalog": catalog} if catalog is not None else {}
    matches = await async_call(s.find_song_matches, query, limit=8, **options)
    local_song, ambiguous = s.pick_song_match(matches)
    import copy
    return copy.deepcopy({"song": local_song, "ambiguous": ambiguous})


async def search(query, *, prepared=None):
    from .. import song as s
    resolved = prepared if prepared is not None else await prepare(query)
    local_song, ambiguous = resolved["song"], resolved["ambiguous"]
    if ambiguous:
        jackets = await asyncio.gather(*(s.download_match_jacket(m) for m in ambiguous[:8]))
        content = await async_call(s.render_song_candidates_image, query, ambiguous[:8], list(jackets))
        return outcome("AMBIGUOUS_TARGET", "有多个候选，请用歌曲 ID 选择。", status="needs_input",
                       data={"candidates": [{"id": m["song"].get("id"), "title": m["song"].get("title")} for m in ambiguous[:8]]},
                       artifacts=[image(content)])
    if not local_song:
        return outcome("NOT_FOUND", "没有找到匹配的曲目。", status="needs_input")
    song_id = local_song.get("id")
    if not song_id:
        return outcome("INVALID_DATA", "匹配到的曲目数据中缺少ID。", status="unavailable")
    from .song_model import jacket_id
    jacket = await s.download_jacket(jacket_id(local_song))
    content = await async_call(s.render_song_image, local_song, jacket)
    return outcome(data={"id": song_id, "title": local_song.get("title"),
                         "data_source": local_song.get("data_source"),
                         "charts": local_song.get("difficulties", []),
                         "related_song_ids": [row["id"] for row in local_song.get("related_songs", [])]}, artifacts=[image(content)])


async def aliases(query):
    from .. import song as s
    matches = await async_call(s.find_song_matches, query, limit=8)
    song, ambiguous = s.pick_song_match(matches)
    if ambiguous:
        return outcome("AMBIGUOUS_TARGET", "请提供更精确的名称或ID。", status="needs_input",
                       data={"candidates": [{"id": m["song"].get("id"), "title": m["song"].get("title")} for m in ambiguous[:8]]})
    if song is None:
        return outcome("NOT_FOUND", "未找到匹配的曲目。", status="needs_input")
    return outcome(data={"id": song.get("id"), "title": song.get("title"), "aliases": song.get("aliases", [])})


async def detail(song_id):
    """Render details for one explicit local song ID without fuzzy selection."""
    from .. import song as s

    matches = await async_call(s.find_song_matches, str(song_id), limit=8)
    local_song = next((item["song"] for item in matches
                       if str(item.get("song", {}).get("id")) == str(song_id)), None)
    if local_song is None:
        return outcome("NOT_FOUND", "没有找到这个曲目 ID。", status="needs_input")
    from .song_model import jacket_id
    jacket = await s.download_jacket(jacket_id(local_song))
    content = await async_call(s.render_song_image, local_song, jacket)
    return outcome(data={"id": local_song.get("id"), "title": local_song.get("title"),
                         "data_source": local_song.get("data_source"),
                         "charts": local_song.get("difficulties", []),
                         "related_song_ids": [row["id"] for row in local_song.get("related_songs", [])]}, artifacts=[image(content)])
