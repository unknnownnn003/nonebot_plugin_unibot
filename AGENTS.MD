# AGENTS.MD

## 群报错自动撤回

`services/error_notices.py` 与另两个插件共享单个 OneBot 回执hook，群内报错成功发送后10秒撤回。只标记程序错误分支和失败结果，不按任意群消息关键词判断；私聊及正常结果保留。撤回目标只能来自Bot自身发送回执，失败只记日志；独立加载时由入口安装，停机取消待撤回任务。纯状态字符串可用error_text标记；已有success布尔或结果status应直接用于result_call。

## 项目概览

这是一个基于 NoneBot2 和 OneBot v11 的 CHUNITHM（中二节奏）查分、曲库、剧情与图片生成插件。插件包位于 `nonebot_plugin_unibot/`，主入口是 `nonebot_plugin_unibot/__init__.py`，导入各功能模块后由 NoneBot 自动注册命令与正则匹配器。

主要能力：

- 绑定落雪（Lxns）好友码：`/bind <好友码>`
- 上传或同步成绩：`/chuupdate`，其他同步入口以 `get_player_score.py` 当前注册命令和 README 为准，不以历史工作区状态推断
- 查询个人信息与本地成绩进度：`/个人信息`、`/分数`、`/chuinfo`、`/chulist`
- 查询曲目、管理本地别名、随机推荐歌曲：`是什么歌`、`/添加别名`、`/删除别名`、`/查看别名`、`推什么`
- 更新曲库、曲绘、收藏品和剧情资源：`/更新songlist`、`/更新曲绘`、`/更新收藏品`、`/更新剧情`
- 查询剧情：`/查剧情`、`/随机剧情`、`/剧情分类`、`/剧情列表`

## 重要文件

- `README.md`：用户侧指令说明。
- `nonebot_plugin_unibot/__init__.py`：插件元数据与模块导入入口。
- `nonebot_plugin_unibot/config.py`：插件配置模型，当前声明 `lxns_token`、`chunirec_token`、`naominet_token`、`naominet_session` 四个可选字段。后续退役数据源前仍保留现有配置兼容。
- `nonebot_plugin_unibot/requirements.txt`：运行依赖清单。
- `nonebot_plugin_unibot/user_bind.py`：QQ 到落雪好友码的本地绑定。
- `nonebot_plugin_unibot/get_player_score.py`：成绩上传、解析、合并和同步逻辑。
- `nonebot_plugin_unibot/get_player_songlist.py`：玩家资料查询与图片渲染。
- `nonebot_plugin_unibot/overpower_list_local.py`：本地成绩进度、Overpower 计算与列表图生成。
- `nonebot_plugin_unibot/song.py`：曲目模糊搜索、详情图、别名管理。
- `nonebot_plugin_unibot/recommendation.py`：基于本地成绩的推荐曲目。
- `nonebot_plugin_unibot/songlist_update.py`：更新 `songlist.json`。
- `nonebot_plugin_unibot/get_resource.py`：下载曲绘资源。
- `nonebot_plugin_unibot/collection_update.py`：更新收藏品资源。
- `nonebot_plugin_unibot/story.py`：剧情索引、下载、渲染与查询。
- `nonebot_plugin_unibot/help.py`：帮助图生成。

## 运行与依赖

本仓库当前没有标准打包配置文件（例如 `pyproject.toml` 或 `setup.py`）。作为插件开发时，通常将整个仓库放在 NoneBot 项目的插件搜索路径中，或把 `nonebot_plugin_unibot/` 作为本地插件包加载。

安装依赖时参考：

```powershell
pip install -r nonebot_plugin_unibot\requirements.txt
```

运行环境需要 NoneBot2、`nonebot-adapter-onebot`、`nonebot-plugin-htmlrender`、Pillow、httpx、aiofiles、jaconv、zhconv、pypinyin。需要访问落雪接口的功能依赖 `.env` 或 NoneBot 配置中的 `lxns_token`。

## 数据与资源

代码会在插件目录下读写运行期资源，例如：

