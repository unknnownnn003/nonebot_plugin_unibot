import os
import json
import csv
import asyncio
from typing import Annotated
from datetime import timedelta
from nonebot import on_command
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import MessageEvent, Bot
from nonebot.log import logger
from nonebot.typing import T_State
from nonebot.exception import FinishedException
from nonebot.params import Arg

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PLUGIN_DIR, "data")
SCORE_DIR = os.path.join(DATA_DIR, "score")

if not os.path.exists(SCORE_DIR):
    os.makedirs(SCORE_DIR)

update_score = on_command("update", priority=5, block=True, expire_time=timedelta(seconds=300))

def _parse_csv_to_json(csv_path: str, json_path: str) -> bool:
    """
    读取并解析 cvs 文件，然后将其转存为指定路径下的 json 文件
    如果csv中同一id同一level_index的项目出现多次，请保留分数最高（其次最新）的一项
    """
    try:
        data_dict = {}
        with open(csv_path, mode="r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                song_id = row.get("id")
                level_index = row.get("level_index")
                
                # 如果缺少主键信息，则视为异常数据跳过
                if song_id is None or level_index is None:
                    continue
                
                key = f"{song_id}_{level_index}"
                
                try:
                    score = int(row.get("score", 0))
                except ValueError:
                    score = -1
                
                if key not in data_dict:
                    data_dict[key] = row
                else:
                    existing_row = data_dict[key]
                    try:
                        existing_score = int(existing_row.get("score", 0))
                    except ValueError:
                        existing_score = -1
                        
                    if score > existing_score:
                        data_dict[key] = row
                    elif score == existing_score:
                        # 分数相同时，尝试比较时间（以靠后的或时间字符串更大的为准）
                        time_field = None
                        for tf in ["play_time", "updated_at", "time", "date"]:
                            if tf in row:
                                time_field = tf
                                break
                                
                        if time_field and row.get(time_field) and existing_row.get(time_field):
                            if str(row.get(time_field)) > str(existing_row.get(time_field)):
                                data_dict[key] = row
                        else:
                            # 默认如果没找到时间字段，后来者居上（假设 CSV 靠后的较新）
                            data_dict[key] = row

        data_list = list(data_dict.values())

        with open(json_path, mode="w", encoding="utf-8") as f:
            json.dump(data_list, f, ensure_ascii=False, indent=4)
        return True
    except Exception as e:
        logger.error(f"解析 CSV 并保存为 JSON 时出错: {e}")
        return False

@update_score.handle()
async def _(bot: Bot, event: MessageEvent):
    """
    处理 /update 命令：提示上传 csv
    """
    await update_score.send("请上传 lxns 查分器导出的 chunithm-scores.csv 文件")

# 使用 expire_time 让等待状态 5 分钟后超时
@update_score.got("file_msg", prompt="等待上传中...")
async def get_uploaded_file(bot: Bot, event: MessageEvent, file_msg: Event = Arg("file_msg")):
    try:
        user_qq = str(event.get_user_id())
        
        # 提取当前消息的纯文本或特殊段
        msg = event.get_message()
        file_url = ""
        local_file = ""
        base64_data = ""
        
        # 在 OneBot v11 中，通过 file 消息段获取文件，这里也兼容提取 URL 或者通过 API 操作
        # 但通常直接给 bot.get_file 或解析 message中的 url
        # 详细判断取决于具体 adapter 和 client(Napcat, go-cqhttp)
        for seg in msg:
            if seg.type == "file":
                file_url = seg.data.get("url", "")
                if not file_url and "file_id" in seg.data:
                    try:
                        file_info = await bot.get_file(file_id=seg.data["file_id"])
                        
                        # 尝试解包 Napcat 嵌套的 data 字段
                        target_info = file_info.get("data", file_info) if isinstance(file_info, dict) else file_info
                        
                        file_url = target_info.get("url", "")
                        local_file = target_info.get("file", "")
                        base64_data = target_info.get("base64", "")
                    except Exception as e:
                        logger.error(f"获取文件信息失败: {e}")
                break

        # fallback for napcat sometimes
        if not file_url and not local_file and not base64_data and msg:
           for seg in msg:
                if "url" in seg.data:
                    file_url = seg.data["url"]
                    break

        temp_csv_path = os.path.join(SCORE_DIR, f"{user_qq}_temp.csv")
        target_json_path = os.path.join(SCORE_DIR, f"{user_qq}.json")
        
        logger.info(f"开始处理用户 {user_qq} 提供的成绩文件...")

        if base64_data:
            import base64
            with open(temp_csv_path, "wb") as f:
                f.write(base64.b64decode(base64_data))
        elif local_file and os.path.exists(local_file):
            import shutil
            shutil.copy2(local_file, temp_csv_path)
        elif file_url:
            if isinstance(file_url, str):
                if file_url.startswith("//"):
                    file_url = f"http:{file_url}"
                elif file_url.startswith("/") and not os.path.exists(file_url):
                    file_url = f"http://{file_url}"

            if not isinstance(file_url, str) or not (file_url.startswith("http://") or file_url.startswith("https://")):
                await update_score.finish("接收到的不是有效的文件链接，更新操作已取消。")
                
            import httpx
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(file_url, timeout=30.0)
                    if resp.status_code == 200:
                        with open(temp_csv_path, "wb") as f:
                            f.write(resp.content)
                    else:
                        await update_score.finish("下载文件失败，请稍后重试！")
            except Exception as e:
                logger.error(f"下载文件发生错误: {e}")
                await update_score.finish("下载文件时发生错误，请检查网络连接或稍后重试。")
        else:
            # 兼容如果由于某些原因 file_url 是本地路径字符串但没被 local_file 捕获
            if isinstance(file_url, str) and os.path.exists(file_url):
                import shutil
                shutil.copy2(file_url, temp_csv_path)
            else:
                await update_score.finish("接收到的不是有效的文件，更新操作已取消。")
        
        # 解析与转存
        success = _parse_csv_to_json(temp_csv_path, target_json_path)
        
        # 无论成功与否，清理临时下载的 CSV 文件
        if os.path.exists(temp_csv_path):
            os.remove(temp_csv_path)
            
        if success:
            await update_score.finish("保存成功")
        else:
            await update_score.finish("CSV 解析失败，请确认导出的格式是否正常并重试！")
            
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"处理上传成绩文件错误: {e}")
        await update_score.finish(f"处理文件时发生意外错误。")
