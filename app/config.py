from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Akashic Record"
    environment: str = "development"
    database_url: str = (
        "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/akashic_record"
    )
    max_upload_size_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    max_workbook_uncompressed_bytes: int = Field(
        default=50 * 1024 * 1024,
        gt=0,
    )
    max_import_rows: int = Field(default=50_000, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