- `nonebot_plugin_unibot/data/songlist.json`
- `nonebot_plugin_unibot/data/score/*.json`
- `nonebot_plugin_unibot/data/jacket/`
- `nonebot_plugin_unibot/data/story/`
- 绑定信息和其他用户数据

这些数据不应默认纳入版本控制。根目录 `.gitignore` 已忽略 `data/`、`info/`、`__pycache__/`、`*.pyc`、`.vscode/` 和 workspace 文件。新增资源缓存或用户隐私数据时，优先确认是否需要补充 `.gitignore`。

## 开发注意事项

- 先检查 `git status --short --branch`。当前仓库可能存在用户未提交改动，特别注意 `nonebot_plugin_unibot/get_player_score.py`；是否存在既有变更以当次检查为准。
- 该项目以中文命令和中文用户提示为主，编辑源码时保持 UTF-8 编码。
- 网络请求主要使用 `httpx`，命令处理函数是 async；新增阻塞逻辑时避免卡住事件循环。
- 图片生成依赖 Pillow 和本机字体，跨平台字体逻辑分散在多个模块中，改动渲染时要检查 Windows/Linux 字体路径兼容性。
- 成绩、曲库和剧情逻辑依赖远端 API 返回结构；新增解析字段时保留向后兼容和异常兜底。
- 超级用户命令通常通过 `permission=SUPERUSER` 注册，新增资源更新或删除类命令时沿用这个权限边界。

## 建议验证

没有完整测试套件时，至少执行语法检查。使用单独缓存目录，避免把编译产物散落到源码和历史资料中：

```powershell
$env:PYTHONPYCACHEPREFIX = Join-Path $PWD '.tools/pycache'
python -m compileall -q nonebot_plugin_unibot tools/fetch_chunirec_data.py
# 完成后恢复本次命令前的 PYTHONPYCACHEPREFIX 设置
```

如改动涉及 git 工作流，可用以下命令检查：

```powershell
git status --short --branch
git diff --check
git diff --stat
git remote -v
git log --oneline -5
```

提交前确认只暂存自己的改动，避免把本地 `data/`、用户成绩、token、图片缓存或他人未提交代码带入提交。

## Git 工作流（2026-11-17 更新）

本目录是独立 Git 仓库。用户已授权本项目默认直接在 `main` 开发，每个阶段完成针对性测试和必要检查后自动 Commit 并 Push 到 GitHub 的 `origin/main`，方便远程审查，无需逐次询问。本约定优先于协作总入口中的默认新建任务分支规则，仅适用于 UniBot 仓库，不扩大到其他插件、App 或服务器。

- 每次接手先检查实际分支、`git status --short --branch`、暂存区、远端与最近提交，保护已有修改。切换到 `main` 前确认不会覆盖修改；有独立工作时先隔离或保存可恢复检查点，不擅自重置、清场或丢弃文件。
- 开始开发与推送前获取远端状态；落后时仅在工作区安全的情况下快进同步。若与远端分叉，检查双方差异并安全合并、重新验证，保留已有提交；不能通过强制推送或覆盖式重置绕过冲突。无法安全解决时报告具体阻塞并保留现场。
- 阶段完成后先运行与改动相关的测试、`git diff --check`，按明确文件范围暂存，检查 `git diff --cached --check`、暂存内容和敏感信息，再提交并普通推送。禁止全量暂存混入未经检查的改动，禁止 `--force`、`--force-with-lease` 或其他改写远端历史的推送。
- 推送后核对本地 `HEAD` 与远端 `main` 提交及工作区状态。提交或推送失败须如实报告，不能将本地提交称为已同步；远端并发更新造成拒绝时，重新获取、保留双方历史并验证后重试。
- 自动 Commit/Push 的授权不包括 GitHub Actions 部署、服务器同步、服务重启或发布。不得新增、启用或手动触发部署；若仓库接入了推送即部署的流程，先处理部署触发边界再推送。业务代码、运行数据与凭据仍遵守本文件的保护要求。

例如仅修改本文档且检查通过后：

```powershell
git add -- AGENTS.MD
git diff --cached --check
git commit -m "docs: update development workflow"
git push origin main
```


## 2026-11-15 统一群策略

