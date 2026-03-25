from pydantic import BaseModel
from typing import Optional

class Config(BaseModel):
    """Plugin Config Here"""
    lxns_token: Optional[str] = None
