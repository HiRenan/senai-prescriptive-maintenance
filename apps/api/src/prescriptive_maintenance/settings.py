"""Validated application configuration."""

from typing import Literal

from pydantic import Field, PostgresDsn, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Load one explicit startup profile from the environment or a dotenv file."""

    environment: Literal["local", "offline", "aws"]
    persistence_backend: Literal["memory", "postgres"]
    database_url: PostgresDsn | None = Field(default=None, repr=False)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="PRESCRIPTIVE_MAINTENANCE_",
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        validate_default=True,
    )

    @field_validator("database_url")
    @classmethod
    def validate_profile_dependencies(
        cls,
        database_url: PostgresDsn | None,
        info: ValidationInfo,
    ) -> PostgresDsn | None:
        """Require only the dependencies that belong to the selected profile."""

        environment = info.data.get("environment")
        persistence_backend = info.data.get("persistence_backend")
        if environment == "offline" and persistence_backend != "memory":
            raise ValueError("Offline profile requires the memory backend.")
        if persistence_backend == "memory":
            if database_url is not None:
                raise ValueError("Memory backend cannot configure a database URL.")
            return database_url
        if persistence_backend == "postgres" and database_url is None:
            raise ValueError("PostgreSQL backend requires a database URL.")
        return database_url


def load_settings() -> Settings:
    """Load the process configuration at application startup."""

    return Settings()  # pyright: ignore[reportCallIssue]