- 三插件共享综合插件 feature_config.json 的 _policy_version=2，按群保存 _plugins 总开关及单项选择；新群和新功能默认关闭。
- 原 _whitelist、绘图白名单及环境默认白名单不再强制开启任何功能。关闭总开关保留单项、LLM预设和概率。
- 现有运行环境必须先以已加入群和旧实际状态迁移，再加载新版本；不得在导入期猜测UniBot/LLM启用状态并覆写旧文件。
- 原命令、工具目录与执行、后台任务、延迟通知及Bot出站API均遵守当前群策略；管理设置及本人任务查询/取消保持原权限可用。关闭期间不新增群聊上下文。
- 群设置不改变私聊、SUPERUSER/群角色、绘图审核、额度、预算、账本与幂等规则。

## Server Companion 协同与 Git（2026-11-15）

- 本 App 工作区为 `E:/projects/my_app`，项目协作总入口见 [AGENTS.md](E:/projects/my_app/AGENTS.md)，详细用户需求见 [requirements.md](E:/projects/my_app/docs/requirements.md)，群策略迁移与验收见 [group-policy-update.md](E:/projects/my_app/docs/group-policy-update.md)。本插件业务细则仍以本文件及目标子目录规则为准。
- 本地三仓库位于 `D:/Files/nonebot/nonebot_plugin_unknnownnn`、`D:/Files/nonebot/nonebot_plugin_unibot`、`D:/Files/nonebot/nonebot_plugin_llmchat`；UniBot 的规则文件名为 `AGENTS.MD`。跨插件修改须读取各自规则，其他插件不动。
- 编辑前检查当前 Git 分支与工作区，先保护既有修改；用户已允许合理保存既有工作，提交时明确既有内容、本轮改动与实际验证范围。按范围提交，不能用全量暂存混入未经检查的数据，不重置或清理他人工作。UniBot 的 main 开发及阶段自动 Commit/Push 遵循上文 Git 工作流授权，其他仓库的推送、合并、发布仍须在对应授权范围内执行。
- 不提交真实 .env、令牌、密钥、聊天/账号/成绩数据、缓存、预览图和本地部署快照；公开模板使用占位值。中文用明确 UTF-8 读写，避免默认 ANSI 回写造成乱码。
- 依赖缓存和临时验证文件优先使用 `E:/projects/my_app/.tools/`，可复用 `tools/env.ps1` 设置环境；不得为测试擅自安装完整 NoneBot 宿主、连接真实账号或发送群消息。只报告实际执行的验证，不把语法检查或 mock 测试称为线上闭环。
- 先本地修改和验证，再按会话授权同步 `/root/robinbot/plugins/` 下的对应插件；检查线上基线、任务状态与配置，备份后再更新，保留可回滚版本。已明确授权的同一同步范围继续执行，不重复确认。
- 服务使用 `robinbot.service`，必要时同时更新管理网关 `server-companion.service`；不无故重启 `napcat.service` 或退出 QQ。`qqbot` 必须显式指定组件。只修改协作文档无需部署运行代码或重启服务。
- 统一策略检查不能仅依赖预处理器的 ContextVar 或普通 Bot API hook 异常：前者在子任务中不能回传，后者可能被 NoneBot 吞掉。原生命令、工具、排队任务与延迟通知均须遵守实际执行时的策略，拒绝不能伪装成功，相关测试须确认底层 API 未执行。

## 目录组织与本地整理

- 运行包保持在 `nonebot_plugin_unibot/`：顶层模块承接原生命令和现有渲染，`services/` 放共享业务、存储、队列和权限适配，`llm_api.py` 为可选能力接口。不要仅为目录美观移动注册模块；模块名参与群策略映射、原命令回执及能力目录。
- `tools/` 只放需显式执行的开发脚本与说明，不由插件入口导入；`docs/` 存当前开发说明和评审记录。当前不建立第二套插件包、成绩仓库或无实际用途的细分目录。
- `data/` 为运行资源和用户状态，既有路径暂不迁移。绑定、成绩、手动成绩、玩家资料、别名、任务与媒体库均先按用户数据保护；曲绘、收藏品、剧情等资源也不能以“Git 已忽略”为由删除。
- 清理前同时检查跟踪文件、未跟踪文件、忽略文件和代码/文档引用。无引用仅是证据之一；用途不明的脚本、本地配置和编辑器文件优先原位保留。只对已核验的可再生缓存直接删除。
- 有价值的旧代码、旧说明、抓取快照、调试样本、压缩备份归档到仓库外的私密目录，保留原相对路径、大小、SHA-256 和恢复说明，校验完成再移除原件。归档可能含用户数据或历史凭据，禁止提交、发布或随部署打包。
- 本轮审计和归档位置见 [第一阶段整理记录](docs/phase1-housekeeping.md)。`.agents/`、`.codex/` 等工具配置不纳入普通清理；新增本地产物放 `.tools/` 并确认被忽略。不要用 `git clean -fdx` 清场。

