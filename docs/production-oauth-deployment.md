# 第三阶段 D：生产部署与真实 OAuth 联调

日期：2026-11-17（UTC+8）。本轮获准部署三个插件、迁移持久数据、配置 Caddy 和重启 NoneBot；未重启 NapCat、未退出 QQ、未修改其他服务。

## 实际版本与 Git

| 插件 | 生产运行源码提交 | 发布结果 |
| --- | --- | --- |
| UniBot | `bcb9a05e36cfdcfe6cb9268c7aba8d941dce9a21` | 已正常推送 origin/main |
| LLMChat | `c1adf6a2a10d00d595d499d2861628bac6527c92` | 无远端，按提交文件安全传输 |
| 综合插件 | `97e0116a8de89f8b644965e4a500b560df5a88b2` | 私有 origin/main 已正常快进推送42个提交 |

三仓已跟踪工作区接手时干净；邻仓既有未跟踪资产、预览和审查文件保留，未混入部署。生产 Python 源码逐一对应本地 Git 历史，换行差异单独归一化核对，没有覆盖独立线上源码。没有执行旧部署脚本。部署包只含已提交运行文件；不含环境、凭据、玩家数据或本机迁移原件。

## 宿主、存储及备份

- 服务：`robinbot.service`；工作目录 `/root/robinbot`；启动 `/root/robinbot/.venv/bin/nb run`，Python 3.12，NoneBot 2.4.3。宿主通过 `plugin_dirs=["plugins"]` 加载三个插件。
- 插件目录：`/root/robinbot/plugins/nonebot_plugin_unibot`、`/root/robinbot/plugins/nonebot_plugin_llmchat`、`/root/robinbot/plugins/nonebot_plugin_unknnownnn`。
- 实际 LocalStore 基目录 `/root/robinbot/data`，UniBot 为 `/root/robinbot/data/nonebot_plugin_unibot`，无插件路径覆盖。
- 主备份：`/root/backups/unibot-phase3d-20261117-222409`。停写后备份生产代码、全部 LocalStore、config、生产环境文件、宿主依赖清单、服务单元和 Caddy 配置/状态。`production.tar.gz` 为721855239字节，3005个文件逐一与 `manifest.json` 的 SHA-256 校验一致；另存 `archive.sha256`。
- `code-before/` 为首次替换前完整生产插件。真实授权后两次修复前，分别备份最新 UniBot LocalStore 到 `before-we-fix/localstore/`、`before-presentation-fix/localstore/`，对应代码也保留。`deployed-versions.json` 记录最终运行提交。

迁移按 `localstore-migration.md`，使用实际宿主配置运行正式 `data_migration` 实现。旧源 `/root/robinbot/plugins/nonebot_plugin_unibot/data` 共54文件、7908153字节；目标原有命令回执数据库完整保留。文件大小和 SHA-256 全部一致，完成标记已发布，旧源未删除。迁移前目标另存 `/root/robinbot/data/.nonebot_plugin_unibot.before-migration-c1201d4da0834249b5d53592565abb0f`。SQLite检查连接显式关闭并正常checkpoint后再预检，没有手工删除journal。

群策略仍为v2，13个群保留全部现有选择。`feature_config.json` 部署前后摘要一致；未重置群开关、管理员、绘图额度或权限。UniBot任务、回执、媒体数据库完整性检查均为ok。

## OAuth、HTTPS和服务

NoneBot实际环境为prod，已确认加载服务器 `.env.prod`。五项 `LXNS_OAUTH_*` 均存在，启用生效，回调地址匹配，Fernet格式有效；未修改或重新生成密钥，未复制本地真实密钥。`.env.prod` 为600；OAuth目录700、凭据库600。

实际 Caddy 位于 Docker `gateway-caddy-1`，使用host网络，配置 `/opt/ai-stack/gateway/Caddyfile`，并非宿主 `caddy.service`。在现有 HTTPS IP 站点中新增：

```caddyfile
handle /oauth/lxns/callback {
    reverse_proxy 127.0.0.1:8080
}
```

修改前备份，`caddy validate` 成功后通过容器内 `caddy reload` 生效。保留根路径Open WebUI、`/companion/*`、原有重定向、8443 NapCat代理和TLS配置。公网HTTPS证书验证成功，根路径200，回调缺参/错误state/重复错误state均400且no-store，不反射参数；相邻带斜杠路径仍由原站点处理。初次启动尚未监听时出现一次502，随后复测400正常。

Caddy未配置请求访问日志；ASGI在日志前清除回调query；新增Loguru保护，隐藏确认指令和授权URL日志，保留宿主patcher/extra。测试请求参数未出现在Caddy或NoneBot日志。生产新增依赖仅PyJWT和cryptography，宿主pyproject同步声明；没有重装运行环境。

`robinbot.service`、`napcat.service` active，三插件真实启动加载且OneBot连接成功。NapCat主PID仍1457342，启动时间仍为11月15日，未重启或下线。用户收到真实私聊回复和查询图片，也证明QQ消息链路正常。Open WebUI容器持续healthy、`/health`200，根路径200；Caddy容器持续运行；8443 NapCat入口保留原重定向。

## 验证矩阵

