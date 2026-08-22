"""Deterministic aggregate-only proofs for banner quality profiling."""

from __future__ import annotations

import builtins
import json
import locale
from pathlib import Path
from typing import Final, NoReturn

import pandas as pd
import pytest
from prescriptive_maintenance.data import (
    BANNER_COLUMN_NAMES,
    BANNER_PROFILE_SCHEMA_VERSION,
    PUBLIC_BANNER_PROFILE_SCHEMA,
    BannerDataProfile,
    BannerProfileConfigurationError,
    ColumnProfile,
    ProfileFieldClassification,
    ProfilePrivacyError,
    PublicProfileField,
    TemporalOrder,
    banner_profile_json_bytes,
    profile_banner_dataframe,
    render_banner_profile_markdown,
    validate_public_profile_schema,
)
from synthetic_banner_factory import (
    SYNTHETIC_FAULT_ALLOWLIST,
    BannerScenario,
    make_banner_dataframe,
)

_DECLARED_KEY: Final = ("id", "created_at")
_GOLDEN_PATH: Final = Path(__file__).parent / "golden" / "banner_profile.json"


def _profile(
    scenario: BannerScenario = BannerScenario.VALID,
    *,
    allowed_fault_categories: frozenset[str] | None = SYNTHETIC_FAULT_ALLOWLIST,
) -> BannerDataProfile:
    return profile_banner_dataframe(
        make_banner_dataframe(scenario=scenario),
        key_columns=_DECLARED_KEY,
        allowed_fault_categories=allowed_fault_categories,
    )


def _column(profile: BannerDataProfile, name: str) -> ColumnProfile:
    return next(column for column in profile.columns if column.name == name)


def test_profile_is_typed_complete_and_uses_fixed_numeric_definitions() -> None:
    profile = _profile()
    rpm = _column(profile, "rpm")

    assert profile.profile_schema_version == BANNER_PROFILE_SCHEMA_VERSION == 1
    assert tuple(column.name for column in profile.columns) == BANNER_COLUMN_NAMES
    assert len(profile.columns) == 26
    assert profile.volume.row_count == 3
    assert profile.volume.observed_column_count == 26
    assert profile.volume.missing_expected_column_count == 0
    assert profile.volume.unexpected_column_count == 0
    assert profile.volume.columns_in_contract_order is True
    assert profile.definitions.quantile_probabilities == (0.25, 0.5, 0.75)
    assert profile.definitions.quantile_method == "linear_type_7"
    assert profile.definitions.standard_deviation == "population_ddof_0"
    assert profile.definitions.temporal_order_basis == (
        "valid_timestamps_in_input_row_order"
    )
    assert profile.definitions.cadence_method == (
        "mode_of_sorted_distinct_intervals_ties_to_smallest"
    )
    assert profile.definitions.gap_definition == (
        "interval_strictly_greater_than_nominal_cadence"
    )
    assert profile.definitions.iqr_multiplier == 1.5
    assert profile.definitions.timezone == "UTC"
    assert profile.definitions.decimal_places == 6
    assert profile.definitions.rounding_mode == "decimal_half_even"
    assert profile.definitions.unavailable_numeric_value == "json_null"
    assert profile.definitions.percentage_denominators == (
        "column=observed;label=valid;unit_pair=comparable"
    )
    assert profile.definitions.column_order == "banner_contract_position_ascending"
    assert profile.definitions.category_order == "unicode_code_point_ascending"
    assert profile.definitions.json_key_order == "public_schema_declaration_order"
    assert _column(profile, "id").numeric_statistics is None
    assert rpm.numeric_statistics is not None
    assert rpm.numeric_statistics.finite_count == 3
    assert rpm.numeric_statistics.minimum == 1700.0
    assert rpm.numeric_statistics.maximum == 1800.0
    assert rpm.numeric_statistics.mean == 1750.0
    assert rpm.numeric_statistics.population_standard_deviation == 40.824829
    assert rpm.numeric_statistics.quantile_25 == 1725.0
    assert rpm.numeric_statistics.median == 1750.0
    assert rpm.numeric_statistics.quantile_75 == 1775.0
    assert rpm.numeric_statistics.iqr == 50.0
    assert rpm.numeric_statistics.iqr_lower_bound == 1650.0
    assert rpm.numeric_statistics.iqr_upper_bound == 1850.0
    assert rpm.numeric_statistics.iqr_outlier_count == 0


