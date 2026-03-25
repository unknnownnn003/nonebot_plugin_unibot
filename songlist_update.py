import json
import httpx
from pathlib import Path
from nonebot import on_command
from nonebot.permission import SUPERUSER
from nonebot.log import logger

# 请求的基本URL
BASE_URL = "https://maimai.lxns.net"
# 目标API路径
API_PATH = "/api/v0/chunithm/song/list"

# 定义插件本地数据存储目录
DATA_DIR = Path(__file__).parent / "data"
# 定义 songlist.json 保存路径
SONGLIST_FILE = DATA_DIR / "songlist.json"

# 注册命令 `/更新songlist`
# permission=SUPERUSER 确保只有超级用户可以触发此命令
# priority=10 设定响应优先级
# block=True 阻止事件向低优先级继续传递
update_songlist = on_command("更新songlist", permission=SUPERUSER, priority=10, block=True)

@update_songlist.handle()
async def handle_update_songlist():
    """
    更新 songlist 列表的主处理函数。
    向 API 请求最新的 songlist，如果成功则覆盖写入本地 data/songlist.json 文件中。
    """
    # 发送提醒消息，表示已开始执行
    await update_songlist.send("开始从外部 API 获取 songlist，请稍候...")
    
    # 确保 data 目录存在，如果不存在则自动创建
    if not DATA_DIR.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        logger.info(f"已创建数据目录: {DATA_DIR}")

    # 完整的请求URL
    target_url = BASE_URL + API_PATH

    try:
        # 使用 httpx 进行异步 HTTP 请求
        async with httpx.AsyncClient() as client:
            response = await client.get(target_url, timeout=30.0)
            
            # 检查响应状态码，如果不为 20x 则抛出 HTTPError 异常
            response.raise_for_status()
            
            # 解析获取到的 JSON 数据
            songlist_data = response.json()
            
        # 以 UTF-8 编码将 JSON 数据覆写保存到指定文件路径中
        with open(SONGLIST_FILE, "w", encoding="utf-8") as f:
            json.dump(songlist_data, f, ensure_ascii=False, indent=4)
            
        # 记录日志，并回复用户更新成功
        logger.success("songlist.json 成功更新")
        await update_songlist.send("更新成功！songlist.json 已成功获取并覆盖。")
        
    except httpx.HTTPError as http_err:
        # 捕获 HTTP 网络请求相关的异常
        error_msg = f"更新失败，网络请求异常：{http_err}"
        logger.error(error_msg)
        await update_songlist.send(error_msg)
        
    except Exception as e:
        # 捕获其它的异常，例如 JSON 格式错误或文件写入失败等
        error_msg = f"更新失败，写入或解析发生了未知错误：{e}"
        logger.error(error_msg)
        await update_songlist.send(error_msg)
