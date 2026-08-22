"""Synthetic proofs for the versioned declarative banner quality policy."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from math import inf, nan, nextafter
from pathlib import Path
from typing import IO, Any, Final, NoReturn, cast

import pandas as pd
import pytest
from prescriptive_maintenance.data import (
    BANNER_COLUMN_CATALOG,
    BANNER_PHYSICAL_MINIMUMS,
    EFFECTIVE_ACTION_PRECEDENCE,
    Action,
    QualityMatch,
    QualityPolicyError,
    ReasonCode,
    RuleBinding,
    banner_value_violates_domain,
    identify_banner_quality_policy,
    load_banner_quality_policy,
    profile_banner_dataframe,
    render_banner_quality_policy_markdown,
    resolve_quality_rules,
    validate_banner_quality_policy,
)
from synthetic_banner_factory import (
    BannerScenario,
    make_banner_dataframe,
)

_REPOSITORY_ROOT: Final = Path(__file__).parents[3]
_POLICY_PATH: Final = (
    _REPOSITORY_ROOT
    / "apps"
    / "api"
    / "src"
    / "prescriptive_maintenance"
    / "data"
    / "policies"
    / "banner_quality_policy.v1.json"
)
_BASELINE_PATH: Final = (
    _REPOSITORY_ROOT
    / "data"
    / "baselines"
    / "banner"
    / "48ce42c0362edb7e25c215c68dd8e51890d435ab6df4b359501a83b001a994b7"
    / "baseline.v1.json"
)
_MANIFEST_PATH: Final = _REPOSITORY_ROOT / "data" / "source-manifest.json"
_HUMAN_VIEW_PATH: Final = (
    _REPOSITORY_ROOT / "docs" / "data" / "banner-quality-policy.md"
)
_POLICY_ID: Final = "51190f2c6662c2ffb236d887f4bb43f1f7cb03f98dfc387441cc5410dcd3838b"


def _payload() -> dict[str, object]:
    value: object = json.loads(_POLICY_PATH.read_bytes())
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _sequence(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast(list[object], value)


def _rule_payload(payload: dict[str, object], rule_id: str) -> dict[str, object]:
    matches = [
        _mapping(item)
        for item in _sequence(payload["rules"])
        if _mapping(item).get("rule_id") == rule_id
    ]
    assert len(matches) == 1
    return matches[0]


def test_public_enums_and_total_orders_are_stable() -> None:
    assert tuple(ReasonCode) == (
        ReasonCode.REQUIRED_VALUE_MISSING,
        ReasonCode.NON_FINITE,
        ReasonCode.INVALID_PHYSICAL_DOMAIN,
        ReasonCode.CONFLICTING_DUPLICATE,
        ReasonCode.INCONSISTENT_REDUNDANT_UNIT,
        ReasonCode.IDENTICAL_DUPLICATE,
        ReasonCode.IQR_OUTLIER,
    )
    assert tuple(Action) == (
        Action.KEEP,
        Action.CORRECT_DETERMINISTICALLY,
        Action.MAP,
        Action.FLAG,
        Action.REJECT,
    )
    assert EFFECTIVE_ACTION_PRECEDENCE == (
        Action.REJECT,
        Action.CORRECT_DETERMINISTICALLY,
        Action.MAP,
        Action.FLAG,
        Action.KEEP,
    )


def test_canonical_policy_loads_with_immutable_rule_bindings() -> None:
    policy = load_banner_quality_policy()

    assert policy.policy_id == _POLICY_ID
    assert identify_banner_quality_policy(policy) == _POLICY_ID
    assert validate_banner_quality_policy(policy) is policy
    assert policy.reason_code_order == tuple(ReasonCode)
    assert policy.effective_action_precedence == EFFECTIVE_ACTION_PRECEDENCE
    assert len(policy.rule_index) == len(policy.rules)
    assert policy.rule_index["outlier.iqr.flag"] == RuleBinding(
        ReasonCode.IQR_OUTLIER,
        Action.FLAG,
    )

    mutable_view = cast(dict[str, RuleBinding], policy.rule_index)
    with pytest.raises(TypeError):
        mutable_view["synthetic.rule"] = RuleBinding(
            ReasonCode.IQR_OUTLIER,
            Action.REJECT,
        )


@pytest.mark.parametrize("location", ("root", "rule", "threshold"))
def test_schema_rejects_extra_keys_without_echoing_them(location: str) -> None:
    payload = _payload()
    canary = "synthetic_private_extra_key_9137"
    if location == "root":
        payload[canary] = True
    else:
        rule = _rule_payload(payload, "outlier.iqr.flag")
        if location == "rule":
            rule[canary] = True
        else:
            _mapping(rule["threshold"])[canary] = True

    with pytest.raises(QualityPolicyError) as raised:
        validate_banner_quality_policy(payload)

    assert canary not in str(raised.value)


def test_json_loader_rejects_duplicate_keys() -> None:
    raw = _POLICY_PATH.read_bytes().replace(
        b'"schema_version": 1,',
        b'"schema_version": 1,\n  "schema_version": 1,',
        1,
    )

    with pytest.raises(QualityPolicyError):
        validate_banner_quality_policy(raw)


@pytest.mark.parametrize(
    ("mutation", "value"),
    (
        ("schema_version", True),
        ("precedence", "10"),
        ("multiplier", "1.5"),
        ("action", "drop"),
    ),
)
def test_schema_rejects_invalid_json_types_and_enum_values(
    mutation: str, value: object
) -> None:
    payload = _payload()
    if mutation == "schema_version":
        payload["schema_version"] = value
    elif mutation == "precedence":
        _rule_payload(payload, "outlier.iqr.flag")["precedence"] = value
    elif mutation == "multiplier":
        _mapping(payload["iqr"])["multiplier"] = value
    else:
        _rule_payload(payload, "outlier.iqr.flag")["action"] = value

    with pytest.raises(QualityPolicyError):
        validate_banner_quality_policy(payload)


@pytest.mark.parametrize(
    "payload",
    (
        b"\xff",
        b'{"schema_version":1,"unsafe":"\\ud800"}',
        b'{"schema_version":1,"unsafe":"\\u0000"}',
    ),
)
def test_schema_rejects_invalid_or_unsafe_unicode(payload: bytes) -> None:
    with pytest.raises(QualityPolicyError):
        validate_banner_quality_policy(payload)


def test_equivalent_array_order_does_not_change_policy_id() -> None:
    payload = deepcopy(_payload())
    _sequence(payload["columns"]).reverse()
    _sequence(payload["unit_relations"]).reverse()
    _sequence(payload["rules"]).reverse()
    _sequence(payload["limitations"]).reverse()
    for item in _sequence(payload["rules"]):
        rule = _mapping(item)
        _sequence(_mapping(rule["scope"])["columns"]).reverse()
        _sequence(rule["unit_relation_ids"]).reverse()
        _sequence(rule["origins"]).reverse()

    policy = validate_banner_quality_policy(payload)

    assert policy.policy_id == _POLICY_ID
    assert identify_banner_quality_policy(policy) == _POLICY_ID


def test_equivalent_constructed_tuple_order_does_not_change_policy_id() -> None:
    policy = load_banner_quality_policy()
    reordered_rules = tuple(
        replace(
            rule,
            scope=replace(
                rule.scope,
                columns=tuple(reversed(rule.scope.columns)),
            ),
            unit_relation_ids=tuple(reversed(rule.unit_relation_ids)),
            origins=tuple(reversed(rule.origins)),
        )
        for rule in reversed(policy.rules)
    )
    reordered = replace(
        policy,
        columns=tuple(reversed(policy.columns)),
        duplicate_key_columns=tuple(reversed(policy.duplicate_key_columns)),
        unit_relations=tuple(reversed(policy.unit_relations)),
        rules=reordered_rules,
        limitations=tuple(reversed(policy.limitations)),
    )

    assert identify_banner_quality_policy(reordered) == policy.policy_id
    assert validate_banner_quality_policy(reordered) == policy


def test_any_semantic_change_changes_id_and_invalidates_stored_id() -> None:
    policy = load_banner_quality_policy()
    altered = replace(
        policy,
        limitations=(*policy.limitations, "Limitação pública sintética adicional."),
    )
    payload = _payload()
    _sequence(payload["limitations"]).append("Limitação pública sintética adicional.")

    assert identify_banner_quality_policy(altered) != policy.policy_id
    with pytest.raises(QualityPolicyError, match="identifier"):
        validate_banner_quality_policy(payload)


def test_unit_rule_action_swap_is_rejected_by_predicate_semantics() -> None:
    payload = _payload()
    ambiguous = _rule_payload(payload, "unit.inconsistent.ambiguous")
    deterministic = _rule_payload(payload, "unit.inconsistent.deterministic")
    ambiguous["action"], deterministic["action"] = (
        deterministic["action"],
        ambiguous["action"],
    )

    with pytest.raises(QualityPolicyError, match="Redundant-unit policy"):
        validate_banner_quality_policy(payload)


def test_semantically_duplicate_unit_pair_with_another_id_is_rejected() -> None:
    payload = _payload()
    relations = _sequence(payload["unit_relations"])
    duplicate = deepcopy(_mapping(relations[0]))
    duplicate["relation_id"] = "synthetic_duplicate_pair"
    relations.append(duplicate)

    with pytest.raises(QualityPolicyError, match="semantically unique"):
        validate_banner_quality_policy(payload)


def test_overlapping_non_finite_column_coverage_is_rejected() -> None:
    payload = _payload()
    duplicate = deepcopy(_rule_payload(payload, "measurement.non_finite"))
    duplicate["rule_id"] = "measurement.non_finite.overlap"
    duplicate["precedence"] = 55
    _sequence(payload["rules"]).append(duplicate)

    with pytest.raises(QualityPolicyError, match="overlapping"):
        validate_banner_quality_policy(payload)


def test_required_absence_is_partitioned_by_column_role() -> None:
    policy = load_banner_quality_policy()
    rules = policy.rules_for_reason(ReasonCode.REQUIRED_VALUE_MISSING)

    assert {rule.scope.role for rule in rules} == {
        "identifier",
        "event_timestamp",
        "measurement",
        "raw_label",
    }
    assert all(rule.action is Action.REJECT for rule in rules)
    assert {
        column.name
        for rule in rules
        for column in policy.columns_for_rule(rule.rule_id)
    } == {column.name for column in policy.columns}


@pytest.mark.parametrize(
    ("role", "column_name", "rule_id"),
    (
        ("identifier", "id", "required.identifier.missing"),
        ("event_timestamp", "created_at", "required.event_timestamp.missing"),
        ("measurement", "rpm", "required.measurement.missing"),
        ("raw_label", "fault", "required.raw_label.missing"),
    ),
)
def test_synthetic_required_absence_resolves_by_column_role(
    role: str,
    column_name: str,
    rule_id: str,
) -> None:
    dataframe = make_banner_dataframe()
    if column_name == "id":
        dataframe[column_name] = dataframe[column_name].astype("Int64")
    dataframe.at[0, column_name] = pd.NA
    profile = profile_banner_dataframe(dataframe, key_columns=("id",))
    column = next(item for item in profile.columns if item.name == column_name)
    policy = load_banner_quality_policy()

    decision = resolve_quality_rules(
        policy,
        (QualityMatch(rule_id, column_name=column_name),),
    )

    assert column.missing_count == 1
    assert policy.rule(rule_id).scope.role == role
    assert decision.reason_codes == (ReasonCode.REQUIRED_VALUE_MISSING,)
    assert decision.effective_action is Action.REJECT


@pytest.mark.parametrize(
    ("scenario", "counter", "matches", "expected_reasons"),
    (
        (
            BannerScenario.NAN_VALUE,
            "nan_count",
            (
                QualityMatch("required.measurement.missing", column_name="rpm"),
                QualityMatch("measurement.non_finite", column_name="rpm"),
            ),
            (ReasonCode.REQUIRED_VALUE_MISSING, ReasonCode.NON_FINITE),
        ),
        (
            BannerScenario.INFINITE_VALUE,
            "infinite_count",
            (QualityMatch("measurement.non_finite", column_name="rpm"),),
            (ReasonCode.NON_FINITE,),
        ),
    ),
)
def test_nan_and_infinity_map_to_blocking_non_finite_reason(
    scenario: BannerScenario,
    counter: str,
    matches: tuple[QualityMatch, ...],
    expected_reasons: tuple[ReasonCode, ...],
) -> None:
    dataframe = make_banner_dataframe(scenario=scenario)
    profile = profile_banner_dataframe(dataframe, key_columns=("id",))
    rpm = next(column for column in profile.columns if column.name == "rpm")
    policy = load_banner_quality_policy()
    decision = resolve_quality_rules(policy, matches)

    assert getattr(rpm, counter) == 1
    assert decision.reason_codes == expected_reasons
    assert decision.effective_action is Action.REJECT


@pytest.mark.parametrize("column_name", tuple(BANNER_PHYSICAL_MINIMUMS))
def test_physical_domain_boundaries_are_exactly_below_on_and_above(
    column_name: str,
) -> None:
    policy = load_banner_quality_policy()
    contract_column = next(
        column for column in BANNER_COLUMN_CATALOG if column.name == column_name
    )
    boundary = BANNER_PHYSICAL_MINIMUMS[column_name]
    matching_rules = [
        rule
        for rule in policy.rules_for_reason(ReasonCode.INVALID_PHYSICAL_DOMAIN)
        if column_name
        in {column.name for column in policy.columns_for_rule(rule.rule_id)}
    ]

    assert len(matching_rules) == 1
    assert matching_rules[0].threshold.value == boundary
    assert matching_rules[0].threshold.operator == "<"
    assert matching_rules[0].threshold.inclusive is False
    assert banner_value_violates_domain(nextafter(boundary, -inf), contract_column)
    assert not banner_value_violates_domain(boundary, contract_column)
    assert not banner_value_violates_domain(nextafter(boundary, inf), contract_column)


def test_identical_and_conflicting_duplicates_have_distinct_auditable_actions() -> None:
    identical_profile = profile_banner_dataframe(
        make_banner_dataframe(scenario=BannerScenario.IDENTICAL_DUPLICATE),
        key_columns=("id",),
    )
    conflicting_profile = profile_banner_dataframe(
        make_banner_dataframe(scenario=BannerScenario.CONFLICTING_DUPLICATE),
        key_columns=("id",),
    )
    policy = load_banner_quality_policy()
    identical = resolve_quality_rules(
        policy,
        (QualityMatch("duplicate.identical.map_identity"),),
    )
    conflicting = resolve_quality_rules(
        policy,
        (QualityMatch("duplicate.conflicting.reject"),),
    )

    assert identical_profile.duplicates.complete_duplicate_group_count == 1
    assert identical_profile.duplicates.conflicting_key_group_count == 0
    assert conflicting_profile.duplicates.complete_duplicate_group_count == 0
    assert conflicting_profile.duplicates.conflicting_key_group_count == 1
    assert identical.effective_action is Action.MAP
    assert identical.matches == (QualityMatch("duplicate.identical.map_identity"),)
    assert identical.reason_codes == (ReasonCode.IDENTICAL_DUPLICATE,)
    identical_rules = policy.rules_for_reason(ReasonCode.IDENTICAL_DUPLICATE)
    assert len(identical_rules) == 1
    assert "registro distinto" in identical_rules[0].justification
    assert conflicting.effective_action is Action.REJECT
    assert conflicting.reason_codes == (ReasonCode.CONFLICTING_DUPLICATE,)


def test_redundant_unit_policy_separates_deterministic_and_ambiguous_cases() -> None:
    coherent = profile_banner_dataframe(
        make_banner_dataframe(scenario=BannerScenario.COHERENT_UNIT_PAIRS),
        key_columns=("id",),
    )
    incoherent = profile_banner_dataframe(
        make_banner_dataframe(scenario=BannerScenario.INCOHERENT_UNIT_PAIRS),
        key_columns=("id",),
    )
    policy = load_banner_quality_policy()
    relation_id = "temperature_c_to_temperature_f"
    ambiguous = resolve_quality_rules(
        policy,
        (
            QualityMatch(
                "unit.inconsistent.ambiguous",
                unit_relation_id=relation_id,
            ),
        ),
    )
    deterministic = resolve_quality_rules(
        policy,
        (
            QualityMatch(
                "unit.inconsistent.deterministic",
                column_name="temperature_f",
                unit_relation_id=relation_id,
                trusted_column="temperature_c",
            ),
        ),
    )

    assert coherent.redundant_unit_pairs[0].inconsistent_count == 0
    assert incoherent.redundant_unit_pairs[0].inconsistent_count == 1
    assert ambiguous.effective_action is Action.REJECT
    assert deterministic.effective_action is Action.CORRECT_DETERMINISTICALLY
    assert (
        ambiguous.reason_codes
        == deterministic.reason_codes
        == (ReasonCode.INCONSISTENT_REDUNDANT_UNIT,)
    )


@pytest.mark.parametrize(
    ("observed", "expected_inconsistent_count"),
    (
        (nextafter(0.000001, 0.0), 0),
        (0.000001, 0),
        (nextafter(0.000001, inf), 1),
    ),
)
def test_redundant_unit_tolerance_is_inclusive_at_exact_boundary(
    observed: float,
    expected_inconsistent_count: int,
) -> None:
    policy = load_banner_quality_policy()
    relation = next(
        item
        for item in policy.unit_relations
        if item.relation_id == "x_rms_velocity_in_s_to_mm_s"
    )
    dataframe = make_banner_dataframe(scenario=BannerScenario.COHERENT_UNIT_PAIRS)
    dataframe.at[0, relation.left_column] = 0.0
    dataframe.at[0, relation.right_column] = observed

    profile = profile_banner_dataframe(dataframe, key_columns=("id",))
    pair = next(
        item
        for item in profile.redundant_unit_pairs
        if item.left_column == relation.left_column
        and item.right_column == relation.right_column
    )

    assert relation.absolute_tolerance == 0.000001
    assert relation.relative_tolerance == 0.000001
    assert relation.tolerance_inclusive is True
    assert pair.inconsistent_count == expected_inconsistent_count


@pytest.mark.parametrize(
    ("match", "message"),
    (
        (
            QualityMatch("unit.inconsistent.deterministic"),
            "requires a relation",
        ),
        (
            QualityMatch(
                "unit.inconsistent.deterministic",
                unit_relation_id="temperature_c_to_temperature_f",
            ),
            "trusted-column proof",
        ),
        (
            QualityMatch(
                "unit.inconsistent.deterministic",
                column_name="temperature_f",
                unit_relation_id="temperature_c_to_temperature_f",
                trusted_column="rpm",
            ),
            "incompatible with relation",
        ),
        (
            QualityMatch(
                "unit.inconsistent.deterministic",
                column_name="temperature_f",
                unit_relation_id="unknown_relation",
                trusted_column="temperature_c",
            ),
            "relation is unavailable",
        ),
        (
            QualityMatch(
                "unit.inconsistent.ambiguous",
                column_name="temperature_f",
                unit_relation_id="temperature_c_to_temperature_f",
                trusted_column="temperature_c",
            ),
            "must not assert a trusted column",
        ),
    ),
)
def test_unit_matches_reject_missing_or_incompatible_typed_proof(
    match: QualityMatch,
    message: str,
) -> None:
    with pytest.raises(QualityPolicyError, match=message):
        resolve_quality_rules(load_banner_quality_policy(), (match,))


def test_unit_matches_reject_multiple_deterministic_proofs_for_one_relation() -> None:
    relation_id = "temperature_c_to_temperature_f"
    forward = QualityMatch(
        "unit.inconsistent.deterministic",
        column_name="temperature_f",
        unit_relation_id=relation_id,
        trusted_column="temperature_c",
    )
    reverse = QualityMatch(
        "unit.inconsistent.deterministic",
        column_name="temperature_c",
        unit_relation_id=relation_id,
        trusted_column="temperature_f",
    )
    deduplicated = resolve_quality_rules(
        load_banner_quality_policy(),
        (forward, forward),
    )

    assert deduplicated.matches == (forward,)
    assert deduplicated.effective_action is Action.CORRECT_DETERMINISTICALLY
    with pytest.raises(QualityPolicyError, match="unique counterpart"):
        resolve_quality_rules(load_banner_quality_policy(), (forward, reverse))


def test_unit_matches_reject_ambiguous_and_deterministic_for_one_relation() -> None:
    relation_id = "temperature_c_to_temperature_f"
    ambiguous = QualityMatch(
        "unit.inconsistent.ambiguous",
        unit_relation_id=relation_id,
    )
    deterministic = QualityMatch(
        "unit.inconsistent.deterministic",
        column_name="temperature_f",
        unit_relation_id=relation_id,
        trusted_column="temperature_c",
    )

    with pytest.raises(QualityPolicyError, match="unique counterpart"):
        resolve_quality_rules(
            load_banner_quality_policy(),
            (ambiguous, deterministic),
        )


@pytest.mark.parametrize(
    "match",
    (
        QualityMatch("outlier.iqr.flag", column_name="id"),
        QualityMatch("duplicate.identical.map_identity", column_name="id"),
        QualityMatch(
            "measurement.non_finite",
            column_name="rpm",
            unit_relation_id="temperature_c_to_temperature_f",
        ),
    ),
)
def test_match_context_is_allowlisted_by_rule_applicability(
    match: QualityMatch,
) -> None:
    with pytest.raises(QualityPolicyError):
        resolve_quality_rules(load_banner_quality_policy(), (match,))


def test_match_identity_deduplicates_exact_repeats_but_preserves_contexts() -> None:
    policy = load_banner_quality_policy()
    rpm = QualityMatch("measurement.non_finite", column_name="rpm")
    temperature = QualityMatch(
        "measurement.non_finite",
        column_name="temperature_c",
    )
    first_relation = QualityMatch(
        "unit.inconsistent.ambiguous",
        unit_relation_id="temperature_c_to_temperature_f",
    )
    second_relation = QualityMatch(
        "unit.inconsistent.ambiguous",
        unit_relation_id="x_rms_velocity_in_s_to_mm_s",
    )

    decision = resolve_quality_rules(
        policy,
        (rpm, temperature, rpm, second_relation, first_relation, first_relation),
    )

    assert decision.matches == (
        temperature,
        rpm,
        first_relation,
        second_relation,
    )
    assert decision.reason_codes == (
        ReasonCode.NON_FINITE,
        ReasonCode.INCONSISTENT_REDUNDANT_UNIT,
    )
    assert decision.effective_action is Action.REJECT


def test_resolver_revalidates_constructed_policy() -> None:
    policy = replace(load_banner_quality_policy(), policy_id="0" * 64)

    with pytest.raises(QualityPolicyError, match="identifier"):
        resolve_quality_rules(policy, ())


def test_corrected_and_mapped_matches_coexist_but_correction_prevails() -> None:
    policy = load_banner_quality_policy()
    correction = QualityMatch(
        "unit.inconsistent.deterministic",
        column_name="temperature_f",
        unit_relation_id="temperature_c_to_temperature_f",
        trusted_column="temperature_c",
    )
    mapping = QualityMatch("duplicate.identical.map_identity")

    decision = resolve_quality_rules(
        policy,
        (mapping, correction),
    )

    assert decision.matches == (correction, mapping)
    assert decision.reason_codes == (
        ReasonCode.INCONSISTENT_REDUNDANT_UNIT,
        ReasonCode.IDENTICAL_DUPLICATE,
    )
    assert decision.actions == (Action.CORRECT_DETERMINISTICALLY, Action.MAP)
    assert decision.effective_action is Action.CORRECT_DETERMINISTICALLY


def test_all_concurrent_matches_survive_effective_action_resolution() -> None:
    policy = load_banner_quality_policy()
    matched = (
        QualityMatch("outlier.iqr.flag", column_name="rpm"),
        QualityMatch("duplicate.identical.map_identity"),
        QualityMatch(
            "unit.inconsistent.deterministic",
            column_name="temperature_f",
            unit_relation_id="temperature_c_to_temperature_f",
            trusted_column="temperature_c",
        ),
        QualityMatch("duplicate.conflicting.reject"),
    )

    decision = resolve_quality_rules(policy, matched)

    assert len(decision.matches) == len(matched)
    assert decision.actions == (
        Action.REJECT,
        Action.CORRECT_DETERMINISTICALLY,
        Action.MAP,
        Action.FLAG,
    )
    assert decision.effective_action is Action.REJECT
    assert decision.reason_codes == (
        ReasonCode.CONFLICTING_DUPLICATE,
        ReasonCode.INCONSISTENT_REDUNDANT_UNIT,
        ReasonCode.IDENTICAL_DUPLICATE,
        ReasonCode.IQR_OUTLIER,
    )


def test_no_match_has_keep_as_effective_action_without_an_audit_event() -> None:
    decision = resolve_quality_rules(load_banner_quality_policy(), ())

    assert decision.matches == ()
    assert decision.reason_codes == ()
    assert decision.actions == ()
    assert decision.effective_action is Action.KEEP


def test_iqr_uses_only_finite_values_and_has_strict_boundaries() -> None:
    base = make_banner_dataframe().iloc[[0]]
    dataframe = pd.concat([base] * 22, ignore_index=True)
    dataframe["id"] = pd.Series(range(-32022, -32000), dtype="int64")
    dataframe["rpm"] = pd.Series(
        (
            -3.000001,
            -3.0,
            *([0.0] * 8),
            *([2.0] * 8),
            5.0,
            5.000001,
            nan,
            inf,
        ),
        dtype="float64",
    )
    profile = profile_banner_dataframe(dataframe, key_columns=("id",))
    rpm = next(column for column in profile.columns if column.name == "rpm")
    statistics = rpm.numeric_statistics
    policy = load_banner_quality_policy()
    decision = resolve_quality_rules(
        policy,
        (QualityMatch("outlier.iqr.flag", column_name="rpm"),),
    )

    assert statistics is not None
    assert statistics.quantile_25 == 0.0
    assert statistics.quantile_75 == 2.0
    assert statistics.iqr_lower_bound == -3.0
    assert statistics.iqr_upper_bound == 5.0
    assert statistics.iqr_outlier_count == 2
    assert rpm.nan_count == 1
    assert rpm.infinite_count == 1
    assert policy.iqr.population == "finite_values_only"
    assert policy.iqr.outlier_boundary_inclusive is False
    assert policy.iqr.preserves_record is True
    assert decision.effective_action is Action.FLAG


def test_human_view_is_derived_and_contains_only_aggregate_baseline_comparison() -> (
    None
):
    policy = load_banner_quality_policy()
    baseline_bytes = _BASELINE_PATH.read_bytes()

    rendered = render_banner_quality_policy_markdown(
        policy,
        baseline_json_bytes=baseline_bytes,
        baseline_manifest_path=_MANIFEST_PATH,
    )

    assert rendered.encode("utf-8") == _HUMAN_VIEW_PATH.read_bytes()
    assert policy.policy_id in rendered
    assert "190107 ocorrências célula-coluna" in rendered
    assert "808926 comparações" in rendered
    assert "2026-04-30T" not in rendered
    assert "categoria 1" not in rendered
    assert "`correct_deterministically` > `map`" in rendered


def test_human_view_rejects_non_aggregate_baseline_fields() -> None:
    baseline: object = json.loads(_BASELINE_PATH.read_bytes())
    assert isinstance(baseline, dict)
    typed_baseline = cast(dict[str, object], baseline)
    typed_baseline["row_level_canary"] = []

    with pytest.raises(QualityPolicyError, match="aggregate baseline"):
        render_banner_quality_policy_markdown(
            load_banner_quality_policy(),
            baseline_json_bytes=json.dumps(typed_baseline).encode("utf-8"),
            baseline_manifest_path=_MANIFEST_PATH,
        )


def test_human_view_rejects_consistently_reidentified_unapproved_baseline() -> None:
    baseline: object = json.loads(_BASELINE_PATH.read_bytes())
    assert isinstance(baseline, dict)
    typed_baseline = cast(dict[str, object], baseline)
    source = _mapping(typed_baseline["source"])
    fictitious_identity = "0" * 64
    source["sha256"] = fictitious_identity
    integrity = _mapping(typed_baseline["integrity"])
    for item in _sequence(integrity["rounds"]):
        round_payload = _mapping(item)
        _mapping(round_payload["pre"])["sha256"] = fictitious_identity
        _mapping(round_payload["post"])["sha256"] = fictitious_identity
    reidentified_bytes = (
        json.dumps(
            typed_baseline,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            separators=(",", ": "),
        )
        + "\n"
    ).encode("utf-8")

    with pytest.raises(QualityPolicyError, match="aggregate baseline"):
        render_banner_quality_policy_markdown(
            load_banner_quality_policy(),
            baseline_json_bytes=reidentified_bytes,
            baseline_manifest_path=_MANIFEST_PATH,
        )


def test_policy_loader_and_renderer_never_discover_or_open_originals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline_bytes = _BASELINE_PATH.read_bytes()
    opened: list[Path] = []
    original_open = Path.open
    protected_names = {
        "banner.csv",
        "11 - prova prtica.pdf",
        *(f"Doc{number}.pdf" for number in range(1, 7)),
    }

    def reject_discovery(*_args: object, **_kwargs: object) -> NoReturn:
        pytest.fail("quality policy must not discover local materials")

    def guarded_open(
        path: Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> IO[Any]:
        assert path.name not in protected_names
        opened.append(path)
        return original_open(path, mode, buffering, encoding, errors, newline)

    monkeypatch.setattr(Path, "glob", reject_discovery)
    monkeypatch.setattr(Path, "rglob", reject_discovery)
    monkeypatch.setattr(Path, "open", guarded_open)

    policy = load_banner_quality_policy(_POLICY_PATH)
    rendered = render_banner_quality_policy_markdown(
        policy,
        baseline_json_bytes=baseline_bytes,
        baseline_manifest_path=_MANIFEST_PATH,
    )

    assert rendered.startswith("# Política de qualidade")
    assert opened == [_POLICY_PATH, _MANIFEST_PATH]
