# nonebot-plugin-unibot

基于 NoneBot2 的 CHUNITHM (中二节奏) 查分与数据统计插件，主要接入落雪 (Lxns) 查分器 API，提供完善的成绩归档、进度查询与排版出图功能。

## 指令列表

| 指令 | 权限 | 描述 |
| --- | --- | --- |
| `/bind <好友码>` | USER | 绑定你的落雪查分器好友码，以便拉取云端数据 |
| `/chuupdate` | USER | 上传 lxns 导出的分数 CSV，并更新本地 JSON 成绩归档 |
| `/个人信息` | USER | 查询并生成玩家的落雪 (Lxns) 游玩个人数据图片（别名：`/分数`、`/chuinfo`） |
| `/chulist <难度/定数>` | USER | 按难度或定数生成对应的 Overpower 进度及评价统计图（例：`/chulist 13+` 或 `/chulist 14.9`） |
| `/chuhelp` | USER | 查看本插件内的基本使用指令帮助菜单 |
| `/更新songlist` | SUPERUSER | 更新本地 `songlist.json` 曲库数据 |
| `/更新曲绘` | SUPERUSER | 获取并更新全部曲绘到 `data/jacket/` 目录 |

Todo:

- [] 别名数据库
- [] 单曲查询
- [] 剧情查询