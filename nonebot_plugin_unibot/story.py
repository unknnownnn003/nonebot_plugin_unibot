from .services.error_notices import error_call as _error_call, result_call as _result_call
import asyncio
import base64
import io
import json
import math
import os
import random
import re
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional
import httpx

from PIL import Image
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment, Message
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

try:
    from nonebot_plugin_htmlrender import md_to_pic
    HTMLRENDER_AVAILABLE = True
except ImportError:
    HTMLRENDER_AVAILABLE = False


DATA_DIR = Path(__file__).parent / "data" / "story"
# index.json 的路径也需要修改
INDEX_FILE = DATA_DIR / "index.json"
BASE_URL = "https://copel-popn.github.io"

# index.json 结构如下：
# {
#   "categories": {
#       "分类名": [ {"name": "角色名", "path": "/path/to.md"} ]
#   },
#   "mapping": {
#       "角色名": "/path/to.md"
#   }
# }

def load_index() -> dict:
    if INDEX_FILE.exists():
        try:
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load story index: {e}")
    return {"categories": {}, "mapping": {}}


def download_image_sync(url: str, rel_path: str, force: bool = False):
    """同步下载图片到本地路径供未来渲染读取"""
    local_path = DATA_DIR / rel_path
    if local_path.exists() and not force:
        return
    local_path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            with open(local_path, "wb") as f:
                f.write(response.read())
    except Exception as e:
        logger.error(f"Failed to download image {url} during scraping: {e}")


def rewrite_links(md_text: str, md_path: str, downloader=None) -> str:
    """将 markdown 中的相对链接转换为绝对链接。对于图片则会统一下载到本地并映射。"""
    base_dir = BASE_URL + '/' + '/'.join(x for x in md_path.split('/')[:-1] if x)
    if not base_dir.endswith('/'):
        base_dir += '/'

    def process_url(url: str) -> str:
        # 如果是图片，并且指向的是本站资源
        if url.lower().split('?')[0].endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
            if url.startswith("http://") or url.startswith("https://"):
                target_url = url
            elif url.startswith("data:"):
                return url
            elif url.startswith("/"):
                target_url = BASE_URL + url
            else:
                target_url = base_dir + url
                
            # 计算对应的本地路径并下载
            if target_url.startswith(BASE_URL):
                img_rel = "images" + target_url[len(BASE_URL):].split('?')[0]
            else:
                ext = target_url.split('.')[-1].split('?')[0]
                img_rel = f"images/external/{abs(hash(target_url))}.{ext}"
                
            (downloader or download_image_sync)(target_url, img_rel, force=True)
            return f"local://{img_rel}"
            
        # 对于其它非图片链接
        if url.startswith("http://") or url.startswith("https://") or url.startswith("data:"):
            return url
        if url.startswith("/"):
            return BASE_URL + url
        return base_dir + url

    # 1. 替换 inline links: ![alt](url) 或 [text](url)
    def repl_md(match):
        alt = match.group(1)
        url = match.group(2)
        title = match.group(3) or ""
        new_url = process_url(url)
        if title:
            return f"[{alt}]({new_url}{title})"
        return f"[{alt}]({new_url})"
        
    md_text = re.sub(r'\[([^\]]*)\]\(([^)\s]+)(\s+[^)]+)?\)', repl_md, md_text)
    
    # 2. 替换 reference links: [1]: /images/xxx
    def repl_ref(match):
        ref = match.group(1)
        url = match.group(2)
        new_url = process_url(url)
        return f"{ref}: {new_url}"
        
    md_text = re.sub(r'^[ \t]*(\[[^\]]+\]):\s*([^<>\s]+)', repl_ref, md_text, flags=re.MULTILINE)

    # 3. 替换 HTML img 标签: <img src="url">
    def repl_img(match):
        prefix = match.group(1)
        url = match.group(2)
        suffix = match.group(3)
        new_url = process_url(url)
        return f'{prefix}"{new_url}"{suffix}'
        
    md_text = re.sub(r'(<img[^>]+src=)["\']([^"\']+)["\']([^>]*>)', repl_img, md_text, flags=re.IGNORECASE)

    return md_text


