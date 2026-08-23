"""Transactional PostgreSQL/pgvector adapter for a verified similarity index."""

from __future__ import annotations

from hashlib import sha256
from math import isclose, isfinite
from typing import Final, LiteralString, cast

import numpy as np
from numpy.typing import NDArray
from psycopg import Error
from psycopg.pq import TransactionStatus

from prescriptive_maintenance.modeling.similarity_index import (
    SIMILARITY_INDEX_DIMENSION,
    SIMILARITY_VECTOR_DTYPE,
    InMemorySimilarityIndexAdapter,
    LoadedSimilarityIndex,
    SimilarityIndexCompatibilityError,
    SimilarityIndexError,
    SimilarityIndexRepositoryError,
    SimilarityNeighbor,
    SimilarityQuery,
)
from prescriptive_maintenance.persistence.migrations import PostgresConnection

type FloatVector = NDArray[np.float32]

_MANIFEST_INSERT_SQL: Final[LiteralString] = """
INSERT INTO similarity_indexes (
    index_id,
    content_sha256,
    manifest_sha256,
    artifact_schema_version,
    dataset_id,
    schema_id,
    feature_contract_version,
    preprocessor_version,
    index_version,
    configuration_version,
    dimension,
    metric,
    record_count,
    source_model_id,
    source_model_content_sha256,
    vector_dtype,
    distance_order,
    distance_tie_break
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s
)
ON CONFLICT (index_id) DO NOTHING
"""
_MANIFEST_SELECT_SQL: Final[LiteralString] = """
SELECT
    index_id,
    content_sha256,
    manifest_sha256,
    artifact_schema_version,
    dataset_id,
    schema_id,
    feature_contract_version,
    preprocessor_version,
    index_version,
    configuration_version,
    dimension,
    metric,
    record_count,
    source_model_id,
    source_model_content_sha256,
    vector_dtype,
    distance_order,
    distance_tie_break
FROM similarity_indexes
WHERE index_id = %s
"""
_ENTRY_INSERT_SQL: Final[LiteralString] = """
INSERT INTO similarity_index_entries (
    index_id,
    opaque_id,
    fault_code,
    embedding,
    vector_sha256
) VALUES (%s, %s, %s, %s::public.vector, %s)
ON CONFLICT (index_id, opaque_id) DO NOTHING
"""
_ENTRIES_SELECT_SQL: Final[LiteralString] = """
SELECT opaque_id, fault_code, embedding::text AS embedding, vector_sha256
FROM similarity_index_entries
WHERE index_id = %s
ORDER BY opaque_id
"""
_ENTRY_COUNT_SQL: Final[LiteralString] = """
SELECT COUNT(*) AS record_count
FROM similarity_index_entries
WHERE index_id = %s
"""
_QUERY_SQL: Final[LiteralString] = """
SELECT
    opaque_id,
    fault_code,
    embedding OPERATOR(public.<->) %s::public.vector AS distance
FROM similarity_index_entries
WHERE index_id = %s
ORDER BY distance, opaque_id
LIMIT %s
"""
_FILTERED_QUERY_SQL: Final[LiteralString] = """
SELECT
    opaque_id,
    fault_code,
    embedding OPERATOR(public.<->) %s::public.vector AS distance
FROM similarity_index_entries
WHERE index_id = %s
  AND fault_code = ANY(%s)
ORDER BY distance, opaque_id
LIMIT %s
"""
_READ_ONLY_SNAPSHOT_SQL: Final[LiteralString] = (
    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
)

_MANIFEST_COLUMNS: Final[tuple[str, ...]] = (
    "index_id",
    "content_sha256",
    "manifest_sha256",
    "artifact_schema_version",
    "dataset_id",
    "schema_id",
    "feature_contract_version",
    "preprocessor_version",
    "index_version",
    "configuration_version",
    "dimension",
    "metric",
    "record_count",
    "source_model_id",
    "source_model_content_sha256",
    "vector_dtype",
    "distance_order",
    "distance_tie_break",
)


