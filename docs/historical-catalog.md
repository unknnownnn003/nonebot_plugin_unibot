# CHUNITHM 离线历史曲库

历史库是独立的公共曲库索引。首个发布覆盖日服初代至 Mate 的 22 个历史截面，另含 JP、INTL、CN 的独立当期观察。运行时只读 SQLite。现有国服 Catalog、OAuth、/b30、/b50 和 /旧框 照常使用原实现。

## 准备原件并构建

默认的 [来源锁](../tools/history_inputs.json) 列出每份输入的本地相对路径、公开 URL、SHA-256、上游版本和观察日。本次调研保留了全部原件。路径可以换成另一个私有目录。Windows 示例使用 PowerShell。

~~~powershell
$sourceDir = Join-Path ([System.IO.Path]::GetTempPath()) 'unibot-chunithm-research-20261121'
$historyDir = Join-Path $PWD '.tools/history-catalog'

python -X utf8 tools/build_history_catalog.py --sources $sourceDir --output $historyDir
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

数据表 schema 和版本号在 [history_schema.sql](../tools/history_schema.sql)。升 schema 时重新从锁定原件构建新包。旧包保留在 releases 中作为回滚点。读取端拒绝未知 schema，不自动改写旧数据库。默认本地验证位置用 .tools，未来部署时管理员可显式构建到 NoneBot LocalStore 数据目录的 historical_catalog。首次导入插件不会构建或激活历史库。

可额外物化一个证据范围内的日服日期。

~~~powershell
python tools/build_history_catalog.py --sources $sourceDir --output $historyDir --checkpoint JP NEW 2022-01-20 --stage-only
~~~

检查、激活及回滚均通过发布 ID 完成。--stage-only 不改 CURRENT；--activate 先像 --check 一样完整校验包。查询实例会固定当时指向的包。不要原地覆盖 catalog.sqlite 或随查询删除旧包。

~~~powershell
python tools/build_history_catalog.py --output $historyDir --activate <release-id>
~~~

## 读取契约

已加载 UniBot 时可以直接导入只读类，显式传入包根目录、单个发布目录或 SQLite 文件。省略 path 时使用 LocalStore 的历史目录。工具侧可以复用已存在的 tools/import_paradise_lost.py 对纯 services 模块的独立加载方式。

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
- 返回区分 song_state、chart_state 和两者结合的 state。仅知版本的定数在该代已证截面才投影，同代较早时间如果遇到未确认的调整窗口就保持未知。删除端点语义不明时留下未知日。Daydream café 的再下架和 ULTIMA 实装有额外曲目页证据。COUNT 仅覆盖导入的观察，historical_population 留空。
- constant_kind 为 exact、display_only、estimated、unknown、conflict 或 not_applicable。十分位整数通过 Decimal 暴露为 constant。trusted_constant 还要求存在可信的可玩性和定数证据。WE 的常规定数永远为空。trusted_constant 仅表示所查目录里的证据条件，不认证个人成绩映射、历史谱面内容或 Rating 就绪。
- Song、Chart 和 Revision 各有独立 ID。历史曲谱通常只有版本定数证据，其 ID 是历史检索锚点，identity_basis 会标 unverified。跨源或跨区同谱映射只有候选、冲突和兼容旧框指纹三种状态。get_constant_changes 默认只列修订身份已证实的变化。include_unverified=True 可比较历史谱面槽的已知定数，返回 revision_verified=false。历史库不会将同一曲的不同地区谱面自动认作同一修订。
- find_charts 返回所有同名候选。get_external_ids(region, source, song_key) 可查看外部原始键与无歧义程度。get_chart_mappings(revision_id) 返回映射证据。get_jackets(song_id) 只返回索引。get_observation 结合原始位置、来源 URL、SHA-256、适用时间、质量和字段类型。规则争议可沿 get_conflicts 返回的 ruleset_candidates 查询 get_ruleset。
- OTOGE 的 LUMINOUS PLUS 串代候选保留在 observation 和 conflict，决议只拒绝污染来源。其他定数争议从观察点起阻断同一争议观察的后续有效区间，直到新的数值证据区间开始。LOST 的 MAX-eipi 历史表原证据为 11 月 2 日，补到 11 月 3 日的推定标 C，原有黄金榜保持独立。

## 增量更新与审核

保留当前来源锁，复制为私有的下一版锁，并把新原件保存成独立文件。只在已核对新日服时光机数据与日历后更新相应的固定输入，必要时更新日服观察截止。默认锁使用研究日服锚点 2026-10-31；11-21 的日服现行观察单独入库。保留旧来源原件、锁文件和发布包，便于审核差异及重现旧结果。

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

