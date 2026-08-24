"""End-to-end proofs for the explicit analysis runtime composition."""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from runpy import run_path
from typing import Any, cast

import pandas as pd
import prescriptive_maintenance.analysis_artifact_documents as artifact_documents
import pytest
from fastapi.testclient import TestClient
from prescriptive_maintenance.analysis_integration import (
    PERSISTED_GENERATION_PROMPT_ID,
    AnalysisRuntimeAuthorization,
    build_analysis_runtime_authorization,
    build_prescription_projection_policy,
)
from prescriptive_maintenance.analysis_runtime import AnalysisRuntimeComposition
from prescriptive_maintenance.contracts import (
    ANALYSIS_FEATURE_NAMES,
    AnalysisRequest,
    PrescriptionPriority,
)
from prescriptive_maintenance.data.document_indexing import (
    ChunkingConfiguration,
    InMemoryChunkRepository,
    LocalHashEmbeddingProvider,
    index_extracted_document,
)
from prescriptive_maintenance.fakes import build_synthetic_analysis_service
from prescriptive_maintenance.generation import (
    GENERATION_SYSTEM_PROMPT,
    FakeGenerationProvider,
)
from prescriptive_maintenance.governed_retrieval import (
    build_governed_retrieval_policy,
)
from prescriptive_maintenance.knowledge_retrieval import (
    build_fault_knowledge_mapping,
    fault_knowledge_mapping_json_bytes,
)
from prescriptive_maintenance.main import create_app
from prescriptive_maintenance.modeling.knn import (
    KnnModelPortAdapter,
    fit_knn_model,
    save_knn_model,
)
from prescriptive_maintenance.modeling.similarity_index import (
    InMemorySimilarityIndexAdapter,
    SimilarityIndexCompatibility,
    SimilarityQuery,
    load_similarity_index,
    save_similarity_index_from_knn_artifact,
)
from prescriptive_maintenance.operating_states import operating_state_policy_payload
from prescriptive_maintenance.settings import Settings
from pydantic import ValidationError

_DATASET_ID = sha256(b"sen-79-synthetic-dataset").hexdigest()
_TRAINING_ID = sha256(b"sen-79-synthetic-training").hexdigest()
_SCHEMA_ID = sha256(b"sen-79-synthetic-schema").hexdigest()
_FAULT_CLASS = "bearing-fault"


@dataclass(frozen=True, slots=True)
class _Bundle:
    root: Path
    settings: Settings
    request: AnalysisRequest
    manifest: dict[str, Any]
    manifest_path: Path
    model_id: str
    index_id: str
    neighbor_ref: str
    fault_code: str
    document_id: str
    document_version_id: str
    chunk_id: str


def _training_frame() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for value, label in (
        (1.0, _FAULT_CLASS),
        (1.2, _FAULT_CLASS),
        (8.0, "normal"),
        (8.2, "normal"),
    ):
        row: dict[str, object] = {
            name: float(value if position == 0 else position + 1)
            for position, name in enumerate(ANALYSIS_FEATURE_NAMES)
        }
        row["y"] = label
        rows.append(row)
    frame = pd.DataFrame(rows, columns=(*ANALYSIS_FEATURE_NAMES, "y"))
    frame.loc[:, list(ANALYSIS_FEATURE_NAMES)] = frame.loc[
        :, list(ANALYSIS_FEATURE_NAMES)
    ].astype("float64")
    frame["y"] = frame["y"].astype("string")
    return frame


def _request() -> AnalysisRequest:
    return AnalysisRequest.model_validate(
        {
            "features": {
                name: float(1.0 if position == 0 else position + 1)
                for position, name in enumerate(ANALYSIS_FEATURE_NAMES)
            },
            "top_k": 1,
        }
    )


