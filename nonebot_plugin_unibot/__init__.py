from nonebot import get_plugin_config
from nonebot.plugin import PluginMetadata

from .config import Config
from . import songlist_update, get_resource, user_bind, get_player_songlist, get_player_score, overpower_list, help

__plugin_meta__ = PluginMetadata(
    name="nonebot-plugin-unibot",
    description="",
    usage="",
    config=Config,
)

config = get_plugin_config(Config)

# 导入其他模块并使用
__all__ = ["songlist_update", "get_resource", "user_bind", "get_player_songlist", "get_player_score", "overpower_list", "help"]