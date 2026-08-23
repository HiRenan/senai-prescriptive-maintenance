"""Entirely synthetic persistence aggregates shared by adapter tests."""

from datetime import UTC, datetime
from typing import Final

from prescriptive_maintenance.domain import AnalysisOutcome
from prescriptive_maintenance.persistence import (
    AnalysisMetadata,
    ChunkReference,
    DocumentMetadata,
    DocumentVersionMetadata,
    EvidenceReference,
)

SYNTHETIC_TIME: Final = datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC)
SYNTHETIC_DATASET_ID: Final = "a" * 64

SYNTHETIC_DOCUMENT_VERSION_V1: Final = DocumentVersionMetadata(
    document_version_id="docver_synthetic_guide_v1",
    document_id="doc_synthetic_guide",
    source_sha256="1" * 64,
    created_at=SYNTHETIC_TIME,
    chunks=(
        ChunkReference(
            chunk_ref="chunk_synthetic_guide_v1_02",
            document_id="doc_synthetic_guide",
            document_version_id="docver_synthetic_guide_v1",
            page_number=2,
        ),
        ChunkReference(
            chunk_ref="chunk_synthetic_guide_v1_01",
            document_id="doc_synthetic_guide",
            document_version_id="docver_synthetic_guide_v1",
            page_number=1,
        ),
    ),
)
SYNTHETIC_DOCUMENT_VERSION_V2: Final = DocumentVersionMetadata(
    document_version_id="docver_synthetic_guide_v2",
    document_id="doc_synthetic_guide",
    source_sha256="2" * 64,
    created_at=SYNTHETIC_TIME,
    chunks=(
        ChunkReference(
            chunk_ref="chunk_synthetic_guide_v2_01",
            document_id="doc_synthetic_guide",
            document_version_id="docver_synthetic_guide_v2",
            page_number=3,
        ),
    ),
)
SYNTHETIC_INITIAL_DOCUMENT: Final = DocumentMetadata(
    document_id="doc_synthetic_guide",
    created_at=SYNTHETIC_TIME,
    versions=(SYNTHETIC_DOCUMENT_VERSION_V1,),
)
SYNTHETIC_DOCUMENT: Final = DocumentMetadata(
    document_id="doc_synthetic_guide",
    created_at=SYNTHETIC_TIME,
    versions=(
        SYNTHETIC_DOCUMENT_VERSION_V2,
        SYNTHETIC_DOCUMENT_VERSION_V1,
    ),
)

SYNTHETIC_ANALYSIS: Final = AnalysisMetadata(
    analysis_id="ana_synthetic_trace",
    outcome=AnalysisOutcome.DOCUMENTED_FAULT,
    dataset_id=SYNTHETIC_DATASET_ID,
    model_id="model_synthetic_v1",
    prompt_id="prompt_synthetic_v1",
    configuration_id="config_synthetic_v1",
    created_at=SYNTHETIC_TIME,
    evidence_references=(
        EvidenceReference(
            evidence_id="synthetic-evidence-v1-chunk-02",
            document_id="doc_synthetic_guide",
            document_version_id="docver_synthetic_guide_v1",
            chunk_ref="chunk_synthetic_guide_v1_02",
            ordinal=2,
        ),
        EvidenceReference(
            evidence_id="synthetic-evidence-v2-chunk-01",
            document_id="doc_synthetic_guide",
            document_version_id="docver_synthetic_guide_v2",
            chunk_ref="chunk_synthetic_guide_v2_01",
            ordinal=1,
        ),
    ),
)
