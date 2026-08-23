"""Entirely synthetic persistence aggregates shared by adapter tests."""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from datetime import tzinfo as DateTimeZone
from typing import ClassVar, Final, SupportsIndex, cast
from zoneinfo import ZoneInfo

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
SYNTHETIC_FORBIDDEN_PAYLOAD: Final = (
    "synthetic raw payload that must never be persisted"
)
SYNTHETIC_LYING_TIME: Final = datetime(
    2032,
    2,
    3,
    4,
    5,
    6,
    789012,
    tzinfo=UTC,
    fold=1,
)
_SYNTHETIC_REPORTED_TIME: Final = datetime(
    2033,
    7,
    1,
    23,
    59,
    58,
    123456,
    tzinfo=timezone(timedelta(hours=14)),
)
_SYNTHETIC_AMBIGUOUS_ZONE: Final = ZoneInfo("America/New_York")
SYNTHETIC_AMBIGUOUS_TIME: Final = datetime(
    2021,
    11,
    7,
    1,
    30,
    45,
    654321,
    tzinfo=_SYNTHETIC_AMBIGUOUS_ZONE,
    fold=1,
)
# CPython stores ``fold`` in the high month bit; only the day byte is invalid.
_SYNTHETIC_INVALID_CIVIL_STATE: Final = bytes(
    (0x07, 0xF0, 0x82, 0x00, 0x04, 0x05, 0x06, 0x0C, 0x0A, 0x14)
)
_DATETIME_FROM_STATE: Final = cast(
    Callable[[type[datetime], bytes, DateTimeZone], datetime],
    datetime.__new__,
)


class SyntheticTaintedStr(str):
    """Accepted string subtype carrying forbidden caller-owned state."""

    raw_content: str


class SyntheticTaintedDateTime(datetime):
    """Accepted datetime subtype carrying forbidden caller-owned state."""

    raw_content: str


class LyingDateTime(datetime):
    """Synthetic subtype whose virtual surface reports a different instant."""

    raw_content: str
    virtual_reads: list[str]

    def _record(self, member: str) -> None:
        self.virtual_reads.append(member)

    @property
    def year(self) -> int:
        self._record("year")
        return _SYNTHETIC_REPORTED_TIME.year

    @property
    def month(self) -> int:
        self._record("month")
        return _SYNTHETIC_REPORTED_TIME.month

    @property
    def day(self) -> int:
        self._record("day")
        return _SYNTHETIC_REPORTED_TIME.day

    @property
    def hour(self) -> int:
        self._record("hour")
        return _SYNTHETIC_REPORTED_TIME.hour

    @property
    def minute(self) -> int:
        self._record("minute")
        return _SYNTHETIC_REPORTED_TIME.minute

    @property
    def second(self) -> int:
        self._record("second")
        return _SYNTHETIC_REPORTED_TIME.second

    @property
    def microsecond(self) -> int:
        self._record("microsecond")
        return _SYNTHETIC_REPORTED_TIME.microsecond

    @property
    def fold(self) -> int:
        self._record("fold")
        return _SYNTHETIC_REPORTED_TIME.fold

    @property
    def tzinfo(self) -> DateTimeZone | None:
        self._record("tzinfo")
        return _SYNTHETIC_REPORTED_TIME.tzinfo

    def utcoffset(self) -> timedelta | None:
        self._record("utcoffset")
        return datetime.utcoffset(_SYNTHETIC_REPORTED_TIME)

    def timestamp(self) -> float:
        self._record("timestamp")
        return datetime.timestamp(_SYNTHETIC_REPORTED_TIME)

    def astimezone(self, tz: DateTimeZone | None = None) -> datetime:
        self._record("astimezone")
        return datetime.astimezone(_SYNTHETIC_REPORTED_TIME, tz)

    def isoformat(self, sep: str = "T", timespec: str = "auto") -> str:
        self._record("isoformat")
        return datetime.isoformat(_SYNTHETIC_REPORTED_TIME, sep, timespec)

    def __reduce_ex__(
        self,
        protocol: SupportsIndex,
    ) -> str | tuple[object, ...]:
        self._record("__reduce_ex__")
        return cast(
            str | tuple[object, ...],
            datetime.__reduce_ex__(_SYNTHETIC_REPORTED_TIME, protocol),
        )


class AmbiguousZoneInfoDateTime(LyingDateTime):
    """Ambiguous ZoneInfo value with hostile virtual civil-time members."""

    def toordinal(self) -> int:
        self._record("toordinal")
        return datetime.toordinal(_SYNTHETIC_REPORTED_TIME)


class ReducerPayloadZone(DateTimeZone):
    """Reducer payload that records any attempted timezone execution."""

    virtual_reads: list[str]

    def __init__(self) -> None:
        self.virtual_reads = []

    def utcoffset(self, value: datetime | None) -> timedelta | None:
        del value
        self.virtual_reads.append("utcoffset")
        raise AssertionError("Invalid civil state reached its timezone payload.")

    def dst(self, value: datetime | None) -> timedelta | None:
        del value
        self.virtual_reads.append("dst")
        raise AssertionError("Invalid civil state reached its timezone payload.")

    def tzname(self, value: datetime | None) -> str | None:
        del value
        self.virtual_reads.append("tzname")
        raise AssertionError("Invalid civil state reached its timezone payload.")


