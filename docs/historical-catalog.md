# CHUNITHM 全版本曲库（Versioned Catalog）

全版本曲库提供独立的只读 SQLite，用于查询版本谱面（Version Chart）、版本定数（Version Constants）和版本快照（Version Snapshot）。B5 覆盖日服初代至现行 MATE，共 22 代，另保存 INTL 和 CN 的独立观察。国服查歌、OAuth、`/b30`、`/b50` 和 `/旧框` 继续沿用各自的数据入口。

## 日服版本日历

日历采用日本时间。换代首日属于新版本，前代末日是换代前一天。

| 版本 | 首日（JST） | 末日（JST） | 开服证据 |
|---|---|---|---|
| CHUNITHM | 2015-07-16 | 2016-02-03 | [SEGA 官方](https://www.sega.jp/topics/detail/151228_arcade_1/) |
| PLUS | 2016-02-04 | 2016-08-24 | [SEGA 官方](https://www.sega.jp/topics/detail/160204_arcade_2/) |
| AIR | 2016-08-25 | 2017-02-08 | [SEGA 官方](https://www.sega.jp/topics/detail/160825_arcade_1/) |
| AIR PLUS | 2017-02-09 | 2017-08-23 | [SEGA 官方](https://www.sega.jp/arcade/detail/chunithm-air-plus/) |
| STAR | 2017-08-24 | 2018-03-07 | [SEGA 官方](https://www.sega.jp/topics/detail/170824_arcade_1/) |
| STAR PLUS | 2018-03-08 | 2018-10-24 | [SEGA 官方](https://www.sega.jp/topics/detail/180308_arcade_1/) |
| AMAZON | 2018-10-25 | 2019-04-10 | [SEGA 官方](https://www.sega.jp/topics/detail/181025_arcade_3/) |
| AMAZON PLUS | 2019-04-11 | 2019-10-23 | [SEGA 官方](https://www.sega.jp/topics/detail/190411_arcade_1/) |
| CRYSTAL | 2019-10-24 | 2020-07-15 | [SEGA 官方](https://www.sega.jp/topics/detail/191024_5/) |
| CRYSTAL PLUS | 2020-07-16 | 2021-01-20 | [SEGA 官方](https://www.sega.jp/arcade/detail/chunithm-crystal-plus/) |
| PARADISE | 2021-01-21 | 2021-05-12 | [SEGA 官方](https://chunithm.sega.jp/news/2021-01-21/) |
| PARADISE LOST | 2021-05-13 | 2021-11-03 | [SEGA 官方](https://www.sega.jp/topics/detail/210513_2/) |
| NEW | 2021-11-04 | 2022-04-13 | [SEGA 官方](https://www.sega.jp/arcade/detail/chunithm-new/) |
| NEW PLUS | 2022-04-14 | 2022-10-12 | [SEGA 官方](https://info-chunithm.sega.jp/1820/) |
| SUN | 2022-10-13 | 2023-05-10 | [SEGA 官方](https://info-chunithm.sega.jp/3043/) |
| SUN PLUS | 2023-05-11 | 2023-12-13 | [SEGA 官方](https://info-chunithm.sega.jp/4841/) |
| LUMINOUS | 2023-12-14 | 2024-06-19 | [SEGA 官方](https://info-chunithm.sega.jp/6208/) |
| LUMINOUS PLUS | 2024-06-20 | 2024-12-11 | [SEGA 官方](https://info-chunithm.sega.jp/7346/) |
| VERSE | 2024-12-12 | 2025-07-15 | [SEGA 官方](https://info-chunithm.sega.jp/8704/) |
| X-VERSE | 2025-07-16 | 2025-12-10 | [SEGA 官方](https://info-chunithm.sega.jp/10025/) |
| X-VERSE-X | 2025-12-11 | 2026-07-01 | [SEGA 官方](https://info-chunithm.sega.jp/10941/) |
| MATE | 2026-07-02 | 现行 | [SEGA 官方](https://info-chunithm.sega.jp/12155/) |

MATE 最近一次锁定观察为 2026-11-21，时光机输入范围截至 2026-10-31。中间缺少锁定快照的日期会明确报无可用观察。PARADISE LOST 的末日指日服主线换代，旧框机台的联网服务另有安排。 [MATE 观察日运营公告](https://info-chunithm.sega.jp/13533/) · [旧框联网结束公告](https://info-chunithm.sega.jp/4017/)

## 准备原件并构建

默认的 [来源锁](../tools/history_inputs.json) 列出每份输入的本地相对路径、公开 URL、SHA-256、上游版本和观察日。本次调研保留了全部原件。路径可以换成另一个私有目录。Windows 示例使用 PowerShell。

~~~powershell
$sourceDir = Join-Path $PWD '.tools/history-b5/sources'
$historyDir = Join-Path $PWD '.tools/history-catalog'

$staged = python -X utf8 tools/build_history_catalog.py --sources $sourceDir --output $historyDir --stage-only
python -X utf8 tools/build_history_catalog.py --output $historyDir --activate (Split-Path $staged -Leaf)
python -X utf8 tools/build_history_catalog.py --output $historyDir --check
$env:UNIBOT_TEST_HISTORY = $historyDir
$env:PYTHONUTF8 = '1'
python -B -m unittest discover -s tests -p test_history_catalog.py -v
~~~

显式传入 --fetch-missing 可获取缺失的固定公开原件，下载字节须匹配锁中的哈希。会变化的 API、Google Sheet 和公告页面即使 URL 仍在也可能无法重取原字节。保留原件，或者按下文登记新观察。旧日期不能配新下载数据。构建不读取配置目录、用户数据、状态库或 Token。除可选下载使用仓库现有的 httpx 外，构建与读取仅使用 Python 标准库。

输出目录结构如下。

~~~text
historical_catalog/
  CURRENT
  releases/<release-id>/
    catalog.sqlite
    manifest.json
    coverage.json
~~~

CURRENT 只保存发布 ID。构建先在同一文件系统的临时目录写 SQLite、报告和 manifest，验证 schema、外键、区间、哈希和旧框桥接数量，再安装不可变目录。安装后原子更新 CURRENT。相同输入、工具与目标截面生成相同发布 ID，写入顺序固定。同版 SQLite 在此基线上可重建相同包字节，SQLite 版本不同允许物理布局变化。

数据表 schema 和版本号在 [history_schema.sql](../tools/history_schema.sql)。升 schema 时重新从锁定原件构建新包。旧包保留在 releases 中作为回滚点。读取端拒绝未知 schema，旧库始终按原件保留。默认本地验证位置用 .tools，未来部署时管理员可显式构建到 NoneBot LocalStore 数据目录的 historical_catalog。首次导入插件不会构建或激活历史库。

可额外物化一个证据范围内的日服日期。

~~~powershell
python tools/build_history_catalog.py --sources $sourceDir --output $historyDir --checkpoint JP NEW 2022-01-20 --stage-only
~~~

检查、激活及回滚均通过发布 ID 完成。`--stage-only` 仅生成候选包，`--activate` 会先完整校验再切换。查询实例会固定当时指向的包。不要原地覆盖 catalog.sqlite 或随查询删除旧包。

~~~powershell
python tools/build_history_catalog.py --output $historyDir --activate <release-id>
~~~

## 读取契约

已加载 UniBot 时可以直接导入只读类，显式传入包根目录、单个发布目录或 SQLite 文件。省略 path 时使用 LocalStore 的全版本曲库目录。工具侧可以复用已存在的 tools/import_paradise_lost.py 对纯 services 模块的独立加载方式。

~~~python
from nonebot_plugin_unibot.services.history import HistoryCatalog

catalog = HistoryCatalog()
verse = catalog.get_snapshot("JP", "VERSE")
daydream_candidates = catalog.find_charts("Daydream café")
chart = next(row for row in daydream_candidates if row["difficulty"] == "ULT")
history = catalog.get_chart_history(chart["id"], "JP")
song_history = catalog.get_song_history(chart["song_id"], "JP")
provenance = catalog.get_observation(history["events"][0]["observation_id"])
conflicts = catalog.get_conflicts("JP", "VERSE")
coverage = catalog.get_version_coverage("JP", "VERSE")
changes = catalog.get_constant_changes("JP", "LUMINOUS PLUS", "VERSE", include_unverified=True)
~~~

- region 与 version 使用库内标识。JP 例如 CHUNITHM、PLUS、AIR PLUS、PARADISE LOST、LUMINOUS PLUS、VERSE、MATE。INTL 有 X-VERSE-X 和 MATE 观察，CN 有 CN2027 观察。地区代号独立。版本可通过 coverage.json 的 versions 字段列出。省略日期选该版本已发布的最后观察。指定 date 接受 ISO 日期或 datetime.date。JP 可在已知范围逐日查询，INTL 和 CN 只开放锁定的观察日。缺少观察或日期越界抛出 SnapshotUnavailable。
- 返回区分 song_state、chart_state 和两者结合的 state。通过审核的同期精确定数可在同版、同谱面修订的可用区间内继承。实装、停配、复归单独判定。缺少跨代证据的值保持 UNKNOWN。删除端点语义不明时留下未知日。Daydream café 的再下架和 ULTIMA 实装有额外曲目页证据。COUNT 仅覆盖导入的观察，historical_population 留空。
- constant_kind 为 exact、display_only、estimated、unknown、evidence_insufficient、conflict 或 not_applicable。界面使用 `constant_label` 区分数值、`UNKNOWN`、`CONFLICT`。十分位整数通过 Decimal 暴露为 constant。trusted_constant 还要求存在可信的可玩性和定数证据。WE 的常规定数永远为空。trusted_constant 仅表示所查目录里的证据条件，不认证个人成绩映射、历史谱面内容或 Rating 就绪。
- Song、Chart 和 Revision 各有独立 ID。历史曲谱通常只有版本定数证据，其 ID 是历史检索锚点，identity_basis 会标 unverified。跨源或跨区同谱映射只有候选、冲突和兼容旧框指纹三种状态。get_constant_changes 默认只列修订身份已证实的变化。include_unverified=True 可比较历史谱面槽的已知定数，返回 revision_verified=false。同曲不同地区的谱面保留各自身份。
- find_charts 返回所有同名候选。get_external_ids(region, source, song_key) 可查看外部原始键与无歧义程度。get_chart_mappings(revision_id) 返回映射证据。get_jackets(song_id) 只返回索引。get_observation 结合原始位置、来源 URL、SHA-256、适用时间、质量和字段类型。规则争议可沿 get_conflicts 返回的 ruleset_candidates 查询 get_ruleset。
- B5 移除了 OTOGE 输入。退役证据、支持关系及旧争议仍可通过 B2 包和 B5 对照表追溯。同代可信数值不一致时，该修订在对应版本显示 `CONFLICT`，候选及其出处保留。PARADISE LOST 的 MAX-eipi 表保留 11 月 2 日的真实观察，版本末谱面状态满足条件时沿用精确值。

## 增量更新与审核

保留当前来源锁，复制为私有的下一版锁，并把新原件保存成独立文件。只在已核对新日服时光机数据与日历后更新相应的固定输入，必要时更新日服观察截止。默认锁使用研究日服锚点 2026-10-31，11-21 的日服现行观察单独入库。保留旧来源原件、锁文件和发布包，便于审核差异及重现旧结果。

可以追加原生格式的当期捕获。给 artifacts 增加唯一 ID、原件路径、SHA-256、来源、日期和上游版本，再在 extra_observations 指定地区、版本、catalog，以及 JP/ CN 所需的 companion。JP 的 companion 是同日 ChuniSupport WE 兼容导出，CN 是同日落雪 site/config。CN 的 upstream_version 使用公开资源版。INTL 日期来自 ChuniMaru 文档的生成时间，version 校验该响应的游戏版本。追加日期必须晚于同地区已知截面。原始基准捕获继续存在，新的捕获不会回填中间日期。

~~~json
{
  "extra_observations": [
    {
      "region": "CN",
      "version": "CN2027",
      "catalog": "cn-later-capture",
      "companion": "cn-later-config"
    }
  ]
}
~~~

跨大版本的地方版新增在 regional_releases 中声明 region、version、previous_version、start_on、timezone、artifact 和 locator，证据 artifact 必须锁定。构建器要求前代区间开放并在新代首日排他结束，重叠或跨边界快照拒绝。新版未实装日期、曲号重用或改名导致的身份不一致需要审核。构建失败或输入损坏时，CURRENT 仍指向上一个经过验证的发布。

审核时检查 coverage.json 的逐地区版本数据及 conflict 候选，对比新增日期和变动，依赖同一上游的值只算一批证据。禁止以最新常定数覆盖旧记录，也不要将锁定的推测升级为精确。审批完毕后用 --activate 原子切换。

## B5 差异导出

用现有 B2 原件和 B5 新增锁定证据组成私有来源目录。默认锁的路径均相对于该目录。版本结束取下代首日前一天，现行版取最后一个锁定观察。导出保留稳定 ID、原始来源位置、可玩性、定数可信度及版本前后差异。

```powershell
$historyDir = '.tools/history-catalog'
$before = '781ba62c2dd8ebbf0f963e3389cadb5a86b343b6f0c82f477487031f02e59d27'
$after = (Get-Content "$historyDir/CURRENT" -Raw).Trim()
python -B -X utf8 tools/export_versioned_catalog.py --baseline "$historyDir/releases/$before" --catalog "$historyDir/releases/$after" --output .tools/history-b5/results
```

`acceptance.json` 保存发布 ID、统计范围和七张 CSV 的校验值。逐版对照、冲突明细、ULTIMA 实装审核和逐谱缺口均由这两份已经校验的只读发布生成。报告列出国服验收、P0/P1/P2 和 Rating 开发前提。 [B5 验收报告](B5-全版本曲库纠偏与剩余问题报告.md)

## Rating 接入状态与分发

指定版本 Rating 模拟（Version Rating Simulation）留待阶段 C。现有 `/旧框` 使用固定 762 首与 3,048 张指纹映射。国服中二节奏 2027 和日服 X-VERSE-X 按两个版本的更新结束状态对应。日服目标端点为 2026-07-01，CN 的起止与配信使用独立时间线。可通过 `get_version_correspondences()` 读取版本关系，通过 `get_chart_mappings()` 审阅候选。曲号、曲师、标题和物量相符的记录仍需要逐谱内容核验。

原件、发布包、工作簿衍生数据和逐谱 CSV 存于本地。公开仓库保留构建器、合规来源锁、测试及汇总报告。回滚时对同一根目录用 `--activate` 传入旧发布 ID。
