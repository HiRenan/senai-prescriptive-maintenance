"""Lazy, local RapidOCR adapter for source-document extraction."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from importlib.metadata import version as package_version
from typing import Protocol, SupportsFloat, cast

from prescriptive_maintenance.data.source_documents import OcrPageResult


class RapidOcrAdapterError(Exception):
    """Sanitized failure raised by the local RapidOCR boundary."""


class RapidOcrEngine(Protocol):
    """Minimum callable surface used from RapidOCR."""

    def __call__(self, image: object) -> object: ...


RapidOcrEngineFactory = Callable[[], RapidOcrEngine]


class _RapidOcrOutput(Protocol):
    txts: object
    scores: object


class RapidOcrAdapter:
    """Translate RapidOCR output without initializing its engine eagerly."""

    def __init__(
        self,
        *,
        engine_factory: RapidOcrEngineFactory | None = None,
        engine: RapidOcrEngine | None = None,
    ) -> None:
        if engine_factory is not None and engine is not None:
            raise ValueError("Provide either an OCR engine or an engine factory.")
        self._engine_factory = engine_factory or _create_default_engine
        self._engine = engine

    @property
    def name(self) -> str:
        return "rapidocr-onnxruntime"

    @property
    def version(self) -> str:
        return package_version("rapidocr")

    def extract(self, image: object) -> OcrPageResult:
        try:
            output = self._get_engine()(image)
            return _translate_output(output)
        except RapidOcrAdapterError:
            raise
        except Exception:
            raise RapidOcrAdapterError("Local OCR extraction failed.") from None

    def _get_engine(self) -> RapidOcrEngine:
        if self._engine is None:
            try:
                self._engine = self._engine_factory()
            except Exception:
                raise RapidOcrAdapterError("Local OCR initialization failed.") from None
        return self._engine


def _create_default_engine() -> RapidOcrEngine:
    from rapidocr import RapidOCR

    return cast(
        RapidOcrEngine,
        RapidOCR(params={"Global.log_level": "error"}),
    )


def _translate_output(output: object) -> OcrPageResult:
    typed_output = cast(_RapidOcrOutput, output)
    try:
        texts = _items(typed_output.txts)
        scores = _items(typed_output.scores)
    except Exception:
        raise RapidOcrAdapterError("Local OCR returned an invalid result.") from None

    if len(texts) != len(scores) or any(not isinstance(text, str) for text in texts):
        raise RapidOcrAdapterError("Local OCR returned an invalid result.")

    confidence_values: list[float] = []
    for score in scores:
        if isinstance(score, bool):
            raise RapidOcrAdapterError("Local OCR returned an invalid result.")
        try:
            confidence_values.append(float(cast(SupportsFloat, score)))
        except (TypeError, ValueError, OverflowError):
            raise RapidOcrAdapterError(
                "Local OCR returned an invalid result."
            ) from None

    return OcrPageResult(
        text="\n".join(cast(tuple[str, ...], texts)),
        confidences=tuple(confidence_values),
    )


def _items(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise TypeError
    return tuple(cast(Iterable[object], value))
