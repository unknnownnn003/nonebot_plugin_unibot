"""Maintenance workflows using the original conversion, cache and link rules."""
import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import quote
from uuid import uuid4

import httpx

from .results import outcome
from .maintenance import Stopped, observed_io
from . import http_safety

MAX_ASSET_BYTES = 30 * 1024 * 1024
def write_bytes(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path, data):
    write_bytes(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def local_path(root, relative):
    root = Path(root).resolve()
    target = (root / relative.lstrip("/\\")).resolve()
    target.relative_to(root)
    return target


async def asset_bytes(client, url):
    async with client.stream("GET", url, timeout=30, follow_redirects=True) as response:
        if response.status_code != 200:
            return response.status_code, b"", dict(response.headers)
        content = bytearray()
        async for chunk in response.aiter_bytes(65536):
            content.extend(chunk)
            if len(content) > MAX_ASSET_BYTES:
                raise ValueError("asset exceeds resource byte limit")
        return 200, bytes(content), dict(response.headers)


async def refresh_songlist(context):
    from .. import songlist_update as s
    async with http_safety.client() as client:
        config = await context.network(client.get(s.BASE_URL + "/api/v0/site/config", timeout=30), "fetching_cn_version")
        config.raise_for_status()
        config = config.json()
        if config.get("success") is not True or config.get("code") != 200:
            raise ValueError("Invalid public site configuration")
        version = config.get("data", {}).get("resource_version", {}).get("chunithm")
        if type(version) is not int or version <= 0:
            raise ValueError("Missing CN resource version")
        songs = await context.network(client.get(s.BASE_URL + s.API_PATH, params={"notes": True, "version": version}, timeout=30), "fetching_songlist")
        songs.raise_for_status()
        aliases = await context.network(client.get(s.BASE_URL + "/api/v0/chunithm/alias/list", timeout=30), "fetching_song_aliases")
        aliases.raise_for_status()
        payload, aliases = songs.json(), aliases.json()
        if version not in {v.get("version") for v in payload.get("versions", []) if isinstance(v, dict)}:
            raise ValueError("CN version absent from catalog")
        payload["rating_version"] = version
    counts = await context.write("committing_songlist", s.commit_lxns_songlist, payload, aliases)
    return outcome(hint=("更新成功！songlist.json 及歌曲别名已成功获取并覆盖。\n"
                         f"曲目数：{counts['song_count']}，别名数：{counts['alias_count']}\n文件修改时间：{counts['mtime']}"), data=counts)


async def refresh_jackets(context, full_refresh=False):
    from .. import get_resource as s
    def read():
        from .song_model import read_catalog
        songs = read_catalog(Path(s.DATA_DIR) / "songlist.json").get("songs")
        if not isinstance(songs, list) or not songs:
            raise ValueError("missing songlist")
        return songs, s.load_jacket_status()
    songs, cache = await observed_io(read)
    targets = {}
    for song in songs:
        from .song_model import jacket_id
        identity = jacket_id(song)
        if identity is not None and s.should_use_lxns_jacket(str(identity)):
            targets[str(identity)] = song
    purged = 0
    if full_refresh:
        ids = set(targets)
        purged = await context.write("purging_jacket_cache", s.purge_cached_jackets, ids)
        for identity in ids:
            cache.pop(identity, None)
    counts = dict(success=0, skipped=0, missing=0, failed=0, deferred=0, requests=0, rate_limited=0, purged=purged)
    items, now, last_request = sorted(targets, key=int), time.time(), 0.0
    async with http_safety.client() as client:
        for index, identity in enumerate(items):
            await context.check(f"checking_jackets:{index}/{len(items)}")
            path = local_path(s.JACKET_DIR, identity + ".png")
            if await observed_io(s.is_existing_jacket_valid, str(path)):
                counts["skipped"] += 1
                cache.pop(identity, None)
                continue
            if not full_refresh and s.should_defer_jacket(cache, identity, now):
                counts["deferred"] += 1
                continue
            if not full_refresh and counts["requests"] >= s.MAX_INCREMENTAL_REQUESTS:
                counts["deferred"] += len(items) - index
                break
            counts["requests"] += 1
            for attempt in range(s.MAX_RATE_LIMIT_RETRIES + 1):
                try:
                    delay = s.LXNS_REQUEST_INTERVAL - (time.monotonic() - last_request)
                    if delay > 0:
                        await context.network(asyncio.sleep(delay), "waiting_jacket_request_interval")
                    status, content, headers = await context.network(
                        asset_bytes(client, s.LXNS_JACKET_URL.format(song_id=identity)), f"downloading_jacket:{identity}")
                    last_request = time.monotonic()
                    if status == 429:
                        counts["rate_limited"] += 1
                        if attempt < s.MAX_RATE_LIMIT_RETRIES:
                            delay = s.parse_retry_after(httpx.Response(status, headers=headers))
                            await context.network(asyncio.sleep(delay), "waiting_jacket_rate_limit")
                            continue
                        s.mark_jacket_status(cache, identity, "rate_limited", status)
                    elif status == 200 and s.looks_like_png(content):
                        await context.write(f"saving_jacket:{identity}", write_bytes, path, content)
                        cache.pop(identity, None)
                        counts["success"] += 1
                    else:
                        key = "missing" if status == 404 else "failed"
                        counts[key] += 1
                        s.mark_jacket_status(cache, identity, key, status)
                    break
                except Stopped:
                    raise
                except (httpx.HTTPError, ValueError):
                    counts["failed"] += 1
                    s.mark_jacket_status(cache, identity, "failed", 0)
                    break
            if cache.get(identity, {}).get("state") == "rate_limited":
                counts["deferred"] += len(items) - index - 1
                break
    await context.write("saving_jacket_status", s.write_jacket_status, cache)
    mode = "完整重下" if full_refresh else "增量更新"
    hint = (f"曲绘更新任务完成！（{mode}）\n成功下载：{counts['success']}\n跳过已有：{counts['skipped']}\n"
            f"本次请求：{counts['requests']}\n暂缓跳过：{counts['deferred']}\n删除旧缓存：{counts['purged']}\n"
            f"落雪缺失：{counts['missing']}\n下载失败：{counts['failed']}\n触发限流：{counts['rate_limited']}")
    return outcome("PARTIAL_MAINTENANCE" if counts["failed"] else "OK", hint,
                   status="partial" if counts["failed"] else "ok", data=counts)


async def refresh_collections(context):
    from .. import collection_update as s
    versions = await observed_io(s.load_versions)
    summary, details, incomplete = [], {}, False
    async with http_safety.client() as client:
        for kind, info in s.COLLECTIONS.items():
            items, api_failures = {}, 0
            for version in versions:
                try:
                    response = await context.network(client.get(f"{s.API_BASE_URL}/{kind}/list?version={version}", timeout=30),
                                                     f"fetching_collection:{kind}:{version}")
                    response.raise_for_status()
                    rows = response.json().get(info["key"], [])
                    if not isinstance(rows, list):
                        raise ValueError("invalid collection list")
                    for row in rows:
                        if isinstance(row, dict) and row.get("id") is not None:
                            identity = str(row["id"])
                            if not identity.isdigit():
                                raise ValueError("invalid collection id")
                            items[identity] = row
                except Stopped:
                    raise
                except (httpx.HTTPError, ValueError, AttributeError):
                    api_failures += 1
            if not items:
                incomplete = True
                summary.append(f"{kind}: 未获取到有效数据，保留本地旧文件")
                continue
            await context.write(f"saving_collection_list:{kind}", write_json, s.DATA_DIR / f"{kind}.json", {info["key"]: list(items.values())})
            counts = dict(success=0, skip=0, failed=0, missing=0, api_failures=api_failures)
            semaphore = asyncio.Semaphore(15)
            async def fetch(identity):
                async with semaphore:
                    await context.check(f"checking_collection_asset:{kind}:{identity}")
                    path = local_path(s.DATA_DIR / kind, identity + ".png")
                    if await observed_io(lambda: path.exists() and path.stat().st_size > 8):
                        counts["skip"] += 1
                        return
                    try:
                        status, content, _ = await context.network(asset_bytes(client,
                            s.ASSETS_BASE_URL + s.COLLECTIONS[kind]["path"].format(id=identity)), f"downloading_collection:{kind}:{identity}")
                        if status == 404:
                            counts["missing"] += 1
                        elif status == 200 and s.looks_like_png(content):
                            await context.write(f"saving_collection_asset:{kind}:{identity}", write_bytes, path, content)
                            counts["success"] += 1
                        else:
                            counts["failed"] += 1
                    except Stopped:
                        raise
                    except (httpx.HTTPError, ValueError):
                        counts["failed"] += 1
            tasks = [asyncio.create_task(fetch(identity)) for identity in items]
            try:
                await asyncio.gather(*tasks)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            incomplete |= bool(counts["failed"] or api_failures)
            details[kind] = counts
            summary.append(f"{kind}: 数据 {len(items)}，下载 {counts['success']}，跳过 {counts['skip']}，404 {counts['missing']}，失败 {counts['failed']}，接口失败 {api_failures}")
    hint = "所有收藏品列表及资源已更新处理完毕！\n" + "\n".join(summary) + f"\n完成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    return outcome("PARTIAL_MAINTENANCE" if incomplete else "OK", hint, status="partial" if incomplete else "ok", data=details)


async def refresh_stories(context):
    from .. import story as s
    async with http_safety.client() as client:
        response = await context.network(client.get(s.BASE_URL + "/_sidebar.md", timeout=10), "fetching_story_index")
        response.raise_for_status()
        categories, queue, current = {}, {}, "未分类"
        for line in response.text.splitlines():
            category = re.search(r"^\s*\*\s+([^\[\]]+)$", line.rstrip())
            if category:
                current = category.group(1).strip()
                categories.setdefault(current, [])
            match = re.search(r"\[([^\]]+)\]\((/[^\)]+)\)", line)
            if match:
                name, path = match.group(1).strip(), match.group(2).strip().split("?")[0].split("#")[0]
                if path.endswith(".md") and "README" not in path:
                    try:
                        local_path(s.DATA_DIR, path)
                    except ValueError:
                        continue
                    categories.setdefault(current, []).append(dict(name=name, path=path))
                    queue[path] = name
        if not queue:
            return outcome("INVALID_DATA", "获取目录成功，但没有解析到任何剧情条目；已取消更新，避免覆盖本地索引。", status="unavailable")
        index = dict(categories=categories, mapping={name: path for path, name in queue.items()})
        await context.write("saving_story_index", write_json, s.INDEX_FILE, index)
        successes, failures = 0, 0
        for path in queue:
            try:
                response = await context.network(client.get(s.BASE_URL + quote(path), timeout=10), f"fetching_story:{path}")
                response.raise_for_status()
                assets = []
                text = await observed_io(s.rewrite_links, response.text, path, lambda url, relative, **kwargs: assets.append((url, relative)))
                for url, relative in assets:
                    target = local_path(s.DATA_DIR, relative)
                    status, content, _ = await context.network(asset_bytes(client, url), "fetching_story_image")
                    if status == 200:
                        await context.write("saving_story_image", write_bytes, target, content)
                    else:
                        failures += 1
                await context.write(f"saving_story:{path}", write_bytes, local_path(s.DATA_DIR, path), text.encode("utf-8"))
                successes += 1
            except Stopped:
                raise
            except (httpx.HTTPError, ValueError):
                failures += 1
            await context.network(asyncio.sleep(0.1), "waiting_story_interval")
    return outcome("PARTIAL_MAINTENANCE" if failures else "OK", f"剧情爬取完毕！\n成功: {successes}\n失败: {failures}",
                   status="partial" if failures else "ok", data={"success": successes, "failed": failures})
