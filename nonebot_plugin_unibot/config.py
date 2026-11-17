from pydantic import BaseModel, SecretStr
from typing import Optional

class Config(BaseModel):
    """Plugin Config Here"""
    lxns_token: Optional[str] = None
    lxns_oauth_enabled: bool = False
    lxns_oauth_client_id: str = ""
    lxns_oauth_client_secret: SecretStr = SecretStr("")
    lxns_oauth_redirect_uri: str = ""
    lxns_oauth_encryption_key: SecretStr = SecretStr("")
