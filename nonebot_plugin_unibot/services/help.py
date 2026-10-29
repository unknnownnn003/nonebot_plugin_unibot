"""Native Agent access to the original UniBot help image."""
from __future__ import annotations
from .job_store import async_call

import asyncio

from .results import outcome, image


async def read():
    from .. import help as help_module
    content = await async_call(help_module.draw_help)
    return outcome(data={"scope": "original_unibot_help"}, artifacts=[image(content)])
