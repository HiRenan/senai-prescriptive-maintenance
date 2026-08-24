"""Deterministic extractive assistant over governed synthetic chunks."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from threading import RLock
from typing import Final, Protocol, cast

import numpy as np
from numpy.typing import NDArray
from sklearn.feature_extraction.text import (  # pyright: ignore[reportMissingTypeStubs]
    TfidfVectorizer,
)
from sklearn.metrics.pairwise import (  # pyright: ignore[reportMissingTypeStubs]
    cosine_similarity,  # pyright: ignore[reportUnknownVariableType]
)

from prescriptive_maintenance.contracts import (
    MAX_TOP_K,
    AnsweredAssistantResult,
    AssistantQueryRequest,
    AssistantResponse,
    Citation,
    InsufficientEvidenceAssistantResult,
)
from prescriptive_maintenance.data.document_indexing import (
    DocumentIndexingStatus,
    InMemoryChunkRepository,
    LocalHashEmbeddingProvider,
    index_extracted_document,
)
from prescriptive_maintenance.document_lifecycle import (
    DocumentGovernanceService,
    InMemoryDocumentRepository,
    ProcessingStep,
    SystemUtcClock,
)
from prescriptive_maintenance.knowledge_retrieval import (
    ApprovedKnowledgeRetrievalService,
    KnowledgeRetrievalReason,
    RankedKnowledgeSnapshot,
    build_fault_knowledge_mapping,
)
from prescriptive_maintenance.settings import AnalysisMode

ASSISTANT_POLICY_VERSION: Final = "assistant-tfidf-cosine.v1"
ASSISTANT_SIMILARITY_THRESHOLD: Final = 0.25
ASSISTANT_TOP_K: Final = 3
ASSISTANT_CATALOG_BUDGET: Final = MAX_TOP_K
_ASSISTANT_COLLECTION: Final = "grounded-assistant"
_INSUFFICIENT_MESSAGE: Final = (
    "Não há evidência aprovada e vigente suficiente para responder com segurança."
)
_HUMAN_REVIEW_NOTICE: Final = (
    "Demonstração sintética: confirme a evidência e submeta qualquer decisão à "
    "revisão humana qualificada."
)

_SYNTHETIC_CORPUS: Final[tuple[tuple[str, str], ...]] = (
    (
        "assistant-pump.synthetic.pdf",
        "DEMONSTRAÇÃO SINTÉTICA — Para o ativo fictício Bomba Aurora-01, "
        "vibração radial elevada deve ser verificada por inspeção visual da "
        "fixação e por uma nova medição confirmatória antes de qualquer decisão "
        "de manutenção.",
    ),
    (
        "assistant-motor.synthetic.pdf",
        "DEMONSTRAÇÃO SINTÉTICA — Para o ativo fictício Motor Horizonte-02, uma "
        "temperatura de mancal acima da faixa do cenário deve levar à validação "
        "do sensor e ao registro de nova leitura; nenhuma parada é autorizada "
        "automaticamente.",
    ),
    (
        "assistant-oil.synthetic.pdf",
        "DEMONSTRAÇÃO SINTÉTICA — Para o redutor fictício Prisma-03, partículas "
        "na amostra de óleo exigem uma coleta confirmatória e revisão humana do "
        "resultado antes de definir qualquer intervenção.",
    ),
)


class AssistantUnavailableError(Exception):
    """Sanitized technical failure at the assistant boundary."""


class AssistantQueryService(Protocol):
    def query(self, request: AssistantQueryRequest) -> AssistantResponse: ...


class _CatalogScorer:
    """Select a bounded, stable catalog only after governance filtering."""

    def score(self, *, fault_class: str, chunk: object) -> float:
        del fault_class, chunk
        return 1.0


@dataclass(frozen=True, slots=True)
class _ScoredSnapshot:
    snapshot: RankedKnowledgeSnapshot
    score: float


class GroundedAssistantService:
    """Rank approved evidence and return its exact text or abstain."""

    def __init__(self, *, retrieval: ApprovedKnowledgeRetrievalService) -> None:
        self._retrieval = retrieval

    def query(self, request: AssistantQueryRequest) -> AssistantResponse:
        try:
            clean_request = AssistantQueryRequest.model_validate(
                request.model_dump(mode="python")
            )
            catalog = self._retrieval.retrieve_snapshots(
                _ASSISTANT_COLLECTION,
                top_k=ASSISTANT_CATALOG_BUDGET,
            )
        except Exception:
            raise AssistantUnavailableError(
                "The assistant retrieval boundary is unavailable."
            ) from None

        if catalog.reason is not None:
            if catalog.reason in {
                KnowledgeRetrievalReason.FAULT_CLASS_UNMAPPED,
                KnowledgeRetrievalReason.NO_APPROVED_COVERAGE,
                KnowledgeRetrievalReason.EMPTY_RANKING,
            }:
                return self._insufficient(max_score=None)
            raise AssistantUnavailableError(
                "The assistant retrieval boundary is unavailable."
            )
        if not catalog.evidence:
            return self._insufficient(max_score=None)

        ranked = self._rank(clean_request.question, catalog.evidence)
        try:
            current = self._retrieval.snapshots_are_current(
                fault_class=catalog.fault_class,
                mapping_version=catalog.mapping_version,
                mapping_sha256=catalog.mapping_sha256,
                evidence=catalog.evidence,
            )
        except Exception:
            current = None
        if current is None:
            raise AssistantUnavailableError(
                "The assistant evidence currentness check is unavailable."
            )
        if current is False:
            return self._insufficient(max_score=ranked[0].score if ranked else None)
        if not ranked:
            return self._insufficient(max_score=None)

        best = ranked[0]
        if best.score < ASSISTANT_SIMILARITY_THRESHOLD:
            return self._insufficient(max_score=best.score)
        if (
            len(ranked) > 1
            and abs(best.score - ranked[1].score) <= 1e-12
            and best.snapshot.content_sha256 != ranked[1].snapshot.content_sha256
        ):
            return self._insufficient(max_score=best.score)

        evidence = best.snapshot
        return AssistantResponse(
            root=AnsweredAssistantResult(
                status="answered",
                answer=evidence.content,
                score=best.score,
                threshold=ASSISTANT_SIMILARITY_THRESHOLD,
                policy_version=ASSISTANT_POLICY_VERSION,
                citations=(
                    Citation(
                        document_id=evidence.document_id,
                        document_version=evidence.document_version,
                        chunk=evidence.chunk_id,
                        page_number=evidence.page_number,
                    ),
                ),
                human_review_notice=_HUMAN_REVIEW_NOTICE,
            )
        )

    @staticmethod
    def _rank(
        question: str,
        evidence: tuple[RankedKnowledgeSnapshot, ...],
    ) -> tuple[_ScoredSnapshot, ...]:
        try:
            vectorizer = TfidfVectorizer(
                lowercase=True,
                strip_accents="unicode",
                ngram_range=(1, 2),
                max_features=2_048,
                norm="l2",
            )
            matrix = vectorizer.fit_transform(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                [item.content for item in evidence]
            )
            query = vectorizer.transform(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                [question]
            )
            similarities = cast(
                NDArray[np.float64],
                cosine_similarity(  # pyright: ignore[reportUnknownArgumentType]
                    query,
                    matrix,
                    dense_output=True,
                ),
            )
            raw_scores = similarities[0]
            scored: list[_ScoredSnapshot] = []
            for snapshot, raw_score in zip(evidence, raw_scores, strict=True):
                score = round(float(raw_score), 12)
                if not isfinite(score) or not 0.0 <= score <= 1.0:
                    raise ValueError
                scored.append(_ScoredSnapshot(snapshot=snapshot, score=score))
            return tuple(
                sorted(
                    scored,
                    key=lambda item: (
                        -item.score,
                        item.snapshot.document_id,
                        item.snapshot.document_version,
                        item.snapshot.page_number,
                        item.snapshot.section_id,
                        item.snapshot.chunk_id,
                    ),
                )[:ASSISTANT_TOP_K]
            )
        except Exception:
            raise AssistantUnavailableError(
                "The assistant ranking boundary is unavailable."
            ) from None

    @staticmethod
    def _insufficient(*, max_score: float | None) -> AssistantResponse:
        return AssistantResponse(
            root=InsufficientEvidenceAssistantResult(
                status="insufficient_evidence",
                message=_INSUFFICIENT_MESSAGE,
                max_score=max_score,
                threshold=ASSISTANT_SIMILARITY_THRESHOLD,
                policy_version=ASSISTANT_POLICY_VERSION,
                citations=(),
            )
        )


class ConfiguredAssistantService:
    """Lifespan-configured facade without import-time I/O or mutable singleton."""

    def __init__(self) -> None:
        self._mode: AnalysisMode | None = None
        self._service: AssistantQueryService | None = None
        self._lock = RLock()

    @property
    def available(self) -> bool:
        with self._lock:
            return self._service is not None

    def select(self, mode: AnalysisMode) -> None:
        with self._lock:
            self._mode = mode
            self._service = None

    def configure(self, service: AssistantQueryService) -> None:
        with self._lock:
            self._service = service

    def query(self, request: AssistantQueryRequest) -> AssistantResponse:
        with self._lock:
            service = self._service
        if service is None:
            raise AssistantUnavailableError(
                "The configured assistant runtime is unavailable."
            )
        return service.query(request)


def build_synthetic_grounded_assistant() -> GroundedAssistantService:
    """Compose a governed, entirely synthetic corpus for the demo profile."""

    documents = InMemoryDocumentRepository()
    lifecycle = DocumentGovernanceService(
        repository=documents,
        clock=SystemUtcClock(),
    )
    chunks = InMemoryChunkRepository()
    document_ids: list[str] = []

    for source_name, content in _SYNTHETIC_CORPUS:
        source_sha256 = sha256(
            f"{source_name}\0{content}".encode("utf-8", errors="strict")
        ).hexdigest()
        extraction = _synthetic_extraction(
            source_name=source_name,
            source_sha256=source_sha256,
            content=content,
        )
        indexed = index_extracted_document(
            extraction,
            embedding_provider=LocalHashEmbeddingProvider(dimension=8),
            repository=chunks,
        )
        if (
            indexed.status is not DocumentIndexingStatus.COMPLETED
            or not indexed.records
        ):
            raise AssistantUnavailableError("Synthetic assistant indexing failed.")

        registered = lifecycle.register(
            identity=indexed.document_id,
            version=1,
            sha256=source_sha256,
            actor="synthetic-assistant.registrar",
            expected_revision=0,
        )
        processing = lifecycle.start_processing(
            identity=indexed.document_id,
            version=1,
            actor="synthetic-assistant.processor",
            expected_revision=registered.revision,
        )
        extracted = lifecycle.record_step_succeeded(
            identity=indexed.document_id,
            version=1,
            step=ProcessingStep.EXTRACTION,
            actor="synthetic-assistant.processor",
            expected_revision=processing.revision,
        )
        pending = lifecycle.record_step_succeeded(
            identity=indexed.document_id,
            version=1,
            step=ProcessingStep.INDEXING,
            actor="synthetic-assistant.processor",
            expected_revision=extracted.revision,
        )
        lifecycle.approve(
            identity=indexed.document_id,
            version=1,
            actor="synthetic-assistant.approver",
            reason="Corpus inteiramente sintético aprovado para a demonstração.",
            expected_revision=pending.revision,
        )
        document_ids.append(indexed.document_id)

    mapping = build_fault_knowledge_mapping(
        mapping_version="assistant-synthetic-corpus.v1",
        mappings={_ASSISTANT_COLLECTION: tuple(sorted(document_ids))},
    )
    retrieval = ApprovedKnowledgeRetrievalService(
        mapping=mapping,
        documents=documents,
        chunks=chunks,
        scorer=_CatalogScorer(),
    )
    return GroundedAssistantService(retrieval=retrieval)


def _synthetic_extraction(
    *,
    source_name: str,
    source_sha256: str,
    content: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "extractor_version": 1,
        "source": {
            "name": source_name,
            "sha256": source_sha256,
            "source_version": f"sha256:{source_sha256}",
            "size_bytes": len(content.encode("utf-8", errors="strict")),
            "pdf_version": "1.7",
        },
        "status": "completed",
        "failure_code": None,
        "page_count": 1,
        "pages": [
            {
                "page_number": 1,
                "method": "native",
                "status": "extracted",
                "text": content,
                "failure_code": None,
                "ocr_trigger_codes": [],
                "quality": {"signals": []},
            }
        ],
        "tooling": {
            "pypdfium2": "synthetic-runtime.v1",
            "ocr_adapter": {"configured": False, "name": None, "version": None},
        },
    }
