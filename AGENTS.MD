# AGENTS.MD

## 项目概览

这是一个基于 NoneBot2 和 OneBot v11 的 CHUNITHM（中二节奏）查分、曲库、剧情与图片生成插件。插件包位于 `nonebot_plugin_unibot/`，主入口是 `nonebot_plugin_unibot/__init__.py`，导入各功能模块后由 NoneBot 自动注册命令与正则匹配器。

主要能力：

- 绑定落雪（Lxns）好友码：`/bind <好友码>`
- 上传或同步成绩：`/chuupdate`，当前工作区中 `get_player_score.py` 还包含未提交的 `/lxupdate` 开发改动
- 查询个人信息与本地成绩进度：`/个人信息`、`/分数`、`/chuinfo`、`/chulist`
- 查询曲目、管理本地别名、随机推荐歌曲：`是什么歌`、`/添加别名`、`/删除别名`、`/查看别名`、`推什么`
- 更新曲库、曲绘、收藏品和剧情资源：`/更新songlist`、`/更新曲绘`、`/更新收藏品`、`/更新剧情`
- 查询剧情：`/查剧情`、`/随机剧情`、`/剧情分类`、`/剧情列表`

## 重要文件

- `README.md`：用户侧指令说明。
- `nonebot_plugin_unibot/__init__.py`：插件元数据与模块导入入口。
- `nonebot_plugin_unibot/config.py`：插件配置模型，目前只声明 `lxns_token: Optional[str]`。
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

- 先检查 `git status --short --branch`。当前仓库可能存在用户未提交改动，尤其不要覆盖 `nonebot_plugin_unibot/get_player_score.py` 中已有的工作区变更。
- 该项目以中文命令和中文用户提示为主，编辑源码时保持 UTF-8 编码。
- 网络请求主要使用 `httpx`，命令处理函数是 async；新增阻塞逻辑时避免卡住事件循环。
- 图片生成依赖 Pillow 和本机字体，跨平台字体逻辑分散在多个模块中，改动渲染时要检查 Windows/Linux 字体路径兼容性。
- 成绩、曲库和剧情逻辑依赖远端 API 返回结构；新增解析字段时保留向后兼容和异常兜底。
- 超级用户命令通常通过 `permission=SUPERUSER` 注册，新增资源更新或删除类命令时沿用这个权限边界。

## 建议验证

没有完整测试套件时，至少执行语法检查：

```powershell
python -m compileall nonebot_plugin_unibot
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

## Git 工作流状态

本目录已经是 git 仓库，当前分支为 `main`，配置了 GitHub 远端 `origin`。仓库适合使用常规分支、提交、推送、PR 的 git 工作流，但当前工作区已有未提交改动；后续提交时应单独暂存目标文件，例如只提交本文档时使用：

```powershell
git add AGENTS.MD
git commit -m "Add agent project notes"
```
