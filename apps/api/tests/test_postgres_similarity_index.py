"""Optional real-PostgreSQL parity checks for the similarity index."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Final, cast
from uuid import uuid4

import pandas as pd
import pytest
from prescriptive_maintenance.contracts import ANALYSIS_FEATURE_NAMES, MAX_TOP_K
from prescriptive_maintenance.modeling.knn import fit_knn_model, save_knn_model
from prescriptive_maintenance.modeling.similarity_index import (
    InMemorySimilarityIndexAdapter,
    LoadedSimilarityIndex,
    SimilarityIndexCompatibility,
    SimilarityIndexCompatibilityError,
    SimilarityIndexRepositoryError,
    SimilarityQuery,
    load_similarity_index,
    save_similarity_index_from_knn_artifact,
)
from prescriptive_maintenance.modeling.similarity_postgres import (
    PostgresSimilarityIndexAdapter,
    install_similarity_index,
)
from prescriptive_maintenance.persistence import (
    LATEST_MIGRATION_VERSION,
    PostgresConnectionFactory,
    current_version,
    downgrade,
    upgrade,
)
from prescriptive_maintenance.persistence.migrations import (
    PostgresConnection,
    PostgresRow,
)
from psycopg import Connection, sql
from psycopg.rows import RowFactory, dict_row

_DATABASE_URL_VARIABLE: Final = "PRESCRIPTIVE_MAINTENANCE_TEST_DATABASE_URL"
_TEST_DATABASE_URL: Final = os.environ.get(_DATABASE_URL_VARIABLE)
_ROW_FACTORY: Final = cast(RowFactory[PostgresRow], dict_row)
_DATASET_ID: Final = "d" * 64
_SCHEMA_ID: Final = "e" * 64
_SIMILARITY_TABLES: Final = {
    "similarity_indexes",
    "similarity_index_entries",
}


@pytest.fixture
def postgres_connection_factory() -> Iterator[PostgresConnectionFactory]:
    if _TEST_DATABASE_URL is None:
        pytest.skip(f"{_DATABASE_URL_VARIABLE} is not configured")
    database_url = _TEST_DATABASE_URL
    schema_name = f"sen52_{uuid4().hex}"
    if re.fullmatch(r"sen52_[0-9a-f]{32}", schema_name) is None:
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


@pytest.fixture
def loaded_index(tmp_path: Path) -> LoadedSimilarityIndex:
    rows: list[dict[str, object]] = []
    for value, label in zip(
        (0.0, 2.0, 4.0),
        ("synthetic-alpha", "synthetic-zeta", "synthetic-alpha"),
        strict=True,
    ):
        row: dict[str, object] = {
            name: float(value if position == 0 else position)
            for position, name in enumerate(ANALYSIS_FEATURE_NAMES)
        }
        row["y"] = label
        rows.append(row)
    frame = pd.DataFrame(rows, columns=(*ANALYSIS_FEATURE_NAMES, "y"))
    frame.loc[:, list(ANALYSIS_FEATURE_NAMES)] = frame.loc[
        :, list(ANALYSIS_FEATURE_NAMES)
    ].astype("float64")
    frame["y"] = frame["y"].astype("string")
    model = fit_knn_model(
        frame,
        dataset_id=_DATASET_ID,
        training_partition_sha256=_DATASET_ID,
    )
    model_directory = save_knn_model(model, tmp_path / "model")
    index_directory = save_similarity_index_from_knn_artifact(
        model_directory,
        schema_id=_SCHEMA_ID,
        output_directory=tmp_path / "index",
    )
    return load_similarity_index(
        index_directory,
        expected=SimilarityIndexCompatibility(
            dataset_id=_DATASET_ID,
            schema_id=_SCHEMA_ID,
        ),
    )


def _features(first_value: float) -> tuple[float, ...]:
    return tuple(
        float(first_value if position == 0 else position)
        for position, _name in enumerate(ANALYSIS_FEATURE_NAMES)
    )


def _query(
    index: LoadedSimilarityIndex,
    *,
    top_k: int = 3,
    fault_codes: tuple[str, ...] = (),
) -> SimilarityQuery:
    return SimilarityQuery(
        selector=index.selector,
        features=_features(1.0),
        top_k=top_k,
        fault_codes=fault_codes,
    )


def _table_names(connection: PostgresConnection) -> set[str]:
    rows = connection.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = current_schema()
        """
    ).fetchall()
    return {cast(str, row["table_name"]) for row in rows}


def test_similarity_migration_up_down_up_is_reproducible(
    postgres_connection_factory: PostgresConnectionFactory,
) -> None:
    connection = postgres_connection_factory()
    try:
        assert current_version(connection) == 0
        assert LATEST_MIGRATION_VERSION == 4
        upgrade(connection, target=2)
        assert current_version(connection) == 2
        assert _table_names(connection) >= _SIMILARITY_TABLES
        vector_type = connection.execute(
            """
            SELECT format_type(attribute.atttypid, attribute.atttypmod) AS type_name
            FROM pg_attribute AS attribute
            WHERE attribute.attrelid = 'similarity_index_entries'::regclass
              AND attribute.attname = 'embedding'
            """
        ).fetchone()
        assert vector_type is not None
        assert vector_type["type_name"] == "public.vector(18)"

        downgrade(connection, target=1)
        assert current_version(connection) == 1
        assert not (_table_names(connection) & _SIMILARITY_TABLES)

        upgrade(connection, target=2)
        assert current_version(connection) == 2
        assert _table_names(connection) >= _SIMILARITY_TABLES
    finally:
        connection.close()