class InvalidCivilDateTime(datetime):
    """Datetime with a day-zero CPython state and executable reducer traps."""

    reducer_callable_reads: ClassVar[list[str]] = []
    virtual_reads: list[str]

    def __new__(
        cls,
        *args: object,
        **kwargs: object,
    ) -> "InvalidCivilDateTime":
        del args, kwargs
        cls.reducer_callable_reads.append("__new__")
        raise AssertionError("The reducer callable must never be executed.")

    def __reduce_ex__(self, protocol: SupportsIndex) -> tuple[object, ...]:
        del protocol
        self.virtual_reads.append("__reduce_ex__")
        raise AssertionError("The virtual reducer must never be executed.")


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


def _tainted_text(value: str) -> SyntheticTaintedStr:
    tainted = SyntheticTaintedStr(value)
    tainted.raw_content = SYNTHETIC_FORBIDDEN_PAYLOAD
    return tainted


def _tainted_datetime(value: datetime) -> SyntheticTaintedDateTime:
    local_value = value.astimezone(timezone(timedelta(hours=-3)))
    tainted = SyntheticTaintedDateTime(
        local_value.year,
        local_value.month,
        local_value.day,
        local_value.hour,
        local_value.minute,
        local_value.second,
        local_value.microsecond,
        tzinfo=local_value.tzinfo,
        fold=local_value.fold,
    )
    tainted.raw_content = SYNTHETIC_FORBIDDEN_PAYLOAD
    return tainted


def _lying_datetime() -> LyingDateTime:
    value = LyingDateTime(
        2032,
        2,
        3,
        4,
        5,
        6,
        789012,
        tzinfo=UTC,
        fold=1,
    )
    value.raw_content = SYNTHETIC_FORBIDDEN_PAYLOAD
    value.virtual_reads = []
    return value


def _ambiguous_zoneinfo_datetime() -> AmbiguousZoneInfoDateTime:
    value = AmbiguousZoneInfoDateTime(
        2021,
        11,
        7,
        1,
        30,
        45,
        654321,
        tzinfo=_SYNTHETIC_AMBIGUOUS_ZONE,
        fold=1,
    )
    value.raw_content = SYNTHETIC_FORBIDDEN_PAYLOAD
    value.virtual_reads = []
    return value


def synthetic_ambiguous_zoneinfo_document() -> tuple[
    DocumentMetadata,
    AmbiguousZoneInfoDateTime,
]:
    """Place one hostile ambiguous ZoneInfo timestamp at the adapter boundary."""

    source = _ambiguous_zoneinfo_datetime()
    document = replace(
        SYNTHETIC_DOCUMENT,
        document_id="doc_synthetic_ambiguous_zone",
        created_at=source,
        versions=(),
    )
    return document, source


def synthetic_invalid_civil_datetime_document() -> tuple[
    DocumentMetadata,
    InvalidCivilDateTime,
    ReducerPayloadZone,
]:
    """Inject a day-zero state after model construction for adapter validation."""

    zone = ReducerPayloadZone()
    InvalidCivilDateTime.reducer_callable_reads.clear()
    source = cast(
        InvalidCivilDateTime,
        _DATETIME_FROM_STATE(
            InvalidCivilDateTime,
            _SYNTHETIC_INVALID_CIVIL_STATE,
            zone,
        ),
    )
    source.virtual_reads = []
    document = replace(
        SYNTHETIC_DOCUMENT,
        document_id="doc_synthetic_invalid_civil",
        versions=(),
    )
    object.__setattr__(document, "created_at", source)
    return document, source, zone


def synthetic_tainted_scalar_aggregates() -> tuple[
    DocumentMetadata,
    AnalysisMetadata,
]:
    """Build every persisted scalar field with accepted synthetic subtypes."""

    versions = tuple(
        DocumentVersionMetadata(
            document_version_id=_tainted_text(version.document_version_id),
            document_id=_tainted_text(version.document_id),
            source_sha256=_tainted_text(version.source_sha256),
            created_at=_tainted_datetime(version.created_at),
            chunks=tuple(
                ChunkReference(
                    chunk_ref=_tainted_text(chunk.chunk_ref),
                    document_id=_tainted_text(chunk.document_id),
                    document_version_id=_tainted_text(chunk.document_version_id),
                    page_number=chunk.page_number,
                )
                for chunk in version.chunks
            ),
        )
        for version in SYNTHETIC_DOCUMENT.versions
    )
    document = DocumentMetadata(
        document_id=_tainted_text(SYNTHETIC_DOCUMENT.document_id),
        created_at=_tainted_datetime(SYNTHETIC_DOCUMENT.created_at),
        versions=versions,
    )
    analysis = AnalysisMetadata(
        analysis_id=_tainted_text(SYNTHETIC_ANALYSIS.analysis_id),
        outcome=SYNTHETIC_ANALYSIS.outcome,
        dataset_id=_tainted_text(SYNTHETIC_ANALYSIS.dataset_id),
        model_id=_tainted_text(SYNTHETIC_ANALYSIS.model_id),
        prompt_id=_tainted_text(SYNTHETIC_ANALYSIS.prompt_id),
        configuration_id=_tainted_text(SYNTHETIC_ANALYSIS.configuration_id),
        created_at=_tainted_datetime(SYNTHETIC_ANALYSIS.created_at),
        evidence_references=tuple(
            EvidenceReference(
                evidence_id=_tainted_text(reference.evidence_id),
                document_id=_tainted_text(reference.document_id),
                document_version_id=_tainted_text(reference.document_version_id),
                chunk_ref=_tainted_text(reference.chunk_ref),
                ordinal=reference.ordinal,
            )
            for reference in SYNTHETIC_ANALYSIS.evidence_references
        ),
    )
    return document, analysis


