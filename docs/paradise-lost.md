# /旧框 · PARADISE LOST

`/旧框` 使用当前请求人的落雪 OAuth 成绩快照，并按 2021 年日服 PARADISE LOST 末期的定数重新计算。它沿用 `player` 群开关、队列和原请求授权范围。LLMChat 通过 `player.legacy_rating` 发现同一项无参数能力。

## 历史数据与覆盖范围

- [MAX-eipi 固定快照](https://github.com/MAX-eipi/ChunithmMusicDataTable/blob/ee801ea72184a44fa274a3eaa0b983508ff6c2e4/index.html) 提交于 2021-11-02，标题为 `LOST`，与[发布页面](https://max-eipi.github.io/ChunithmMusicDataTable/)的下载内容逐字节一致。该表记录 893 首、3,572 张 BASIC 至 MASTER 谱面。[2021 年 5 月日服文章](https://chunithmfanclub.hatenablog.com/entry/2021/05/06/125723)可用于核对当时的谱面和难度，日期早于末期快照。[末期曲目记录](https://wikiwiki.jp/chunithmwiki/CHUNITHM%20PARADISE%20LOST)显示版本末期追加曲目。全表理论值也和校验目标一致。没有取得 SEGA 末期全曲目录逐首证明无遗漏。
- 当前映射以 2026-11-21 核对的落雪国服公共曲库为基准。标题按 NFKC、大小写及空白规范化比对。现有别名可以供导入审查，只有独立确认的改名才允许进入映射。碰到同名多候选、版本不符或无法核验的映射时排除。运行时只按固定的 `song_id + difficulty` 查表，并重查当前国服谱面的身份指纹。
- 映射 **762/893 首（85.33%）**，**3,047/3,572 张（85.30%）**。130 首未在国服目录按标题匹配，1 首同名但属于后代谱面（`Help me, ERINNNNNN!!`），0 项多候选重名冲突。`祈 -我ら神祖と共に歩む者なり-` 的 MASTER 依 2022-04-14 [SEGA 改谱公告](https://info-chunithm.sega.jp/2007/)排除，其它难度仍可参与。历史理论值仍包含原始 MASTER。未映射共 131 首和 525 张谱面。
- 落雪公共曲库未提供历史谱面文件或历史指纹，现有字段无法检测所有未公告修订。曲库更新后如果标题、作者、版本、谱师或可用物量字段变化，现有映射会暂停生效，复核后可重新导入。实际玩家候选还会应用禁用、配信和本人授权检查。

## 计算口径

| 成绩 | 单谱面 Rating（定数 `c`） |
|---|---|
| ≥ 1,007,500 | `c + 2` |
| 1,005,000–1,007,499 | `c + 1.5 + (s − 1,005,000) / 5,000` |
| 1,000,000–1,004,999 | `c + 1 + (s − 1,000,000) / 10,000` |
| 975,000–999,999 | `c + (s − 975,000) / 25,000` |
| 900,000–974,999 | `c − 5 + (s − 900,000) / 15,000` |
| 800,000–899,999 | `(c − 5) × (s − 700,000) / 200,000` |
| 500,000–799,999 | `(c − 5) × (s − 500,000) / 600,000` |
| < 500,000 | `0` |

先钳制非负值，再排序取最多 30 张。B30 平均除以 30。两种模拟都以同一份 B30 加上模拟的 10 项并除以 40，差值用未格式化的值计算。缺位计零。现代附加评级折回 S、SS、SSS，卡片难度名旁直接显示一位小数的历史定数。颜色低段起点为绿 0、橙 4、红 7、紫 10，之后是铜 12、银 13、金 14、白金 14.5、虹 15。90 万以下以 50 / 80 / 90 万的公开计算锚点分段线性插值，缺少旧机现场实测样本。[2021 年旧版评级与计算资料](https://forum.gamer.com.tw/C.php?bsn=70357&snA=43) · [版本色阶记录](https://wikiwiki.jp/chunithmwiki/レーティング・OVER%20POWER)

全表每张谱面使用理论 SSS，得到 B30 **15.9700**、B1×10 的 R10 均值 **16.1000**、两种模拟总值 **16.0025** 和 **15.9925**，差 **0.0100**。图片注明「旧版定数模拟｜非真实历史 Rating」。

## 重建与检查

将历史固定提交的 `index.html` 和落雪公开曲库 JSON 下载到 `.tools/`。原始输入及发布 JSON 的来源 SHA-256 见 `assets/historical/paradise_lost.json`。重建工具只接受已固定 SHA-256 的历史 HTML，更新国服映射时请复核统计和差异。

```powershell
python tools/import_paradise_lost.py --html .tools/paradise/index.html --lxns .tools/paradise/lxns.json
python tools/import_paradise_lost.py --html .tools/paradise/index.html --lxns .tools/paradise/lxns.json --check
python tools/import_paradise_lost.py --check
python tools/check_ui_assets.py
python tools/export_native_catalog.py --check
```

排名图复用现有 B30 的五列卡片、玩家栏、分数、Rank、Clear、连击和 Rating 贴图。历史档位决定用哪个色阶，贴图缺失时使用对应颜色的文字。离线预览脚本 `tools/preview_unibot_ui.py` 同时输出满 30、缺位、空榜和无 HDD 素材四种旧框图。
