# 日服历史库配信日期审计

以 `030e13c` 和 `.tools/jp-hdd/image-evidence/catalog.sqlite` 为起点。原数据库及两套 HDD 保留。新版保留 v3 只读接口，研究数据库位于 `.tools/jp-hdd/wiki-dates/published/catalog.sqlite`。

## 日期口径

- 首配采用公告写明的追加日。地图、联动、课程、活动及先行解禁使用对应的官方开放方式。通常解禁、停配、复配、盘内预载和玩家个人解禁各有独立含义。Wiki 只提供公开运营事件，个人完成条件的日期没有入库。
- 普通曲的 BAS、ADV、EXP、MAS 使用同一首配日。继承使用生命周期父身份和第一可核验版本里的 C2S，允许分布在不同 OPTION 或 Music.xml。即使首份本地物证晚于配信，已核实的日期仍可以继承，谱面版本与可用窗口保留原有证据范围。
- ULTIMA 和 WE 按独立谱面证据确定。WE 要求独立曲目和属性吻合。定数、等级、记谱、作者和其他资源以 HDD 数据为准。
- 页首只有「通常配信」时，保留它为候选，除非有相符的 HDD 日期且没有已见的更早路线，或有直接公告。已有明确较早配信时，后来的通常开放单独入库。重复导入保留原证据和判定。缺年份不补猜。

## 交叉核查

