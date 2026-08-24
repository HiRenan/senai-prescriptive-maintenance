"""Small versioned migration runner for the persistence module."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Final, LiteralString, cast

from psycopg import Connection

type PostgresRow = dict[str, object]
type PostgresConnection = Connection[PostgresRow]

_HISTORY_TABLE: Final = "prescriptive_schema_migrations"
_HISTORY_TABLE_SQL: Final[LiteralString] = """
CREATE TABLE IF NOT EXISTS prescriptive_schema_migrations (
    version SMALLINT PRIMARY KEY CHECK (version > 0),
    name TEXT NOT NULL CHECK (name <> ''),
    checksum TEXT NOT NULL CHECK (checksum ~ '^[0-9a-f]{64}$'),
    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""
_HISTORY_INSERT_SQL: Final[LiteralString] = """
INSERT INTO prescriptive_schema_migrations (version, name, checksum)
VALUES (%s, %s, %s)
"""
_HISTORY_DELETE_SQL: Final[LiteralString] = """
DELETE FROM prescriptive_schema_migrations WHERE version = %s
"""
_HISTORY_SELECT_SQL: Final[LiteralString] = """
SELECT version, name, checksum
FROM prescriptive_schema_migrations
ORDER BY version
"""
_HISTORY_DROP_SQL: Final[LiteralString] = "DROP TABLE prescriptive_schema_migrations"
_MIGRATION_LOCK_SQL: Final[LiteralString] = "SELECT pg_advisory_xact_lock(%s)"
_MIGRATION_LOCK_ID: Final = int.from_bytes(
    sha256(b"prescriptive-maintenance:schema-migrations").digest()[:8],
    byteorder="big",
    signed=True,
)

_INITIAL_UP: Final[tuple[LiteralString, ...]] = (
    """
    CREATE TABLE analyses (
        analysis_id TEXT PRIMARY KEY
            CHECK (analysis_id ~ '^ana_[a-z0-9_]{3,64}$'),
        outcome TEXT NOT NULL
            CHECK (outcome IN (
                'normal',
                'documented_fault',
                'undocumented_fault',
                'out_of_distribution',
                'degraded'
            )),
        dataset_id TEXT NOT NULL
            CHECK (dataset_id ~ '^[0-9a-f]{64}$'),
        model_id TEXT NOT NULL
            CHECK (model_id ~ '^model_[a-z0-9_.-]{3,64}$'),
        prompt_id TEXT NOT NULL
            CHECK (prompt_id ~ '^prompt_[a-z0-9_.-]{3,64}$'),
        configuration_id TEXT NOT NULL
            CHECK (configuration_id ~ '^config_[a-z0-9_.-]{3,64}$'),
        created_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE documents (
        document_id TEXT PRIMARY KEY
            CHECK (document_id ~ '^doc_[a-z0-9_]{3,64}$'),
        created_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE document_versions (
        document_version_id TEXT PRIMARY KEY
            CHECK (document_version_id ~ '^docver_[a-z0-9_]{3,64}$'),
        document_id TEXT NOT NULL,
        source_sha256 TEXT NOT NULL
            CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
        created_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT document_versions_document_fk
            FOREIGN KEY (document_id)
            REFERENCES documents (document_id)
            ON DELETE RESTRICT,
        CONSTRAINT document_versions_document_version_unique
            UNIQUE (document_id, document_version_id),
        CONSTRAINT document_versions_document_hash_unique
            UNIQUE (document_id, source_sha256)
    )
    """,
    """
    CREATE TABLE chunk_references (
        chunk_ref TEXT PRIMARY KEY
            CHECK (chunk_ref ~ '^chunk_[a-z0-9_]{3,64}$'),
        document_id TEXT NOT NULL,
        document_version_id TEXT NOT NULL,
        page_number INTEGER NOT NULL CHECK (page_number > 0),
        CONSTRAINT chunk_references_version_fk
            FOREIGN KEY (document_id, document_version_id)
            REFERENCES document_versions (document_id, document_version_id)
            ON DELETE RESTRICT,
        CONSTRAINT chunk_references_locator_unique
            UNIQUE (document_id, document_version_id, chunk_ref)
    )
    """,
    """
    CREATE TABLE evidence_references (
        evidence_id TEXT NOT NULL
            CHECK (evidence_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
        analysis_id TEXT NOT NULL,
        document_id TEXT NOT NULL,
        document_version_id TEXT NOT NULL,
        chunk_ref TEXT NOT NULL,
        ordinal INTEGER NOT NULL CHECK (ordinal > 0),
        PRIMARY KEY (analysis_id, evidence_id),
        CONSTRAINT evidence_references_analysis_fk
            FOREIGN KEY (analysis_id)
            REFERENCES analyses (analysis_id)
            ON DELETE RESTRICT,
        CONSTRAINT evidence_references_chunk_fk
            FOREIGN KEY (document_id, document_version_id, chunk_ref)
            REFERENCES chunk_references (
                document_id,
                document_version_id,
                chunk_ref
            )
            ON DELETE RESTRICT,
        CONSTRAINT evidence_references_analysis_ordinal_unique
            UNIQUE (analysis_id, ordinal)
    )
    """,
)

_INITIAL_DOWN: Final[tuple[LiteralString, ...]] = (
    "DROP TABLE evidence_references",
    "DROP TABLE chunk_references",
    "DROP TABLE document_versions",
    "DROP TABLE documents",
    "DROP TABLE analyses",
)

_SIMILARITY_INDEX_UP: Final[tuple[LiteralString, ...]] = (
    """
    CREATE TABLE similarity_indexes (
        index_id TEXT PRIMARY KEY
            CHECK (index_id ~ '^similarity_index_v1_[0-9a-f]{32}$'),
        content_sha256 TEXT NOT NULL
            CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
        manifest_sha256 TEXT NOT NULL
            CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
        artifact_schema_version SMALLINT NOT NULL
            CHECK (artifact_schema_version > 0),
        dataset_id TEXT NOT NULL
            CHECK (dataset_id ~ '^[0-9a-f]{64}$'),
        schema_id TEXT NOT NULL
            CHECK (schema_id ~ '^[0-9a-f]{64}$'),
        feature_contract_version SMALLINT NOT NULL
            CHECK (feature_contract_version > 0),
        preprocessor_version TEXT NOT NULL
            CHECK (preprocessor_version ~ '^[a-z0-9][a-z0-9_.-]{2,79}$'),
        index_version TEXT NOT NULL
            CHECK (index_version ~ '^[a-z0-9][a-z0-9_.-]{2,79}$'),
        configuration_version TEXT NOT NULL
            CHECK (configuration_version ~ '^[a-z0-9][a-z0-9_.-]{2,79}$'),
        dimension SMALLINT NOT NULL CHECK (dimension = 18),
        metric TEXT NOT NULL
            CHECK (metric ~ '^[a-z0-9][a-z0-9_.-]{2,79}$'),
        record_count INTEGER NOT NULL CHECK (record_count > 0),
        source_model_id TEXT NOT NULL
            CHECK (source_model_id ~ '^model_[a-z0-9_.-]{3,64}$'),
        source_model_content_sha256 TEXT NOT NULL
            CHECK (source_model_content_sha256 ~ '^[0-9a-f]{64}$'),
        vector_dtype TEXT NOT NULL CHECK (vector_dtype <> ''),
        distance_order TEXT NOT NULL CHECK (distance_order <> ''),
        distance_tie_break TEXT NOT NULL CHECK (distance_tie_break <> '')
    )
    """,
    """
    CREATE TABLE similarity_index_entries (
        index_id TEXT NOT NULL,
        opaque_id TEXT NOT NULL
            CHECK (opaque_id ~ '^neighbor_[a-z0-9_]{3,64}$'),
        fault_code TEXT NOT NULL
            CHECK (fault_code ~ '^fault_[a-z0-9_]{3,200}$'),
        embedding public.vector(18) NOT NULL,
        vector_sha256 TEXT NOT NULL
            CHECK (vector_sha256 ~ '^[0-9a-f]{64}$'),
        PRIMARY KEY (index_id, opaque_id),
        CONSTRAINT similarity_index_entries_index_fk
            FOREIGN KEY (index_id)
            REFERENCES similarity_indexes (index_id)
            ON DELETE RESTRICT
    )
    """,
)


_DOCUMENT_LIFECYCLE_UP: Final[tuple[LiteralString, ...]] = (
    """
    CREATE TABLE document_lifecycle_registries (
        logical_document_id TEXT PRIMARY KEY,
        canonical_filename TEXT NOT NULL UNIQUE
            CHECK (
                canonical_filename = lower(canonical_filename)
                AND canonical_filename ~
                    '^[A-Za-z0-9][A-Za-z0-9._ -]{0,249}[.][Pp][Dd][Ff]$'
            ),
        display_filename TEXT NOT NULL
            CHECK (
                display_filename ~
                    '^[A-Za-z0-9][A-Za-z0-9._ -]{0,249}[.][Pp][Dd][Ff]$'
            ),
        revision INTEGER NOT NULL CHECK (revision > 0),
        current_version INTEGER CHECK (current_version > 0),
        CONSTRAINT document_lifecycle_registry_document_fk
            FOREIGN KEY (logical_document_id)
            REFERENCES documents (document_id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE document_lifecycle_versions (
        logical_document_id TEXT NOT NULL,
        version_number INTEGER NOT NULL CHECK (version_number > 0),
        document_id TEXT NOT NULL UNIQUE
            CHECK (document_id ~ '^doc_[a-z0-9_]{3,64}$'),
        document_version_id TEXT NOT NULL UNIQUE
            CHECK (document_version_id ~ '^docver_[a-z0-9_]{3,64}$'),
        media_type TEXT NOT NULL CHECK (media_type = 'application/pdf'),
        size_bytes INTEGER NOT NULL CHECK (size_bytes BETWEEN 1 AND 25000000),
        status TEXT NOT NULL CHECK (status IN (
            'received',
            'processing',
            'pending_approval',
            'approved',
            'rejected',
            'failed',
            'superseded'
        )),
        extraction_status TEXT NOT NULL CHECK (extraction_status IN (
            'pending', 'succeeded', 'failed'
        )),
        indexing_status TEXT NOT NULL CHECK (indexing_status IN (
            'pending', 'succeeded', 'failed'
        )),
        updated_at TIMESTAMPTZ NOT NULL,
        failure_step TEXT CHECK (failure_step IN ('extraction', 'indexing')),
        failure_code TEXT CHECK (
            failure_code IS NULL OR char_length(failure_code) BETWEEN 1 AND 80
        ),
        failure_reason TEXT CHECK (
            failure_reason IS NULL OR char_length(failure_reason) BETWEEN 1 AND 500
        ),
        failure_actor TEXT CHECK (
            failure_actor IS NULL OR char_length(failure_actor) BETWEEN 1 AND 200
        ),
        failure_occurred_at TIMESTAMPTZ,
        superseded_by_version INTEGER CHECK (
            superseded_by_version IS NULL
            OR superseded_by_version > version_number
        ),
        PRIMARY KEY (logical_document_id, version_number),
        CONSTRAINT document_lifecycle_version_registry_fk
            FOREIGN KEY (logical_document_id)
            REFERENCES document_lifecycle_registries (logical_document_id)
            ON DELETE RESTRICT,
        CONSTRAINT document_lifecycle_version_metadata_fk
            FOREIGN KEY (logical_document_id, document_version_id)
            REFERENCES document_versions (document_id, document_version_id)
            ON DELETE RESTRICT,
        CONSTRAINT document_lifecycle_version_superseded_fk
            FOREIGN KEY (logical_document_id, superseded_by_version)
            REFERENCES document_lifecycle_versions (
                logical_document_id,
                version_number
            )
            DEFERRABLE INITIALLY DEFERRED,
        CONSTRAINT document_lifecycle_version_failure_check CHECK (
            (
                status = 'failed'
                AND failure_step IS NOT NULL
                AND failure_code IS NOT NULL
                AND failure_reason IS NOT NULL
                AND failure_actor IS NOT NULL
                AND failure_occurred_at IS NOT NULL
            )
            OR (
                status <> 'failed'
                AND failure_step IS NULL
                AND failure_code IS NULL
                AND failure_reason IS NULL
                AND failure_actor IS NULL
                AND failure_occurred_at IS NULL
            )
        ),
        CONSTRAINT document_lifecycle_version_supersession_check CHECK (
            (status = 'superseded' AND superseded_by_version IS NOT NULL)
            OR (status <> 'superseded' AND superseded_by_version IS NULL)
        )
    )
    """,
    """
    CREATE TABLE document_lifecycle_events (
        logical_document_id TEXT NOT NULL,
        sequence INTEGER NOT NULL CHECK (sequence > 0),
        version_number INTEGER NOT NULL CHECK (version_number > 0),
        action TEXT NOT NULL CHECK (action IN (
            'registered',
            'processing_started',
            'reprocessing_started',
            'extraction_succeeded',
            'indexing_succeeded',
            'processing_failed',
            'approved',
            'rejected',
            'superseded'
        )),
        source_status TEXT CHECK (source_status IS NULL OR source_status IN (
            'received',
            'processing',
            'pending_approval',
            'approved',
            'rejected',
            'failed',
            'superseded'
        )),
        target_status TEXT NOT NULL CHECK (target_status IN (
            'received',
            'processing',
            'pending_approval',
            'approved',
            'rejected',
            'failed',
            'superseded'
        )),
        occurred_at TIMESTAMPTZ NOT NULL,
        actor TEXT NOT NULL CHECK (char_length(actor) BETWEEN 1 AND 200),
        reason TEXT CHECK (
            reason IS NULL OR char_length(reason) BETWEEN 1 AND 500
        ),
        step TEXT CHECK (step IN ('extraction', 'indexing')),
        failure_code TEXT CHECK (
            failure_code IS NULL OR char_length(failure_code) BETWEEN 1 AND 80
        ),
        PRIMARY KEY (logical_document_id, sequence),
        CONSTRAINT document_lifecycle_event_version_fk
            FOREIGN KEY (logical_document_id, version_number)
            REFERENCES document_lifecycle_versions (
                logical_document_id,
                version_number
            )
            ON DELETE RESTRICT
    )
    """,
)

_SIMILARITY_INDEX_DOWN: Final[tuple[LiteralString, ...]] = (
    "DROP TABLE similarity_index_entries",
    "DROP TABLE similarity_indexes",
)

_DOCUMENT_LIFECYCLE_DOWN: Final[tuple[LiteralString, ...]] = (
    "DROP TABLE document_lifecycle_events",
    "DROP TABLE document_lifecycle_versions",
    "DROP TABLE document_lifecycle_registries",
)


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    up: tuple[LiteralString, ...]
    down: tuple[LiteralString, ...]

    @property
    def checksum(self) -> str:
        payload = "\n-- down --\n".join(("\n".join(self.up), "\n".join(self.down)))
        return sha256(payload.encode()).hexdigest()


MIGRATIONS: Final[tuple[Migration, ...]] = (
    Migration(
        version=1,
        name="initial_analysis_metadata",
        up=_INITIAL_UP,
        down=_INITIAL_DOWN,
    ),
    Migration(
        version=2,
        name="versioned_similarity_index",
        up=_SIMILARITY_INDEX_UP,
        down=_SIMILARITY_INDEX_DOWN,
    ),
    Migration(
        version=3,
        name="document_lifecycle_registry",
        up=_DOCUMENT_LIFECYCLE_UP,
        down=_DOCUMENT_LIFECYCLE_DOWN,
    ),
)
LATEST_MIGRATION_VERSION: Final = MIGRATIONS[-1].version


class MigrationError(Exception):
    """Base class for sanitized migration failures."""


class MigrationStateError(MigrationError):
    """Applied migration metadata is inconsistent with this package."""


class MigrationTargetError(MigrationError):
    """The requested migration target is invalid for the operation."""


def upgrade(connection: PostgresConnection, *, target: int | None = None) -> None:
    """Apply every missing migration through ``target`` in one transaction."""

    selected_target = LATEST_MIGRATION_VERSION if target is None else target
    _validate_target(selected_target)
    with connection.transaction():
        _lock_migrations(connection)
        connection.execute(_HISTORY_TABLE_SQL)
        applied = _read_applied(connection)
        current = applied[-1] if applied else 0
        if selected_target < current:
            raise MigrationTargetError("Upgrade target precedes the current version.")
        for migration in MIGRATIONS:
            if current < migration.version <= selected_target:
                for statement in migration.up:
                    connection.execute(statement)
                connection.execute(
                    _HISTORY_INSERT_SQL,
                    (migration.version, migration.name, migration.checksum),
                )


def downgrade(connection: PostgresConnection, *, target: int = 0) -> None:
    """Revert migrations through ``target`` and remove empty history state."""

    _validate_target(target)
    with connection.transaction():
        _lock_migrations(connection)
        if not _history_table_exists(connection):
            if target == 0:
                return
            raise MigrationTargetError("Downgrade target is above an empty database.")

        applied = _read_applied(connection)
        current = applied[-1] if applied else 0
        if target > current:
            raise MigrationTargetError("Downgrade target exceeds the current version.")
        for migration in reversed(MIGRATIONS):
            if target < migration.version <= current:
                for statement in migration.down:
                    connection.execute(statement)
                connection.execute(_HISTORY_DELETE_SQL, (migration.version,))
        if target == 0:
            connection.execute(_HISTORY_DROP_SQL)


def current_version(connection: PostgresConnection) -> int:
    """Inspect the validated schema version without changing the schema."""

    with connection.transaction():
        if not _history_table_exists(connection):
            return 0
        applied = _read_applied(connection)
        return applied[-1] if applied else 0


def _validate_target(target: int) -> None:
    if type(target) is not int or not 0 <= target <= LATEST_MIGRATION_VERSION:
        raise MigrationTargetError("Migration target is outside the known range.")


def _lock_migrations(connection: PostgresConnection) -> None:
    connection.execute(_MIGRATION_LOCK_SQL, (_MIGRATION_LOCK_ID,))


def _history_table_exists(connection: PostgresConnection) -> bool:
    row = connection.execute(
        "SELECT to_regclass(%s) AS relation",
        (_HISTORY_TABLE,),
    ).fetchone()
    return row is not None and row["relation"] is not None


def _read_applied(connection: PostgresConnection) -> tuple[int, ...]:
    rows = connection.execute(_HISTORY_SELECT_SQL).fetchall()
    known = {migration.version: migration for migration in MIGRATIONS}
    versions: list[int] = []
    for row in rows:
        version = cast(int, row["version"])
        migration = known.get(version)
        if (
            migration is None
            or row["name"] != migration.name
            or row["checksum"] != migration.checksum
        ):
            raise MigrationStateError(
                "Applied migration metadata does not match this package."
            )
        versions.append(version)
    expected = list(range(1, len(versions) + 1))
    if versions != expected:
        raise MigrationStateError("Applied migrations are not a contiguous prefix.")
    return tuple(versions)