def _synthetic_extraction() -> dict[str, object]:
    content = (
        "# Inspeção sintética aprovada\n"
        "Verifique a condição do rolamento em parada controlada, confirme o "
        "alinhamento e registre somente medições sintéticas após a inspeção."
    )
    source_material = b"sen-79-approved-synthetic-maintenance-v1"
    source_sha256 = sha256(source_material).hexdigest()
    return {
        "schema_version": 1,
        "extractor_version": 2,
        "tooling": {
            "pypdfium2": "synthetic-5.13.0",
            "ocr_adapter": {"configured": False, "name": None, "version": None},
        },
        "source": {
            "name": "SyntheticApprovedMaintenance.pdf",
            "source_version": f"sha256:{source_sha256}",
            "size_bytes": len(content.encode("utf-8")),
            "sha256": source_sha256,
            "pdf_version": "synthetic-1.0",
        },
        "status": "completed",
        "failure_code": None,
        "page_count": 1,
        "pages": [
            {
                "page_number": 1,
                "method": "native",
                "status": "extracted",
                "text": content,
                "native_quality": {"signals": []},
                "quality": {"signals": []},
                "ocr_trigger_codes": [],
                "failure_code": None,
            }
        ],
    }


def _write_json(path: Path, value: object) -> bytes:
    content = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    path.write_bytes(content)
    return content


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _semantic_sha256(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _settings_for_manifest(path: Path) -> Settings:
    return Settings.model_validate(
        {
            "environment": "offline",
            "persistence_backend": "memory",
            "analysis_mode": "artifacts",
            "analysis_artifacts_manifest": path,
            "analysis_artifacts_manifest_sha256": _file_sha256(path),
        }
    )


def _build_bundle(tmp_path: Path, *, mapped: bool = True) -> _Bundle:
    root = tmp_path / "approved-synthetic-derivatives"
    root.mkdir()
    request = _request()
    model = fit_knn_model(
        _training_frame(),
        dataset_id=_DATASET_ID,
        training_partition_sha256=_TRAINING_ID,
        default_top_k=1,
    )
    model_path = save_knn_model(model, root / "model")
    index_path = save_similarity_index_from_knn_artifact(
        model_path,
        schema_id=_SCHEMA_ID,
        output_directory=root / "index",
    )
    compatibility = SimilarityIndexCompatibility(
        dataset_id=_DATASET_ID,
        schema_id=_SCHEMA_ID,
    )
    index = load_similarity_index(index_path, expected=compatibility)
    model_prediction = KnnModelPortAdapter(model).predict(
        request.features,
        top_k=1,
    )
    indexed_neighbor = InMemorySimilarityIndexAdapter(index).query(
        SimilarityQuery(
            selector=index.selector,
            features=tuple(
                getattr(request.features, name) for name in ANALYSIS_FEATURE_NAMES
            ),
            top_k=1,
        )
    )[0]
    assert model_prediction.retrieval_key == _FAULT_CLASS
    assert model_prediction.diagnosis is not None
    assert model_prediction.neighbors[0].neighbor_ref == indexed_neighbor.opaque_id

    chunking = ChunkingConfiguration(
        max_characters=512,
        overlap_characters=32,
    )
    embedding = LocalHashEmbeddingProvider(dimension=24)
    extraction_path = root / "approved-extraction.json"
    extraction_bytes = _write_json(extraction_path, _synthetic_extraction())
    indexed = index_extracted_document(
        _synthetic_extraction(),
        embedding_provider=embedding,
        repository=InMemoryChunkRepository(),
        configuration=chunking,
    )
    assert not indexed.failures
    assert len(indexed.records) == 1
    record = indexed.records[0]

    mapping = build_fault_knowledge_mapping(
        mapping_version="fault-knowledge.sen79.v1",
        mappings={_FAULT_CLASS: [record.chunk.document_id] if mapped else []},
    )
    mapping_path = root / "fault-knowledge.json"
    mapping_bytes = fault_knowledge_mapping_json_bytes(mapping)
    mapping_path.write_bytes(mapping_bytes)

    retrieval_policy = build_governed_retrieval_policy(
        policy_version="governed-retrieval.sen79.v1",
        minimum_score=0.0,
    )
    fault_code = model_prediction.diagnosis.code
    projection_policy = build_prescription_projection_policy(
        policy_version="prescription-projection.sen79.v1",
        priorities={fault_code: PrescriptionPriority.URGENT},
    )
    authorization: AnalysisRuntimeAuthorization = build_analysis_runtime_authorization(
        authorization_version="analysis-runtime.sen79.v1",
        dataset_id=model.dataset_id,
        model_id=model.model_id,
        index_id=index.selector.index_id,
        retrieval_policy_version=retrieval_policy.policy_version,
        retrieval_policy_sha256=retrieval_policy.policy_sha256,
        mapping_version=mapping.mapping_version,
        mapping_sha256=mapping.mapping_sha256,
        prompt_id=PERSISTED_GENERATION_PROMPT_ID,
        provider_id="fake-generation.v1",
        provider_timeout_seconds=1.0,
        projection_policy=projection_policy,
    )
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "authorization_version": authorization.authorization_version,
        "authorization_sha256": authorization.authorization_sha256,
        "configuration_id": authorization.configuration_id,
        "operating_state_policy_sha256": _semantic_sha256(
            operating_state_policy_payload()
        ),
        "model": {
            "artifact": {
                "path": "model",
                "manifest_sha256": _file_sha256(model_path / "manifest.json"),
            },
            "dataset_id": model.dataset_id,
            "model_id": model.model_id,
            "content_sha256": model.content_sha256,
            "training_partition_sha256": model.training_partition_sha256,
        },
        "index": {
            "artifact": {
                "path": "index",
                "manifest_sha256": _file_sha256(index_path / "manifest.json"),
            },
            "schema_id": _SCHEMA_ID,
            "index_id": index.selector.index_id,
            "content_sha256": index.manifest.content_sha256,
            "source_model_id": index.manifest.source_model_id,
            "source_model_content_sha256": (index.manifest.source_model_content_sha256),
            "record_count": index.manifest.record_count,
        },
        "mapping": {
            "artifact": {
                "path": "fault-knowledge.json",
                "sha256": sha256(mapping_bytes).hexdigest(),
            },
            "mapping_version": mapping.mapping_version,
            "mapping_sha256": mapping.mapping_sha256,
        },
        "retrieval_policy": {
            "policy_version": retrieval_policy.policy_version,
            "minimum_score": retrieval_policy.minimum_score,
            "policy_sha256": retrieval_policy.policy_sha256,
        },
        "projection_policy": {
            "policy_version": projection_policy.policy_version,
            "policy_sha256": projection_policy.policy_sha256,
            "priorities": {fault_code: "urgent"},
        },
        "prompt_id": PERSISTED_GENERATION_PROMPT_ID,
        "prompt_sha256": sha256(
            GENERATION_SYSTEM_PROMPT.text.encode("utf-8")
        ).hexdigest(),
        "provider": {
            "kind": "fake",
            "provider_id": "fake-generation.v1",
            "timeout_seconds": 1.0,
        },
        "chunking": {
            "version": chunking.version,
            "max_characters": chunking.max_characters,
            "overlap_characters": chunking.overlap_characters,
            "cleanup_version": chunking.cleanup_version,
            "section_detection_version": chunking.section_detection_version,
            "configuration_id": chunking.identity,
        },
        "embedding": {
            "provider_id": embedding.provider_id,
            "representation_version": embedding.representation_version,
            "dimension": embedding.dimension,
        },
        "documents": [
            {
                "extraction": {
                    "path": "approved-extraction.json",
                    "sha256": sha256(extraction_bytes).hexdigest(),
                },
                "version": 1,
                "document_id": record.chunk.document_id,
                "document_version_id": record.chunk.document_version,
                "source_sha256": record.chunk.provenance.source_sha256,
                "chunk_ids": [record.chunk.chunk_id],
            }
        ],
    }
    manifest_path = root / "analysis-runtime.json"
    _write_json(manifest_path, manifest)
    return _Bundle(
        root=root,
        settings=_settings_for_manifest(manifest_path),
        request=request,
        manifest=manifest,
        manifest_path=manifest_path,
        model_id=model.model_id,
        index_id=index.selector.index_id,
        neighbor_ref=indexed_neighbor.opaque_id,
        fault_code=fault_code,
        document_id=record.chunk.document_id,
        document_version_id=record.chunk.document_version,
        chunk_id=record.chunk.chunk_id,
    )


