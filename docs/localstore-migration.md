# 第二阶段 A：统一运行数据存储

本记录描述第二阶段 A 的实施与验收；第二阶段 B 的当前曲库和能力契约见 [曲库模型说明](lxns-cn-catalog.md)。

2026-11-17。基线 `b4e5000`，在 `main` 本地修改；没有改变数据源、查分、命令、权限、图片样式或服务器。

## 路径约定

`services/paths.py` 是运行路径入口，先 `require("nonebot_plugin_localstore")`，再通过 `get_plugin_data_dir()`、`get_plugin_cache_dir()` 获取当前插件命名空间。遵守 LocalStore 的宿主基目录与插件专用覆盖配置，Windows/Linux 由宿主决定，不硬编码部署路径。依赖清单显式加入 `nonebot-plugin-localstore>=0.7.0`；本轮复用已有 0.7.4 和 NoneBot 2.5.0，没有安装依赖。

| 内容 | 新位置 |
| --- | --- |
| 成绩、手动成绩、玩家资料 | 插件数据目录 `score/`，字段及命名不变 |
| 绑定、曲库、别名、猜曲记分和其他旧文件 | 插件数据目录，原相对路径不变；未知文件也完整复制 |
| 曲绘、收藏品、剧情及其图片 | 插件数据目录 `jacket/`、`character/`、`icon/`、`plate/`、`trophy/`、`story/` |
| 队列、原命令回执、媒体库和待补发媒体 | 插件数据目录 `native_*.sqlite3` 和 `native_presentations_files/` |
| 上传临时文件 | 插件缓存目录 `uploads/`，解析及提交后清理 |

唯一资源和待补发媒体为持久数据，不放入可任意清理的缓存目录。业务模块保留原名、常量类型和相对布局，只替换根路径；任务库仍沿共享成绩路径定位同一数据根。旧媒体库的 `presentations.path` 绝对路径通过完成标记映射到新的受控 `_files/`，不修改原数据库、文件或摘要，不放宽任意文件读取权限。

