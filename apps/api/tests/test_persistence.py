"""Contract tests for minimal metadata and the in-memory transaction adapter."""

from dataclasses import dataclass, fields, replace
from datetime import timedelta
from typing import cast

import pytest
from persistence_samples import (
    SYNTHETIC_ANALYSIS,
    SYNTHETIC_DATASET_ID,
    SYNTHETIC_DOCUMENT,
    SYNTHETIC_DOCUMENT_VERSION_V2,
    SYNTHETIC_INITIAL_DOCUMENT,
    SYNTHETIC_INVALID_CIVIL_STATES,
    SYNTHETIC_UTC_OFFSETS,
    assert_ambiguous_zoneinfo_datetime_is_canonical,
    assert_lying_datetimes_are_canonical,
    assert_offset_datetime_is_canonical,
    assert_persisted_scalars_are_canonical,
    synthetic_ambiguous_zoneinfo_document,
    synthetic_invalid_civil_datetime_document,
    synthetic_lying_datetime_aggregates,
    synthetic_offset_datetime_document,
    synthetic_tainted_scalar_aggregates,
)
from prescriptive_maintenance.contracts import AnalysisOutcome as ApiAnalysisOutcome
from prescriptive_maintenance.domain import AnalysisOutcome
from prescriptive_maintenance.persistence import (
    AnalysisMetadata,
    ChunkReference,
    DocumentMetadata,
    DocumentVersionMetadata,
    EvidenceReference,
    InMemoryStore,
    InMemoryUnitOfWork,
    PersistenceConflictError,
    PersistenceIntegrityError,
    TransactionConflictError,
    UnitOfWork,
)

_FORBIDDEN_PAYLOAD = "synthetic raw payload that must never be persisted"


@dataclass(frozen=True, slots=True)
class _TaintedChunkReference(ChunkReference):
    raw_content: str = _FORBIDDEN_PAYLOAD


@dataclass(frozen=True, slots=True)
class _TaintedDocumentVersion(DocumentVersionMetadata):
    raw_content: str = _FORBIDDEN_PAYLOAD


@dataclass(frozen=True, slots=True)
class _TaintedDocument(DocumentMetadata):
    raw_content: str = _FORBIDDEN_PAYLOAD


@dataclass(frozen=True, slots=True)
class _TaintedEvidenceReference(EvidenceReference):
    raw_content: str = _FORBIDDEN_PAYLOAD


@dataclass(frozen=True, slots=True)
class _TaintedAnalysis(AnalysisMetadata):
    raw_content: str = _FORBIDDEN_PAYLOAD


def _accepts_unit_of_work(unit_of_work: UnitOfWork) -> UnitOfWork:
    return unit_of_work


def test_metadata_shape_excludes_private_and_raw_payload_fields() -> None:
    persisted_shape = {
        model.__name__: {field.name for field in fields(model)}
        for model in (
            AnalysisMetadata,
            DocumentMetadata,
            DocumentVersionMetadata,
            ChunkReference,
            EvidenceReference,
        )
    }
    assert persisted_shape == {
        "AnalysisMetadata": {
            "analysis_id",
            "outcome",
            "dataset_id",
            "model_id",
            "prompt_id",
            "configuration_id",
            "created_at",
            "evidence_references",
            "index_id",
            "neighbor_refs",
        },
        "DocumentMetadata": {"document_id", "created_at", "versions"},
        "DocumentVersionMetadata": {
            "document_version_id",
            "document_id",
            "source_sha256",
            "created_at",
            "chunks",
        },
        "ChunkReference": {
            "chunk_ref",
            "document_id",
            "document_version_id",
            "page_number",
        },
        "EvidenceReference": {
            "evidence_id",
            "document_id",
            "document_version_id",
            "chunk_ref",
            "ordinal",
        },
    }
    persisted_fields = {
        field_name
        for model_fields in persisted_shape.values()
        for field_name in model_fields
    }

    assert (
        not {
            "features",
            "feature_vector",
            "embedding",
            "content",
            "raw_content",
            "source_path",
            "filename",
            "diagnosis",
            "prescription",
        }
        & persisted_fields
    )


