# UniBot 日服历史查歌与 Rating

| 指令 | 结果 |
| --- | --- |
| `/chu查歌 鳥の詩` | 优先显示最新截点的有效记录。曲目已停配时显示最后有效收录，并标明当前状态。 |
| `/chu查歌 鳥の詩 AIR`、`/chu查歌 FREEDOM DiVE PARADISE LOST`、`/chu查歌 96 MATE` | 指定版本截点。未收录时提示无该版本记录。已明确关联的 WE 用独立行显示星级、属性和 ID。 |
| `/chu查歌 Help me, ERINNNNNN!! VERSE` | 显示同名候选。可用 `/chu查歌 #0:97:1 VERSE` 查看 Band ver. 所属的历史身份。 |
| `/b30 AIR`、`/b30 PARADISE LOST`、`/b30 LUMINOUS PLUS` | 本人 OAuth 成绩 × 当期 HDD 定数。输出 BEST30 均值及 B1×10、B1–B10 两种 RECENT 模拟，模拟总值均除以 40。 |
| `/b50 VERSE`、`/b50 X-VERSE-X`、`/b50 MATE` | 按歌曲在日服的实装证据分 BEST30 与 NEW20，合计除以 50。 |

查歌在群内使用 `songs`，历史 Rating 在群内使用 `player`，与对应国服命令共用队列、权限、作用域和补发机制。查歌可以匿名使用，Rating 使用请求者本人的 OAuth 授权。无版本的 `/b30`、`/b50` 和独立的 `/旧框` 沿用现有口径。LLM 工具参数与版本保持原有契约。

版本名忽略大小写、内部空格及连字符，可用 `+` 代替 `PLUS`，也可加 `CHUNITHM` 前缀。例如 `/b30 chunithmplus`、`/b30 AIR+` 和 `/b50 xversex`。查歌时曲名与版本参数之间留一个空格。历史 Rating 榜单的曲名显示一行，超宽处以省略号结尾。

## SQLite 口径

只读打开已完成发布的统一 v3 库。检查 SQLite application_id、user_version、`complete.ready`、必要表和 `quick_check`。数据库校验失败仅影响历史入口，文件替换期间已开始的历史查询会拒绝混用数据库。版本名、顺序、起止日均来自 `versions`，相同完整歌名优先于拆分后的版本后缀。MATE 截点是 **2026-10-08**。

