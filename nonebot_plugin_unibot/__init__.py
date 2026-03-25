from nonebot import get_plugin_config
from nonebot.plugin import PluginMetadata

from .config import Config
from . import songlist_update
from . import get_resource
from . import get_player_songlist

__plugin_meta__ = PluginMetadata(
    name="nonebot-plugin-unibot",
    description="",
    usage="",
    config=Config,
)

config = get_plugin_config(Config)

# 导入其他模块并使用
__all__ = ["songlist_update", "get_resource", "get_player_songlist"]