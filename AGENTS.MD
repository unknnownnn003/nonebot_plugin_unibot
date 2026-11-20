# AGENTS.MD

## 当前项目与入口

NoneBot2 / OneBot v11 的 CHUNITHM 国服插件，入口为 `nonebot_plugin_unibot/__init__.py`。公共歌曲、谱面与定数使用落雪国服；玩家资料与完整 Score[] 仅使用落雪 OAuth 个人 API。当前主线已完成第三阶段 C，图片视觉重设计留给第四阶段。

- `user_bind.py`：只注册本人 OAuth 授权、确认、状态、解绑；保留模块名是为沿用综合插件的 `scores` 策略映射，不读取旧好友码。
- `services/oauth.py`、`oauth_store.py`、`oauth_runtime.py`：官方授权协议、加密持久凭据、可选 HTTPS 回调与真实 OneBot 命令。
- `services/player_data.py`：完整 Player + Score[] 的唯一查询快照、60秒内存缓存、身份/授权代次/策略/取消检查；不读取旧成绩文件。
- `services/player.py`：B30/B50、个人资料、进度、推荐、单曲成绩共用服务；`services/rating.py`统一配信、禁用及版本分区。
- `best_rating.py`、`get_player_songlist.py`、`overpower_list_local.py`、`recommendation.py`：现有图片及命令适配。`/b` 已删除；`/b30` 单榜与 `/b50` 双区共用快照，不建立第二成绩模型。
- `services/song_model.py`：唯一活动曲库 Catalog/Song/Chart；`song.py`：查询、详情与别名；`chuguess.py`：猜曲。
- `songlist_update.py`、`get_resource.py`、`collection_update.py`、`story.py`：曲库和资源/剧情维护。
- `services/media_jobs.py`、`sync.py`、`job_store.py`、`presentation_store.py`、`command_receipts.py`：统一队列、幂等、取消、投递和缓存补发。`sync.py` 名字保留为队列契约，成绩同步业务已删除。
- `config.py` 只声明 `lxns_oauth_*`；`llm_api.py` 为可选 Agent API。用户说明见 [README](README.md)，OAuth与玩家模型见 [OAuth说明](docs/lxns-oauth.md) 和 [第三阶段B](docs/oauth-player-api.md)。

## 精简与变更原则

- 一个需求只有一条正式数据路径。新实现完整替代旧业务后，删除被替代命令、配置、解析器、存储写入、渲染分支、LLM schema、帮助和相应测试，不长期维护无消费者的兼容层。
- 不为旧测试保留空壳模块、虚假成功接口、第二套成绩模型或自动回退。历史缓存能力/队列用小型明确退役拒绝即可，不重放旧业务。当前退役的是好友码绑定、开发者密钥查分、手动同步、CSV/Rin上传与手动传分。
- 删除代码与删除用户数据分开处理。用户已允许清理旧成绩，但每次仍需核对实际目录、写入者和范围；好友码历史文件、OAuth凭据、曲绘/剧情和迁移备份不能顺手删除。迁移原件和第一阶段私密归档不作为活动数据读取。
- 保留有实际职责的模块名与目录，不为整洁拆层或添加无消费者抽象；共享模型放 services，原生命令与现有渲染保持可读。新增未使用字段、函数、依赖和脚本应删除。
- 只改本仓。邻仓和服务器默认只读核对，不擅自修改、同步、部署、重启或改 App 交接文件。

## 错误提示与授权链接撤回

UniBot错误消息保留，不使用10秒自动撤回。`services/error_notices.py`仍与另两插件共享OneBot回执hook，但UniBot错误调用清除自身标记，不改变另两插件的撤回规则。错误分类仍供结构化状态和LLM诊断使用，不按任意出站消息关键词判断。

OAuth群内授权链接使用Bot自己的成功发送回执，按Bot/QQ/本次授权代次加密保存；本人绑定成功后尝试撤回对应链接消息，失败只记固定日志，不回滚绑定、不重试或撤回用户消息。用户仍须自行撤回确认指令。

## OAuth 与个人查询边界

