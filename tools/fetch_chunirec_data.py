"""
本地 chunirec 数据拉取工具

在本地 PC 上运行此脚本，从 chunirec API 拉取日服曲库数据。
然后将生成的 JSON 文件通过 QQ 发送给 bot，使用 /上传chunirec 命令完成同步。

用法：
    python fetch_chunirec_data.py

环境变量（可选，不设置则会交互式提示）：
    CHUNIREC_TOKEN       - chunirec 访问令牌
    NAOMINET_TOKEN       - naominet 访问令牌（可选，用于补全数字 ID）
    NAOMINET_SESSION     - naominet SESSION cookie（可选）

输出：
    chunirec_data_YYYYMMDD_HHMMSS.json  — 上传给 bot 的数据文件
"""

import json
import os
import sys
import ssl
from datetime import datetime
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode

CHUNIREC_SHOWALL_URL = "https://api.chunirec.net/2.0/music/showall.json"
CHUNIREC_REGION = "jp2"
NAOMINET_DB_VERSION_URL = "https://portal.naominet.live/api/static/dbVersion"
NAOMINET_MUSIC_URL = "https://portal.naominet.live/api/game/chuni/v2/data/music"


def normalize_title_key(value: Any) -> str:
    return str(value or "").strip().casefold()


def pick_first_value(data: dict, keys: list) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return None


def extract_naominet_rows(payload: Any) -> list:
    if isinstance(payload, dict) and isinstance(payload.get("data"), (dict, list)):
        payload = payload["data"]
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("content", "items", "records", "list", "songs", "result"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def http_get_json(url: str, headers: dict | None = None, timeout: int = 30) -> Any:
    req = Request(url)
    if headers:
        for key, value in headers.items():
            req.add_header(key, value)
    ctx = ssl.create_default_context()
    with urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_chunirec(token: str) -> list:
    params = {"region": CHUNIREC_REGION, "token": token}
    url = f"{CHUNIREC_SHOWALL_URL}?{urlencode(params)}"
    print(f"正在请求 chunirec music/showall ...")
    payload = http_get_json(url)
    if not isinstance(payload, list):
        raise ValueError(f"chunirec 返回格式异常，期望 list，实际为 {type(payload).__name__}")
    print(f"  获取到 {len(payload)} 首歌曲数据")
    return payload


def fetch_naominet_id_map(token: str, session: str | None) -> tuple:
    auth_value = token if token.lower().startswith("bearer ") else f"Bearer {token}"
    headers = {
        "Authorization": auth_value,
        "Referer": "https://portal.naominet.live/",
        "Accept": "application/json",
    }
    if session:
        headers["Cookie"] = f"SESSION={session}"

    print("正在探测 naominet ...")
    http_get_json(NAOMINET_DB_VERSION_URL, headers=headers)

    print("正在请求 naominet data/music ...")
    payload = http_get_json(NAOMINET_MUSIC_URL, headers=headers)
    if isinstance(payload, dict):
        status = payload.get("status")
        code = status.get("code") if isinstance(status, dict) else None
        if code == 94011:
            raise PermissionError("naominet 返回 Unauthorized，需要可访问该接口的登录令牌")

    rows = extract_naominet_rows(payload)
    if not rows:
        raise ValueError("naominet data/music 未返回可用歌曲列表")

    result: list[list[str]] = []
    for row in rows:
        numeric_id = pick_first_value(row, ["musicId", "music_id", "songId", "song_id", "id"])
        title = pick_first_value(row, ["title", "name", "songName", "song_name"])
        artist = pick_first_value(row, ["artist", "artistName", "artist_name"])
        if numeric_id is None or title is None:
            continue
        numeric_id_str = str(numeric_id).strip()
        if not numeric_id_str.isdigit():
            continue
        result.append([normalize_title_key(title), normalize_title_key(artist), numeric_id_str])
        result.append([normalize_title_key(title), "", numeric_id_str])

    status_text = f"naominet 匹配表：{len(result)} 条键，读取 {len(rows)} 首"
    if not result:
        status_text = "naominet 未返回可用歌曲数字 ID"
    print(f"  {status_text}")
    return result, status_text


def prompt(prompt_text: str, default: str = "") -> str:
    if default:
        value = input(f"{prompt_text} [{default}]: ").strip()
        return value or default
    while True:
        value = input(f"{prompt_text}: ").strip()
        if value:
            return value


def main():
    if "--jackets" in sys.argv:
        print("Note: chunirec jacket download is disabled; jackets are fetched from Lxns only.")

    print("=" * 50)
    print("chunirec 数据本地拉取工具")
    print("=" * 50)
    print()

    # 读取 token
    chunirec_token = os.environ.get("CHUNIREC_TOKEN", "")
    naominet_token = os.environ.get("NAOMINET_TOKEN", "")
    naominet_session = os.environ.get("NAOMINET_SESSION", "")

    if not chunirec_token:
        chunirec_token = prompt("请输入 chunirec Token")

    if not naominet_token:
        print()
        print("naominet 用于补全与未来国服一致的数字 ID（可选）")
        naominet_input = input("请输入 naominet Token（直接回车跳过）: ").strip()
        if naominet_input:
            naominet_token = naominet_input
            naominet_session = input("请输入 naominet SESSION cookie（直接回车跳过）: ").strip() or ""

    print()

    # 拉取 chunirec
    try:
        chunirec_payload = fetch_chunirec(chunirec_token)
    except (URLError, HTTPError, OSError, ValueError) as e:
        print(f"错误：无法获取 chunirec 数据 — {e}", file=sys.stderr)
        sys.exit(1)

    # 拉取 naominet（可选）
    naominet_id_map: list = []
    naominet_status = "未配置 naominet_token，跳过数字 ID 补全"
    if naominet_token:
        try:
            naominet_id_map, naominet_status = fetch_naominet_id_map(naominet_token, naominet_session or None)
        except PermissionError as e:
            naominet_status = str(e)
            print(f"  警告：{naominet_status}")
        except Exception as e:
            naominet_status = f"naominet 数字 ID 补全失败: {type(e).__name__}: {e}"
            print(f"  警告：{naominet_status}")

    # 曲绘不再从 chunirec 抓取；运行端统一从落雪获取，落雪缺失则留空。

    # 保存
    output = {
        "source": "chunirec_fetch_tool",
        "version": 2,
        "fetched_at": datetime.now().isoformat(),
        "naominet_status": naominet_status,
        "chunirec_payload": chunirec_payload,
        "naominet_id_map": naominet_id_map,
    }

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"chunirec_data_{timestamp}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False)
    print()
    print(f"数据已保存到: {filename}")
    print()
    print("下一步操作：")
    print(f"  1. 将 {filename} 通过 QQ 发送给 bot")
    print(f"  2. 在 bot 上执行 /上传chunirec 命令")


if __name__ == "__main__":
    main()