def _rewrite_manifest(bundle: _Bundle, manifest: dict[str, Any]) -> Settings:
    _write_json(bundle.manifest_path, manifest)
    return _settings_for_manifest(bundle.manifest_path)


def _reauthorize_manifest(manifest: dict[str, Any]) -> None:
    projection = build_prescription_projection_policy(
        policy_version=manifest["projection_policy"]["policy_version"],
        priorities={
            fault_code: PrescriptionPriority(priority)
            for fault_code, priority in manifest["projection_policy"][
                "priorities"
            ].items()
        },
    )
    manifest["projection_policy"]["policy_sha256"] = projection.policy_sha256
    authorization = build_analysis_runtime_authorization(
        authorization_version=manifest["authorization_version"],
        dataset_id=manifest["model"]["dataset_id"],
        model_id=manifest["model"]["model_id"],
        index_id=manifest["index"]["index_id"],
        retrieval_policy_version=manifest["retrieval_policy"]["policy_version"],
        retrieval_policy_sha256=manifest["retrieval_policy"]["policy_sha256"],
        mapping_version=manifest["mapping"]["mapping_version"],
        mapping_sha256=manifest["mapping"]["mapping_sha256"],
        prompt_id=manifest["prompt_id"],
        provider_id=manifest["provider"]["provider_id"],
        provider_timeout_seconds=manifest["provider"]["timeout_seconds"],
        projection_policy=projection,
    )
    manifest["authorization_sha256"] = authorization.authorization_sha256
    manifest["configuration_id"] = authorization.configuration_id


