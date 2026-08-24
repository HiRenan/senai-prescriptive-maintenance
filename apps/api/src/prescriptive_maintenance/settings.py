"""Validated application configuration."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import (
    Field,
    PostgresDsn,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")

type AnalysisMode = Literal["synthetic_demo", "artifacts"]


class Settings(BaseSettings):
    """Load one explicit startup profile from the environment or a dotenv file."""

    environment: Literal["local", "offline", "aws"]
    persistence_backend: Literal["memory", "postgres"]
    database_url: PostgresDsn | None = Field(default=None, repr=False)
    analysis_mode: AnalysisMode
    analysis_artifacts_manifest: Path | None = Field(default=None, repr=False)
    analysis_artifacts_manifest_sha256: str | None = Field(
        default=None,
        repr=False,
    )

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

    @model_validator(mode="after")
    def validate_analysis_mode_dependencies(self) -> Settings:
        """Require an exact artifact authorization only in artifacts mode."""

        manifest = self.analysis_artifacts_manifest
        manifest_sha256 = self.analysis_artifacts_manifest_sha256
        if self.analysis_mode == "synthetic_demo":
            if manifest is not None or manifest_sha256 is not None:
                raise ValueError(
                    "Synthetic demo mode cannot configure artifact references."
                )
            return self
        if manifest is None or manifest_sha256 is None:
            raise ValueError(
                "Artifacts mode requires a manifest and its approved SHA-256."
            )
        if _SHA256_PATTERN.fullmatch(manifest_sha256) is None:
            raise ValueError("Artifacts manifest SHA-256 is invalid.")
        return self


def load_settings() -> Settings:
    """Load the process configuration at application startup."""

    return Settings()  # pyright: ignore[reportCallIssue]
