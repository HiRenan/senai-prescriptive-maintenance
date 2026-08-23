"""Optional real-PostgreSQL validation in a disposable isolated schema."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from typing import Final, cast
from uuid import uuid4

import pytest
from persistence_samples import (
    SYNTHETIC_ANALYSIS,
    SYNTHETIC_DATASET_ID,
    SYNTHETIC_DOCUMENT,
    SYNTHETIC_DOCUMENT_VERSION_V2,
    SYNTHETIC_INITIAL_DOCUMENT,
    assert_persisted_scalars_are_canonical,
    synthetic_tainted_scalar_aggregates,
)
from prescriptive_maintenance.domain import AnalysisOutcome
from prescriptive_maintenance.persistence import (
    LATEST_MIGRATION_VERSION,
    ChunkReference,
    DocumentMetadata,
    DocumentVersionMetadata,
    InMemoryUnitOfWork,
    PersistenceConflictError,
    PersistenceIntegrityError,
    PostgresConnectionFactory,
    PostgresUnitOfWork,
    TransactionRollbackOnlyError,
    UnitOfWorkStateError,
    current_version,
    downgrade,
    upgrade,
)
from prescriptive_maintenance.persistence.migrations import (
    PostgresConnection,
    PostgresRow,
)
from psycopg import Connection, sql
from psycopg.errors import CheckViolation
from psycopg.pq import TransactionStatus
from psycopg.rows import RowFactory, dict_row

_DATABASE_URL_VARIABLE: Final = "PRESCRIPTIVE_MAINTENANCE_TEST_DATABASE_URL"
_TEST_DATABASE_URL: Final = os.environ.get(_DATABASE_URL_VARIABLE)
_ROW_FACTORY: Final = cast(RowFactory[PostgresRow], dict_row)
_APPLICATION_TABLES: Final = {
    "analyses",
    "documents",
    "document_versions",
    "chunk_references",
    "evidence_references",
}
_EXPECTED_COLUMNS: Final = {
    "analyses": {
        "analysis_id",
        "outcome",
        "dataset_id",
        "model_id",
        "prompt_id",
        "configuration_id",
        "created_at",
    },
    "documents": {"document_id", "created_at"},
    "document_versions": {
        "document_version_id",
        "document_id",
        "source_sha256",
        "created_at",
    },
    "chunk_references": {
        "chunk_ref",
        "document_id",
        "document_version_id",
        "page_number",
    },
    "evidence_references": {
        "evidence_id",
        "analysis_id",
        "document_id",
        "document_version_id",
        "chunk_ref",
        "ordinal",
    },
}


@pytest.fixture
def postgres_connection_factory() -> Iterator[PostgresConnectionFactory]:
    if _TEST_DATABASE_URL is None:
        pytest.skip(f"{_DATABASE_URL_VARIABLE} is not configured")
    database_url = _TEST_DATABASE_URL

    schema_name = f"sen60_{uuid4().hex}"
    if re.fullmatch(r"sen60_[0-9a-f]{32}", schema_name) is None:
        raise AssertionError("Unexpected synthetic schema name.")
    admin = Connection[PostgresRow].connect(
        database_url,
        autocommit=True,
        row_factory=_ROW_FACTORY,
    )
    admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))

    def factory() -> PostgresConnection:
        connection = Connection[PostgresRow].connect(
            database_url,
            row_factory=_ROW_FACTORY,
        )
        connection.execute(
            sql.SQL("SET search_path TO {}, pg_catalog").format(
                sql.Identifier(schema_name)
            )
        )
        connection.commit()
        return connection

    try:
        yield factory
    finally:
        admin.execute(
            sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name))
        )
        admin.close()


def _table_names(connection: PostgresConnection) -> set[str]:
    with connection.transaction():
        rows = connection.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = current_schema()
            """
        ).fetchall()
    return {cast(str, row["table_name"]) for row in rows}