def install_similarity_index(
    connection: PostgresConnection,
    index: LoadedSimilarityIndex,
) -> None:
    """Install or exactly replay one verified index in a single transaction."""

    _require_idle_connection(connection)
    manifest_values = _manifest_values(index)
    vectors = index.vectors_copy()
    entry_values = tuple(
        (
            index.selector.index_id,
            record.opaque_id,
            record.fault_code,
            _vector_literal(vectors[position]),
            _vector_sha256(vectors[position]),
        )
        for position, record in enumerate(index.records)
    )
    try:
        with connection.transaction():
            connection.execute(_MANIFEST_INSERT_SQL, manifest_values)
            with connection.cursor() as cursor:
                cursor.executemany(_ENTRY_INSERT_SQL, entry_values)
            _validate_stored_index(connection, index, verify_vectors=True)
    except SimilarityIndexError:
        raise
    except Error:
        raise SimilarityIndexRepositoryError(
            "Similarity index installation failed."
        ) from None


class PostgresSimilarityIndexAdapter:
    """Exact pgvector search over one fully verified, caller-owned connection."""

    def __init__(
        self,
        connection: PostgresConnection,
        index: LoadedSimilarityIndex,
    ) -> None:
        self._connection = connection
        self._index = index
        _require_idle_connection(connection)
        try:
            with connection.transaction():
                connection.execute(_READ_ONLY_SNAPSHOT_SQL)
                _validate_stored_index(connection, index, verify_vectors=True)
        except SimilarityIndexError:
            raise
        except Error:
            raise SimilarityIndexRepositoryError(
                "Similarity index repository validation failed."
            ) from None

    def query(self, query: SimilarityQuery) -> tuple[SimilarityNeighbor, ...]:
        transformed = self._index.transformed_query(query)
        canonical = InMemorySimilarityIndexAdapter(self._index).query(query)
        query_vector = _vector_literal(transformed)
        _require_idle_connection(self._connection)
        try:
            with self._connection.transaction():
                self._connection.execute(_READ_ONLY_SNAPSHOT_SQL)
                _validate_stored_index(
                    self._connection,
                    self._index,
                    verify_vectors=False,
                )
                if query.fault_codes:
                    rows = self._connection.execute(
                        _FILTERED_QUERY_SQL,
                        (
                            query_vector,
                            self._index.selector.index_id,
                            list(query.fault_codes),
                            query.top_k,
                        ),
                    ).fetchall()
                else:
                    rows = self._connection.execute(
                        _QUERY_SQL,
                        (
                            query_vector,
                            self._index.selector.index_id,
                            query.top_k,
                        ),
                    ).fetchall()
        except SimilarityIndexError:
            raise
        except Error:
            raise SimilarityIndexRepositoryError(
                "Similarity index query failed."
            ) from None

        if len(rows) != len(canonical):
            raise SimilarityIndexRepositoryError(
                "PostgreSQL retrieval does not match the canonical index."
            )
        for row, expected in zip(rows, canonical, strict=True):
            opaque_id = _stored_text(row.get("opaque_id"))
            fault_code = _stored_text(row.get("fault_code"))
            if opaque_id != expected.opaque_id or fault_code != expected.fault_code:
                raise SimilarityIndexRepositoryError(
                    "PostgreSQL retrieval does not match the canonical index."
                )
            database_distance = _stored_distance(row.get("distance"))
            if not isclose(
                database_distance,
                expected.distance,
                rel_tol=1e-6,
                abs_tol=1e-6,
            ):
                raise SimilarityIndexRepositoryError(
                    "PostgreSQL retrieval does not match the canonical index."
                )
        return canonical


def _validate_stored_index(
    connection: PostgresConnection,
    index: LoadedSimilarityIndex,
    *,
    verify_vectors: bool,
) -> None:
    row = connection.execute(
        _MANIFEST_SELECT_SQL,
        (index.selector.index_id,),
    ).fetchone()
    if row is None:
        raise SimilarityIndexCompatibilityError(
            "The selected similarity index is not installed."
        )
    expected_values = _manifest_values(index)
    if any(
        row.get(column) != expected
        for column, expected in zip(
            _MANIFEST_COLUMNS,
            expected_values,
            strict=True,
        )
    ):
        raise SimilarityIndexCompatibilityError(
            "Stored similarity index identity or compatibility is inconsistent."
        )
    count_row = connection.execute(
        _ENTRY_COUNT_SQL,
        (index.selector.index_id,),
    ).fetchone()
    if count_row is None or count_row.get("record_count") != (
        index.manifest.record_count
    ):
        raise SimilarityIndexRepositoryError(
            "Stored similarity index record count is inconsistent."
        )
    if not verify_vectors:
        return

    stored_rows = connection.execute(
        _ENTRIES_SELECT_SQL,
        (index.selector.index_id,),
    ).fetchall()
    expected_vectors = index.vectors_copy()
    if len(stored_rows) != len(index.records):
        raise SimilarityIndexRepositoryError(
            "Stored similarity index content is incomplete."
        )
    for position, (stored, record) in enumerate(
        zip(stored_rows, index.records, strict=True)
    ):
        vector = _parse_vector(stored.get("embedding"))
        expected_vector = expected_vectors[position]
        expected_hash = _vector_sha256(expected_vector)
        if (
            stored.get("opaque_id") != record.opaque_id
            or stored.get("fault_code") != record.fault_code
            or stored.get("vector_sha256") != expected_hash
            or _vector_sha256(vector) != expected_hash
            or not np.array_equal(vector, expected_vector)
        ):
            raise SimilarityIndexRepositoryError(
                "Stored similarity index content integrity is inconsistent."
            )