## LLM 能力接口与三插件依赖

- UniBot 拥有查歌、查分、资源维护及其任务和结果。LLMChat 的 `agent_tools.py` 只发现已加载插件的 `llm_api`，负责工具编排与确认，不另写成绩文件、不复制查询算法、不通过构造群消息绕回命令。UniBot 不反向依赖 LLMChat 才能完成原生命令或任务。
- 当前 `llm_api.API_VERSION=1`、`CAPABILITY_VERSION="3.2.0"`；接口包括 `describe_capabilities`、`describe_context`、`prepare_capability`、`invoke_capability`、`set_job_notifier` 及任务材料/表达接口。版本号是本次核对的基线，接手时核对实际代码。LLMChat 当前要求 API v1，旧于 3.1.2 的提供者写能力会被限制。
- 参数 schema、能力 ID、`nb_unibot_*` 名称、effect、long_task、状态和制品结构是跨插件契约。结果使用 `services/results.outcome` 的 `status/code/data/public_hint/fallback_text/artifacts/effect/retryable`，拒绝、缺配置、结果未知不能伪装成功。改契约须评估兼容并按需调整版本，不悄悄复用旧 ID 表示不同操作。
- Bot、用户、群、权限和附件来自可信调用上下文；模型参数不能指定身份、SUPERUSER 或任意本地路径。写入沿用准备预览、真实用户确认、执行时重查权限和约束；预览后目标、附件、群策略变化须拒绝或重新预览。
- `services/native_catalog.py` 由 LLMChat 的 `tools/export_native_catalog.py` 生成，不手改；命令增删改名时核对生成器及其写入范围，再更新原命令目录、schema、帮助与 README。未获跨仓库授权时只记录依赖，不运行会改其他仓库的生成器。
- 原生与 LLM 入口共用 `command_receipts`、`services/sync.py`、`job_store.py`、`storage.py` 和 `presentation_store.py`。保持真实作用域、幂等、取消、停机中断、未知副作用不自动重试及补发只读缓存；LLM 通知器缺席时不能阻断原生结果。
- 综合插件拥有 `feature_manager.py`、`services/group_policy.py`、`services/policy_guard.py` 和统一设置/维护入口。UniBot 的 `services/group_policy.py` 动态适配该策略，不能另建同名配置作为第二策略源。

## 统一群策略的实现边界

- 唯一持久化策略源为宿主 `config/nonebot_plugin_unknnownnn/feature_config.json`：`_policy_version=2`，每群 `_plugins.unibot` 总开关与 `_unibot` 单项。单项为 `songs/scores/player/recommend/story/guess/maintenance/help`，以综合插件实际目录为准。
- 原生命令模块映射由综合插件 `MODULE_FEATURES` 维护，能力由 `capability_feature` 映射。增加功能要同时检查两者、动态目录、设置入口和实际执行点；旧工具缓存不能替代重新判定。
- 当前没有综合策略提供者时，群业务默认拒绝；私聊不受群开关影响，仍受各自身份权限约束。独立加载成功不代表群功能默认启用。`job.list/get/cancel` 保持本人任务管理权限，`job.replay` 是发送行为，不能套用查询例外。
- 提交、执行、网络/写入副作用、延迟通知及 Bot 出站均核对当前策略。管理设置和任务管理例外不扩大到业务执行；LLMChat 关闭时不得新增群上下文。
- 迁移须以已加入群及旧有效状态为输入，备份原策略，离线试算、逐项对比、原子保存并支持回滚。未知群/新增项关闭；总开关关闭保留单项、预设和概率。禁止导入时推断旧群启用情况或覆盖文件。