def _columns_by_table(connection: PostgresConnection) -> dict[str, set[str]]:
    with connection.transaction():
        rows = connection.execute(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = ANY(%s)
            """,
            (sorted(_APPLICATION_TABLES),),
        ).fetchall()
    columns = {table_name: set[str]() for table_name in _APPLICATION_TABLES}
    for row in rows:
        columns[cast(str, row["table_name"])].add(cast(str, row["column_name"]))
    return columns


def _primary_key_columns(
    connection: PostgresConnection,
    table_name: str,
) -> tuple[str, ...]:
    with connection.transaction():
        rows = connection.execute(
            """
            SELECT key_usage.column_name
            FROM information_schema.table_constraints AS constraints
            JOIN information_schema.key_column_usage AS key_usage
              ON key_usage.constraint_schema = constraints.constraint_schema
             AND key_usage.constraint_name = constraints.constraint_name
            WHERE constraints.table_schema = current_schema()
              AND constraints.table_name = %s
              AND constraints.constraint_type = 'PRIMARY KEY'
            ORDER BY key_usage.ordinal_position
            """,
            (table_name,),
        ).fetchall()
    return tuple(cast(str, row["column_name"]) for row in rows)


def test_migration_empty_up_down_up_is_reproducible(
    postgres_connection_factory: PostgresConnectionFactory,
) -> None:
    connection = postgres_connection_factory()
    try:
        assert current_version(connection) == 0
        assert _table_names(connection) == set()

        upgrade(connection)
        upgrade(connection)
        assert current_version(connection) == LATEST_MIGRATION_VERSION
        assert _table_names(connection) > _APPLICATION_TABLES

        downgrade(connection)
        downgrade(connection)
        assert current_version(connection) == 0
        assert _table_names(connection) == set()

        upgrade(connection)
        assert current_version(connection) == LATEST_MIGRATION_VERSION
        assert _table_names(connection) > _APPLICATION_TABLES
    finally:
        connection.close()


def test_concurrent_empty_database_upgrades_are_serialized_and_idempotent(
    postgres_connection_factory: PostgresConnectionFactory,
) -> None:
    barrier = Barrier(2)

    def migrate_from_empty() -> int:
        connection = postgres_connection_factory()
        try:
            barrier.wait(timeout=10)
            upgrade(connection)
            return current_version(connection)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = tuple(executor.submit(migrate_from_empty) for _ in range(2))
        versions = tuple(future.result(timeout=20) for future in futures)

    assert versions == (LATEST_MIGRATION_VERSION, LATEST_MIGRATION_VERSION)
    verification = postgres_connection_factory()
    try:
        assert current_version(verification) == LATEST_MIGRATION_VERSION
        assert _table_names(verification) > _APPLICATION_TABLES
    finally:
        verification.close()


def test_schema_constraints_and_columns_are_minimal(
    postgres_connection_factory: PostgresConnectionFactory,
) -> None:
    connection = postgres_connection_factory()
    try:
        upgrade(connection)
        columns_by_table = _columns_by_table(connection)
        assert columns_by_table == _EXPECTED_COLUMNS
        assert _primary_key_columns(connection, "evidence_references") == (
            "analysis_id",
            "evidence_id",
        )
        assert not {
            "features",
            "feature_vector",
            "embedding",
            "content",
            "raw_content",
            "source_path",
            "filename",
            "diagnosis",
            "prescription",
        } & set().union(*columns_by_table.values())

        with pytest.raises(CheckViolation), connection.transaction():
            connection.execute(
                """
                INSERT INTO analyses (
                    analysis_id,
                    outcome,
                    dataset_id,
                    model_id,
                    prompt_id,
                    configuration_id,
                    created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                """,
                (
                    "ana_synthetic_invalid",
                    "normal",
                    "dataset_synthetic_v1",
                    "model_synthetic_v1",
                    "prompt_synthetic_v1",
                    "config_synthetic_v1",
                ),
            )

        with connection.transaction():
            connection.execute(
                """
                INSERT INTO analyses (
                    analysis_id,
                    outcome,
                    dataset_id,
                    model_id,
                    prompt_id,
                    configuration_id,
                    created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                """,
                (
                    "ana_synthetic_dataset",
                    "normal",
                    SYNTHETIC_DATASET_ID,
                    "model_synthetic_v1",
                    "prompt_synthetic_v1",
                    "config_synthetic_v1",
                ),
            )
        row = connection.execute(
            "SELECT dataset_id FROM analyses WHERE analysis_id = %s",
            ("ana_synthetic_dataset",),
        ).fetchone()
        assert row is not None
        assert row["dataset_id"] == SYNTHETIC_DATASET_ID

        with connection.transaction():
            for index, outcome in enumerate(AnalysisOutcome):
                connection.execute(
                    """
                    INSERT INTO analyses (
                        analysis_id,
                        outcome,
                        dataset_id,
                        model_id,
                        prompt_id,
                        configuration_id,
                        created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                    """,
                    (
                        f"ana_synthetic_outcome_{index}",
                        outcome.value,
                        SYNTHETIC_DATASET_ID,
                        "model_synthetic_v1",
                        "prompt_synthetic_v1",
                        "config_synthetic_v1",
                    ),
                )

        with pytest.raises(CheckViolation), connection.transaction():
            connection.execute(
                """
                INSERT INTO analyses (
                    analysis_id,
                    outcome,
                    dataset_id,
                    model_id,
                    prompt_id,
                    configuration_id,
                    created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                """,
                (
                    "ana_synthetic_arbitrary_outcome",
                    "synthetic_arbitrary_outcome",
                    SYNTHETIC_DATASET_ID,
                    "model_synthetic_v1",
                    "prompt_synthetic_v1",
                    "config_synthetic_v1",
                ),
            )
    finally:
        connection.close()


def test_postgres_adapter_rejects_an_arbitrary_outcome(
    postgres_connection_factory: PostgresConnectionFactory,
) -> None:
    migration_connection = postgres_connection_factory()
    try:
        upgrade(migration_connection)
    finally:
        migration_connection.close()

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
        PostgresUnitOfWork(postgres_connection_factory) as transaction,
        pytest.raises(ValueError, match="five API v1 outcomes"),
    ):
        transaction.analyses.add(invalid)


def test_postgres_round_trip_and_exact_replay_idempotency(
    postgres_connection_factory: PostgresConnectionFactory,
) -> None:
    migration_connection = postgres_connection_factory()
    try:
        upgrade(migration_connection)
    finally:
        migration_connection.close()

    with PostgresUnitOfWork(postgres_connection_factory) as transaction:
        transaction.documents.add(SYNTHETIC_DOCUMENT)
        transaction.analyses.add(SYNTHETIC_ANALYSIS)
        transaction.commit()

    with PostgresUnitOfWork(postgres_connection_factory) as replay:
        replay.documents.add(SYNTHETIC_DOCUMENT)
        replay.analyses.add(SYNTHETIC_ANALYSIS)
        replay.commit()

    with PostgresUnitOfWork(postgres_connection_factory) as query:
        assert query.documents.get(SYNTHETIC_DOCUMENT.document_id) == (
            SYNTHETIC_DOCUMENT
        )
        recovered = query.analyses.get(SYNTHETIC_ANALYSIS.analysis_id)
        assert recovered == SYNTHETIC_ANALYSIS
        assert recovered is not None
        assert recovered.document_version_ids == SYNTHETIC_ANALYSIS.document_version_ids

    conflicting = replace(
        SYNTHETIC_ANALYSIS,
        configuration_id="config_synthetic_v2",
    )
    with (
        PostgresUnitOfWork(postgres_connection_factory) as conflict,
        pytest.raises(PersistenceConflictError),
    ):
        conflict.analyses.add(conflicting)


def test_postgres_canonicalizes_every_nested_scalar_value(
    postgres_connection_factory: PostgresConnectionFactory,
) -> None:
    migration_connection = postgres_connection_factory()
    try:
        upgrade(migration_connection)
    finally:
        migration_connection.close()

    tainted_document, tainted_analysis = synthetic_tainted_scalar_aggregates()
    with PostgresUnitOfWork(postgres_connection_factory) as transaction:
        transaction.documents.add(tainted_document)
        transaction.analyses.add(tainted_analysis)
        transaction.commit()

    with PostgresUnitOfWork(postgres_connection_factory) as query:
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


def test_postgres_adds_a_version_idempotently_without_rewriting_history(
    postgres_connection_factory: PostgresConnectionFactory,
) -> None:
    migration_connection = postgres_connection_factory()
    try:
        upgrade(migration_connection)
    finally:
        migration_connection.close()

    with PostgresUnitOfWork(postgres_connection_factory) as initial:
        initial.documents.add(SYNTHETIC_INITIAL_DOCUMENT)
        initial.commit()

    with PostgresUnitOfWork(postgres_connection_factory) as evolved:
        evolved.documents.add_version(SYNTHETIC_DOCUMENT_VERSION_V2)
        evolved.analyses.add(SYNTHETIC_ANALYSIS)
        evolved.commit()

    with PostgresUnitOfWork(postgres_connection_factory) as replay:
        replay.documents.add_version(SYNTHETIC_DOCUMENT_VERSION_V2)
        replay.commit()

    with PostgresUnitOfWork(postgres_connection_factory) as query:
        assert query.documents.get(SYNTHETIC_DOCUMENT.document_id) == (
            SYNTHETIC_DOCUMENT
        )
        recovered = query.analyses.get(SYNTHETIC_ANALYSIS.analysis_id)
        assert recovered is not None
        assert recovered.document_version_ids == (
            "docver_synthetic_guide_v2",
            "docver_synthetic_guide_v1",
        )

    conflicting_identifier = replace(
        SYNTHETIC_DOCUMENT_VERSION_V2,
        source_sha256="3" * 64,
    )
    with (
        PostgresUnitOfWork(postgres_connection_factory) as conflict,
        pytest.raises(PersistenceConflictError),
    ):
        conflict.documents.add_version(conflicting_identifier)

    different_id_same_hash = replace(
        SYNTHETIC_DOCUMENT_VERSION_V2,
        document_version_id="docver_synthetic_guide_v3",
        chunks=tuple(
            replace(
                chunk,
                chunk_ref="chunk_synthetic_guide_v3_01",
                document_version_id="docver_synthetic_guide_v3",
            )
            for chunk in SYNTHETIC_DOCUMENT_VERSION_V2.chunks
        ),
    )
    with (
        PostgresUnitOfWork(postgres_connection_factory) as conflict,
        pytest.raises(PersistenceConflictError),
    ):
        conflict.documents.add_version(different_id_same_hash)

    missing_document_version = replace(
        SYNTHETIC_DOCUMENT_VERSION_V2,
        document_id="doc_synthetic_missing",
        document_version_id="docver_synthetic_missing",
        chunks=tuple(
            replace(
                chunk,
                chunk_ref="chunk_synthetic_missing",
                document_id="doc_synthetic_missing",
                document_version_id="docver_synthetic_missing",
            )
            for chunk in SYNTHETIC_DOCUMENT_VERSION_V2.chunks
        ),
    )
    with (
        PostgresUnitOfWork(postgres_connection_factory) as missing,
        pytest.raises(PersistenceIntegrityError),
    ):
        missing.documents.add_version(missing_document_version)


def test_postgres_evidence_identifiers_are_scoped_to_the_analysis(
    postgres_connection_factory: PostgresConnectionFactory,
) -> None:
    migration_connection = postgres_connection_factory()
    try:
        upgrade(migration_connection)
    finally:
        migration_connection.close()

    second_analysis = replace(
        SYNTHETIC_ANALYSIS,
        analysis_id="ana_synthetic_trace_second",
    )
    with PostgresUnitOfWork(postgres_connection_factory) as transaction:
        transaction.documents.add(SYNTHETIC_DOCUMENT)
        transaction.analyses.add(SYNTHETIC_ANALYSIS)
        transaction.analyses.add(second_analysis)
        transaction.commit()

    with PostgresUnitOfWork(postgres_connection_factory) as query:
        assert query.analyses.get(SYNTHETIC_ANALYSIS.analysis_id) == (
            SYNTHETIC_ANALYSIS
        )
        assert query.analyses.get(second_analysis.analysis_id) == second_analysis


def test_constraint_failure_is_sanitized_and_marks_transaction_rollback_only(
    postgres_connection_factory: PostgresConnectionFactory,
) -> None:
    migration_connection = postgres_connection_factory()
    try:
        upgrade(migration_connection)
    finally:
        migration_connection.close()

    conflicting_document = DocumentMetadata(
        document_id="doc_synthetic_conflict",
        created_at=SYNTHETIC_DOCUMENT.created_at,
        versions=(
            DocumentVersionMetadata(
                document_version_id="docver_synthetic_conflict",
                document_id="doc_synthetic_conflict",
                source_sha256="4" * 64,
                created_at=SYNTHETIC_DOCUMENT.created_at,
                chunks=(
                    ChunkReference(
                        chunk_ref="chunk_synthetic_guide_v1_01",
                        document_id="doc_synthetic_conflict",
                        document_version_id="docver_synthetic_conflict",
                        page_number=1,
                    ),
                ),
            ),
        ),
    )

    with PostgresUnitOfWork(postgres_connection_factory) as transaction:
        repository = transaction.documents
        repository.add(SYNTHETIC_DOCUMENT)
        with pytest.raises(PersistenceConflictError) as caught:
            repository.add(conflicting_document)

        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None
        assert "psycopg" not in repr(caught.value).lower()
        assert transaction.rollback_only
        with pytest.raises(TransactionRollbackOnlyError, match="explicit rollback"):
            repository.get(SYNTHETIC_DOCUMENT.document_id)
        with pytest.raises(TransactionRollbackOnlyError, match="explicit rollback"):
            transaction.commit()
        transaction.rollback()

    with PostgresUnitOfWork(postgres_connection_factory) as query:
        assert query.documents.get(SYNTHETIC_DOCUMENT.document_id) is None
        assert query.documents.get(conflicting_document.document_id) is None


def test_unit_of_work_rejects_an_external_transaction_without_touching_it(
    postgres_connection_factory: PostgresConnectionFactory,
) -> None:
    migration_connection = postgres_connection_factory()
    try:
        upgrade(migration_connection)
    finally:
        migration_connection.close()

    external = postgres_connection_factory()
    observer = postgres_connection_factory()
    try:
        external.execute(
            "INSERT INTO documents (document_id, created_at) VALUES (%s, %s)",
            ("doc_synthetic_external", SYNTHETIC_DOCUMENT.created_at),
        )
        assert external.info.transaction_status is TransactionStatus.INTRANS

        with (
            pytest.raises(UnitOfWorkStateError, match="idle connection"),
            PostgresUnitOfWork(lambda: external),
        ):
            raise AssertionError("A non-idle connection must not be accepted.")

        assert not external.closed
        assert external.info.transaction_status is TransactionStatus.INTRANS
        assert (
            external.execute(
                "SELECT document_id FROM documents WHERE document_id = %s",
                ("doc_synthetic_external",),
            ).fetchone()
            is not None
        )
        assert (
            observer.execute(
                "SELECT document_id FROM documents WHERE document_id = %s",
                ("doc_synthetic_external",),
            ).fetchone()
            is None
        )

        external.commit()
        assert (
            observer.execute(
                "SELECT document_id FROM documents WHERE document_id = %s",
                ("doc_synthetic_external",),
            ).fetchone()
            is not None
        )
    finally:
        if not external.closed:
            external.rollback()
        observer.rollback()
        external.close()
        observer.close()


def test_unit_of_work_preserves_an_explicit_autocommit_transaction_on_entry_failure(
    postgres_connection_factory: PostgresConnectionFactory,
) -> None:
    migration_connection = postgres_connection_factory()
    try:
        upgrade(migration_connection)
    finally:
        migration_connection.close()

    external = postgres_connection_factory()
    observer = postgres_connection_factory()
    external.autocommit = True
    unit_of_work = PostgresUnitOfWork(lambda: external)
    try:
        external.execute("BEGIN")
        external.execute(
            "INSERT INTO documents (document_id, created_at) VALUES (%s, %s)",
            ("doc_synthetic_external_autocommit", SYNTHETIC_DOCUMENT.created_at),
        )
        assert external.info.transaction_status is TransactionStatus.INTRANS

        with pytest.raises(UnitOfWorkStateError, match="idle connection"):
            unit_of_work.__enter__()

        assert not external.closed
        assert external.info.transaction_status is TransactionStatus.INTRANS
        assert (
            external.execute(
                "SELECT document_id FROM documents WHERE document_id = %s",
                ("doc_synthetic_external_autocommit",),
            ).fetchone()
            is not None
        )
        assert (
            observer.execute(
                "SELECT document_id FROM documents WHERE document_id = %s",
                ("doc_synthetic_external_autocommit",),
            ).fetchone()
            is None
        )
        with pytest.raises(UnitOfWorkStateError, match="not active"):
            _ = unit_of_work.analyses

        external.commit()
        assert (
            observer.execute(
                "SELECT document_id FROM documents WHERE document_id = %s",
                ("doc_synthetic_external_autocommit",),
            ).fetchone()
            is not None
        )
    finally:
        if not external.closed:
            external.rollback()
        observer.rollback()
        external.close()
        observer.close()


def test_unit_of_work_closes_an_idle_autocommit_connection_on_entry_failure(
    postgres_connection_factory: PostgresConnectionFactory,
) -> None:
    external = postgres_connection_factory()
    external.autocommit = True
    unit_of_work = PostgresUnitOfWork(lambda: external)

    with pytest.raises(UnitOfWorkStateError, match="autocommit"):
        unit_of_work.__enter__()

    assert external.closed
    with pytest.raises(UnitOfWorkStateError, match="not active"):
        _ = unit_of_work.documents


def test_invalid_reference_and_failure_roll_back_entire_postgres_transaction(
    postgres_connection_factory: PostgresConnectionFactory,
) -> None:
    migration_connection = postgres_connection_factory()
    try:
        upgrade(migration_connection)
    finally:
        migration_connection.close()

    with PostgresUnitOfWork(postgres_connection_factory) as initial:
        initial.documents.add(SYNTHETIC_INITIAL_DOCUMENT)
        initial.commit()

    reference = SYNTHETIC_ANALYSIS.evidence_references[0]
    invalid_analysis = replace(
        SYNTHETIC_ANALYSIS,
        evidence_references=(
            replace(
                reference,
                evidence_id="evidence_synthetic_invalid_reference",
                document_id="doc_synthetic_missing",
                document_version_id="docver_synthetic_missing",
                chunk_ref="chunk_synthetic_missing",
                ordinal=1,
            ),
        ),
    )
    with (
        pytest.raises(PersistenceIntegrityError),
        PostgresUnitOfWork(postgres_connection_factory) as invalid,
    ):
        invalid.documents.add_version(SYNTHETIC_DOCUMENT_VERSION_V2)
        invalid.analyses.add(invalid_analysis)
        invalid.commit()

    with (
        pytest.raises(RuntimeError, match="synthetic transaction failure"),
        PostgresUnitOfWork(postgres_connection_factory) as failed,
    ):
        failed.documents.add_version(SYNTHETIC_DOCUMENT_VERSION_V2)
        raise RuntimeError("synthetic transaction failure")

    with PostgresUnitOfWork(postgres_connection_factory) as query:
        assert query.analyses.get(invalid_analysis.analysis_id) is None
        assert query.documents.get(SYNTHETIC_DOCUMENT.document_id) == (
            SYNTHETIC_INITIAL_DOCUMENT
        )


def test_standard_memory_adapter_does_not_require_postgres() -> None:
    with InMemoryUnitOfWork() as unit_of_work:
        assert unit_of_work.analyses.get("ana_synthetic_absent") is None
