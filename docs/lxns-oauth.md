# 第三阶段 A：落雪 OAuth 授权与凭据管理

> 本文记录第三阶段A实施时的基线。第三阶段B已替换旧成绩路径、退役开发者密钥和上传/同步能力；当前查询及接口以 [OAuth个人查询](oauth-player-api.md)、README和AGENTS为准。回调、HTTPS/Caddy和凭据保护方案继续适用。

2026-11-17，基于 `main` 的 `e4080f4`。本轮只实现授权与凭据管理；没有改写成绩获取、好友码绑定、曲库、图片渲染或历史用户文件，没有修改邻仓、Caddy 和服务器。

## 官方协议核对

依据 [落雪 OAuth 接入指南](https://maimai.lxns.net/docs/oauth-guide)、[OIDC 发现文档](https://maimai.lxns.net/.well-known/openid-configuration) 和 [OAuth 发现文档](https://maimai.lxns.net/.well-known/oauth-authorization-server)。官方仍标注功能处于测试阶段，上线前需重新核对。

- 仅请求 `read_player openid`，不申请写成绩、个人密钥、邮箱或用户名权限。
- 授权端点为 `https://maimai.lxns.net/oauth/authorize`；令牌端点为 `https://maimai.lxns.net/api/v0/oauth/token`。使用授权码流程、机密客户端 `client_secret_post`，同时使用 PKCE S256；不支持用户把上游授权码贴给机器人。
- OIDC issuer 固定为 `https://maimai.lxns.net`，JWKS 为 `https://maimai.lxns.net/.well-known/jwks.json`；发现文档必须与固定可信端点一致，禁止从 Token 的 URL 字段任意获取密钥。验签仅接受 RS256 和唯一匹配 kid 的 RSA 公钥；校验 iss、aud、exp、iat、sub，首次校验 nonce，多受众时核验 azp。
- 身份使用 `(iss, sub)`，不使用邮箱或名字。每个 Bot 的同一外部身份不能绑定多个 QQ；Bot/QQ 都来自真实 OneBot 事件，不接受参数代填。
- 文档说明访问令牌有效期15分钟，刷新令牌30天且每次成功刷新立即轮换。代码使用返回的 `expires_in`，提前60秒刷新。刷新也校验新 ID Token 和原 sub；按照官方约定，刷新 ID Token 不要求初次 nonce。
- **当前两份发现文档没有 `revocation_endpoint`，接入指南也没有主动撤销 API 契约。** 本轮不猜测 `/revoke`。`/落雪解绑 确认` 立即删除本地凭据及待完成请求，但必须明确告知用户到落雪应用管理页撤销；不宣称远端撤销成功。上游 `invalid_grant` 和第三阶段 B 可传入的明确401会停用本地凭据。若官方以后公布撤销协议，再补实现及测试。

## 用户流程与群策略

1. 本人在已启用 UniBot「OAuth 授权与账号管理」的群内发送 `/落雪授权`，机器人直接在群内返回授权链接，无需添加好友或私聊。打开链接后请群管理员及时撤回机器人的授权链接消息，不转发链接。私聊仍可管理本人授权。
2. 链接有效10分钟，使用随机256位 state、nonce 和 PKCE verifier；数据库只保存 state 哈希，nonce/verifier 加密。新发起使同一 Bot/QQ 的旧待授权请求失效。已完成绑定必须先明确解绑才能换号。
3. 浏览器登录本人落雪账号、同意最小权限，回调原子消费 state 后仅交换一次。未知、过期、重复、被替换或已取消的请求不交换令牌；拒绝授权也消费请求。回调不信任 URL 中 QQ、Bot 或群号。
4. 验证签名与身份后，先保存为「待本人确认」，**尚不能取得 Access Token**。页面给出本地确认串，原 QQ 必须在5分钟内回到发起授权的聊天发送 `/落雪确认 <网页确认串>`。这是 UniBot 自己的确认串，不是落雪授权码。请只确认本人刚登录的账号，不转发页面、链接或确认串。群内发送后请立即撤回本人确认指令，机器人消息由群管理员撤回。其他 QQ/Bot 的确认不能完成绑定。
5. `/落雪授权状态` 仅返回授权是否可用，不显示 sub、QQ、好友码、Token 或账号资料；群内调用时直接回复结果，无需私聊。`/落雪解绑` 显示影响说明，`/落雪解绑 确认` 才执行；旧好友码与成绩不动。群功能关闭时仍可在私聊管理自己的授权。

新增命令放在既有 `user_bind` 模块，沿用综合插件 `MODULE_FEATURES.user_bind = scores` 和内部 `oauth.manage -> scores`，没有新增第二份策略文件或修改综合插件。群发起、回调交换前、回调保存前、最终确认及群内回复前重查原群策略；等待期间关停不完成绑定。私聊不受群开关影响，但仍校验真实 OneBot 身份。群内可接受原 QQ 的本地网页确认串，但不接受 Token、客户端密钥或落雪授权码。

OAuth **不作为 LLM 工具**。现有 API v1、能力版本4.1.0和25项公开能力保持契约；好友码绑定和旧同步能力已退役。伪造的 `oauth.*` 工具在私聊返回 `NATIVE_ONLY`，群内先遵守当前策略。原生命令仍受 command_receipts 去重，目录新增独立 `NATIVE_ONLY_COMMANDS`，不会把授权链接、凭据或确认交给模型处理。LLMChat 无需为本阶段改代码。

## 配置

参考根目录 [.env.example](../.env.example)，在宿主私密环境文件中设置：

```dotenv
LXNS_OAUTH_ENABLED=true
LXNS_OAUTH_CLIENT_ID="<开发者面板创建的应用ID>"
LXNS_OAUTH_CLIENT_SECRET="<应用密钥>"
LXNS_OAUTH_REDIRECT_URI="https://152.175.241.96/oauth/lxns/callback"
LXNS_OAUTH_ENCRYPTION_KEY="<独立生成的Fernet密钥>"
```

应用面板勾选 `read_player`、`openid`，登记与配置**完全一致**的 HTTPS 回调。地址中的 IP 必须有浏览器信任且覆盖该 IP 的证书；此文档没有验证目标证书、端口或现有 Caddy 实际配置。若 IP 证书或官方面板登记受限，改用具有有效证书的域名，并同时更新面板和环境配置；不能关闭 TLS 校验解决。

在不记录终端输出的私密运维会话中生成密钥，存入受限环境文件或宿主秘密管理系统，不放群、Git、工单或普通日志：

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

运行依赖新增 `PyJWT[crypto]>=2.8,<3`、`cryptography>=42`；没有为本轮测试安装完整 NoneBot。OAuth 默认关闭，仅显式启用后注册一条 FastAPI GET 回调；无 FastAPI、路径冲突、坏配置、错误加密密钥或文件权限失败只禁用 OAuth 并输出固定提示，原查分插件继续加载。`LXNS_TOKEN` 仍是旧开发者密钥，不能用 OAuth Client Secret 或 Access Token 替换它。OAuth 配置模型用 SecretStr 隐藏密钥；宿主仍须保持生产日志等级，禁止打印完整环境配置和请求体。

## 回调与 Caddy 部署方案（本轮未执行）

插件挂在现有 NoneBot FastAPI 应用的精确路径 `/oauth/lxns/callback`。不启动第二个 Bot，不接管网站根路径，不增加管理 API。NoneBot 内网监听地址、端口、现有路由应在单独授权部署时核对。

以下只是**现有同一 HTTPS 站点块内的路由示例**，不能直接覆盖服务器 Caddyfile。`127.0.0.1:8080` 和 `127.0.0.1:3000` 都是占位端口，部署时取实际 NoneBot 和 Open WebUI 上游。若原站点有其他 route、handle、认证或 WebSocket 规则，须保留原规则并调整顺序，不机械追加第二套默认代理。

```caddyfile
# Inside the existing site block, preserving its TLS and other directives:
@lxns_callback path /oauth/lxns/callback
log_skip @lxns_callback

handle @lxns_callback {
    reverse_proxy 127.0.0.1:8080
}
handle {
    # Keep the current Open WebUI upstream and its existing options here.
    reverse_proxy 127.0.0.1:3000
}
```

采用 `handle` 保留完整路径，不使用会剥掉前缀的 `handle_path`；只有精确回调转给 NoneBot，`/` 和其他路径继续到 Open WebUI。参考 [Caddy handle](https://caddyserver.com/docs/caddyfile/directives/handle)、[reverse_proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy) 和 [log_skip](https://caddyserver.com/docs/caddyfile/directives/log_skip)。`log_skip` 名称适用于 Caddy 2.8及之后；旧版需核对对应配置。

必须在启用授权前消除所有可能记录回调查询串的入口日志：Caddy access log、错误/调试请求转储、前置代理、APM、ASGI 审计中间件及 Uvicorn。`log_skip` 只解决访问日志，不能当作其他日志的脱敏保证。插件 ASGI 中间件会在路由解析前从共享 scope 移除 query_string，只在内部取用一次，并给结果加 no-store、no-referrer、CSP 和禁止嵌入头；回调无外部资源、无个人资料，也不回显上游错误。不要把未经验证的请求/响应 body、完整URL、异常对象、数据库解密结果写日志。关闭宿主 DEBUG/trace 请求转储；NoneBot 内网端口仅对本机/受信代理开放。

单独授权上线时：先备份原站点配置，按真实版本做 Caddy 配置校验；用合成 state/code 测试精确路由、拒绝页与日志脱敏，并确认 `/`、Open WebUI 登录/WebSocket 不受影响，再进行真人 OAuth 验收。此阶段没有 SSH、改 Caddy、重载服务、连接真人账号或发群消息。

## 受保护存储、并发和失败

- 路径统一为 LocalStore 插件数据根下 `oauth/credentials.sqlite3`，由 `services/paths.OAUTH_DIR` 提供。Token、OIDC sub、PKCE verifier、nonce、确认摘要均在持久化前由 Fernet 认证加密；不保存 ID Token、玩家资料、授权码或明文 state。受保护数据库含 Bot/QQ 索引和状态元数据，仍属于个人数据。
- Linux/macOS 目录0700、数据库0600，并检查所有者；Windows 设置受保护 DACL，仅当前运行账号和 SYSTEM。拒绝链接/reparse目录、不安全数据库文件及硬链接；SQLite事务日志位于同一受保护目录。权限配置失败即停止 OAuth；系统管理员和同账号恶意进程不在此文件权限隔离范围内。
- 密钥从配置读取，不与数据库同目录自动生成。备份只包含密文数据库，密钥单独保管；不要随源码、旧 data/ 归档、聊天附件或网站静态目录发布。在线 SQLite 用一致性备份，或停写后复制；不要只复制有未提交事务的单文件。原 LocalStore迁移标记、3228个文件和源码原件均不改写。
- 暂无自动密钥轮换工具。更换密钥会拒绝打开原库，不会清空重建或覆盖；恢复正确旧密钥可恢复。需要轮换时先停用 OAuth、受限备份，再单独审查重加密迁移；不能删除库来“修复”密钥错误。Client ID/redirect 改变后旧请求和凭据不能继续使用，应由用户重新授权。
- SQLite `BEGIN IMMEDIATE` 保证 state消费、唯一外部身份、确认和刷新预约的原子性。同进程刷新按 Bot/QQ singleflight；跨进程同时比较 generation、状态和加密快照，迟到请求不能使用旧 Refresh Token。每次刷新预约使用独立随机代次，已完成操作的迟到异常清理也不能覆盖下一次刷新。并发解绑/重新授权会使旧交换/刷新写回失败，不能复活或覆盖新凭据。
- 发出刷新前先持久化 `refreshing`。网络中断、限流、无效响应、异常或进程崩溃后不盲目重试可能已经轮换的 Refresh Token：`refresh_unknown` 或陈旧的 `refreshing` 要求重新授权。单次请求12秒超时，陈旧预约120秒后可重新授权；文档明确这些保守失败会牺牲自动恢复，避免错误覆盖。明确 `invalid_grant` 清空凭据并要求重新授权。
- 待授权10分钟/待确认5分钟，过期请求在后续发起中清理；过期待确认在发起/回调事务中清空凭据、释放账号占用。没有常驻自动清理线程。解绑马上清除双方，不删除历史成绩、好友码或旧任务；远端授权仍需用户撤销。

## 第三阶段 B 接口

`services.oauth.OAuthService` 提供内部 `access_token(bot_id, qq)`、`status`、`unlink`、`invalidate_access(bot_id, qq, rejected_token)`；宿主通过内部访问器 `services.oauth_runtime.get_service()` 取得服务；未配置会明确抛出固定错误。第三阶段 B 应通过服务适配访问，不对模型、HTTP或QQ返回 Token，不能让参数伪造 Bot/QQ。

第三阶段 B 成绩获取者在每次调用及排队任务执行时重新验证真实身份、当前群策略和授权状态，再调用内部凭据接口，向固定落雪本人玩家 API 发送 Bearer。明确401用 `invalidate_access` 且匹配被拒绝的实际Token，陈旧401不能使新凭据失效；禁止自动转为开发者密钥查他人。解绑不能撤回已经发出的网络请求，因此3B还须在写成绩前复核授权代次和任务状态，避免解绑后的迟到结果写回。

本阶段没有接入 OAuth 玩家 API、自动同步或改造现有 `/lxupdate`、`/b`、`player.best`、`score.sync`。原 Rin/CSV 兼容、好友码与开发者 token、无法匹配的旧ID及持久任务仍由原路径处理。3B要结合落雪国服模型、成绩/资料提交原子性、空响应保护及任务幂等一起设计，不能只替换请求头。邻仓旧维护菜单、静态能力快照及生成器仍是之前记录的配套项，不随本阶段修改。

## 验证范围

测试仅使用合成身份、真实本地RSA/Fernet/SQLite、httpx MockTransport、ASGITransport和临时复制的真实 NoneBot/FastAPI/LocalStore。覆盖PKCE、state重放/过期/替换、拒绝、OIDC坏签名/算法/kid/claims、多人授权、Bot/QQ隔离、确认过期与账号唯一性、同进程与跨实例刷新、迟到快照、Token轮换、身份变化、invalid_grant/超时/限流、解绑竞态、坏密钥、回调脱敏与重复参数、群策略关闭不交换/不发底层API、群内确认/撤回提示、无私聊依赖及LLM拒绝。

本轮最终验证：

- `python -m unittest discover -s tests -v`：47项，46通过，1项因Windows账号无符号链接创建权限跳过；其中OAuth新增21项，包括真实隔离FastAPI/OneBot加载。
- `python tests/run_neighbor_regressions.py`：56项通过，仍按第二阶段B的明确清单排除6项过时契约；本阶段没有再增加排除项，没有修改邻仓测试。
- `python tools/export_native_catalog.py --check`、独立缓存的compileall、UTF-8/本地文档链接与Git差异检查通过。
- 原LocalStore和源码迁移原件均为3228/3228文件与第一阶段SHA-256清单一致；真实路径没有创建OAuth目录，所有授权数据只在测试临时目录。
- 独立代码安全审查发现的跨实例旧快照刷新和过期待确认占用问题已修复并加入回归；最终复核没有剩余重要问题。

真实公网TLS、Caddy规则、真人账号同意/撤销及生产日志链路均留到单独授权部署验收，不能把本地模拟称为线上闭环。