def test_synthetic_demo_is_explicit_and_exposes_only_its_mode() -> None:
    settings = Settings.model_validate(
        {
            "environment": "offline",
            "persistence_backend": "memory",
            "analysis_mode": "synthetic_demo",
        }
    )
    with TestClient(create_app(settings=settings)) as client:
        ready = client.get("/health/ready")
        response = client.post(
            "/analysis",
            json=_request().model_dump(mode="json"),
        )

    assert ready.status_code == 200
    assert ready.headers["X-Analysis-Mode"] == "synthetic_demo"
    assert response.headers["X-Analysis-Mode"] == "synthetic_demo"


def test_artifacts_http_journey_uses_real_bindings_and_persists_exact_citation(
    tmp_path: Path,
) -> None:
    bundle = _build_bundle(tmp_path)
    application = create_app(settings=bundle.settings)

    with TestClient(application) as client:
        ready = client.get("/health/ready")
        response = client.post(
            "/analysis",
            json=bundle.request.model_dump(mode="json"),
        )
        recovered = client.get(f"/analysis/{response.json()['analysis_id']}")
        composition = application.state.analysis_runtime

    assert ready.status_code == 200
    assert response.status_code == 200
    assert recovered.status_code == 200
    assert response.headers["X-Analysis-Mode"] == "artifacts"
    payload = response.json()
    assert payload["outcome"] == "documented_fault"
    assert payload["model_id"] == bundle.model_id
    assert payload["neighbors"] == [
        {
            "neighbor_ref": bundle.neighbor_ref,
            "rank": 1,
            "fault_code": bundle.fault_code,
            "distance": 0.0,
        }
    ]
    assert payload["citations"] == [
        {
            "document_id": bundle.document_id,
            "document_version": bundle.document_version_id,
            "chunk": bundle.chunk_id,
            "page_number": 1,
        }
    ]
    assert recovered.json() == payload
    assert type(composition) is AnalysisRuntimeComposition
    assert composition.summary.index_record_count == 4
    assert composition.summary.approved_document_count == 1
    assert composition.summary.indexed_chunk_count == 1
    provider = composition.generation_provider
    assert type(provider) is FakeGenerationProvider
    assert provider.call_count == 1
    factory = composition.unit_of_work_factory
    assert factory is not None
    with factory() as transaction:
        metadata = transaction.analyses.get(payload["analysis_id"])
        transaction.rollback()
    assert metadata is not None
    assert metadata.dataset_id == _DATASET_ID
    assert metadata.model_id == bundle.model_id
    assert metadata.configuration_id == bundle.manifest["configuration_id"]
    assert tuple(
        (
            item.document_id,
            item.document_version_id,
            item.chunk_ref,
        )
        for item in metadata.evidence_references
    ) == ((bundle.document_id, bundle.document_version_id, bundle.chunk_id),)