## 日服历史定数补全与差异导出

B2 从锁文件构建同一套 SQLite schema。CRYSTAL PLUS 和 PARADISE 使用 MAX-eipi 在版本末日的固定提交。从 PARADISE LOST → NEW 到 X-VERSE-X → MATE 的有名列对照表使用前代列核对前代末，后代列按各自的日期证据入库。历史匹配限于已建档的日服谱面槽，要求曲名无歧义、曲目在目标日可玩、谱面无已知反证。可用的历史槽仍可能缺少内容版本的身份凭证，跨地区映射审核沿用原规则。

旧调查文章各自保留观察日，且只在该日曲目和谱面状态均可确认时登记同期 B 级观察。文章给出的版本末候选保留 C 级。初代调查作者声明的近似误差保留为估算。MATE 以当天官方曲号、曲名、曲师及难度匹配 ChuniSupport 与 CHUNITHM Info，再核对 CN10 工作簿或有谱面物量的 Wiki 数据。Wiki 低等级表引用 CN10，这两项证据共用一条来源链。显示等级规则仅能在锁定的版本、日期和证据范围内产生 `rule_derived` 值。

前代 A/B 定数有新异议时，保留原值和原来源，将异议登记在 `conflict` 供审核。已有未决冲突和 OTOGE LUMINOUS PLUS 的隔离结论继续生效。

```powershell
$historyDir = '.tools/history-catalog'
$sources = '.tools/history-b2/sources'
$before = (Get-Content "$historyDir/CURRENT" -Raw).Trim()
$staged = python tools/build_history_catalog.py --sources $sources --output $historyDir --stage-only
python tools/export_history_constants.py --catalog $staged --baseline "$historyDir/releases/$before" --output .tools/history-b2/results
```

`check.json` 记录基线、发布 ID、CSV 校验值及原可信值的差异。`verified_constants.csv` 和 `newly_verified_constants.csv` 按版本及内部谱面 ID 给出观察证据。`missing_constants.csv` 保留完全未知、仅显示等级、估计、冲突以及身份或时间证据不足的各自状态。CSV 的候选观察 ID 可用 `get_observation` 查值及来源。`constant_conflicts.csv` 每候选证据一行，同一个 `conflict_id` 可能有多行。`coverage_by_version.csv` 按难度列出确定可玩谱面及额外待确认谱面槽，可信占比的分母为确定可玩谱面，真实历史分母字段留空。闭合版本取后继首日前一天，MATE 取最近有证据的观察日。

核对差异和回归测试后，用 `--activate <release-id>` 选择新包。用同一个命令传入 `$before` 可恢复上一包。原件、SQLite 和来源导出单独保存在本地。可变网页和工作簿的在线重新下载可能已经改变字节，离线重建用通过锁文件 SHA-256 验证的原件。

原始入口包括 [MAX-eipi 提交历史](https://github.com/MAX-eipi/ChunithmMusicDataTable/commits/master/index.html)、[历代工作簿索引](https://chunithm.org/)、[RKS 同期调查](https://chunithmfanclub.hatenablog.com/archive)、[MATE CN10 对照工作簿](https://docs.google.com/spreadsheets/d/1XK3_hjMlJk1YXLMRbSNAnZdVrGh986Qp5X0rXc2qSHQ/edit)、[CHUNITHM Info 定数表](https://chuni.info/const-table?lang=ja) 和 [SEGA ULTIMA 首发公告](https://info-chunithm.sega.jp/1151/)。

## Rating 接入状态与分发

本阶段的历史库未开放新的 Rating 查询。PARADISE LOST 标 legacy_reference_only，现有 /旧框 仍用固定 762 首与 3,048 张指纹映射。VERSE 已有 2025-07-15 截面、互斥新旧池规则证据和双代定数对照。后续开放前需要审核官方逐曲 New 资格、目标版谱面等价、剩余定数冲突、低分段和机台取整。其余版本依赖各自的数据证据，X-VERSE 及后续目前只属于曲库建设范围。

原件与生成数据包含许可不明确或限制再分发的来源。manifest 标 local-only，严禁把其完整数据默认提交 Git。公开仓库只保留代码、规范、锁定来源和合成测试。曲绘条目按 link_only 记录，尺寸和哈希只写入已取得测量的少量样本，不下载全量曲绘。OTOGE 代码 MIT 与曲绘/数据权利分开。ChuniSupport 公开 API 有非营利限制。
