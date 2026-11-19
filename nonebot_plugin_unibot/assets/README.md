# UniBot 私有 UI 素材

本地 `ui/` 是从用户提供的 CHUNITHM Mate HDD 导出的 4A 定稿所需静态裁片。以 `ui/manifest.json` 为清单，每张图记录来源组件、尺寸和 SHA-256，数字表记录字距。普通加载从插件目录读取 RGBA PNG。私有部署随插件目录复制 `assets/ui/` 即可，Git 已忽略此目录。未取得权利方许可，请勿公开分发这些贴图或将其打入公开安装包。

- Mate 2.50.00 A000 的 Surfride 和引用纹理提供 Rating 色阶、标题和数字，Rank、Clear、FC、AJ、AJC、Chain，CLASS、玩家栏、难度、等级和版本图标。UNI 标识使用定稿里的角色 30000 方形头像裁片。
- 2.40.00 的 `CHU_UI_Result_01_v10.srd` 提供 Normal、Bronze、Silver、Gold、Platinum 五组结算数字及逗号。
- 新版本缺少对应标识时可显示曲库版本文本。游戏字体原件没有纳入素材包。玩家名牌、图片称号和头像按当前公开 ID 从落雪获取，文字称号可直接显示。公开资源接口未提供单独的高清立绘入口，本地经过核验的立绘可放在 LocalStore 数据目录 `portrait/<character_id>.png`。

运行 `python tools/check_ui_assets.py` 核对清单内的 PNG、尺寸和 SHA-256。缺少私有贴图时，六类图片仍可生成，版本、难度、评级和徽章会使用文字或色块。单曲成绩优先显示等比缩放的完整立绘，缺少有效立绘时回退到公开头像。

六类图片的普通数字和文字统一使用仓库内的 [DejaVu Sans 2.37](https://dejavu-fonts.github.io/) 和 [Noto Sans CJK SC 2.004](https://github.com/notofonts/noto-cjk/releases/tag/Sans2.004)。部署时必须保留 `assets/fonts/`，不依赖 Windows 或 Ubuntu 系统字体。开放字体许可见 [DejaVu License](fonts/DejaVu-LICENSE.txt) 和 [SIL OFL 1.1](fonts/OFL.txt)，不属于私有 `assets/ui/`；游戏结算数字等贴图保持不变。
