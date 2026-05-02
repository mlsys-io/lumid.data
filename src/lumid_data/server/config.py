"""Server config (Pydantic Settings)."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    http_port: int = Field(9100, alias="LUMID_DATA_HTTP_PORT")
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    database_url: str = Field(..., alias="DATABASE_URL")

    s3_endpoint: str = Field(..., alias="S3_ENDPOINT")
    s3_access_key: str = Field(..., alias="S3_ACCESS_KEY")
    s3_secret_key: str = Field(..., alias="S3_SECRET_KEY")
    s3_bucket: str = Field("lumid-data", alias="S3_BUCKET")
    s3_region: str = Field("us-east-1", alias="S3_REGION")

    uc_base_url: str = Field(..., alias="UC_BASE_URL")
    uc_token: str | None = Field(None, alias="UC_TOKEN")
    uc_catalog: str = Field("lumid_data", alias="UC_CATALOG")

    nats_url: str = Field(..., alias="NATS_URL")

    flowmesh_governance_url: str | None = Field(None, alias="FLOWMESH_GOVERNANCE_URL")
    flowmesh_governance_token: str | None = Field(None, alias="FLOWMESH_GOVERNANCE_TOKEN")
    governance_mode: str = Field("live", alias="LUMID_DATA_GOVERNANCE_MODE")

    plugins: str = Field("", alias="LUMID_DATA_PLUGINS")
    lumid_oauth_introspect_url: str | None = Field(None, alias="LUMID_OAUTH_INTROSPECT_URL")


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