def split_image(img_bytes: bytes, max_height: int = 4000) -> List[bytes]:
    """切分过长的图片"""
    try:
        # 防止过长图片抛出 DecompressionBombError 或 Warning 导致跳过切分
        import warnings
        from PIL import Image
        warnings.simplefilter('ignore', Image.DecompressionBombWarning)
        Image.MAX_IMAGE_PIXELS = None
        
        img = Image.open(io.BytesIO(img_bytes))
        width, height = img.size
        if height <= max_height:
            return [img_bytes]
            
        pieces = []
        num_pieces = math.ceil(height / max_height)
        for i in range(num_pieces):
            box = (0, i * max_height, width, min((i + 1) * max_height, height))
            piece = img.crop(box)
            buf = io.BytesIO()
            if img.mode in ("RGBA", "P"):
                piece = piece.convert("RGB")
            # 适当降低切片图片质量，防止合并转发总包大小超出框架 WS 上限
            piece.save(buf, format="JPEG", quality=75)
            pieces.append(buf.getvalue())
        return pieces
    except Exception as e:
        logger.error(f"Image split error: {e}")
        return [img_bytes]


def split_markdown(md_text: str, max_lines: int = 400) -> List[str]:
    """将长的markdown按 Episode 标题或者行数拆分，降低单次渲染的内存和CPU压力"""
    lines = md_text.split('\n')
    chunks = []
    current_chunk = []
    
    for line in lines:
        # 如果遇到 ## 标题，且当前已经积攒了一定行数（避免紧挨着标题切割）
        if line.startswith('## ') and len(current_chunk) > 300:
            chunks.append('\n'.join(current_chunk))
            current_chunk = [line]
            continue
            
        current_chunk.append(line)
        
        # 如果超过了最大行数限制，并在段落边界（空行）处裁切
        if len(current_chunk) >= max_lines and not line.strip():
            chunks.append('\n'.join(current_chunk))
            current_chunk = []
        # 如果一直没有空行，超过了硬性绝对限制(max_lines + 80)，则强制切断
        elif len(current_chunk) >= max_lines + 80:
            chunks.append('\n'.join(current_chunk))
            current_chunk = []

    if current_chunk:
        chunks.append('\n'.join(current_chunk))
        
    return [c for c in chunks if c.strip()]


cmd_update = on_command("更新剧情", permission=SUPERUSER, priority=5, block=True)
cmd_story = on_command("查剧情", aliases={"剧情", "查询剧情"}, priority=5, block=True)
cmd_random = on_command("随机剧情", priority=5, block=True)
cmd_list = on_command("剧情分类", aliases={"剧情列表"}, priority=5, block=True)


@cmd_update.handle()
async def handle_update_stories(bot: Bot, event: Event):
    from .services.maintenance import submit
    result = await submit(bot, event, "stories", origin="command")
    from .services.results import send_command_result
    await send_command_result(cmd_update, result, finish=False)


@cmd_list.handle()
async def _(bot: Bot, event: Event, args: Message = CommandArg()):
    query = args.extract_plain_text().strip()
    from .services.story import categories
    result = await categories(query)
    if result["status"] != "ok":
        await _result_call(result, cmd_list.finish, result["fallback_text"])
    data = result["data"]
    if not query:
        cat_names = "\n".join(f"- {item['name']} ({item['count']}篇)" for item in data["categories"])
        await cmd_list.finish(f"当前收录以下剧情分类：\n{cat_names}\n\n使用「/剧情分类 <分类名>」查看具体分类下的人物。")
    names = "、".join(data["stories"])
    msg = f"分类【{data['category']}】下的剧情有：\n{names}"
    if len(msg) > 500:
        msg = msg[:500] + "\n...（内容过多已省略。请直接使用/查剧情 <人物>）"
    await cmd_list.finish(msg)


