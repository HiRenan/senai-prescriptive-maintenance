"""Contract tests for minimal metadata and the in-memory transaction adapter."""

from dataclasses import fields, replace

import pytest
from persistence_samples import (
    SYNTHETIC_ANALYSIS,
    SYNTHETIC_DATASET_ID,
    SYNTHETIC_DOCUMENT,
    SYNTHETIC_DOCUMENT_VERSION_V2,
    SYNTHETIC_INITIAL_DOCUMENT,
)
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
