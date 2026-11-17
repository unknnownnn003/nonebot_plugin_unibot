# nonebot-plugin-unibot

基于 NoneBot2 / OneBot v11 的 CHUNITHM 国服插件。曲库与谱面统一来自落雪国服；玩家资料和完整成绩统一使用落雪 OAuth 个人 API，无需在机器人内上传或手动同步。

## 账号与玩家查询

| 指令 | 行为 |
| --- | --- |
| `/落雪授权` | 本人网页授权；在已启用相关功能的群内返回链接，网页验证后直接在群内确认，按提示及时撤回消息。 |
| `/落雪确认 <网页确认串>` | 本人在当前聊天完成绑定，无需私聊；群内发送后立即撤回确认指令。确认串不是落雪授权码。 |
| `/落雪授权状态` | 在当前聊天查看本人 OAuth 状态，不显示凭据或账号资料。 |
| `/落雪解绑 确认` | 清除本地凭据、取消待完成授权、使个人查询缓存失效；还需在落雪应用管理页撤销远端授权。 |
| `/b30` | 所有版本的有效普通谱面按落雪单谱面 Rating 排序取前30，缺位计零，均值除以30；不是玩家总 Rating。 |
| `/b50` | 旧版本 BEST 30 + 当前国服版本 NEW 20，按歌曲版本分区，互不重复；缺位计零，合计除以50。 |
| `/个人信息`、`/chuinfo`、`/分数` | 读取完整 OAuth Player 并沿用玩家资料图片。 |
| `/chulist <条件> [--nocat]` | 筛选已配信 MASTER/ULTIMA，逐谱面合计 OP 与理论值；未记录计零，合计不是游戏内按曲最高的玩家总 OP。 |
| `推什么 [等级或 aj/ajc/fc]` | 基于本人 OAuth 成绩推荐可推谱面。 |
| `/chu成绩 <歌名或ID> [难度]` | 查询该曲全部已记录难度；支持别名，难度可为 bas/adv/exp/mas/ult/we 或0～5。明确关联的 WORLD'S END 归入原曲，保留实际谱面 ID、版本及属性。 |

`/b` 已删除，不保留别名。B30/B50 排除 WORLD'S END、禁用和未配信谱面。B50 的当前版本由曲库更新时读取落雪公共配置后保存；缺少版本信息会要求管理员 `/更新songlist`，不会取版本目录最大值或以旧曲新增 ULTIMA 判断新曲。单曲 WORLD'S END 只展示实际成绩与属性，标明不计普通 Rating/OP。

普通「是什么歌」只查公共曲库，不查询个人成绩。相同标题的不同歌曲不会自动合并；有歧义时使用原始歌曲 ID。单曲成绩不会把未记录的难度当作零分；未知 ID 不按标题猜测映射。

完整 Player 与 Score[] 以 Bot、真实 QQ 和授权代次隔离，使用60秒内存缓存，最多保留64份；重启后自然失效。超时、异常、空响应和坏数据不会覆盖有效快照，也不会回退到旧成绩文件；请求失败会明确提示。上游撤销最迟在下次个人 API 请求或刷新失败时被发现，收到401后停用对应凭据并使缓存失效。绑定解除后旧任务不能继续发送、补发或提供玩家材料。

旧 `/bind`、`/lxupdate`、`/chuupdate`、`/cf` 及其上传/手动别名已退役，对应代码与开发者密钥查分已删除。OAuth 是唯一正式绑定方式。旧好友码文件与迁移备份不再参与查询；旧成绩可在核对范围后离线清理，不自动导入新模型。

## 曲库、猜曲与剧情

| 指令 | 行为 |
| --- | --- |
| `<曲名/别名/ID>是什么歌` | 公共曲目详情与候选。 |
| `/添加别名 <ID> <别名>` | 添加本地别名；`/删除别名` 仅 SUPERUSER。 |
| `/查看别名 <查询>` | 查看本地及上游别名。 |
| `/chuguess [bas/adv/exp/mas/ult]`、`/猜 <歌名>` | 群内曲绘猜曲；`/提示`、`/放弃` 沿用原行为。 |
| `/查剧情 <查询>`、`/随机剧情`、`/剧情分类`、`/剧情列表` | 查询剧情与分类。 |
| `/更新songlist`、`/更新曲绘 [full]`、`/更新收藏品`、`/更新剧情` | SUPERUSER 资源维护；完整重下仅曲绘。 |
| `/chuhelp [文字或分类]` | 指令帮助。 |
| `/中二任务 [任务ID]`、`/取消中二任务 <任务ID>`、`/补发中二任务 <任务ID>` | 管理当前 Bot、会话中本人的任务。补发玩家结果要求原授权仍有效。 |

群功能遵守综合插件唯一群策略，新群默认关闭。OAuth管理使用「OAuth 授权与账号管理」，沿用功能键 `scores`，B30/B50/个人信息/进度/单曲成绩使用 `player`，推荐使用 `recommend`。三插件已同步清理旧标签与日服维护选项，现有开关和用户选择保持不变。

## 安装与配置

作为 NoneBot 本地插件加载 `nonebot_plugin_unibot/`。依赖：

```powershell
pip install -r nonebot_plugin_unibot/requirements.txt
```

参照 [.env.example](.env.example) 配置 `LXNS_OAUTH_ENABLED`、`LXNS_OAUTH_CLIENT_ID`、`LXNS_OAUTH_CLIENT_SECRET`、`LXNS_OAUTH_REDIRECT_URI` 与独立的 `LXNS_OAUTH_ENCRYPTION_KEY`。OAuth需FastAPI宿主、有效HTTPS回调和受保护 LocalStore。默认关闭；未配置时公共曲库功能仍可用，玩家查询明确返回不可用。`LXNS_TOKEN` 不再读取，请勿将 OAuth Token 填入旧键。

凭据加密存于 LocalStore `oauth/credentials.sqlite3`；Client Secret 和存储密钥从宿主私密配置读取，不能提交真实 `.env`。配置、回调与Caddy路由说明见 [OAuth说明](docs/lxns-oauth.md)；本仓变更不会自动配置服务器。

## 开发与升级

- [AGENTS.MD](AGENTS.MD)：当前开发规则与精简原则。
- [第三阶段 D 生产部署记录](docs/production-oauth-deployment.md)：实际版本、迁移、HTTPS、真实 OAuth、验收及回滚边界。
- [第三阶段 C 集成与统计语义](docs/oauth-integration.md)：B30/B50、群策略、能力目录和跨插件验证。
- [第三阶段 B 玩家查询说明](docs/oauth-player-api.md)：统一模型、缓存/授权边界、退役范围、邻仓配套项与测试。
- [曲库模型说明](docs/lxns-cn-catalog.md)：ULTIMA、WORLD'S END 与原始ID规则。
- [LocalStore迁移](docs/localstore-migration.md)：离线复制、校验与回滚。运行路径只由 `services/paths.py` 管理。
- [开发工具](tools/README.md)：显式脚本；命令目录使用本仓生成器，不能用旧邻仓生成器覆盖。

本阶段沿用现有图片布局与样式，视觉重设计留到第四阶段。测试使用合成数据与模拟网络，不代表真实OAuth账号或服务器已验收。
