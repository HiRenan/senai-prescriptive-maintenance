"""Typed, immutable access to the declarative banner quality policy."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256
from importlib.resources import files
from math import isfinite
from pathlib import Path
from types import MappingProxyType
from typing import Final, NoReturn, cast
from unicodedata import category as unicode_category

from prescriptive_maintenance.data.baseline import (
    BannerBaselineError,
    render_banner_baseline_markdown,
    validate_banner_baseline_bytes,
)
from prescriptive_maintenance.data.contract import (
    BANNER_COLUMN_CATALOG,
    BANNER_CONTRACT_VERSION,
    BANNER_PHYSICAL_MINIMUMS,
    LogicalType,
    ValidationSeverity,
)
from prescriptive_maintenance.data.profiling import (
    BANNER_PROFILE_SCHEMA_VERSION,
    BANNER_REDUNDANT_UNIT_PAIR_IDENTITIES,
    PROFILE_DEFINITIONS,
    PROFILE_UNIT_ABSOLUTE_TOLERANCE,
    PROFILE_UNIT_RELATIVE_TOLERANCE,
)

BANNER_QUALITY_POLICY_SCHEMA_VERSION: Final = 1
BANNER_QUALITY_POLICY_VERSION: Final = 1

_POLICY_RESOURCE_PACKAGE: Final = "prescriptive_maintenance.data.policies"
_POLICY_RESOURCE_NAME: Final = "banner_quality_policy.v1.json"
_SHA256_LENGTH: Final = 64
_UNSAFE_UNICODE_CATEGORIES: Final = frozenset({"Cc", "Cf", "Cs"})
_COLUMN_ROLES: Final = frozenset(
    {"identifier", "event_timestamp", "measurement", "raw_label"}
)
_AMBIGUOUS_UNIT_RULE_ID: Final = "unit.inconsistent.ambiguous"
_DETERMINISTIC_UNIT_RULE_ID: Final = "unit.inconsistent.deterministic"


class ReasonCode(StrEnum):
    """Stable public reasons in their total serialization order."""

    REQUIRED_VALUE_MISSING = "required_value_missing"
    NON_FINITE = "non_finite"
    INVALID_PHYSICAL_DOMAIN = "invalid_physical_domain"
    CONFLICTING_DUPLICATE = "conflicting_duplicate"
    INCONSISTENT_REDUNDANT_UNIT = "inconsistent_redundant_unit"
    IDENTICAL_DUPLICATE = "identical_duplicate"
    IQR_OUTLIER = "iqr_outlier"


class Action(StrEnum):
    """Stable public actions allowed by quality policy rules."""

    KEEP = "keep"
    CORRECT_DETERMINISTICALLY = "correct_deterministically"
    MAP = "map"
    FLAG = "flag"
    REJECT = "reject"


EFFECTIVE_ACTION_PRECEDENCE: Final[tuple[Action, ...]] = (
    Action.REJECT,
    Action.CORRECT_DETERMINISTICALLY,
    Action.MAP,
    Action.FLAG,
    Action.KEEP,
)


class QualityPolicyError(ValueError):
    """Raised when a quality policy is unavailable or invalid."""


@dataclass(frozen=True, slots=True)
class QualityPolicyColumn:
    """One contract column classified for policy targeting."""

    position: int
    name: str
    role: str
    unit: str


@dataclass(frozen=True, slots=True)
class IqrPolicy:
    """Reproducible IQR definition shared by every statistical target."""

    q1_probability: float
    q3_probability: float
    quantile_method: str
    multiplier: float
    population: str
    lower_fence_operator: str
    upper_fence_operator: str
    outlier_boundary_inclusive: bool
    preserves_record: bool
    origin: str


@dataclass(frozen=True, slots=True)
class UnitRelationPolicy:
    """One deterministic relationship between redundant source columns."""

    relation_id: str
    left_column: str
    left_unit: str
    right_column: str
    right_unit: str
    multiplier: float
    offset: float
    relation: str
    absolute_tolerance: float
    relative_tolerance: float
    tolerance_inclusive: bool
    origin: str


@dataclass(frozen=True, slots=True)
class RuleScope:
    """Role and optional explicit columns selected by a policy rule."""

    role: str
    columns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RuleThreshold:
    """Reviewable threshold, operator, and equality behavior for one rule."""

    operator: str
    value: str | float | None
    inclusive: bool | None


@dataclass(frozen=True, slots=True)
class QualityRule:
    """One immutable rule in the complete quality decision matrix."""

    rule_id: str
    reason_code: ReasonCode
    action: Action
    severity: ValidationSeverity
    precedence: int
    condition: str
    scope: RuleScope
    threshold: RuleThreshold
    uses_iqr: bool
    unit_relation_ids: tuple[str, ...]
    justification: str
    origins: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RuleBinding:
    """Stable public rule-id binding requested by downstream audit consumers."""

    reason_code: ReasonCode
    action: Action


@dataclass(frozen=True, slots=True)
class QualityMatch:
    """One rule match with allowlisted, non-row-level audit context."""

    rule_id: str
    column_name: str | None = None
    unit_relation_id: str | None = None
    trusted_column: str | None = None


@dataclass(frozen=True, slots=True)
class QualityDecision:
    """Concurrent matches plus their single effective policy action."""

    matches: tuple[QualityMatch, ...]
    reason_codes: tuple[ReasonCode, ...]
    actions: tuple[Action, ...]
    effective_action: Action


@dataclass(frozen=True, slots=True)
class BannerQualityPolicy:
    """Validated, immutable banner policy loaded from the canonical JSON."""

    schema_version: int
    policy_version: int
    subject: str
    contract_version: int
    profile_schema_version: int
    policy_id: str
    columns: tuple[QualityPolicyColumn, ...]
    duplicate_key_columns: tuple[str, ...]
    reason_code_order: tuple[ReasonCode, ...]
    effective_action_precedence: tuple[Action, ...]
    iqr: IqrPolicy
    unit_relations: tuple[UnitRelationPolicy, ...]
    rules: tuple[QualityRule, ...]
    limitations: tuple[str, ...]

    @property
    def rule_index(self) -> Mapping[str, RuleBinding]:
        """Return an immutable rule-id to reason/action index."""

        return MappingProxyType(
            {
                rule.rule_id: RuleBinding(rule.reason_code, rule.action)
                for rule in self.rules
            }
        )

    def rule(self, rule_id: str) -> QualityRule:
        """Return exactly one declared rule by stable identifier."""

        matches = tuple(rule for rule in self.rules if rule.rule_id == rule_id)
        if len(matches) != 1:
            raise QualityPolicyError("Quality policy rule is unavailable.")
        return matches[0]

    def rules_for_reason(self, reason_code: ReasonCode) -> tuple[QualityRule, ...]:
        """Return every rule carrying a reason without collapsing alternatives."""

        return tuple(rule for rule in self.rules if rule.reason_code is reason_code)

    def columns_for_rule(self, rule_id: str) -> tuple[QualityPolicyColumn, ...]:
        """Resolve a rule scope to its ordered contract columns."""

        return _columns_for_rule(self, self.rule(rule_id))


def load_banner_quality_policy(path: Path | None = None) -> BannerQualityPolicy:
    """Load the packaged canonical policy or one explicit policy path."""

    try:
        raw = (
            files(_POLICY_RESOURCE_PACKAGE).joinpath(_POLICY_RESOURCE_NAME).read_bytes()
            if path is None
            else path.read_bytes()
        )
    except OSError:
        raise QualityPolicyError("Quality policy resource is unavailable.") from None
    return validate_banner_quality_policy(raw)


def validate_banner_quality_policy(payload: object) -> BannerQualityPolicy:
    """Strictly validate JSON bytes, a mapping, or an immutable policy."""

    policy = _coerce_policy(payload)
    _validate_policy_semantics(policy)
    calculated = _calculate_policy_id(policy)
    if policy.policy_id != calculated:
        raise QualityPolicyError("Quality policy identifier does not match semantics.")
    return policy


def identify_banner_quality_policy(payload: object) -> str:
    """Calculate SHA-256 over normalized semantics excluding the stored ID."""

    policy = _coerce_policy(payload)
    _validate_policy_semantics(policy)
    return _calculate_policy_id(policy)


def resolve_quality_rules(
    policy: BannerQualityPolicy, matches: Sequence[QualityMatch]
) -> QualityDecision:
    """Resolve contextual matches without evaluating rows or dropping evidence."""

    validated = validate_banner_quality_policy(policy)
    if isinstance(matches, str | bytes):
        raise QualityPolicyError("Quality matches must be an ordered sequence.")
    raw_matches = tuple(cast(Sequence[object], matches))
    if any(not isinstance(match, QualityMatch) for match in raw_matches):
        raise QualityPolicyError("Quality matches have an invalid type.")
    typed_matches = cast(tuple[QualityMatch, ...], raw_matches)
    for match in typed_matches:
        _validate_quality_match(validated, match)
    deduplicated_matches = set(typed_matches)
    _validate_unit_match_groups(deduplicated_matches)

    column_positions = {column.name: column.position for column in validated.columns}
    relation_positions = {
        relation.relation_id: position
        for position, relation in enumerate(validated.unit_relations)
    }
    matches_by_serialization = tuple(
        sorted(
            deduplicated_matches,
            key=lambda match: (
                validated.rule(match.rule_id).precedence,
                match.rule_id,
                column_positions.get(match.column_name or "", -1),
                relation_positions.get(match.unit_relation_id or "", -1),
                column_positions.get(match.trusted_column or "", -1),
            ),
        )
    )
    selected_rules = tuple(
        validated.rule(match.rule_id) for match in matches_by_serialization
    )
    reason_codes = tuple(
        reason
        for reason in validated.reason_code_order
        if any(rule.reason_code is reason for rule in selected_rules)
    )
    actions = tuple(
        action
        for action in validated.effective_action_precedence
        if any(rule.action is action for rule in selected_rules)
    )
    effective_action = actions[0] if actions else Action.KEEP
    return QualityDecision(
        matches=matches_by_serialization,
        reason_codes=reason_codes,
        actions=actions,
        effective_action=effective_action,
    )


def render_banner_quality_policy_markdown(
    policy: BannerQualityPolicy,
    *,
    baseline_json_bytes: bytes,
    baseline_manifest_path: Path,
) -> str:
    """Derive the public human view from policy and tracked aggregate JSON."""

    validated = validate_banner_quality_policy(policy)
    baseline = _baseline_summary(
        baseline_json_bytes,
        validated,
        manifest_path=baseline_manifest_path,
    )
    lines = [
        "# Política de qualidade e outliers do banner",
        "",
        "## Identidade e alcance",
        "",
        f"- `policy_id`: `{validated.policy_id}`",
        f"- Versão da política: {validated.policy_version}",
        f"- Contrato de colunas: v{validated.contract_version}",
        f"- Esquema do profiler: v{validated.profile_schema_version}",
        "- Alcance: declaração e consulta; nenhuma linha é alterada ou removida.",
        "",
        "## Precedências imutáveis",
        "",
        "- Motivos: "
        + " > ".join(f"`{reason.value}`" for reason in validated.reason_code_order),
        "- Ação efetiva: "
        + " > ".join(
            f"`{action.value}`" for action in validated.effective_action_precedence
        ),
        "",
        (
            "A precedência escolhe somente a ação principal e a ordem de "
            "serialização. Todos os matches e motivos concorrentes permanecem na "
            "decisão auditável."
        ),
        "",
        "## Matriz pública de regras",
        "",
        (
            "| Regra | Papel e coluna(s) | Unidade(s) | Motivo | Ação | Severidade "
            "| Precedência | Operador, limiar e inclusividade | "
            "Justificativa | Origem |"
        ),
        "| --- | --- | --- | --- | --- | --- | ---: | --- | --- | --- |",
    ]
    for rule in validated.rules:
        columns = _columns_for_rule(validated, rule)
        column_names = ", ".join(column.name for column in columns)
        units = ", ".join(dict.fromkeys(column.unit for column in columns))
        scope = f"{rule.scope.role}: {column_names}"
        threshold = _threshold_markdown(rule.threshold)
        lines.append(
            f"| `{_markdown_cell(rule.rule_id)}` | {_markdown_cell(scope)} | "
            f"{_markdown_cell(units)} | `{rule.reason_code.value}` | "
            f"`{rule.action.value}` | `{rule.severity.value}` | {rule.precedence} | "
            f"{_markdown_cell(threshold)} | "
            f"{_markdown_cell(rule.justification)} | "
            f"{_markdown_cell(', '.join(rule.origins))} |"
        )

    lines.extend(
        [
            "",
            "## IQR congelado",
            "",
            f"- Q1: {validated.iqr.q1_probability}",
            f"- Q3: {validated.iqr.q3_probability}",
            f"- Método: `{validated.iqr.quantile_method}`",
            f"- Multiplicador: {validated.iqr.multiplier}",
            f"- População: `{validated.iqr.population}`",
            (
                "- Fronteiras: valor "
                f"`{validated.iqr.lower_fence_operator}` Q1 - k * IQR ou "
                f"`{validated.iqr.upper_fence_operator}` Q3 + k * IQR; igualdade "
                "não é outlier."
            ),
            "- Ação: `flag`; o registro é preservado.",
            "",
            "## Relações redundantes",
            "",
            (
                "| Relação | Fórmula | Tolerância absoluta | "
                "Tolerância relativa | Inclusiva |"
            ),
            "| --- | --- | ---: | ---: | :---: |",
        ]
    )
    for relation in validated.unit_relations:
        lines.append(
            f"| `{_markdown_cell(relation.relation_id)}` | "
            f"`{_markdown_cell(relation.relation)}` | "
            f"{relation.absolute_tolerance:g} | {relation.relative_tolerance:g} | "
            f"{_yes_no(relation.tolerance_inclusive)} |"
        )

    lines.extend(
        [
            "",
            "## Comparação agregada com a baseline rastreada",
            "",
            (
                f"A baseline aprovada contém {baseline.row_count} registros e "
                f"{baseline.column_count} colunas. A comparação abaixo usa somente "
                "contagens agregadas já publicadas."
            ),
            "",
            "| Motivo | Evidência agregada | Leitura da política |",
            "| --- | ---: | --- |",
            (
                "| `required_value_missing` | "
                f"{baseline.missing_count} células | Rejeitar por papel da coluna. |"
            ),
            (
                "| `non_finite` | "
                f"{baseline.non_finite_count} células | Rejeitar; não entram no IQR. |"
            ),
            (
                "| `invalid_physical_domain` | "
                f"{baseline.physical_domain_count} células | "
                "Rejeitar pelos limites do contrato. |"
            ),
            (
                "| `identical_duplicate` | "
                f"{baseline.identical_duplicate_group_count} grupos / "
                f"{baseline.identical_duplicate_excess_count} excedentes | "
                "Mapear a identidade de auditoria e manter cada registro. |"
            ),
            (
                "| `conflicting_duplicate` | "
                f"{baseline.conflicting_duplicate_group_count} grupos / "
                f"{baseline.conflicting_duplicate_row_count} registros | Rejeitar. |"
            ),
            (
                "| `inconsistent_redundant_unit` | "
                f"{baseline.unit_inconsistency_count} comparações em "
                f"{baseline.unit_pair_count} pares | Bloquear sem prova única; "
                "corrigir somente com contraparte independentemente comprovada. |"
            ),
            (
                "| `iqr_outlier` | "
                f"{baseline.iqr_outlier_count} ocorrências célula-coluna em "
                f"{baseline.iqr_column_count} colunas | Sinalizar e preservar. |"
            ),
            "",
            "## Limitações",
            "",
        ]
    )
    lines.extend(f"- {limitation}" for limitation in validated.limitations)
    lines.extend(
        [
            (
                "- As ocorrências IQR são contagens por célula-coluna e podem se "
                "sobrepor no mesmo registro; não equivalem a uma contagem de linhas."
            ),
            (
                "- A baseline agregada não prova qual contraparte redundante está "
                "correta e não autoriza correção automática."
            ),
            "",
        ]
    )
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class _BaselineSummary:
    row_count: int
    column_count: int
    missing_count: int
    non_finite_count: int
    physical_domain_count: int
    identical_duplicate_group_count: int
    identical_duplicate_excess_count: int
    conflicting_duplicate_group_count: int
    conflicting_duplicate_row_count: int
    unit_inconsistency_count: int
    unit_pair_count: int
    iqr_outlier_count: int
    iqr_column_count: int


def _coerce_policy(payload: object) -> BannerQualityPolicy:
    if isinstance(payload, BannerQualityPolicy):
        return _normalize_policy(payload)
    if isinstance(payload, bytes):
        raw = _decode_json(payload)
    elif isinstance(payload, Mapping):
        raw = _mapping(cast(Mapping[object, object], payload), "root")
    else:
        raise QualityPolicyError("Quality policy must be JSON bytes or an object.")
    _scan_safe_unicode(raw)
    return _parse_policy(raw)


def _normalize_policy(policy: BannerQualityPolicy) -> BannerQualityPolicy:
    """Canonicalize order-insensitive tuples in a constructed policy object."""

    try:
        normalized_rules = tuple(
            sorted(
                (
                    replace(
                        rule,
                        scope=replace(
                            rule.scope,
                            columns=tuple(sorted(rule.scope.columns)),
                        ),
                        unit_relation_ids=tuple(sorted(rule.unit_relation_ids)),
                        origins=tuple(sorted(rule.origins)),
                    )
                    for rule in policy.rules
                ),
                key=lambda rule: (rule.precedence, rule.rule_id),
            )
        )
        normalized = replace(
            policy,
            columns=tuple(sorted(policy.columns, key=lambda column: column.position)),
            duplicate_key_columns=tuple(sorted(policy.duplicate_key_columns)),
            unit_relations=tuple(
                sorted(
                    policy.unit_relations,
                    key=lambda relation: relation.relation_id,
                )
            ),
            rules=normalized_rules,
            limitations=tuple(sorted(policy.limitations)),
        )
    except (AttributeError, TypeError):
        raise QualityPolicyError("Quality policy object is invalid.") from None
    return policy if normalized == policy else normalized


def _decode_json(raw: bytes) -> dict[str, object]:
    try:
        text = raw.decode("utf-8", errors="strict")
        payload: object = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, QualityPolicyError, ValueError):
        raise QualityPolicyError("Quality policy JSON is invalid.") from None
    return _mapping(payload, "root")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise QualityPolicyError("Quality policy JSON contains duplicate keys.")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> NoReturn:
    raise ValueError("non-finite JSON number")


def _parse_policy(payload: Mapping[str, object]) -> BannerQualityPolicy:
    _exact_keys(
        payload,
        (
            "schema_version",
            "policy_version",
            "subject",
            "contract_version",
            "profile_schema_version",
            "policy_id",
            "columns",
            "duplicate_key_columns",
            "reason_code_order",
            "effective_action_precedence",
            "iqr",
            "unit_relations",
            "rules",
            "limitations",
        ),
        "root",
    )
    columns = tuple(
        sorted(
            (_parse_column(item) for item in _sequence(payload["columns"], "columns")),
            key=lambda column: column.position,
        )
    )
    unit_relations = tuple(
        sorted(
            (
                _parse_unit_relation(item)
                for item in _sequence(payload["unit_relations"], "unit_relations")
            ),
            key=lambda relation: relation.relation_id,
        )
    )
    rules = tuple(
        sorted(
            (_parse_rule(item) for item in _sequence(payload["rules"], "rules")),
            key=lambda rule: (rule.precedence, rule.rule_id),
        )
    )
    return BannerQualityPolicy(
        schema_version=_integer(payload["schema_version"], "schema_version"),
        policy_version=_integer(payload["policy_version"], "policy_version"),
        subject=_text(payload["subject"], "subject"),
        contract_version=_integer(payload["contract_version"], "contract_version"),
        profile_schema_version=_integer(
            payload["profile_schema_version"], "profile_schema_version"
        ),
        policy_id=_text(payload["policy_id"], "policy_id"),
        columns=columns,
        duplicate_key_columns=_unique_sorted_texts(
            payload["duplicate_key_columns"], "duplicate_key_columns"
        ),
        reason_code_order=tuple(
            _enum_value(ReasonCode, item, "reason_code_order")
            for item in _sequence(payload["reason_code_order"], "reason_code_order")
        ),
        effective_action_precedence=tuple(
            _enum_value(Action, item, "effective_action_precedence")
            for item in _sequence(
                payload["effective_action_precedence"],
                "effective_action_precedence",
            )
        ),
        iqr=_parse_iqr(payload["iqr"]),
        unit_relations=unit_relations,
        rules=rules,
        limitations=_unique_sorted_texts(payload["limitations"], "limitations"),
    )


def _parse_column(value: object) -> QualityPolicyColumn:
    mapping = _mapping(value, "column")
    _exact_keys(mapping, ("position", "name", "role", "unit"), "column")
    return QualityPolicyColumn(
        position=_integer(mapping["position"], "column.position"),
        name=_text(mapping["name"], "column.name"),
        role=_text(mapping["role"], "column.role"),
        unit=_text(mapping["unit"], "column.unit"),
    )


def _parse_iqr(value: object) -> IqrPolicy:
    mapping = _mapping(value, "iqr")
    fields = (
        "q1_probability",
        "q3_probability",
        "quantile_method",
        "multiplier",
        "population",
        "lower_fence_operator",
        "upper_fence_operator",
        "outlier_boundary_inclusive",
        "preserves_record",
        "origin",
    )
    _exact_keys(mapping, fields, "iqr")
    return IqrPolicy(
        q1_probability=_number(mapping["q1_probability"], "iqr.q1_probability"),
        q3_probability=_number(mapping["q3_probability"], "iqr.q3_probability"),
        quantile_method=_text(mapping["quantile_method"], "iqr.quantile_method"),
        multiplier=_number(mapping["multiplier"], "iqr.multiplier"),
        population=_text(mapping["population"], "iqr.population"),
        lower_fence_operator=_text(
            mapping["lower_fence_operator"], "iqr.lower_fence_operator"
        ),
        upper_fence_operator=_text(
            mapping["upper_fence_operator"], "iqr.upper_fence_operator"
        ),
        outlier_boundary_inclusive=_boolean(
            mapping["outlier_boundary_inclusive"],
            "iqr.outlier_boundary_inclusive",
        ),
        preserves_record=_boolean(mapping["preserves_record"], "iqr.preserves_record"),
        origin=_text(mapping["origin"], "iqr.origin"),
    )


def _parse_unit_relation(value: object) -> UnitRelationPolicy:
    mapping = _mapping(value, "unit_relation")
    fields = (
        "relation_id",
        "left_column",
        "left_unit",
        "right_column",
        "right_unit",
        "multiplier",
        "offset",
        "relation",
        "absolute_tolerance",
        "relative_tolerance",
        "tolerance_inclusive",
        "origin",
    )
    _exact_keys(mapping, fields, "unit_relation")
    return UnitRelationPolicy(
        relation_id=_text(mapping["relation_id"], "unit_relation.relation_id"),
        left_column=_text(mapping["left_column"], "unit_relation.left_column"),
        left_unit=_text(mapping["left_unit"], "unit_relation.left_unit"),
        right_column=_text(mapping["right_column"], "unit_relation.right_column"),
        right_unit=_text(mapping["right_unit"], "unit_relation.right_unit"),
        multiplier=_number(mapping["multiplier"], "unit_relation.multiplier"),
        offset=_number(mapping["offset"], "unit_relation.offset"),
        relation=_text(mapping["relation"], "unit_relation.relation"),
        absolute_tolerance=_number(
            mapping["absolute_tolerance"], "unit_relation.absolute_tolerance"
        ),
        relative_tolerance=_number(
            mapping["relative_tolerance"], "unit_relation.relative_tolerance"
        ),
        tolerance_inclusive=_boolean(
            mapping["tolerance_inclusive"], "unit_relation.tolerance_inclusive"
        ),
        origin=_text(mapping["origin"], "unit_relation.origin"),
    )


def _parse_rule(value: object) -> QualityRule:
    mapping = _mapping(value, "rule")
    fields = (
        "rule_id",
        "reason_code",
        "action",
        "severity",
        "precedence",
        "condition",
        "scope",
        "threshold",
        "uses_iqr",
        "unit_relation_ids",
        "justification",
        "origins",
    )
    _exact_keys(mapping, fields, "rule")
    scope = _mapping(mapping["scope"], "rule.scope")
    _exact_keys(scope, ("role", "columns"), "rule.scope")
    threshold = _mapping(mapping["threshold"], "rule.threshold")
    _exact_keys(threshold, ("operator", "value", "inclusive"), "rule.threshold")
    return QualityRule(
        rule_id=_text(mapping["rule_id"], "rule.rule_id"),
        reason_code=_enum_value(ReasonCode, mapping["reason_code"], "rule.reason"),
        action=_enum_value(Action, mapping["action"], "rule.action"),
        severity=_enum_value(ValidationSeverity, mapping["severity"], "rule.severity"),
        precedence=_integer(mapping["precedence"], "rule.precedence"),
        condition=_text(mapping["condition"], "rule.condition"),
        scope=RuleScope(
            role=_text(scope["role"], "rule.scope.role"),
            columns=_unique_sorted_texts(scope["columns"], "rule.scope.columns"),
        ),
        threshold=RuleThreshold(
            operator=_text(threshold["operator"], "rule.threshold.operator"),
            value=_threshold_value(threshold["value"]),
            inclusive=_optional_boolean(
                threshold["inclusive"], "rule.threshold.inclusive"
            ),
        ),
        uses_iqr=_boolean(mapping["uses_iqr"], "rule.uses_iqr"),
        unit_relation_ids=_unique_sorted_texts(
            mapping["unit_relation_ids"], "rule.unit_relation_ids"
        ),
        justification=_text(mapping["justification"], "rule.justification"),
        origins=_unique_sorted_texts(mapping["origins"], "rule.origins"),
    )


def _validate_policy_semantics(policy: BannerQualityPolicy) -> None:
    if (
        policy.schema_version != BANNER_QUALITY_POLICY_SCHEMA_VERSION
        or policy.policy_version != BANNER_QUALITY_POLICY_VERSION
        or policy.subject != "banner"
        or policy.contract_version != BANNER_CONTRACT_VERSION
        or policy.profile_schema_version != BANNER_PROFILE_SCHEMA_VERSION
    ):
        raise QualityPolicyError("Quality policy versions or subject are invalid.")
    if len(policy.policy_id) != _SHA256_LENGTH or any(
        character not in "0123456789abcdef" for character in policy.policy_id
    ):
        raise QualityPolicyError("Quality policy identifier is invalid.")

    expected_columns = tuple(
        QualityPolicyColumn(
            column.position,
            column.name,
            _contract_role(column.name, column.logical_type),
            column.source_unit,
        )
        for column in BANNER_COLUMN_CATALOG
    )
    if policy.columns != expected_columns:
        raise QualityPolicyError("Quality policy columns diverge from the contract.")
    if not policy.duplicate_key_columns or any(
        key not in {column.name for column in policy.columns}
        for key in policy.duplicate_key_columns
    ):
        raise QualityPolicyError("Quality policy duplicate keys are invalid.")
    if policy.reason_code_order != tuple(ReasonCode):
        raise QualityPolicyError("Quality policy reason-code order is invalid.")
    if policy.effective_action_precedence != EFFECTIVE_ACTION_PRECEDENCE:
        raise QualityPolicyError("Effective action precedence is invalid.")

    _validate_iqr(policy)
    _validate_unit_relations(policy)
    _validate_rules(policy)
    if not policy.limitations:
        raise QualityPolicyError("Quality policy limitations must be explicit.")
    _scan_safe_unicode(_semantic_payload(policy))


def _validate_iqr(policy: BannerQualityPolicy) -> None:
    expected_quantiles = PROFILE_DEFINITIONS.quantile_probabilities
    if (
        policy.iqr.q1_probability != expected_quantiles[0]
        or policy.iqr.q3_probability != expected_quantiles[2]
        or policy.iqr.quantile_method != PROFILE_DEFINITIONS.quantile_method
        or policy.iqr.multiplier != PROFILE_DEFINITIONS.iqr_multiplier
        or policy.iqr.population != "finite_values_only"
        or policy.iqr.lower_fence_operator != "<"
        or policy.iqr.upper_fence_operator != ">"
        or policy.iqr.outlier_boundary_inclusive
        or not policy.iqr.preserves_record
    ):
        raise QualityPolicyError("IQR policy diverges from the profiler.")


def _validate_unit_relations(policy: BannerQualityPolicy) -> None:
    if len(policy.unit_relations) != len(
        {relation.relation_id for relation in policy.unit_relations}
    ):
        raise QualityPolicyError("Quality policy unit relation IDs must be unique.")
    expected = {
        (left, right): relation
        for left, right, relation in BANNER_REDUNDANT_UNIT_PAIR_IDENTITIES
    }
    observed_pairs = [
        (relation.left_column, relation.right_column)
        for relation in policy.unit_relations
    ]
    if len(policy.unit_relations) != len(expected) or len(observed_pairs) != len(
        set(observed_pairs)
    ):
        raise QualityPolicyError(
            "Quality policy unit relation pairs must be semantically unique."
        )
    columns = {column.name: column for column in policy.columns}
    observed: set[tuple[str, str]] = set()
    for relation in policy.unit_relations:
        pair = (relation.left_column, relation.right_column)
        observed.add(pair)
        if (
            pair not in expected
            or relation.relation != expected[pair]
            or relation.relation != _relation_text(relation)
            or relation.left_unit != columns[relation.left_column].unit
            or relation.right_unit != columns[relation.right_column].unit
            or relation.absolute_tolerance != PROFILE_UNIT_ABSOLUTE_TOLERANCE
            or relation.relative_tolerance != PROFILE_UNIT_RELATIVE_TOLERANCE
            or not relation.tolerance_inclusive
        ):
            raise QualityPolicyError("Quality policy unit relation is invalid.")
    if observed != set(expected):
        raise QualityPolicyError("Quality policy unit relations are incomplete.")


def _validate_rules(policy: BannerQualityPolicy) -> None:
    if not policy.rules:
        raise QualityPolicyError("Quality policy rules are unavailable.")
    if len(policy.rules) != len({rule.rule_id for rule in policy.rules}):
        raise QualityPolicyError("Quality policy rule IDs must be unique.")
    if len(policy.rules) != len({rule.precedence for rule in policy.rules}):
        raise QualityPolicyError("Quality policy rule precedence must be total.")
    if any(rule.precedence <= 0 or not rule.origins for rule in policy.rules):
        raise QualityPolicyError("Quality policy rule metadata is invalid.")
    if {rule.reason_code for rule in policy.rules} != set(ReasonCode):
        raise QualityPolicyError("Every public reason code must have a rule.")

    relation_ids = {relation.relation_id for relation in policy.unit_relations}
    for rule in policy.rules:
        columns = _columns_for_rule(policy, rule)
        if not columns:
            raise QualityPolicyError("Quality policy rule scope is empty.")
        if any(reference not in relation_ids for reference in rule.unit_relation_ids):
            raise QualityPolicyError("Quality policy rule relation is unavailable.")

    _validate_missing_rules(policy)
    _validate_non_finite_rules(policy)
    _validate_physical_rules(policy)
    _validate_duplicate_rules(policy)
    _validate_unit_rules(policy)
    _validate_iqr_rule(policy)


def _validate_missing_rules(policy: BannerQualityPolicy) -> None:
    rules = policy.rules_for_reason(ReasonCode.REQUIRED_VALUE_MISSING)
    expected_roles = set(_COLUMN_ROLES)
    if len(rules) != len(expected_roles) or {rule.scope.role for rule in rules} != (
        expected_roles
    ):
        raise QualityPolicyError("Missing-value policy must be declared by role.")
    covered: set[str] = set()
    for rule in rules:
        names = {column.name for column in _columns_for_rule(policy, rule)}
        if (
            covered.intersection(names)
            or rule.action is not Action.REJECT
            or rule.severity is not ValidationSeverity.ERROR
            or rule.threshold != RuleThreshold("is_missing", None, None)
            or rule.uses_iqr
            or rule.unit_relation_ids
        ):
            raise QualityPolicyError("Missing-value policy is invalid.")
        covered.update(names)
    if covered != {column.name for column in policy.columns}:
        raise QualityPolicyError("Missing-value policy does not cover the contract.")


def _validate_non_finite_rules(policy: BannerQualityPolicy) -> None:
    rules = policy.rules_for_reason(ReasonCode.NON_FINITE)
    expected = {
        column.name for column in policy.columns if column.role == "measurement"
    }
    covered: set[str] = set()
    for rule in rules:
        names = {column.name for column in _columns_for_rule(policy, rule)}
        if (
            covered.intersection(names)
            or rule.action is not Action.REJECT
            or rule.severity is not ValidationSeverity.ERROR
            or rule.threshold != RuleThreshold("not_finite", None, None)
            or rule.uses_iqr
            or rule.unit_relation_ids
        ):
            raise QualityPolicyError("Non-finite policy is invalid or overlapping.")
        covered.update(names)
    if covered != expected:
        raise QualityPolicyError("Non-finite policy is invalid.")


def _validate_physical_rules(policy: BannerQualityPolicy) -> None:
    observed: dict[str, float] = {}
    for rule in policy.rules_for_reason(ReasonCode.INVALID_PHYSICAL_DOMAIN):
        value = rule.threshold.value
        if (
            not isinstance(value, float)
            or rule.action is not Action.REJECT
            or rule.severity is not ValidationSeverity.ERROR
            or rule.threshold.operator != "<"
            or rule.threshold.inclusive is not False
            or rule.uses_iqr
            or rule.unit_relation_ids
        ):
            raise QualityPolicyError("Physical-domain policy is invalid.")
        for column in _columns_for_rule(policy, rule):
            if column.name in observed:
                raise QualityPolicyError("Physical-domain policy overlaps columns.")
            observed[column.name] = value
    if observed != {
        name: float(value) for name, value in BANNER_PHYSICAL_MINIMUMS.items()
    }:
        raise QualityPolicyError("Physical-domain policy diverges from the contract.")


def _validate_duplicate_rules(policy: BannerQualityPolicy) -> None:
    conflicting = policy.rules_for_reason(ReasonCode.CONFLICTING_DUPLICATE)
    identical = policy.rules_for_reason(ReasonCode.IDENTICAL_DUPLICATE)
    if (
        len(conflicting) != 1
        or conflicting[0].action is not Action.REJECT
        or conflicting[0].severity is not ValidationSeverity.ERROR
        or conflicting[0].threshold.operator != "repeated_key_with_different_record"
        or conflicting[0].threshold.value != "all_non_key_columns"
        or conflicting[0].threshold.inclusive is not None
        or conflicting[0].scope.role != "record"
        or conflicting[0].scope.columns != policy.duplicate_key_columns
        or conflicting[0].uses_iqr
        or conflicting[0].unit_relation_ids
    ):
        raise QualityPolicyError("Conflicting-duplicate policy is invalid.")
    if (
        len(identical) != 1
        or identical[0].action is not Action.MAP
        or identical[0].severity is not ValidationSeverity.WARNING
        or identical[0].threshold.operator != "repeated_key_with_identical_record"
        or identical[0].threshold.value != "all_contract_columns"
        or identical[0].threshold.inclusive is not None
        or identical[0].scope.role != "record"
        or identical[0].scope.columns != policy.duplicate_key_columns
        or identical[0].uses_iqr
        or identical[0].unit_relation_ids
    ):
        raise QualityPolicyError("Identical-duplicate policy is invalid.")


def _validate_unit_rules(policy: BannerQualityPolicy) -> None:
    rules = policy.rules_for_reason(ReasonCode.INCONSISTENT_REDUNDANT_UNIT)
    by_id = {rule.rule_id: rule for rule in rules}
    all_relations = {relation.relation_id for relation in policy.unit_relations}
    expected_semantics = {
        _AMBIGUOUS_UNIT_RULE_ID: (
            "outside_tolerance_without_unique_trusted_counterpart",
            Action.REJECT,
            ValidationSeverity.ERROR,
        ),
        _DETERMINISTIC_UNIT_RULE_ID: (
            "outside_tolerance_with_unique_trusted_counterpart",
            Action.CORRECT_DETERMINISTICALLY,
            ValidationSeverity.WARNING,
        ),
    }
    expected_columns = {
        column_name
        for relation in policy.unit_relations
        for column_name in (relation.left_column, relation.right_column)
    }
    if set(by_id) != set(expected_semantics) or any(
        (
            rule.threshold.operator,
            rule.action,
            rule.severity,
        )
        != expected_semantics[rule.rule_id]
        or set(rule.unit_relation_ids) != all_relations
        or rule.scope.role != "measurement"
        or rule.uses_iqr
        or rule.threshold.value != "declared_relation"
        or rule.threshold.inclusive is not False
        or {column.name for column in _columns_for_rule(policy, rule)}
        != expected_columns
        for rule in rules
    ):
        raise QualityPolicyError("Redundant-unit policy is invalid.")


def _validate_iqr_rule(policy: BannerQualityPolicy) -> None:
    rules = policy.rules_for_reason(ReasonCode.IQR_OUTLIER)
    expected = {
        column.name for column in policy.columns if column.role == "measurement"
    }
    if (
        len(rules) != 1
        or rules[0].action is not Action.FLAG
        or rules[0].severity is not ValidationSeverity.WARNING
        or rules[0].threshold
        != RuleThreshold("outside_iqr_fences", "Q1 - 1.5 * IQR; Q3 + 1.5 * IQR", False)
        or not rules[0].uses_iqr
        or rules[0].unit_relation_ids
        or {column.name for column in _columns_for_rule(policy, rules[0])} != expected
    ):
        raise QualityPolicyError("IQR rule is invalid.")


def _columns_for_rule(
    policy: BannerQualityPolicy, rule: QualityRule
) -> tuple[QualityPolicyColumn, ...]:
    if rule.scope.role == "record":
        selected = set(rule.scope.columns)
    else:
        if rule.scope.role not in _COLUMN_ROLES:
            raise QualityPolicyError("Quality policy rule role is invalid.")
        role_columns = tuple(
            column for column in policy.columns if column.role == rule.scope.role
        )
        selected = (
            set(rule.scope.columns)
            if rule.scope.columns
            else {column.name for column in role_columns}
        )
        if any(
            column.name in selected and column.role != rule.scope.role
            for column in policy.columns
        ):
            raise QualityPolicyError("Quality policy scope conflicts with column role.")
    known = {column.name for column in policy.columns}
    if not selected or not selected.issubset(known):
        raise QualityPolicyError("Quality policy scope contains an unknown column.")
    return tuple(column for column in policy.columns if column.name in selected)


def _calculate_policy_id(policy: BannerQualityPolicy) -> str:
    canonical = json.dumps(
        _semantic_payload(policy),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8", errors="strict")
    return sha256(canonical).hexdigest()


def _semantic_payload(policy: BannerQualityPolicy) -> dict[str, object]:
    return {
        "schema_version": policy.schema_version,
        "policy_version": policy.policy_version,
        "subject": policy.subject,
        "contract_version": policy.contract_version,
        "profile_schema_version": policy.profile_schema_version,
        "columns": [
            {
                "position": column.position,
                "name": column.name,
                "role": column.role,
                "unit": column.unit,
            }
            for column in policy.columns
        ],
        "duplicate_key_columns": list(policy.duplicate_key_columns),
        "reason_code_order": [reason.value for reason in policy.reason_code_order],
        "effective_action_precedence": [
            action.value for action in policy.effective_action_precedence
        ],
        "iqr": {
            "q1_probability": policy.iqr.q1_probability,
            "q3_probability": policy.iqr.q3_probability,
            "quantile_method": policy.iqr.quantile_method,
            "multiplier": policy.iqr.multiplier,
            "population": policy.iqr.population,
            "lower_fence_operator": policy.iqr.lower_fence_operator,
            "upper_fence_operator": policy.iqr.upper_fence_operator,
            "outlier_boundary_inclusive": policy.iqr.outlier_boundary_inclusive,
            "preserves_record": policy.iqr.preserves_record,
            "origin": policy.iqr.origin,
        },
        "unit_relations": [
            {
                "relation_id": relation.relation_id,
                "left_column": relation.left_column,
                "left_unit": relation.left_unit,
                "right_column": relation.right_column,
                "right_unit": relation.right_unit,
                "multiplier": relation.multiplier,
                "offset": relation.offset,
                "relation": relation.relation,
                "absolute_tolerance": relation.absolute_tolerance,
                "relative_tolerance": relation.relative_tolerance,
                "tolerance_inclusive": relation.tolerance_inclusive,
                "origin": relation.origin,
            }
            for relation in policy.unit_relations
        ],
        "rules": [
            {
                "rule_id": rule.rule_id,
                "reason_code": rule.reason_code.value,
                "action": rule.action.value,
                "severity": rule.severity.value,
                "precedence": rule.precedence,
                "condition": rule.condition,
                "scope": {
                    "role": rule.scope.role,
                    "columns": list(rule.scope.columns),
                },
                "threshold": {
                    "operator": rule.threshold.operator,
                    "value": rule.threshold.value,
                    "inclusive": rule.threshold.inclusive,
                },
                "uses_iqr": rule.uses_iqr,
                "unit_relation_ids": list(rule.unit_relation_ids),
                "justification": rule.justification,
                "origins": list(rule.origins),
            }
            for rule in policy.rules
        ],
        "limitations": list(policy.limitations),
    }


def _baseline_summary(
    baseline_json_bytes: bytes,
    policy: BannerQualityPolicy,
    *,
    manifest_path: Path,
) -> _BaselineSummary:
    try:
        validate_banner_baseline_bytes(
            json_bytes=baseline_json_bytes,
            manifest_path=manifest_path,
        )
        # Keep the renderer as a second proof that the validated artifact is derivable.
        render_banner_baseline_markdown(baseline_json_bytes)
    except BannerBaselineError:
        raise QualityPolicyError("Tracked aggregate baseline is invalid.") from None
    payload = _decode_json(baseline_json_bytes)
    profile = _mapping(payload.get("profile"), "baseline.profile")
    volume = _mapping(profile.get("volume"), "baseline.volume")
    duplicates = _mapping(profile.get("duplicates"), "baseline.duplicates")
    columns = tuple(
        _mapping(item, "baseline.column")
        for item in _sequence(profile.get("columns"), "baseline.columns")
    )
    pairs = tuple(
        _mapping(item, "baseline.unit_pair")
        for item in _sequence(
            profile.get("redundant_unit_pairs"), "baseline.redundant_unit_pairs"
        )
    )
    by_name = {
        _text(column.get("name"), "baseline.column.name"): column for column in columns
    }
    if set(by_name) != {column.name for column in policy.columns}:
        raise QualityPolicyError("Tracked baseline columns are incompatible.")
    measurement_names = {
        column.name for column in policy.columns if column.role == "measurement"
    }
    missing_count = sum(
        _nonnegative_integer(by_name[name].get("missing_count"), "baseline.missing")
        for name in by_name
    )
    non_finite_count = sum(
        _nonnegative_integer(by_name[name].get("nan_count"), "baseline.nan")
        + _nonnegative_integer(by_name[name].get("infinite_count"), "baseline.infinite")
        for name in measurement_names
    )
    physical_domain_count = sum(
        _nonnegative_integer(
            by_name[name].get("domain_violation_count"), "baseline.domain"
        )
        for name in BANNER_PHYSICAL_MINIMUMS
    )
    iqr_outlier_count = 0
    iqr_column_count = 0
    for name in measurement_names:
        statistics = by_name[name].get("numeric_statistics")
        if statistics is None:
            continue
        numeric = _mapping(statistics, "baseline.numeric_statistics")
        iqr_outlier_count += _nonnegative_integer(
            numeric.get("iqr_outlier_count"), "baseline.iqr_outlier_count"
        )
        iqr_column_count += 1
    return _BaselineSummary(
        row_count=_nonnegative_integer(volume.get("row_count"), "baseline.row_count"),
        column_count=_nonnegative_integer(
            volume.get("observed_column_count"), "baseline.column_count"
        ),
        missing_count=missing_count,
        non_finite_count=non_finite_count,
        physical_domain_count=physical_domain_count,
        identical_duplicate_group_count=_nonnegative_integer(
            duplicates.get("complete_duplicate_group_count"),
            "baseline.complete_duplicate_group_count",
        ),
        identical_duplicate_excess_count=_nonnegative_integer(
            duplicates.get("complete_duplicate_excess_row_count"),
            "baseline.complete_duplicate_excess_row_count",
        ),
        conflicting_duplicate_group_count=_nonnegative_integer(
            duplicates.get("conflicting_key_group_count"),
            "baseline.conflicting_key_group_count",
        ),
        conflicting_duplicate_row_count=_nonnegative_integer(
            duplicates.get("conflicting_row_count"), "baseline.conflicting_row_count"
        ),
        unit_inconsistency_count=sum(
            _nonnegative_integer(
                pair.get("inconsistent_count"), "baseline.unit_inconsistent"
            )
            for pair in pairs
        ),
        unit_pair_count=len(pairs),
        iqr_outlier_count=iqr_outlier_count,
        iqr_column_count=iqr_column_count,
    )


def _contract_role(name: str, logical_type: LogicalType) -> str:
    if name == "id":
        return "identifier"
    if name == "created_at":
        return "event_timestamp"
    if name == "fault":
        return "raw_label"
    if logical_type is LogicalType.FLOAT64:
        return "measurement"
    raise QualityPolicyError("Contract column has no supported policy role.")


def _relation_text(relation: UnitRelationPolicy) -> str:
    multiplier = f"{relation.multiplier:g}"
    offset = f"{abs(relation.offset):g}"
    if relation.offset > 0:
        return f"right = left * {multiplier} + {offset}"
    if relation.offset < 0:
        return f"right = left * {multiplier} - {offset}"
    return f"right = left * {multiplier}"


def _validate_quality_match(policy: BannerQualityPolicy, match: QualityMatch) -> None:
    for field_name, value in (
        ("rule_id", match.rule_id),
        ("column_name", match.column_name),
        ("unit_relation_id", match.unit_relation_id),
        ("trusted_column", match.trusted_column),
    ):
        if value is not None:
            _validate_safe_text(_text(value, f"quality_match.{field_name}"))

    rule = policy.rule(match.rule_id)
    if rule.reason_code is ReasonCode.INCONSISTENT_REDUNDANT_UNIT:
        if match.unit_relation_id is None:
            raise QualityPolicyError("Unit quality match requires a relation.")
        relations = {
            relation.relation_id: relation for relation in policy.unit_relations
        }
        relation = relations.get(match.unit_relation_id)
        if relation is None or match.unit_relation_id not in rule.unit_relation_ids:
            raise QualityPolicyError("Unit quality match relation is unavailable.")

        if rule.rule_id == _AMBIGUOUS_UNIT_RULE_ID:
            if match.column_name is not None or match.trusted_column is not None:
                raise QualityPolicyError(
                    "Ambiguous unit match must not assert a trusted column."
                )
            return
        if rule.rule_id != _DETERMINISTIC_UNIT_RULE_ID:
            raise QualityPolicyError("Unit quality match rule is unsupported.")
        if match.column_name is None or match.trusted_column is None:
            raise QualityPolicyError(
                "Deterministic unit correction requires typed trusted-column proof."
            )
        endpoints = {relation.left_column, relation.right_column}
        if (
            match.column_name == match.trusted_column
            or {match.column_name, match.trusted_column} != endpoints
        ):
            raise QualityPolicyError(
                "Deterministic unit correction proof is incompatible with relation."
            )
        return

    if match.unit_relation_id is not None or match.trusted_column is not None:
        raise QualityPolicyError("Quality match context is not allowed for this rule.")
    if rule.scope.role == "record":
        if match.column_name is not None:
            raise QualityPolicyError("Record quality match must not select a column.")
        return
    if match.column_name is None or match.column_name not in {
        column.name for column in policy.columns_for_rule(rule.rule_id)
    }:
        raise QualityPolicyError("Quality match column is outside the rule scope.")


def _validate_unit_match_groups(matches: set[QualityMatch]) -> None:
    grouped: dict[str, list[QualityMatch]] = {}
    for match in matches:
        if match.rule_id not in {
            _AMBIGUOUS_UNIT_RULE_ID,
            _DETERMINISTIC_UNIT_RULE_ID,
        }:
            continue
        if match.unit_relation_id is None:
            raise QualityPolicyError("Unit quality match requires a relation.")
        grouped.setdefault(match.unit_relation_id, []).append(match)

    for relation_matches in grouped.values():
        deterministic = tuple(
            match
            for match in relation_matches
            if match.rule_id == _DETERMINISTIC_UNIT_RULE_ID
        )
        ambiguous = any(
            match.rule_id == _AMBIGUOUS_UNIT_RULE_ID for match in relation_matches
        )
        if len(deterministic) > 1 or (deterministic and ambiguous):
            raise QualityPolicyError(
                "Unit quality matches do not prove one unique counterpart."
            )


def _mapping(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise QualityPolicyError(f"Quality policy {context} must be an object.")
    raw = cast(Mapping[object, object], value)
    if any(not isinstance(key, str) for key in raw):
        raise QualityPolicyError(f"Quality policy {context} has invalid keys.")
    return dict(cast(Mapping[str, object], raw))


def _sequence(value: object, context: str) -> list[object]:
    if not isinstance(value, list):
        raise QualityPolicyError(f"Quality policy {context} must be an array.")
    return cast(list[object], value)


def _exact_keys(
    mapping: Mapping[str, object], expected: tuple[str, ...], context: str
) -> None:
    if set(mapping) != set(expected) or len(mapping) != len(expected):
        raise QualityPolicyError(f"Quality policy {context} fields are invalid.")


def _text(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QualityPolicyError(f"Quality policy {context} must be non-empty text.")
    return value


def _integer(value: object, context: str) -> int:
    if type(value) is not int:
        raise QualityPolicyError(f"Quality policy {context} must be an integer.")
    return value


def _nonnegative_integer(value: object, context: str) -> int:
    result = _integer(value, context)
    if result < 0:
        raise QualityPolicyError(f"Quality policy {context} must be non-negative.")
    return result


def _number(value: object, context: str) -> float:
    if type(value) not in {int, float}:
        raise QualityPolicyError(f"Quality policy {context} must be numeric.")
    result = float(cast(int | float, value))
    if not isfinite(result):
        raise QualityPolicyError(f"Quality policy {context} must be finite.")
    return result


def _boolean(value: object, context: str) -> bool:
    if type(value) is not bool:
        raise QualityPolicyError(f"Quality policy {context} must be boolean.")
    return value


def _optional_boolean(value: object, context: str) -> bool | None:
    if value is None:
        return None
    return _boolean(value, context)


def _threshold_value(value: object) -> str | float | None:
    if value is None:
        return None
    if isinstance(value, str):
        return _text(value, "rule.threshold.value")
    return _number(value, "rule.threshold.value")


def _unique_sorted_texts(value: object, context: str) -> tuple[str, ...]:
    items = tuple(_text(item, context) for item in _sequence(value, context))
    if len(items) != len(set(items)):
        raise QualityPolicyError(f"Quality policy {context} has duplicate values.")
    return tuple(sorted(items))


def _enum_value[EnumType: StrEnum](
    enum_type: type[EnumType], value: object, context: str
) -> EnumType:
    text = _text(value, context)
    try:
        return enum_type(text)
    except ValueError:
        raise QualityPolicyError(f"Quality policy {context} is unsupported.") from None


def _scan_safe_unicode(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in cast(Mapping[object, object], value).items():
            if not isinstance(key, str):
                raise QualityPolicyError("Quality policy contains a non-text key.")
            _validate_safe_text(key)
            _scan_safe_unicode(item)
        return
    if isinstance(value, list | tuple):
        for item in cast(Sequence[object], value):
            _scan_safe_unicode(item)
        return
    if isinstance(value, str):
        _validate_safe_text(value)


def _validate_safe_text(value: str) -> None:
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise QualityPolicyError(
            "Quality policy contains unsafe Unicode text."
        ) from None
    if any(
        unicode_category(character) in _UNSAFE_UNICODE_CATEGORIES for character in value
    ):
        raise QualityPolicyError("Quality policy contains unsafe Unicode text.")


def _threshold_markdown(threshold: RuleThreshold) -> str:
    value = (
        "não aplicável"
        if threshold.value is None
        else f"{threshold.value:g}"
        if isinstance(threshold.value, float)
        else threshold.value
    )
    inclusive = (
        "não aplicável" if threshold.inclusive is None else _yes_no(threshold.inclusive)
    )
    return f"{threshold.operator}; {value}; inclusivo: {inclusive}"


def _markdown_cell(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("\\", "&#92;")
        .replace("|", "&#124;")
        .replace("`", "&#96;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _yes_no(value: bool) -> str:
    return "sim" if value else "não"