def test_metadata_is_canonical_and_exposes_every_used_version() -> None:
    assert SYNTHETIC_ANALYSIS.dataset_id == SYNTHETIC_DATASET_ID == "a" * 64
    assert SYNTHETIC_ANALYSIS.evidence_references[0].evidence_id == (
        "synthetic-evidence-v2-chunk-01"
    )
    assert tuple(
        version.document_version_id for version in SYNTHETIC_DOCUMENT.versions
    ) == (
        "docver_synthetic_guide_v1",
        "docver_synthetic_guide_v2",
    )
    assert tuple(
        chunk.chunk_ref for chunk in SYNTHETIC_DOCUMENT.versions[0].chunks
    ) == (
        "chunk_synthetic_guide_v1_01",
        "chunk_synthetic_guide_v1_02",
    )
    assert SYNTHETIC_ANALYSIS.document_version_ids == (
        "docver_synthetic_guide_v2",
        "docver_synthetic_guide_v1",
    )


def test_analysis_rejects_noncanonical_dataset_and_duplicate_local_evidence() -> None:
    with pytest.raises(ValueError, match="dataset_id"):
        replace(SYNTHETIC_ANALYSIS, dataset_id="dataset_synthetic_v1")

    first, second = SYNTHETIC_ANALYSIS.evidence_references
    with pytest.raises(ValueError, match="unique within an analysis"):
        replace(
            SYNTHETIC_ANALYSIS,
            evidence_references=(
                first,
                replace(second, evidence_id=first.evidence_id),
            ),
        )


def test_analysis_rejects_invalid_or_unbound_neighbor_references() -> None:
    with pytest.raises(ValueError, match="require one similarity index"):
        replace(SYNTHETIC_ANALYSIS, index_id=None)

    with pytest.raises(ValueError, match="index_id"):
        replace(SYNTHETIC_ANALYSIS, index_id="similarity_index_invalid")

    with pytest.raises(ValueError, match="neighbor references are invalid"):
        replace(
            SYNTHETIC_ANALYSIS,
            neighbor_refs=("neighbor_synthetic_duplicate",) * 2,
        )

    with pytest.raises(ValueError, match="neighbor references are invalid"):
        replace(
            SYNTHETIC_ANALYSIS,
            neighbor_refs=tuple(
                f"neighbor_synthetic_trace_{position:02d}" for position in range(11)
            ),
        )


def test_analysis_outcome_is_exactly_the_closed_api_v1_domain() -> None:
    expected_outcomes = (
        "normal",
        "documented_fault",
        "undocumented_fault",
        "out_of_distribution",
        "degraded",
    )
    assert ApiAnalysisOutcome is AnalysisOutcome
    assert tuple(outcome.value for outcome in AnalysisOutcome) == expected_outcomes

    store = InMemoryStore()
    analyses = tuple(
        replace(
            SYNTHETIC_ANALYSIS,
            analysis_id=f"ana_synthetic_outcome_{index}",
            outcome=outcome,
            evidence_references=(),
        )
        for index, outcome in enumerate(AnalysisOutcome)
    )
    with InMemoryUnitOfWork(store) as transaction:
        for analysis in analyses:
            transaction.analyses.add(analysis)
        transaction.commit()

    with InMemoryUnitOfWork(store) as query:
        assert (
            tuple(query.analyses.get(analysis.analysis_id) for analysis in analyses)
            == analyses
        )

    with pytest.raises(ValueError, match="five API v1 outcomes"):
        replace(
            SYNTHETIC_ANALYSIS,
            outcome=cast(AnalysisOutcome, "synthetic_arbitrary_outcome"),
        )

    invalid = replace(
        SYNTHETIC_ANALYSIS,
        analysis_id="ana_synthetic_invalid_outcome",
        evidence_references=(),
    )
    object.__setattr__(
        invalid,
        "outcome",
        cast(AnalysisOutcome, "synthetic_arbitrary_outcome"),
    )
    with (
        InMemoryUnitOfWork() as transaction,
        pytest.raises(ValueError, match="five API v1 outcomes"),
    ):
        transaction.analyses.add(invalid)


