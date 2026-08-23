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