- 授权码流程只申请 `read_player openid`，机密客户端 + PKCE S256。固定可信 issuer/端点，OIDC RS256、kid、iss、aud/azp、exp/iat、sub及首次nonce必须验证；按官方实际文档核对，不从开发者密钥推断用户权限。
- 身份来自真实 OneBot Bot/Event，不接受模型或命令参数指定 QQ、Bot、群、SUPERUSER 或任意本地路径。只有本人授权的固定 `/api/v0/user/chunithm/player` 与 `/scores` 可供个人查询，禁止再引入好友码或按QQ访问他人的开发者接口。
- 授权可在已启用 scores 的群中全程进行，链接、确认、状态与解绑均在当前聊天回复，无需私聊；最终确认仍只接受原 QQ。绑定成功后自动撤回Bot的对应授权链接消息，确认指令提醒用户自行撤回。state一次性、10分钟有效，网页确认5分钟有效；群来源在交换、保存和确认时重查原群开关。群内不展示 Token、落雪授权码或客户端密钥。
- Token、PKCE、OIDC身份只存受保护 LocalStore 加密字段，密钥独立从配置读取。不得进入模型上下文、任务payload、QQ、日志、Git或普通备份。坏密钥、权限或回调冲突应禁用OAuth，不能清库重建。
- `binding_id` 表示一次授权绑定，重绑改变、正常刷新保持；刷新 `generation/reservation` 只表示一次轮换。两者不能混用。刷新预约比较凭据快照并使用独立操作代次；未知轮换结果不盲目重试。
- 缓存按 Bot/QQ/binding 隔离，最多64份、60秒，不建立可回退的磁盘成绩库；任务数据库和持久媒体仍包含已生成查询结果，必须按原作用域和授权控制访问。每次用缓存仍检查有效授权。401仅失效被拒绝的实际Token，迟到401不能撤销新Token或驱逐新快照；超时、限流、空/坏响应不得覆盖最后有效快照，不用过期缓存冒充成功。
- 上游未公布撤销端点时只明确本地解绑，提示用户到落雪应用管理页撤销。远端撤销无法实时推送给本地；最迟在下次个人API或刷新失败时发现。不能假报远端撤销或自动切回旧查分。
- 排队时捕获binding，执行、缓存发布、渲染后、发送前和补发都重新核验身份、原作用域、当前策略、授权和取消状态。解绑后迟到响应不发布，旧任务结果、模型材料和通知须受控；已发送给用户的消息无法收回其已读内容。
- 提供给LLMChat的异步玩家通知只带通用完成状态，不向其延迟队列复制个人资料/成绩；需要模型材料时由当前作用域重新核验。本人任务查询/取消可用，但失效授权不应暴露旧玩家结果。

## 数据与曲库

所有路径经 `services/paths.py` 获取 NoneBot LocalStore，不从源码或cwd推导运行数据：

- 持久目录：曲库、别名、曲绘、收藏品、剧情、加密OAuth库以及任务/命令/投递数据库和持久媒体。
- LocalStore缓存目录为可丢弃内容预留；当前个人快照只在内存，不再使用旧 uploads/ 和 score/。
- 旧源码 `data/` 为第二阶段 A 的迁移原件。首次升级必须停止写入者，显式运行 `tools/migrate_localstore.py`；检测旧数据却无完成标记时拒绝加载，不导入期猜测或静默迁移。完整核对/回滚见 [存储迁移](docs/localstore-migration.md)。不删除标记重跑覆盖新数据。
- 曲库来源 `lxns_cn`、schema_version=2。歌曲按原始ID区分，谱面保留所属ID、难度、定数、版本、物量及未知属性。ULTIMA用谱面版本，不以原曲版本覆盖。
- WE只按明确 `origin_id` 关联存在的普通原曲，在查询层聚合，不按标题/曲师/曲绘合并。孤立、冲突或链式关联保留独立。多WE不以难度为唯一键覆盖；同ID多元数据无法由API唯一选择时保留全部并报告歧义。
- 完整个人Score[]保留原始ID、clear/combo/chain、rating/OP、时间及未知字段；未知ID只报告未匹配，不按标题重编号，不引入旧成绩兜底。B30用普通谱面并排除WE，使用API的rating；OP用API的over_power。
- 更新公共曲库先验证完整响应、拒绝空库，再按原始ID保留别名并完整备份旧曲库到 `catalog_backups/`，原子替换。不得改写用户OAuth或任务数据。
- 调试、临时依赖、预览放 `.tools/` 并确认忽略；不提交真实配置、账号、成绩、令牌、缓存和部署快照。用途不明历史文件优先保留，禁止 `git clean -fdx`。第一阶段归档见 [记录](docs/phase1-housekeeping.md)。

