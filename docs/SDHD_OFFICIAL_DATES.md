# 日服 SDHD 官方公告与日期证据

本次重建使用 v3 的相同 HDD、旧框源库和现有日期证据，再合并已核读的官方公告。发布日、有效日、适用路线、具体曲目和可见 HDD 修订分别记录。普通游玩、地图、课题或课程、序列号、限时及通常开放按公告的适用范围收录。

## 官方来源和取证范围

- 从 SEGA 新闻列表归档 346 篇覆盖期内文章，先按章节和更新类型筛选，再核对正式曲目卡、公告图和事件正文。文章归档含通知、活动及开发者文章，篇数不等于更新批次。最终写入证据清单的官方网页与 X 帖通过 URL 和 SHA-256 定位。 [SEGA 日服新闻归档](https://info-chunithm.sega.jp/)
- X 主站检索无法完整回看账号时间线。公开历史索引提供 52 个覆盖期内的官方帖号候选。通过 X 官方 oEmbed 核对账号、原帖号和可见文本，保留 29 个更新相关帖。图片从少量经账号、帖号核对的媒体索引定位到 X 原始 CDN，再核读标题卡、日期和难度。X 嵌入中省略的内容按仍可见的正文和官方站交叉核实。 [官方账号](https://x.com/chunithm) · [X oEmbed 文档](https://developer.x.com/en/docs/twitter-for-websites/oembed-api)
- 同日官网与 X 或同一更新的多条 X 保留各自来源和关联。状态和修订只按身份、日期及类型生效一次。只核实了日期的帖仍可入索引，不对无法确认的谱面增写事件。
- 告知图不提交。清单只存核实过的曲号、难度、栏目定位、网址和原图哈希。原始 HTML、oEmbed 响应、少量图像以及被排除的候选保留在本地 `.tools/jp-hdd/official-dates/`。

## 物证字段和交叉样本

从 `E:/SDHD` 实读 15 个 Music.xml 样本并复验档案 SHA-256，涵盖解包目录和 ZIP 选件。对现有索引的 2,203 个新框 Event.xml 检查了日期、开放和时段字段。另从 HDD 打开此前未入索引的 `TimeTable.xml`。样本 `timeTable00000002` 是平日 09:00–17:00 的时段条件。Event.xml 的 `timeTableName` 是引用，`alwaysOpen` 是布尔开关。抽查范围内没有可直接还原全部日服开服日期的 Event.xml 日期表。

| 证据 | 能确定的语义 |
| --- | --- |
| ENDYMION 普通曲 2184，NEW PLUS A000。后续 `releaseDate=20220414` | 2022-04-14 是课程先行机会，通常开放是 2022-06-09。同曲 WE 8255 是独立身份。 |
| Halcyon ULTIMA，LUMINOUS A163，`netOpenName=v2_20 00_0` | 2024-04-25 经地图游玩，5/9 才进入 NET 相关展示。后续普通曲 `releaseDate=20160218` 属于底层歌曲日期。 |
| ERIS ULTIMA，VERSE A151，`netOpenName=v2_30 00_0`；オーバーライド，A152，`v2_30 15_2` | 公告分别证实 2025-03-06 和 2025-03-19。前者仍带旧曲日期 2016-09-21，后者是普通曲和 ULTIMA 同期。名称数字与开放机制相关，未验证可从数字反解一张统一日历。 |
| Aether Crest: Celestial 解禁 | 2025-02-25 开始联动，双方进度达标后才宣布 2/27 10:00 开放解禁地图。公告给出受运营条件控制的实例。 |
| SUN PLUS A000 晚于版首的构建标记，以及《回レ！雪月花》A001 | 公开日期可以早于手头的基础镜像。例如 A001 与 5/11 新增图相符。构建日是存档观察的下界，作为拒绝官方首配证据的条件会漏记。 |
| `releaseTagName`、`firstLock`、`disableFlag`、`enableUltima` | 归属标签和各层入口标志待结合外部排期。Halcyon 和 ERIS 的已有旧曲归属不随独立 ULTIMA 追加重置。文件启用与指定路线能否游玩分开。 |

[ENDYMION 课程公告](https://info-chunithm.sega.jp/2109/) · [Halcyon 与 5/8 补充公告](https://info-chunithm.sega.jp/7088/) · [ERIS 官方 X](https://x.com/chunithm/status/1893983417840067068) · [オーバーライド官方 X](https://x.com/chunithm/status/1901830929146134617) · [联动达标公告](https://x.com/chunithm/status/1894707223533871104) · [SUN PLUS 开服公告](https://info-chunithm.sega.jp/4841/)

## 日期应用和排除

- 首次开放须有同身份的 HDD 见证，核对版本或包、曲号、名称和难度。更晚的 HDD 持有期仍可作为身份见证，早期时间窗保留未知内容和定数。活动表、约束式解禁和已知旧曲日期不能推导后加 ULTIMA 的日期。首配与通常开放、復活、条件调整和限时结束可同时作为不同事件存在。
- SEGA 明示 2024-07-18 停配的曲目使用维护日。社区条目导出的次日作为被排除的说法保留。X 的 Dig Delight! 和 Floor Killer 停配图另核实了 2025-04-10。 [SEGA 停配公告](https://info-chunithm.sega.jp/7946/) · [官方 X 停配图](https://x.com/chunithm/status/1908014963857461573)
- 定数原值照 HDD。在 NEW PLUS 的三张 MASTER 和 X-VERSE 的一张 ULTIMA 上，公告点名了内容变更，前后 C2S 指纹匹配到对应包。VERSE 标题更名亦需前后值匹配。LUMINOUS 的谱师标签规则仅用于 A000 中 `【Ex】` 前缀确实消失且其余名义一致的字段。官方同时提到 ExTAP 行为变化，尚未证明每个 C2S 差值都由该规则导致，相关通用日期未展开。 [NEW PLUS 修订](https://info-chunithm.sega.jp/2007/) · [X-VERSE 修订](https://info-chunithm.sega.jp/10018/) · [VERSE 曲名](https://info-chunithm.sega.jp/8930/) · [LUMINOUS 标签调整](https://info-chunithm.sega.jp/6357/)
- 缺日期重点为缺少可读原帖或图证、日期来源冲突或超出已持存档范围、同名多身份、特殊路线未确认开关，以及内部变化没有逐谱公告。下一次调查可从缺口 CSV 的包、原始 XML、日期候选和版本公告入口继续。优先找活动页、较早存档、图中文字和开服前后的差分。

## 重建与查询

在 [原有重建命令](JP_HDD_HISTORY.md) 上加 `--official-notices tools/sdhd_official_notices.json` 并指定一个新输出目录。本次为 `.tools/jp-hdd/official-final`。旧版 v3 保留作物证与日期基线。

```powershell
python -X utf8 tools/audit_sdhd_official_dates.py `
  .tools/jp-hdd/complete/catalog.sqlite .tools/jp-hdd/official-final/catalog.sqlite `
  --gaps .tools/jp-hdd/official-dates/missing-after.csv
```

```python
from nonebot_plugin_unibot.services.old_hdd_history import OldHddHistory

history = OldHddHistory(".tools/jp-hdd/official-final/catalog.sqlite")
announcements = history.official_notices(day="2025-03-06")
changes = history.batches_on("2025-07-16")
```

`sdhd_notice` 存来源、发布日期、公告层面明确的有效日、媒体渠道和关联 URL。`sdhd_notice_claim` 可通过身份、日期查每项证据、适用路线、定位、图片与 HDD 修订。只有一部分字段有明确生效证据时，日查结果的 `dated_fields` 标出这些字段。旧 v3 仍可读取，其公告查询为空。

## 本次验收结果

105 篇 SEGA 官网公告及 29 条官方 X 原帖纳入索引。24 条 X 帖有明确生效日，共覆盖 13 个日期。两类官方来源合计与 HDD 建立逐项关联的日期有 92 个。

| 身份类型 | 原有已确认 | 本次已确认 | 新增 | 待确认 |
| --- | ---: | ---: | ---: | ---: |
| 普通曲 | 793 | 838 / 970 | 45 | 132 |
| 普通谱面 | 3,172 | 3,352 / 3,888 | 180 | 536 |
| ULTIMA | 34 | 104 / 114 | 70 | 10 |
| WE 谱面 | 87 | 107 / 113 | 20 | 6 |

确认了 4 张谱面的内容修订、383 条谱师标签修订和 1 条曲名修订。4 张谱面各有一组原始 C2S 与标准化记谱指纹变更。逐项定数仍待证实生效日。官方公告另关联 7 条歌曲停配、1 首歌曲及 4 张普通谱面的复配、4 条通常开放、3 条条件变更和 2 条限时结束。

新旧框 22 版物证快照逐项一致。原有已确认首配日期均保留。构建约 331 秒，SQLite 为 1,302,228,992 字节。同机交替测量五个日期，旧库中位数为 267–364 毫秒，新库为 266–343 毫秒。全量单测 156 项中 144 通过、11 跳过。`dōnatsuhōru` 别名查询仍是此前基线已复现的单项失败。HDD 专项、邻仓回归、能力目录和编译检查均通过。