def test_artifacts_without_governed_evidence_never_prescribe(
    tmp_path: Path,
) -> None:
    bundle = _build_bundle(tmp_path, mapped=False)
    application = create_app(settings=bundle.settings)

    with TestClient(application) as client:
        response = client.post(
            "/analysis",
            json=bundle.request.model_dump(mode="json"),
        )
        composition = application.state.analysis_runtime

    assert response.status_code == 200
    assert response.json()["outcome"] == "undocumented_fault"
    assert response.json()["prescription"] is None
    assert response.json()["citations"] == []
    assert type(composition) is AnalysisRuntimeComposition
    provider = composition.generation_provider
    assert type(provider) is FakeGenerationProvider
    assert provider.call_count == 0


@pytest.mark.failure_matrix
def test_mapping_without_a_governed_document_is_unready_and_never_prescribes(
    tmp_path: Path,
) -> None:
    bundle = _build_bundle(tmp_path)
    manifest = deepcopy(bundle.manifest)
    manifest["documents"] = []
    settings = _rewrite_manifest(bundle, manifest)
    application = create_app(settings=settings)

    with TestClient(application) as client:
        ready = client.get("/health/ready")
        response = client.post(
            "/analysis",
            json=bundle.request.model_dump(mode="json"),
        )
        composition = application.state.analysis_runtime

    assert ready.status_code == 503
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "analysis_unavailable"
    assert "prescription" not in response.json()
    assert composition is None


@pytest.mark.failure_matrix
@pytest.mark.parametrize("incompatible_link", ("model", "projection"))
def test_mapping_cross_binding_is_fail_closed(
    tmp_path: Path,
    incompatible_link: str,
) -> None:
    bundle = _build_bundle(tmp_path)
    manifest = deepcopy(bundle.manifest)
    if incompatible_link == "model":
        mapping = build_fault_knowledge_mapping(
            mapping_version=manifest["mapping"]["mapping_version"],
            mappings={"unknown-synthetic-fault": [bundle.document_id]},
        )
        mapping_bytes = fault_knowledge_mapping_json_bytes(mapping)
        (bundle.root / "fault-knowledge.json").write_bytes(mapping_bytes)
        manifest["mapping"]["artifact"]["sha256"] = sha256(mapping_bytes).hexdigest()
        manifest["mapping"]["mapping_sha256"] = mapping.mapping_sha256
    else:
        manifest["projection_policy"]["priorities"] = {}
    _reauthorize_manifest(manifest)
    settings = _rewrite_manifest(bundle, manifest)

    with TestClient(create_app(settings=settings)) as client:
        ready = client.get("/health/ready")
        response = client.post(
            "/analysis",
            json=bundle.request.model_dump(mode="json"),
        )

    assert ready.status_code == 503
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "analysis_unavailable"


def _mutate_manifest(manifest: dict[str, Any], field: str) -> None:
    replacements: dict[str, tuple[str, ...]] = {
        "operating_policy": ("operating_state_policy_sha256",),
        "model_dataset": ("model", "dataset_id"),
        "model_content": ("model", "content_sha256"),
        "model_training": ("model", "training_partition_sha256"),
        "model_manifest": ("model", "artifact", "manifest_sha256"),
        "index_content": ("index", "content_sha256"),
        "index_source_content": ("index", "source_model_content_sha256"),
        "index_manifest": ("index", "artifact", "manifest_sha256"),
        "mapping_semantics": ("mapping", "mapping_sha256"),
        "mapping_file": ("mapping", "artifact", "sha256"),
        "retrieval_policy": ("retrieval_policy", "policy_sha256"),
        "projection_policy": ("projection_policy", "policy_sha256"),
        "prompt": ("prompt_sha256",),
        "authorization": ("authorization_sha256",),
        "chunking": ("chunking", "configuration_id"),
        "document_source": ("documents", "0", "source_sha256"),
        "document_file": ("documents", "0", "extraction", "sha256"),
    }
    if field == "model_id":
        manifest["model"]["model_id"] = "model_different_sen79_v1"
        return
    if field == "index_count":
        manifest["index"]["record_count"] += 1
        return
    if field == "index_id":
        manifest["index"]["index_id"] = "similarity_index_v1_" + "0" * 32
        return
    if field == "index_source_model":
        manifest["index"]["source_model_id"] = "model_different_sen79_v1"
        return
    if field == "mapping_version":
        manifest["mapping"]["mapping_version"] = "different-mapping.v1"
        return
    if field == "retrieval_version":
        manifest["retrieval_policy"]["policy_version"] = "different-retrieval.v1"
        return
    if field == "projection_priority":
        fault_code = next(iter(manifest["projection_policy"]["priorities"]))
        manifest["projection_policy"]["priorities"][fault_code] = "scheduled"
        return
    if field == "prompt_id":
        manifest["prompt_id"] = "prompt_different.v1"
        return
    if field == "provider":
        manifest["provider"]["provider_id"] = "different-provider.v1"
        return
    if field == "provider_kind":
        manifest["provider"]["kind"] = "network"
        return
    if field == "configuration":
        manifest["configuration_id"] = "config_" + "0" * 32
        return
    if field == "embedding":
        manifest["embedding"]["provider_id"] = "different-embedding.v1"
        return
    if field == "embedding_representation":
        manifest["embedding"]["representation_version"] = "different-vector.v1"
        return
    if field == "document_id":
        manifest["documents"][0]["document_id"] = "doc_" + "0" * 64
        return
    if field == "document_version":
        manifest["documents"][0]["document_version_id"] = "docver_" + "0" * 64
        return
    if field == "document_chunk":
        manifest["documents"][0]["chunk_ids"] = ["chunk_" + "0" * 32]
        return
    if field == "unsafe_path":
        manifest["model"]["artifact"]["path"] = "../model"
        return
    path = replacements[field]
    target: Any = manifest
    for component in path[:-1]:
        target = target[int(component)] if component.isdigit() else target[component]
    target[path[-1]] = "0" * 64


