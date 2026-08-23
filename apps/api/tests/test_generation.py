"""Synthetic tests for the evidence-grounded generation boundary."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast

import pytest
from prescriptive_maintenance.generation import (
    GENERATION_CONTRACT_VERSION,
    GENERATION_SYSTEM_PROMPT,
    GENERATION_SYSTEM_PROMPT_VERSION,
    MAX_EVIDENCE_CONTENT_CHARACTERS,
    MAX_EVIDENCE_ITEMS,
    MAX_TOTAL_EVIDENCE_CONTENT_CHARACTERS,
    BedrockGenerationProvider,
    BedrockProviderConfig,
    BedrockRuntimeClient,
    Diagnosis,
    Evidence,
    FakeGenerationProvider,
    GenerationRequest,
    GenerationStatus,
    InvalidProviderOutputError,
    ProviderExecutionError,
    ProviderRequest,
    ProviderResponse,
    build_provider_request,
    generate_prescription,
    validate_provider_output,
)
from pydantic import ValidationError


def _synthetic_request() -> GenerationRequest:
    return GenerationRequest(
        diagnosis=Diagnosis(
            fault_code="synthetic-bearing-fault",
            technical_summary="Synthetic model diagnosis for a test asset.",
        ),
        evidence=(
            Evidence(
                evidence_id="synthetic-evidence-1",
                source_id="synthetic-source-1",
                locator="synthetic section 1",
                content="A synthetic vibration indicator crossed a test threshold.",
            ),
        ),
    )


def _synthetic_evidence(
    index: int, *, content: str = "Synthetic evidence."
) -> Evidence:
    return Evidence(
        evidence_id=f"synthetic-evidence-{index:02d}",
        source_id=f"synthetic-source-{index:02d}",
        locator=f"synthetic section {index}",
        content=content,
    )


def _valid_output_text(evidence_id: str = "synthetic-evidence-1") -> str:
    return json.dumps(
        {
            "schema_version": GENERATION_CONTRACT_VERSION,
            "diagnostic_support": {
                "fault_code": "synthetic-bearing-fault",
                "status": "supported",
                "assessment": "The synthetic indicator documents model support.",
                "citations": [{"evidence_id": evidence_id}],
            },
            "prescriptions": [
                {
                    "action": "Inspect the synthetic asset.",
                    "rationale": (
                        "The cited synthetic observation warrants verification."
                    ),
                    "citations": [{"evidence_id": evidence_id}],
                }
            ],
            "warnings": [],
        },
        sort_keys=True,
        separators=(",", ":"),
    )


class _RecordingBedrockClient:
    def __init__(self, response: Mapping[str, object]) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def converse(self, **kwargs: object) -> Mapping[str, object]:
        self.calls.append(kwargs)
        return self.response


class _FailingBedrockClient:
    def __init__(self, private_message: str) -> None:
        self.private_message = private_message

    def converse(self, **kwargs: object) -> Mapping[str, object]:
        del kwargs
        raise RuntimeError(self.private_message)


class _InvalidPortResponseProvider:
    def generate(self, request: ProviderRequest) -> ProviderResponse:
        del request
        return cast(ProviderResponse, {"private": "synthetic-private-response"})


def _bedrock_response(
    output_text: str,
    *,
    usage: Mapping[str, object] | None = None,
) -> Mapping[str, object]:
    response: dict[str, object] = {
        "output": {"message": {"content": [{"text": output_text}]}}
    }
    if usage is not None:
        response["usage"] = usage
    return response


def test_prompt_v1_requires_only_evidence_citations_and_explicit_gaps() -> None:
    normalized_prompt = GENERATION_SYSTEM_PROMPT.text.casefold()

    assert GENERATION_SYSTEM_PROMPT.version == GENERATION_SYSTEM_PROMPT_VERSION
    assert GENERATION_SYSTEM_PROMPT_VERSION in GENERATION_SYSTEM_PROMPT.text
    assert "exclusivamente" in normalized_prompt
    assert "nunca complete lacunas" in normalized_prompt
    assert "evidence_id" in GENERATION_SYSTEM_PROMPT.text
    assert "insufficient_evidence" in GENERATION_SYSTEM_PROMPT.text
    assert "conhecimento externo" in normalized_prompt
    assert "dado imutável" in normalized_prompt
    assert "nunca prescreva sem evidência citada" in normalized_prompt


def test_generation_request_rejects_duplicate_evidence_ids() -> None:
    evidence = _synthetic_request().evidence[0]

    with pytest.raises(ValidationError):
        GenerationRequest(
            diagnosis=_synthetic_request().diagnosis,
            evidence=(evidence, evidence),
        )


def test_generation_request_enforces_individual_total_and_count_budgets() -> None:
    diagnosis = _synthetic_request().diagnosis

    with pytest.raises(ValidationError):
        _synthetic_evidence(
            1,
            content="x" * (MAX_EVIDENCE_CONTENT_CHARACTERS + 1),
        )

    with pytest.raises(ValidationError, match="content exceeds"):
        GenerationRequest(
            diagnosis=diagnosis,
            evidence=tuple(
                _synthetic_evidence(
                    index,
                    content="x" * MAX_EVIDENCE_CONTENT_CHARACTERS,
                )
                for index in range(
                    1,
                    MAX_TOTAL_EVIDENCE_CONTENT_CHARACTERS
                    // MAX_EVIDENCE_CONTENT_CHARACTERS
                    + 2,
                )
            ),
        )

    with pytest.raises(ValidationError, match="count exceeds"):
        GenerationRequest(
            diagnosis=diagnosis,
            evidence=tuple(
                _synthetic_evidence(index) for index in range(1, MAX_EVIDENCE_ITEMS + 2)
            ),
        )


def test_provider_payload_orders_evidence_deterministically() -> None:
    diagnosis = _synthetic_request().diagnosis
    first = _synthetic_evidence(1)
    second = _synthetic_evidence(2)
    forward = build_provider_request(
        GenerationRequest(diagnosis=diagnosis, evidence=(first, second))
    )
    reverse = build_provider_request(
        GenerationRequest(diagnosis=diagnosis, evidence=(second, first))
    )
    payload = json.loads(forward.input_json)

    assert forward.input_json == reverse.input_json
    assert forward.allowed_evidence_ids == (
        "synthetic-evidence-01",
        "synthetic-evidence-02",
    )
    assert payload["diagnosis"] == {
        "fault_code": diagnosis.fault_code,
        "technical_summary": diagnosis.technical_summary,
    }


def test_fake_provider_is_deterministic_and_offline_without_aws_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_PROFILE",
        "AWS_DEFAULT_REGION",
    ):
        monkeypatch.delenv(name, raising=False)
    first_provider = FakeGenerationProvider()
    second_provider = FakeGenerationProvider()

    first_result = generate_prescription(_synthetic_request(), first_provider)
    second_result = generate_prescription(_synthetic_request(), second_provider)

    assert first_result == second_result
    assert first_result.status is GenerationStatus.GENERATED
    assert first_result.diagnosis == _synthetic_request().diagnosis
    assert first_result.diagnostic_support is not None
    assert first_result.diagnostic_support.fault_code == (
        first_result.diagnosis.fault_code
    )
    assert first_provider.call_count == 1
    assert second_provider.call_count == 1


def test_no_evidence_is_distinct_and_does_not_call_the_provider() -> None:
    provider = FakeGenerationProvider()

    request = GenerationRequest(diagnosis=_synthetic_request().diagnosis)

    result = generate_prescription(request, provider)

    assert result.status is GenerationStatus.NO_EVIDENCE
    assert result.diagnosis == request.diagnosis
    assert result.diagnostic_support is None
    assert result.prescriptions == ()
    assert result.usage is None
    assert result.warnings[0].code == "no_evidence"
    assert provider.call_count == 0


def test_invalid_provider_schema_is_sanitized_and_distinct() -> None:
    private_value = "synthetic-private-secret-8472"
    provider = FakeGenerationProvider(
        response_text=json.dumps({"unexpected": private_value})
    )

    result = generate_prescription(_synthetic_request(), provider)

    assert result.status is GenerationStatus.INVALID_OUTPUT
    assert result.warnings[0].code == "invalid_output"
    assert result.usage is None
    assert private_value not in repr(result)


def test_unknown_citation_is_invalid_without_exposing_the_identifier() -> None:
    private_identifier = "synthetic-private-evidence-9371"
    provider = FakeGenerationProvider(
        response_text=_valid_output_text(private_identifier)
    )

    result = generate_prescription(_synthetic_request(), provider)

    assert result.status is GenerationStatus.INVALID_OUTPUT
    assert private_identifier not in repr(result)


def test_provider_cannot_replace_the_model_diagnosis() -> None:
    private_fault_code = "synthetic-provider-invented-fault"
    output = json.loads(_valid_output_text())
    output["diagnostic_support"]["fault_code"] = private_fault_code
    provider = FakeGenerationProvider(response_text=json.dumps(output))

    result = generate_prescription(_synthetic_request(), provider)

    assert result.status is GenerationStatus.INVALID_OUTPUT
    assert result.diagnosis == _synthetic_request().diagnosis
    assert result.diagnostic_support is None
    assert private_fault_code not in repr(result)


def test_insufficient_evidence_cannot_include_a_prescription() -> None:
    invalid_output = json.dumps(
        {
            "schema_version": GENERATION_CONTRACT_VERSION,
            "diagnostic_support": {
                "fault_code": "synthetic-bearing-fault",
                "status": "insufficient_evidence",
                "assessment": None,
                "citations": [],
            },
            "prescriptions": [
                {
                    "action": "Do not allow this unsupported synthetic action.",
                    "rationale": "This synthetic rationale has no evidence.",
                    "citations": [{"evidence_id": "synthetic-evidence-1"}],
                }
            ],
            "warnings": [
                {
                    "code": "evidence_gap",
                    "message": "Synthetic evidence is insufficient.",
                    "citations": [],
                }
            ],
        }
    )

    result = generate_prescription(
        _synthetic_request(),
        FakeGenerationProvider(response_text=invalid_output),
    )

    assert result.status is GenerationStatus.INVALID_OUTPUT
    assert result.prescriptions == ()


def test_valid_evidence_gap_has_an_explicit_non_generated_outcome() -> None:
    gap_output = json.dumps(
        {
            "schema_version": GENERATION_CONTRACT_VERSION,
            "diagnostic_support": {
                "fault_code": "synthetic-bearing-fault",
                "status": "insufficient_evidence",
                "assessment": None,
                "citations": [],
            },
            "prescriptions": [],
            "warnings": [
                {
                    "code": "evidence_gap",
                    "message": "Synthetic evidence is insufficient.",
                    "citations": [],
                }
            ],
        }
    )

    result = generate_prescription(
        _synthetic_request(),
        FakeGenerationProvider(response_text=gap_output),
    )

    assert result.status is GenerationStatus.INSUFFICIENT_EVIDENCE
    assert result.diagnosis == _synthetic_request().diagnosis
    assert result.diagnostic_support is not None
    assert result.prescriptions == ()


def test_provider_failure_is_sanitized_and_distinct_from_invalid_output() -> None:
    private_value = "synthetic-secret-token-3951"
    provider = FakeGenerationProvider(
        error=ProviderExecutionError(
            f"request failed with {private_value} at C:\\Users\\synthetic-private"
        )
    )

    result = generate_prescription(_synthetic_request(), provider)

    assert result.status is GenerationStatus.PROVIDER_ERROR
    assert result.warnings[0].code == "provider_error"
    assert private_value not in repr(result)
    assert "synthetic-private" not in repr(result)


def test_invalid_port_response_is_sanitized_as_invalid_output() -> None:
    result = generate_prescription(
        _synthetic_request(),
        _InvalidPortResponseProvider(),
    )

    assert result.status is GenerationStatus.INVALID_OUTPUT
    assert "synthetic-private-response" not in repr(result)


def test_direct_validation_rejects_duplicate_json_keys_without_raw_content() -> None:
    private_value = "synthetic-private-duplicate-6824"
    output_text = (
        '{"schema_version":"prescriptive-generation.v1",'
        f'"schema_version":"{private_value}"}}'
    )

    with pytest.raises(InvalidProviderOutputError) as error_info:
        validate_provider_output(
            output_text,
            allowed_evidence_ids=frozenset({"synthetic-evidence-1"}),
            expected_fault_code="synthetic-bearing-fault",
        )

    assert private_value not in str(error_info.value)
    assert private_value not in repr(error_info.value)


def test_disabled_bedrock_never_constructs_a_client() -> None:
    requested_regions: list[str] = []

    def client_factory(region: str) -> BedrockRuntimeClient:
        requested_regions.append(region)
        return _RecordingBedrockClient(_bedrock_response(_valid_output_text()))

    provider = BedrockGenerationProvider(
        BedrockProviderConfig(),
        client_factory=client_factory,
    )

    result = generate_prescription(_synthetic_request(), provider)

    assert result.status is GenerationStatus.PROVIDER_DISABLED
    assert result.warnings[0].code == "provider_disabled"
    assert requested_regions == []


def test_bedrock_client_is_lazy_configurable_and_usage_is_allowlisted() -> None:
    private_metadata = "synthetic-private-request-metadata-7163"
    client = _RecordingBedrockClient(
        _bedrock_response(
            _valid_output_text(),
            usage={
                "inputTokens": 9,
                "outputTokens": 7,
                "totalTokens": 16,
                "private": private_metadata,
            },
        )
    )
    requested_regions: list[str] = []

    def client_factory(region: str) -> BedrockRuntimeClient:
        requested_regions.append(region)
        return client

    provider = BedrockGenerationProvider(
        BedrockProviderConfig(
            enabled=True,
            model_id="synthetic.model-v1",
            region="synthetic-region-1",
            max_tokens=512,
        ),
        client_factory=client_factory,
    )

    assert requested_regions == []
    assert client.calls == []

    result = generate_prescription(_synthetic_request(), provider)

    assert result.status is GenerationStatus.GENERATED
    assert requested_regions == ["synthetic-region-1"]
    assert len(client.calls) == 1
    assert client.calls[0]["modelId"] == "synthetic.model-v1"
    assert client.calls[0]["inferenceConfig"] == {
        "maxTokens": 512,
        "temperature": 0.0,
    }
    assert result.usage is not None
    assert result.usage.input_tokens == 9
    assert result.usage.output_tokens == 7
    assert result.usage.total_tokens == 16
    assert private_metadata not in repr(result)


def test_bedrock_drops_invalid_usage_metadata() -> None:
    client = _RecordingBedrockClient(
        _bedrock_response(
            _valid_output_text(),
            usage={
                "inputTokens": "synthetic-private-count",
                "outputTokens": True,
                "totalTokens": -1,
            },
        )
    )
    provider = BedrockGenerationProvider(
        BedrockProviderConfig(
            enabled=True,
            model_id="synthetic.model-v1",
            region="synthetic-region-1",
        ),
        client_factory=lambda _: client,
    )

    result = generate_prescription(_synthetic_request(), provider)

    assert result.status is GenerationStatus.GENERATED
    assert result.usage is None


def test_bedrock_execution_error_does_not_expose_provider_details() -> None:
    private_value = "synthetic-secret-provider-detail-4682"
    client = _FailingBedrockClient(
        f"{private_value} at C:\\Users\\synthetic-private\\credentials"
    )
    provider = BedrockGenerationProvider(
        BedrockProviderConfig(
            enabled=True,
            model_id="synthetic.model-v1",
            region="synthetic-region-1",
        ),
        client_factory=lambda _: client,
    )

    result = generate_prescription(_synthetic_request(), provider)

    assert result.status is GenerationStatus.PROVIDER_ERROR
    assert private_value not in repr(result)
    assert "credentials" not in repr(result)


def test_bedrock_invalid_envelope_is_classified_as_invalid_output() -> None:
    private_value = "synthetic-private-envelope-8246"
    client = _RecordingBedrockClient({"private": private_value})
    provider = BedrockGenerationProvider(
        BedrockProviderConfig(
            enabled=True,
            model_id="synthetic.model-v1",
            region="synthetic-region-1",
        ),
        client_factory=lambda _: client,
    )

    result = generate_prescription(_synthetic_request(), provider)

    assert result.status is GenerationStatus.INVALID_OUTPUT
    assert private_value not in repr(result)


def test_bedrock_non_mapping_envelope_is_classified_as_invalid_output() -> None:
    class NonMappingBedrockClient:
        def converse(self, **kwargs: object) -> object:
            del kwargs
            return "synthetic-private-invalid-envelope"

    provider = BedrockGenerationProvider(
        BedrockProviderConfig(
            enabled=True,
            model_id="synthetic.model-v1",
            region="synthetic-region-1",
        ),
        client_factory=lambda _: NonMappingBedrockClient(),
    )

    result = generate_prescription(_synthetic_request(), provider)

    assert result.status is GenerationStatus.INVALID_OUTPUT
    assert "synthetic-private-invalid-envelope" not in repr(result)


def test_enabled_bedrock_configuration_rejects_missing_or_unsafe_values() -> None:
    with pytest.raises(ValueError, match="requires a model id"):
        BedrockProviderConfig(enabled=True, region="synthetic-region-1")
    with pytest.raises(ValueError, match="requires a region"):
        BedrockProviderConfig(
            enabled=True,
            model_id="synthetic.model-v1",
            region="synthetic-region-1\nprivate",
        )