def test_in_memory_rebuilds_subclasses_without_forbidden_payloads() -> None:
    chunk = SYNTHETIC_DOCUMENT.versions[0].chunks[0]
    tainted_chunk = _TaintedChunkReference(
        chunk_ref=chunk.chunk_ref,
        document_id=chunk.document_id,
        document_version_id=chunk.document_version_id,
        page_number=chunk.page_number,
    )
    version = SYNTHETIC_DOCUMENT.versions[0]
    tainted_version = _TaintedDocumentVersion(
        document_version_id=version.document_version_id,
        document_id=version.document_id,
        source_sha256=version.source_sha256,
        created_at=version.created_at,
        chunks=(tainted_chunk,),
    )
    tainted_document = _TaintedDocument(
        document_id=SYNTHETIC_DOCUMENT.document_id,
        created_at=SYNTHETIC_DOCUMENT.created_at,
        versions=(tainted_version,),
    )
    tainted_reference = _TaintedEvidenceReference(
        evidence_id="synthetic-tainted-evidence",
        document_id=tainted_document.document_id,
        document_version_id=tainted_version.document_version_id,
        chunk_ref=tainted_chunk.chunk_ref,
        ordinal=1,
    )
    tainted_analysis = _TaintedAnalysis(
        analysis_id="ana_synthetic_tainted",
        outcome=AnalysisOutcome.DOCUMENTED_FAULT,
        dataset_id=SYNTHETIC_DATASET_ID,
        model_id="model_synthetic_v1",
        prompt_id="prompt_synthetic_v1",
        configuration_id="config_synthetic_v1",
        created_at=SYNTHETIC_ANALYSIS.created_at,
        evidence_references=(tainted_reference,),
    )

    store = InMemoryStore()
    with InMemoryUnitOfWork(store) as transaction:
        transaction.documents.add(tainted_document)
        transaction.analyses.add(tainted_analysis)
        transaction.commit()

    with InMemoryUnitOfWork(store) as query:
        recovered_document = query.documents.get(tainted_document.document_id)
        recovered_analysis = query.analyses.get(tainted_analysis.analysis_id)

    assert recovered_document is not None
    assert recovered_analysis is not None
    recovered_version = recovered_document.versions[0]
    recovered_chunk = recovered_version.chunks[0]
    recovered_reference = recovered_analysis.evidence_references[0]
    assert type(recovered_document) is DocumentMetadata
    assert type(recovered_version) is DocumentVersionMetadata
    assert type(recovered_chunk) is ChunkReference
    assert type(recovered_analysis) is AnalysisMetadata
    assert type(recovered_reference) is EvidenceReference
    recovered_nodes: tuple[object, ...] = (
        recovered_document,
        recovered_version,
        recovered_chunk,
        recovered_analysis,
        recovered_reference,
    )
    assert all(not hasattr(node, "raw_content") for node in recovered_nodes)
    assert all(_FORBIDDEN_PAYLOAD not in repr(node) for node in recovered_nodes)


def test_in_memory_canonicalizes_every_nested_scalar_value() -> None:
    tainted_document, tainted_analysis = synthetic_tainted_scalar_aggregates()
    store = InMemoryStore()
    with InMemoryUnitOfWork(store) as transaction:
        transaction.documents.add(tainted_document)
        transaction.analyses.add(tainted_analysis)
        transaction.commit()

    with InMemoryUnitOfWork(store) as query:
        recovered_document = query.documents.get(tainted_document.document_id)
        recovered_analysis = query.analyses.get(tainted_analysis.analysis_id)

    assert recovered_document == tainted_document
    assert recovered_analysis == tainted_analysis
    assert recovered_document is not None
    assert recovered_analysis is not None
    assert_persisted_scalars_are_canonical(
        recovered_document,
        recovered_analysis,
    )


