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


def download_image_sync(url: str, rel_path: str):
    """同步下载图片到本地路径供未来渲染读取"""
    local_path = DATA_DIR / rel_path
    if local_path.exists():
        return
    local_path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            with open(local_path, "wb") as f:
                f.write(response.read())
    except Exception as e:
        logger.error(f"Failed to download image {url} during scraping: {e}")


def rewrite_links(md_text: str, md_path: str) -> str:
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
                
            download_image_sync(target_url, img_rel)
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
async def _(bot: Bot, event: Event):
    await cmd_update.send("开始拉取网站目录并排查本地缺失剧情内容，请稍候...")
    
    def fetch_sidebar():
        req = urllib.request.Request(f"{BASE_URL}/_sidebar.md", headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.read().decode('utf-8')
            
    try:
        sidebar_text = await asyncio.to_thread(fetch_sidebar)
    except Exception as e:
        await cmd_update.finish(f"获取目录失败: {e}")
        return
        
    lines = sidebar_text.split('\n')
    categories = {}
    current_category = "未分类"
    download_queue = {}
    
    for line in lines:
        line = line.rstrip()
        if not line:
            continue
            
        # 粗略分类推断（通常以无链接的列表项表示）
        cat_match = re.search(r'^\s*\*\s+([^\[\]]+)$', line)
        if cat_match:
            current_category = cat_match.group(1).strip()
            if current_category not in categories:
                categories[current_category] = []
            continue
            
        link_match = re.search(r'\[([^\]]+)\]\((/[^\)]+)\)', line)
        if link_match:
            name = link_match.group(1).strip()
            raw_url = link_match.group(2).strip()
            file_path = raw_url.split('?')[0].split('#')[0]
            if file_path.endswith('.md') and 'README' not in file_path:
                if current_category not in categories:
                    categories[current_category] = []
                categories[current_category].append({"name": name, "path": file_path})
                download_queue[file_path] = name
                
    if not DATA_DIR.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        
    index_data = {"categories": categories, "mapping": {}}
    for cat, items in categories.items():
        for item in items:
            index_data["mapping"][item["name"]] = item["path"]
            
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index_data, f, ensure_ascii=False, indent=2)
        
    # 排查哪些本地没有
    missing_files = []
    for file_path in download_queue.keys():
        local_path = DATA_DIR / file_path.lstrip('/')
        if not local_path.exists():
            missing_files.append(file_path)
            
    if not missing_files:
        await cmd_update.finish("索引已更新，所有剧情均已保存在本地，无需爬取新内容。")

    await cmd_update.send(f"共有 {len(missing_files)} 篇新剧情需要下载，正在后台爬取该文本并转换图片链接为绝对路径...\n完成后会进行通知。")
    
    def download_and_rewrite(f_path):
        target_url = f"{BASE_URL}{urllib.parse.quote(f_path)}"
        req = urllib.request.Request(target_url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                md_text = response.read().decode('utf-8')
                return rewrite_links(md_text, f_path)
        except Exception as e:
            logger.error(f"Download failed for {f_path}: {e}")
            return None

    success_count = 0
    fail_count = 0
    
    for file_path in missing_files:
        local_path = (DATA_DIR / file_path.lstrip('/')).resolve()
        if not str(local_path).startswith(str(DATA_DIR.resolve())):
            logger.warning(f"跳过包含越权路径的文件: {file_path}")
            fail_count += 1
            continue

        local_dir = local_path.parent
        if not local_dir.exists():
            local_dir.mkdir(parents=True, exist_ok=True)
            
        md_content = await asyncio.to_thread(download_and_rewrite, file_path)
        if md_content:
            with open(local_path, "w", encoding="utf-8") as f:
                f.write(md_content)
            success_count += 1
        else:
            fail_count += 1
            
        await asyncio.sleep(0.1)
        
    await cmd_update.send(f"剧情爬取完毕！\n成功: {success_count}\n失败: {fail_count}")


@cmd_list.handle()
async def _(bot: Bot, event: Event, args: Message = CommandArg()):
    data = load_index()
    if not data.get("categories"):
        await cmd_list.finish("尚未建立剧情索引，请联系管理员发送 /更新剧情。")
        
    query = args.extract_plain_text().strip()
    categories = data["categories"]
    
    if not query:
        cat_names = "\n".join(f"- {c} ({len(items)}篇)" for c, items in categories.items())
        await cmd_list.finish(f"当前收录以下剧情分类：\n{cat_names}\n\n使用「/剧情分类 <分类名>」查看具体分类下的人物。")
        
    for cat, items in categories.items():
        if query in cat:
            names = [item["name"] for item in items]
            msg = f"分类【{cat}】下的剧情有：\n" + "、".join(names)
            
            # 若字符太多则切断避免风控
            if len(msg) > 500:
                msg = msg[:500] + "\n...（内容过多已省略。请直接使用/查剧情 <人物>）"
            await cmd_list.finish(msg)
            
    await cmd_list.finish(f"未找到相关分类，请检查名称。")


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
    local_path = DATA_DIR / file_path.lstrip('/')
    if not local_path.exists():
        await matcher.finish(f"此剧情虽然有索引，但本地文件未找到！请联系超级用户重新拉取文件。")
        
    with open(local_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    # 预先定义护眼背景的CSS
    base_style = """<style>
.markdown-body {
    background-color: #fdf6e3 !important;
    color: #333333 !important;
}
.markdown-body table tr {
    background-color: #fdf6e3 !important;
}
.markdown-body table tr:nth-child(2n) {
    background-color: #f2e9ce !important;
}
</style>
"""

    if HTMLRENDER_AVAILABLE:
        await matcher.send("加载中...")
        try:
            # 预下载所有图片以解决渲染时头图不显示的问题
            md_text = await predownload_images(md_text)

            # 对长文本进行分块，避免一次性渲染对低配服务器施加巨大压力
            chunks = split_markdown(md_text, max_lines=400)
            
            nodes = []
            source_url = f"{BASE_URL}/#{file_path}"
            nodes.append(
                MessageSegment.node_custom(
                    user_id=int(bot.self_id),
                    nickname="uni",
                    content=Message(f"当前角色：{name}\n内容来源：{source_url}")
                )
            )

            # 遍历渲染每一块
            for i, chunk in enumerate(chunks):
                # 只有第一段加上角色大标题
                header = f"\n# {name}\n\n" if i == 0 else ""
                chunk_md = base_style + header + chunk
                
                # 开始渲染当前分块，适度降低DPI和使用JPEG格式，减少API发送超时风险
                img_bytes = await md_to_pic(chunk_md, width=800, type="jpeg", quality=90, device_scale_factor=1.5)
                
                # 保险起见（某段没有空行或者超长情况），依旧使用 PIL 切分以适应QQ发送限制
                pieces = split_image(img_bytes, max_height=6000)
                for piece in pieces:
                    nodes.append(
                        MessageSegment.node_custom(
                            user_id=int(bot.self_id),
                            nickname="uni",
                            content=Message(MessageSegment.image(piece))
                        )
                    )

            is_group = getattr(event, "message_type", None) == "group"
            try:
                if is_group:
                    await bot.call_api("send_group_forward_msg", group_id=event.group_id, messages=nodes)
                else:
                    await bot.call_api("send_private_forward_msg", user_id=event.user_id, messages=nodes)
            except Exception as e:
                logger.error(f"合并转发发送失败: {e}")
                await matcher.send("由于合并转发限制或失败，将退回直接发送前两张图片：")
                # nodes[1] 开始是图片（nodes[0]是文字来源）
                img_sent = 0
                for node in nodes[1:]:
                    if img_sent >= 2: break
                    # 这里尝试提取图片内容并发送
                    try:
                        img_node_content = node["data"]["content"]
                        await matcher.send(img_node_content)
                        img_sent += 1
                    except:
                        pass

        except Exception as e:
            import traceback; logger.error(f"Render failed: {traceback.format_exc()}")
            await matcher.send(f"渲染图片失败：{e}\n下面为您提供部分纯文本：\n{md_text[:500]}...")
    else:
        # 如果未安装 HtmlRender 发送前 500 字提示
        msg = f"未安装 HtmlRender 插件以渲染 Markdown 文本，只能为您发送文本片段。\n\n# {name}\n\n" + md_text[:500]
        if len(md_text) > 500:
            msg += "\n\n...[由于内容过长已省略。请联系 Bot 管理员安装 Playwright 及 HTMLRender 插件以获取完整精美带图渲染版本]"
        await matcher.send(msg)


@cmd_story.handle()
async def _(bot: Bot, event: Event, args: Message = CommandArg()):
    data = load_index()
    if not data.get("mapping"):
        await cmd_story.finish("并未建立剧情索引或空列表，请联系管理员发送 /更新剧情。")
        
    name = args.extract_plain_text().strip()
    if not name:
        await cmd_story.finish("请输入要查询的角色名！例如：/查剧情 提亚马特")

    matches = []
    # 模糊搜索
    for k, v in data["mapping"].items():
        if name.lower() in k.lower():
            matches.append((k, v))

    if not matches:
        await cmd_story.finish(f"本地未找到包含「{name}」的剧情索引记录，请检查名称。")
        
    target_name = ""
    target_path = ""
    if len(matches) > 1:
        # 尝试完全匹配
        exact = [m for m in matches if m[0].lower() == name.lower()]
        if exact:
            target_name, target_path = exact[0]
        else:
            names_str = "、".join([m[0] for m in matches])
            if len(names_str) > 200:
                names_str = names_str[:200] + "..."
            await cmd_story.finish(f"匹配到多个结果，请提供更精确的名称:\n{names_str}")
    else:
        target_name, target_path = matches[0]

    await render_and_send(bot, event, target_name, target_path, cmd_story)

@cmd_random.handle()
async def _(bot: Bot, event: Event):
    data = load_index()
    mapping = data.get("mapping", {})
    if not mapping:
        await cmd_random.finish("并未建立剧情索引或空列表，请联系管理员发送 /更新剧情。")

    target_name = random.choice(list(mapping.keys()))
    target_path = mapping[target_name]
    await render_and_send(bot, event, target_name, target_path, cmd_random)
