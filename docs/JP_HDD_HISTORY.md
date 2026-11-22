# 日服新旧框统一 HDD 历史库

离线构建把十代 SDHD 记录追加到旧框 v2 生命周期库的副本。v3 共用歌曲、谱面、修订、观察、批次、日期窗口和资源索引。跨代证据充分的谱面沿用原身份，可以一次查到旧框定数和新框修订。身份不明的谱面保留独立记录及候选链接。

## 输入与覆盖

| 版本 | 本地基础镜像 | 运营首日 | 本地选件来源 |
| --- | --- | --- | --- |
| NEW | 2.00.01 | 2021-11-04 | 14 个已解包 VHD |
| NEW PLUS | 2.05.00 | 2022-04-14 | 14 个已解包 VHD |
| SUN | 2.10.01 | 2022-10-13 | 16 个已解包 VHD |
| SUN PLUS | 2.16.00 | 2023-05-11 | 13 个已解包 VHD，数据修订为 2.15 系列 |
| LUMINOUS | 2.20.00 | 2023-12-14 | 加密 ZIP 内 19 个包 |
| LUMINOUS PLUS | 2.25.01 | 2024-06-20 | ZIP 内 17 个包 |
| VERSE | 2.30.00 | 2024-12-12 | ZIP 内 20 个包 |
| X-VERSE | 2.40.00 | 2025-07-16 | ZIP 内 16 个包 |
| X-VERSE-X | 2.45.00 | 2025-12-11 | ZIP 内 20 个包 |
| Mate | 2.50.00 | 2026-07-02 | ZIP 内 13 个包，A101 有 2026-10-08 的曲目 |