def test_empty_dataframe_keeps_every_column_and_normalizes_unavailable_values() -> None:
    dataframe = make_banner_dataframe().iloc[0:0]

    profile = profile_banner_dataframe(dataframe, key_columns=_DECLARED_KEY)

    assert len(profile.columns) == 26
    assert profile.volume.row_count == 0
    assert profile.temporal.period_start_utc is None
    assert profile.temporal.period_end_utc is None
    assert profile.temporal.nominal_cadence_seconds is None
    assert profile.labels.distribution == ()
    for column in profile.columns:
        assert column.present is True
        assert column.observed_count == 0
        assert column.missing_percentage is None
    rpm_statistics = _column(profile, "rpm").numeric_statistics
    assert rpm_statistics is not None
    assert rpm_statistics.finite_count == 0
    assert rpm_statistics.minimum is None


@pytest.mark.parametrize(
    ("scenario", "column_name", "attribute", "expected"),
    (
        (BannerScenario.NULL_VALUE, "fault", "null_count", 1),
        (BannerScenario.NAN_VALUE, "rpm", "nan_count", 1),
        (BannerScenario.INFINITE_VALUE, "rpm", "infinite_count", 1),
        (
            BannerScenario.PHYSICAL_VIOLATION,
            "z_rms_velocity_in_s",
            "domain_violation_count",
            1,
        ),
        (
            BannerScenario.INVALID_TIMESTAMP,
            "created_at",
            "domain_violation_count",
            1,
        ),
        (BannerScenario.EMPTY_FAULT, "fault", "domain_violation_count", 1),
        (
            BannerScenario.UNKNOWN_CATEGORY,
            "fault",
            "unknown_category_count",
            1,
        ),
    ),
)
def test_column_findings_are_counted_separately(
    scenario: BannerScenario,
    column_name: str,
    attribute: str,
    expected: int,
) -> None:
    profile = _profile(scenario)
    column = _column(profile, column_name)

    assert getattr(column, attribute) == expected
    assert column.observed_count == 3
    assert len(profile.columns) == 26


def test_nan_and_infinity_are_excluded_from_finite_statistics() -> None:
    nan_profile = _profile(BannerScenario.NAN_VALUE)
    infinite_profile = _profile(BannerScenario.INFINITE_VALUE)

    nan_statistics = _column(nan_profile, "rpm").numeric_statistics
    infinite_statistics = _column(infinite_profile, "rpm").numeric_statistics

    assert nan_statistics is not None
    assert infinite_statistics is not None
    assert nan_statistics.finite_count == 2
    assert infinite_statistics.finite_count == 2
    assert nan_statistics.minimum == infinite_statistics.minimum == 1700.0
    assert nan_statistics.maximum == infinite_statistics.maximum == 1750.0
    assert _column(nan_profile, "rpm").missing_count == 1
    assert _column(infinite_profile, "rpm").missing_count == 0


def test_missing_and_unexpected_columns_are_aggregated_without_names_leaking() -> None:
    missing = _profile(BannerScenario.MISSING_COLUMN)
    private_column = "synthetic_private_path_C_users_fixture_4729"
    dataframe = make_banner_dataframe().assign(**{private_column: 1.0})

    unexpected = profile_banner_dataframe(dataframe, key_columns=_DECLARED_KEY)
    unexpected_json = banner_profile_json_bytes(unexpected).decode("utf-8")

    assert len(missing.columns) == 26
    assert missing.volume.missing_expected_column_count == 1
    assert _column(missing, "rpm").present is False
    statistics = _column(missing, "rpm").numeric_statistics
    assert statistics is not None
    assert statistics.minimum is None
    assert unexpected.volume.unexpected_column_count == 1
    assert private_column not in unexpected_json


