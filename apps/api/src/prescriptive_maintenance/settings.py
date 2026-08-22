"""Validated application configuration."""

from typing import Literal

from pydantic import PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Load required settings explicitly from the environment or a dotenv file."""

    environment: Literal["local", "test", "production"]
    database_url: PostgresDsn

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="PRESCRIPTIVE_MAINTENANCE_",
    )
