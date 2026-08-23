"""Golden end-to-end journeys for the five product states and document cycle."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from prescriptive_maintenance import product_golden

_GOLDEN_PATH = Path(__file__).parent / "golden" / "product_journeys.v1.json"
_GOLDEN_SHA256 = "6f0de6237178c27a1b01cf2bc2ab48ed4cfde5799cbd51afd6e9e96311282c54"


@pytest.fixture(scope="module")
def golden_report() -> dict[str, object]:
    return product_golden.run_product_golden(_GOLDEN_PATH)


def test_five_http_states_are_closed_and_layer_calls_match_the_golden_set(
    golden_report: dict[str, object],
) -> None:
    journeys = _sequence(golden_report["analysis_journeys"])

    assert tuple(_mapping(item)["id"] for item in journeys) == (
        "normal",
        "documented_fault",
        "undocumented_fault",
        "out_of_distribution",
        "degraded",
    )
    assert all(
        _mapping(item)["id"] == _mapping(item)["observed_outcome"] for item in journeys
    )
    assert all(_mapping(item)["round_trip"] is True for item in journeys)
    assert all(_mapping(item)["approved_citation_match"] is True for item in journeys)
    assert tuple(_mapping(_mapping(item)["layer_calls"]) for item in journeys) == (
        {"generation": 0, "model": 1, "provider": 0, "retrieval": 0},
        {"generation": 1, "model": 1, "provider": 1, "retrieval": 1},
        {"generation": 0, "model": 1, "provider": 0, "retrieval": 1},
        {"generation": 0, "model": 1, "provider": 0, "retrieval": 0},
        {"generation": 1, "model": 1, "provider": 1, "retrieval": 1},
    )


def test_document_registration_approval_and_rejection_use_real_boundaries(
    golden_report: dict[str, object],
) -> None:
    documents = tuple(
        _mapping(item) for item in _sequence(golden_report["document_journeys"])
    )

    assert tuple(item["decision"] for item in documents) == ("approve", "reject")
    assert tuple(item["registered_status"] for item in documents) == (
        "received",
        "received",
    )
    assert tuple(item["result_status"] for item in documents) == (
        "approved",
        "rejected",
    )
    assert all(str(item["document_id"]).startswith("doc_") for item in documents)
    assert all(
        str(item["document_version"]).startswith("docver_") for item in documents
    )


def test_fake_provider_is_blocked_without_current_approved_evidence(
    golden_report: dict[str, object],
) -> None:
    probes = {
        cast(str, item["id"]): item
        for item in (
            _mapping(value) for value in _sequence(golden_report["safety_probes"])
        )
    }

    assert probes["missing_evidence"] == {
        "id": "missing_evidence",
        "provider_calls": 0,
        "refusal": "no_evidence",
        "status": "refused",
    }
    assert probes["rejected_evidence"] == {
        "id": "rejected_evidence",
        "provider_calls": 0,
        "refusal": "stale_evidence",
        "status": "refused",
    }
    assert probes["invented_citation"] == {
        "id": "invented_citation",
        "provider_calls": 1,
        "refusal": "invalid_provider_output",
        "status": "refused",
    }


def test_report_separates_layers_and_identifies_versions_and_configuration(
    golden_report: dict[str, object],
) -> None:
    metrics = _mapping(golden_report["metrics"])
    assert metrics["measurement"] == "deterministic_call_and_error_counts"
    assert _mapping(metrics["model"]) == {
        "attempts": 5,
        "errors": 0,
        "successes": 5,
        "version": "product-golden-model.v1",
    }
    assert _mapping(metrics["retrieval"]) == {
        "attempts": 3,
        "errors": 0,
        "policy_sha256": (
            "0455d46e0c7fb2bd2fde5e9bdfbfa0704a08786311a520aa8d74baba52a67de4"
        ),
        "successes": 3,
        "version": "product-golden-approved-retrieval.v1",
    }
    assert _mapping(metrics["generation"]) == {
        "attempts": 2,
        "errors": 1,
        "provider_attempts": 2,
        "provider_errors": 1,
        "successes": 1,
        "version": "prescriptive-generation-system.v2",
    }

    configuration = _mapping(golden_report["configuration"])
    assert configuration == {
        "environment": "offline",
        "persistence_backend": "memory",
        "provider_mode": "synthetic_offline",
        "top_k": 1,
    }
    bindings = _mapping(golden_report["bindings"])
    assert bindings["api_contract_version"] == "1.0.0"
    assert bindings["generation_contract_version"] == "prescriptive-generation.v1"
    assert bindings["provider_version"] == "fake-generation-provider.v1"
    assert bindings["retrieval_policy_version"] == "product-golden-retrieval.v1"
    assert bindings["mapping_version"] == "product-golden-mapping.v1"


def test_report_is_byte_stable_and_contains_no_raw_inputs_or_document_content(
    golden_report: dict[str, object],
) -> None:
    first = product_golden.render_product_golden_report(golden_report)
    second = product_golden.render_product_golden_report(golden_report)

    assert first == second
    assert first.endswith("\n")
    parsed = _mapping(json.loads(first))
    golden = _mapping(parsed["golden_set"])
    assert golden == {
        "id": "product-golden-synthetic.v1",
        "sha256": _GOLDEN_SHA256,
    }
    assert _mapping(parsed["limits"]) == {
        "aws_dependencies": False,
        "network_calls": False,
        "original_materials_accessed": False,
        "paid_provider_calls": False,
        "raw_content_in_report": False,
    }
    forbidden = (
        "feature_template",
        "temperature_c",
        "rpm",
        "Synthetic approved evidence",
        "golden-approved.synthetic.pdf",
        "golden-rejected.synthetic.pdf",
        "1111111111111111111111111111111111111111111111111111111111111111",
        "chunk_golden_invented_01",
        str(_GOLDEN_PATH.parent),
    )
    assert not any(value in first for value in forbidden)


def test_cli_emits_the_sanitized_report_on_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert product_golden.main([]) == 0

    streams = capsys.readouterr()
    payload = _mapping(json.loads(streams.out))
    assert payload["schema_version"] == 1
    assert _mapping(payload["golden_set"])["id"] == "product-golden-synthetic.v1"
    assert "golden-e2e:" not in streams.err


def _mapping(value: object) -> dict[str, object]:
    assert type(value) is dict
    return cast(dict[str, object], value)


def _sequence(value: object) -> list[object]:
    assert type(value) is list
    return cast(list[object], value)
