# 日服新旧框统一 HDD 历史库

正式库位于 `.tools/jp-hdd/compact/catalog.sqlite`。离线构建复用十二代 SDBT 的已验证解析成果，从 SDHD 实体文件重放十代 A000 与 OPTION，再导入已审核的运营日期和证据。SQLite 保持 v3 查询结构，资源仍可凭相对路径与 SHA-256 从原盘读取。已解析字段、历次观察与差异留在库内，原始 XML 字节保留在 HDD。

## 原盘覆盖

| 版本 | A000 | OPTION | 有 Music.xml | 无 Music.xml |
| --- | ---: | ---: | ---: | ---: |
| CHUNITHM | 1 | 7 | 2 | 5 |
| PLUS | 1 | 5 | 3 | 2 |
| AIR | 1 | 3 | 2 | 1 |
| AIR PLUS | 1 | 3 | 2 | 1 |
| STAR | 1 | 4 | 2 | 2 |
| STAR PLUS | 1 | 13 | 8 | 5 |
| AMAZON | 1 | 4 | 2 | 2 |
| AMAZON PLUS | 1 | 4 | 2 | 2 |
| CRYSTAL | 1 | 7 | 5 | 2 |
| CRYSTAL PLUS | 1 | 7 | 4 | 3 |
| PARADISE | 1 | 5 | 4 | 1 |
| PARADISE LOST | 1 | 5 | 4 | 1 |
| NEW | 1 | 14 | 11 | 3 |
| NEW PLUS | 1 | 14 | 14 | 0 |
| SUN | 1 | 16 | 13 | 3 |
| SUN PLUS | 1 | 13 | 10 | 3 |
| LUMINOUS | 1 | 19 | 15 | 4 |
| LUMINOUS PLUS | 1 | 17 | 13 | 4 |
| VERSE | 1 | 20 | 18 | 2 |
| X-VERSE | 1 | 16 | 12 | 4 |
| X-VERSE-X | 1 | 20 | 16 | 4 |
| MATE | 1 | 13 | 9 | 4 |

PARADISE 与 PARADISE LOST 共用手头 SDBT 1.50 镜像；表中按运营版本各列一次。配置包也可能附有曲绘、C2S 或其他补丁，这里的分类仅按是否存在 Music.xml。SDHD 十代共 162 个 OPTION，其中 131 个有 Music.xml。