@pytest.mark.failure_matrix
@pytest.mark.parametrize(
    "field",
    (
        "operating_policy",
        "model_dataset",
        "model_content",
        "model_training",
        "model_manifest",
        "model_id",
        "index_content",
        "index_source_content",
        "index_manifest",
        "index_id",
        "index_source_model",
        "index_count",
        "mapping_semantics",
        "mapping_file",
        "mapping_version",
        "retrieval_policy",
        "retrieval_version",
        "projection_policy",
        "projection_priority",
        "prompt",
        "prompt_id",
        "provider",
        "provider_kind",
        "authorization",
        "configuration",
        "chunking",
        "embedding",
        "embedding_representation",
        "document_source",
        "document_file",
        "document_id",
        "document_version",
        "document_chunk",
        "unsafe_path",
    ),
)
def test_artifact_binding_mismatch_is_unready_without_synthetic_fallback(
    tmp_path: Path,
    field: str,
) -> None:
    bundle = _build_bundle(tmp_path)
    manifest = deepcopy(bundle.manifest)
    _mutate_manifest(manifest, field)
    settings = _rewrite_manifest(bundle, manifest)

    with TestClient(create_app(settings=settings)) as client:
        live = client.get("/health/live")
        ready = client.get("/health/ready")
        analysis = client.post(
            "/analysis",
            json=bundle.request.model_dump(mode="json"),
        )
        recovered = client.get("/analysis/ana_unavailable_runtime")

    assert live.status_code == 200
    assert ready.status_code == 503
    assert analysis.status_code == 503
    assert recovered.status_code == 503
    assert analysis.headers["X-Analysis-Mode"] == "artifacts"
    assert analysis.json()["error"]["code"] == "analysis_unavailable"
    assert "synthetic" not in analysis.text.lower()
    assert str(bundle.root) not in analysis.text


@pytest.mark.failure_matrix
@pytest.mark.parametrize(
    "artifact",
    ("missing", "manifest", "model", "index", "mapping", "extraction"),
)
def test_missing_or_corrupt_artifact_is_sanitized_and_unready(
    tmp_path: Path,
    artifact: str,
) -> None:
    bundle = _build_bundle(tmp_path)
    settings = bundle.settings
    if artifact == "missing":
        (bundle.root / "approved-extraction.json").unlink()
    elif artifact == "manifest":
        settings = Settings.model_validate(
            {
                **bundle.settings.model_dump(mode="python"),
                "analysis_artifacts_manifest_sha256": "0" * 64,
            }
        )
    else:
        paths = {
            "model": bundle.root / "model" / "training_vectors.npy",
            "index": bundle.root / "index" / "vectors.npy",
            "mapping": bundle.root / "fault-knowledge.json",
            "extraction": bundle.root / "approved-extraction.json",
        }
        paths[artifact].write_bytes(b"corrupt synthetic artifact")

    with TestClient(create_app(settings=settings)) as client:
        live = client.get("/health/live")
        ready = client.get("/health/ready")
        analysis = client.post(
            "/analysis",
            json=bundle.request.model_dump(mode="json"),
        )

    assert live.status_code == 200
    assert ready.status_code == 503
    assert analysis.status_code == 503
    assert str(bundle.root) not in ready.text + analysis.text


