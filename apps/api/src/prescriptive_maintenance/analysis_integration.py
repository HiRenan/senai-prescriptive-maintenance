"""Explicit, traceable composition for the API v1 analysis journey."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from math import isclose, isfinite
from threading import RLock
from typing import Final, Protocol, cast
from uuid import uuid4

from prescriptive_maintenance.contracts import (
    ANALYSIS_FEATURE_NAMES,
    AbstentionReason,
    AnalysisFeatures,
    AnalysisOutcome,
    AnalysisRequest,
    AnalysisResponse,
    AnalysisResult,
    AnalysisWarning,
    Citation,
    DegradedAnalysisResult,
    DependencyUnavailableAbstention,
    Diagnosis,
    DocumentedFaultAnalysisResult,
    InsufficientSupport,
    NormalAnalysisResult,
    OpaqueNeighbor,
    OutOfDistributionAbstention,
    OutOfDistributionAnalysisResult,
    Prescription,
    PrescriptionPriority,
    SufficientSupport,
    UndocumentedFaultAbstention,
    UndocumentedFaultAnalysisResult,
)
from prescriptive_maintenance.generation.contracts import GenerationStatus
from prescriptive_maintenance.generation.prompt import (
    GENERATION_SYSTEM_PROMPT_VERSION,
)
from prescriptive_maintenance.governed_retrieval import GovernedRetrievalStatus
from prescriptive_maintenance.modeling.similarity_index import (
    SimilarityIndexCompatibility,
    SimilarityIndexPort,
    SimilarityIndexSelector,
    SimilarityNeighbor,
    SimilarityQuery,
)
from prescriptive_maintenance.operations import current_correlation_id
from prescriptive_maintenance.persistence.models import (
    AnalysisMetadata,
    EvidenceReference,
)
from prescriptive_maintenance.persistence.ports import UnitOfWork
from prescriptive_maintenance.ports import (
    ModelAbstentionReason,
    ModelDisposition,
    ModelPort,
    ModelPrediction,
    PortUnavailableError,
)
from prescriptive_maintenance.prescription_orchestration import (
    MAX_PROVIDER_TIMEOUT_SECONDS,
    PrescriptionOrchestrationBinding,
    PrescriptionOrchestrationReason,
    PrescriptionOrchestrationResult,
    PrescriptionOrchestrationStatus,
)
from prescriptive_maintenance.services import (
    AnalysisNotFoundError,
    AnalysisUnavailableError,
)

ANALYSIS_AUTHORIZATION_SCHEMA_VERSION: Final = 1
PERSISTED_GENERATION_PROMPT_ID: Final = f"prompt_{GENERATION_SYSTEM_PROMPT_VERSION}"

_ANALYSIS_LOGGER: Final = logging.getLogger("prescriptive_maintenance.analysis")
_ANALYSIS_ID_PATTERN: Final = re.compile(r"ana_[a-z0-9_]{3,64}")
_CONFIGURATION_ID_PATTERN: Final = re.compile(r"config_[a-z0-9_.-]{3,64}")
_DATASET_ID_PATTERN: Final = re.compile(r"[0-9a-f]{64}")
_FAULT_CODE_PATTERN: Final = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)*")
_INDEX_ID_PATTERN: Final = re.compile(r"similarity_index_v1_[0-9a-f]{32}")
_MODEL_ID_PATTERN: Final = re.compile(r"model_[a-z0-9_.-]{3,64}")
_PROMPT_ID_PATTERN: Final = re.compile(r"prompt_[a-z0-9_.-]{3,64}")
_PROVIDER_ID_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_SHA256_PATTERN: Final = re.compile(r"[0-9a-f]{64}")
_VERSION_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SIMILARITY_DISTANCE_RELATIVE_TOLERANCE: Final = 1e-6
_SIMILARITY_DISTANCE_ABSOLUTE_TOLERANCE: Final = 1e-6


class AnalysisIntegrationConfigurationError(ValueError):
    """The explicit runtime binding is incomplete or inconsistent."""


@dataclass(frozen=True, slots=True)
class FaultPriorityRule:
    """One exact public diagnosis-to-priority projection."""

    fault_code: str
    priority: PrescriptionPriority

    def __post_init__(self) -> None:
        if (
            type(self.fault_code) is not str
            or _FAULT_CODE_PATTERN.fullmatch(self.fault_code) is None
            or type(self.priority) is not PrescriptionPriority
        ):
            raise AnalysisIntegrationConfigurationError(
                "Prescription priority rule is invalid."
            )


@dataclass(frozen=True, slots=True)
class PrescriptionProjectionPolicy:
    """Versioned, no-fallback mapping from fault code to public priority."""

    schema_version: int
    policy_version: str
    policy_sha256: str
    rules: tuple[FaultPriorityRule, ...]

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != 1
            or type(self.policy_version) is not str
            or _VERSION_PATTERN.fullmatch(self.policy_version) is None
            or type(self.policy_sha256) is not str
            or _SHA256_PATTERN.fullmatch(self.policy_sha256) is None
            or type(self.rules) is not tuple
        ):
            raise AnalysisIntegrationConfigurationError(
                "Prescription projection policy is invalid."
            )
        copied: list[FaultPriorityRule] = []
        for item in cast(tuple[object, ...], self.rules):
            if type(item) is not FaultPriorityRule:
                raise AnalysisIntegrationConfigurationError(
                    "Prescription projection policy is invalid."
                )
            copied.append(
                FaultPriorityRule(
                    fault_code=item.fault_code,
                    priority=item.priority,
                )
            )
        normalized = tuple(sorted(copied, key=lambda item: item.fault_code))
        if len({item.fault_code for item in normalized}) != len(normalized):
            raise AnalysisIntegrationConfigurationError(
                "Prescription projection policy contains duplicate fault codes."
            )
        object.__setattr__(self, "rules", normalized)
        if self.policy_sha256 != _projection_policy_sha256(self):
            raise AnalysisIntegrationConfigurationError(
                "Prescription projection policy hash does not match its semantics."
            )

    def priority_for(self, fault_code: str) -> PrescriptionPriority | None:
        """Return only an explicitly reviewed priority, never a fallback."""

        if type(fault_code) is not str:
            return None
        for rule in self.rules:
            if rule.fault_code == fault_code:
                return rule.priority
        return None


def build_prescription_projection_policy(
    *,
    policy_version: str,
    priorities: Mapping[str, PrescriptionPriority],
) -> PrescriptionProjectionPolicy:
    """Build and identify an explicit priority policy from basic values."""

    if type(priorities) is not dict:
        raise AnalysisIntegrationConfigurationError(
            "Prescription priorities must use an explicit dictionary."
        )
    raw_priorities = cast(dict[object, object], priorities)
    rules = tuple(
        FaultPriorityRule(
            fault_code=_base_text(fault_code),
            priority=cast(PrescriptionPriority, priority),
        )
        for fault_code, priority in raw_priorities.items()
    )
    clean_policy_version = _base_text(policy_version)
    normalized_rules = tuple(sorted(rules, key=lambda item: item.fault_code))
    return PrescriptionProjectionPolicy(
        schema_version=1,
        policy_version=clean_policy_version,
        policy_sha256=_projection_policy_identity(
            schema_version=1,
            policy_version=clean_policy_version,
            rules=normalized_rules,
        ),
        rules=normalized_rules,
    )


@dataclass(frozen=True, slots=True)
class AnalysisRuntimeAuthorization:
    """Approval bound to one exact model, index, retrieval, prompt and policy set."""

    schema_version: int
    authorization_version: str
    authorization_sha256: str
    dataset_id: str
    model_id: str
    index_id: str
    retrieval_policy_version: str
    retrieval_policy_sha256: str
    mapping_version: str
    mapping_sha256: str
    prompt_id: str
    provider_id: str
    provider_timeout_seconds: float
    projection_policy_version: str
    projection_policy_sha256: str

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != ANALYSIS_AUTHORIZATION_SCHEMA_VERSION
            or not _matches(self.authorization_version, _VERSION_PATTERN)
            or not _matches(self.authorization_sha256, _SHA256_PATTERN)
            or not _matches(self.dataset_id, _DATASET_ID_PATTERN)
            or not _matches(self.model_id, _MODEL_ID_PATTERN)
            or not _matches(self.index_id, _INDEX_ID_PATTERN)
            or not _matches(self.retrieval_policy_version, _VERSION_PATTERN)
            or not _matches(self.retrieval_policy_sha256, _SHA256_PATTERN)
            or not _matches(self.mapping_version, _VERSION_PATTERN)
            or not _matches(self.mapping_sha256, _SHA256_PATTERN)
            or not _matches(self.prompt_id, _PROMPT_ID_PATTERN)
            or not _matches(self.provider_id, _PROVIDER_ID_PATTERN)
            or type(self.provider_timeout_seconds) is not float
            or not isfinite(self.provider_timeout_seconds)
            or self.provider_timeout_seconds <= 0.0
            or self.provider_timeout_seconds > MAX_PROVIDER_TIMEOUT_SECONDS
            or not _matches(self.projection_policy_version, _VERSION_PATTERN)
            or not _matches(self.projection_policy_sha256, _SHA256_PATTERN)
        ):
            raise AnalysisIntegrationConfigurationError(
                "Analysis runtime authorization is invalid."
            )
        if self.authorization_sha256 != _authorization_sha256(self):
            raise AnalysisIntegrationConfigurationError(
                "Analysis authorization hash does not match its binding."
            )

    @property
    def configuration_id(self) -> str:
        """Return the persisted identity of the complete approved binding."""

        value = f"config_{self.authorization_sha256[:32]}"
        if _CONFIGURATION_ID_PATTERN.fullmatch(value) is None:
            raise AnalysisIntegrationConfigurationError(
                "Analysis configuration identifier is invalid."
            )
        return value


def build_analysis_runtime_authorization(
    *,
    authorization_version: str,
    dataset_id: str,
    model_id: str,
    index_id: str,
    retrieval_policy_version: str,
    retrieval_policy_sha256: str,
    mapping_version: str,
    mapping_sha256: str,
    prompt_id: str,
    provider_id: str,
    provider_timeout_seconds: float,
    projection_policy: PrescriptionProjectionPolicy,
) -> AnalysisRuntimeAuthorization:
    """Build one immutable allowlist entry for an explicitly approved set."""

    if type(projection_policy) is not PrescriptionProjectionPolicy:
        raise AnalysisIntegrationConfigurationError(
            "Prescription projection policy is required."
        )
    safe_policy = _copy_projection_policy(projection_policy)
    values = {
        "authorization_version": _base_text(authorization_version),
        "dataset_id": _base_text(dataset_id),
        "index_id": _base_text(index_id),
        "mapping_sha256": _base_text(mapping_sha256),
        "mapping_version": _base_text(mapping_version),
        "model_id": _base_text(model_id),
        "projection_policy_sha256": safe_policy.policy_sha256,
        "projection_policy_version": safe_policy.policy_version,
        "prompt_id": _base_text(prompt_id),
        "provider_id": _base_text(provider_id),
        "provider_timeout_seconds": provider_timeout_seconds,
        "retrieval_policy_sha256": _base_text(retrieval_policy_sha256),
        "retrieval_policy_version": _base_text(retrieval_policy_version),
    }
    return AnalysisRuntimeAuthorization(
        schema_version=ANALYSIS_AUTHORIZATION_SCHEMA_VERSION,
        authorization_version=cast(str, values["authorization_version"]),
        authorization_sha256=_authorization_identity(values),
        dataset_id=cast(str, values["dataset_id"]),
        model_id=cast(str, values["model_id"]),
        index_id=cast(str, values["index_id"]),
        retrieval_policy_version=cast(
            str,
            values["retrieval_policy_version"],
        ),
        retrieval_policy_sha256=cast(
            str,
            values["retrieval_policy_sha256"],
        ),
        mapping_version=cast(str, values["mapping_version"]),
        mapping_sha256=cast(str, values["mapping_sha256"]),
        prompt_id=cast(str, values["prompt_id"]),
        provider_id=cast(str, values["provider_id"]),
        provider_timeout_seconds=cast(float, values["provider_timeout_seconds"]),
        projection_policy_version=cast(
            str,
            values["projection_policy_version"],
        ),
        projection_policy_sha256=cast(
            str,
            values["projection_policy_sha256"],
        ),
    )


class TraceableModelPort(ModelPort, Protocol):
    """Model port whose complete similarity binding can be checked eagerly."""

    @property
    def dataset_id(self) -> str: ...

    @property
    def model_id(self) -> str: ...

    @property
    def index_id(self) -> str: ...


class PrescriptionOrchestrationPort(Protocol):
    """Stable orchestration boundary consumed by the integrated use case."""

    @property
    def runtime_binding(self) -> PrescriptionOrchestrationBinding: ...

    def orchestrate(
        self,
        prediction: object,
        *,
        top_k: object,
    ) -> PrescriptionOrchestrationResult: ...


class UnitOfWorkFactory(Protocol):
    """Create a fresh transaction for each analysis."""

    def __call__(self) -> UnitOfWork: ...


class UtcClock(Protocol):
    def __call__(self) -> datetime: ...


class AnalysisIdFactory(Protocol):
    def __call__(self) -> str: ...


class SimilarityCheckedModelPort:
    """Use SEN-52 as an exact identity/ranking check around one model decision."""

    def __init__(
        self,
        *,
        model: ModelPort,
        similarity: SimilarityIndexPort,
        selector: SimilarityIndexSelector,
        authorization: AnalysisRuntimeAuthorization,
    ) -> None:
        if type(selector) is not SimilarityIndexSelector:
            raise AnalysisIntegrationConfigurationError(
                "Similarity selector is invalid."
            )
        if type(authorization) is not AnalysisRuntimeAuthorization:
            raise AnalysisIntegrationConfigurationError(
                "Analysis authorization is required."
            )
        safe_authorization = _copy_authorization(authorization)
        safe_selector = _copy_selector(selector)
        if (
            safe_selector.compatibility.dataset_id != safe_authorization.dataset_id
            or safe_selector.model_id != safe_authorization.model_id
            or safe_selector.index_id != safe_authorization.index_id
        ):
            raise AnalysisIntegrationConfigurationError(
                "Similarity selector is not authorized for this runtime."
            )
        self._model = model
        self._similarity = similarity
        self._selector = safe_selector
        self._authorization = safe_authorization

    @property
    def dataset_id(self) -> str:
        return self._authorization.dataset_id

    @property
    def model_id(self) -> str:
        return self._authorization.model_id

    @property
    def index_id(self) -> str:
        return self._authorization.index_id

    def predict(
        self,
        features: AnalysisFeatures,
        *,
        top_k: int,
    ) -> ModelPrediction:
        """Require model and versioned index to identify the same neighbors."""

        try:
            raw_prediction = cast(
                object,
                self._model.predict(features, top_k=top_k),
            )
            prediction = _copy_prediction(raw_prediction)
            if prediction is None or prediction.model_id != self.model_id:
                raise ValueError
            raw_neighbors = cast(
                object,
                self._similarity.query(
                    SimilarityQuery(
                        selector=self._selector,
                        features=tuple(
                            getattr(features, name) for name in ANALYSIS_FEATURE_NAMES
                        ),
                        top_k=top_k,
                    )
                ),
            )
            neighbors = _copy_similarity_neighbors(raw_neighbors)
            if neighbors is None or not _rankings_match(
                prediction.neighbors,
                neighbors,
            ):
                raise ValueError
        except PortUnavailableError:
            raise
        except Exception:
            raise PortUnavailableError(
                "Authorized model and similarity index are unavailable."
            ) from None

        return ModelPrediction(
            disposition=prediction.disposition,
            abstention_reason=prediction.abstention_reason,
            diagnosis=prediction.diagnosis,
            support_score=prediction.support_score,
            model_id=prediction.model_id,
            neighbors=prediction.neighbors,
            retrieval_key=prediction.retrieval_key,
        )


class IntegratedAnalysisService:
    """Compose authorized diagnosis, governed RAG and metadata persistence."""

    def __init__(
        self,
        *,
        model: TraceableModelPort,
        orchestration: PrescriptionOrchestrationPort,
        authorization: AnalysisRuntimeAuthorization,
        projection_policy: PrescriptionProjectionPolicy,
        unit_of_work_factory: UnitOfWorkFactory,
        clock: UtcClock = lambda: datetime.now(UTC),
        analysis_id_factory: AnalysisIdFactory = lambda: f"ana_{uuid4().hex}",
    ) -> None:
        if type(authorization) is not AnalysisRuntimeAuthorization:
            raise AnalysisIntegrationConfigurationError(
                "Analysis authorization is required."
            )
        if type(projection_policy) is not PrescriptionProjectionPolicy:
            raise AnalysisIntegrationConfigurationError(
                "Prescription projection policy is required."
            )
        safe_authorization = _copy_authorization(authorization)
        safe_projection = _copy_projection_policy(projection_policy)
        try:
            binding = (model.dataset_id, model.model_id, model.index_id)
        except Exception:
            raise AnalysisIntegrationConfigurationError(
                "Traceable model binding is unavailable."
            ) from None
        try:
            raw_orchestration_binding = orchestration.runtime_binding
            if type(raw_orchestration_binding) is not PrescriptionOrchestrationBinding:
                raise TypeError
            orchestration_binding = PrescriptionOrchestrationBinding(
                prompt_id=raw_orchestration_binding.prompt_id,
                provider_id=raw_orchestration_binding.provider_id,
                provider_timeout_seconds=(
                    raw_orchestration_binding.provider_timeout_seconds
                ),
                retrieval_policy_version=(
                    raw_orchestration_binding.retrieval_policy_version
                ),
                retrieval_policy_sha256=(
                    raw_orchestration_binding.retrieval_policy_sha256
                ),
                mapping_version=raw_orchestration_binding.mapping_version,
                mapping_sha256=raw_orchestration_binding.mapping_sha256,
            )
        except Exception:
            raise AnalysisIntegrationConfigurationError(
                "Prescription orchestration binding is unavailable."
            ) from None
        if binding != (
            safe_authorization.dataset_id,
            safe_authorization.model_id,
            safe_authorization.index_id,
        ) or (
            safe_projection.policy_version
            != safe_authorization.projection_policy_version
            or safe_projection.policy_sha256
            != safe_authorization.projection_policy_sha256
        ):
            raise AnalysisIntegrationConfigurationError(
                "Analysis dependencies do not match the authorization."
            )
        if (
            orchestration_binding.prompt_id != safe_authorization.prompt_id
            or orchestration_binding.provider_id != safe_authorization.provider_id
            or orchestration_binding.provider_timeout_seconds
            != safe_authorization.provider_timeout_seconds
            or orchestration_binding.retrieval_policy_version
            != safe_authorization.retrieval_policy_version
            or orchestration_binding.retrieval_policy_sha256
            != safe_authorization.retrieval_policy_sha256
            or orchestration_binding.mapping_version
            != safe_authorization.mapping_version
            or orchestration_binding.mapping_sha256 != safe_authorization.mapping_sha256
        ):
            raise AnalysisIntegrationConfigurationError(
                "Prescription orchestration is not authorized for this runtime."
            )
        if (
            not callable(unit_of_work_factory)
            or not callable(clock)
            or not callable(analysis_id_factory)
        ):
            raise AnalysisIntegrationConfigurationError(
                "Analysis runtime factories are invalid."
            )

        self._model = model
        self._orchestration = orchestration
        self._authorization = safe_authorization
        self._projection_policy = safe_projection
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._analysis_id_factory = analysis_id_factory
        self._results: dict[str, AnalysisResponse] = {}
        self._lock = RLock()

    def analyze(self, request: AnalysisRequest) -> AnalysisResponse:
        correlation_id = current_correlation_id()
        analysis_id = self._new_analysis_id(correlation_id)
        try:
            prediction = self._model.predict(
                request.features,
                top_k=request.top_k,
            )
        except Exception:
            _log_failure(correlation_id, analysis_id, stage="model")
            raise AnalysisUnavailableError(
                "The authorized analysis model is unavailable."
            ) from None
        if prediction.model_id != self._authorization.model_id:
            _log_failure(correlation_id, analysis_id, stage="authorization")
            raise AnalysisUnavailableError(
                "The analysis runtime authorization does not match."
            )
        _log_stage(
            correlation_id,
            analysis_id,
            event="analysis_model_completed",
            fields={
                "dataset_id": self._authorization.dataset_id,
                "index_id": self._authorization.index_id,
                "model_id": prediction.model_id,
            },
        )

        try:
            raw_orchestration = cast(
                object,
                self._orchestration.orchestrate(
                    prediction,
                    top_k=request.top_k,
                ),
            )
            orchestration = _copy_orchestration_result(raw_orchestration)
            if orchestration is None:
                raise ValueError
            self._validate_orchestration_binding(orchestration)
        except Exception:
            _log_failure(correlation_id, analysis_id, stage="orchestration")
            raise AnalysisUnavailableError(
                "The prescriptive orchestration is unavailable."
            ) from None
        _log_stage(
            correlation_id,
            analysis_id,
            event="analysis_orchestration_completed",
            fields={
                "reason": (
                    orchestration.notice.code.value
                    if orchestration.notice is not None
                    else "generated"
                ),
                "status": orchestration.status.value,
            },
        )

        try:
            result = _project_result(
                analysis_id=analysis_id,
                orchestration=orchestration,
                projection_policy=self._projection_policy,
            )
            response = AnalysisResponse(root=result)
            stored = _copy_analysis_response(response)
            outbound = _copy_analysis_response(stored)
        except Exception:
            _log_failure(correlation_id, analysis_id, stage="projection")
            raise AnalysisUnavailableError(
                "The analysis result could not be projected safely."
            ) from None

        try:
            metadata = self._metadata(stored, orchestration)
        except Exception:
            _log_failure(correlation_id, analysis_id, stage="metadata")
            raise AnalysisUnavailableError(
                "The analysis traceability could not be prepared safely."
            ) from None

        try:
            self._persist(metadata)
        except Exception:
            _log_failure(correlation_id, analysis_id, stage="persistence")
            raise AnalysisUnavailableError(
                "The analysis could not be persisted safely."
            ) from None

        cache_published = False
        try:
            with self._lock:
                self._results[analysis_id] = stored
            cache_published = True
        except Exception:
            _log_stage(
                correlation_id,
                analysis_id,
                event="analysis_cache_unavailable",
                fields={"stage": "cache"},
            )
        _log_stage(
            correlation_id,
            analysis_id,
            event="analysis_completed",
            fields={
                "cache_published": cache_published,
                "chunk_refs": [item.chunk_ref for item in metadata.evidence_references],
                "configuration_id": metadata.configuration_id,
                "document_version_ids": list(metadata.document_version_ids),
                "model_id": metadata.model_id,
                "outcome": metadata.outcome.value,
                "prompt_id": metadata.prompt_id,
            },
        )
        return outbound

    def get(self, analysis_id: str) -> AnalysisResponse:
        with self._lock:
            response = self._results.get(analysis_id)
        if response is None:
            raise AnalysisNotFoundError("Analysis was not found.")
        return _copy_analysis_response(response)

    def _new_analysis_id(self, correlation_id: str | None) -> str:
        try:
            value = self._analysis_id_factory()
        except Exception:
            _log_failure(correlation_id, None, stage="analysis_id")
            raise AnalysisUnavailableError(
                "The analysis identifier could not be created."
            ) from None
        if type(value) is not str or _ANALYSIS_ID_PATTERN.fullmatch(value) is None:
            _log_failure(correlation_id, None, stage="analysis_id")
            raise AnalysisUnavailableError(
                "The analysis identifier could not be created."
            )
        return value

    def _validate_orchestration_binding(
        self,
        result: PrescriptionOrchestrationResult,
    ) -> None:
        if result.model_id != self._authorization.model_id:
            raise AnalysisIntegrationConfigurationError(
                "Orchestration model identity is not authorized."
            )
        metadata = result.metadata
        if metadata is not None and (
            f"prompt_{metadata.prompt_id}" != self._authorization.prompt_id
            or metadata.provider_id != self._authorization.provider_id
        ):
            raise AnalysisIntegrationConfigurationError(
                "Generation metadata is not authorized."
            )
        trace = result.retrieval_trace
        if trace is None:
            return
        if (
            trace.policy_version != self._authorization.retrieval_policy_version
            or trace.policy_sha256 != self._authorization.retrieval_policy_sha256
        ):
            raise AnalysisIntegrationConfigurationError(
                "Retrieval policy is not authorized."
            )
        if trace.mapping_version is not None and (
            trace.mapping_version != self._authorization.mapping_version
            or trace.mapping_sha256 != self._authorization.mapping_sha256
        ):
            raise AnalysisIntegrationConfigurationError(
                "Knowledge mapping is not authorized."
            )
        if (
            trace.status is GovernedRetrievalStatus.EVIDENCE
            and trace.mapping_version is None
        ):
            raise AnalysisIntegrationConfigurationError(
                "Evidence retrieval has no authorized mapping."
            )

    def _metadata(
        self,
        response: AnalysisResponse,
        orchestration: PrescriptionOrchestrationResult,
    ) -> AnalysisMetadata:
        try:
            instant = self._clock()
        except Exception:
            raise AnalysisUnavailableError("Analysis clock is unavailable.") from None
        if type(instant) is not datetime or instant.utcoffset() is None:
            raise AnalysisUnavailableError("Analysis clock is unavailable.")
        trace = orchestration.retrieval_trace
        references = () if trace is None else trace.evidence
        return AnalysisMetadata(
            analysis_id=response.root.analysis_id,
            outcome=AnalysisOutcome(response.root.outcome.value),
            dataset_id=self._authorization.dataset_id,
            model_id=response.root.model_id,
            prompt_id=self._authorization.prompt_id,
            configuration_id=self._authorization.configuration_id,
            created_at=instant,
            index_id=self._authorization.index_id,
            neighbor_refs=tuple(
                neighbor.neighbor_ref for neighbor in response.root.neighbors
            ),
            evidence_references=tuple(
                EvidenceReference(
                    evidence_id=item.chunk_ref,
                    document_id=item.document_id,
                    document_version_id=item.document_version_id,
                    chunk_ref=item.chunk_ref,
                    ordinal=item.rank,
                )
                for item in references
            ),
        )

    def _persist(self, metadata: AnalysisMetadata) -> None:
        try:
            unit_of_work = self._unit_of_work_factory()
            with unit_of_work as transaction:
                transaction.analyses.add(metadata)
                transaction.commit()
        except Exception:
            raise AnalysisUnavailableError(
                "Analysis persistence is unavailable."
            ) from None


def _project_result(
    *,
    analysis_id: str,
    orchestration: PrescriptionOrchestrationResult,
    projection_policy: PrescriptionProjectionPolicy,
) -> AnalysisResult:
    diagnosis = orchestration.diagnosis
    support_score = orchestration.support_score
    model_id = orchestration.model_id
    neighbors = orchestration.neighbors
    if support_score is None or model_id is None or type(neighbors) is not tuple:
        raise AnalysisUnavailableError("Analysis model context is incomplete.")

    reason = orchestration.notice.code if orchestration.notice is not None else None
    if (
        orchestration.status is PrescriptionOrchestrationStatus.SKIPPED
        and reason is PrescriptionOrchestrationReason.NORMAL
        and diagnosis is not None
    ):
        return NormalAnalysisResult(
            analysis_id=analysis_id,
            outcome=AnalysisOutcome.NORMAL,
            diagnosis=diagnosis,
            support=SufficientSupport(
                level="sufficient",
                support_score=support_score,
            ),
            abstention=None,
            model_id=model_id,
            neighbors=neighbors,
            prescription=None,
            citations=(),
            warnings=(),
        )
    if (
        orchestration.status is PrescriptionOrchestrationStatus.SKIPPED
        and reason is PrescriptionOrchestrationReason.OUT_OF_DISTRIBUTION
        and diagnosis is None
    ):
        return OutOfDistributionAnalysisResult(
            analysis_id=analysis_id,
            outcome=AnalysisOutcome.OUT_OF_DISTRIBUTION,
            diagnosis=None,
            support=InsufficientSupport(
                level="insufficient",
                support_score=support_score,
            ),
            abstention=OutOfDistributionAbstention(
                reason=AbstentionReason.OUT_OF_DISTRIBUTION,
                message="O modelo se absteve de emitir um diagnóstico suportado.",
            ),
            model_id=model_id,
            neighbors=neighbors,
            prescription=None,
            citations=(),
            warnings=(
                AnalysisWarning(
                    code="out_of_distribution",
                    message="Nenhuma prescrição foi produzida.",
                ),
            ),
        )
    if (
        orchestration.status is PrescriptionOrchestrationStatus.SKIPPED
        and reason
        in {
            PrescriptionOrchestrationReason.UNDOCUMENTED_FAULT,
            PrescriptionOrchestrationReason.NO_EVIDENCE,
            PrescriptionOrchestrationReason.UNMAPPED_FAULT,
        }
        and diagnosis is not None
        and neighbors
    ):
        return UndocumentedFaultAnalysisResult(
            analysis_id=analysis_id,
            outcome=AnalysisOutcome.UNDOCUMENTED_FAULT,
            diagnosis=diagnosis,
            support=SufficientSupport(
                level="sufficient",
                support_score=support_score,
            ),
            abstention=UndocumentedFaultAbstention(
                reason=AbstentionReason.UNDOCUMENTED_FAULT,
                message="Não há documentação aprovada suficiente para prescrever.",
            ),
            model_id=model_id,
            neighbors=neighbors,
            prescription=None,
            citations=(),
            warnings=(
                AnalysisWarning(
                    code="documentation_not_found",
                    message="O diagnóstico não possui suporte documental elegível.",
                ),
            ),
        )
    if orchestration.status is PrescriptionOrchestrationStatus.GENERATED:
        prescription = _public_prescription(orchestration, projection_policy)
        citations = _generated_public_citations(orchestration)
        safe_citations = () if citations is None else citations
        if (
            prescription is not None
            and diagnosis is not None
            and neighbors
            and citations
        ):
            return DocumentedFaultAnalysisResult(
                analysis_id=analysis_id,
                outcome=AnalysisOutcome.DOCUMENTED_FAULT,
                diagnosis=diagnosis,
                support=SufficientSupport(
                    level="sufficient",
                    support_score=support_score,
                ),
                abstention=None,
                model_id=model_id,
                neighbors=neighbors,
                prescription=prescription,
                citations=safe_citations,
                warnings=(),
            )
        return _degraded_result(
            analysis_id=analysis_id,
            diagnosis=diagnosis,
            support_score=support_score,
            model_id=model_id,
            neighbors=neighbors,
            citations=safe_citations,
            warning_code="prescription_projection_unavailable",
        )
    return _degraded_result(
        analysis_id=analysis_id,
        diagnosis=diagnosis,
        support_score=support_score,
        model_id=model_id,
        neighbors=neighbors,
        citations=(),
        warning_code=("dependency_unavailable" if reason is None else reason.value),
    )


def _degraded_result(
    *,
    analysis_id: str,
    diagnosis: Diagnosis | None,
    support_score: float,
    model_id: str,
    neighbors: tuple[OpaqueNeighbor, ...],
    citations: tuple[Citation, ...],
    warning_code: str,
) -> DegradedAnalysisResult:
    if diagnosis is None:
        raise AnalysisUnavailableError("Degraded analysis has no diagnosis.")
    return DegradedAnalysisResult(
        analysis_id=analysis_id,
        outcome=AnalysisOutcome.DEGRADED,
        diagnosis=diagnosis,
        support=SufficientSupport(
            level="sufficient",
            support_score=support_score,
        ),
        abstention=DependencyUnavailableAbstention(
            reason=AbstentionReason.DEPENDENCY_UNAVAILABLE,
            message="A análise parcial não permite uma prescrição segura.",
        ),
        model_id=model_id,
        neighbors=neighbors,
        prescription=None,
        citations=citations,
        warnings=(
            AnalysisWarning(
                code=warning_code,
                message="Uma dependência opcional não produziu resultado seguro.",
            ),
        ),
    )


def _public_prescription(
    orchestration: PrescriptionOrchestrationResult,
    policy: PrescriptionProjectionPolicy,
) -> Prescription | None:
    try:
        diagnosis = orchestration.diagnosis
        guardrail = orchestration.guardrail
        if diagnosis is None or guardrail is None or guardrail.generation is None:
            return None
        generated = guardrail.generation
        if generated.status is not GenerationStatus.GENERATED:
            return None
        diagnostic_support = generated.diagnostic_support
        if diagnostic_support is None or diagnostic_support.assessment is None:
            return None
        priority = policy.priority_for(diagnosis.code)
        if priority is None:
            return None
        actions = tuple(item.action for item in generated.prescriptions)
        return Prescription(
            summary=diagnostic_support.assessment,
            priority=priority,
            actions=actions,
        )
    except Exception:
        return None


def _generated_public_citations(
    orchestration: PrescriptionOrchestrationResult,
) -> tuple[Citation, ...] | None:
    """Project only evidence identifiers cited by one accepted generation."""

    trace = orchestration.retrieval_trace
    guardrail = orchestration.guardrail
    if (
        trace is None
        or trace.status is not GovernedRetrievalStatus.EVIDENCE
        or guardrail is None
        or guardrail.generation is None
        or guardrail.generation.status is not GenerationStatus.GENERATED
        or guardrail.generation.diagnostic_support is None
    ):
        return None
    generated = guardrail.generation
    diagnostic_support = generated.diagnostic_support
    if diagnostic_support is None:
        return None
    cited_ids = {citation.evidence_id for citation in diagnostic_support.citations}
    for prescription in generated.prescriptions:
        cited_ids.update(citation.evidence_id for citation in prescription.citations)
    evidence_by_id = {item.chunk_ref: item for item in trace.evidence}
    if not cited_ids or not cited_ids.issubset(evidence_by_id):
        return None
    return tuple(
        Citation(
            document_id=item.document_id,
            document_version=item.document_version_id,
            chunk=item.chunk_ref,
            page_number=item.page_number,
        )
        for item in trace.evidence
        if item.chunk_ref in cited_ids
    )


def _copy_prediction(value: object) -> ModelPrediction | None:
    try:
        if type(value) is not ModelPrediction:
            return None
        if (
            type(value.disposition) is not ModelDisposition
            or (
                value.abstention_reason is not None
                and type(value.abstention_reason) is not ModelAbstentionReason
            )
            or type(value.support_score) is not float
            or not isfinite(value.support_score)
            or not 0.0 <= value.support_score <= 1.0
            or not _matches(value.model_id, _MODEL_ID_PATTERN)
            or type(value.neighbors) is not tuple
        ):
            return None
        diagnosis = (
            None
            if value.diagnosis is None
            else Diagnosis(
                code=value.diagnosis.code,
                summary=value.diagnosis.summary,
            )
        )
        neighbors = tuple(
            OpaqueNeighbor(
                neighbor_ref=item.neighbor_ref,
                rank=item.rank,
                fault_code=item.fault_code,
                distance=item.distance,
            )
            for item in value.neighbors
            if type(item) is OpaqueNeighbor
        )
        if len(neighbors) != len(value.neighbors):
            return None
        retrieval_key = value.retrieval_key
        if retrieval_key is not None and type(retrieval_key) is not str:
            return None
        return ModelPrediction(
            disposition=value.disposition,
            abstention_reason=value.abstention_reason,
            diagnosis=diagnosis,
            support_score=value.support_score,
            model_id=_base_text(value.model_id),
            neighbors=neighbors,
            retrieval_key=(
                None if retrieval_key is None else _base_text(retrieval_key)
            ),
        )
    except Exception:
        return None


def _copy_similarity_neighbors(
    value: object,
) -> tuple[SimilarityNeighbor, ...] | None:
    try:
        if type(value) is not tuple:
            return None
        items = cast(tuple[object, ...], value)
        copied = tuple(
            SimilarityNeighbor(
                opaque_id=item.opaque_id,
                rank=item.rank,
                fault_code=item.fault_code,
                distance=item.distance,
            )
            for item in items
            if type(item) is SimilarityNeighbor
        )
        return copied if len(copied) == len(items) else None
    except Exception:
        return None


def _rankings_match(
    model_neighbors: tuple[OpaqueNeighbor, ...],
    similarity_neighbors: tuple[SimilarityNeighbor, ...],
) -> bool:
    return len(model_neighbors) == len(similarity_neighbors) and all(
        model.neighbor_ref == indexed.opaque_id
        and model.rank == indexed.rank
        and model.fault_code == indexed.fault_code
        and isfinite(model.distance)
        and isfinite(indexed.distance)
        and isclose(
            model.distance,
            indexed.distance,
            rel_tol=_SIMILARITY_DISTANCE_RELATIVE_TOLERANCE,
            abs_tol=_SIMILARITY_DISTANCE_ABSOLUTE_TOLERANCE,
        )
        for model, indexed in zip(
            model_neighbors,
            similarity_neighbors,
            strict=True,
        )
    )


def _copy_orchestration_result(
    value: object,
) -> PrescriptionOrchestrationResult | None:
    try:
        if type(value) is not PrescriptionOrchestrationResult:
            return None
        return PrescriptionOrchestrationResult(
            status=value.status,
            disposition=value.disposition,
            diagnosis=value.diagnosis,
            support_score=value.support_score,
            model_id=value.model_id,
            neighbors=value.neighbors,
            guardrail=value.guardrail,
            metadata=value.metadata,
            notice=value.notice,
            retrieval_trace=value.retrieval_trace,
        )
    except Exception:
        return None


def _copy_analysis_response(response: AnalysisResponse) -> AnalysisResponse:
    return AnalysisResponse.model_validate_json(response.model_dump_json())


def _copy_projection_policy(
    policy: PrescriptionProjectionPolicy,
) -> PrescriptionProjectionPolicy:
    return PrescriptionProjectionPolicy(
        schema_version=policy.schema_version,
        policy_version=policy.policy_version,
        policy_sha256=policy.policy_sha256,
        rules=policy.rules,
    )


def _copy_authorization(
    authorization: AnalysisRuntimeAuthorization,
) -> AnalysisRuntimeAuthorization:
    return AnalysisRuntimeAuthorization(
        schema_version=authorization.schema_version,
        authorization_version=authorization.authorization_version,
        authorization_sha256=authorization.authorization_sha256,
        dataset_id=authorization.dataset_id,
        model_id=authorization.model_id,
        index_id=authorization.index_id,
        retrieval_policy_version=authorization.retrieval_policy_version,
        retrieval_policy_sha256=authorization.retrieval_policy_sha256,
        mapping_version=authorization.mapping_version,
        mapping_sha256=authorization.mapping_sha256,
        prompt_id=authorization.prompt_id,
        provider_id=authorization.provider_id,
        provider_timeout_seconds=authorization.provider_timeout_seconds,
        projection_policy_version=authorization.projection_policy_version,
        projection_policy_sha256=authorization.projection_policy_sha256,
    )


def _copy_selector(selector: SimilarityIndexSelector) -> SimilarityIndexSelector:
    compatibility = selector.compatibility
    return SimilarityIndexSelector(
        index_id=selector.index_id,
        model_id=selector.model_id,
        compatibility=SimilarityIndexCompatibility(
            dataset_id=compatibility.dataset_id,
            schema_id=compatibility.schema_id,
            feature_contract_version=compatibility.feature_contract_version,
            preprocessor_version=compatibility.preprocessor_version,
            index_version=compatibility.index_version,
            configuration_version=compatibility.configuration_version,
            dimension=compatibility.dimension,
            metric=compatibility.metric,
        ),
    )


def _projection_policy_sha256(policy: PrescriptionProjectionPolicy) -> str:
    return _projection_policy_identity(
        schema_version=policy.schema_version,
        policy_version=policy.policy_version,
        rules=policy.rules,
    )


def _projection_policy_identity(
    *,
    schema_version: int,
    policy_version: str,
    rules: tuple[FaultPriorityRule, ...],
) -> str:
    return sha256(
        _canonical_json_bytes(
            {
                "policy_version": policy_version,
                "rules": [
                    {
                        "fault_code": item.fault_code,
                        "priority": item.priority.value,
                    }
                    for item in rules
                ],
                "schema_version": schema_version,
            }
        )
    ).hexdigest()


def _authorization_sha256(authorization: AnalysisRuntimeAuthorization) -> str:
    return _authorization_identity(
        {
            "authorization_version": authorization.authorization_version,
            "dataset_id": authorization.dataset_id,
            "index_id": authorization.index_id,
            "mapping_sha256": authorization.mapping_sha256,
            "mapping_version": authorization.mapping_version,
            "model_id": authorization.model_id,
            "projection_policy_sha256": authorization.projection_policy_sha256,
            "projection_policy_version": authorization.projection_policy_version,
            "prompt_id": authorization.prompt_id,
            "provider_id": authorization.provider_id,
            "provider_timeout_seconds": authorization.provider_timeout_seconds,
            "retrieval_policy_sha256": authorization.retrieval_policy_sha256,
            "retrieval_policy_version": authorization.retrieval_policy_version,
        }
    )


def _authorization_identity(values: Mapping[str, object]) -> str:
    timeout = values["provider_timeout_seconds"]
    if type(timeout) is not float:
        raise AnalysisIntegrationConfigurationError(
            "Provider timeout identity is invalid."
        )
    return sha256(
        _canonical_json_bytes(
            {
                "authorization_version": values["authorization_version"],
                "dataset_id": values["dataset_id"],
                "index_id": values["index_id"],
                "mapping_sha256": values["mapping_sha256"],
                "mapping_version": values["mapping_version"],
                "model_id": values["model_id"],
                "projection_policy_sha256": values["projection_policy_sha256"],
                "projection_policy_version": values["projection_policy_version"],
                "prompt_id": values["prompt_id"],
                "provider_id": values["provider_id"],
                "provider_timeout_seconds_hex": timeout.hex(),
                "retrieval_policy_sha256": values["retrieval_policy_sha256"],
                "retrieval_policy_version": values["retrieval_policy_version"],
                "schema_version": ANALYSIS_AUTHORIZATION_SCHEMA_VERSION,
            }
        )
    ).hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _base_text(value: object) -> str:
    if type(value) is not str:
        raise AnalysisIntegrationConfigurationError(
            "Analysis configuration text is invalid."
        )
    return str.__add__("", value)


def _matches(value: object, pattern: re.Pattern[str]) -> bool:
    return type(value) is str and pattern.fullmatch(value) is not None


def _log_failure(
    correlation_id: str | None,
    analysis_id: str | None,
    *,
    stage: str,
) -> None:
    fields: dict[str, object] = {"stage": stage}
    if analysis_id is not None:
        fields["analysis_id"] = analysis_id
    _log_stage(
        correlation_id,
        analysis_id,
        event="analysis_failed",
        fields=fields,
    )


def _log_stage(
    correlation_id: str | None,
    analysis_id: str | None,
    *,
    event: str,
    fields: Mapping[str, object],
) -> None:
    if correlation_id is None:
        return
    try:
        record: dict[str, object] = {
            "correlation_id": correlation_id,
            "event": event,
        }
        if analysis_id is not None:
            record["analysis_id"] = analysis_id
        record.update(fields)
        _ANALYSIS_LOGGER.info(
            json.dumps(
                record,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    except Exception:
        return
