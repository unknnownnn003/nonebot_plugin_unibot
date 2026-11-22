# 日服 HDD 告知图片与首次配信日期

本轮从 `7b3ac24` 和 `.tools/jp-hdd/date-mechanism/rebuilt/catalog.sqlite` 开始。Git 拉取后，本地与远端 `main` 的提交一致。两棵 HDD 目录保持只读。普查包括盘目录、已展开 OPTION、ZIP 成员目录、RAR 成员清单和 VHD 容器条目。VHD 中已展开部分按目录统计，VHD 文件本身单列。

## 资源分布

以下为路径及成员数，同一图片在多个包出现时分别计数。完整的目录、格式、加密及缺索引分布见本地 `inventory.json`。

| 范围 | 基础包 | OPTION | 自制包 | 其他目录或独立补丁 | 合计 | 不在原库 `sources` 图片清单 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| SDBT DDS | 33,370 | 8,896 | 0 | 1,428 | 43,694 | 1,428 |
| SDHD DDS | 108,057 | 8,380 | 2,375 | 2,780 | 121,592 | 28,247 |
| SDHD PNG | 165 | 26 | 26 | 130 | 347 | 345 |

SDBT 清单有 11 个 VHD。SDHD 有 67 个 VHD、25 个 ZIP、5 个 RAR。旧框原库未登记的独立 DDS 集中在 `texture`（1,384）和 `dds`（44）。新框差额主要来自 `avatarAccessory`、`namePlate`、`texture`、`rightsInfo`、`mapIcon`、`systemVoice`，还包含独立 2.47 ZIP 补丁中的 24 个 DDS。自制包只列入资源普查。SDHD ZIP 清单有 3,312 个加密图片成员，其中 157 个落在本次告知候选范围。LUMINOUS 2.20 找到 15 个 `CHU_info_music*` 路径，缺少可读成员的凭据，预览使用同系列 LUMINOUS PLUS 实盘作为补充。

AFB `FILS` 和 SVO `AVTS` 还包含嵌入 DDS。本次对 9,859 个官方范围的候选容器查找有效 DDS 头，读到 9,836 个容器里的 48,131 个纹理头。22 个成员需要 ZIP 凭据，1 个超过单容器读取上限。`embedded.json` 有容器 SHA-256、DDS 偏移、尺寸和 FourCC。人工抽样见舞台图、特效和判定线。`texlist` 直接列出纹理名，SRD `VTBF` 物证及 AFB/SVO 布局支持组合界面的存在。缩略图上的曲目及日期在所核对的更新 DDS 中已经栅格化。USM、程序、未知编码的封装及 VHD 裸扇区未做全量图片雕刻，不能据此估算里面的告知数量。

候选队列用文件名和目录作召回，覆盖 12,147 个物理候选，成功解码 **3,866 个不同 SHA-256**。其中 **1,138** 个有 `CHU_info_` 命名，**382** 个有 `CHU_info_music` 命名，**1,404** 个在现有库中有路径及摘要同时相符的资源引用。`info_music` 也用于期限票券，`info_event` 也用于只发奖励的地图。未逐张审阅的项目保持候选状态。 `CHU_info_` 批量 OCR 已处理 1,138 张，323 张含可解析日期，501 张命中至少一行完整曲名，240 张同时命中同批 HDD 对象，169 张同时有可解析日期。255 张命中至少一个曲目追加、ULTIMA、WE、通常开放、恢复或停配关键词。关键词结果均留待复核。缓存复跑新增解码和识别均为 0。

## 样本、独立验证和误判

本地 `editions.jpg`、`editions.json` 提供早期和末期旧框、NEW、SUN、LUMINOUS PLUS、VERSE、X-VERSE、X-VERSE-X、Mate 的实际预览和哈希。旧框 `info_music_new0005` 有曲目和 NEW 角标，`new0204` 有四张独立 WE 曲绘及标题，两幅均未印日期。NEW `new0212`、SUN `sun001` 均把星期印在主标题，LUMINOUS PLUS 和后续取样通常省去星期。Mate `mate0009` 同图列两个普通曲及 `きゅうくらりん` 的 ULTIMA。

23 张按官网曲目及生效日独立核读的图用于回顾性验证，预先固定规则为整图 OCR、NFKC 完整行匹配，分类按关键词。实际结果为日期月日命中 **17/23（73.9%）**，期望类型召回 **16/23（69.6%）**，完整曲名 **30/48（62.5%）**。显式 ULTIMA 标签和曲名同时命中 **1/1**，单例不足以估算独立谱面准确率。此组为 X-VERSE-X 和 Mate 的正样本，未随机抽样，分类数字是召回率。其他版式与负样本用作诊断。课程、停配、复配及 WE 样本量不足以评估首配识别准确率。

