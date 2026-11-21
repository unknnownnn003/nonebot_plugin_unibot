# UniBot 日服旧框 HDD 数据接入

旧框 HDD 数据使用独立 SQLite。查询类是 `OldHddHistory`，构造时传入本地数据库文件。导入器复用现有日服版本日历、B5 查询与旧框成绩映射输入。线上查分继续使用当前流程。

## 重建数据包

在本仓运行以下命令。`--output` 指向一个新的本地目录。保存的 Wiki 页面以 `tools/old_hdd_evidence.py` 中的名称放入 `--evidence`。本次使用的输入指纹可从输出的 `references_used.csv` 核对。Wiki 适配器使用当前环境已有的 Beautiful Soup。数据库构建结束后会检查外键和页完整性，再发布整个目录。

```powershell
python -X utf8 tools/build_old_hdd_history.py `
  --root 'E:\SDBT 11 in 1' `
  --output .tools/old-hdd/rebuild `
  --evidence .tools/old-hdd `
  --b5 .tools/history-b5/rebuild `
  --hash-all
```

`--hash-all` 为基础包和 OPTION 里的文件计算 SHA-256。容器 VHD 只登记大小。原始 XML 按 HDD 相对路径写入 `raw_xml`。图像和 C2S 仅记录指纹及路径。保留原 HDD 时，应用可以直接读取去重后的曲绘来源。

## 离线查询

```python
from nonebot_plugin_unibot.services.old_hdd_history import OldHddHistory

history = OldHddHistory(r"D:\Files\nonebot\nonebot_plugin_unibot\.tools\old-hdd\hdd-authoritative\catalog.sqlite")
songs = history.songs("PARADISE LOST")
chart = history.chart("PARADISE LOST", 3, "MAS")
we = history.worlds_end("PARADISE LOST")
constants = history.constants("PARADISE LOST")
```

`charts`、`songs` 和 `worlds_end` 默认查询有效记录。传入 `active_only=False` 可查看未知、停配、禁用及已知尚未推出的记录。`chart_history` 与 `changes` 使用证据关联后的 `chart_identity`。`song_history` 以原始 ID 查询，结果每行保留自己的 `identity`。`chart` 遇到复用 ID 或多候选谱面会返回 `identity_conflict`。`not_observed`、`unknown_before_observation` 与 `absent_after_observation` 描述原始资料覆盖情况。只有明确日期或 XML 状态才会产生 `not_released`、`removed` 或 `disabled`。

定数使用百分位整数存储。`hdd_hundredths` 保存直接提取值，`selected_hundredths` 保存可用于该版本的 HDD 值，`constant_status` 表明是直接提取还是过渡推定。B5 和 Wiki 的不同数值记入对照表。`constants` 批量返回 Decimal，默认排除过渡推定。显式 `include_inferred=True` 才包含推定。WE 由独立接口返回，字段包括属性、原始星级编码、星数与谱面指纹。

`jackets` 返回候选曲绘以及路径和 SHA-256。`jacket_path` 将记录解析到显式提供的 HDD 根目录。`score_mappings` 带出既有 PARADISE LOST 国服映射的候选。接入 Rating 时，应使用现有 `legacy_rating.chart_identity` 复核当前 CN 谱面指纹。其他版本的历史槽位与现行 CN 成绩需要独立证据，当前包保留 B5 revision 关联供核查。

生产接入前需针对目标版本审阅曲目有效范围、同名和复用 ID、推定定数与内容改谱，再决定 Rating 可用集合。验收应独立测试版本窗口、覆盖率门槛及 CN 谱面身份。
