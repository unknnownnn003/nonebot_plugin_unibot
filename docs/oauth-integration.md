# 第三阶段 C：OAuth 联动与统计语义

本阶段仅修改本地 UniBot、LLMChat、综合插件的 OAuth 成绩体系联动。未配置服务器、Caddy、真实密钥或生产账号；完整视觉重设计仍留给第四阶段。

## B30 / B50

| 入口 / 能力 | 选择规则 | 汇总与展示 |
| --- | --- | --- |
| `/b30` / `player.best` | 所有版本有效普通谱面，按个人 API 返回的单谱面 Rating 降序取30 | 单区域；缺位为零，除以30，称作 B30 均值，不当作游戏内玩家总 Rating |
| `/b50` / `player.b50` | 歌曲属于旧版本的 BEST30，加当前版本歌曲的 NEW20；两个池互斥 | 双区域；缺位为零，合计除以50，显示估算值；个人资料仍显示落雪 Player.rating |

`/b` 及其别名已完全删除。两个入口复用 PlayerData 的同一 OAuth 快照、授权代次、群权限、并发刷新与任务队列，不创建独立成绩文件或成绩模型。

曲库更新先读取落雪公开 `/api/v0/site/config` 的 `resource_version.chunithm`，向曲库请求显式传入此版本，核对它出现在版本目录后随曲库原子保存为 `rating_version`。2026-11-17 本轮只读核对值为24000，目录还包含24500，因此不能取目录最大值。该值只记录在本地工具输出中，没有硬编码到业务。

NEW20 依据**歌曲**的首次版本归属；追加 ULTIMA 的独立谱面版本仍用于展示，不能把旧曲变为新曲。未知或晚于当前版本的歌曲在 B50 中明确统计为无法分区，发送更新曲库提示，不按名称猜测；旧曲库缺少 `rating_version` 时，B30 仍可查，B50 要求管理员更新曲库。

普通 Rating 和 OP 排除 WORLD'S END、禁用歌曲及尚未配信的歌曲/谱面。配信按官方 release_date 契约，在 UTC+8 当天07:00生效；日期缺失或无法解析不限制计算。曲库自身保留全部记录和原始版本，不因统计过滤删除歌曲或成绩。

## WORLD'S END 和进度统计

单曲查询仍按显式 origin_id 关联 WE 与原曲，保留独立歌曲ID、谱面属性、版本和同ID多谱面元数据。WE结果只展示实际分数、状态和属性，标记普通Rating/OP不适用。完整快照不改写；对外结构中的上游数值移至 `upstream_metrics`，并提供 `standard_rating_applicable=false`、`standard_op_applicable=false`，避免模型把它解读为普通指标。

`/chulist` 只筛选 MASTER/ULTIMA。总计和分组均逐筛选谱面相加，每张各计一次；未记录谱面以零计入完成率分母。个人OP来自API，理论上限为 `(定数+3)*5`。图片和实际投递文字明确标识“筛选谱面OP合计，非游戏内按曲最高的玩家总OP”。此前总计按曲取最高、分组逐谱面相加的混用已消除。

B30、B50与进度图的说明文字和图片一起进入持久投递计划，包括B50版本无法分区警告。每段投递仍重新检查授权、取消和当前群策略。

## 三插件契约