## LLM 接口与三插件依赖

- 当前 API_VERSION=1、CAPABILITY_VERSION=4.3.0，公开25项能力。玩家能力为 `player.best/profile/progress/recommend/song_scores`，均为真实请求人的OAuth查询；OAuth授权/确认/解绑保持原生，不交给模型代理。
- 参数schema、ID、nb_unibot名称、effect、long_task、结果码与制品是跨插件契约。变更须核对LLMChat调用方、动态发现、静态快照及综合面板，按语义调整版本。当前移除 `player.bind`、`score.submit/sync/import_file`，旧缓存明确拒绝；`player.best`不再接收mode/version。
- 保留 `describe_capabilities/context`、prepare/invoke、任务通知、材料及表达接口。结果统一 `services/results.outcome`，拒绝/缺配置/未知结果不能假报成功。写入继续真实用户确认和执行时重查权限。
- `services/native_catalog.py` 由**本仓** `tools/export_native_catalog.py` 生成；命令改动后生成并运行 `--check`。OAuth单独列入NATIVE_ONLY_COMMANDS，不作为模型工具。不能运行旧邻仓生成器覆盖。
- LLMChat仅动态发现和编排，不复制模型、写成绩文件、构造群消息绕回命令。UniBot不依赖其加载才能使用原生命令或队列。
- 综合插件拥有唯一群策略，UniBot动态适配，不另建配置。MODULE_FEATURES及capability_feature配套项需只读检查；默认不改其他仓库。

## 统一群策略与任务

- 唯一策略源为宿主综合插件 `feature_config.json`，_policy_version=2；每群 `_plugins.unibot` 总开关和 `_unibot` 的 songs/scores/player/recommend/story/guess/maintenance/help 单项。新群/新功能默认关闭，总开关关闭保留选择；旧白名单不强制开功能。
- 无综合提供者时群业务默认拒绝，私聊仍受本人身份/授权约束。SUPERUSER不绕过本人OAuth查询边界。群设置不改绘图审核、额度、账本或其他插件权限。
- 当前执行点都重查群策略；不能只依赖预处理器ContextVar或普通API hook异常，前者不能从子任务回传，后者可能被框架吞掉。拒绝测试必须证明底层API未调用。
- 本人 job.list/get/cancel 保持可用，job.replay 是发送操作，不套查询例外。结果投递未知不自动重试；补发只读缓存，不重新查分/生图。LLM通知器缺席不能阻断原生结果。
- 持久任务使用真实Bot/会话/用户/消息作用域；启动后缺失原请求上下文的历史任务标记中断，不从模型参数伪造恢复。关停取消待执行协程并观察已启动的线程写入完成。

## Git 工作流

本仓独立Git，默认直接在main开发，每阶段完成测试后自动Commit并普通Push到origin/main，无需再次询问。授权仅限本仓，不包括服务器部署、服务重启或其他仓库推送。

1. 接手先检查 `git status --short --branch`、暂存区、远端与最近提交；fetch核对基线。保护既有修改，不reset/clean/覆盖。当前无关未提交内容不能混入本轮提交。
2. 落后只在安全时快进；分叉保留双方历史并安全合并、重新验证，不强推或改写远端历史。不能安全处理时保留现场并说明。
3. 通过针对性测试、`git diff --check` 后按明确文件暂存，检查 `git diff --cached --check`、差异、敏感信息，再Commit/Push。不能全量暂存未知文件。
4. 推送后核对HEAD、远端main和工作区。失败如实报告，不将本地提交称为已推送；有推送即部署流程时先处理触发边界，不能越过不部署要求。

## 验证要求

复用现有依赖，不擅自安装完整宿主、连接真人账号、发真实群消息或改线上配置。UTF-8明确读写，网络用async httpx，线程文件/渲染通过可观察取消的async_call，日志不打印个人API响应、异常对象或密钥。

