# 开发工具

脚本需显式执行，插件启动不会加载此目录。临时验证文件和编译缓存放仓库根目录 `.tools/`，不要放运行包。

第二阶段 B 已移除 `fetch_chunirec_data.py`；脚本源码历史保留在 Git，第一阶段的抓取快照与曲绘仍在仓库外私密归档，运行数据未删除。

`export_native_catalog.py` 在本仓库解析实际 matcher 和能力声明，只生成 UniBot 的 `services/native_catalog.py`。使用 `python tools/export_native_catalog.py --check` 核对目录；不带参数时重新生成。原邻仓生成器仍包含旧日服映射，不能再用它覆盖当前 UniBot 目录；本工具不写邻仓。

`migrate_localstore.py` 用于停止所有写入后的离线数据迁移，不导入 UniBot 或启动适配器。默认只预检，应用需 `--apply --offline`，可用 `--data-root` 指定宿主 LocalStore 基目录；仍遵守 LocalStore 的插件专用目录覆盖配置。使用方法、冲突和回滚处理见 [存储迁移说明](../docs/localstore-migration.md)。必须在目标宿主的工作目录和同一配置环境下运行，不能把本机默认路径当成服务器路径。