版本日历沿用项目里已核对的 SEGA 日服日期。历史版截点是下版开服的前一天。Mate 的证据截止到 A101 对应的 2026-10-08。另有后续运营公告，本地盘无法验证相应曲库。 [NEW 开服](https://info-chunithm.sega.jp/1151/) · [X-VERSE-X 开服](https://info-chunithm.sega.jp/10941/) · [Mate 开服](https://info-chunithm.sega.jp/12155/) · [Mate A101 对应公告](https://info-chunithm.sega.jp/12754/) · [Mate 2.51 公告](https://info-chunithm.sega.jp/13533/)

`E:/SDHD` 中，NEW 至 SUN PLUS 读取 `SDHD_Axxx_时间戳_0`。后六代读取 `option(A001~...)/option/Axxx`。匹配的 ZIP 跳过，已审核库的包清单用于检查解压结果。目录遍历排除 `自制包`。X-VERSE-X 的外围 2.47 程序更新不含 Music.xml 或 C2S。另一份 Mate A001（位于主输入以外）与本次主输入有已知内容差异，继续使用 `E:/SDHD` 的套件。详见 [原调研记录](SDHD_OFFICIAL_DATES.md)。

## 构建和验证

```powershell
$reviewed = '.tools/jp-hdd/wiki-dates/published/catalog.sqlite'
python -X utf8 tools/build_jp_hdd_history.py `
  --old .tools/old-hdd/lifecycle-dates-final/catalog.sqlite `
  --root 'E:\SDHD' --reviewed $reviewed --output .tools/jp-hdd/compact `
  --through 2026-10-08 --through-url https://info-chunithm.sega.jp/12754/
python -X utf8 tools/verify_jp_hdd_history.py `
  .tools/jp-hdd/compact/catalog.sqlite $reviewed 'E:\SDHD'
```

输出目录须不存在。构建使用同一输出父目录下的临时工作库，完成裁剪、压实、完整性及外键检查后发布。原盘和日期归档只读。本地保留旧 SDBT 构建输入和已审核 v3 回滚库。正式库、大型证据和清理清单留在忽略的本地目录。常规 SQLite 查询无需连接 HDD。若缺解压目录且只剩原始 ZIP，构建器仍支持归档回退及原有密码配置。

## 物证和历史查询

- 基础 `data/A000` 和各更新的 `data.conf` 分开登记。增量按 data.conf 修订号覆盖，同修订号有完整构建时间时以时间排序。未定序的同修订号可以合并无冲突的更新；相互覆盖且内容不同时保留诊断，核心音乐文件的冲突会中止发布。SUN 的 A151 构建早于同修订号 A150。SUN PLUS 现存 A000 镜像构建晚于开服，盘内变化保留日期范围。构建时间提供本地物证界限。
- Music.xml 分开记录歌曲禁用、初始锁定、选件开放标签、谱面开关及 ULTIMA 总开关。第 4 槽识别 ULTIMA，第 5 槽识别 WE。普通定数以 XML 的整数和百分位原值为准。NEW 起显示等级的加号门槛为 .50。 [NEW 等级变更说明](https://wikiwiki.jp/chunithmwiki/CHUNITHM%20NEW%20%E3%83%AC%E3%83%99%E3%83%AB%E5%A4%89%E6%9B%B4%E4%B8%80%E8%A6%A7)
- 新框实际采到的谱面文件仍是文本 C2S。记录原始文件和标准化记谱的 SHA-256，并提取 CREATOR。标准化指纹忽略已知标签与统计行，保留 BPM 和记谱行。它用于对比修订，未实现游戏判定模拟或音符重放。
- 跨框合并歌曲时同时核对 ID、标题、作者及 cue、曲绘或谱面旁证。跨世代和跨 ID 的谱面合并要求记谱一致。候选链接保留冲突原因。新框同一身份同槽后续更改形成修订。PARADISE 与 PARADISE LOST 同身份、同有效定数的可证继承条目在 v3 以 `hdd_inherited` 标注，可进入批量查询。
- `releaseDate` 从本地 LUMINOUS PLUS 基础盘开始出现，既含历史回填也含预载。运用观察版本、日期范围和可审计外部证据约束。普通曲日期不能直接用于后加 ULTIMA。未解禁、活动窗口和允许通过指定条件游玩保持独立。没有服务器状态时，时间范围和候选不会转换为精确配信日期。
- 游戏内公告和 Banner 可以追溯到事件或活动引用及 DDS 路径。曲绘按内容哈希去重，从版本来源查询原件。官方 ULTIMA 告知图以 URL 和既有证据哈希索引。不复制图片、音源或游戏程序。
- `checkpoint` 重建手头的本地版本覆盖。`catalog_on` 给出当日开放证据及候选修订，二者在活动控制、预载、缺包时可能不同。检验器独立从 HDD 上的 Music.xml 重放截点覆盖，检查定数、WE 星级及实际谱面抽样。新框的历史服务器状态仍缺完整独立基线。

```python
from nonebot_plugin_unibot.services.old_hdd_history import OldHddHistory

history = OldHddHistory(".tools/jp-hdd/compact/catalog.sqlite")
patch = history.package_checkpoint("pkg:SDHD_2.50:A101")
song = history.song_lifecycle("0:3:1")
master = history.lifecycle("0:3:1:3:MAS")
date_evidence = history.first_release("0:3:1:3:MAS")
updates = history.batches_on("2022-01-20")
checkpoint = history.checkpoint("MATE", active_only=False)
today = history.catalog_on("2026-10-08", include_possible=True)
constants = history.constants_on("2026-10-08")
```

旧接口保留。可用 `title_identities` 解析版本中保留的精确歌名，`identities` 按实际出现过的数字 ID 查找，`first_release` 查询首配证据，`lifecycle` 返回修订、变更与关系。曲绘可将 `jackets` 返回的 `path` 和 `sha256` 传给 `resource_bytes` 读取校验。 `cn_mapping_candidates` 沿统一谱面身份回溯旧框已有的 CN 映射证据，返回来源版本、状态和原指纹。现有 CN 成绩接口继续使用原数据和验证过的映射。

`raw_xml` 已移除。旧框 WE 和歌曲索引仍通过 `sources` 引用 XML 路径及哈希。修订 JSON 已按规范化内容与身份去重；每次 OPTION 的状态通过观察表、修订表和资源索引拼出。B5 离线研究包保留其他地区数据。现行国服成绩和旧框 Rating 指令继续使用各自现有输入。

新框日期证据补全与字段实证见 [官方公告与日期证据](SDHD_OFFICIAL_DATES.md)。

盘内告知图普查、OCR 候选及日期补证见 [HDD 告知图片](HDD_NOTICE_IMAGES.md)。

首次配信、通常解禁、Wiki 日期证据及最终本地库见 [日服配信日期审计](JP_RELEASE_DATE_AUDIT.md)。

## MusicSort 和 OPTION 目录

MusicSort.xml 的根为 `SerializeSortData`，顺序来自 `SortList/StringID/id`。STAR PLUS 基础盘有 677 项，`str` 含曲名。SUN PLUS、LUMINOUS、Mate 基础盘分别有 1,742、1,846、2,339 项，抽样版本中 `str` 均为空。上述三代的手头 OPTION 未发现后续副本；X-VERSE-X 的 A000 与 A001 副本均有 2,247 项且字节相同。NEW PLUS 的 A062 副本仍有 1,494 项，文件哈希与 A000 不同。排序表可以包含预载或其他索引项；单包新增和覆盖以实际 Music.xml 及相应文件判断。

| 目录 | 样本与字段 | 当前用途 |
| --- | --- | --- |
| music | Music.xml 的 `MusicData`、C2S、曲绘 DDS；`name`、`fumens`、`cueFileName`、`firstLock`、`disableFlag`、`enableUltima` | 读取曲目、谱面、定数和状态。 |
| event | Event.xml 的 `EventData`；`substances`、`alwaysOpen`、`ddsBannerName`、`netOpenName` | 索引曲目、活动、地图和课程关系及部分告知图。 |
| map、mapArea | `MapData` 和 `MapAreaData`；`infos`、`grids`、`netDispPeriod`、`stopReleaseEventName` | 保留来源；可进一步解析地图路线、奖励与终止事件。 |
| course | `CourseData`；`infos`、`conditionsCourse`、`netOpenName`、`disableFlag` | 保留来源及事件引用；可进一步索引课程曲目和规则。 |
| releaseTag | `ReleaseTagData`；`name`、`titleName` | 保留配置来源及歌曲引用，可用于关联标签。 |
| ddsBanner、ddsImage | `DDSBannerData`、`DDSImageData`；`ddsFile` 或 `ddsFile0` 至 `ddsFile2` | 索引 DDS 和 Banner 路径及哈希。 |
| gameTable | `GameTableData`；`tableType`、`records` | 保留配置来源，需要逐类解释。 |
| cueFile | `CueFileData`；`acbFile`、`awbFile` | 歌曲保留 cue ID；音频容器在原盘。 |
| chara、namePlate 及其他奖励目录 | `CharaData`、`NamePlateData`；`name`、`netOpenName`、图像、奖励条件 | 可从各包的相应子目录查原件，暂未接入曲库。 |

在 `LUMINOUS PLUS / A072` 中，Music ID 988、2211、2212 等仍有 Music.xml，`disableFlag` 已改变，正好对应 2024-07-18 的停配。VERSE 的 A162 同样保留被禁用的 2200 和 2201，审核日期为 2025-04-10。Mate 的 8329 在 2026-07-02 有复配证据，首份本地记录在 A001，其文件可追溯到 `option(A001~A101)/option/A001`。歌曲总开关、谱面开关、初始锁定、通常解禁与运营停复配是独立证据。活动路径结束时若其他开放渠道未确认，只保留开放状态的不确定边界。单包缺少既有曲目只表示该包未重写它。

## 本地重建结果

- 正式 SQLite 为 549,421,056 字节，较已审核 v3 的 1,304,076,288 字节减少 754,655,232 字节（57.87%）。原 `raw_xml` 与自身主键索引共占 761,507,840 字节，新库移除两者。保留的 `sources`、`history_observations` 和 `history_revisions` 分别约占 75.08、50.02 和 32.57 MB。新增逐包资源引用表及其主键约占 29.02 MB。
- SHA-256 为 `3254abc9527229eae2a0fbc722f9d6afe2c7a1df63727d3042e157d397408e46`。
- 22 代截点及两套盘的 C2S 每代抽样通过。逐表核对保留了已有身份、精确首配索引及带日期的首配、解禁、停复配和修订事件。重叠记录的 HDD 定数、WE 星级和属性一致。新框共保留 2,164 次定数变化及 559 次 C2S 文件变化，其中 14 次定数变化来自版本内 OPTION。NEW A111 的 Music ID 999 在同批次修改 ADV、EXP、MAS。
- 十代新框的 172 个层均有索引（含 10 个 A000），使用解压目录且无 ZIP 成员来源。19 组同修订号组缺少可证的构建先后，未发现它们有不同内容的 Music.xml 或 C2S 同路径覆盖。SUN 的 A151 已排在 A150 前，两包的顺序变化未改变版本末定数。NEW PLUS 的《竹》（Music ID 2185）在 A001，保留 2022-04-14 的首日配信证据。读取和解压异常以正式库的 `sdhd_issues` 和本地 `report.json` 为准。
- 完整性和外键检查通过。实测新框版本末日期查询的各版本中位耗时为 247–436 ms，十代中位数 315 ms。满量 Mate A101 包级查询约 0.3–0.4 秒。
- 删除 27 份已替代或已确认重复的持久研究库，释放 25,914,658,816 字节。另已移除本轮临时构建文件。相同口径统计的 `.tools` 文件逻辑大小由 36,080,249,407 减至 10,715,351,308 字节。实际删除清单含路径、大小及 SHA-256，见 `.tools/jp-hdd/compact/deleted-catalogs.csv`。保留已审核 v3 回滚库、旧框重建输入、HDD 告知图证据索引，以及包含 CN、INTL 数据的独立研究包。

## OPTION 生效日推定

对缺精确生效日的定数和 C2S 变化，若同一 OPTION 在物证时间范围内只有一个已知的首配或修订运营日，则暂用该日。事件以 `SDHD option-day assumption` 标明推定依据。已审核日期优先，通常解禁、构建时间和整包默认日期均不参与推定。首配与停复配记录继续各自保留。

首次增量补入 12 次定数变化和 74 次 C2S 变化的日期。NEW A121 有两个匹配的运营日，保留 2 次定数变化的区间。X-VERSE-X A242 暂缺可用运营日，保留 3 次 C2S 变化的区间。SUN PLUS A000 的 49 次定数变化属于晚构建的基础盘，继续保留范围。

构建器会自动执行该规则。已发布的精简库也可就地增量更新，完成完整性和外键检查后才提交事务。

```powershell
python -X utf8 tools/jp_option_dates.py .tools/jp-hdd/compact/catalog.sqlite
```

增量后的 SQLite 为 549,507,072 字节，SHA-256 为 `26e27acfbe17fd3415cf0d17423f8495d308da01a5efd0fe3f8cadff0e5102cc`。

随后按用户指定日期对剩余 54 条谱面修订补入生效日。NEW A121 的《凛として咲く花の如く》EXP、MAS 定数采用 2021-12-09。SUN PLUS A000 的 49 条定数变化采用开服日 2023-05-11。X-VERSE-X A242 的 3 条 C2S 修订暂用开服日 2025-12-11。原时间范围留在事件依据，人工日期与运营证据各自保留。

本地曲目生命周期仍记录《凛として咲く花の如く》普通曲 2016-08-25 首配、从 2021-01-21 起在 PARADISE 留存截点缺档、2021-12-05 复配。该曲 ULTIMA 独立记录 2021-12-09 首配。

本次增量后的 SQLite 为 549,560,320 字节，SHA-256 为 `9b3b7cbcd031f355687a408e4a85d5786f230d13d203b42f643a18d1916c41de`。

## 换代停配推定

上一代留存曲库含某首可玩曲、下一代留存曲库找不到对应身份或 Music ID 时，以新版本开服日记一条推定停配。曲库中仍存在相同 ID 或关联身份时跳过。已明确的停配和复配日期优先。事件以 `JP version boundary removal inference` 标明依据，并保留原盘来源及恢复查询窗口的界限。

本地库新增 283 条推定。其中《凛として咲く花の如く》普通曲在 CRYSTAL PLUS 存在、在 PARADISE 缺席，停配推定为 2021-01-21。已有复配日 2021-12-05 继续采用数据库中归档的证据。

```powershell
python -X utf8 tools/jp_version_removals.py .tools/jp-hdd/compact/catalog.sqlite
```

增量后的 SQLite 为 549,957,632 字节，SHA-256 为 `f4728cf4cd35c2c5a448e06a8aa57720ab7687d169c9867a7c45e9b608c164e5`。

SUN PLUS A000 的整包采用日期后来设为 **2023-05-11**。该基础盘共 1,437 条已定日的内容变更字段，其中 49 条定数变化此前已设为同一天。原盘构建日期和盘内观察依据仍记录为 2023-05-31。

当前 SQLite 为 550,182,912 字节，SHA-256 为 `3a6f224cd6b8e001b5fd6581babb336af9037fa749f53ab5444e7b08c6921094`。

## 首配证据复核

- 开服推定要求 A000 正常启用的普通曲出现在实际开服曲目开放事件中。ID、曲名和开放标签需要对应，事件原盘路径及哈希须能验证。同一个 A000 批次内有可用 C2S 的普通谱面可以继承该日。NEW 的 Music ID 2039 据此采用 2021-11-04，来源是 `event00010012/Event.xml`。该事件不提供后续通常解禁日。
- 曲名与 Music ID 一致、音源引用相同、四张普通谱面都有唯一匹配的记谱指纹时，可从已记录首配的关联身份继承日期。保留谱面身份、定数、修订、首个本地物证和停复配记录。不要以较早的继承日期给后来的 HDD 版本生成新的可玩窗口。
- 从本地单曲页归档核对了《ゴー・トゥ・大都会》2023-06-08、《Λlteration》2023-10-12，以及 Daydream café 的 2017-03-09 通常配信。Daydream café 的 2017-02-09 联动先行配信仍为首次开放。Λlteration 的本地谱面物证始于 LUMINOUS，四张谱面的原始追加日期仍待确认。
- 增量共补 14 个曲目身份和 52 张普通谱面记录，包含 2 个仅见于早期批次的曲目身份。上一轮口径下的首配缺口由 17 个普通曲身份及 88 张谱面降至 5 个及 36 张。剩余曲目为 Alice's Suitcase、ぱくぱく☆がーる、進め！マイウェイ！、反撃! 突撃! Back To Back! 和 GEOMETRIC DANCE。

```powershell
python -X utf8 tools/jp_release_reconcile.py .tools/jp-hdd/compact/catalog.sqlite 'E:\SDHD'
```

本次增量后的 SQLite 为 550,264,832 字节，SHA-256 为 `e3fd167b4adb02331d9c71876a67dfcafffa4702bf29a7b4a56827ac2a0a8caf`。
