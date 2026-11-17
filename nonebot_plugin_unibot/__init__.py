from .services.error_notices import install as _install_error_notices
_install_error_notices()
from nonebot import get_plugin_config
from nonebot.plugin import PluginMetadata

from .config import Config
from . import songlist_update, get_resource, user_bind, get_player_songlist, overpower_list_local, help, collection_update, song, recommendation, story, best_rating, chuguess, job_control

__plugin_meta__ = PluginMetadata(
    name="nonebot-plugin-unibot",
    description="CHUNITHM 查分、曲库、推荐、猜曲与剧情",
    usage="/chuhelp [文字|分类] 查看完整帮助；日志群 /设置面板 可批量维护资源。",
    config=Config,
)

config = get_plugin_config(Config)

# 导入其他模块并使用
__all__ = ["songlist_update", "get_resource", "user_bind", "get_player_songlist", "overpower_list_local", "help", "collection_update", "song", "recommendation", "story", "best_rating", "chuguess", "job_control"]


# The business queue works without loading llmchat. No second score store exists.
from nonebot import get_driver
from .services.sync import ensure_started, stop_workers
get_driver().on_startup(ensure_started)
get_driver().on_shutdown(stop_workers)

from .services.command_receipts import install as install_command_receipts
_native_command_guards = install_command_receipts([globals()[name] for name in __all__])

from .services.group_policy import install as _install_group_policy
_install_group_policy()

from .services.oauth_runtime import configure as _configure_oauth
_configure_oauth()