`玩具狂奏曲 -終焉-` 页首是 2017-07-06 通常配信，历史段记载 2016-12-15 首次追加及地图课题。旧首配日期可以保留，通常开放单独索引。 [Wiki 曲目页](https://wikiwiki.jp/chunithmwiki/玩具狂奏曲%20-終焉-)

`Trrricksters!!` 普通曲 Wiki 单曲页写 2020-03-04，正好是 SEGA 文章发布日期。SEGA 明确写明 2020-03-05 上线，Wiki 更新页也指向 03-05。保留两个日期的原始证据，普通曲选 03-05。独立 WE「狂☆5」于 2021-11-04 随 NEW 的 aWEsome!!!! 课程开放，后续一般开放独立于课程首配。 [SEGA 地图公告](https://chunithm.sega.jp/news/2020-03-04/) · [Wiki 更新页](https://wikiwiki.jp/chunithmwiki/楽曲追加のお知らせ%282020.3.5%29) · [NEW 课程记录](https://wikiwiki.jp/chunithmwiki/CHUNITHM%20NEW情報まとめ)

`革命ダンス` 的 Wiki 页首写 2025-05-08 通常解禁。SEGA 当日联动公告明确将其列为追加，原有 VERSE 首配日期得到印证。另一份 X-VERSE 的同名 HDD 生命周期身份仍需确认。 [SEGA 追加公告](https://info-chunithm.sega.jp/9657/) · [Wiki 曲目页](https://wikiwiki.jp/chunithmwiki/革命ダンス)

`DataErr0r` 与 `Parousia` 的 ULTIMA 于 2026-03-19 通过 Secret Area 添加。一般开放记录独立，普通谱面的日期与之无关。 [Wiki 更新页](https://wikiwiki.jp/chunithmwiki/楽曲追加のお知らせ%282026.3.19%29)

## 采集、重建和复核

```powershell
$work = '.tools/jp-hdd/wiki-dates'
$base = '.tools/jp-hdd/image-evidence/catalog.sqlite'
python -X utf8 tools/build_jp_wiki_dates.py $base "$work/wiki-cache.json" "$work/rebuilt"
python -X utf8 tools/verify_jp_hdd_history.py "$work/rebuilt/catalog.sqlite" `
  .tools/old-hdd/lifecycle-dates-final/catalog.sqlite 'E:\SDHD' --allow-unencrypted-sample
```

输出目录里的 `catalog.sqlite` 要求不存在，构建通过完整性与外键检查后发布。加 `--collect` 可逐页补采缺口与仅有盘内日期的对象，已有缓存直接复用。加 `--refresh` 可重查这些对象。采集按固定站点、robots.txt 和串行请求间隔运行，收到拒绝或限流后暂停。当前环境直连返回 403，实际导入使用公开网页索引的源码或渲染摘录。摘要只覆盖归档行，`jp_wiki_pages` 中的哈希以实际归档为准。

- `wiki-cache.json` 保存 URL、页名、取得时间和原始摘录。增量构建可离线运行。
- `jp_wiki_dates` 保存曲目或谱面身份、日期原文、章节、证据行与审核状态。无法匹配的日期片段放在 `jp_wiki_unmatched`。`jp_wiki_review` 保存页面采集或解析状态。
- `jp_date_evidence` 将现有 HDD 日期、生命周期事件和新证据一起暴露给只读 SQL 查询。已有 `first_release()`、`checkpoint()` 和 `catalog_on()` 继续使用原有接口。运营事件不赋予未知历史版本的 C2S 可用性。
- `remaining.csv` 列出来源、身份、首个 HDD 物证、候选日期、审核原因及进一步的核查入口。`date-review.csv` 列出冲突、缺年、身份歧义和原始证据。`audit.json` 记录实际数量。`tools/jp_reviewed_dates.json` 保留额外核对的公告或 Wiki 更新表条目。



## 本轮覆盖与验收

| 对象 | 基线缺精确日期 | 新版缺精确日期 |
| --- | ---: | ---: |
| 普通歌曲 | 82 | 20 |
| BAS～MAS | 464 | 92 |
| ULTIMA | 10 | 0 |
| WORLD’S END | 6 | 0 |

普通谱面基线包含新框索引的 456 条及仅见于旧框的 8 条。新版补齐 62 首普通曲和 372 张普通谱面，其中上一轮 25 首活动曲和 5 首地图曲的 120 张普通谱面均已对齐。10 张 ULTIMA 都有 Wiki 日期证据，其中 8 张来自单曲页，2 张来自更新页。6 张 WE 中 5 张使用 SEGA 直接公告，1 张使用 Wiki 课程及 HDD 本体证据。

共找到并解析 80 个 Wiki 单曲页。12 个候选未取得可核验的详情页，另有 8 个日期片段未能匹配到唯一可用的曲谱身份。单曲页提取 17 条明确追加、12 条先行、23 条通常解禁及 21 条停配或复配记录。12 条日期与已索引的官方首配一致。1 个普通曲日期冲突由 SEGA 公告裁决，原始 Wiki 证据保留。另有 22 条按对象展开的同名身份候选和 5 条缺完整日期记录。没有证实需要改写的既有通常解禁误填；`革命ダンス` 的同日表述已用公告核验。

22 代均运行数据库规则审计。发现 3 个官方确认的多配信日 OPTION，以及 3,151 条本地观察下界早于已知首配的记录。7 张与普通曲同日且采用 HDD 候选的 ULTIMA，以及 87 张与其独立 WE 曲条目同日且采用 HDD 候选的 WE，保留既有日期及来源，仍可逐条复核。日期审核未重扫全部 Wiki 单曲页，也未把 2,164 次新框定数变化、1,307 次显示等级变化及 C2S 修订的版本范围推定为运营日。

SQLite 完整性、外键检查、22 代内容快照和两套原盘的 C2S 每代抽样通过。LUMINOUS 抽验使用可读的基础盘样本。全量测试 174 项，163 项通过、11 项按原条件跳过。邻仓契约、26 项原生命令能力检查及编译检查通过。

相同输入重复构建的数据库 SHA-256 一致。

```text
d38109aecf8c41ee30e52fa1bcff1d5c84d02ec9cd3473dcc6daee24b1b9ebfd
```


逐表比对确认 39 张非日期基线表完全相同。按 22 个版本截点分别查询三次，再比较各截点中位数，catalog_on 耗时由 175.3 ms 到 181.2 ms（约 +3.4%）。查询记录和原盘抽样见本地 verification.json 和 verification-extra.json。