def test_in_memory_ignores_hostile_datetime_overrides_and_preserves_instant() -> None:
    document, analysis, sources = synthetic_lying_datetime_aggregates()
    store = InMemoryStore()
    with InMemoryUnitOfWork(store) as transaction:
        transaction.documents.add(document)
        transaction.analyses.add(analysis)
        transaction.commit()

    with InMemoryUnitOfWork(store) as query:
        recovered_document = query.documents.get(document.document_id)
        recovered_analysis = query.analyses.get(analysis.analysis_id)

    assert recovered_document is not None
    assert recovered_analysis is not None
    assert_lying_datetimes_are_canonical(
        recovered_document,
        recovered_analysis,
        sources,
    )


@pytest.mark.parametrize(
    ("case_name", "offset"),
    SYNTHETIC_UTC_OFFSETS,
    ids=[case_name for case_name, _ in SYNTHETIC_UTC_OFFSETS],
)
def test_in_memory_preserves_valid_fractional_offset_instants(
    case_name: str,
    offset: timedelta,
) -> None:
    document, source = synthetic_offset_datetime_document(case_name, offset)
    store = InMemoryStore()
    with InMemoryUnitOfWork(store) as transaction:
        transaction.documents.add(document)
        transaction.commit()

    with InMemoryUnitOfWork(store) as query:
        recovered_document = query.documents.get(document.document_id)

    assert recovered_document is not None
    assert_offset_datetime_is_canonical(recovered_document, source)


@pytest.mark.parametrize(
    ("case_name", "state"),
    SYNTHETIC_INVALID_CIVIL_STATES,
    ids=[case_name for case_name, _ in SYNTHETIC_INVALID_CIVIL_STATES],
)
def test_in_memory_rejects_invalid_civil_datetime_without_executing_reducer(
    case_name: str,
    state: bytes,
) -> None:
    document, source, zone = synthetic_invalid_civil_datetime_document(
        case_name,
        state,
    )
    store = InMemoryStore()

    with (
        InMemoryUnitOfWork(store) as transaction,
        pytest.raises(ValueError) as caught,
    ):
        transaction.documents.add(document)

    assert type(caught.value) is ValueError
    assert str(caught.value) == "created_at could not be canonicalized safely."
    assert source.virtual_reads == []
    assert type(source).reducer_callable_reads == []
    assert zone.virtual_reads == []
    with InMemoryUnitOfWork(store) as query:
        assert query.documents.get(document.document_id) is None


def test_in_memory_canonicalizes_ambiguous_zoneinfo_without_virtual_reads() -> None:
    document, source = synthetic_ambiguous_zoneinfo_document()
    store = InMemoryStore()
    with InMemoryUnitOfWork(store) as transaction:
        transaction.documents.add(document)
        transaction.commit()

    with InMemoryUnitOfWork(store) as query:
        recovered_document = query.documents.get(document.document_id)

    assert recovered_document is not None
    assert_ambiguous_zoneinfo_datetime_is_canonical(
        recovered_document,
        source,
    )


def test_document_rejects_a_chunk_identifier_reused_across_versions() -> None:
    first, second = SYNTHETIC_DOCUMENT.versions
    duplicate = replace(
        second,
        chunks=(
            replace(
                first.chunks[0],
                document_version_id=second.document_version_id,
            ),
        ),
    )

    with pytest.raises(ValueError, match="unique across document versions"):
        replace(SYNTHETIC_DOCUMENT, versions=(first, duplicate))


