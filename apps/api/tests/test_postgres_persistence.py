"""Optional real-PostgreSQL validation in a disposable isolated schema."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import replace
from typing import Final, cast
from uuid import uuid4

import pytest
from persistence_samples import (
    SYNTHETIC_ANALYSIS,
    SYNTHETIC_DATASET_ID,
    SYNTHETIC_DOCUMENT,
    SYNTHETIC_DOCUMENT_VERSION_V2,
    SYNTHETIC_INITIAL_DOCUMENT,
)
from prescriptive_maintenance.persistence import (
    LATEST_MIGRATION_VERSION,
    InMemoryUnitOfWork,
    PersistenceConflictError,
    PersistenceIntegrityError,
    PostgresConnectionFactory,
    PostgresUnitOfWork,
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
    finally:
        connection.close()


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
