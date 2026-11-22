# UniBot 日服旧框历史库

旧框库覆盖 2015-07-16 至 2021-11-03 的日本运营日历。它保存歌曲、独立谱面、内容修订、配信证据和实际 HDD 包。现有 Rating、国服查分和 QQ 命令沿用各自入口。

## 构建与比对

```powershell
python -X utf8 tools/build_old_hdd_history.py `
  --root 'E:\SDBT 11 in 1' `
  --output .tools/old-hdd/lifecycle-dates-final `
  --evidence .tools/old-hdd

python -X utf8 tools/verify_old_hdd_timeline.py `
  .tools/old-hdd/lifecycle-dates-final/catalog.sqlite `
  .tools/old-hdd/lifecycle-complete/catalog.sqlite
```

输出必须位于 HDD 树外，且目标目录尚不存在。先在同一父目录构建，通过 SQLite 完整性和外键检查后再发布。最终数据库与报告各有 SHA-256。验证器可传 `--repeat 第二次构建的数据库` 比对数据库字节。默认只生成数据库、统计与校验清单。有一次性审计需要时可加 `--audit-csv`。默认计算 XML、C2S、图片和配置的 SHA-256，`--hash-all` 额外哈希包内其他资源，VHD 仅登记大小。

证据目录可保留 12 版追加日期 Wiki HTML、停配和复配表、PARADISE 更新页、SEGA POP 索引及 `sega-pop/<页面ID>.html`。输入名称见 `tools/old_hdd_evidence.py`。按数据库 `references_used` 的 URL、HTML 指纹和表格行号复查证据。历史 Wiki 能补日期、归属和变更线索，定数和等级完全由 HDD 数据提取。该构建器已移除 B5 比较和 B5 定数依赖。独立的新框历史工程以及现有 CN 成绩映射仍有调用者。

## HDD 观察和日期证据

- `data/A000` 是基础内容。 `bin/option/Axxx` 里混有累积音乐包、仅配置或仅活动的包。前十份 HDD 的 `data.conf` 提供部分顺序。CRYSTAL 的 A001、A011、A014、A017、A020 都写 1.40.0，音乐 ID 集合严格逐包包含，共有 Music.xml 完全相同，因此可排列这条音乐链，生效日期仍未知。
- `releaseTagName` 匹配 `ReleaseTag.xml` 中的归属标签，`netOpenName` 存在排期类标记，`firstLock`、`disableFlag` 和谱面的 `enable` 提供不同层级的状态证据。初代和 PLUS 缺少 `disableFlag`。基础或累积包中存在文件，只能证明机台收到内容。
- `Event.xml` 的事件子项能列出实际音乐 ID、地图、推荐曲和素材引用。初代 Event 11 是首发歌曲开放列表，A000 的 `alwaysOpen=false`，A002 的同一事件为 `true`，A002 无 Music.xml。此类字段变化单独记录。部分名称含月份或活动名。未发现包含完整日服开服日期的本地事件日历。`TimeTable.xml` 的样本是工作日时段。
- 1.50 的 A032/A033 不含 `data.conf`。A033 包含 A032 全部 154 个音乐 ID，140 个 Music.xml 一致，其余含替换，另有 12 个 ID。它们的内容关系可以逐条追溯，运营日期依赖公告或兼容快照证据。A114、A910、A999 的来源仍可审计，生产曲目推断排除这些包。
- WORLD’S END 按独立 ID、类型、槽位和内容证据识别。普通谱面与同曲 WE 可通过共享 cue 加题名或曲绘建立关联。跨版本身份同时看类型、ID、题名及作者、音源或谱面证据。重用的 ID 或有旁证的跨 ID 同内容可在 `lifecycle().related` 查看关系，各自保留独立档案。C2S 的字节指纹用于复查原件。另存省去已识别导出标签和统计汇总后的记谱指纹。其保留节拍、音符和控制行，字段编码变化也可能改变指纹。谱师优先读取 Music.xml，当该栏为空时使用 C2S 的 UTF-8 `CREATOR` 头并记录字段来源。
- 原 HDD 活动图片通过 `DDSBanner.xml` 和 `Event.xml` 直接引用，用字节 SHA-256 去重。SEGA 官网 POP 是外部发布的 PDF，链接按规范化 URL 去重，发布日期独立保存。文件名日期与已知更新日吻合时提供关联候选，单靠网页发布日期不能认定活动当天生效。 [SEGA 历史新闻](https://chunithm.sega.jp/news/) · [SEGA 店铺 POP 列表](https://am.sega.jp/dlpop/dlpop_cat/chunithm) · [SEGA 历代回顾](https://info-chunithm.sega.jp/5544/)
- 2021-11-04 是新版 NEW 的边界。旧框另行运营至 2023-01-31，这一并行运营阶段没有本轮对应截点或服务端排期。 [SEGA 旧框服务结束公告](https://info-chunithm.sega.jp/4017/)

## 查询

```python
from nonebot_plugin_unibot.services.old_hdd_history import OldHddHistory

