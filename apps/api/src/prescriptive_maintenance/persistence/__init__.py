"""Minimal persistence contracts and adapters."""

from prescriptive_maintenance.persistence.memory import (
    InMemoryAnalysisRepository,
    InMemoryDocumentRepository,
    InMemoryStore,
    InMemoryUnitOfWork,
)
from prescriptive_maintenance.persistence.migrations import (
    LATEST_MIGRATION_VERSION,
    MigrationError,
    MigrationStateError,
    MigrationTargetError,
    current_version,
    downgrade,
    upgrade,
)
from prescriptive_maintenance.persistence.models import (
    AnalysisMetadata,
    ChunkReference,
    DocumentMetadata,
    DocumentVersionMetadata,
    EvidenceReference,
)
from prescriptive_maintenance.persistence.ports import (
    AnalysisRepository,
    DocumentRepository,
    PersistenceConflictError,
    PersistenceError,
    PersistenceIntegrityError,
    TransactionConflictError,
    TransactionRollbackOnlyError,
    UnitOfWork,
    UnitOfWorkStateError,
)
from prescriptive_maintenance.persistence.postgres import (
    PostgresAnalysisRepository,
    PostgresConnectionFactory,
    PostgresDocumentRepository,
    PostgresUnitOfWork,
)

__all__ = [
    "LATEST_MIGRATION_VERSION",
    "AnalysisMetadata",
    "AnalysisRepository",
    "ChunkReference",
    "DocumentMetadata",
    "DocumentRepository",
    "DocumentVersionMetadata",
    "EvidenceReference",
    "InMemoryAnalysisRepository",
    "InMemoryDocumentRepository",
    "InMemoryStore",
    "InMemoryUnitOfWork",
    "MigrationError",
    "MigrationStateError",
    "MigrationTargetError",
    "PersistenceConflictError",
    "PersistenceError",
    "PersistenceIntegrityError",
    "PostgresAnalysisRepository",
    "PostgresConnectionFactory",
    "PostgresDocumentRepository",
    "PostgresUnitOfWork",
    "TransactionConflictError",
    "TransactionRollbackOnlyError",
    "UnitOfWork",
    "UnitOfWorkStateError",
    "current_version",
    "downgrade",
    "upgrade",
]