async def predownload_images(md_text: str) -> str:
    """找出所有的图片链接并将它们下载后转为 Base64，防止 Playwright 截图时图片未加载"""
    urls_to_fetch = set()
    local_urls = set()
    
    # 匹配离线缓存的本地图片协议 local://...
    local_pattern = re.compile(r'local://([^\s"\')]+)')
    for match in local_pattern.finditer(md_text):
         local_urls.add(match.group(1))
         
    # regex for inline images (忽略 local://)
    inline_pattern = re.compile(r'!\[[^\]]*\]\((https?://[^)\s]+)[^\)]*\)')
    for match in inline_pattern.finditer(md_text):
        urls_to_fetch.add(match.group(1))
        
    # regex for html images
    img_pattern = re.compile(r'<img[^>]+src=["\'](https?://[^"\']+)["\']')
    for match in img_pattern.finditer(md_text):
        urls_to_fetch.add(match.group(1))
        
    # regex for reference links
    ref_pattern = re.compile(r'^\[[^\]]+\]:\s*(https?://[^\s]+\.(?:png|jpe?g|gif|webp)(?:\?[^\s]*)?)', re.IGNORECASE | re.MULTILINE)
    for match in ref_pattern.finditer(md_text):
        urls_to_fetch.add(match.group(1))
        
    url_to_base64 = {}
    
    # 处理本地图片
    for l_path in local_urls:
        file_path = DATA_DIR / l_path
        if file_path.exists():
            try:
                with open(file_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode('utf-8')
                    ctype = "image/png"
                    if l_path.lower().endswith("jpg") or l_path.lower().endswith("jpeg"):
                        ctype = "image/jpeg"
                    url_to_base64[f"local://{l_path}"] = f"data:{ctype};base64,{b64}"
            except Exception as e:
                logger.error(f"Failed to read local image {l_path}: {e}")

    # 处理未被拦截的网络图片
    if urls_to_fetch:
        async with httpx.AsyncClient(timeout=15.0) as client:
            async def fetch(url):
                try:
                    kwargs = {}
                    import inspect
                    sig = inspect.signature(client.get)
                    if 'follow_redirects' in sig.parameters:
                        kwargs['follow_redirects'] = True
                    elif 'allow_redirects' in sig.parameters:
                        kwargs['allow_redirects'] = True

                    resp = await client.get(url, **kwargs)
                    if resp.status_code == 200:
                        ctype = resp.headers.get("Content-Type", "image/png").split(';')[0]
                        b64 = base64.b64encode(resp.content).decode('utf-8')
                        url_to_base64[url] = f"data:{ctype};base64,{b64}"
                    else:
                        logger.warning(f"Failed to fetch image {url}: status {resp.status_code}")
                except Exception as e:
                    logger.warning(f"Failed to fetch image {url}: {e}")

            tasks = [fetch(url) for url in urls_to_fetch]
            if tasks:
                await asyncio.gather(*tasks)
                
    # Now replace URLs in text
    for url, b64_str in url_to_base64.items():
        md_text = md_text.replace(url, b64_str)

    return md_text


async def render_and_send(bot: Bot, event: Event, name: str, file_path: str, matcher):
    """Compatibility entry: use the same durable story task as other commands."""
    from .services.story import submit_selected
    result = await submit_selected(bot, event, name, file_path, origin="command")
    from .services.results import send_command_result
    await send_command_result(matcher, result, finish=False)
    return result


@cmd_story.handle()
async def _(bot: Bot, event: Event, args: Message = CommandArg()):
    name = args.extract_plain_text().strip()
    if not name:
        await _error_call(cmd_story.finish, "请输入要查询的角色名！例如：/查剧情 提亚马特")
    from .services.story import search
    result = await search(bot, event, name, origin="command")
    if result["status"] not in {"ok", "running"}:
        if result["code"] == "AMBIGUOUS_TARGET":
            names_str = "、".join(item["name"] for item in result["data"].get("candidates", []))
            await _error_call(cmd_story.finish, f"匹配到多个结果，请提供更精确的名称:\n{names_str}")
        await _result_call(result, cmd_story.finish, result["fallback_text"])
    from .services.results import send_command_result
    await send_command_result(cmd_story, result, finish=False)
    event.__dict__.setdefault("_native_capability_results", {})["story.search"] = dict(
        status=result["status"], code=result["code"], delivery="sent")

@cmd_random.handle()
async def _(bot: Bot, event: Event):
    from .services.story import random_story
    result = await random_story(bot, event, origin="command")
    if result["status"] not in {"ok", "running"}:
        await _result_call(result, cmd_random.finish, result["fallback_text"])
    from .services.results import send_command_result
    await send_command_result(cmd_random, result, finish=False)
    event.__dict__.setdefault("_native_capability_results", {})["story.random"] = dict(
        status=result["status"], code=result["code"], delivery="sent")