@pytest.mark.parametrize("scenario", tuple(BannerScenario))
def test_every_synthetic_scenario_produces_publishable_stable_json(
    scenario: BannerScenario,
) -> None:
    first = _profile(scenario)
    second = _profile(scenario)

    assert banner_profile_json_bytes(first) == banner_profile_json_bytes(second)
    assert len(first.columns) == 26


def test_identical_duplicates_and_declared_key_conflicts_are_separate() -> None:
    identical = _profile(BannerScenario.IDENTICAL_DUPLICATE).duplicates
    conflicting = _profile(BannerScenario.CONFLICTING_DUPLICATE).duplicates

    assert identical.key_columns == _DECLARED_KEY
    assert identical.key_columns_available is True
    assert identical.complete_duplicate_group_count == 1
    assert identical.complete_duplicate_excess_row_count == 1
    assert identical.duplicate_key_group_count == 1
    assert identical.duplicate_key_excess_row_count == 1
    assert identical.conflicting_key_group_count == 0
    assert identical.conflicting_row_count == 0
    assert conflicting.complete_duplicate_group_count == 0
    assert conflicting.complete_duplicate_excess_row_count == 0
    assert conflicting.duplicate_key_group_count == 1
    assert conflicting.duplicate_key_excess_row_count == 1
    assert conflicting.conflicting_key_group_count == 1
    assert conflicting.conflicting_row_count == 2


def test_incomplete_or_unavailable_declared_keys_never_emit_key_values() -> None:
    dataframe = make_banner_dataframe()
    dataframe.loc[0, "created_at"] = pd.NA

    incomplete = profile_banner_dataframe(dataframe, key_columns=_DECLARED_KEY)
    unavailable = _profile(BannerScenario.MISSING_COLUMN)

    assert incomplete.duplicates.rows_with_incomplete_key_count == 1
    assert unavailable.duplicates.key_columns_available is True
    assert unavailable.duplicates.rows_with_incomplete_key_count == 0
    missing_key = profile_banner_dataframe(
        dataframe.drop(columns="created_at"), key_columns=_DECLARED_KEY
    )
    assert missing_key.duplicates.key_columns_available is False
    assert missing_key.duplicates.duplicate_key_group_count == 0


def test_temporal_period_order_cadence_and_gaps_are_fixed() -> None:
    valid = _profile().temporal
    irregular = _profile(BannerScenario.IRREGULAR_CADENCE).temporal
    long_gap = _profile(BannerScenario.LONG_GAP).temporal

    assert valid.period_start_utc == "2099-04-01T00:00:00.000000Z"
    assert valid.period_end_utc == "2099-04-01T00:02:00.000000Z"
    assert valid.input_order is TemporalOrder.NONDECREASING
    assert valid.nominal_cadence_seconds == 60.0
    assert valid.irregular_interval_count == 0
    assert valid.gap_count == 0
    assert valid.total_gap_seconds == 0.0
    assert irregular.nominal_cadence_seconds == 60.0
    assert irregular.irregular_interval_count == 1
    assert irregular.gap_count == 1
    assert irregular.total_gap_seconds == 60.0
    assert irregular.maximum_interval_seconds == 120.0
    assert long_gap.nominal_cadence_seconds == 60.0
    assert long_gap.gap_count == 1
    assert long_gap.total_gap_seconds == 28740.0
    assert long_gap.maximum_interval_seconds == 28800.0


