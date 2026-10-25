from pydantic import BaseModel, ConfigDict, Field


class WatchReadInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    notification_token: str = Field(pattern=r'^[a-f0-9]{64}$')