def test_in_memory_unit_of_work_round_trips_complete_aggregates() -> None:
    store = InMemoryStore()
    unit_of_work = _accepts_unit_of_work(InMemoryUnitOfWork(store))

    with unit_of_work:
        unit_of_work.documents.add(SYNTHETIC_DOCUMENT)
        unit_of_work.analyses.add(SYNTHETIC_ANALYSIS)
        assert unit_of_work.documents.get(SYNTHETIC_DOCUMENT.document_id) == (
            SYNTHETIC_DOCUMENT
        )
        assert unit_of_work.analyses.get(SYNTHETIC_ANALYSIS.analysis_id) == (
            SYNTHETIC_ANALYSIS
        )
        unit_of_work.commit()

    with InMemoryUnitOfWork(store) as query:
        assert query.documents.get(SYNTHETIC_DOCUMENT.document_id) == SYNTHETIC_DOCUMENT
        recovered = query.analyses.get(SYNTHETIC_ANALYSIS.analysis_id)
        assert recovered == SYNTHETIC_ANALYSIS
        assert recovered is not None
        assert recovered.document_version_ids == SYNTHETIC_ANALYSIS.document_version_ids


def test_in_memory_adds_a_version_idempotently_without_rewriting_history() -> None:
    store = InMemoryStore()
    with InMemoryUnitOfWork(store) as initial:
        initial.documents.add(SYNTHETIC_INITIAL_DOCUMENT)
        initial.commit()

    with InMemoryUnitOfWork(store) as evolved:
        evolved.documents.add_version(SYNTHETIC_DOCUMENT_VERSION_V2)
        evolved.analyses.add(SYNTHETIC_ANALYSIS)
        evolved.commit()

    with InMemoryUnitOfWork(store) as replay:
        replay.documents.add_version(SYNTHETIC_DOCUMENT_VERSION_V2)
        replay.commit()

    with InMemoryUnitOfWork(store) as query:
        assert query.documents.get(SYNTHETIC_DOCUMENT.document_id) == SYNTHETIC_DOCUMENT
        recovered = query.analyses.get(SYNTHETIC_ANALYSIS.analysis_id)
        assert recovered is not None
        assert recovered.document_version_ids == (
            "docver_synthetic_guide_v2",
            "docver_synthetic_guide_v1",
        )


def test_in_memory_version_evolution_rejects_hash_and_identifier_conflicts() -> None:
    store = InMemoryStore()
    with InMemoryUnitOfWork(store) as initial:
        initial.documents.add(SYNTHETIC_INITIAL_DOCUMENT)
        initial.commit()

    conflicting_identifier = replace(
        SYNTHETIC_DOCUMENT_VERSION_V2,
        document_version_id="docver_synthetic_guide_v1",
        chunks=tuple(
            replace(
                chunk,
                document_version_id="docver_synthetic_guide_v1",
            )
            for chunk in SYNTHETIC_DOCUMENT_VERSION_V2.chunks
        ),
    )
    with (
        InMemoryUnitOfWork(store) as conflict,
        pytest.raises(PersistenceConflictError),
    ):
        conflict.documents.add_version(conflicting_identifier)

    conflicting_hash = replace(
        SYNTHETIC_DOCUMENT_VERSION_V2,
        source_sha256=SYNTHETIC_INITIAL_DOCUMENT.versions[0].source_sha256,
    )
    with (
        InMemoryUnitOfWork(store) as conflict,
        pytest.raises(PersistenceConflictError),
    ):
        conflict.documents.add_version(conflicting_hash)

    with (
        InMemoryUnitOfWork() as missing_document,
        pytest.raises(PersistenceIntegrityError),
    ):
        missing_document.documents.add_version(SYNTHETIC_DOCUMENT_VERSION_V2)


