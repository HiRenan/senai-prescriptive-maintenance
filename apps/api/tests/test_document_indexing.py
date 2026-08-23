"""Entirely synthetic tests for deterministic document chunk indexing."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from unicodedata import category as unicode_category

import pytest
from prescriptive_maintenance.contracts import Citation
from prescriptive_maintenance.data.document_indexing import (
    DOCUMENT_REPRESENTATION_VERSION,
    ChunkEmbedding,
    ChunkIdentityCollisionError,
    ChunkingConfiguration,
    DocumentChunk,
    DocumentChunkingError,
    DocumentIndexingStatus,
    EmbeddingStatus,
    IndexedChunk,
    InMemoryChunkRepository,
    LocalHashEmbeddingProvider,
    PgVectorChunkRepository,
    PgVectorRow,
    chunk_extracted_document,
    index_extracted_document,
)

_SYNTHETIC_SOURCE_HASH = "a" * 64


def _documented_cleanup(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.translate(
        str.maketrans(
            {"\x00": None, "\x08": None, "\x0b": "\n", "\x0c": "\n", "\x7f": None}
        )
    )
    lines = [line.rstrip(" \t") for line in normalized.split("\n")]
    cleaned_lines: list[str] = []
    blank_count = 0
    for line in lines:
        if line:
            blank_count = 0
            cleaned_lines.append(line)
        else:
            blank_count += 1
            if blank_count <= 2:
                cleaned_lines.append("")
    return "\n".join(cleaned_lines).strip("\n")


def _synthetic_page(
    page_number: int,
    text: str | None,
    *,
    method: str = "native",
    status: str = "extracted",
    failure_code: str | None = None,
    quality_signals: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "page_number": page_number,
        "method": method,
        "status": status,
        "text": text,
        "native_quality": {"signals": []},
        "quality": {"signals": list(quality_signals)},
        "ocr_trigger_codes": [],
        "failure_code": failure_code,
    }


def _synthetic_extraction(
    pages: list[dict[str, object]],
    *,
    document_status: str = "completed",
    source_hash: str = _SYNTHETIC_SOURCE_HASH,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "extractor_version": 2,
        "tooling": {
            "pypdfium2": "5.13.0",
            "ocr_adapter": {
                "configured": False,
                "name": None,
                "version": None,
            },
        },
        "source": {
            "name": "SyntheticManual.pdf",
            "source_version": f"sha256:{source_hash}",
            "size_bytes": 4_096,
            "sha256": source_hash,
            "pdf_version": "1.7",
        },
        "status": document_status,
        "failure_code": None,
        "page_count": len(pages),
        "pages": pages,
    }


@dataclass(slots=True)
class _PartialEmbeddingProvider:
    failed_index: int

    @property
    def provider_id(self) -> str:
        return "synthetic-partial"

    @property
    def representation_version(self) -> str:
        return "synthetic-partial.v1"

    @property
    def dimension(self) -> int:
        return 8

    def embed(self, chunks: Sequence[DocumentChunk]) -> tuple[ChunkEmbedding, ...]:
        typed_chunks = tuple(chunks)
        successful = LocalHashEmbeddingProvider(dimension=self.dimension).embed(
            typed_chunks
        )
        outcomes: list[ChunkEmbedding] = []
        for index, outcome in enumerate(successful):
            if index == self.failed_index:
                outcomes.append(
                    ChunkEmbedding(
                        chunk_id=outcome.chunk_id,
                        provider_id=self.provider_id,
                        representation_version=self.representation_version,
                        dimension=self.dimension,
                        status=EmbeddingStatus.FAILED,
                        vector=None,
                        failure_code="embedding.synthetic_partial_failure",
                    )
                )
            else:
                outcomes.append(
                    replace(
                        outcome,
                        provider_id=self.provider_id,
                        representation_version=self.representation_version,
                    )
                )
        return tuple(outcomes)


class _FailingEmbeddingProvider:
    @property
    def provider_id(self) -> str:
        return "synthetic-failing"

    @property
    def representation_version(self) -> str:
        return "synthetic-failing.v1"

    @property
    def dimension(self) -> int:
        return 4

    def embed(self, chunks: Sequence[DocumentChunk]) -> tuple[ChunkEmbedding, ...]:
        del chunks
        raise RuntimeError("synthetic private provider detail")


@dataclass(slots=True)
class _SyntheticPgVectorWriter:
    rows: tuple[PgVectorRow, ...] = ()
    calls: int = 0

    def upsert(self, rows: Sequence[PgVectorRow]) -> None:
        self.calls += 1
        self.rows = tuple(rows)


def test_chunks_respect_page_section_limits_and_overlap() -> None:
    section_one = " ".join(f"bearing-{index:02d}" for index in range(24))
    section_two = " ".join(f"lubrication-{index:02d}" for index in range(18))
    first_page_text = (
        f"# BEARING INSPECTION\n{section_one}\n\n## LUBRICATION\n{section_two}"
    )
    extraction = _synthetic_extraction(
        [
            _synthetic_page(1, first_page_text),
            _synthetic_page(2, "Fictional second-page maintenance note."),
        ]
    )
    configuration = ChunkingConfiguration(
        max_characters=96,
        overlap_characters=18,
    )

    result = chunk_extracted_document(extraction, configuration=configuration)

    assert result.status is DocumentIndexingStatus.COMPLETED
    assert len(result.chunks) > 4
    assert all(len(chunk.content) <= 96 for chunk in result.chunks)
    assert tuple(chunk.ordinal for chunk in result.chunks) == tuple(
        range(1, len(result.chunks) + 1)
    )
    assert tuple(chunk.page_number for chunk in result.chunks) == tuple(
        sorted(chunk.page_number for chunk in result.chunks)
    )
    assert {
        chunk.section_title for chunk in result.chunks if chunk.page_number == 1
    } == {
        "BEARING INSPECTION",
        "LUBRICATION",
    }
    adjacent = zip(result.chunks, result.chunks[1:], strict=False)
    same_section_pairs = [
        (left, right) for left, right in adjacent if left.section_id == right.section_id
    ]
    assert same_section_pairs
    assert all(
        0 < left.character_end - right.character_start <= 18
        for left, right in same_section_pairs
    )
    assert all(
        first_page_text[chunk.character_start : chunk.character_end] == chunk.content
        for chunk in result.chunks
        if chunk.page_number == 1
    )


def test_cleanup_preserves_unicode_while_removing_only_technical_noise() -> None:
    text = "Medição do eixo ⚙️ e cafe\u0301.  \r\nLinha\u00a0preservada.\x00\x7f"
    extraction = _synthetic_extraction([_synthetic_page(1, text)])

    result = chunk_extracted_document(extraction)

    assert len(result.chunks) == 1
    content = result.chunks[0].content
    assert content == "Medição do eixo ⚙️ e cafe\u0301.\nLinha\u00a0preservada."
    assert "é" not in content
    assert "\u0301" in content
    assert "\x00" not in content
    assert "\x7f" not in content


def test_empty_page_is_explicit_and_does_not_hide_other_chunks() -> None:
    extraction = _synthetic_extraction(
        [
            _synthetic_page(1, None, method="none", status="ocr_required"),
            _synthetic_page(2, "Entirely synthetic available page text."),
        ],
        document_status="ocr_required",
    )
    repository = InMemoryChunkRepository()

    result = index_extracted_document(
        extraction,
        embedding_provider=LocalHashEmbeddingProvider(),
        repository=repository,
    )

    assert result.status is DocumentIndexingStatus.PARTIAL
    assert len(result.records) == 1
    assert result.records[0].chunk.page_number == 2
    assert len(result.failures) == 1
    assert result.failures[0].code == "chunking.page_text_unavailable"
    assert result.failures[0].page_number == 1
    assert result.failures[0].chunk_id is None
    assert len(repository) == 1


def test_page_without_chunk_preserves_its_original_failure_and_provenance() -> None:
    extraction = _synthetic_extraction(
        [
            _synthetic_page(
                1,
                None,
                method="ocr",
                status="failed",
                failure_code="page.ocr_failed",
                quality_signals=("text.empty",),
            ),
            _synthetic_page(2, "Entirely synthetic available page text."),
        ],
        document_status="partial",
    )

    result = chunk_extracted_document(extraction)

    assert result.status is DocumentIndexingStatus.PARTIAL
    assert tuple(chunk.page_number for chunk in result.chunks) == (2,)
    assert len(result.failures) == 1
    failure = result.failures[0]
    assert failure.code == "page.ocr_failed"
    assert failure.page_number == 1
    assert failure.chunk_id is None
    assert failure.provenance is not None
    assert failure.provenance.page_number == 1
    assert failure.provenance.page_extraction_method == "ocr"
    assert failure.provenance.page_extraction_status == "failed"
    assert failure.provenance.page_failure_code == "page.ocr_failed"
    assert failure.provenance.quality_signals == ("text.empty",)
    assert not hasattr(failure.provenance, "text")


def test_page_failure_provenance_rejects_an_unsanitized_source_code() -> None:
    extraction = _synthetic_extraction(
        [
            _synthetic_page(
                1,
                None,
                method="ocr",
                status="failed",
                failure_code="page.ocr_failed/private-detail",
            )
        ],
        document_status="failed",
    )

    with pytest.raises(DocumentChunkingError) as raised:
        chunk_extracted_document(extraction)

    assert str(raised.value) == "Structured document extraction is invalid."
    assert "private-detail" not in str(raised.value)


def test_offsets_reference_original_page_text_after_length_changing_cleanup() -> None:
    page_text = (
        "\nalpha\x00beta  \r\n\r\n\r\n\r\n"
        "gammagammagammagammagammagammagammagammagammagamma\x7f\n"
    )
    extraction = _synthetic_extraction([_synthetic_page(1, page_text)])
    configuration = ChunkingConfiguration(max_characters=36, overlap_characters=6)

    first = chunk_extracted_document(extraction, configuration=configuration)
    second = chunk_extracted_document(extraction, configuration=configuration)

    assert first == second
    assert len(first.chunks) > 1
    assert all(
        len(chunk.content) <= configuration.max_characters for chunk in first.chunks
    )
    assert first.chunks[0].character_start == page_text.index("a")
    assert first.chunks[-1].character_end == page_text.rindex("a") + 1
    for chunk in first.chunks:
        assert 0 <= chunk.character_start < chunk.character_end <= len(page_text)
        source_excerpt = page_text[chunk.character_start : chunk.character_end]
        assert _documented_cleanup(source_excerpt) == chunk.content
        assert "\x00" not in chunk.content
        assert "\x7f" not in chunk.content
    for left, right in zip(first.chunks, first.chunks[1:], strict=False):
        overlap_lengths = tuple(
            length
            for length in range(1, configuration.overlap_characters + 1)
            if left.content[-length:] == right.content[:length]
        )
        assert overlap_lengths


@pytest.mark.parametrize(
    ("prefix_length", "grapheme"),
    [
        (31, "e\u0301"),
        (31, "\u2699\ufe0f"),
        (30, "\U0001f469\u200d\U0001f527"),
    ],
)
def test_chunk_boundaries_never_split_required_grapheme_sequences(
    prefix_length: int,
    grapheme: str,
) -> None:
    page_text = f"{'x' * prefix_length}{grapheme}{'y' * 40}"
    extraction = _synthetic_extraction([_synthetic_page(1, page_text)])
    configuration = ChunkingConfiguration(max_characters=32, overlap_characters=0)

    result = chunk_extracted_document(extraction, configuration=configuration)

    assert all(
        len(chunk.content) <= configuration.max_characters for chunk in result.chunks
    )
    assert any(grapheme in chunk.content for chunk in result.chunks)
    chunk_boundaries = {
        boundary
        for chunk in result.chunks
        for boundary in (chunk.character_start, chunk.character_end)
    }
    grapheme_start = page_text.index(grapheme)
    assert chunk_boundaries.isdisjoint(
        range(grapheme_start + 1, grapheme_start + len(grapheme))
    )
    assert all(
        not (
            unicode_category(page_text[boundary]).startswith("M")
            or page_text[boundary] == "\u200d"
            or page_text[boundary - 1] == "\u200d"
        )
        for boundary in chunk_boundaries
        if 0 < boundary < len(page_text)
    )


def test_one_grapheme_longer_than_the_limit_remains_indivisible() -> None:
    grapheme = "a" + "\u200db" * 20
    page_text = f"{grapheme}tail"
    extraction = _synthetic_extraction([_synthetic_page(1, page_text)])
    configuration = ChunkingConfiguration(max_characters=32, overlap_characters=0)

    result = chunk_extracted_document(extraction, configuration=configuration)

    assert result.chunks[0].content == grapheme
    assert len(result.chunks[0].content) > configuration.max_characters
    assert result.chunks[0].character_start == 0
    assert result.chunks[0].character_end == len(grapheme)
    assert result.chunks[1].content == "tail"


def test_failed_extraction_without_pages_preserves_the_source_failure() -> None:
    extraction = _synthetic_extraction([], document_status="failed")
    extraction["page_count"] = None
    extraction["failure_code"] = "document.pdf_unreadable"

    result = chunk_extracted_document(extraction)

    assert result.status is DocumentIndexingStatus.FAILED
    assert result.document_extraction_status == "failed"
    assert result.document_extraction_failure_code == "document.pdf_unreadable"
    assert result.chunks == ()
    assert result.failures[0].code == "chunking.document_text_unavailable"


def test_sections_never_cross_boundaries_and_repeated_text_has_distinct_ids() -> None:
    repeated = "Entirely synthetic repeated maintenance sentence."
    extraction = _synthetic_extraction(
        [
            _synthetic_page(
                1,
                f"# FIRST SECTION\n{repeated}\n## SECOND SECTION\n{repeated}",
            ),
            _synthetic_page(2, f"# FIRST SECTION\n{repeated}"),
        ]
    )

    result = chunk_extracted_document(extraction)

    assert len(result.chunks) == 3
    assert len({chunk.chunk_id for chunk in result.chunks}) == 3
    assert len({chunk.section_id for chunk in result.chunks}) == 3
    assert all(
        "SECOND SECTION" not in chunk.content
        for chunk in result.chunks
        if chunk.section_title == "FIRST SECTION"
    )
    repeated_hashes = {
        chunk.content_sha256 for chunk in result.chunks if repeated in chunk.content
    }
    assert len(repeated_hashes) == 2


def test_configuration_changes_chunk_identity_even_with_equal_content() -> None:
    extraction = _synthetic_extraction(
        [_synthetic_page(1, "Entirely synthetic short maintenance text.")]
    )
    first_configuration = ChunkingConfiguration(
        max_characters=96,
        overlap_characters=8,
    )
    second_configuration = ChunkingConfiguration(
        max_characters=96,
        overlap_characters=9,
    )

    first = chunk_extracted_document(
        extraction,
        configuration=first_configuration,
    )
    second = chunk_extracted_document(
        extraction,
        configuration=second_configuration,
    )

    assert first.chunks[0].content == second.chunks[0].content
    assert first.chunks[0].content_sha256 == second.chunks[0].content_sha256
    assert first.chunks[0].chunk_id != second.chunks[0].chunk_id
    assert first.chunks[0].chunking_configuration_id != (
        second.chunks[0].chunking_configuration_id
    )


def test_chunk_id_collision_is_explicit_and_never_overwrites(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extraction = _synthetic_extraction(
        [_synthetic_page(1, " ".join(f"synthetic-{index:03d}" for index in range(80)))]
    )
    configuration = ChunkingConfiguration(
        max_characters=80,
        overlap_characters=10,
    )

    def constant_chunk_id(**kwargs: object) -> str:
        del kwargs
        return f"chunk_{'0' * 64}"

    monkeypatch.setattr(
        "prescriptive_maintenance.data.document_indexing._build_chunk_id",
        constant_chunk_id,
    )

    result = chunk_extracted_document(extraction, configuration=configuration)

    assert result.status is DocumentIndexingStatus.PARTIAL
    assert len(result.chunks) == 1
    assert result.failures
    assert all(
        failure.code == "chunking.chunk_id_collision" for failure in result.failures
    )


def test_partial_embedding_failure_preserves_every_chunk_and_provenance() -> None:
    extraction = _synthetic_extraction(
        [
            _synthetic_page(
                1,
                "Entirely synthetic page one.",
                method="ocr",
                status="suspect",
                failure_code="page.synthetic_review",
                quality_signals=("ocr.low_mean_confidence",),
            ),
            _synthetic_page(2, "Entirely synthetic page two."),
        ],
        document_status="attention_required",
    )
    repository = InMemoryChunkRepository()

    result = index_extracted_document(
        extraction,
        embedding_provider=_PartialEmbeddingProvider(failed_index=1),
        repository=repository,
    )

    assert result.status is DocumentIndexingStatus.PARTIAL
    assert len(result.records) == 2
    assert len(repository) == 2
    assert [record.embedding.status for record in result.records] == [
        EmbeddingStatus.EMBEDDED,
        EmbeddingStatus.FAILED,
    ]
    assert result.failures[-1].code == "embedding.synthetic_partial_failure"
    first = result.records[0].chunk
    assert first.provenance.source_sha256 == _SYNTHETIC_SOURCE_HASH
    assert first.provenance.page_extraction_method == "ocr"
    assert first.provenance.page_extraction_status == "suspect"
    assert first.provenance.page_failure_code == "page.synthetic_review"
    assert first.provenance.quality_signals == ("ocr.low_mean_confidence",)
    assert not hasattr(result, "approved")


def test_provider_failure_is_sanitized_and_chunks_remain_queryable() -> None:
    private_detail = "synthetic private provider detail"
    extraction = _synthetic_extraction(
        [_synthetic_page(1, "Entirely synthetic text for provider failure.")]
    )
    repository = InMemoryChunkRepository()

    result = index_extracted_document(
        extraction,
        embedding_provider=_FailingEmbeddingProvider(),
        repository=repository,
    )

    assert result.status is DocumentIndexingStatus.FAILED
    assert len(result.records) == 1
    assert len(repository) == 1
    assert result.records[0].embedding.vector is None
    assert result.failures[0].code == "embedding.provider_failed"
    assert private_detail not in repr(result)


def test_local_provider_and_memory_repository_are_ordered_and_idempotent() -> None:
    extraction = _synthetic_extraction(
        [
            _synthetic_page(1, "# ALPHA\nEntirely synthetic first section."),
            _synthetic_page(2, "# BETA\nEntirely synthetic second section."),
        ]
    )
    provider = LocalHashEmbeddingProvider(dimension=16)
    repository = InMemoryChunkRepository()

    first = index_extracted_document(
        extraction,
        embedding_provider=provider,
        repository=repository,
    )
    second = index_extracted_document(
        extraction,
        embedding_provider=provider,
        repository=repository,
    )

    assert first == second
    assert len(repository) == len(first.records)
    stored = repository.list_by_document(
        first.document_id,
        document_version=first.document_version,
    )
    assert stored == first.records
    assert all(
        record.embedding.provider_id == "fake-local-hash"
        and record.embedding.representation_version
        == f"{DOCUMENT_REPRESENTATION_VERSION}.d16"
        for record in stored
    )
    assert all(
        record.embedding.vector is not None and len(record.embedding.vector) == 16
        for record in stored
    )


def test_repository_rejects_a_conflicting_record_with_the_same_identity() -> None:
    extraction = _synthetic_extraction(
        [_synthetic_page(1, "Entirely synthetic collision guard text.")]
    )
    repository = InMemoryChunkRepository()
    result = index_extracted_document(
        extraction,
        embedding_provider=LocalHashEmbeddingProvider(),
        repository=repository,
    )
    record = result.records[0]
    conflicting = IndexedChunk(
        chunk=replace(record.chunk, content="Different synthetic content."),
        embedding=record.embedding,
    )

    with pytest.raises(ChunkIdentityCollisionError):
        repository.save((conflicting,))

    assert repository.list_by_document(result.document_id) == result.records


def test_pgvector_repository_uses_only_the_injected_writer() -> None:
    extraction = _synthetic_extraction(
        [_synthetic_page(1, "Entirely synthetic pgvector boundary text.")]
    )
    writer = _SyntheticPgVectorWriter()

    result = index_extracted_document(
        extraction,
        embedding_provider=LocalHashEmbeddingProvider(dimension=6),
        repository=PgVectorChunkRepository(writer=writer),
    )

    assert writer.calls == 1
    assert len(writer.rows) == len(result.records) == 1
    row = writer.rows[0]
    assert row.chunk_id == result.records[0].chunk.chunk_id
    assert row.embedding == result.records[0].embedding.vector
    assert row.source_sha256 == _SYNTHETIC_SOURCE_HASH
    assert row.source_version == f"sha256:{_SYNTHETIC_SOURCE_HASH}"
    assert row.page_extraction_method == "native"
    assert row.character_end > row.character_start
    assert row.embedding_dimension == 6
    assert row.embedding_status == "embedded"


def test_generated_ids_are_compatible_with_existing_citation_contract() -> None:
    extraction = _synthetic_extraction(
        [_synthetic_page(1, "Entirely synthetic auditable citation text.")]
    )
    chunk = chunk_extracted_document(extraction).chunks[0]

    citation = Citation(
        document_id=chunk.document_id,
        document_version=chunk.document_version,
        chunk=chunk.chunk_id,
        page_number=chunk.page_number,
    )

    assert citation.chunk == chunk.chunk_id
    assert "syntheticmanual" not in chunk.document_id.lower()
    assert "syntheticmanual" not in chunk.chunk_id.lower()
    assert len(chunk.content_sha256) == 64


def test_invalid_structured_extraction_fails_without_exposing_source_values() -> None:
    extraction = _synthetic_extraction(
        [_synthetic_page(1, "Entirely synthetic invalid payload text.")]
    )
    extraction["page_count"] = 9

    with pytest.raises(DocumentChunkingError) as raised:
        chunk_extracted_document(extraction)

    assert str(raised.value) == "Structured document extraction is invalid."
    assert "SyntheticManual.pdf" not in str(raised.value)
