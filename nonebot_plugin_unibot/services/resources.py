"""Validated public PNG assets shared by maintenance and renderers."""
import asyncio
from collections import OrderedDict
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from io import BytesIO
import re
import struct
import time

import httpx
from PIL import Image

from . import http_safety, paths
from .maintenance import observed_io
from .song_model import numeric_id
from .storage import atomic_bytes

CDN = "https://assets2.lxns.net/chunithm"
PUBLIC_KINDS = {"jacket", "character", "icon", "plate", "trophy"}
PLAYER_KINDS = {"character": "character", "icon": "map_icon",
                "plate": "name_plate", "trophy": "trophy", "portrait": "character"}
MAX_ASSET_BYTES = 30 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
REQUEST_INTERVAL = 1.0
DEMAND_WAIT_SECONDS = 10


async def asset_bytes(client, url, *, timeout=30, follow_redirects=True):
    async with client.stream("GET", url, timeout=timeout, follow_redirects=follow_redirects) as response:
        if response.status_code != 200:
            return response.status_code, b"", response.headers
        content = bytearray()
        async for chunk in response.aiter_bytes(65536):
            content.extend(chunk)
            if len(content) > MAX_ASSET_BYTES:
                raise ValueError("asset exceeds resource byte limit")
        return 200, bytes(content), response.headers


def decode_png(content, *, minimum_side=1):
    if not content or len(content) > MAX_ASSET_BYTES:
        return None
    try:
        with Image.open(BytesIO(content), formats=["PNG"]) as image:
            if min(image.size) < minimum_side or image.width * image.height > MAX_IMAGE_PIXELS:
                return None
            image.verify()
        with Image.open(BytesIO(content), formats=["PNG"]) as image:
            return image.convert("RGBA")
    except (OSError, ValueError, SyntaxError, IndexError, EOFError, struct.error, Image.DecompressionBombError):
        return None


def read_png_bytes(path):
    try:
        with path.open("rb") as file:
            return file.read(MAX_ASSET_BYTES + 1)
    except OSError:
        return None


def read_png(path, *, minimum_side=1):
    content = read_png_bytes(path)
    image = decode_png(content, minimum_side=minimum_side)
    if image is None:
        return None
    image.close()
    return content


def asset_path(kind, identity):
    if kind not in PUBLIC_KINDS and kind != "portrait":
        raise ValueError("unknown image asset type")
    if kind == "jacket":
        return paths.JACKET_DIR / f"{numeric_id(identity)}.png"
    if not re.fullmatch(r"[0-9]{1,18}", str(identity)):
        raise ValueError("invalid collection asset ID")
    return paths.DATA_DIR / kind / f"{int(identity)}.png"


def local_jacket(identity):
    try:
        return decode_png(read_png_bytes(asset_path("jacket", identity)))
    except ValueError:
        return None


def load_static_asset(group, name):
    """Return an independent RGBA image from the plugin's private UI pack."""
    if not all(re.fullmatch(r"[a-z0-9][a-z0-9_-]*", part) for part in (group, name)):
        return None
    return decode_png(read_png_bytes(paths.ASSETS_DIR / "ui" / group / f"{name}.png"))


def retry_after(headers):
    value = headers.get("Retry-After", "")
    try:
        seconds = int(value)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            seconds = 60
    return max(REQUEST_INTERVAL, seconds)


@dataclass(frozen=True)
class Asset:
    status: str
    content: bytes | None = None
    had_local: bool = False


