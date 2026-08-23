"""Snapshot and code-generation compatibility checks for OpenAPI v1."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from prescriptive_maintenance.contracts import (
    ANALYSIS_FEATURE_NAMES,
    API_CONTRACT_VERSION,
    DEFAULT_TOP_K,
    MAX_TOP_K,
    AnalysisResponse,
    DocumentResponse,
)
from prescriptive_maintenance.openapi import openapi_bytes

OPENAPI_SNAPSHOT = Path(__file__).parents[1] / "openapi" / "v1.json"


def _iter_refs(value: object) -> tuple[str, ...]:
    refs: list[str] = []
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        for key, nested in mapping.items():
            if key == "$ref" and isinstance(nested, str):
                refs.append(nested)
            else:
                refs.extend(_iter_refs(nested))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for nested in cast(Sequence[object], value):
            refs.extend(_iter_refs(nested))
    return tuple(refs)


def test_openapi_snapshot_is_byte_stable_across_two_generations() -> None:
    first = openapi_bytes()
    second = openapi_bytes()

    assert first == second
    assert first == OPENAPI_SNAPSHOT.read_bytes()
    assert first.endswith(b"\n")


def test_openapi_has_frozen_paths_and_unique_operation_ids() -> None:
    schema = json.loads(openapi_bytes())

    assert schema["openapi"] == "3.1.0"
    assert schema["info"]["version"] == API_CONTRACT_VERSION
    assert set(schema["paths"]) == {
        "/health/live",
        "/analysis",
        "/analysis/{analysis_id}",
        "/documents",
        "/documents/{document_id}",
        "/documents/{document_id}/approve",
        "/documents/{document_id}/reject",
        "/documents/{document_id}/reprocess",
    }
    operation_ids = [
        operation["operationId"]
        for path in schema["paths"].values()
        for operation in path.values()
    ]
    assert len(operation_ids) == len(set(operation_ids))


def test_openapi_freezes_exact_feature_order_and_top_k_limits() -> None:
    schema = json.loads(openapi_bytes())
    components = schema["components"]["schemas"]
    features = components["AnalysisFeatures"]
    request = components["AnalysisRequest"]

    assert tuple(features["properties"]) == ANALYSIS_FEATURE_NAMES
    assert tuple(features["required"]) == ANALYSIS_FEATURE_NAMES
    assert features["additionalProperties"] is False
    assert request["properties"]["top_k"] == {
        "type": "integer",
        "maximum": MAX_TOP_K,
        "minimum": 1,
        "title": "Top K",
        "default": DEFAULT_TOP_K,
    }
    assert request["additionalProperties"] is False


def test_openapi_models_neighbor_distance_without_unit_ceiling() -> None:
    schema = json.loads(openapi_bytes())
    components = schema["components"]["schemas"]
    neighbor = components["OpaqueNeighbor"]
    distance = neighbor["properties"]["distance"]

    assert tuple(neighbor["required"]) == (
        "neighbor_ref",
        "rank",
        "fault_code",
        "distance",
    )
    assert distance["minimum"] == 0.0
    assert "maximum" not in distance
    assert "similarity" not in neighbor["properties"]
    assert (
        "não calibrada"
        in components["SufficientSupport"]["properties"]["support_score"]["description"]
    )


def test_openapi_requires_opaque_auditable_citation_references() -> None:
    schema = json.loads(openapi_bytes())
    citation = schema["components"]["schemas"]["Citation"]

    assert tuple(citation["required"]) == (
        "document_id",
        "document_version",
        "chunk",
        "title",
        "locator",
    )
    assert citation["properties"]["document_version"]["pattern"].startswith("^docver_")
    assert citation["properties"]["chunk"]["pattern"].startswith("^chunk_")
    assert not {"text", "content", "source_path"} & set(citation["properties"])


def test_openapi_examples_cover_five_outcomes_and_document_states() -> None:
    schema = json.loads(openapi_bytes())
    analysis_examples = schema["paths"]["/analysis"]["post"]["responses"]["200"][
        "content"
    ]["application/json"]["examples"]
    document_examples = schema["paths"]["/documents/{document_id}"]["get"]["responses"][
        "200"
    ]["content"]["application/json"]["examples"]

    assert set(analysis_examples) == {
        "normal",
        "documented_fault",
        "undocumented_fault",
        "out_of_distribution",
        "degraded",
    }
    assert set(document_examples) == {
        "received",
        "processing",
        "pending_approval",
        "approved",
        "rejected",
        "failed",
        "superseded",
    }
    for example in analysis_examples.values():
        AnalysisResponse.model_validate_json(json.dumps(example["value"]))
    for example in document_examples.values():
        DocumentResponse.model_validate_json(json.dumps(example["value"]))


def test_every_local_reference_resolves_for_future_client_generation() -> None:
    schema = json.loads(openapi_bytes())
    components = schema["components"]["schemas"]
    for reference in _iter_refs(schema):
        assert reference.startswith("#/components/schemas/")
        component = reference.rsplit("/", maxsplit=1)[-1]
        assert component in components

    analysis_response = components["AnalysisResponse"]
    document_response = components["DocumentResponse"]
    assert analysis_response["discriminator"]["propertyName"] == "outcome"
    assert document_response["discriminator"]["propertyName"] == "status"
    for reference in analysis_response["oneOf"]:
        component = reference["$ref"].rsplit("/", maxsplit=1)[-1]
        assert "outcome" in components[component]["required"]
    for reference in document_response["oneOf"]:
        component = reference["$ref"].rsplit("/", maxsplit=1)[-1]
        assert "status" in components[component]["required"]

    serialized = json.dumps(schema)
    assert "ModelPrediction" not in serialized
    assert "DocumentEvidence" not in serialized
    assert "feature_vector" not in serialized
    assert "source_path" not in serialized
    assert "embedding" not in serialized


def test_snapshot_location_is_inside_the_backend_contract_boundary() -> None:
    expected = Path(__file__).parents[1] / "openapi" / "v1.json"

    assert expected == OPENAPI_SNAPSHOT