def test_temporal_input_order_is_reported_without_reordering_the_dataframe() -> None:
    descending = make_banner_dataframe().iloc[::-1].reset_index(drop=True)
    unordered = make_banner_dataframe().iloc[[0, 2, 1]].reset_index(drop=True)
    descending_before = descending.copy(deep=True)

    descending_profile = profile_banner_dataframe(descending, key_columns=_DECLARED_KEY)
    unordered_profile = profile_banner_dataframe(unordered, key_columns=_DECLARED_KEY)

    assert descending_profile.temporal.input_order is TemporalOrder.NONINCREASING
    assert unordered_profile.temporal.input_order is TemporalOrder.UNORDERED
    pd.testing.assert_frame_equal(descending, descending_before)


def test_redundant_unit_pairs_use_fixed_absolute_and_relative_tolerance() -> None:
    coherent = _profile(BannerScenario.COHERENT_UNIT_PAIRS)
    incoherent = _profile(BannerScenario.INCOHERENT_UNIT_PAIRS)
    coherent_pair = coherent.redundant_unit_pairs[0]
    incoherent_pair = incoherent.redundant_unit_pairs[0]

    assert coherent.definitions.unit_absolute_tolerance == 0.000001
    assert coherent.definitions.unit_relative_tolerance == 0.000001
    assert coherent_pair.left_column == "z_rms_velocity_in_s"
    assert coherent_pair.right_column == "z_rms_velocity_mm_s"
    assert coherent_pair.comparable_count == 3
    assert coherent_pair.inconsistent_count == 0
    assert coherent_pair.consistency_percentage == 100.0
    assert incoherent_pair.comparable_count == 3
    assert incoherent_pair.consistent_count == 2
    assert incoherent_pair.inconsistent_count == 1
    assert incoherent_pair.consistency_percentage == 66.666667
    assert incoherent_pair.maximum_absolute_error == 0.025


def test_allowed_categories_include_zero_count_entries_and_balance() -> None:
    absent = _profile().labels
    transition = _profile(BannerScenario.LABEL_TRANSITION).labels

    assert absent.allowed_category_check_applied is True
    assert absent.allowed_category_count == 2
    assert absent.absent_allowed_category_count == 1
    assert tuple((item.label, item.count) for item in absent.distribution) == (
        ("synthetic_nominal", 3),
        ("synthetic_warning", 0),
    )
    assert absent.majority_count == 3
    assert absent.minority_count == 0
    assert absent.majority_to_minority_ratio is None
    assert absent.normalized_entropy == 0.0
    assert tuple((item.label, item.count) for item in transition.distribution) == (
        ("synthetic_nominal", 2),
        ("synthetic_warning", 1),
    )
    assert transition.majority_to_minority_ratio == 2.0
    assert transition.normalized_entropy == 0.918296


def test_json_is_byte_stable_golden_and_locale_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _GOLDEN_PATH.read_bytes()
    first = banner_profile_json_bytes(_profile())

    def reject_locale_format(*_args: object, **_kwargs: object) -> NoReturn:
        pytest.fail("profile serialization must not use locale formatting")

    monkeypatch.setattr(locale, "format_string", reject_locale_format)
    monkeypatch.setattr(
        locale,
        "localeconv",
        lambda: {"decimal_point": ",", "thousands_sep": "."},
    )
    second = banner_profile_json_bytes(_profile())

    assert first == expected
    assert second == expected
    assert first.endswith(b"\n")
    assert b'"mean": 1750.0' in first


def test_json_key_column_and_category_order_are_explicit() -> None:
    payload = json.loads(banner_profile_json_bytes(_profile()))

    assert tuple(payload) == (
        "profile_schema_version",
        "contract_version",
        "definitions",
        "volume",
        "temporal",
        "columns",
        "duplicates",
        "labels",
        "redundant_unit_pairs",
    )
    assert tuple(column["name"] for column in payload["columns"]) == (
        BANNER_COLUMN_NAMES
    )
    assert tuple(item["label"] for item in payload["labels"]["distribution"]) == (
        "synthetic_nominal",
        "synthetic_warning",
    )


