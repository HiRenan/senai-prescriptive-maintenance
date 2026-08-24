"""Typed internal ports required by the analysis application service."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast

from prescriptive_maintenance.contracts import (
    AnalysisFeatures,
    Citation,
    Diagnosis,
    OpaqueNeighbor,
    Prescription,
)


class PortUnavailableError(Exception):
    """A sanitized signal that an internal port cannot serve the request."""


class PortContractError(Exception):
    """A sanitized signal that an internal port returned unsafe evidence."""


def _validate_citations(value: object) -> None:
    # Pydantic subclasses may declare fields forbidden on the public base model.
    if not isinstance(value, tuple) or any(
        type(citation) is not Citation for citation in cast(tuple[object, ...], value)
    ):
        raise PortContractError("Retrieval evidence violates the internal contract.")


class ModelDisposition(StrEnum):
    NORMAL = "normal"
    FAULT = "fault"
    OUT_OF_DISTRIBUTION = "out_of_distribution"


class ModelAbstentionReason(StrEnum):
    """Stable model-level reasons that prevent a diagnostic decision."""

    DISTANCE_OUT_OF_DISTRIBUTION = "distance_out_of_distribution"
    INCONCLUSIVE_VOTE = "inconclusive_vote"
    RARE_CLASS_SUPPORT = "rare_class_support"


@dataclass(frozen=True, slots=True)
class ModelPrediction:
    """Probable condition with heuristic support and opaque similar histories."""

    disposition: ModelDisposition
    abstention_reason: ModelAbstentionReason | None
    diagnosis: Diagnosis | None
    support_score: float
    model_id: str
    neighbors: tuple[OpaqueNeighbor, ...]
    retrieval_key: str | None


@dataclass(frozen=True, slots=True)
class DocumentEvidence:
    """Governed structured citations, never paths, titles, text, or neighbors."""

    support_score: float
    citations: tuple[Citation, ...]

    def __post_init__(self) -> None:
        _validate_citations(self.citations)


class ModelPort(Protocol):
    """Retrieve a probable condition without transport concerns."""

    def predict(
        self,
        features: AnalysisFeatures,
        *,
        top_k: int,
    ) -> ModelPrediction: ...


class RetrievalPort(Protocol):
    """Retrieve bounded, governed documentary evidence for one diagnostic key."""

    def retrieve(self, retrieval_key: str, *, top_k: int) -> DocumentEvidence: ...


class GenerationPort(Protocol):
    """Generate a bounded prescription from public diagnostic evidence."""

    def generate(
        self,
        diagnosis: Diagnosis,
        evidence: DocumentEvidence,
    ) -> Prescription: ...