- UniBot API_VERSION=1，CAPABILITY_VERSION=4.1.0，共25项公开能力；新增 `nb_unibot_b50`，`nb_unibot_best`保持B30单榜，`nb_unibot_song_scores`保留单曲。OAuth不交给模型代理。
- 综合插件移除 `get_player_score` 模块映射，`scores` 显示为“OAuth 授权与账号管理”，维护菜单仅剩国服曲库、曲绘、收藏品、剧情。内部 `oauth.manage`映射到原`scores`；`player.*`继续映射`player`，推荐仍为`recommend`。不改变 `_policy_version=2`、八个功能键或持久用户选择。
- LLMChat清理退役好友码、手动传分、同步、附件导入和日服维护的工具名称；动态发现当前已加载提供者，按真实身份和当前授权生成上下文目录。能力概览区分B30/B50/单曲。
- LLMChat的 `tools/export_capabilities.py` 从公开schema生成现有 `docs/v3.1/CAPABILITIES.json` 审查快照（68项，UniBot25 + 综合43）。不加载宿主、不读取凭据或运行数据。旧v3.1过程文档保留为历史记录。
- UniBot原生命令目录只由本仓生成器维护；LLMChat旧生成器现在只处理综合插件，不能覆盖UniBot。
- 邻仓已用OAuth和当前国服模型替换退役业务断言；UniBot回归器不再有SUPERSEDED列表或隐藏排除。纯事务测试显式设置临时策略；真实权限由三插件宿主测试验证。

## 验证与边界

本阶段运行数据与源码迁移原件各3228个文件均与第一阶段清单逐一SHA-256匹配；未删除或迁移真实用户数据。`.env.prod`已确认被Git忽略，内容没有读取、输出或提交。

测试使用临时目录、fake OneBot底层API和mock HTTP。三插件宿主测试实际加载三个入口、LocalStore、ORM和OneBot适配器，完成ORM建表/连接释放及插件清理，验证动态目录、B50队列、群关闭不发请求/消息、私聊独立、解绑后目录失效与任务结果隐藏/补发拒绝。未访问真实个人API、授权真人或发群消息。

必要检查：

```powershell
python -m unittest discover -s tests -v
python tests/run_neighbor_regressions.py
python tools/export_native_catalog.py --check
python ../nonebot_plugin_llmchat/tools/export_capabilities.py --check
python ../nonebot_plugin_llmchat/tools/export_native_catalog.py --check
```

实际结果：

- UniBot unittest：65项，64通过、1项Windows真实符号链接权限跳过（mock reparse覆盖通过）；包含真实三插件隔离宿主。
- 当前邻仓定向回归：99项全部通过，旧断言排除项14→0。国服目录与别名并发事务测试另行通过。
- 综合插件全量：隔离NoneBot、临时LocalStore，禁用无关pytest自动插件，只加载pytest_asyncio，431项中410通过、21失败。导出本轮前HEAD基线同环境为429项中406通过、23失败；当前失败用例集合未新增。
- LLMChat全量：368项，22项失败、33项错误；当前失败方法集合均在导出的本轮前HEAD测试基线中出现。基线包含更早的源码布局/退役契约，不以错误计数差直接当作覆盖率改善。
- 两仓全量遗留主要为旧群策略v2以前的白名单/权限断言、旧AST/宿主夹具，以及字体/平台依赖；本轮不扩大修改其他业务，不能报告全绿。
- UniBot及两邻仓目录生成检查、UTF-8语法检查、Git差异空白检查通过；没有线上验证。


## 推送与后续部署

UniBot在main正常提交与推送。综合插件开始时main已领先origin/main 41个既有提交，远端尚无本次所依赖的群策略和设置模块；直接推main会一并发布它们，不能当作只推本次小改动。LLMChat的master没有远端配置。必须先明确这两处推送边界，保留本次提交和既有工作，不能强推或用覆盖重置处理。

用户已说明真实OAuth配置在本地`.env.prod`，服务器未修改。将来获准同步服务器时，将其内容追加到服务器`.env.prod`文末；先备份并核对同名键，在私密文件操作中处理，不输出真实值。本阶段没有服务器操作。

## 官方依据

- 落雪中二API：<https://maimai.lxns.net/docs/api/chunithm>，歌曲/谱面版本、disabled、release_date及个人Score[]。
- 落雪公开站点配置：<https://maimai.lxns.net/api/v0/site/config>。
- 落雪官方前端：<https://github.com/Lxns-Network/maimai-prober-frontend>，`src/utils/chunithm/overPower.ts`和版本配置消费者。