- 查歌读取 `music_versions`、`chart_versions`、`worlds_end_versions`，通过历史名称与实际观察 ID 搜索。用 `history_observations` 和 `history_revisions` 关联每个版本的修订。仅通过 `origin_identity` 或 `history_identity_links.worlds_end_origin` 展示关联 WE。配信版本及日期来自 `sdhd_first_release` 和首配事件，当前版本下方为查询截点。
- Rating 只接受截点为 `present` 且日历窗口确认可玩、适用修订一致的启用谱面。采用 `selected_hundredths` 的 `hdd_observed`、`hdd_inherited` 值。排除 WE、禁用、未配信和不明修订。旧歌复配、加 ULTIMA 后仍属旧曲。NEW20 使用歌曲首配证据，包日期推定或仅有原盘观察时不能据此分为新曲。见 [SEGA VERSE](https://info-chunithm.sega.jp/8930/) 与 [SEGA X-VERSE 新旧曲分区](https://info-chunithm.sega.jp/10018/)。
- NEW 前使用旧框单谱公式，SSS 在 1,007,500 分封顶为定数 +2.00。NEW 起使用现有现代计算公式，1,007,500 同为 +2.00，SSS+ 在 1,009,000 分封顶为 +2.15。B30/B50 的命令分界是 VERSE。SEGA 的公告用于确认游戏时期，分段实现沿用仓库计算器。见 [SEGA NEW 新功能](https://info-chunithm.sega.jp/1098/) 与 [落雪现代计算器实现](https://github.com/Lxns-Network/maimai-prober-frontend/blob/fffe5141cbb7db59752a7187e36e1b5adcfc25d2/src/utils/chunithm/rating.ts)。

## 同 ID 映射与覆盖

历史 Rating 以落雪 OAuth 成绩的原始 `(id, level_index)` 对照指定日服版本可计分谱面的 `(music_id, slot)`。唯一匹配时直接用这条成绩和目标版本的 HDD 定数计算。该规则采用相同 ID、相同难度即对应的假设，适用于模拟。没有对应项或存在多个目标时，计入未匹配或冲突。当前国服公开曲库、C2S 和人工映射清单均非运行依赖。

部分模拟会显示参与样本、匹配率、遗漏数和可能偏低的提示。B30 显示在图内，B50 随图片发送。少于 10 张时返回数据不足。B50 有未解决记录且其中一池为空时也返回数据不足。高分遗漏按 1,007,500 及以上统计。

以下使用 1,466 首公开落雪曲库快照估算谱面覆盖，个人结果取决于本人的 OAuth 成绩。这个快照在 X-VERSE-X 和 MATE 没有匹配的 NEW20 谱面。可以用更新的公开快照复算。

```powershell
python -X utf8 tools/report_jp_rating_coverage.py .tools/jp-hdd/compact/catalog.sqlite --cn-catalog .tools/lxns-public-songlist.json
```

| 版本 | JP 可计分谱面 | 同 ID/同难度匹配 | BEST30 池 | NEW20 池 | 待分区 | 冲突 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| CHUNITHM | 504 | 364 | — | — | — | 0 |
| PLUS | 752 | 540 | — | — | — | 0 |
| AIR | 1108 | 824 | — | — | — | 0 |
| AIR PLUS | 1448 | 1080 | — | — | — | 0 |
| STAR | 1788 | 1372 | — | — | — | 0 |
| STAR PLUS | 2080 | 1632 | — | — | — | 0 |
| AMAZON | 2360 | 1860 | — | — | — | 0 |
| AMAZON PLUS | 2648 | 2116 | — | — | — | 0 |
| CRYSTAL | 2956 | 2392 | — | — | — | 0 |
| CRYSTAL PLUS | 3212 | 2648 | — | — | — | 0 |
| PARADISE | 3328 | 2816 | — | — | — | 0 |
| PARADISE LOST | 3572 | 3052 | — | — | — | 0 |
| NEW | 4066 | 3465 | — | — | — | 0 |
| NEW PLUS | 4433 | 3808 | — | — | — | 0 |
| SUN | 4760 | 4129 | — | — | — | 0 |
| SUN PLUS | 4907 | 4323 | — | — | — | 0 |
| LUMINOUS | 5423 | 4830 | — | — | — | 0 |
| LUMINOUS PLUS | 5570 | 5118 | — | — | — | 0 |
| VERSE | 5973 | 5528 | 5141 | 387 | 0 | 0 |
| X-VERSE | 6233 | 5691 | 5528 | 163 | 0 | 0 |
| X-VERSE-X | 6580 | 5684 | 5684 | 0 | 0 | 0 |
| MATE | 6821 | 5687 | 5687 | 0 | 0 | 0 |

## 文件与上线准备

运行路径默认为 NoneBot LocalStore 插件持久目录下的 `historical_catalog/catalog.sqlite`。本地开发可把进程环境变量 `UNIBOT_JP_HISTORY_DB` 指向工作区 `.tools/jp-hdd/compact/catalog.sqlite` 的绝对路径。曲绘始终读取 LocalStore 的 `historical_catalog/`。数据库应作为关闭写入的单文件发布，授予 Bot 账号读权限。

1. 核对数据库为 553,123,840 字节（约 527.5 MiB），SHA-256 为 `9830c721bc1c2f2dbf69720f298f758ae0fc79d3de4bd7b15753bc2404801f23`。用独立文件预校验后，替换目标 `catalog.sqlite`，保留上一版供回滚。
2. 在有原盘的机器离线导出曲绘。可重复传 `--root`，可用 `--version` 和重复的 `--id` 缩小范围。导出器复核 DDS 哈希、保留比例与透明度，以原件哈希去重，记录版本、身份、修订、PNG 路径、哈希和失败。可重复运行，校验健康的 PNG 后跳过转换。
3. 复制 `jackets/` 下 PNG 与各自的 `.sha256` 到生产 LocalStore。正式库清单有 21,824 条匹配记录和 2,048 个去重原件。本地实导出 4 条版本记录、3 张 PNG，共 260,845 字节。全量容量以真实导出结果为准。找不到有效 PNG 时图片显示占位。
4. 配置后先验证公共历史查歌和群开关，再由授权本人核对指定版命令与覆盖回复。已导出资源无需挂载 HDD。C2S 归档可独立保存，用于后续看谱。

```powershell
python -X utf8 tools/export_jp_jackets.py .tools/jp-hdd/compact/catalog.sqlite `
  --root 'E:/SDBT 11 in 1' --root 'E:/SDHD' --output '<LocalStore>/historical_catalog' `
  --version 'AIR' --id 96
```

## 本地验收

使用正式库覆盖了 22 代解析、版本末查询、改名和 ID 冲突、停复配、WE、HDD 定数和 NEW20 分类。实测 ID 96 的 MASTER 在 AIR 为 11.90，在 MATE 为 12.90。真实原盘曲绘、停配回查的占位图和 Rating 版式均在本地渲染。经模拟 OneBot 发送验证了公开查歌入队与幂等，指定版本 Rating 在缺 OAuth 时拒绝。MATE 图标加载本地 `assets/ui/version/25000.png`，上线时随现有 UI 贴图复制。Logo SHA-256 为 `2dc3266c41f6ed82d8af93d04bfa7a965febd6216477b2b8794aff6f75678672`。 [SEGA 官网 MATE Logo](https://chunithm.sega.jp/$site/components/chuniNavi/logo.png?_=20260701.190431)