def _manifest_values(index: LoadedSimilarityIndex) -> tuple[object, ...]:
    manifest = index.manifest
    compatibility = manifest.selector.compatibility
    return (
        manifest.selector.index_id,
        manifest.content_sha256,
        manifest.manifest_sha256,
        manifest.artifact_schema_version,
        compatibility.dataset_id,
        compatibility.schema_id,
        compatibility.feature_contract_version,
        compatibility.preprocessor_version,
        compatibility.index_version,
        compatibility.configuration_version,
        compatibility.dimension,
        compatibility.metric,
        manifest.record_count,
        manifest.source_model_id,
        manifest.source_model_content_sha256,
        manifest.vector_dtype,
        manifest.distance_order,
        manifest.distance_tie_break,
    )


def _vector_literal(vector: NDArray[np.generic]) -> str:
    value = np.asarray(vector, dtype=SIMILARITY_VECTOR_DTYPE, order="C")
    if value.shape != (SIMILARITY_INDEX_DIMENSION,) or not np.isfinite(value).all():
        raise SimilarityIndexRepositoryError(
            "Similarity index vector cannot be represented safely."
        )
    return "[" + ",".join(format(float(item), ".9g") for item in value) + "]"


def _parse_vector(value: object) -> FloatVector:
    if (
        not isinstance(value, str)
        or not value.startswith("[")
        or not value.endswith("]")
    ):
        raise SimilarityIndexRepositoryError(
            "Stored similarity index vector is invalid."
        )
    items = value[1:-1].split(",") if value != "[]" else []
    try:
        with np.errstate(over="ignore", invalid="ignore"):
            result = np.asarray(
                tuple(float(item) for item in items),
                dtype=SIMILARITY_VECTOR_DTYPE,
                order="C",
            )
    except (TypeError, ValueError):
        raise SimilarityIndexRepositoryError(
            "Stored similarity index vector is invalid."
        ) from None
    if result.shape != (SIMILARITY_INDEX_DIMENSION,) or not np.isfinite(result).all():
        raise SimilarityIndexRepositoryError(
            "Stored similarity index vector is invalid."
        )
    return cast(FloatVector, result)


def _vector_sha256(vector: NDArray[np.generic]) -> str:
    value = np.asarray(vector, dtype=SIMILARITY_VECTOR_DTYPE, order="C")
    if value.shape != (SIMILARITY_INDEX_DIMENSION,) or not np.isfinite(value).all():
        raise SimilarityIndexRepositoryError(
            "Similarity index vector integrity is invalid."
        )
    return sha256(value.tobytes(order="C")).hexdigest()


def _stored_text(value: object) -> str:
    if not isinstance(value, str):
        raise SimilarityIndexRepositoryError(
            "Similarity index query returned invalid metadata."
        )
    return value


def _stored_distance(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SimilarityIndexRepositoryError(
            "Similarity index query returned an invalid distance."
        )
    result = float(value)
    if not isfinite(result) or result < 0.0:
        raise SimilarityIndexRepositoryError(
            "Similarity index query returned an invalid distance."
        )
    return result


def _require_idle_connection(connection: PostgresConnection) -> None:
    if (
        connection.closed
        or connection.autocommit
        or connection.info.transaction_status is not TransactionStatus.IDLE
    ):
        raise SimilarityIndexRepositoryError(
            "Similarity index operations require an idle transactional connection."
        )
