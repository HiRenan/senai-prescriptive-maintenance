"""Typed internal ports required by the analysis application service."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from prescriptive_maintenance.contracts import (
    AnalysisFeatures,
    Citation,
    Diagnosis,
    OpaqueNeighbor,
    Prescription,
)


class PortUnavailableError(Exception):
    """A sanitized signal that an internal port cannot serve the request."""


class ModelDisposition(StrEnum):
    NORMAL = "normal"
    FAULT = "fault"
    OUT_OF_DISTRIBUTION = "out_of_distribution"


@dataclass(frozen=True, slots=True)
class ModelPrediction:
    """Model disposition with non-calibrated support and opaque k-NN neighbors."""

    disposition: ModelDisposition
    diagnosis: Diagnosis | None
    support_score: float
    model_id: str
    neighbors: tuple[OpaqueNeighbor, ...]
    retrieval_key: str | None


@dataclass(frozen=True, slots=True)
class DocumentEvidence:
    """Governed documentary support and citations, never model neighbors."""

    support_score: float
    citations: tuple[Citation, ...]


class ModelPort(Protocol):
    """Infer a model-level disposition without transport concerns."""

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
