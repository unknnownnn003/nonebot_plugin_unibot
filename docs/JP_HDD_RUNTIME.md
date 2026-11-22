# UniBot 日服历史查歌与 Rating

| 指令 | 结果 |
| --- | --- |
| `/chu查歌 鳥の詩` | 优先显示最新截点的有效记录。曲目已停配时显示最后有效收录，并标明当前状态。 |
| `/chu查歌 鳥の詩 AIR`、`/chu查歌 FREEDOM DiVE PARADISE LOST`、`/chu查歌 96 MATE` | 指定版本截点。未收录时提示无该版本记录。已明确关联的 WE 用独立行显示星级、属性和 ID。 |
| `/chu查歌 Help me, ERINNNNNN!! VERSE` | 显示同名候选。可用 `/chu查歌 #0:97:1 VERSE` 查看 Band ver. 所属的历史身份。 |
| `/b30 AIR`、`/b30 PARADISE LOST`、`/b30 LUMINOUS PLUS` | 本人 OAuth 成绩 × 当期 HDD 定数。输出 BEST30 均值及 B1×10、B1–B10 两种 RECENT 模拟，模拟总值均除以 40。 |
| `/b50 VERSE`、`/b50 X-VERSE-X`、`/b50 MATE` | 按歌曲在日服的实装证据分 BEST30 与 NEW20，合计除以 50。 |

查歌在群内使用 `songs`，历史 Rating 在群内使用 `player`，与对应国服命令共用队列、权限、作用域和补发机制。查歌可以匿名使用，Rating 使用请求者本人的 OAuth 授权。无版本的 `/b30`、`/b50` 和独立的 `/旧框` 沿用现有口径。LLM 工具参数与版本保持原有契约。

## SQLite 口径

只读打开已完成发布的统一 v3 库。检查 SQLite application_id、user_version、`complete.ready`、必要表和 `quick_check`。数据库校验失败仅影响历史入口，文件替换期间已开始的历史查询会拒绝混用数据库。版本名、顺序、起止日均来自 `versions`，相同完整歌名优先于拆分后的版本后缀。MATE 截点是 **2026-10-08**。