版本日历沿用项目里已核对的 SEGA 日服日期。历史版截点是下版开服的前一天。Mate 的证据截止到 A101 对应的 2026-10-08。另有后续运营公告，本地盘无法验证相应曲库。 [NEW 开服](https://info-chunithm.sega.jp/1151/) · [X-VERSE-X 开服](https://info-chunithm.sega.jp/10941/) · [Mate 开服](https://info-chunithm.sega.jp/12155/) · [Mate A101 对应公告](https://info-chunithm.sega.jp/12754/) · [Mate 2.51 公告](https://info-chunithm.sega.jp/13533/)

`E:/SDHD` 是本轮主输入。排除各版目录下的自制包。X-VERSE-X 的 `SDHD_2.47_Update.zip` 含程序、音源容器、视频、界面及等级筛选表，目录清单没有 Music.xml 或 C2S。它未参与歌曲覆盖。`D:/Files/SDHD - CHUNITHM Mate/option` 中的 A001 ZIP 与主输入共享 1,602 个文件名，其中 151 个文件的 ZIP CRC 或大小不同。两份同编号材料缺可确认的运营先后，库使用主输入的整套选件，另一份保留作对照。

## 构建和验证

```powershell
$research = '.tools/history-rebuild-final'
$catalog = Join-Path $research ('releases/' + (Get-Content "$research/CURRENT" -Raw).Trim() + '/catalog.sqlite')
python -X utf8 tools/build_jp_hdd_history.py `
  --old .tools/old-hdd/lifecycle-dates-final/catalog.sqlite `
  --root 'E:\SDHD' --output .tools/jp-hdd/complete `
  --date-catalog $catalog `
  --availability .tools/old-hdd/wiki-deleted.html `
  --ultima-evidence .tools/history-b5/results/ultima_release_audit.csv `
  --through 2026-10-08 --through-url https://info-chunithm.sega.jp/12754/
python -X utf8 tools/verify_jp_hdd_history.py `
  .tools/jp-hdd/complete/catalog.sqlite `
  .tools/old-hdd/lifecycle-dates-final/catalog.sqlite 'E:\SDHD'
```

目标目录须不存在且位于 HDD 树外。源库按文件复制，构建通过 SQLite 完整性和外键检查之后发布。源库、HDD 和公共材料均只读。复验可用相同输入生成第二份数据库，再给验证器传 `--repeat 第二份数据库路径`。构建报告记录数据库和旧框源库的 SHA-256。数据库和证据留在忽略的本地目录。

构建及抽验 LUMINOUS 的加密成员前，设置 `UNIBOT_HDD_ZIP_PASSWORD`。构建器也支持 `--password-file`。缺失或不接受密码的包仍登记编号、路径和文件数。发布前应检查 `report.json`。日期证据参数可省略，对应日期继续标为未知。

## 物证和历史查询

- 基础 `data/A000` 和各更新的 `data.conf` 分开登记。增量按数据修订和选件编号覆盖同路径文件，保留配置包、资源包和音乐包。VHD 文件名的构建时间记录为本地数据下界。部分 SUN PLUS 选件早于手头的基础盘。
- Music.xml 分开记录歌曲禁用、初始锁定、选件开放标签、谱面开关及 ULTIMA 总开关。第 4 槽识别 ULTIMA，第 5 槽识别 WE。普通定数以 XML 的整数和百分位原值为准。NEW 起显示等级的加号门槛为 .50。 [NEW 等级变更说明](https://wikiwiki.jp/chunithmwiki/CHUNITHM%20NEW%20%E3%83%AC%E3%83%99%E3%83%AB%E5%A4%89%E6%9B%B4%E4%B8%80%E8%A6%A7)
- 新框实际采到的谱面文件仍是文本 C2S。记录原始文件和标准化记谱的 SHA-256，并提取 CREATOR。标准化指纹忽略已知标签与统计行，保留 BPM 和记谱行。它用于对比修订，未实现游戏判定模拟或音符重放。
- 跨框合并歌曲时同时核对 ID、标题、作者及 cue、曲绘或谱面旁证。跨世代和跨 ID 的谱面合并要求记谱一致。候选链接保留冲突原因。新框同一身份同槽后续更改形成修订。PARADISE 与 PARADISE LOST 同身份、同有效定数的可证继承条目在 v3 以 `hdd_inherited` 标注，可进入批量查询。
- `releaseDate` 从本地 LUMINOUS PLUS 基础盘开始出现，既含历史回填也含预载。运用观察版本、日期范围和可审计外部证据约束。普通曲日期不能直接用于后加 ULTIMA。未解禁、活动窗口和允许通过指定条件游玩保持独立。没有服务器状态时，时间范围和候选不会转换为精确配信日期。
- 游戏内公告和 Banner 可以追溯到事件或活动引用及 DDS 路径。曲绘按内容哈希去重，从版本来源查询原件。官方 ULTIMA 告知图以 URL 和既有证据哈希索引。不复制图片、音源或游戏程序。
- `checkpoint` 重建手头的本地版本覆盖。`catalog_on` 给出当日开放证据及候选修订，二者在活动控制、预载、缺包时可能不同。检验器独立从保留的 Music.xml 重放截点覆盖，检查定数、WE 星级及实际谱面抽样。新框的历史服务器状态仍缺完整独立基线。

```python
from nonebot_plugin_unibot.services.old_hdd_history import OldHddHistory

history = OldHddHistory(".tools/jp-hdd/complete/catalog.sqlite")
song = history.song_lifecycle("0:3:1")
master = history.lifecycle("0:3:1:3:MAS")
date_evidence = history.first_release("0:3:1:3:MAS")
updates = history.batches_on("2022-01-20")
checkpoint = history.checkpoint("MATE", active_only=False)
today = history.catalog_on("2026-10-08", include_possible=True)
constants = history.constants_on("2026-10-08")
```

旧接口保留。可用 `title_identities` 解析版本中保留的精确歌名，`identities` 按实际出现过的数字 ID 查找，`first_release` 查询首配证据，`lifecycle` 返回修订、变更与关系。ZIP 曲绘可将 `jackets` 返回的 `path` 和 `sha256` 传给 `resource_bytes` 读取校验。 `cn_mapping_candidates` 沿统一谱面身份回溯旧框已有的 CN 映射证据，返回来源版本、状态和原指纹。现有 CN 成绩接口继续使用原数据和验证过的映射。

全库保留研究和溯源所需 XML 与索引。后续若需部署，仅在能逐项校验日期、定数、图片索引及身份关系的条件下再裁剪运行库。

新框日期证据补全与字段实证见 [官方公告与日期证据](SDHD_OFFICIAL_DATES.md)。