| 范围 | 实际结果与边界 |
| --- | --- |
| 本仓测试 | 最终67项，66通过、1项Windows符号链接权限跳过；真实三插件隔离加载、OAuth、策略、缓存、取消、补发和解绑保护覆盖通过 |
| 邻仓定向回归 | 99项全部通过；目录/能力快照检查、compileall、diff检查通过 |
| 两邻仓全量基线 | 综合410通过/21历史失败；LLMChat 368项中22失败/33错误；失败用例集合与阶段C记录一致，无新增，不报告全绿 |
| 生产模块加载 | 三入口在生产环境离线加载、实际服务启动均成功，无启动traceback；动态发现新B30/B50/单曲能力，退役工具不出现 |
| 公共曲库 | 官方site/config读取当前CN版本24000，再显式请求该版本；1466首，source=lxns_cn/schema2/rating_version=24000；完整旧曲库备份后原子更新 |
| 真实OAuth | 用户自行网页登录同意、QQ私聊确认和授权状态成功；这同时验证应用回调一致性与真实Token交换。没有代替用户登录 |
| 真实个人API及渲染 | 在服务器内存读取已同意测试账号的固定本人端点；B30、B50、profile、chulist均生成PNG，推荐及WE单曲返回ok；测试结果未落盘或发送 |
| 真实消息送达 | 用户重试确认可以生成，提供B50送达截图。后续字体、等级和接单提示修复已部署；四个渲染模块各权重均验证为正确字号的实际字体文件，合成中日文字和总等级预览已目视检查；最终视觉/提示体验待用户再确认 |
| 未授权、解绑、401、群关闭、取消、有效state重放 | 自动化模拟与隔离宿主通过，证明拒绝时不调用底层API、不泄露材料。未为验收撤销真实账号或改生产群开关；不冒称这些路径已做真人破坏性测试 |
| LLM联动 | 真实生产环境动态发现及本地调用/策略/队列测试通过；未付费调用真实模型做自然语言端到端验收 |

## 本轮修复

1. 真实API对WE省略OP。仅WE允许缺少不适用指标，普通谱面仍严格校验；不补零，不读旧成绩；WE单曲只保留实际存在的upstream_metrics。
2. 失败查询曾被缓存成成功文本媒体并随后发“查询结果已发送”。现在保留原失败，由原生通知单次投递；回归证明不stage失败媒体、不重复成功提示。
3. Linux实际字体文件为Regular，旧Normal路径导致默认小字体。B30/B50、个人信息、进度和帮助改用实际Regular、自定义思源字体及Regular回退。
4. B30/B50/进度统一显示 `reborn_count * 100 + level`，个人信息沿用同口径。合成测试验证30级加4次转生为430，真实账号总等级口径已核对。用户名保持API原文，不改写资料。
5. 新原生图片查询任务在统一队列gate内先发送“正在生成，请稍候”，再允许worker执行；重复任务不重复提示。生成图片后不追加统计说明，元数据仍可供任务查询/模型材料使用。文本单曲、错误及无数据提示保留。剧情图片任务也使用该接单入口。

## 回滚与剩余验收

若出现严重回归，先停止robinbot，完整私密备份当前LocalStore（尤其OAuth和任务/媒体），再恢复本轮确实替换的代码。首次旧代码在 `code-before/`，最近修复前代码在对应 `before-*/`。需要回滚Caddy/宿主pyproject时，从已校验tar中只提取明确文件，核对当前文件没有新修改后恢复，校验Caddy再重载。只重启NoneBot并检查OneBot和Open WebUI。

**禁止把旧LocalStore或旧环境备份直接覆盖当前数据，不删除OAuth库、迁移标记或密钥。** 迁移后的有效授权、任务和媒体必须保留；若回滚旧业务代码，需要单独评估迁移后增量，不能宣称旧源包含新数据。pip新增依赖可保留，不必为代码回滚卸载。

后续用户可私聊或在已明确授权的测试群重试图片命令，确认接单提示、清晰字体及总等级显示。真实远端撤销/重新绑定、群关闭/取消和LLM自然语言调用仍按需要另做真人验收；自动测试结果不能替代这些验收。此前部署脚本入口保护和固定群号技术债按本轮要求未处理。第四阶段图片UI重设计未开始。


## 后续更新：OAuth 群内完整流程

同日用户明确要求授权全程在群聊进行、无需私聊并提醒撤回。运行提交 `bcb9a05`：授权链接、本人确认、状态和解绑直接在当前聊天返回；网页也提示回到发起授权的聊天。授权与确认的成功/失败回复提醒撤回链接和确认指令，机器人消息须由群管理员撤回。私聊仍可管理本人授权，但不再是必需步骤。真实Bot/QQ身份、群策略、原群策略及一次性确认保护不变，不发送Token或落雪授权码。

67项本仓测试（66通过、1权限跳过）、99项邻仓回归、原生命令目录/语法/差异检查通过。新增群内加密凭据确认、他人确认拒绝、重复确认拒绝、群开关拒绝、撤回提醒及全程不调用私聊发送的覆盖。帮助图片与用户文档同步。

部署前确认无活动UniBot任务，停机备份 `/root/backups/unibot-group-oauth-20261117-231256`，含替换前两处源码及当前完整UniBot LocalStore，逐文件摘要验证一致。仅更新 `services/oauth_runtime.py`、`help.py` 后启动NoneBot，已有OAuth、任务和群权限原样保留；Caddy和NapCat不改动。回滚时只恢复该备份的两处源码，保留当前LocalStore，不能以旧授权库覆盖新数据。
