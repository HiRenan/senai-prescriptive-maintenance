"""Strict local manifest boundary for an approved analysis runtime."""

from __future__ import annotations

import json
import stat
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, NoReturn, cast

from pydantic import BaseModel, ConfigDict, Field

from prescriptive_maintenance.prescription_orchestration import (
    MAX_PROVIDER_TIMEOUT_SECONDS,
)
from prescriptive_maintenance.settings import Settings

MAX_MANIFEST_BYTES = 1_048_576
MAX_JSON_ARTIFACT_BYTES = 16_777_216
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
_DOCUMENT_ID_PATTERN = r"^doc_[0-9a-f]{64}$"
_DOCUMENT_VERSION_PATTERN = r"^docver_[0-9a-f]{64}$"
_CHUNK_ID_PATTERN = r"^chunk_[a-z0-9_]{3,64}$"
_MODEL_ID_PATTERN = r"^model_[a-z0-9_.-]{3,64}$"
_INDEX_ID_PATTERN = r"^similarity_index_v1_[0-9a-f]{32}$"
_CONFIGURATION_ID_PATTERN = r"^config_[a-z0-9_.-]{3,64}$"

Sha256 = Annotated[str, Field(pattern=_SHA256_PATTERN)]
Identity = Annotated[str, Field(pattern=_ID_PATTERN)]