配置沿用 [NoneBot LocalStore 官方说明](https://nonebot.dev/docs/best-practice/data-storing)：`LOCALSTORE_DATA_DIR`、`LOCALSTORE_CACHE_DIR` 指定宿主基目录；`LOCALSTORE_PLUGIN_DATA_DIR`、`LOCALSTORE_PLUGIN_CACHE_DIR` 可仅覆盖 `nonebot_plugin_unibot`。不额外增加 UniBot 路径环境变量，不改变综合插件的策略配置位置。若仓库作为宿主工作目录，`data/`、`cache/`、`config/` 均已忽略。

## 离线迁移

必须先停止写入该源和目标的所有 Bot/工具。不要为迁移启动第二个真实 Bot。以下工具仅初始化 NoneBot 配置和 LocalStore，不加载 UniBot、启动驱动或连接适配器。应从**目标宿主工作目录**运行脚本绝对路径，使用该宿主相同的 `.env`、运行环境和 LocalStore 配置。`--data-root` 仅覆盖 LocalStore 基目录，插件专用覆盖项仍优先。

```powershell
# 默认预检：完整读取源和已有目标，不创建或覆盖数据文件
python tools/migrate_localstore.py
# 确认停写后复制、校验、发布
python tools/migrate_localstore.py --apply --offline
# 如需指定宿主基目录（示例是占位路径）
python tools/migrate_localstore.py --data-root "D:/BotData/data" --apply --offline
```

工具保留全部源文件、未知文件和空目录。所有文件按大小和 SHA-256 建清单，已有目标仅接受同名同内容或无冲突的额外文件，任何同名异内容均停止。拒绝重叠目录、符号链接、Windows junction、特殊文件及未处理的 SQLite `-wal/-shm/-journal`；发现 journal 时先确认停写并按 SQLite 规范检查点/恢复，不得直接删除 journal。

迁移在目标旁创建独占锁和 UUID staging 目录，复制旧目标与源数据，重查源、目标与副本清单，全部一致后写完成标记，再改名发布。有旧目标时将其完整改名保留为 `.<目标名>.before-migration-<UUID>`，发布失败恢复旧目标。复制错误只清理本次 staging，不删源和原目标。

完成标记 `_unibot_migration_v1.json` 在新数据根，包含版本、源/目标路径、时间、每文件大小/摘要及旧目标备份路径；属于私密数据，不提交。重复执行只核对源是否仍与原清单一致，不再次复制，更不把旧数据覆盖到已经运行更新的新目标；发现回滚后源变化则拒绝并要求人工合并评估。启动时只检查完成标记，不做全量哈希、迁移、自动补拷或线上配置写入。检测到非空旧 `data/` 且无有效完成标记则拒绝插件加载，避免默默启用空库。

不要在新版运行后把数据根换到另一空目录，也不要复制完成标记到不同目标；这是另一次显式迁移/恢复，应停机、备份并校验。嵌套加载插件需按实际 LocalStore 插件 ID 单独评估；本工具面向当前顶层 `nonebot_plugin_unibot`。

## 回滚与硬中断恢复

- 源码内旧 `data/` 是迁移时的完整原件。本轮未删除、重命名或改写它，可切回旧代码使用。新版运行期间产生的新成绩、绑定及任务不在这份原件中；回滚前停写并备份当前 LocalStore，显式评估增量，不能直接用旧快照覆盖新记录。
- 有旧 LocalStore 目标时，迁移标记记录的 `previous_target` 保留其完整副本。需要恢复时先隔离当前目标，再恢复所需快照并校验，不覆盖式移动。
- 若进程在两次改名之间被硬杀，可能出现目标缺失、staging/旧目标备份及 `.migration.lock` 同时留存。先确认没有迁移或 Bot 进程，再核对 UUID 目录、staging 完成标记和原始源；优先将旧目标备份改名恢复为原目标，或把已经校验完整的 staging 发布到目标。若目标原本不存在，也可保留 staging 作为证据后从不变源重新迁移。最后在确认无人持锁后移走 stale lock；不要盲删锁或 UUID 目录。新版缺标记会拒绝加载，不会自动重建业务数据。
- 原件、备份和清单都是私密数据；发布/同步源码应显式排除它们。恢复后重新核对摘要与数据库完整性，再启动宿主。本轮不执行服务器恢复或部署。

## 本机实际迁移结果

源为仓库 `nonebot_plugin_unibot/data/`，目标为本机 LocalStore 默认插件数据目录 `C:/Users/30578/AppData/Local/nonebot2/nonebot_plugin_unibot`。这是本机实际路径记录，不作为其他机器的配置默认值。

- 迁移 3228 文件、247,608,703 字节，源完整保留；目标此前不存在，无需旧目标备份。
- 迁移后独立核对源、目标与第一阶段私密清单，全部相对路径、大小和 SHA-256 相同；目标仅额外增加迁移标记。再次预检返回已完成，没有覆盖目标。
- 本机旧 `score/` 为空且无持久任务数据库；不据此推断服务器状态。历史 SQLite/媒体迁移使用合成数据专项验证，未触碰线上数据。

## 跨插件依赖与验证

只读核对 LLMChat `agent_tools.py`、通知与已有服务测试，以及综合插件策略、设置和维护入口；它们通过 `llm_api` 和服务接口调用 UniBot，没有读写旧源码 `data/` 的业务依赖。API v1、能力版本 3.2.0、能力 ID、schema、生成的原命令目录、模块名和群策略映射保持不变，因此不需要修改其他插件。综合插件继续独占 `feature_config.json`。

本仓库测试：`python -m unittest discover -s tests -v`。覆盖只读预检、全量复制/校验、源保留、旧目标回执和备份、冲突、重复执行、回滚后源变化、错误标记、重叠路径、journal、复制失败、源变动、发布失败恢复和并发锁；真实 NoneBot/LocalStore 在临时复制的包及合成数据中加载，验证插件专用配置覆盖、各模块路径、队列/媒体/回执归属、原 LLM 目录、缺策略群默认拒绝及私聊保留；旧媒体绝对路径在移走源后仍从新副本读取，越界路径拒绝。

邻仓现有测试以分进程方式运行，避免模块级单例串扰。旧上传 fixture 需注入临时 `UPLOAD_DIR`，旧剧情和原命令回执 fixture 未配置策略会先被默认拒绝，因此这些业务/回执测试显式注入允许策略；这部分是业务/队列 mock 回归，不冒称真实策略集成验收。邻仓文件不改写。未运行真实查分、下载、生图、账号连接或群消息；未部署服务器。

邻仓回归可通过 `python tests/run_neighbor_regressions.py` 重现，需要邻仓及其已有测试依赖；可用 `--tests-dir` 指定现有测试目录。输出留在被忽略的 `.tools/neighbor-regressions/`，不包含真实数据，也不修改邻仓。适配脚本明确展示所有 fixture 注入。

实际结果：本仓库执行 17 项测试，16 项通过，1 项实际符号链接创建测试因当前 Windows 账号权限不足跳过；Windows reparse 属性兼容检测另有模拟测试通过。邻仓 `test_provider_services`（23）、`test_context_catalog`（5）、`test_sync_runtime`（2）、`test_story_jobs`（3）、`test_maintenance`（17）、`test_command_receipts`（4）、`test_error_notices`（8），合计 62 项 mock 回归通过。语法检查 `python -m compileall -q nonebot_plugin_unibot tools tests`、差异检查及文档本地链接检查通过。
