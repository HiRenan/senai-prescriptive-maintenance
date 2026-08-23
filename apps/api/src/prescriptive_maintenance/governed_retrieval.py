"""Typed RAG retrieval boundary over approved documentary ranking."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from math import isfinite
from typing import Final, Protocol, cast

from prescriptive_maintenance.contracts import MAX_TOP_K
from prescriptive_maintenance.generation.contracts import (
    MAX_EVIDENCE_CONTENT_CHARACTERS,
    MAX_EVIDENCE_ITEMS,
    MAX_TOTAL_EVIDENCE_CONTENT_CHARACTERS,
)
from prescriptive_maintenance.knowledge_retrieval import (
    KnowledgeRetrievalReason,
    KnowledgeSnapshotRetrievalResult,
    RankedKnowledgeSnapshot,
    canonical_fault_class,
    ranked_knowledge_snapshot_order_key,
)
from prescriptive_maintenance.ports import ModelDisposition

GOVERNED_RETRIEVAL_POLICY_SCHEMA_VERSION: Final = 1

_SHA256_PATTERN: Final = re.compile(r"[0-9a-f]{64}")
_VERSION_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class GovernedRetrievalStatus(StrEnum):
    """Closed outcomes exposed to the future RAG orchestration."""

    EVIDENCE = "evidence"
    NO_EVIDENCE = "no_evidence"
    UNMAPPED_FAULT = "unmapped_fault"
    RETRIEVAL_UNAVAILABLE = "retrieval_unavailable"


@dataclass(frozen=True, slots=True)
class GovernedRetrievalPolicy:
    """Explicit score threshold identified by normalized semantics."""

    schema_version: int
    policy_version: str
    minimum_score: float
    policy_sha256: str

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != GOVERNED_RETRIEVAL_POLICY_SCHEMA_VERSION
        ):
            raise ValueError("Governed retrieval policy schema is unsupported.")
        if (
            type(self.policy_version) is not str
            or _VERSION_PATTERN.fullmatch(self.policy_version) is None
        ):
            raise ValueError("Governed retrieval policy version is invalid.")
        if type(self.minimum_score) is not float or not isfinite(self.minimum_score):
            raise ValueError("Governed retrieval minimum score must be finite.")
        if (
            type(self.policy_sha256) is not str
            or _SHA256_PATTERN.fullmatch(self.policy_sha256) is None
        ):
            raise ValueError("Governed retrieval policy hash is invalid.")

        minimum_score = 0.0 if self.minimum_score == 0.0 else self.minimum_score
        object.__setattr__(self, "minimum_score", minimum_score)
        if self.policy_sha256 != _policy_sha256(
            schema_version=self.schema_version,
            policy_version=self.policy_version,
            minimum_score=minimum_score,
        ):
            raise ValueError("Governed retrieval policy hash does not match semantics.")


def build_governed_retrieval_policy(
    *,
    policy_version: str,
    minimum_score: float,
) -> GovernedRetrievalPolicy:
    """Build one deterministic policy without deriving operational values."""

    if (
        type(policy_version) is not str
        or _VERSION_PATTERN.fullmatch(policy_version) is None
    ):
        raise ValueError("Governed retrieval policy version is invalid.")
    if type(minimum_score) is not float or not isfinite(minimum_score):
        raise ValueError("Governed retrieval minimum score must be finite.")
    canonical_score = 0.0 if minimum_score == 0.0 else minimum_score
    return GovernedRetrievalPolicy(
        schema_version=GOVERNED_RETRIEVAL_POLICY_SCHEMA_VERSION,
        policy_version=policy_version,
        minimum_score=canonical_score,
        policy_sha256=_policy_sha256(
            schema_version=GOVERNED_RETRIEVAL_POLICY_SCHEMA_VERSION,
            policy_version=policy_version,
            minimum_score=canonical_score,
        ),
    )


@dataclass(frozen=True, slots=True)
class GovernedRetrievalResult:
    """Immutable decision plus provenance for one governed retrieval attempt."""

    status: GovernedRetrievalStatus
    fault_class: str | None
    policy_schema_version: int
    policy_version: str
    minimum_score: float
    policy_sha256: str
    mapping_version: str | None
    mapping_sha256: str | None
    evidence: tuple[RankedKnowledgeSnapshot, ...]

    def __post_init__(self) -> None:
        if type(self.status) is not GovernedRetrievalStatus:
            raise ValueError("Governed retrieval status is invalid.")

        fault_class = self.fault_class
        if fault_class is not None:
            try:
                fault_class = canonical_fault_class(fault_class)
            except Exception:
                raise ValueError("Governed retrieval fault class is invalid.") from None
            object.__setattr__(self, "fault_class", fault_class)

        policy = GovernedRetrievalPolicy(
            schema_version=self.policy_schema_version,
            policy_version=self.policy_version,
            minimum_score=self.minimum_score,
            policy_sha256=self.policy_sha256,
        )
        object.__setattr__(self, "minimum_score", policy.minimum_score)

        if (self.mapping_version is None) != (self.mapping_sha256 is None):
            raise ValueError("Governed retrieval mapping identity is incomplete.")
        if self.mapping_version is not None and (
            type(self.mapping_version) is not str
            or _VERSION_PATTERN.fullmatch(self.mapping_version) is None
            or type(self.mapping_sha256) is not str
            or _SHA256_PATTERN.fullmatch(self.mapping_sha256) is None
        ):
            raise ValueError("Governed retrieval mapping identity is invalid.")

        if type(self.evidence) is not tuple or len(self.evidence) > min(
            MAX_TOP_K, MAX_EVIDENCE_ITEMS
        ):
            raise ValueError("Governed retrieval evidence is invalid.")
        canonical_evidence: list[RankedKnowledgeSnapshot] = []
        for item in cast(tuple[object, ...], self.evidence):
            if type(item) is not RankedKnowledgeSnapshot:
                raise ValueError("Governed retrieval evidence is invalid.")
            canonical_evidence.append(
                RankedKnowledgeSnapshot(
                    document_id=item.document_id,
                    document_version=item.document_version,
                    chunk_id=item.chunk_id,
                    page_number=item.page_number,
                    section_id=item.section_id,
                    content=item.content,
                    content_sha256=item.content_sha256,
                    score=item.score,
                )
            )
        copied_evidence = tuple(canonical_evidence)
        if copied_evidence != tuple(
            sorted(copied_evidence, key=ranked_knowledge_snapshot_order_key)
        ):
            raise ValueError("Governed retrieval evidence order is invalid.")
        evidence_ids = tuple(
            (item.document_id, item.document_version, item.chunk_id)
            for item in copied_evidence
        )
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("Governed retrieval evidence must be unique.")
        if any(item.score < policy.minimum_score for item in copied_evidence):
            raise ValueError("Governed retrieval evidence is below policy threshold.")
        if any(
            len(item.content) > MAX_EVIDENCE_CONTENT_CHARACTERS
            for item in copied_evidence
        ) or (
            sum(len(item.content) for item in copied_evidence)
            > MAX_TOTAL_EVIDENCE_CONTENT_CHARACTERS
        ):
            raise ValueError("Governed retrieval evidence exceeds content budget.")
        object.__setattr__(self, "evidence", copied_evidence)

        if self.status is GovernedRetrievalStatus.EVIDENCE:
            if (
                not copied_evidence
                or fault_class is None
                or self.mapping_version is None
            ):
                raise ValueError("Successful governed retrieval requires evidence.")
        elif copied_evidence:
            raise ValueError("Empty governed retrieval states cannot contain evidence.")


class ApprovedKnowledgeRetriever(Protocol):
    """SEN-56 boundary reused without repeating governance filters."""

    def retrieve_snapshots(
        self,
        fault_class: str,
        *,
        top_k: int,
    ) -> KnowledgeSnapshotRetrievalResult: ...


class ApprovedSnapshotCurrentness(Protocol):
    """Exact snapshot check offered by the approved retrieval implementation."""

    def snapshots_are_current(
        self,
        *,
        fault_class: str,
        mapping_version: str,
        mapping_sha256: str,
        evidence: tuple[RankedKnowledgeSnapshot, ...],
    ) -> bool | None: ...


class RagKnowledgeRetrievalPort(Protocol):
    """Retrieval decision consumed by future RAG orchestration."""

    def retrieve(
        self,
        *,
        disposition: ModelDisposition,
        fault_class: str | None,
        top_k: int,
    ) -> GovernedRetrievalResult: ...


class GovernedKnowledgeRetrievalService:
    """Gate model disposition and threshold an approved SEN-56 ranking."""

    def __init__(
        self,
        *,
        approved_retrieval: ApprovedKnowledgeRetriever,
        policy: GovernedRetrievalPolicy,
    ) -> None:
        if type(policy) is not GovernedRetrievalPolicy:
            raise ValueError("Governed retrieval policy is invalid.")
        self._approved_retrieval = approved_retrieval
        self._policy = GovernedRetrievalPolicy(
            schema_version=policy.schema_version,
            policy_version=policy.policy_version,
            minimum_score=policy.minimum_score,
            policy_sha256=policy.policy_sha256,
        )

    def retrieve(
        self,
        *,
        disposition: ModelDisposition,
        fault_class: str | None,
        top_k: int,
    ) -> GovernedRetrievalResult:
        """Return a total, fail-closed decision without generic fallback search."""

        if type(disposition) is not ModelDisposition:
            return self._result(GovernedRetrievalStatus.RETRIEVAL_UNAVAILABLE)
        if disposition in {
            ModelDisposition.NORMAL,
            ModelDisposition.OUT_OF_DISTRIBUTION,
        }:
            return self._result(GovernedRetrievalStatus.NO_EVIDENCE)
        if type(top_k) is not int or not 1 <= top_k <= MAX_TOP_K:
            return self._result(
                GovernedRetrievalStatus.RETRIEVAL_UNAVAILABLE,
                fault_class=_safe_fault_class(fault_class),
            )
        if fault_class is None:
            return self._result(GovernedRetrievalStatus.UNMAPPED_FAULT)
        try:
            clean_fault_class = canonical_fault_class(fault_class)
        except Exception:
            return self._result(GovernedRetrievalStatus.RETRIEVAL_UNAVAILABLE)

        try:
            raw_result = cast(
                object,
                self._approved_retrieval.retrieve_snapshots(
                    clean_fault_class,
                    top_k=top_k,
                ),
            )
            result = _canonical_backend_result(
                raw_result,
                expected_fault_class=clean_fault_class,
                top_k=top_k,
            )
        except Exception:
            result = None
        if result is None:
            return self._result(
                GovernedRetrievalStatus.RETRIEVAL_UNAVAILABLE,
                fault_class=clean_fault_class,
            )

        if result.reason is not None:
            status = _status_for_reason(result.reason)
            return self._result(
                status,
                fault_class=clean_fault_class,
                mapping_version=result.mapping_version,
                mapping_sha256=result.mapping_sha256,
            )

        threshold_evidence = tuple(
            item for item in result.evidence if item.score >= self._policy.minimum_score
        )
        if any(
            len(item.content) > MAX_EVIDENCE_CONTENT_CHARACTERS
            for item in threshold_evidence
        ):
            return self._result(
                GovernedRetrievalStatus.RETRIEVAL_UNAVAILABLE,
                fault_class=clean_fault_class,
                mapping_version=result.mapping_version,
                mapping_sha256=result.mapping_sha256,
            )
        evidence: list[RankedKnowledgeSnapshot] = []
        content_characters = 0
        for item in threshold_evidence[:MAX_EVIDENCE_ITEMS]:
            next_total = content_characters + len(item.content)
            if next_total > MAX_TOTAL_EVIDENCE_CONTENT_CHARACTERS:
                break
            evidence.append(item)
            content_characters = next_total
        if not evidence:
            return self._result(
                GovernedRetrievalStatus.NO_EVIDENCE,
                fault_class=clean_fault_class,
                mapping_version=result.mapping_version,
                mapping_sha256=result.mapping_sha256,
            )
        return self._result(
            GovernedRetrievalStatus.EVIDENCE,
            fault_class=clean_fault_class,
            mapping_version=result.mapping_version,
            mapping_sha256=result.mapping_sha256,
            evidence=tuple(evidence),
        )

    def snapshots_are_current(
        self,
        *,
        fault_class: str,
        policy_schema_version: int,
        policy_version: str,
        minimum_score: float,
        policy_sha256: str,
        mapping_version: str,
        mapping_sha256: str,
        evidence: tuple[RankedKnowledgeSnapshot, ...],
    ) -> bool | None:
        """Revalidate one exact governed result without search or reranking."""

        try:
            candidate = GovernedRetrievalResult(
                status=GovernedRetrievalStatus.EVIDENCE,
                fault_class=fault_class,
                policy_schema_version=policy_schema_version,
                policy_version=policy_version,
                minimum_score=minimum_score,
                policy_sha256=policy_sha256,
                mapping_version=mapping_version,
                mapping_sha256=mapping_sha256,
                evidence=evidence,
            )
        except Exception:
            return False
        if (
            candidate.policy_schema_version != self._policy.schema_version
            or candidate.policy_version != self._policy.policy_version
            or candidate.minimum_score != self._policy.minimum_score
            or candidate.policy_sha256 != self._policy.policy_sha256
            or candidate.fault_class is None
            or candidate.mapping_version is None
            or candidate.mapping_sha256 is None
        ):
            return False
        try:
            currentness = cast(
                ApprovedSnapshotCurrentness,
                self._approved_retrieval,
            )
            current = cast(
                object,
                currentness.snapshots_are_current(
                    fault_class=candidate.fault_class,
                    mapping_version=candidate.mapping_version,
                    mapping_sha256=candidate.mapping_sha256,
                    evidence=candidate.evidence,
                ),
            )
            return current if type(current) is bool or current is None else None
        except Exception:
            return None

    def _result(
        self,
        status: GovernedRetrievalStatus,
        *,
        fault_class: str | None = None,
        mapping_version: str | None = None,
        mapping_sha256: str | None = None,
        evidence: tuple[RankedKnowledgeSnapshot, ...] = (),
    ) -> GovernedRetrievalResult:
        return GovernedRetrievalResult(
            status=status,
            fault_class=fault_class,
            policy_schema_version=self._policy.schema_version,
            policy_version=self._policy.policy_version,
            minimum_score=self._policy.minimum_score,
            policy_sha256=self._policy.policy_sha256,
            mapping_version=mapping_version,
            mapping_sha256=mapping_sha256,
            evidence=evidence,
        )


def _canonical_backend_result(
    value: object,
    *,
    expected_fault_class: str,
    top_k: int,
) -> KnowledgeSnapshotRetrievalResult | None:
    if type(value) is not KnowledgeSnapshotRetrievalResult:
        return None
    result = KnowledgeSnapshotRetrievalResult(
        fault_class=value.fault_class,
        mapping_version=value.mapping_version,
        mapping_sha256=value.mapping_sha256,
        evidence=value.evidence,
        reason=value.reason,
    )
    if result.fault_class != expected_fault_class or len(result.evidence) > top_k:
        return None
    if result.evidence != tuple(
        sorted(result.evidence, key=ranked_knowledge_snapshot_order_key)
    ):
        return None
    evidence_ids = tuple(
        (item.document_id, item.document_version, item.chunk_id)
        for item in result.evidence
    )
    if len(evidence_ids) != len(set(evidence_ids)):
        return None
    return result


def _status_for_reason(reason: KnowledgeRetrievalReason) -> GovernedRetrievalStatus:
    if reason is KnowledgeRetrievalReason.FAULT_CLASS_UNMAPPED:
        return GovernedRetrievalStatus.UNMAPPED_FAULT
    if reason in {
        KnowledgeRetrievalReason.NO_APPROVED_COVERAGE,
        KnowledgeRetrievalReason.EMPTY_RANKING,
    }:
        return GovernedRetrievalStatus.NO_EVIDENCE
    return GovernedRetrievalStatus.RETRIEVAL_UNAVAILABLE


def _safe_fault_class(value: object) -> str | None:
    try:
        return canonical_fault_class(value)
    except Exception:
        return None


def _policy_sha256(
    *,
    schema_version: int,
    policy_version: str,
    minimum_score: float,
) -> str:
    canonical = json.dumps(
        {
            "minimum_score_hex": minimum_score.hex(),
            "policy_version": policy_version,
            "schema_version": schema_version,
        },
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(canonical).hexdigest()
