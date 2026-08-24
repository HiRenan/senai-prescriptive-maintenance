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


def test_document_operations_declare_exact_applicable_responses() -> None:
    schema = json.loads(openapi_bytes())
    expected_statuses = {
        ("/documents", "post"): {"201", "409", "422", "503"},
        ("/documents", "get"): {"200", "503"},
        ("/documents/{document_id}", "get"): {"200", "404", "422", "503"},
        ("/documents/{document_id}/approve", "post"): {
            "200",
            "404",
            "409",
            "422",
            "503",
        },
        ("/documents/{document_id}/reject", "post"): {
            "200",
            "404",
            "409",
            "422",
            "503",
        },
        ("/documents/{document_id}/reprocess", "post"): {
            "200",
            "404",
            "409",
            "422",
            "503",
        },
    }

    for (path, method), statuses in expected_statuses.items():
        responses = schema["paths"][path][method]["responses"]
        assert set(responses) == statuses
        for status_code in statuses - {"200", "201"}:
            assert responses[status_code]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ErrorResponse"
            }


def test_openapi_distinguishes_analysis_and_document_error_descriptions() -> None:
    schema = json.loads(openapi_bytes())
    paths = schema["paths"]

    assert paths["/analysis"]["post"]["responses"]["503"]["description"] == (
        "A análise está temporariamente indisponível."
    )
    for path, method in (
        ("/documents", "post"),
        ("/documents", "get"),
        ("/documents/{document_id}", "get"),
        ("/documents/{document_id}/approve", "post"),
        ("/documents/{document_id}/reject", "post"),
        ("/documents/{document_id}/reprocess", "post"),
    ):
        assert paths[path][method]["responses"]["503"]["description"] == (
            "O ciclo documental está temporariamente indisponível."
        )

    assert paths["/documents"]["post"]["responses"]["409"]["description"] == (
        "O comando documental conflita com o estado armazenado."
    )
    for path in (
        "/documents/{document_id}/approve",
        "/documents/{document_id}/reject",
        "/documents/{document_id}/reprocess",
    ):
        assert paths[path]["post"]["responses"]["409"]["description"] == (
            "O comando documental conflita com o estado armazenado ou a transição "
            "solicitada não é válida para o estado atual."
        )


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
        "page_number",
    )
    assert citation["properties"]["document_version"]["pattern"].startswith("^docver_")
    assert citation["properties"]["chunk"]["pattern"].startswith("^chunk_")
    assert citation["properties"]["page_number"]["minimum"] == 1
    assert citation["additionalProperties"] is False
    assert not {
        "title",
        "locator",
        "section_ref",
        "text",
        "content",
        "path",
        "source_path",
    } & set(citation["properties"])


def test_openapi_specializes_abstention_reason_for_each_variant() -> None:
    schema = json.loads(openapi_bytes())
    components = schema["components"]["schemas"]
    expected = {
        "UndocumentedFaultAnalysisResult": (
            "UndocumentedFaultAbstention",
            "undocumented_fault",
        ),
        "OutOfDistributionAnalysisResult": (
            "OutOfDistributionAbstention",
            "out_of_distribution",
        ),
        "DegradedAnalysisResult": (
            "DependencyUnavailableAbstention",
            "dependency_unavailable",
        ),
    }

    for result_name, (abstention_name, reason) in expected.items():
        abstention_reference = components[result_name]["properties"]["abstention"]
        assert abstention_reference["$ref"] == (
            f"#/components/schemas/{abstention_name}"
        )
        abstention = components[abstention_name]
        assert tuple(abstention["required"]) == ("reason", "message")
        assert abstention["properties"]["reason"]["const"] == reason
        assert abstention["additionalProperties"] is False


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
    components = schema["components"]
    schemas = components["schemas"]
    headers = components["headers"]
    for reference in _iter_refs(schema):
        assert reference.startswith(("#/components/schemas/", "#/components/headers/"))
        component = reference.rsplit("/", maxsplit=1)[-1]
        if reference.startswith("#/components/schemas/"):
            assert component in schemas
        else:
            assert component in headers

    analysis_response = schemas["AnalysisResponse"]
    document_response = schemas["DocumentResponse"]
    assert analysis_response["discriminator"]["propertyName"] == "outcome"
    assert document_response["discriminator"]["propertyName"] == "status"
    for reference in analysis_response["oneOf"]:
        component = reference["$ref"].rsplit("/", maxsplit=1)[-1]
        assert "outcome" in schemas[component]["required"]
    for reference in document_response["oneOf"]:
        component = reference["$ref"].rsplit("/", maxsplit=1)[-1]
        assert "status" in schemas[component]["required"]

    serialized = json.dumps(schema)
    assert "ModelPrediction" not in serialized
    assert "DocumentEvidence" not in serialized
    assert "feature_vector" not in serialized
    assert "source_path" not in serialized
    assert "embedding" not in serialized


def test_snapshot_location_is_inside_the_backend_contract_boundary() -> None:
    expected = Path(__file__).parents[1] / "openapi" / "v1.json"

    assert expected == OPENAPI_SNAPSHOT