def synthetic_lying_datetime_aggregates() -> tuple[
    DocumentMetadata,
    AnalysisMetadata,
    tuple[LyingDateTime, ...],
]:
    """Place hostile datetime subclasses in every persisted timestamp field."""

    document_created_at = _lying_datetime()
    version_created_at = tuple(_lying_datetime() for _ in SYNTHETIC_DOCUMENT.versions)
    analysis_created_at = _lying_datetime()
    versions = tuple(
        replace(version, created_at=created_at)
        for version, created_at in zip(
            SYNTHETIC_DOCUMENT.versions,
            version_created_at,
            strict=True,
        )
    )
    document = replace(
        SYNTHETIC_DOCUMENT,
        created_at=document_created_at,
        versions=versions,
    )
    analysis = replace(SYNTHETIC_ANALYSIS, created_at=analysis_created_at)
    return (
        document,
        analysis,
        (document_created_at, *version_created_at, analysis_created_at),
    )


def assert_persisted_scalars_are_canonical(
    document: DocumentMetadata,
    analysis: AnalysisMetadata,
) -> None:
    """Assert base scalar types recursively across the persistence shape."""

    versions = document.versions
    chunks = tuple(chunk for version in versions for chunk in version.chunks)
    references = analysis.evidence_references
    text_values = (
        document.document_id,
        *(version.document_version_id for version in versions),
        *(version.document_id for version in versions),
        *(version.source_sha256 for version in versions),
        *(chunk.chunk_ref for chunk in chunks),
        *(chunk.document_id for chunk in chunks),
        *(chunk.document_version_id for chunk in chunks),
        analysis.analysis_id,
        analysis.dataset_id,
        analysis.model_id,
        analysis.prompt_id,
        analysis.configuration_id,
        *(reference.evidence_id for reference in references),
        *(reference.document_id for reference in references),
        *(reference.document_version_id for reference in references),
        *(reference.chunk_ref for reference in references),
    )
    datetime_values = (
        document.created_at,
        *(version.created_at for version in versions),
        analysis.created_at,
    )
    integer_values = (
        *(chunk.page_number for chunk in chunks),
        *(reference.ordinal for reference in references),
    )
    scalar_values: tuple[object, ...] = (
        *text_values,
        *datetime_values,
        *integer_values,
        analysis.outcome,
    )

    assert all(type(value) is str for value in text_values)
    assert all(type(value) is datetime for value in datetime_values)
    assert all(type(value) is int for value in integer_values)
    assert type(analysis.outcome) is AnalysisOutcome
    assert all(not hasattr(value, "raw_content") for value in scalar_values)


def assert_lying_datetimes_are_canonical(
    document: DocumentMetadata,
    analysis: AnalysisMetadata,
    sources: tuple[LyingDateTime, ...],
) -> None:
    """Assert hostile virtual members were ignored and the instant survived."""

    persisted = (
        document.created_at,
        *(version.created_at for version in document.versions),
        analysis.created_at,
    )
    expected_epoch = datetime.timestamp(SYNTHETIC_LYING_TIME)

    assert all(type(value) is datetime for value in persisted)
    assert all(value == SYNTHETIC_LYING_TIME for value in persisted)
    assert all(value.tzinfo is UTC for value in persisted)
    assert all(value.microsecond == 789012 for value in persisted)
    assert all(datetime.timestamp(value) == expected_epoch for value in persisted)
    assert all(not hasattr(value, "raw_content") for value in persisted)
    assert all(source.virtual_reads == [] for source in sources)


def assert_ambiguous_zoneinfo_datetime_is_canonical(
    document: DocumentMetadata,
    source: AmbiguousZoneInfoDateTime,
) -> None:
    """Assert ZoneInfo received only safe base clones of an ambiguous instant."""

    persisted = document.created_at
    expected_utc = datetime.astimezone(SYNTHETIC_AMBIGUOUS_TIME, UTC)
    expected_epoch = datetime.timestamp(SYNTHETIC_AMBIGUOUS_TIME)

    assert type(persisted) is datetime
    assert persisted == expected_utc
    assert persisted.tzinfo is UTC
    assert persisted.microsecond == 654321
    assert datetime.timestamp(persisted) == expected_epoch
    assert not hasattr(persisted, "raw_content")
    assert source.virtual_reads == []
