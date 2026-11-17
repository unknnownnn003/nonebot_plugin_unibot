# 开发工具

脚本需显式执行，插件启动不会加载此目录。临时验证文件和编译缓存放仓库根目录 `.tools/`，不要放运行包。

`fetch_chunirec_data.py` 是当前仍由 README 和 `/上传chunirec` 引用的日服曲库抓取工具，故第一阶段保留原路径和行为。它读取环境变量或交互输入，访问网络，向**当前工作目录**写 `chunirec_data_*.json`；不要为普通检查执行它。凭据说明见脚本头部，不把真实值写入命令历史或提交。

旧 `jackets/` 和抓取快照已移至仓库外归档，详见 [整理记录](../docs/phase1-housekeeping.md)。后续落雪国服改造应配套退役原命令、LLM 维护能力、文档与脚本，当前不能仅因计划废弃就删除工具。

`migrate_localstore.py` 用于停止所有写入后的离线数据迁移，不导入 UniBot 或启动适配器。默认只预检，应用需 `--apply --offline`，可用 `--data-root` 指定宿主 LocalStore 基目录；仍遵守 LocalStore 的插件专用目录覆盖配置。使用方法、冲突和回滚处理见 [存储迁移说明](../docs/localstore-migration.md)。必须在目标宿主的工作目录和同一配置环境下运行，不能把本机默认路径当成服务器路径。