def test_analysis_mode_configuration_is_required_and_mutually_exclusive(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate(
            {"environment": "offline", "persistence_backend": "memory"}
        )
    with pytest.raises(ValidationError):
        Settings.model_validate(
            {
                "environment": "offline",
                "persistence_backend": "memory",
                "analysis_mode": "legacy",
            }
        )
    with pytest.raises(ValidationError):
        Settings.model_validate(
            {
                "environment": "offline",
                "persistence_backend": "memory",
                "analysis_mode": "artifacts",
            }
        )
    with pytest.raises(ValidationError):
        Settings.model_validate(
            {
                "environment": "offline",
                "persistence_backend": "memory",
                "analysis_mode": "synthetic_demo",
                "analysis_artifacts_manifest": tmp_path / "manifest.json",
                "analysis_artifacts_manifest_sha256": "0" * 64,
            }
        )


@pytest.mark.failure_matrix
def test_missing_analysis_mode_fails_startup_with_a_sanitized_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_marker = "sen79-private-startup-marker"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PRESCRIPTIVE_MAINTENANCE_ENVIRONMENT", "offline")
    monkeypatch.setenv("PRESCRIPTIVE_MAINTENANCE_PERSISTENCE_BACKEND", "memory")
    monkeypatch.delenv("PRESCRIPTIVE_MAINTENANCE_ANALYSIS_MODE", raising=False)
    monkeypatch.setenv(
        "PRESCRIPTIVE_MAINTENANCE_ANALYSIS_ARTIFACTS_MANIFEST",
        str(tmp_path / private_marker / "runtime.json"),
    )

    with pytest.raises(RuntimeError) as captured, TestClient(create_app()):
        pass

    assert "startup configuration is invalid" in str(captured.value)
    assert private_marker not in str(captured.value)


@pytest.mark.failure_matrix
def test_artifacts_mode_rejects_the_synthetic_injection_seam(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)
    with (
        pytest.raises(RuntimeError, match="startup configuration is invalid"),
        TestClient(
            create_app(
                settings=bundle.settings,
                analysis_service=build_synthetic_analysis_service(),
            )
        ),
    ):
        pass


@pytest.mark.failure_matrix
def test_artifacts_postgres_dependency_is_required_and_failure_is_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _build_bundle(tmp_path)
    private_marker = "sen79-private-database-marker"
    settings = Settings.model_validate(
        {
            **bundle.settings.model_dump(mode="python"),
            "environment": "local",
            "persistence_backend": "postgres",
            "database_url": (
                "postgresql://synthetic_user:"
                f"{private_marker}@127.0.0.1:1/synthetic_database"
            ),
        }
    )
    bounded_timeout_seen = False
    private_marker_seen = False

    def reject_database_dependency(database_url: str) -> None:
        nonlocal bounded_timeout_seen, private_marker_seen
        bounded_timeout_seen = "connect_timeout=1" in database_url
        private_marker_seen = private_marker in database_url
        raise RuntimeError("Synthetic database dependency is unavailable.")

    monkeypatch.setattr(
        artifact_documents,
        "build_postgres_connection_factory",
        reject_database_dependency,
    )

    with TestClient(create_app(settings=settings)) as client:
        live = client.get("/health/live")
        ready = client.get("/health/ready")
        analysis = client.post(
            "/analysis",
            json=bundle.request.model_dump(mode="json"),
        )

    public_output = live.text + ready.text + analysis.text
    assert live.status_code == 200
    assert ready.status_code == 503
    assert analysis.status_code == 503
    assert bounded_timeout_seen
    assert private_marker_seen
    assert private_marker not in public_output
    assert str(bundle.root) not in public_output


def test_openapi_documents_the_sanitized_mode_header() -> None:
    schema = create_app().openapi()

    assert schema["components"]["headers"]["AnalysisMode"]["schema"] == {
        "type": "string",
        "enum": ["synthetic_demo", "artifacts"],
    }
    assert schema["paths"]["/analysis"]["post"]["responses"]["200"]["headers"][
        "X-Analysis-Mode"
    ] == {"$ref": "#/components/headers/AnalysisMode"}


def test_optional_artifact_smoke_outputs_only_sanitized_aggregates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle = _build_bundle(tmp_path)
    monkeypatch.setenv("PRESCRIPTIVE_MAINTENANCE_ENVIRONMENT", "offline")
    monkeypatch.setenv("PRESCRIPTIVE_MAINTENANCE_PERSISTENCE_BACKEND", "memory")
    monkeypatch.setenv("PRESCRIPTIVE_MAINTENANCE_ANALYSIS_MODE", "artifacts")
    monkeypatch.setenv(
        "PRESCRIPTIVE_MAINTENANCE_ANALYSIS_ARTIFACTS_MANIFEST",
        str(bundle.manifest_path),
    )
    monkeypatch.setenv(
        "PRESCRIPTIVE_MAINTENANCE_ANALYSIS_ARTIFACTS_MANIFEST_SHA256",
        _file_sha256(bundle.manifest_path),
    )
    monkeypatch.delenv("PRESCRIPTIVE_MAINTENANCE_DATABASE_URL", raising=False)

    namespace = run_path(str(Path(__file__).parents[3] / "scripts" / "smoke.py"))
    check_artifacts = cast(Callable[[], bool], namespace["_check_artifacts"])
    assert check_artifacts()

    output = capsys.readouterr().out
    assert "amostras=4" in output
    assert "registros=4" in output
    assert "documentos=1" in output
    assert "chunks=1" in output
    assert str(bundle.root) not in output
    assert bundle.model_id not in output
    assert bundle.document_id not in output
    assert _FAULT_CLASS not in output


def test_optional_artifact_smoke_marks_absence_as_skipped(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("PRESCRIPTIVE_MAINTENANCE_ANALYSIS_MODE", raising=False)
    namespace = run_path(str(Path(__file__).parents[3] / "scripts" / "smoke.py"))
    check_artifacts = cast(Callable[[], bool], namespace["_check_artifacts"])
    cast(Any, check_artifacts).__globals__["REPOSITORY_ROOT"] = Path(
        "missing-synthetic-root"
    )

    assert not check_artifacts()

    assert capsys.readouterr().out == (
        "Artefatos: indisponíveis; verificação opcional ignorada.\n"
    )

    main = cast(Callable[..., None], namespace["main"])

    def skipped_run_smoke(
        _with_services: bool,
        _with_applications: bool,
        _with_artifacts: bool,
    ) -> bool:
        return False

    cast(Any, main).__globals__["_run_smoke"] = skipped_run_smoke
    main(with_artifacts=True)

    assert capsys.readouterr().out == "Smoke concluído com sucesso (base local).\n"


def test_optional_artifact_smoke_rejects_an_invalid_configured_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PRESCRIPTIVE_MAINTENANCE_ANALYSIS_MODE", "legacy")
    namespace = run_path(str(Path(__file__).parents[3] / "scripts" / "smoke.py"))
    check_artifacts = cast(Callable[[], None], namespace["_check_artifacts"])
    smoke_failure = cast(type[RuntimeError], namespace["SmokeFailure"])

    with pytest.raises(smoke_failure, match="configurado é inválido"):
        check_artifacts()


def test_optional_artifact_smoke_rejects_an_invalid_dotenv_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PRESCRIPTIVE_MAINTENANCE_ANALYSIS_MODE", raising=False)
    (tmp_path / ".env").write_text(
        "PRESCRIPTIVE_MAINTENANCE_ANALYSIS_MODE=\n",
        encoding="utf-8",
    )
    namespace = run_path(str(Path(__file__).parents[3] / "scripts" / "smoke.py"))
    check_artifacts = cast(Callable[[], None], namespace["_check_artifacts"])
    cast(Any, check_artifacts).__globals__["REPOSITORY_ROOT"] = tmp_path
    smoke_failure = cast(type[RuntimeError], namespace["SmokeFailure"])

    with pytest.raises(smoke_failure, match="configurado é inválido"):
        check_artifacts()