```powershell
python -m unittest discover -s tests -v
python tests/run_neighbor_regressions.py
python tools/export_native_catalog.py --check
$env:PYTHONPYCACHEPREFIX = Join-Path $PWD '.tools/pycache'
python -m compileall -q nonebot_plugin_unibot tools tests
# 在同一长寿命终端使用时，完成后恢复原PYTHONPYCACHEPREFIX。
git diff --check
```

- 本仓测试覆盖迁移、WE/ULT、OAuth/OIDC、完整个人API、TTL/空响应、401/刷新/重绑、线程渲染后解绑、多人/多Bot隔离、权限/取消、旧工具拒绝、队列补发/材料/通知；模拟网络必须断言固定本人端点和底层API拒绝。
- 邻仓回归仅运行仍有效契约。旧业务及旧静态快照在本地适配器明确列出替代原因，`--legacy-contracts`可复现旧断言；不得保留无用实现或伪造fixture让旧业务“通过”。新增OAuth/队列替代测试在本仓维护，不擅改邻仓。
- 数据清理前后记录范围、数量、校验和实际删除结果；本地清理不能被报告为服务器清理。真实TLS、Caddy、账号同意/撤销及生产日志是另行授权验收，不能把mock或语法检查称为线上闭环。

## 协作入口

App总入口为 [AGENTS](E:/projects/my_app/AGENTS.md)，本插件业务与本轮用户授权优先。本地邻仓为 `D:/Files/nonebot/nonebot_plugin_llmchat`、`D:/Files/nonebot/nonebot_plugin_unknnownnn`；跨仓修改前需单独授权并读取各自规则。服务器 `/root/robinbot/plugins/`、Caddy及 `robinbot.service` 等均不在本轮写入范围；不重启NapCat、不退出QQ。


## 第三阶段 C：统计语义与联动

- `/b`及其别名完全删除。`player.best` / `/b30`按全版本已匹配且有效的普通谱面Rating降序取30，分母固定30，标题为B30均值，不能称作玩家总Rating。`player.b50` / `/b50`按歌曲版本分为旧曲BEST30与当前曲NEW20，互不重叠，缺位计零，合计除50；两者只读同一OAuth快照。
- 当前国服版本从落雪 `/api/v0/site/config` 的 `resource_version.chunithm`取得，并显式传给曲库请求，验证后随曲库原子写入`rating_version`。不能取`versions`最大值、谱面追加版本或本机日期猜测。旧曲ULTIMA保留自身版本展示，但不改变歌曲所属分区；旧曲库缺此元数据时B50明确拒绝，更新曲库后恢复。
- 普通Rating/OP排除WORLD’S END和disabled曲目。按歌曲及谱面release_date在UTC+8当天07:00生效；日期缺失或无效沿用官方不限制契约。WORLD’S END单曲结果显示实际分数/ID/属性，普通Rating/OP标为不适用，上游数值仅保留在明确标记的upstream_metrics，不作为标准指标输出。
- `/chulist`筛选MASTER/ULTIMA，每张筛选谱面各计一次，分组与总计同口径，未记录计零。明确标记“筛选谱面OP合计，非游戏内按曲最高的玩家总OP”；不可把此值冒充完整账号OP。公式仅计算理论上限，个人OP用落雪返回值。
- OAuth群策略标识使用内部`oauth.manage`映射至原`scores`键，不再借用已退役`player.bind`工具名。原命令模块仍为user_bind；B30/B50/单曲等沿用player，推荐沿用recommend，群功能键和选择不重置。
- 只有当前维护的契约进入测试与能力快照。邻仓回归器不再排除旧断言；删除退役附件/同步测试，保留明确拒绝及新OAuth替代覆盖。LLMChat生成器不能覆盖UniBot原生命令目录；公开schema快照由其`tools/export_capabilities.py`生成并检查。
- 本阶段用户特别授权三个本地插件的本次联动修改及按范围Commit/Push；不扩展到其他仓库或生产环境。既有邻仓未推送提交不应夹带发布；远端缺失时保留本地提交，报告需要的远端信息。

## 后续服务器配置交接

用户2026-11-17说明OAuth密钥相关配置已放在本地`.env.prod`，服务器尚未修改。本阶段不读取、输出、提交或复制真实密钥，也不部署。后续明确获准同步服务器时，将该文件内容追加到服务器`.env.prod`文末；先安全备份并检查同名键冲突，在私密文件操作中处理，不把原值写入日志、命令参数、模型上下文或Git。