- 查歌读取 `music_versions`、`chart_versions`、`worlds_end_versions`，通过历史名称与实际观察 ID 搜索。用 `history_observations` 和 `history_revisions` 关联每个版本的修订。仅通过 `origin_identity` 或 `history_identity_links.worlds_end_origin` 展示关联 WE。首次实装信息来自 `sdhd_first_release` 和首配事件，区分日期记录、推定、范围和未知。
- Rating 只接受截点为 `present` 且日历窗口确认可玩、适用修订一致的启用谱面。采用 `selected_hundredths` 的 `hdd_observed`、`hdd_inherited` 值。排除 WE、禁用、未配信和不明修订。旧歌复配、加 ULTIMA 后仍属旧曲。NEW20 使用歌曲首配证据，包日期推定或仅有原盘观察时不能据此分为新曲。见 [SEGA VERSE](https://info-chunithm.sega.jp/8930/) 与 [SEGA X-VERSE 新旧曲分区](https://info-chunithm.sega.jp/10018/)。
- NEW 前使用旧框单谱公式，SSS 在 1,007,500 分封顶为定数 +2.00。NEW 起使用现有现代计算公式，1,007,500 同为 +2.00，SSS+ 在 1,009,000 分封顶为 +2.15。B30/B50 的命令分界是 VERSE。SEGA 的公告用于确认游戏时期，分段实现沿用仓库计算器。见 [SEGA NEW 新功能](https://info-chunithm.sega.jp/1098/) 与 [落雪现代计算器实现](https://github.com/Lxns-Network/maimai-prober-frontend/blob/fffe5141cbb7db59752a7187e36e1b5adcfc25d2/src/utils/chunithm/rating.ts)。

## 映射审核与覆盖

正式库中 3,048 项 `score_mapping` 都是 `pinned_candidate`。它们按现有《旧框》的 CN 元数据指纹及歌名/槽位建立候选，`cn_mapping_candidates()` 可经同一 JP 身份追溯到新框。B5 的外部 ID 和同名、同物量、同定数记录也只适合找原件。见 [B5 风险审计](B5-全版本曲库纠偏与剩余问题报告.md)。

补证后可向 `HISTORY_DIR/score_mappings.json` 加入 `{"schema_version": 1, "verified": [...]}`。每条记录有以下字段。

| 字段 | 审核依据 |
| --- | --- |
| `cn_id`、`slot` | 落雪国服原始曲号与普通难度槽 0–4。 |
| `cn_fingerprint` | `legacy_rating.chart_identity()` 对当前 CN 歌曲、谱面元数据生成的 SHA-256。运行时复核唯一的当前可玩 CN 槽。 |
| `jp_identity` | JP `chart_identity`，保留版本修订链。 |
| `notation_sha256` | 审核确认两地相同的标准化 C2S 记谱 SHA-256。运行时要求目标可玩修订的 `history_revisions.payload.notation_sha256` 完全一致。不同记谱修订要分别取证。 |
| `evidence` | 审核记录定位符。应可以回查两地原件哈希、版本、槽位和标准化工具，或回查逐谱等价审核。由管理者当作可信发布文件维护。 |

运行时区分已验证、候选、冲突、未知。CN 记录去重按原始 ID 和槽，双向冲突阻止入榜。部分模拟在图上显示参与样本、覆盖率、各类遗漏以及可能偏低的提示。少于 10 张时返回数据不足。B50 有未解决记录且其中一池为空时也返回数据不足。每次只有本人可查看自己的遗漏计数，`未纳入 SSS 及以上` 的阈值为 1,007,500。所有未计入的普通成绩都按可能影响榜单处理。

当前 22 代查歌均可用。**指定版本 Rating 的相容审核映射为 0，22 代都会按实际成绩返回覆盖及数据不足。本轮没有采集真实账号成绩，因此无法报告个人高分遗漏的实测数量。** 若一位用户有任何 SSS 及以上普通成绩，当前这些成绩都无法进入指定版本榜单。以下数字以 JP 谱面为分母，逐版本相加会重复计入跨版谱面。用下列命令复算及审核新清单。

```powershell
python -X utf8 tools/report_jp_rating_coverage.py .tools/jp-hdd/compact/catalog.sqlite
```

| 版本 | 截点 | 曲目身份 | 统计谱面 | 相容审核证据 | 候选 | 冲突 | 未知 | NEW20 池 | 待分区 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| CHUNITHM | 2016-02-03 | 126 | 504 | 0 | 352 | 0 | 152 | — | — |
| PLUS | 2016-08-24 | 217 | 752 | 0 | 520 | 0 | 232 | — | — |
| AIR | 2017-02-08 | 313 | 1108 | 0 | 800 | 0 | 308 | — | — |
| AIR PLUS | 2017-08-23 | 428 | 1448 | 0 | 1052 | 0 | 396 | — | — |
| STAR | 2018-03-07 | 502 | 1788 | 0 | 1344 | 0 | 444 | — | — |
| STAR PLUS | 2018-10-24 | 594 | 2080 | 0 | 1604 | 0 | 476 | — | — |
| AMAZON | 2019-04-10 | 684 | 2360 | 0 | 1832 | 0 | 528 | — | — |
| AMAZON PLUS | 2019-10-23 | 735 | 2648 | 0 | 2088 | 0 | 560 | — | — |
| CRYSTAL | 2020-07-15 | 816 | 2956 | 0 | 2364 | 0 | 592 | — | — |
| CRYSTAL PLUS | 2021-01-20 | 882 | 3212 | 0 | 2620 | 0 | 592 | — | — |
| PARADISE | 2021-05-12 | 909 | 3328 | 0 | 2812 | 0 | 516 | — | — |
| PARADISE LOST | 2021-11-03 | 973 | 3572 | 0 | 3048 | 0 | 524 | — | — |
| NEW | 2022-04-13 | 1109 | 4066 | 0 | 3034 | 0 | 1032 | — | — |
| NEW PLUS | 2022-10-12 | 1185 | 4433 | 0 | 3032 | 0 | 1401 | — | — |
| SUN | 2023-05-10 | 1260 | 4760 | 0 | 3034 | 0 | 1726 | — | — |
| SUN PLUS | 2023-12-13 | 1315 | 4907 | 0 | 3024 | 0 | 1883 | — | — |
| LUMINOUS | 2024-06-19 | 1419 | 5423 | 0 | 3034 | 0 | 2389 | — | — |
| LUMINOUS PLUS | 2024-12-11 | 1460 | 5570 | 0 | 3033 | 0 | 2537 | — | — |
| VERSE | 2025-07-15 | 1554 | 5973 | 0 | 3034 | 0 | 2939 | 416 | 0 |
| X-VERSE | 2025-12-10 | 1611 | 6233 | 0 | 3034 | 0 | 3199 | 307 | 0 |
| X-VERSE-X | 2026-07-01 | 1697 | 6580 | 0 | 3034 | 0 | 3546 | 430 | 0 |
| MATE | 2026-10-08 | 1754 | 6821 | 0 | 3034 | 0 | 3787 | 250 | 0 |

## 文件与上线准备

运行路径默认为 NoneBot LocalStore 插件持久目录下的 `historical_catalog/catalog.sqlite`。本地开发可把进程环境变量 `UNIBOT_JP_HISTORY_DB` 指向工作区 `.tools/jp-hdd/compact/catalog.sqlite` 的绝对路径。曲绘和映射清单始终读取 LocalStore 的 `historical_catalog/`。数据库应作为关闭写入的单文件发布，授予 Bot 账号读权限。

1. 核对数据库为 553,123,840 字节（约 527.5 MiB），SHA-256 为 `9830c721bc1c2f2dbf69720f298f758ae0fc79d3de4bd7b15753bc2404801f23`。用独立文件预校验后，替换目标 `catalog.sqlite`，保留上一版供回滚。
2. 在有原盘的机器离线导出曲绘。可重复传 `--root`，可用 `--version` 和重复的 `--id` 缩小范围。导出器复核 DDS 哈希、保留比例与透明度，以原件哈希去重，记录版本、身份、修订、PNG 路径、哈希和失败。可重复运行，校验健康的 PNG 后跳过转换。
3. 复制 `jackets/` 下 PNG 与各自的 `.sha256` 到生产 LocalStore。正式库清单有 21,824 条匹配记录和 2,048 个去重原件。本地实导出 4 条版本记录、3 张 PNG，共 260,845 字节。全量容量以真实导出结果为准。找不到有效 PNG 时图片显示占位。
4. 配置后先验证公共历史查歌和群开关，再由授权本人核对指定版命令与覆盖回复。已导出资源无需挂载 HDD。后续若有正式审核清单，将其与数据库版本和审核依据一并登记。

```powershell
python -X utf8 tools/export_jp_jackets.py .tools/jp-hdd/compact/catalog.sqlite `
  --root 'E:/SDBT 11 in 1' --root 'E:/SDHD' --output '<LocalStore>/historical_catalog' `
  --version 'AIR' --id 96
```

## 本地验收

使用正式库覆盖了 22 代解析、版本末查询、改名和 ID 冲突、停复配、WE、HDD 定数和 NEW20 分类。实测 ID 96 的 MASTER 在 AIR 为 11.90，在 MATE 为 12.90。真实原盘曲绘、停配回查的占位图和 Rating 版式均在本地渲染。经模拟 OneBot 发送验证了公开查歌入队与幂等，指定版本 Rating 在缺 OAuth 时拒绝。今年版 MATE 使用文字版本名作为缺少 Logo 的降级。
