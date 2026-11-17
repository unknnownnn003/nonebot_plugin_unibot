> 本文记录第三阶段B基线；B30/B50、统计口径及邻仓配套以[第三阶段C](oauth-integration.md)为准，`/b`已删除。

# 第三阶段 B：OAuth 个人查询与旧业务退役

基于 `main` 的 `60c3894`，2026-11-17完成本地改造。只改UniBot，邻仓只读检查，没有部署服务器、改Caddy或连接真实账号。

## 一条正式路径

依据 [落雪国服 API](https://maimai.lxns.net/docs/api/chunithm) 的个人接口和 [OAuth API](https://maimai.lxns.net/docs/api/oauth) 的标准响应封套，使用 `read_player openid` 授权访问：

- `GET /api/v0/user/chunithm/player` → 完整 `Player`。
- `GET /api/v0/user/chunithm/player/scores` → 完整 `Score[]`。

请求使用本人授权的 Bearer，URL没有QQ、好友码、其他玩家ID或模型可选地址。无开发者密钥回退，不以历史本地成绩补空。`PlayerData` 验证响应封套、必要字段、有限数值、分数范围和唯一 `(id, level_index)`，保留 Player/Score 中全部上游字段及未知属性；重复成绩身份不静默覆盖。公开API返回200不代表数据有效。

`Snapshot` 是唯一玩家数据模型。B30、资料、OP、推荐和单曲成绩共用此模型，不再读写 `score/*.json`。B30用个人API的rating，OP/推荐用API的over_power；国服Catalog负责曲目/谱面身份、常数、版本及资源。完整玩家资料可供原资料图与受控结构化结果使用，不保留Rin资料回退或双来源Rating文字。

`/b` 仅为 `/b30` 的别名，统一将匹配国服普通谱面的个人成绩按API Rating排序取前30，分母固定30；WE不参加标准Rating。它表示单榜B30平均，不能冒充游戏当前B30+N20总Rating；玩家官方rating仍在个人资料中展示。原N20、版本猜测、开发者best查询、手动/本地双模型及推荐卡分支均删除。图片复用现有B30、个人资料、进度和推荐布局，仅替换数据来源文字；视觉重设计留给第四阶段。

## 单曲成绩与身份

`/chu成绩 <歌名或ID> [难度]` 和 `player.song_scores` 使用既有查歌/别名选择。无难度时输出该曲全部已记录成绩，难度支持bas/adv/exp/mas/ult/we或0～5；无记录明确说明，不伪造零分。普通「是什么歌」不读取个人接口。

同名歌曲仍有歧义，要求原始ID。WE按Catalog中明确origin_id归入原曲查询家族，即使输入独立WE ID也能筛选原曲家族中的WE；每条成绩仍返回真实 `id/level_index`、clear/combo/chain、rating/OP、时间及元数据列表。ULTIMA用谱面自身版本。孤立/冲突/链式WE保持独立，不按标题合并。

API的成绩键只有id和level_index；同一WE记录有多条同难度元数据时，保留 `charts[]`、报告 `metadata_ambiguous`，不擅自选择或覆盖。未知数值ID或无法匹配难度计入unmatched_scores，原响应快照保留；不按标题改号、套日服常数或纳入B30。图片结果另发未匹配数量说明，建议更新国服曲库重查。

## 缓存、刷新和迟到响应

- 快照只在内存，TTL60秒，LRU最多64份；重启自然失效，不再建立第二份可作为查询源的磁盘成绩库。既有任务数据库会持久保存已生成的查询结果和媒体，用于任务查询/补发，它们不作为新查询数据源；解绑后按下述授权检查隐藏，不能将整个LocalStore作为网站静态目录公开。身份键为 `(Bot, QQ, binding_id)`，同一人的不同群/私聊使用同一份本人数据，但每次查询和发送按当前请求作用域判定权限。
- 每次缓存命中仍读取当前凭据状态；解绑、明确失效、换绑或配置身份变化会阻止旧缓存，并清除本进程对应内容。其他进程撤销会在读取凭据时被发现，旧绑定键不复用。
- `binding_id` 是一次真实用户授权；第三阶段A已有active凭据首次使用时，在加密事务中补一个稳定ID，不改Token或请求上游。刷新改变reservation但保留binding_id；重新授权一定产生新binding_id。不能以轮换代次代替账号授权代次。
- 单用户查询singleflight，串行发布全量快照。资料和成绩两次请求全部成功、验证通过，再检查身份/策略/授权/取消并同步发布；不会把其中一半当完整结果。失败、超时、限流、空列表或坏JSON保留最后有效快照原样，但不把过期快照作为成功回退。
- HTTP请求固定可信端点、HTTPS校验、12秒超时、禁用环境代理与重定向、不自动重试；最多16MiB响应。错误只用固定码与文案，不输出Token、上游异常文本或原始响应。
- 401用实际被拒绝的Token原子失效对应凭据。迟到401若Token已经轮换，不撤销新Token，也不驱逐新快照；此请求要求重查。令牌刷新沿用第三阶段A的跨进程快照CAS与独立reservation，未知轮换结果不盲目重试。
- 官方未公布主动撤销端点或撤销推送，本地无法实时知道远端操作；最长在60秒缓存窗口之后的下一次个人API请求，或下一次刷新失败时发现。`/落雪解绑 确认`立即阻止本地使用，并提示用户在落雪应用管理页撤销。

空Score[]也可能表示玩家尚未同步到落雪，此时明确NO_SCORES，提示到落雪同步游戏数据；机器人内不恢复手动上传/同步入口。用户可稍后重新查询。

## 队列、发送和LLM边界

- 原生和LLM查询走同一队列。提交时获取真实OneBot身份及binding_id；任务只存随机授权标识，不存Token/完整快照。执行必须匹配原Bot、QQ、群/私聊和原授权。
- 网络等待可取消；查询结束、渲染后、媒体落盘及每次底层发送前重查取消、当前群策略和授权。线程渲染已启动时观察其结束，再丢弃因解绑而失效的图，不能因拿到图片就继续发送。
- 解绑后的迟到HTTP响应不能发布缓存；换绑后的旧排队任务不能改用新账号继续执行。公开任务结果对失效授权隐藏个人内容，本人任务查询/取消保留；旧素材补发和模型材料访问也核验原授权。
- 提供给LLMChat异步通知队列的玩家结果只保留通用状态与投递元数据，避免其延迟通知持有完整个人资料。模型材料读取前后复核原授权与当前策略；已经在授权有效时读出或送达的内容无法通过解绑撤回。
- API_VERSION保持1，CAPABILITY_VERSION改为4.0.0，公开24项能力。新增 `player.song_scores`，`player.best`参数精简为 `{}`；移除player.bind、score.submit、score.sync、score.import_file及其schema。OAuth命令仍不向模型开放。当前任务由原生命令接管时回执去重优先，否则旧缓存调用返回CAPABILITY_RETIRED，不执行旧代码。
- scores策略键现在仅用于OAuth管理；player用于B30/资料/进度/单曲成绩，recommend用于推荐。既有综合capability_feature的player前缀及模块映射已经覆盖，无需擅改群配置。

## 删除范围与历史数据

删除 `get_player_score.py`、`services/scores.py`、`services/uploads.py`、`services/attachments.py`；删除旧好友码命令/读写、开发者玩家请求、Rin/CSV/压缩包解析与文件下载、手动写入、合并/迁移分支、成绩同步worker及submit接口、双B图/版本逻辑和无消费者的Rating/OP旧计算。`storage.py`只保留公共曲库/别名需要的原子JSON替换，`sync.py`只保留通用队列、取消和补发。

同时删除 `lxns_token` 配置和示例，清理LLM描述、帮助、README、命令目录、旧fixture；没有保留空壳模块来满足旧测试。历史队列和工具只用小型退役拒绝，无旧业务兼容层。

用户追加允许删除旧成绩。本机检查的 LocalStore `score/` 和源码迁移原件 `data/score/` 均为空，因此**实际删除旧用户成绩文件0个**；没有假报已清理服务器成绩。历史好友码文件停用保留，既有迁移原件、归档和备份不动。其他机器升级时若需清理旧score/，必须先停止写入者、核对真实LocalStore与备份范围后按授权离线处理；源码更新本身不在导入期删用户文件。

## 邻仓待配套项目（未修改）

- 综合插件 `services/group_policy.py` 的player/recommend前缀和现有模块映射适用；`get_player_score`映射已无消费者，可在获授权后清理。scores显示名仍叫「成绩同步与绑定」，应更新为OAuth账号管理；不得因此改功能键或重置群选择。
- 综合插件 `settings_flow.py` 仍有chunirec维护项，UniBot继续明确拒绝；需邻仓单独授权清理该菜单。
- LLMChat动态发现API v1能读取新目录，版本4.0.0满足最低版本限制；无需保留已退役工具。其 `agent_tools.py` 的WRITE_CAPABILITIES、友好名称仍包含旧绑定/上传/同步，应配套删除并增加新单曲成绩显示名。
- LLMChat `docs/v3.1/CAPABILITIES.json`、旧生成器及若干历史测试仍使用3.x契约。新目录只能由本仓生成器导出；邻仓快照/测试应另行更新，不能反向把旧命令生成回UniBot。
- 本仓邻仓回归适配器明确列出14项过时断言（此前6项，本轮新增8项），保留 `--legacy-contracts` 复现；默认仍运行资源、剧情、回执、权限及群报错等有效测试。旧sync模块在过滤后0项，不计通过；队列所有权/取消已用本仓真实OAuth宿主测试替代。

## 验证

本轮实际检查：

- `python -m unittest discover -s tests -v`：61项，60通过，1项因Windows账号无符号链接创建权限跳过。本轮新增14项个人API/完整快照/缓存/错误/隔离/迟到响应与真实宿主测试，并更新被替代的旧断言。
- `python tests/run_neighbor_regressions.py`：48项有效契约通过；14项过时断言明确列出且不计通过。资源维护、剧情、回执和群报错仍通过，邻仓文件未修改。
- `python tools/export_native_catalog.py --check`：24项能力目录一致；compileall使用独立缓存，UTF-8/文档本地链接、Git差异检查通过。
- 真实LocalStore和源码迁移原件的3228个文件分别全部与第一阶段SHA-256清单一致；两边旧score/均0文件，真实路径没有创建OAuth库，旧用户成绩实际删除0个。
- 独立审查发现的可空combo渲染及任务公开结果在异步元数据读取期间撤销授权的问题，均已修复并用精确竞态回归验证；任务列表在整批元数据读完后作最终授权判定。

测试使用临时复制插件、合成完整Player/Score、真实RSA/Fernet/SQLite/Pillow、真实NoneBot/OneBot/FastAPI与mock网络。没有真实OAuth账号、群消息、服务器或Caddy动作；生产TLS、OAuth应用配置、权限和真实空数据语义需要单独授权验收。