history = OldHddHistory(".tools/old-hdd/lifecycle-dates-final/catalog.sqlite")
catalog = history.catalog_on("2021-08-05", include_possible=True)
song_and_charts = history.identities(96)
lifecycle = history.lifecycle("0:96:1:3:MAS")
updates = history.batches_on("2021-08-05")
details = history.batch_history("day:2021-08-05")
version_end = history.checkpoint("PARADISE LOST")
constants = history.constants("PARADISE LOST")
```

`catalog_on` 接收 JST 日历日期。返回日期所属版本、覆盖标签、逐行可用性、候选内容修订和不确定条数。`present` 表示有当日证据或版本末截点支持，`inferred_present` 表示连续观察或当期首发证据支持。`possible` 表示存续时间仍不明确，默认仅返回前两者。`value_status=partial` 时只返回候选一致的字段。例如曲绘或 C2S 存在多个可能修订时，已一致的定数仍可批量读取。超出范围显式返回 `out_of_scope`。

`lifecycle` 返回每条观察、相应内容字段、差异及其日期证据。可通过 `content_sources` 找到原 C2S 或图片的相对路径。批次使用 `pkg:<HDD>:<包名>`、`day:YYYY-MM-DD` 与 `end:<版本>`。日批次代表该日确定的公开事件，包批次有自己的数据版本和顺序，截点是本地物证的对照面。范围型事件的 `earliest_on/latest_on` 包含首尾日。缺失记录以观察空档入库。Wiki 仅写某版为最后一版的证据会保留边界候选，同日冲突保留来源。游戏内活动素材可查 `assets`，歌曲关联活动可查 `game_events`。

`checkpoint` 通过修订模型重建正式版本末查询。原 `songs/charts/worlds_end/chart/changes/jackets/constants/score_mappings` 继续适用于 v1 与 v2。`constants` 默认使用直接确认的对应版本 HDD 值，用户可以通过 `include_inferred=True` 显式纳入 PARADISE 过渡推定。定数单位为百分位整数，兼容批量 `constants` 返回 Decimal。接入旧框 Rating 前仍需验证当前 CN 谱面指纹与历史身份。

原始 HDD、外部证据、验收基线及正式数据库保留在本地。运行库、临时 SQLite 和解出的游戏素材放入 `.tools/old-hdd/`。清理以完整构建目录的文件大小和 SHA-256 对照为依据，保留不同修订或用途不明的材料。

## 首配日期复核

日服 WikiWiki 的配信日期按明确记录采用。遇到来源冲突时，核查 HDD 中对应的曲目 ID、课程和开放事件，并查找同期官方公告。复核条目在 `tools/old_hdd_reviewed_dates.json`，构建时通过实际 Music.xml 路径限定身份。有课程、活动或地图见证时，检查其音乐引用中确有该 ID。日期修订的 `released.evidence` 包含来源 URL、HDD 路径和有证据支持的首发开放方式。课程专用谱面能通过课程游玩，普通模式的单独开放记录保存在 `access` 事件里。

- 普通歌曲 8 个待确认 ID 及所属 32 张谱面均补齐日期。《GO》保留两个不同谱面版本。723 对应 2019-04-11 联动地图先行，808 对应 2019-06-06 通常开放。联动地图关闭后已解禁玩家对 723 的访问仍缺直接证据。 [《GO》历史记录](https://wikiwiki.jp/chunithmwiki/GO) · [SEGA 通常开放公告](https://chunithm.sega.jp/news/2019-06-03/)
- 全部 18 张待确认 WE 补齐日期。官方 X 告知图明确写出 2017-05-11 的 Crazy -II- Set 和 2018-06-21 的 Double Set；HDD 对应课程分别引用 8081 和 8121。 [Crazy -II- Set 告知图](https://pbs.twimg.com/media/C_RdU6OUIAAvjBS.jpg?name=orig) · [Double Set 告知图](https://pbs.twimg.com/media/DgBbKCJU0AAIpZl.jpg?name=orig)
- ナイト・オブ・ナイツ［時］、B.B.K.K.B.K.K.［戻］和 FREEDOM DiVE［狂］的首配采用 PLUS 追加表及 HDD 5 月下半、6 月下半开放事件。三张谱面在 AIR 切换时停配，2017-02-09 随 AIR PLUS 复配。官方告知图逐一标出三曲，HDD 事件 1294 引用三个音乐 ID。复配表原写 2016-02-09，已作为被排除的说法保留。 [PLUS 追加日表](https://wikiwiki.jp/chunithmwiki/CHUNITHM%20PLUS%20%E6%A5%BD%E6%9B%B2%E4%B8%80%E8%A6%A7%28%E8%BF%BD%E5%8A%A0%E6%97%A5%E9%A0%86%29) · [官方复配告知](https://x.com/chunithm/status/829162884299001857)
- 《小野ちゃん…》采用官方 2020-06-18 限定开放日，记录至 2020-07-15 和 2021-04-01 至 04-07 两段在覆盖期内的限时开放。Genesis［分］三张独立谱面同日加入 Evolutionary Set，课程文件记录三个 ID 的随机池。LINK LINK FEVER!!!［招］于 2021-05-13 随 WELCOME Set 首次开放。 [SEGA 2020 限定公告](https://chunithm.sega.jp/news/2020-06-17/) · [SEGA 2021 复配公告](https://chunithm.sega.jp/news/2021-04-01/) · [官方 Genesis 课程公告](https://x.com/chunithm/status/1331070211148038145) · [官方 WELCOME Set 课程公告](https://x.com/chunithm/status/1391996830536269824)

版本中途尚未获得独立生效日期的谱面修订、其余停配与复配，继续提供范围或未确认状态。按日期查询时，明确的限时开放区间和地图关闭证据优先。兼容截点仍可返回硬盘留存的版本记录，便于核对差异。

完整 HDD 重建后核对了 1,021 条普通歌曲、4,084 张普通谱面和 205 张 WE，首配日期均可查询。和初版逐项比较，12 版截点仅在 PARADISE LOST 新增了已验证的 LINK LINK FEVER!!!［招］及其曲目记录。按日查询另外反映《ウソテイ》首次限时路线关闭、《GO》723 联动地图关闭，以及《小野ちゃん…》两段限时活动结束后的状态。

## 初版真实数据验收

两次完整构建生成相同的 SQLite SHA-256。最终库为 803,622,912 字节。逐项比对 12 版的有效曲目与谱面、曲绘、主要元信息、选用定数及 WE 属性和星级，已有字段一致。C2S 的 `CREATOR` 为 25,760 条普通和 738 条 WE 版本记录补齐了谱师。

库内有 1,021 首普通歌曲、4,084 张普通谱面和 205 张独立 WE。附有明确首配日期的各为 1,013、4,052 和 187。165 个公开更新日有逐项事件。共登记 73 个物理包，44 个有可用的顺序信息，29 个日期和完整顺序仍待补证。实际游戏生效史仍缺服务器排期。追加另一时期的原始包、运营侧排期或有明文日期的公告，可以缩小日期窗口并校验现有推定。

初版逐项差异、事件统计、SHA-256 复验和日期查询耗时见 `.tools/old-hdd/lifecycle-verification.json`。日期修订与初版的比对见 `.tools/old-hdd/lifecycle-dates-validation.json`。初版清理文件列表和校验和见同目录的 `cleanup-manifest.json`，执行结果见 `cleanup-result.json`。

日服新框及统一 v3 查询见 [日服新旧框统一 HDD 历史库](JP_HDD_HISTORY.md)。
