"""Validate provider output at the generation/domain boundary."""

from __future__ import annotations

import json
from typing import Any, Final, Never, cast

from pydantic import ValidationError

from prescriptive_maintenance.generation.contracts import (
    GENERATION_CONTRACT_VERSION,
    Citation,
    Diagnosis,
    DiagnosticSupportStatus,
    GenerationRequest,
    GenerationResult,
    GenerationStatus,
    GenerationWarning,
    ProviderOutput,
    ProviderUsage,
)
from prescriptive_maintenance.generation.prompt import build_provider_request
from prescriptive_maintenance.generation.provider import (
    GenerationProvider,
    InvalidProviderOutputError,
    ProviderDisabledError,
    ProviderInvalidResponseError,
    ProviderResponse,
)

_MAX_PROVIDER_OUTPUT_CHARACTERS: Final = 64_000


def generate_prescription(
    request: GenerationRequest,
    provider: GenerationProvider,
) -> GenerationResult:
    """Run one explicit provider attempt and expose only sanitized outcomes."""

    if not request.evidence:
        return _failure_result(
            request.diagnosis,
            GenerationStatus.NO_EVIDENCE,
            code="no_evidence",
            message="No evidence was supplied; generation was not attempted.",
        )

    provider_request = build_provider_request(request)
    try:
        response = cast(object, provider.generate(provider_request))
    except ProviderDisabledError:
        return _failure_result(
            request.diagnosis,
            GenerationStatus.PROVIDER_DISABLED,
            code="provider_disabled",
            message="Generation provider is disabled.",
        )
    except ProviderInvalidResponseError:
        return _failure_result(
            request.diagnosis,
            GenerationStatus.INVALID_OUTPUT,
            code="invalid_output",
            message="Generation provider returned invalid output.",
        )
    except Exception:
        return _failure_result(
            request.diagnosis,
            GenerationStatus.PROVIDER_ERROR,
            code="provider_error",
            message="Generation provider failed.",
        )

    if type(response) is not ProviderResponse:
        return _failure_result(
            request.diagnosis,
            GenerationStatus.INVALID_OUTPUT,
            code="invalid_output",
            message="Generation provider returned invalid output.",
        )

    try:
        output = validate_provider_output(
            response.output_text,
            allowed_evidence_ids=frozenset(
                evidence.evidence_id for evidence in request.evidence
            ),
            expected_fault_code=request.diagnosis.fault_code,
        )
    except InvalidProviderOutputError:
        return _failure_result(
            request.diagnosis,
            GenerationStatus.INVALID_OUTPUT,
            code="invalid_output",
            message="Generation provider returned invalid output.",
        )

    status = (
        GenerationStatus.GENERATED
        if output.diagnostic_support.status is DiagnosticSupportStatus.SUPPORTED
        else GenerationStatus.INSUFFICIENT_EVIDENCE
    )
    return GenerationResult(
        schema_version=GENERATION_CONTRACT_VERSION,
        status=status,
        diagnosis=request.diagnosis,
        diagnostic_support=output.diagnostic_support,
        prescriptions=output.prescriptions,
        warnings=output.warnings,
        usage=_allowlisted_usage(response.usage),
    )


def validate_provider_output(
    output_text: object,
    *,
    allowed_evidence_ids: frozenset[str],
    expected_fault_code: str,
) -> ProviderOutput:
    """Validate strict JSON and citation references without exposing raw values."""

    if (
        type(output_text) is not str
        or not output_text.strip()
        or len(output_text) > _MAX_PROVIDER_OUTPUT_CHARACTERS
        or type(allowed_evidence_ids) is not frozenset
        or any(
            type(evidence_id) is not str
            for evidence_id in cast(frozenset[object], allowed_evidence_ids)
        )
        or type(expected_fault_code) is not str
    ):
        raise InvalidProviderOutputError(
            "Generation provider output did not match the contract."
        )
    try:
        json.loads(
            output_text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_json_number,
        )
        output = ProviderOutput.model_validate_json(output_text, strict=True)
    except (ValidationError, ValueError, TypeError, RecursionError):
        raise InvalidProviderOutputError(
            "Generation provider output did not match the contract."
        ) from None

    if output.diagnostic_support.fault_code != expected_fault_code:
        raise InvalidProviderOutputError(
            "Generation provider output did not match the contract."
        )

    citations = _all_citations(output)
    if any(citation.evidence_id not in allowed_evidence_ids for citation in citations):
        raise InvalidProviderOutputError(
            "Generation provider output did not match the contract."
        )
    return output


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for key, value in pairs:
        if key in parsed:
            raise ValueError("Duplicate JSON object key.")
        parsed[key] = value
    return parsed


def _reject_non_finite_json_number(_: str) -> Never:
    raise ValueError("Non-finite JSON number.")


def _all_citations(output: ProviderOutput) -> tuple[Citation, ...]:
    citations = list(output.diagnostic_support.citations)
    for prescription in output.prescriptions:
        citations.extend(prescription.citations)
    for warning in output.warnings:
        citations.extend(warning.citations)
    return tuple(citations)


def _allowlisted_usage(usage: object) -> ProviderUsage | None:
    try:
        if type(usage) is not ProviderUsage:
            return None
        return ProviderUsage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
        )
    except Exception:
        return None


def _failure_result(
    diagnosis: Diagnosis,
    status: GenerationStatus,
    *,
    code: str,
    message: str,
) -> GenerationResult:
    return GenerationResult(
        schema_version=GENERATION_CONTRACT_VERSION,
        status=status,
        diagnosis=diagnosis,
        warnings=(
            GenerationWarning(
                code=code,
                message=message,
                citations=(),
            ),
        ),
    )
