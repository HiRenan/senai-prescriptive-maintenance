"""Synthetic tests for the local RapidOCR adapter boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import pytest
import rapidocr
from prescriptive_maintenance.data import (
    RapidOcrAdapter,
    RapidOcrAdapterError,
    RapidOcrEngine,
)


@dataclass(slots=True)
class _SyntheticRapidOcrOutput:
    txts: object
    scores: object


@dataclass(slots=True)
class _SyntheticRapidOcrEngine:
    output: object
    calls: int = 0

    def __call__(self, image: object) -> object:
        assert image is _SYNTHETIC_IMAGE
        self.calls += 1
        return self.output


_SYNTHETIC_IMAGE = object()


def test_initializes_factory_lazily_and_translates_texts_and_scores() -> None:
    output = _SyntheticRapidOcrOutput(
        txts=("Synthetic heading", "Synthetic locator"),
        scores=(0.98, 0.87),
    )
    engine = _SyntheticRapidOcrEngine(output)
    factory_calls = 0

    def create_engine() -> RapidOcrEngine:
        nonlocal factory_calls
        factory_calls += 1
        return engine

    adapter = RapidOcrAdapter(engine_factory=create_engine)

    assert factory_calls == 0
    first = adapter.extract(_SYNTHETIC_IMAGE)
    second = adapter.extract(_SYNTHETIC_IMAGE)

    assert factory_calls == 1
    assert engine.calls == 2
    assert first.text == "Synthetic heading\nSynthetic locator"
    assert first.confidences == (0.98, 0.87)
    assert second == first


def test_accepts_an_injected_engine() -> None:
    engine = _SyntheticRapidOcrEngine(
        _SyntheticRapidOcrOutput(txts=("Synthetic text",), scores=(0.91,))
    )

    result = RapidOcrAdapter(engine=engine).extract(_SYNTHETIC_IMAGE)

    assert result.text == "Synthetic text"
    assert result.confidences == (0.91,)


def test_default_engine_uses_error_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_params: object = None
    engine = _SyntheticRapidOcrEngine(
        _SyntheticRapidOcrOutput(txts=("Synthetic text",), scores=(0.93,))
    )

    def create_engine(*, params: object) -> RapidOcrEngine:
        nonlocal captured_params
        captured_params = params
        return engine

    monkeypatch.setattr(rapidocr, "RapidOCR", create_engine)

    result = RapidOcrAdapter().extract(_SYNTHETIC_IMAGE)

    assert result.text == "Synthetic text"
    assert captured_params == {"Global.log_level": "error"}


def test_sanitizes_engine_failures() -> None:
    class _FailingEngine:
        def __call__(self, image: object) -> object:
            raise RuntimeError("synthetic private OCR payload")

    adapter = RapidOcrAdapter(engine=cast(RapidOcrEngine, _FailingEngine()))

    with pytest.raises(RapidOcrAdapterError) as raised:
        adapter.extract(_SYNTHETIC_IMAGE)

    assert str(raised.value) == "Local OCR extraction failed."
    assert raised.value.__cause__ is None


@pytest.mark.parametrize(
    ("texts", "scores"),
    [
        (("Synthetic text",), (0.9, 0.8)),
        ((42,), (0.9,)),
        (("Synthetic text",), (True,)),
    ],
)
def test_rejects_malformed_engine_results(texts: object, scores: object) -> None:
    engine = _SyntheticRapidOcrEngine(
        _SyntheticRapidOcrOutput(txts=texts, scores=scores)
    )

    with pytest.raises(RapidOcrAdapterError) as raised:
        RapidOcrAdapter(engine=engine).extract(_SYNTHETIC_IMAGE)

    assert str(raised.value) == "Local OCR returned an invalid result."
