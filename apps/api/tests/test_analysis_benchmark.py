"""Functional checks for the synthetic local analysis benchmark."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import tracemalloc
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from threading import Lock
from time import sleep
from typing import cast

import prescriptive_maintenance.analysis_benchmark as benchmark_module
import pytest
from fastapi import FastAPI
from prescriptive_maintenance.analysis_integration import IntegratedAnalysisService
from prescriptive_maintenance.settings import Settings

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_WORKING_TREE_SHA256 = "e" * 64


@pytest.fixture(scope="module")
def benchmark_report() -> benchmark_module.AnalysisBenchmarkReport:
    return benchmark_module.run_local_analysis_benchmark(
        benchmark_module.AnalysisBenchmarkConfig(
            warmup_iterations=1,
            measured_iterations=3,
            seed=65,
            top_k=3,
        ),
        repository_root=_REPOSITORY_ROOT,
    )


def test_real_http_journey_separates_provider_failure_from_valid_latency(
    benchmark_report: benchmark_module.AnalysisBenchmarkReport,
) -> None:
    payload = _mapping(benchmark_report.payload)
    metrics = _mapping(payload["metrics"])
    assert metrics["primary_view"] == "scenarios"
    scenarios = _mapping(metrics["scenarios"])
    documented = _mapping(scenarios["documented_fault"])
    documented_outcomes = _mapping(documented["observed_outcomes"])
    assert documented_outcomes == {"documented_fault": 3}
    documented_layers = _mapping(documented["layers"])
    for layer in ("http_total", "model", "retrieval"):
        documented_summary = _mapping(documented_layers[layer])
        assert documented_summary["valid_sample_count"] == 3
        assert documented_summary["error_count"] == 0
    documented_generation = _mapping(documented_layers["generation"])
    assert documented_generation["valid_sample_count"] == 3
    assert documented_generation["error_count"] == 0

    failed = _mapping(scenarios["provider_failure"])
    failed_outcomes = _mapping(failed["observed_outcomes"])
    assert failed_outcomes == {"degraded": 3}
    failed_layers = _mapping(failed["layers"])
    for layer in ("http_total", "model", "retrieval"):
        failed_summary = _mapping(failed_layers[layer])
        assert failed_summary["valid_sample_count"] == 3
        assert failed_summary["error_count"] == 0
    failed_generation = _mapping(failed_layers["generation"])
    assert failed_generation["attempt_count"] == 3
    assert failed_generation["valid_sample_count"] == 0
    assert failed_generation["error_count"] == 3
    assert failed_generation["p50"] is None
    assert failed_generation["p95"] is None

    scenario_mix = _mapping(metrics["synthetic_scenario_mix"])
    assert scenario_mix["population"] == "documented_fault_and_provider_failure"
    assert scenario_mix["percentile_population"] == "successful_attempts_only"
    assert scenario_mix["error_rate_population"] == (
        "deliberate_provider_success_and_provider_failure_mix"
    )
    mixed_layers = _mapping(scenario_mix["layers"])
    mixed_generation = _mapping(mixed_layers["generation"])
    assert mixed_generation["attempt_count"] == 6
    assert mixed_generation["valid_sample_count"] == 3
    assert mixed_generation["error_count"] == 3
    assert mixed_generation["error_rate"] == 0.5
    assert mixed_generation["p50"] is not None
    assert mixed_generation["p95"] is not None

    provider_failures = _mapping(metrics["provider_failures"])
    assert provider_failures == {
        "count": 3,
        "excluded_from_valid_generation_latency": True,
    }


def test_warmup_memory_and_ai_usage_are_labeled_without_overclaiming(
    benchmark_report: benchmark_module.AnalysisBenchmarkReport,
) -> None:
    payload = _mapping(benchmark_report.payload)
    warmup = _mapping(payload["warmup"])
    assert warmup == {
        "request_count": 2,
        "included_in_distributions": False,
    }
    configuration = _mapping(payload["configuration"])
    assert configuration["measured_iterations_per_scenario"] == 3
    assert configuration["memory_iterations_per_scenario"] == 3

    metrics = _mapping(payload["metrics"])
    protocol = _mapping(metrics["measurement_protocol"])
    assert protocol == {
        "timed_pass": "warmup_then_measured_without_tracemalloc",
        "memory_pass": "separate_fresh_service_and_application",
        "memory_schedule_matches_measured_schedule": True,
        "http_total_includes": [
            "application_response_serialization",
            "operational_request_log_serialization_and_handler_io",
        ],
    }
    memory = _mapping(metrics["memory"])
    assert memory["value_kind"] == "measured"
    assert memory["method"] == (
        "separate_pass_maximum_of_per_request_tracemalloc_peaks"
    )
    assert memory["scope"] == "individual_memory_pass_http_requests"
    assert memory["included_application_work"] == [
        "application_response_serialization",
        "operational_request_log_serialization_and_handler_io",
    ]
    assert memory["excluded_harness_work"] == [
        "request_preparation",
        "response_validation",
        "benchmark_layer_event_serialization_and_sink_io",
    ]
    assert memory["unit"] == "bytes"
    peak_traced_bytes = memory["peak_traced_bytes"]
    assert isinstance(peak_traced_bytes, int)
    assert peak_traced_bytes > 0

    ai_usage = _mapping(payload["ai_usage"])
    tokens = _mapping(ai_usage["tokens"])
    assert tokens == {
        "value_kind": "simulated",
        "source": "synthetic_provider_reported_counters",
        "successful_calls": 3,
        "input_tokens": 72,
        "output_tokens": 36,
        "total_tokens": 108,
    }
    failed_usage = _mapping(ai_usage["failed_provider_attempts"])
    assert failed_usage["value_kind"] == "not_available"
    assert failed_usage["attempts"] == 3
    cost = _mapping(ai_usage["cost"])
    assert cost["value_kind"] == "not_available"
    assert cost["value"] is None
    assert cost["currency"] is None

    limits = _mapping(payload["limits"])
    assert limits["original_materials_accessed"] is False
    assert limits["network_calls"] is False
    assert limits["paid_provider_calls"] is False
    assert "RSS" in cast(str, limits["memory_scope"])
    assert "native" in cast(str, limits["memory_scope"])


def test_report_discovers_real_provenance_and_requires_complete_bindings(
    benchmark_report: benchmark_module.AnalysisBenchmarkReport,
) -> None:
    payload = _mapping(benchmark_report.payload)
    repository = _mapping(payload["repository"])
    discovered = benchmark_module.discover_repository_state(_REPOSITORY_ROOT)
    assert repository["commit"] == discovered.commit
    assert repository["working_tree_dirty"] is discovered.dirty
    assert repository["working_tree_sha256"] == discovered.working_tree_sha256
    assert len(discovered.working_tree_sha256) == 64
    assert (
        repository["uv_lock_sha256"]
        == sha256((_REPOSITORY_ROOT / "uv.lock").read_bytes()).hexdigest()
    )

    bindings = _mapping(payload["bindings"])
    assert set(bindings) == {
        "api_contract_version",
        "dataset_id",
        "dataset_version",
        "feature_contract_version",
        "feature_schema_id",
        "model_id",
        "model_version",
        "index_id",
        "index_version",
        "similarity_configuration_version",
        "similarity_dimension",
        "similarity_metric",
        "similarity_preprocessor_version",
        "generation_contract_version",
        "prompt_id",
        "prompt_version",
        "provider_id",
        "provider_timeout_seconds",
        "retrieval_policy_schema_version",
        "retrieval_policy_version",
        "retrieval_policy_sha256",
        "mapping_version",
        "mapping_sha256",
        "projection_policy_schema_version",
        "projection_policy_version",
        "projection_policy_sha256",
        "authorization_schema_version",
        "authorization_version",
        "authorization_sha256",
    }
    assert bindings["api_contract_version"] == "1.0.0"
    assert bindings["dataset_version"] == "benchmark-synthetic-dataset.v1"
    assert bindings["model_version"] == "benchmark-synthetic-model.v1"
    assert bindings["prompt_id"] == f"prompt_{bindings['prompt_version']}"
    assert bindings["provider_timeout_seconds"] == 2.0
    assert bindings["authorization_schema_version"] == 1

    runtime = _mapping(payload["runtime"])
    dependencies = _mapping(runtime["dependencies"])
    assert set(dependencies) == {
        "fastapi",
        "httpx2",
        "prescriptive-maintenance-api",
        "pydantic",
        "starlette",
    }
    assert all(
        type(package_version) is str and package_version
        for package_version in dependencies.values()
    )


def test_json_and_markdown_reports_are_stable_and_sanitized(
    benchmark_report: benchmark_module.AnalysisBenchmarkReport,
) -> None:
    first = benchmark_report.to_json()
    second = benchmark_report.to_json()
    assert first == second
    assert first.endswith("\n")
    assert json.loads(first)["benchmark_id"] == "analysis-local-synthetic.v1"
    assert first.lstrip().startswith('{\n  "ai_usage"')

    forbidden = (
        "temperature_c",
        "system_prompt",
        "input_json",
        "Synthetic benchmark evidence",
        "Synthetic provider failure",
        str(_REPOSITORY_ROOT),
    )
    assert not any(value in first for value in forbidden)

    markdown = benchmark_report.to_markdown()
    assert markdown.startswith("# Benchmark local da análise prescritiva\n")
    assert markdown.index("## Métricas por cenário") < markdown.index(
        "## `synthetic_scenario_mix`"
    )
    assert "`simulated`" in markdown
    assert "`not_available`" in markdown
    assert "não representa RSS nem memória nativa" in markdown
    assert "falhas de provider são contadas" in markdown
    assert "Os percentis usam somente tentativas bem-sucedidas" in markdown
    assert not any(value in markdown for value in forbidden)


def test_report_keeps_private_validated_payload_against_nested_mutation(
    benchmark_report: benchmark_module.AnalysisBenchmarkReport,
) -> None:
    original_json = benchmark_report.to_json()
    original_markdown = benchmark_report.to_markdown()
    exposed = benchmark_report.payload
    repository = exposed["repository"]
    assert type(repository) is dict
    cast(dict[str, object], repository)["commit"] = "f" * 40
    metrics = exposed["metrics"]
    assert type(metrics) is dict
    memory = cast(dict[str, object], metrics)["memory"]
    assert type(memory) is dict
    excluded = cast(dict[str, object], memory)["excluded_harness_work"]
    assert type(excluded) is list
    cast(list[object], excluded).append("synthetic_mutation")

    assert benchmark_report.to_json() == original_json
    assert benchmark_report.to_markdown() == original_markdown
    fresh_repository = _mapping(benchmark_report.payload["repository"])
    assert fresh_repository["commit"] != "f" * 40
    fresh_memory = _mapping(_mapping(benchmark_report.payload["metrics"])["memory"])
    assert "synthetic_mutation" not in cast(
        list[object], fresh_memory["excluded_harness_work"]
    )

    with pytest.raises(
        benchmark_module.AnalysisBenchmarkError,
        match="report structure is invalid",
    ):
        benchmark_module.AnalysisBenchmarkReport(payload={"value": float("nan")})


def test_layer_events_are_json_correlated_and_content_free(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(
        logging.INFO,
        logger="prescriptive_maintenance.benchmark",
    ):
        benchmark_module.run_local_analysis_benchmark(
            benchmark_module.AnalysisBenchmarkConfig(
                warmup_iterations=0,
                measured_iterations=1,
                seed=65,
                top_k=1,
            ),
            repository_root=_REPOSITORY_ROOT,
        )

    records = tuple(
        json.loads(record.message)
        for record in caplog.records
        if record.name == "prescriptive_maintenance.benchmark"
    )
    assert len(records) == 8
    assert all(
        set(record)
        == {
            "benchmark_id",
            "correlation_id",
            "duration_ms",
            "event",
            "layer",
            "phase",
            "scenario",
            "status",
        }
        for record in records
    )
    assert all(record["phase"] == "measured" for record in records)
    assert {
        (record["scenario"], record["layer"], record["status"]) for record in records
    } == {
        ("documented_fault", "http_total", "success"),
        ("documented_fault", "model", "success"),
        ("documented_fault", "retrieval", "success"),
        ("documented_fault", "generation", "success"),
        ("provider_failure", "http_total", "success"),
        ("provider_failure", "model", "success"),
        ("provider_failure", "retrieval", "success"),
        ("provider_failure", "generation", "error"),
    }
    assert all(
        str(record["correlation_id"]).startswith("sen65_measured_")
        for record in records
    )
    failed_generation = tuple(
        record
        for record in records
        if record["layer"] == "generation" and record["scenario"] == "provider_failure"
    )
    assert len(failed_generation) == 1
    assert failed_generation[0]["status"] == "error"

    serialized = json.dumps(records)
    assert not any(
        value in serialized
        for value in (
            "features",
            "temperature_c",
            "system_prompt",
            "input_json",
            "content",
            "token=",
        )
    )


def test_scenario_schedule_is_versioned_balanced_and_deterministic(
    caplog: pytest.LogCaptureFixture,
) -> None:
    expected = (
        "provider_failure",
        "documented_fault",
        "provider_failure",
        "documented_fault",
        "provider_failure",
        "documented_fault",
        "documented_fault",
        "provider_failure",
        "provider_failure",
        "documented_fault",
    )
    with caplog.at_level(
        logging.INFO,
        logger="prescriptive_maintenance.benchmark",
    ):
        report = benchmark_module.run_local_analysis_benchmark(
            benchmark_module.AnalysisBenchmarkConfig(
                warmup_iterations=0,
                measured_iterations=5,
                seed=65,
                top_k=1,
            ),
            repository_root=_REPOSITORY_ROOT,
        )
    records = tuple(
        json.loads(record.message)
        for record in caplog.records
        if record.name == "prescriptive_maintenance.benchmark"
    )
    observed = tuple(
        record["scenario"] for record in records if record["layer"] == "http_total"
    )
    assert observed == expected
    assert all(
        set(observed[index : index + 2]) == {"documented_fault", "provider_failure"}
        for index in range(0, len(observed), 2)
    )

    payload = _mapping(report.payload)
    configuration = _mapping(payload["configuration"])
    assert configuration["scenario_schedule_version"] == ("balanced-pairs-sha256.v1")
    assert (
        configuration["measured_scenario_order_sha256"]
        == sha256("\n".join(expected).encode("ascii")).hexdigest()
    )


def test_timed_and_usage_pass_is_untraced_and_memory_uses_fresh_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace_states: list[bool] = []
    created_services: list[IntegratedAnalysisService] = []
    created_applications: list[FastAPI] = []
    original_create_app = benchmark_module.create_app

    def observed_elapsed(started_at_ns: int) -> float:
        trace_states.append(tracemalloc.is_tracing())
        return (benchmark_module.perf_counter_ns() - started_at_ns) / 1_000_000.0

    def observed_create_app(
        *,
        analysis_service: IntegratedAnalysisService,
        settings: Settings,
    ) -> FastAPI:
        application = original_create_app(
            analysis_service=analysis_service,
            settings=settings,
        )
        created_services.append(analysis_service)
        created_applications.append(application)
        return application

    monkeypatch.setattr(benchmark_module, "_elapsed_milliseconds", observed_elapsed)
    monkeypatch.setattr(benchmark_module, "create_app", observed_create_app)
    report = benchmark_module.run_local_analysis_benchmark(
        benchmark_module.AnalysisBenchmarkConfig(
            warmup_iterations=1,
            measured_iterations=1,
            seed=65,
            top_k=1,
        ),
        repository_root=_REPOSITORY_ROOT,
    )

    assert trace_states == [False] * 16
    assert len(created_services) == 2
    assert created_services[0] is not created_services[1]
    assert len(created_applications) == 2
    assert created_applications[0] is not created_applications[1]
    assert not tracemalloc.is_tracing()
    usage = _mapping(report.payload["ai_usage"])
    tokens = _mapping(usage["tokens"])
    assert tokens["successful_calls"] == 1
    assert tokens["total_tokens"] == 36


def test_slow_event_sink_runs_after_timers_and_memory_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = benchmark_module.AnalysisBenchmarkConfig(
        warmup_iterations=0,
        measured_iterations=1,
        seed=65,
        top_k=1,
    )
    with monkeypatch.context() as scoped:
        scoped.setattr(benchmark_module, "perf_counter_ns", _StepClock())
        scoped.setattr(benchmark_module, "_log_layer_event", _discard_event)
        baseline = benchmark_module.run_local_analysis_benchmark(
            config,
            repository_root=_REPOSITORY_ROOT,
        )

    sink_trace_states: list[bool] = []

    def slow_sink(event: object) -> None:
        del event
        sink_trace_states.append(tracemalloc.is_tracing())
        sleep(0.005)

    with monkeypatch.context() as scoped:
        scoped.setattr(benchmark_module, "perf_counter_ns", _StepClock())
        scoped.setattr(benchmark_module, "_log_layer_event", slow_sink)
        with_slow_sink = benchmark_module.run_local_analysis_benchmark(
            config,
            repository_root=_REPOSITORY_ROOT,
        )

    baseline_metrics = _mapping(baseline.payload["metrics"])
    slow_metrics = _mapping(with_slow_sink.payload["metrics"])
    assert slow_metrics["scenarios"] == baseline_metrics["scenarios"]
    assert (
        slow_metrics["synthetic_scenario_mix"]
        == (baseline_metrics["synthetic_scenario_mix"])
    )
    assert sink_trace_states == [False] * 8


def test_real_request_log_handler_is_inside_http_and_memory_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _AdvancingClock()
    trace_states: list[bool] = []

    class ObservedRequestHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            payload = json.loads(record.getMessage())
            if payload.get("event") != "http_request_completed":
                return
            trace_states.append(tracemalloc.is_tracing())
            clock.advance(25_000_000)

    request_logger = logging.getLogger("prescriptive_maintenance.requests")
    handler = ObservedRequestHandler()
    request_logger.addHandler(handler)
    try:
        monkeypatch.setattr(benchmark_module, "perf_counter_ns", clock)
        report = benchmark_module.run_local_analysis_benchmark(
            benchmark_module.AnalysisBenchmarkConfig(
                warmup_iterations=0,
                measured_iterations=1,
                seed=65,
                top_k=1,
            ),
            repository_root=_REPOSITORY_ROOT,
        )
    finally:
        request_logger.removeHandler(handler)

    assert trace_states == [False, False, True, True]
    metrics = _mapping(report.payload["metrics"])
    mixed_layers = _mapping(_mapping(metrics["synthetic_scenario_mix"])["layers"])
    assert _mapping(mixed_layers["http_total"])["p50"] == 25.0
    assert _mapping(mixed_layers["http_total"])["p95"] == 25.0
    assert _mapping(mixed_layers["model"])["p95"] == 0.0
    assert _mapping(mixed_layers["retrieval"])["p95"] == 0.0
    assert _mapping(mixed_layers["generation"])["p95"] == 0.0
    memory = _mapping(metrics["memory"])
    assert memory["included_application_work"] == [
        "application_response_serialization",
        "operational_request_log_serialization_and_handler_io",
    ]


def test_active_tracemalloc_fails_typed_without_destroying_caller_state() -> None:
    tracemalloc.start()
    try:
        with pytest.raises(
            benchmark_module.AnalysisBenchmarkError,
            match="must be inactive",
        ):
            benchmark_module.run_local_analysis_benchmark(
                benchmark_module.AnalysisBenchmarkConfig(
                    warmup_iterations=0,
                    measured_iterations=1,
                    seed=65,
                    top_k=1,
                ),
                repository_root=_REPOSITORY_ROOT,
            )
        assert tracemalloc.is_tracing()
    finally:
        tracemalloc.stop()


def test_working_tree_digest_distinguishes_dirty_to_dirty_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before_path = tmp_path / "synthetic_before.py"
    after_path = tmp_path / "synthetic_after.py"
    before_path.write_bytes(b"before\n")

    def fake_git(repository_root: Path, arguments: tuple[str, ...]) -> bytes:
        assert repository_root == tmp_path.resolve()
        if arguments == ("rev-parse", "HEAD"):
            return ("a" * 40 + "\n").encode("ascii")
        if arguments[0] == "status":
            active_path = before_path if before_path.exists() else after_path
            return b"?? " + active_path.name.encode("ascii") + b"\0"
        if arguments == ("ls-files", "--stage", "-v", "-z"):
            return b""
        raise AssertionError("Unexpected synthetic Git command.")

    monkeypatch.setattr(benchmark_module, "_run_git_bytes", fake_git)
    initial = benchmark_module.discover_repository_state(tmp_path)
    before_path.rename(after_path)
    after_path.write_bytes(b"after\n")
    final = benchmark_module.discover_repository_state(tmp_path)

    assert initial.commit == final.commit == "a" * 40
    assert initial.dirty is final.dirty is True
    assert initial.working_tree_sha256 != final.working_tree_sha256
    assert "synthetic_before.py" not in repr(initial)
    assert "synthetic_after.py" not in repr(final)


@pytest.mark.parametrize(
    "index_flag",
    ("--assume-unchanged", "--skip-worktree"),
)
def test_index_flags_cannot_hide_modified_tracked_bytes(
    tmp_path: Path,
    index_flag: str,
) -> None:
    repository = tmp_path / "private-synthetic-repository"
    repository.mkdir()
    tracked_path = repository / "tracked-synthetic.txt"
    tracked_path.write_bytes(b"original\n")
    _run_synthetic_git(repository, ("init", "--quiet"))
    _run_synthetic_git(repository, ("add", "--", tracked_path.name))
    _run_synthetic_git(
        repository,
        (
            "-c",
            "user.name=Synthetic Test",
            "-c",
            "user.email=synthetic@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "synthetic baseline",
        ),
    )
    _run_synthetic_git(
        repository,
        ("update-index", index_flag, "--", tracked_path.name),
    )
    tracked_path.write_bytes(b"modified\n")

    status = _run_synthetic_git(
        repository,
        (
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--ignore-submodules=none",
            "--no-renames",
        ),
    )
    assert status == b""
    with pytest.raises(benchmark_module.AnalysisBenchmarkError) as error:
        benchmark_module.discover_repository_state(repository)

    assert str(error.value) == (
        "Git index contains flags that may hide working tree changes."
    )
    assert str(repository) not in str(error.value)
    assert tracked_path.name not in str(error.value)


@pytest.mark.parametrize(
    ("states", "lock_hashes"),
    (
        (
            (
                benchmark_module.RepositoryState(
                    commit="a" * 40,
                    dirty=False,
                    working_tree_sha256=_WORKING_TREE_SHA256,
                ),
                benchmark_module.RepositoryState(
                    commit="c" * 40,
                    dirty=False,
                    working_tree_sha256=_WORKING_TREE_SHA256,
                ),
            ),
            ("b" * 64, "b" * 64),
        ),
        (
            (
                benchmark_module.RepositoryState(
                    commit="a" * 40,
                    dirty=False,
                    working_tree_sha256=_WORKING_TREE_SHA256,
                ),
                benchmark_module.RepositoryState(
                    commit="a" * 40,
                    dirty=True,
                    working_tree_sha256=_WORKING_TREE_SHA256,
                ),
            ),
            ("b" * 64, "b" * 64),
        ),
        (
            (
                benchmark_module.RepositoryState(
                    commit="a" * 40,
                    dirty=False,
                    working_tree_sha256=_WORKING_TREE_SHA256,
                ),
                benchmark_module.RepositoryState(
                    commit="a" * 40,
                    dirty=False,
                    working_tree_sha256=_WORKING_TREE_SHA256,
                ),
            ),
            ("b" * 64, "d" * 64),
        ),
        (
            (
                benchmark_module.RepositoryState(
                    commit="a" * 40,
                    dirty=True,
                    working_tree_sha256="e" * 64,
                ),
                benchmark_module.RepositoryState(
                    commit="a" * 40,
                    dirty=True,
                    working_tree_sha256="f" * 64,
                ),
            ),
            ("b" * 64, "b" * 64),
        ),
    ),
)
def test_repository_snapshot_revalidation_covers_commit_dirty_content_and_lock(
    states: tuple[benchmark_module.RepositoryState, benchmark_module.RepositoryState],
    lock_hashes: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_values = iter(states)
    lock_values = iter(lock_hashes)

    def discover_state(repository_root: Path) -> benchmark_module.RepositoryState:
        del repository_root
        return next(state_values)

    def read_lock(repository_root: Path) -> str:
        del repository_root
        return next(lock_values)

    monkeypatch.setattr(benchmark_module, "discover_repository_state", discover_state)
    monkeypatch.setattr(benchmark_module, "_read_uv_lock_sha256", read_lock)
    monkeypatch.setattr(benchmark_module, "_log_layer_event", _discard_event)
    with pytest.raises(
        benchmark_module.AnalysisBenchmarkError,
        match="provenance changed",
    ):
        benchmark_module.run_local_analysis_benchmark(
            benchmark_module.AnalysisBenchmarkConfig(
                warmup_iterations=0,
                measured_iterations=1,
                seed=65,
                top_k=1,
            ),
            repository_root=_REPOSITORY_ROOT,
        )


def test_filesystem_git_and_lock_failures_do_not_expose_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "private" / "missing-repository"
    with pytest.raises(benchmark_module.AnalysisBenchmarkError) as filesystem_error:
        benchmark_module.discover_repository_state(missing)
    assert str(filesystem_error.value) == "Repository root is unavailable."

    with pytest.raises(benchmark_module.AnalysisBenchmarkError) as git_error:
        benchmark_module.discover_repository_state(tmp_path)
    assert str(git_error.value) == "Git state could not be read safely."

    def fixed_state(repository_root: Path) -> benchmark_module.RepositoryState:
        del repository_root
        return benchmark_module.RepositoryState(
            commit="a" * 40,
            dirty=False,
            working_tree_sha256=_WORKING_TREE_SHA256,
        )

    monkeypatch.setattr(benchmark_module, "discover_repository_state", fixed_state)
    with pytest.raises(benchmark_module.AnalysisBenchmarkError) as lock_error:
        benchmark_module.run_local_analysis_benchmark(
            benchmark_module.AnalysisBenchmarkConfig(
                warmup_iterations=0,
                measured_iterations=1,
                seed=65,
                top_k=1,
            ),
            repository_root=tmp_path,
        )
    assert str(lock_error.value) == "Frozen dependency lock is unavailable."

    serialized_errors = " ".join(
        str(error.value) for error in (filesystem_error, git_error, lock_error)
    )
    assert str(tmp_path) not in serialized_errors


def test_cli_keeps_report_on_stdout_and_allowlisted_events_on_stderr() -> None:
    completed = subprocess.run(
        (
            sys.executable,
            "-m",
            "scripts.analysis_benchmark",
            "--warmup",
            "0",
            "--iterations",
            "1",
            "--top-k",
            "1",
        ),
        cwd=_REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=60.0,
    )
    assert completed.returncode == 0, completed.stderr
    payload = _mapping(json.loads(completed.stdout))
    assert payload["benchmark_id"] == "analysis-local-synthetic.v1"
    assert "analysis_benchmark_layer_completed" not in completed.stdout

    records = tuple(json.loads(line) for line in completed.stderr.splitlines())
    request_records = tuple(
        record for record in records if record.get("event") == "http_request_completed"
    )
    benchmark_records = tuple(
        record
        for record in records
        if record.get("event") == "analysis_benchmark_layer_completed"
    )
    assert len(request_records) == 4
    assert all(
        set(record) == {"correlation_id", "event", "method", "route", "status_code"}
        and record["method"] == "POST"
        and record["route"] == "/analysis"
        and record["status_code"] == 200
        for record in request_records
    )
    assert (
        sum(
            str(record["correlation_id"]).startswith("sen65_measured_")
            for record in request_records
        )
        == 2
    )
    assert (
        sum(
            str(record["correlation_id"]).startswith("sen65_memory_")
            for record in request_records
        )
        == 2
    )
    assert len(benchmark_records) == 8
    assert all(
        set(record)
        == {
            "benchmark_id",
            "correlation_id",
            "duration_ms",
            "event",
            "layer",
            "phase",
            "scenario",
            "status",
        }
        and record["event"] == "analysis_benchmark_layer_completed"
        for record in benchmark_records
    )
    assert len(records) == len(request_records) + len(benchmark_records)
    assert str(_REPOSITORY_ROOT) not in completed.stdout + completed.stderr


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("warmup_iterations", -1),
        ("warmup_iterations", True),
        ("measured_iterations", 0),
        ("seed", -1),
        ("top_k", 0),
        ("top_k", 11),
    ),
)
def test_invalid_configuration_fails_closed(field: str, value: object) -> None:
    values: dict[str, object] = {
        "warmup_iterations": 1,
        "measured_iterations": 1,
        "seed": 65,
        "top_k": 1,
    }
    values[field] = value
    with pytest.raises(ValueError, match="configuration is invalid"):
        benchmark_module.AnalysisBenchmarkConfig(
            **values  # pyright: ignore[reportArgumentType]
        )


class _StepClock:
    def __init__(self) -> None:
        self._value = 0
        self._lock = Lock()

    def __call__(self) -> int:
        with self._lock:
            value = self._value
            self._value += 1_000_000
            return value


class _AdvancingClock:
    def __init__(self) -> None:
        self._value = 0
        self._lock = Lock()

    def __call__(self) -> int:
        with self._lock:
            return self._value

    def advance(self, nanoseconds: int) -> None:
        with self._lock:
            self._value += nanoseconds


def _discard_event(event: object) -> None:
    del event


def _run_synthetic_git(repository: Path, arguments: tuple[str, ...]) -> bytes:
    executable = shutil.which("git")
    assert executable is not None
    completed = subprocess.run(  # noqa: S603
        (str(Path(executable).resolve()), *arguments),
        cwd=repository,
        check=False,
        capture_output=True,
        timeout=10.0,
    )
    assert completed.returncode == 0
    return completed.stdout


def _mapping(value: object) -> Mapping[str, object]:
    assert isinstance(value, Mapping)
    return cast(Mapping[str, object], value)
