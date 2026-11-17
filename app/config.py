from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    keitaro_url: str
    api_key: str
    database_url: str = "sqlite:///./adrobot.db"

    default_domain_id: int | None = None
    default_group_id: int | None = None
    default_traffic_source_id: int | None = None
    geo_redirect_url: str = "https://google.com/"

    @field_validator("default_domain_id", "default_group_id", "default_traffic_source_id", mode="before")
    @classmethod
    def empty_to_none(cls, v):
        return None if v == "" else v


settings = Settings()
