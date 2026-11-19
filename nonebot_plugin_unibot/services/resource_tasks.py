"""Maintenance workflows using the original conversion, cache and link rules."""
import asyncio
from datetime import datetime
import json
from pathlib import Path
import re
from urllib.parse import quote

import httpx

from .results import outcome
from .resources import asset_bytes, public_images
from .storage import atomic_bytes as write_bytes
from .maintenance import Stopped, observed_io
from . import http_safety


def write_json(path, data):
    write_bytes(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def local_path(root, relative):
    root = Path(root).resolve()
    target = (root / relative.lstrip("/\\")).resolve()
    target.relative_to(root)
    return target


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
    summary = [
        f"{label}：新增 {counts[kind + '_added']}，变更 {counts[kind + '_changed']}，"
        f"移除 {counts[kind + '_removed']}，新停用 {counts[kind + '_disabled']}"
        for kind, label in (("songs", "歌曲"), ("charts", "谱面"))
    ] if counts["changes_available"] else ["旧曲库元信息异常，已保留备份，无法可靠计算差异。"]
    hint = f"国服曲库已更新，共 {counts['song_count']} 首，保留及合并别名 {counts['alias_count']} 条。"
    return outcome(hint=hint + "\n" + "\n".join(summary), data=counts)


async def refresh_jackets(context, full_refresh=False):
    from .paths import SONGLIST_PATH
    from .song_model import Catalog, read_catalog

    catalog = Catalog(await observed_io(read_catalog, SONGLIST_PATH))
    if not catalog.songs:
        raise ValueError("missing songlist")
    targets = sorted({identity for song in catalog.songs.values()
                      for identity in ({chart.jacket_id for chart in song.charts} or {song.id})})
    counts = dict(total=len(targets), existing=0, success=0, failed=0, missing=0,
                  not_found=0, rate_limited=0, deferred=0, skipped=0)
    async with http_safety.client() as client:
        for index, identity in enumerate(targets, start=1):
            await context.check(f"checking_jackets:{index}/{len(targets)}")
            limited = counts["rate_limited"] > 0
            result = await public_images.get("jacket", identity, force=full_refresh,
                                             context=context, client=client, local_only=limited)
            counts["existing"] += result.had_local
            counts["missing"] += result.content is None
            if limited and (full_refresh or result.content is None):
                counts["deferred"] += 1
            elif result.status == "existing":
                counts["skipped"] += 1
            elif result.status == "downloaded":
                counts["success"] += 1
            else:
                counts["failed"] += 1
                if result.status in {"not_found", "rate_limited"}:
                    counts[result.status] += 1
    mode = "校验后刷新" if full_refresh else "补齐缺失和损坏"
    hint = (f"曲绘维护（{mode}）\n总数：{counts['total']}，原有可用：{counts['existing']}，"
            f"下载成功：{counts['success']}，失败：{counts['failed']}，仍缺：{counts['missing']}\n"
            f"其中 CDN 无图：{counts['not_found']}，限流失败：{counts['rate_limited']}，暂缓：{counts['deferred']}")
    partial = counts["failed"] or counts["deferred"]
    return outcome("PARTIAL_MAINTENANCE" if partial else "OK", hint,
                   status="partial" if partial else "ok", data=counts)


async def refresh_collections(context):
    from .. import collection_update as s
    versions = await observed_io(s.load_versions)
    summary, details, incomplete = [], {}, False
    async with http_safety.client() as client:
        for kind, key in s.COLLECTIONS.items():
            items, api_failures = {}, 0
            for version in versions:
                try:
                    response = await context.network(client.get(f"{s.API_BASE_URL}/{kind}/list?version={version}", timeout=30),
                                                     f"fetching_collection:{kind}:{version}")
                    response.raise_for_status()
                    rows = response.json().get(key, [])
                    if not isinstance(rows, list):
                        raise ValueError("invalid collection list")
                    for row in rows:
                        if isinstance(row, dict) and row.get("id") is not None:
                            identity = str(row["id"])
                            if not identity.isascii() or not identity.isdigit() or len(identity) > 18:
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
            if api_failures == 0:
                await context.write(f"saving_collection_list:{kind}", write_json,
                                    s.DATA_DIR / f"{kind}.json", {key: list(items.values())})
            counts = dict(success=0, skip=0, failed=0, missing=0, api_failures=api_failures)
            for identity in items:
                await context.check(f"checking_collection_asset:{kind}:{identity}")
                result = await public_images.get(kind, identity, client=client, context=context)
                key = {"existing": "skip", "downloaded": "success", "not_found": "missing"}.get(result.status, "failed")
                counts[key] += 1
                if result.status == "rate_limited":
                    break
            counts["deferred"] = len(items) - sum(counts[key] for key in ("skip", "success", "missing", "failed"))
            incomplete |= bool(counts["failed"] or counts["missing"] or api_failures)
            details[kind] = counts
            summary.append(f"{kind}: 数据 {len(items)}，下载 {counts['success']}，跳过 {counts['skip']}，404 {counts['missing']}，失败 {counts['failed']}，接口失败 {api_failures}，暂缓 {counts['deferred']}")
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