class PublicImages:
    def __init__(self):
        self.inflight = {}
        self.failures = OrderedDict()
        self.request_lock = asyncio.Lock()
        self.next_request = 0.0
        self.rate_limited_until = 0.0

    async def request(self, client, url, context):
        async with self.request_lock:
            now = time.monotonic()
            if self.rate_limited_until - now > (60 if context else 0):
                return 429, b""
            wait = max(self.next_request, self.rate_limited_until) - now
            if wait > 0:
                await asyncio.sleep(wait)
            if context:
                await context.check()
            try:
                async with AsyncExitStack() as stack:
                    if client is None:
                        client = await stack.enter_async_context(http_safety.client())
                    status, content, headers = await asyncio.wait_for(
                        asset_bytes(client, url, timeout=15, follow_redirects=False), timeout=25)
                if status == 429:
                    self.rate_limited_until = time.monotonic() + retry_after(headers)
                return status, content
            finally:
                self.next_request = time.monotonic() + REQUEST_INTERVAL

    async def download(self, client, kind, path, context):
        url = f"{CDN}/{kind}/{path.name}"
        try:
            for _ in range(3 if context else 1):
                work = self.request(client, url, context)
                if context:
                    status, content = await context.network(work, f"downloading_{kind}:{path.stem}")
                else:
                    # Cold B50/candidate batches share rate limiting. Each
                    # visible tile waits only this long, including queue time.
                    try:
                        status, content = await asyncio.wait_for(work, timeout=DEMAND_WAIT_SECONDS)
                    except asyncio.TimeoutError:
                        return Asset("deferred")
                if status != 429:
                    break
            if status != 200:
                return Asset({404: "not_found", 429: "rate_limited"}.get(status, "failed"))
            if not await observed_io(valid_png, content):
                return Asset("failed")
            if context:
                await context.write(f"saving_{kind}:{path.stem}", atomic_bytes, path, content)
            else:
                await observed_io(atomic_bytes, path, content)
            return Asset("downloaded", content)
        except (httpx.HTTPError, asyncio.TimeoutError, ValueError, OSError):
            # Let the task report an uncertain maintenance write via RunContext.
            if context and context.possible_effect:
                raise
            return Asset("failed")

    async def get(self, kind, identity, *, force=False, context=None, client=None, local_only=False):
        try:
            path = asset_path(kind, identity)
        except ValueError:
            return Asset("not_found")
        if local_only or kind == "portrait":
            local = await observed_io(read_portrait if kind == "portrait" else read_png, path)
            return Asset("existing" if local is not None else "unavailable", local, local is not None)

        if not force and path in self.inflight:
            local = await observed_io(read_png, path)
            if local is not None:
                return Asset("existing", local, True)
        while (flight := self.inflight.get(path)) is not None:
            pending, maintenance_owned = flight
            wait = asyncio.shield(pending)
            if context:
                result = await context.network(wait, f"waiting_{kind}:{path.stem}")
            else:
                try:
                    result = await asyncio.wait_for(wait, timeout=DEMAND_WAIT_SECONDS)
                except asyncio.TimeoutError:
                    return Asset("deferred")
            if result is None or (force and result.status == "existing"):
                continue
            if context and not maintenance_owned and result.status in {"deferred", "rate_limited"}:
                continue
            return result

        pending = asyncio.get_running_loop().create_future()
        self.inflight[path] = (pending, context is not None)
        result = None
        try:
            local = await observed_io(read_png, path)
            if local is not None and not force:
                result = Asset("existing", local, True)
                return result
            if context is None and not force and self.failures.get(path, 0) > time.monotonic():
                result = Asset("deferred")
                return result
            downloaded = await self.download(client, kind, path, context)
            if downloaded.status == "downloaded":
                self.failures.pop(path, None)
            elif downloaded.status in {"failed", "not_found"}:
                delay = 3600 if downloaded.status == "not_found" else 60
                self.failures[path] = time.monotonic() + delay
                self.failures.move_to_end(path)
                if len(self.failures) > 2048:
                    self.failures.popitem(last=False)
            result = Asset(downloaded.status, downloaded.content or local, local is not None)
            return result
        finally:
            self.inflight.pop(path)
            pending.set_result(result)


def valid_png(content):
    image = decode_png(content)
    if image is None:
        return False
    image.close()
    return True


def read_portrait(path):
    # Public character PNGs are thumbnails. Large, verified artwork has its own slot.
    return read_png(path, minimum_side=512)


public_images = PublicImages()


async def jacket_image(identity):
    asset = await public_images.get("jacket", identity)
    return await observed_io(decode_png, asset.content) if asset.content else None


async def player_asset(player, kind):
    """Return validated PNG bytes for the currently equipped public resource."""
    field = PLAYER_KINDS.get(kind)
    row = player.get(field) if field else None
    if not isinstance(row, dict):
        return None
    return (await public_images.get(kind, row.get("id"))).content


async def prefetch_jackets(identities):
    """Warm visible progress-card jackets within a bounded cold-cache wait."""
    async def fetch():
        async with http_safety.client() as client:
            for identity in identities:
                await public_images.get("jacket", identity, client=client)

    try:
        await asyncio.wait_for(fetch(), timeout=DEMAND_WAIT_SECONDS)
    except asyncio.TimeoutError:
        pass  # The renderer can show its existing placeholders.