def test_in_memory_evidence_identifiers_are_scoped_to_the_analysis() -> None:
    second_analysis = replace(
        SYNTHETIC_ANALYSIS,
        analysis_id="ana_synthetic_trace_second",
    )
    store = InMemoryStore()
    with InMemoryUnitOfWork(store) as transaction:
        transaction.documents.add(SYNTHETIC_DOCUMENT)
        transaction.analyses.add(SYNTHETIC_ANALYSIS)
        transaction.analyses.add(second_analysis)
        transaction.commit()

    with InMemoryUnitOfWork(store) as query:
        assert query.analyses.get(SYNTHETIC_ANALYSIS.analysis_id) == SYNTHETIC_ANALYSIS
        assert query.analyses.get(second_analysis.analysis_id) == second_analysis


def test_exact_replay_is_idempotent_and_conflicting_replay_is_rejected() -> None:
    store = InMemoryStore()
    with InMemoryUnitOfWork(store) as initial:
        initial.documents.add(SYNTHETIC_DOCUMENT)
        initial.analyses.add(SYNTHETIC_ANALYSIS)
        initial.commit()

    with InMemoryUnitOfWork(store) as replay:
        replay.documents.add(SYNTHETIC_DOCUMENT)
        replay.analyses.add(SYNTHETIC_ANALYSIS)
        replay.commit()

    conflicting_analysis = replace(
        SYNTHETIC_ANALYSIS,
        configuration_id="config_synthetic_v2",
    )
    with (
        InMemoryUnitOfWork(store) as conflict,
        pytest.raises(PersistenceConflictError),
    ):
        conflict.analyses.add(conflicting_analysis)


def test_invalid_evidence_rolls_back_every_staged_write() -> None:
    store = InMemoryStore()
    with InMemoryUnitOfWork(store) as initial:
        initial.documents.add(SYNTHETIC_INITIAL_DOCUMENT)
        initial.commit()

    reference = SYNTHETIC_ANALYSIS.evidence_references[0]
    invalid_analysis = replace(
        SYNTHETIC_ANALYSIS,
        evidence_references=(
            replace(
                reference,
                evidence_id="evidence_synthetic_missing_chunk",
                chunk_ref="chunk_synthetic_missing",
                ordinal=1,
            ),
        ),
    )

    with (
        pytest.raises(PersistenceIntegrityError),
        InMemoryUnitOfWork(store) as transaction,
    ):
        transaction.documents.add_version(SYNTHETIC_DOCUMENT_VERSION_V2)
        transaction.analyses.add(invalid_analysis)
        transaction.commit()

    with InMemoryUnitOfWork(store) as query:
        assert query.documents.get(SYNTHETIC_DOCUMENT.document_id) == (
            SYNTHETIC_INITIAL_DOCUMENT
        )
        assert query.analyses.get(invalid_analysis.analysis_id) is None


def test_uncommitted_and_failed_transactions_do_not_publish_state() -> None:
    store = InMemoryStore()
    with InMemoryUnitOfWork(store) as uncommitted:
        uncommitted.documents.add(SYNTHETIC_DOCUMENT)

    with (
        pytest.raises(RuntimeError, match="synthetic transaction failure"),
        InMemoryUnitOfWork(store) as failed,
    ):
        failed.documents.add(SYNTHETIC_DOCUMENT)
        raise RuntimeError("synthetic transaction failure")

    with InMemoryUnitOfWork(store) as query:
        assert query.documents.get(SYNTHETIC_DOCUMENT.document_id) is None


def test_concurrent_in_memory_commit_fails_without_overwriting_state() -> None:
    store = InMemoryStore()
    with InMemoryUnitOfWork(store) as first, InMemoryUnitOfWork(store) as stale:
        first.documents.add(SYNTHETIC_DOCUMENT)
        first.commit()
        stale.documents.add(SYNTHETIC_DOCUMENT)
        with pytest.raises(TransactionConflictError):
            stale.commit()

    with InMemoryUnitOfWork(store) as query:
        assert query.documents.get(SYNTHETIC_DOCUMENT.document_id) == SYNTHETIC_DOCUMENT
