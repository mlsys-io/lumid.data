"""Server config (Pydantic Settings)."""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProvider = Literal["anthropic", "openai", "openai_compat"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    http_port: int = Field(9100, alias="LUMID_DATA_HTTP_PORT")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    base_url: str = Field("http://127.0.0.1:9100", alias="LUMID_DATA_BASE_URL")

    database_url: str = Field(..., alias="DATABASE_URL")

    s3_endpoint: str = Field(..., alias="S3_ENDPOINT")
    s3_access_key: str = Field(..., alias="S3_ACCESS_KEY")
    s3_secret_key: str = Field(..., alias="S3_SECRET_KEY")
    s3_region: str = Field("us-east-1", alias="S3_REGION")
    s3_default_bucket: str = Field("lumid-data", alias="S3_DEFAULT_BUCKET")

    postgrest_url: str = Field("http://postgrest:3000", alias="POSTGREST_URL")
    postgrest_jwt_secret: str = Field(..., alias="POSTGREST_JWT_SECRET")
    postgrest_jwt_ttl_sec: int = Field(60, alias="POSTGREST_JWT_TTL_SEC")
    postgrest_anon_role: str = Field("postgrest_anon", alias="POSTGREST_ANON_ROLE")
    postgrest_user_role: str = Field("app_user", alias="POSTGREST_USER_ROLE")
    postgrest_admin_role: str = Field("app_admin", alias="POSTGREST_ADMIN_ROLE")

    nats_url: str | None = Field(None, alias="NATS_URL")
    kafka_bootstrap: str | None = Field(None, alias="KAFKA_BOOTSTRAP")

    llm_provider: LLMProvider = Field("anthropic", alias="LUMID_DATA_LLM_PROVIDER")
    llm_model: str = Field("claude-sonnet-4-6", alias="LUMID_DATA_LLM_MODEL")
    llm_api_key: str | None = Field(None, alias="LUMID_DATA_LLM_API_KEY")
    llm_base_url: str | None = Field(None, alias="LUMID_DATA_LLM_BASE_URL")
    agent_max_steps: int = Field(20, alias="LUMID_DATA_AGENT_MAX_STEPS")

    plugins: str = Field("", alias="LUMID_DATA_PLUGINS")


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