def test_postgres_adapter_has_exact_memory_parity_for_ties_filters_and_empty(
    postgres_connection_factory: PostgresConnectionFactory,
    loaded_index: LoadedSimilarityIndex,
) -> None:
    connection = postgres_connection_factory()
    try:
        upgrade(connection)
        install_similarity_index(connection, loaded_index)
        install_similarity_index(connection, loaded_index)
        postgres = PostgresSimilarityIndexAdapter(connection, loaded_index)
        memory = InMemorySimilarityIndexAdapter(loaded_index)
        codes = tuple(sorted({record.fault_code for record in loaded_index.records}))
        queries = (
            _query(loaded_index, top_k=2),
            _query(loaded_index, top_k=MAX_TOP_K, fault_codes=(codes[0],)),
            _query(
                loaded_index,
                top_k=MAX_TOP_K,
                fault_codes=("fault_00000000000000000000000000000000",),
            ),
        )

        for query in queries:
            assert postgres.query(query) == memory.query(query)
        tied = postgres.query(queries[0])
        assert tied[0].distance == tied[1].distance
        assert tuple(item.opaque_id for item in tied) == tuple(
            sorted(item.opaque_id for item in tied)
        )
    finally:
        connection.close()


def test_postgres_adapter_rejects_manifest_version_drift_before_search(
    postgres_connection_factory: PostgresConnectionFactory,
    loaded_index: LoadedSimilarityIndex,
) -> None:
    connection = postgres_connection_factory()
    try:
        upgrade(connection)
        install_similarity_index(connection, loaded_index)
        adapter = PostgresSimilarityIndexAdapter(connection, loaded_index)
        with connection.transaction():
            connection.execute(
                """
                UPDATE similarity_indexes
                SET configuration_version = %s
                WHERE index_id = %s
                """,
                ("euclidean-opaque-ranking.v999", loaded_index.selector.index_id),
            )

        with pytest.raises(SimilarityIndexCompatibilityError, match="inconsistent"):
            adapter.query(_query(loaded_index))
    finally:
        connection.close()


def test_query_rejects_a_tampered_vector_that_would_leave_the_top_k(
    postgres_connection_factory: PostgresConnectionFactory,
    loaded_index: LoadedSimilarityIndex,
) -> None:
    connection = postgres_connection_factory()
    try:
        upgrade(connection)
        install_similarity_index(connection, loaded_index)
        adapter = PostgresSimilarityIndexAdapter(connection, loaded_index)
        expected = InMemorySimilarityIndexAdapter(loaded_index).query(
            _query(loaded_index, top_k=1)
        )
        with connection.transaction():
            connection.execute(
                """
                UPDATE similarity_index_entries
                SET embedding = %s::public.vector
                WHERE index_id = %s AND opaque_id = %s
                """,
                (
                    "[99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99]",
                    loaded_index.selector.index_id,
                    expected[0].opaque_id,
                ),
            )

        with pytest.raises(SimilarityIndexRepositoryError, match="canonical"):
            adapter.query(_query(loaded_index, top_k=1))
    finally:
        connection.close()


def test_failed_replay_rolls_back_every_partial_entry(
    postgres_connection_factory: PostgresConnectionFactory,
    loaded_index: LoadedSimilarityIndex,
) -> None:
    connection = postgres_connection_factory()
    try:
        upgrade(connection)
        install_similarity_index(connection, loaded_index)
        retained_id = loaded_index.records[0].opaque_id
        with connection.transaction():
            connection.execute(
                """
                DELETE FROM similarity_index_entries
                WHERE index_id = %s AND opaque_id <> %s
                """,
                (loaded_index.selector.index_id, retained_id),
            )
            connection.execute(
                """
                UPDATE similarity_index_entries
                SET embedding = %s::public.vector
                WHERE index_id = %s AND opaque_id = %s
                """,
                (
                    "[9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9]",
                    loaded_index.selector.index_id,
                    retained_id,
                ),
            )

        with pytest.raises(SimilarityIndexRepositoryError, match="integrity"):
            install_similarity_index(connection, loaded_index)

        row = connection.execute(
            """
            SELECT COUNT(*) AS record_count
            FROM similarity_index_entries
            WHERE index_id = %s
            """,
            (loaded_index.selector.index_id,),
        ).fetchone()
        assert row is not None
        assert row["record_count"] == 1
    finally:
        connection.close()


def test_concurrent_exact_replays_are_serialized_and_idempotent(
    postgres_connection_factory: PostgresConnectionFactory,
    loaded_index: LoadedSimilarityIndex,
) -> None:
    migration_connection = postgres_connection_factory()
    try:
        upgrade(migration_connection)
    finally:
        migration_connection.close()
    barrier = Barrier(2)

    def install_from_worker() -> int:
        connection = postgres_connection_factory()
        try:
            barrier.wait(timeout=10)
            install_similarity_index(connection, loaded_index)
            return len(
                PostgresSimilarityIndexAdapter(connection, loaded_index).query(
                    _query(loaded_index)
                )
            )
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = tuple(executor.submit(install_from_worker) for _ in range(2))
        assert tuple(future.result(timeout=20) for future in futures) == (3, 3)

    verification = postgres_connection_factory()
    try:
        row = verification.execute(
            """
            SELECT COUNT(*) AS record_count
            FROM similarity_index_entries
            WHERE index_id = %s
            """,
            (loaded_index.selector.index_id,),
        ).fetchone()
        assert row is not None
        assert row["record_count"] == loaded_index.manifest.record_count
    finally:
        verification.close()