- LUMINOUS PLUS 的 `lmn014` 印有 7 月 4 日及两首曲名。旧框无日期的 NEW 和 WE 图保留无日期，曲目分类可独立通过标题与画面复核。
- OCR 漏读 `ayakaki0001`、`lovelive0002`、`bookoff0002`、`akuten0001`、`haruhi0001`、`bluearchive0002` 的小型日期横条。未命中整行的不自动模糊匹配。`normal0091` 是同期仅有奖励的地图图，`iyowa0002` 是活动图，含 ULTIMA 标签的是同日 `mate0009`。
- `maimai005` 明确列 2994、2995，`ongeki21` 列 2996–2998。共享 `netOpenName` 的两张图要分别核对。SEGA 12053 正文后段有 `2025年6月18日(木)`，与开头 2026 年、生效星期及两张图上的 `2026/6/18` 冲突。2994 的 6 月 25 日旧候选保留在原证据表。
- 2026 年 8 月 6 日的 3038、3040 当天属直接追加，3041、3042 在同包的 Blue Archive 图及单独公告上。当天的地图奖励图和更新曲目之间不存在日期传播关系。2026 年 9 月 25 日为星期五，活动续至 11 月 11 日，活动结束日独立保留在图文证据里。
- 2026 年 9 月 17 日 `Mate ep. III` 图有日期，主要标插图和设计者。现有图文未能确认 Colossus 的首次可玩条件。通常开放和限定路线在公告中多次同日并列，普通首配导入只接受已核对的对象与路线。

23 张发布用图片都找到了官网对应 PNG。归一化 RGB 后的缩放和中心裁剪平均误差为 0.00134–0.00320，阈值 0.025 只用于挑选整幅近似副本。`new0212` 在本轮下载的 NEW 官网图中无近似副本。官方 X 可用本地配对清单提供对照图，本轮 23 张入库图均以 SEGA 官网存档为证，X 图片对应数暂未验证。视觉相似度用于复核线索，日期、曲目和难度仍以图文证据为准。

## 日期补全与验收

| 类型 | 原缺口 | 新确定 | 剩余 |
| --- | ---: | ---: | ---: |
| 普通曲 | 129 | 47 | 82 |
| 普通谱面 | 524 | 68 | 456 |
| ULTIMA | 10 | 0 | 10 |
| WE 独立谱面 | 6 | 0 | 6 |

135 条 `kind=song` 空值包含 6 条 WE 父记录。23 张图共确认 48 个带独立曲名的对象，其中 1 张 ULTIMA 的 2026 年 9 月 25 日原日期得到复核。仅在直接追加及同批同源 C2S 齐备时给 BAS、ADV、EXP、MAS 写入首配日。地图及联动的歌曲日期表示该开放路线的生效日，玩家实际游玩还取决于条件。导入保留快照、历史修订及现有 `possible` 状态，缩窄已确认首配身份在首发版里的时间窗口。定数、显示等级、C2S、元信息改版、停配和复配本轮没有新增可证日期。库内原冲突来源保留，导入结果标记既有确切日期冲突。

正式库为 `.tools/jp-hdd/image-evidence/catalog.sqlite`，起点为前轮验证过的统一库。本轮从起点重新复制并应用证据。数据库版本和读接口兼容。22 代内容快照用旧框独立源库及 SDHD 存档 XML 校核，检查 SQLite 完整性和外键，抽验实盘 C2S。LUMINOUS 的 C2S 检查采用已记录的明文基础盘样本。正式库 SHA-256 为 `27575295517b56d3c31a7cb8d83ac1b96b599e935499043df85a81594f3f42b0`。逐表差分确认既有实体、身份关系、修订、快照、资源索引及已有确切日期没有变化。全量回归 162 项，11 项跳过，其余通过。邻仓契约回归、26 项能力清单检查和编译检查均通过。

## 复跑

```powershell
$work = '.tools/jp-hdd/image-evidence'
$base = '.tools/jp-hdd/date-mechanism/rebuilt/catalog.sqlite'
$scan = @('--old-root','E:\SDBT 11 in 1','--new-root','E:\SDHD','--catalog',$base,'--output',$work)
python -X utf8 tools/hdd_image_inventory.py @scan
python -X utf8 tools/hdd_embedded_images.py @scan
# 使用独立环境安装 rapidocr==3.9.2、onnxruntime 和 Pillow 后运行 OCR。
python -X utf8 tools/hdd_notice_images.py @scan --ocr named --review-set tools/hdd_notice_reviews.json --official-pairs "$work/official/pairs.json"
python -X utf8 tools/apply_hdd_notice_reviews.py $base tools/hdd_notice_reviews.json "$work/repeat/catalog.sqlite" --new-root 'E:\SDHD' --official-dir "$work/official"
python -X utf8 tools/verify_jp_hdd_history.py "$work/repeat/catalog.sqlite" .tools/old-hdd/lifecycle-dates-final/catalog.sqlite 'E:\SDHD' --allow-unencrypted-sample
```

归档目录使用公告 ID 为 `.html` 文件名，图片使用官网原文件名。审核清单保留文章与插图摘要、原盘路径与摘要、事件 XML、OPTION、开放方式、逐项曲名和独立谱面标签。`--ocr all` 可扩大到所有解码候选，跳过 `--ocr` 可直接重出缓存结果。新盘或新增 OPTION 重新跑 inventory，后续以文件元数据复用摘要，以内容 SHA-256 和 OCR 版本复用解码与识别。未进入统一库的新版本仍出候选，历法和对象见证随正式库导入后建立。ZIP 凭据可由进程环境 `UNIBOT_HDD_ZIP_PASSWORD` 或私密文件注入。运行日志、数据、图像及报告 JSON 位于忽略目录。