## 后续落雪国服与 OAuth 改造约束

- 已确认的后续方向是仅保留落雪国服数据源并支持 OAuth 直接查分。本阶段只整理目录和规则，不删除 Rin/CSV 导入、chunirec/naominet 路径，不改国服 B 图、本地日服 B30、推荐、OP 或渲染行为。
- 开始实施前列清影响面：曲库合并与 ID/定数映射、`get_player_score` 的解析和本地提交、个人资料 Rin 回退、`best_rating` 与 `services/player` 的 b/b30 分支，以及维护任务、历史队列、LLM schema/目录、综合设置面板、帮助和配置模板。数据源退役不能只删 HTTP 请求。
- 对旧 `songlist.json` 的 `data_source/chunirec_id/rinnet_id/lx_*/chunirec_*`、用户成绩、手动记录、资料与别名建立明确迁移规则。先备份和只读试算，报告映射冲突、无法匹配、覆盖与保留数量；迁移可重复执行、有版本与回滚路径。不得以空数据覆盖错误响应，不将日服定数直接解释为国服，不静默抛弃用户成绩。
- OAuth 实施前查阅落雪当时的官方协议，确认国服支持、端点、scope、授权模式、回调部署方式与刷新/撤销语义，不从现有开发者 `lxns_token` 推断用户授权能力；不提前写死未核验的协议细节。
- 用户授权绑定真实 Bot/用户身份；授权在私密通道完成，群内不出现凭据。校验一次性、限时且绑定请求人的 state，防重放与串号；PKCE 等按实际协议支持实施。access/refresh token、授权码和客户端密钥不进入模型上下文、普通日志、仓库或公开回执，令牌存储与备份需明确保护方式。
- 处理过期、撤销、刷新并发、拒绝授权、限流和网络失败；最小 scope，仅访问本人授权数据。OAuth 凭据与开发者密钥的用途和迁移分别说明，不改写或删除既有好友码绑定。解除授权对凭据、本地成绩保留和历史任务的影响须事先定义。

## 跨仓库变更与针对性验证

- 默认仅改本仓库。涉及 LLMChat、综合插件或 Server Companion 契约时，先只读核对其规则、Git 状态、调用方和测试；列明需配套的版本与迁移顺序。只有获得相应范围授权才写其他仓库，不以共享代码为由顺手同步。
- 本仓库暂无正式测试配置；现有跨插件隔离测试位于 LLMChat 的 `tests/`，包括 `test_provider_services.py`、`test_context_catalog.py`、`test_sync_runtime.py`、`test_story_jobs.py`、`test_isolated_host.py`、`test_error_notices.py`。使用前核对测试的宿主、依赖与临时数据路径，不能把邻仓旧通过记录算成本轮验证。
- 文档和本地整理：检查差异、链接、忽略规则、归档清单及校验值，证明运行数据和跟踪业务代码未变，再做语法检查；无需安装宿主或触发真实查分、生图与发消息。
- 接口/策略变更：隔离加载、缺少 LLM/综合提供者、schema 与目录一致性、真实用户权限、群/私聊隔离、预览后关停、排队后关停、延迟通知、旧工具拒绝与本人任务管理；用真实 Bot + fake adapter 断言被拒绝时底层 API 未调用。
- 存储/数据源变更：脱敏 fixtures 覆盖旧数据、重复导入、并发提交、部分写入失败和迁移重跑/回滚；`storage.commit_scores` 只保证单文件原子替换，成绩与资料两个文件不是事务。OAuth 另覆盖 state 重放/串号、过期/撤销、刷新并发、密钥脱敏、失败不覆写本地成绩。
- 测试只使用临时目录与 mock 网络/适配器；没有本轮真实验收授权时，不连接真实账号、不发送群消息、不写线上配置。报告实际命令和结果，区分语法、mock、宿主与线上验证。本阶段不部署、不重启服务、不修改 App 交接文件。