class AnalysisArtifactsError(RuntimeError):
    """Sanitized failure raised for unavailable or incompatible artifacts."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class DirectoryReference(_StrictModel):
    path: str
    manifest_sha256: Sha256


class FileReference(_StrictModel):
    path: str
    sha256: Sha256


class ModelBinding(_StrictModel):
    artifact: DirectoryReference
    dataset_id: Sha256
    model_id: Annotated[str, Field(pattern=_MODEL_ID_PATTERN)]
    content_sha256: Sha256
    training_partition_sha256: Sha256


class IndexBinding(_StrictModel):
    artifact: DirectoryReference
    schema_id: Sha256
    index_id: Annotated[str, Field(pattern=_INDEX_ID_PATTERN)]
    content_sha256: Sha256
    source_model_id: Annotated[str, Field(pattern=_MODEL_ID_PATTERN)]
    source_model_content_sha256: Sha256
    record_count: Annotated[int, Field(ge=1)]


class MappingBinding(_StrictModel):
    artifact: FileReference
    mapping_version: Identity
    mapping_sha256: Sha256


class RetrievalPolicyBinding(_StrictModel):
    policy_version: Identity
    minimum_score: Annotated[float, Field(ge=0.0, le=1.0)]
    policy_sha256: Sha256


class ProjectionPolicyBinding(_StrictModel):
    policy_version: Identity
    policy_sha256: Sha256
    priorities: dict[str, Literal["routine", "scheduled", "urgent"]]


class ProviderBinding(_StrictModel):
    kind: Literal["fake"]
    provider_id: Identity
    timeout_seconds: Annotated[
        float,
        Field(gt=0.0, le=MAX_PROVIDER_TIMEOUT_SECONDS),
    ]


class ChunkingBinding(_StrictModel):
    version: Identity
    max_characters: Annotated[int, Field(ge=32)]
    overlap_characters: Annotated[int, Field(ge=0)]
    cleanup_version: Identity
    section_detection_version: Identity
    configuration_id: Annotated[str, Field(pattern=r"^chunkcfg_[0-9a-f]{64}$")]


class EmbeddingBinding(_StrictModel):
    provider_id: Identity
    representation_version: Identity
    dimension: Annotated[int, Field(ge=1, le=4096)]


class DocumentBinding(_StrictModel):
    extraction: FileReference
    version: Annotated[int, Field(ge=1)]
    document_id: Annotated[str, Field(pattern=_DOCUMENT_ID_PATTERN)]
    document_version_id: Annotated[str, Field(pattern=_DOCUMENT_VERSION_PATTERN)]
    source_sha256: Sha256
    chunk_ids: tuple[Annotated[str, Field(pattern=_CHUNK_ID_PATTERN)], ...]


class ArtifactsManifest(_StrictModel):
    schema_version: Literal[1]
    authorization_version: Identity
    authorization_sha256: Sha256
    configuration_id: Annotated[str, Field(pattern=_CONFIGURATION_ID_PATTERN)]
    operating_state_policy_sha256: Sha256
    model: ModelBinding
    index: IndexBinding
    mapping: MappingBinding
    retrieval_policy: RetrievalPolicyBinding
    projection_policy: ProjectionPolicyBinding
    prompt_id: Identity
    prompt_sha256: Sha256
    provider: ProviderBinding
    chunking: ChunkingBinding
    embedding: EmbeddingBinding
    documents: tuple[DocumentBinding, ...]


@dataclass(frozen=True, slots=True)
class LoadedArtifactsManifest:
    manifest: ArtifactsManifest
    root: Path


def load_artifacts_manifest(settings: Settings) -> LoadedArtifactsManifest:
    """Load one hash-pinned manifest without path discovery."""

    manifest_path = settings.analysis_artifacts_manifest
    expected_sha256 = settings.analysis_artifacts_manifest_sha256
    if manifest_path is None or expected_sha256 is None:
        raise AnalysisArtifactsError("The configured artifacts are unavailable.")
    manifest_bytes = read_artifact_file(
        manifest_path,
        maximum_bytes=MAX_MANIFEST_BYTES,
    )
    if content_sha256(manifest_bytes) != expected_sha256:
        raise AnalysisArtifactsError("The configured artifacts are unavailable.")
    try:
        manifest = ArtifactsManifest.model_validate(
            _freeze_json_arrays(_decode_json_object(manifest_bytes))
        )
        root = manifest_path.resolve(strict=True).parent
    except AnalysisArtifactsError:
        raise
    except Exception:
        raise AnalysisArtifactsError(
            "The configured artifacts are unavailable."
        ) from None
    return LoadedArtifactsManifest(manifest=manifest, root=root)


def resolve_artifact_reference(root: Path, value: str, *, directory: bool) -> Path:
    """Resolve one safe relative reference beneath the manifest directory."""

    try:
        relative = PurePosixPath(value)
        if (
            type(value) is not str
            or not value
            or "\\" in value
            or relative.is_absolute()
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise ValueError
        candidate = root.joinpath(*relative.parts)
        _reject_linked_components(root, candidate)
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
        metadata = resolved.stat()
        if directory and not stat.S_ISDIR(metadata.st_mode):
            raise ValueError
        if not directory and not stat.S_ISREG(metadata.st_mode):
            raise ValueError
        return resolved
    except Exception:
        raise AnalysisArtifactsError(
            "The configured artifacts are unavailable."
        ) from None


def _reject_linked_components(root: Path, candidate: Path) -> None:
    current = root
    for part in candidate.relative_to(root).parts:
        current /= part
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            raise ValueError


def read_artifact_file(path: Path, *, maximum_bytes: int) -> bytes:
    """Read one bounded regular file while rejecting visible replacement races."""

    try:
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError
        before = path.stat()
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= maximum_bytes:
            raise ValueError
        content = path.read_bytes()
        after = path.stat()
        if (
            len(content) != before.st_size
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
        ):
            raise ValueError
        return content
    except Exception:
        raise AnalysisArtifactsError(
            "The configured artifacts are unavailable."
        ) from None


def verify_artifact_sha256(
    path: Path,
    expected: str,
    *,
    maximum_bytes: int,
) -> None:
    content = read_artifact_file(path, maximum_bytes=maximum_bytes)
    if content_sha256(content) != expected:
        raise AnalysisArtifactsError("The configured artifacts are unavailable.")


def decode_artifact_json(content: bytes) -> dict[str, object]:
    return _decode_json_object(content)


def semantic_sha256(value: object) -> str:
    return content_sha256(
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def content_sha256(value: bytes) -> str:
    return sha256(value).hexdigest()


def _decode_json_object(content: bytes) -> dict[str, object]:
    try:
        value = json.loads(
            content.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
        if type(value) is not dict:
            raise ValueError
        return cast(dict[str, object], value)
    except AnalysisArtifactsError:
        raise
    except Exception:
        raise AnalysisArtifactsError(
            "The configured artifacts are unavailable."
        ) from None


def _freeze_json_arrays(value: object) -> object:
    if type(value) is list:
        return tuple(_freeze_json_arrays(item) for item in cast(list[object], value))
    if type(value) is dict:
        return {
            key: _freeze_json_arrays(item)
            for key, item in cast(dict[str, object], value).items()
        }
    return value


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AnalysisArtifactsError("The configured artifacts are unavailable.")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> NoReturn:
    raise AnalysisArtifactsError("The configured artifacts are unavailable.")
