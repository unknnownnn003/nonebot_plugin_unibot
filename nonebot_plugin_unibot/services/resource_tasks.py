"""Maintenance workflows using the original conversion, cache and link rules."""
import asyncio
import gzip
import io
from datetime import datetime
import json
import os
from pathlib import Path
import re
import time
import zipfile
import zlib
from urllib.parse import quote
from uuid import uuid4

import httpx

from .results import outcome
from .maintenance import Stopped, observed_io
from . import http_safety

MAX_ASSET_BYTES = 30 * 1024 * 1024
MAX_IMPORT_BYTES = 50 * 1024 * 1024


def parse_import(raw, depth=0, budget=None):
    budget = [MAX_IMPORT_BYTES] if budget is None else budget
    if len(raw) > MAX_IMPORT_BYTES or depth > 4:
        raise ValueError("import expansion limit")
    if raw.startswith(b"\x1f\x8b"):
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as file:
            content = file.read(budget[0] + 1)
        budget[0] -= len(content)
        if budget[0] < 0:
            raise ValueError("import expansion limit")
        return parse_import(content, depth + 1, budget)
    if raw.startswith(b"PK"):
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            if len(members) > 256:
                raise ValueError("too many archive members")
            for member in sorted(members, key=lambda item: not item.filename.lower().endswith(".json")):
                if member.is_dir():
                    continue
                if member.file_size > budget[0]:
                    raise ValueError("import expansion limit")
                with archive.open(member) as file:
                    content = file.read(budget[0] + 1)
                budget[0] -= len(content)
                if budget[0] < 0:
                    raise ValueError("import expansion limit")
                try:
                    return parse_import(content, depth + 1, budget)
                except (ValueError, UnicodeDecodeError):
                    continue
            raise ValueError("no JSON in archive")
    for encoding in (None, "utf-8-sig", "utf-8", "gbk", "gb2312", "utf-16", "latin-1"):
        try:
            return json.loads(raw if encoding is None else raw.decode(encoding))
        except (ValueError, UnicodeDecodeError):
            continue
    raise ValueError("invalid import JSON")


async def import_chunirec(context, bot, event):
    from .. import songlist_update as s
    from .attachments import read_file, AttachmentError
    try:
        raw = await context.network(read_file(bot, event, MAX_IMPORT_BYTES, prefer_get_file=True, check=context.check), "reading_chunirec_attachment")
        payload = await observed_io(parse_import, raw)
        rows = payload.get("chunirec_payload") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not rows:
            return outcome("INVALID_DATA", "文件格式错误：缺少 chunirec_payload 或不是有效列表。", status="needs_input")
        ids = {}
        for item in payload.get("naominet_id_map", []):
            if isinstance(item, list) and len(item) >= 3:
                ids[(str(item[0]), str(item[1]))] = str(item[2])
        counts = await context.write("committing_chunirec_upload", s.commit_chunirec_songlist, rows, ids)
        note = str(payload.get("naominet_status", "未包含 naominet 数据"))[:160]
        return outcome(hint=("chunirec 上传曲库同步完成！\n"
            f"新增：{counts['added']}，刷新：{counts['refreshed']}，保留落雪已有：{counts['skipped_lx']}\n"
            f"数字 ID：{note}\n文件修改时间：{counts['mtime']}"), data={**counts, "trust": "user_content"})
    except AttachmentError as exc:
        return outcome(exc.code, exc.hint, status="needs_input")
    except (ValueError, zipfile.BadZipFile, gzip.BadGzipFile, zlib.error, EOFError):
        if context.possible_effect:
            raise
        return outcome("INVALID_DATA", "文件不合法、压缩层数过多或超过50MB限制。", status="needs_input")


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
        songs = await context.network(client.get(s.BASE_URL + s.API_PATH, timeout=30), "fetching_songlist")
        songs.raise_for_status()
        aliases = await context.network(client.get(s.BASE_URL + "/api/v0/chunithm/alias/list", timeout=30), "fetching_song_aliases")
        aliases.raise_for_status()
        payload, aliases = songs.json(), aliases.json()
    counts = await context.write("committing_songlist", s.commit_lxns_songlist, payload, aliases)
    return outcome(hint=("更新成功！songlist.json 及歌曲别名已成功获取并覆盖。\n"
                         f"曲目数：{counts['song_count']}，别名数：{counts['alias_count']}\n文件修改时间：{counts['mtime']}"), data=counts)


async def refresh_chunirec(context):
    from .. import songlist_update as s
    token = s.config.chunirec_token
    if not token:
        return outcome("NOT_CONFIGURED", "同步失败：未配置 chunirec_token。", status="unavailable")
    ids, note = {}, "未配置 naominet_token，跳过数字 ID 补全"
    async with http_safety.client() as client:
        response = await context.network(client.get(s.CHUNIREC_SHOWALL_URL,
            params={"region": s.CHUNIREC_REGION, "token": token}, timeout=30), "fetching_chunirec")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            return outcome("INVALID_DATA", "chunirec未返回曲库列表，本次未写入。", status="unavailable")
        naominet = (s.config.naominet_token or "").strip()
        if naominet:
            try:
                class GuardedClient:
                    async def get(self, *args, **kwargs):
                        await context.check()
                        return await context.network(client.get(*args, **kwargs), "fetching_naominet_ids")
                ids, note = await context.network(s.fetch_naominet_id_map(GuardedClient(), naominet,
                    (s.config.naominet_session or "").strip() or None), "resolving_chunirec_ids")
            except Stopped:
                raise
            except Exception:
                note = "naominet 数字 ID 补全失败，保留原有ID"
    counts = await context.write("committing_chunirec", s.commit_chunirec_songlist, payload, ids)
    return outcome(hint=("chunirec 曲库同步完成！\n"
        f"新增：{counts['added']}，刷新：{counts['refreshed']}，保留落雪已有：{counts['skipped_lx']}\n"
        f"数字 ID：{note}\n文件修改时间：{counts['mtime']}"), data=counts)


async def refresh_jackets(context, full_refresh=False):
    from .. import get_resource as s
    def read():
        songs = json.loads((Path(s.DATA_DIR) / "songlist.json").read_text(encoding="utf-8")).get("songs")
        if not isinstance(songs, list) or not songs:
            raise ValueError("missing songlist")
        return songs, s.load_jacket_status()
    songs, cache = await observed_io(read)
    targets = {}
    for song in songs:
        identity = song.get("jacket_id") or song.get("rinnet_id") or song.get("id")
        for diff in song.get("difficulties", []) or []:
            if diff.get("difficulty") in (4, 5):
                identity = diff.get("origin_id", identity)
                break
        if identity is not None and s.should_use_lxns_jacket(str(identity)):
            targets[str(identity)] = song
    purged = 0
    if full_refresh:
        ids = set(targets) | s.collect_chunirec_jacket_ids(songs)
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