@pytest.mark.parametrize(
    "classification",
    (
        ProfileFieldClassification.ROW_LEVEL,
        ProfileFieldClassification.LOCAL_PATH,
        ProfileFieldClassification.SAMPLE,
        ProfileFieldClassification.INDIVIDUAL_IDENTIFIER,
        ProfileFieldClassification.INDIVIDUAL_TIMESTAMP,
        ProfileFieldClassification.REIDENTIFIABLE_COMBINATION,
    ),
)
def test_recursive_privacy_guard_rejects_every_disclosure_classification(
    classification: ProfileFieldClassification,
) -> None:
    unsafe_schema = PublicProfileField(
        "root",
        ProfileFieldClassification.AGGREGATE,
        (
            PublicProfileField(
                "nested",
                ProfileFieldClassification.AGGREGATE,
                (PublicProfileField("unsafe", classification),),
            ),
        ),
    )

    with pytest.raises(ProfilePrivacyError, match=classification.value):
        validate_public_profile_schema(unsafe_schema)


def test_public_schema_is_recursively_aggregate_safe() -> None:
    validate_public_profile_schema(PUBLIC_BANNER_PROFILE_SCHEMA)


def test_outputs_never_include_row_ids_middle_timestamp_or_local_paths() -> None:
    profile = _profile()
    json_output = banner_profile_json_bytes(profile).decode("utf-8")
    markdown_output = render_banner_profile_markdown(profile)
    outputs = (json_output, markdown_output)

    for output in outputs:
        assert "-24001" not in output
        assert "-24002" not in output
        assert "-24003" not in output
        assert "2099-04-01T00:01:00" not in output
        assert "C:\\Users" not in output
        assert "data/raw" not in output


def test_markdown_is_stable_aggregate_and_contains_all_contract_columns() -> None:
    first = render_banner_profile_markdown(_profile())
    second = render_banner_profile_markdown(_profile())

    assert first == second
    assert first.endswith("\n")
    assert "# Perfil agregado de qualidade do banner" in first
    assert "## Duplicatas e conflitos" in first
    assert "## Consistência de pares redundantes" in first
    assert all(name in first for name in BANNER_COLUMN_NAMES)


def test_profiler_uses_only_the_already_loaded_dataframe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataframe = make_banner_dataframe()

    def reject_open(*_args: object, **_kwargs: object) -> NoReturn:
        pytest.fail("the profiler must not open files")

    monkeypatch.setattr(builtins, "open", reject_open)

    assert (
        profile_banner_dataframe(dataframe, key_columns=_DECLARED_KEY).volume.row_count
        == 3
    )


@pytest.mark.parametrize(
    ("key_columns", "message"),
    (
        ((), "At least one"),
        (("synthetic_unknown_key",), "banner contract"),
        (("id", "id"), "must not contain duplicates"),
    ),
)
def test_declared_key_must_be_explicit_valid_and_unique(
    key_columns: tuple[str, ...], message: str
) -> None:
    with pytest.raises(BannerProfileConfigurationError, match=message):
        profile_banner_dataframe(make_banner_dataframe(), key_columns=key_columns)


def test_allowed_categories_reject_ambiguous_empty_labels() -> None:
    with pytest.raises(BannerProfileConfigurationError, match="non-empty strings"):
        profile_banner_dataframe(
            make_banner_dataframe(),
            key_columns=_DECLARED_KEY,
            allowed_fault_categories=frozenset({"synthetic_nominal", " "}),
        )


def test_duplicate_dataframe_column_names_are_rejected() -> None:
    dataframe = make_banner_dataframe()
    dataframe.columns = (*BANNER_COLUMN_NAMES[:-1], "fault")

    with pytest.raises(BannerProfileConfigurationError, match="unique"):
        profile_banner_dataframe(dataframe, key_columns=_DECLARED_KEY)
