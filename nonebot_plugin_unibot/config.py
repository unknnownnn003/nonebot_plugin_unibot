from pydantic import BaseModel, SecretStr

class Config(BaseModel):
    """Plugin Config Here"""
    lxns_oauth_enabled: bool = False
    lxns_oauth_client_id: str = ""
    lxns_oauth_client_secret: SecretStr = SecretStr("")
    lxns_oauth_redirect_uri: str = ""
    lxns_oauth_encryption_key: SecretStr = SecretStr("")
