"""Explicit, lazy adapter for the Amazon Bedrock Converse interface."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, cast

from prescriptive_maintenance.generation.contracts import ProviderUsage
from prescriptive_maintenance.generation.provider import (
    ProviderConfigurationError,
    ProviderDisabledError,
    ProviderExecutionError,
    ProviderInvalidResponseError,
    ProviderRequest,
    ProviderResponse,
)


class BedrockRuntimeClient(Protocol):
    """Minimal structural type for an injected Bedrock runtime client."""

    def converse(self, **kwargs: object) -> object:
        """Invoke the configured model through Bedrock Converse."""

        ...


type BedrockClientFactory = Callable[[str], BedrockRuntimeClient]


@dataclass(frozen=True, slots=True)
class BedrockProviderConfig:
    """Explicit Bedrock adapter configuration, disabled by default."""

    enabled: bool = False
    model_id: str | None = None
    region: str | None = None
    max_tokens: int = 1_024

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("Bedrock enabled flag must be boolean.")
        if type(self.max_tokens) is not int or not 1 <= self.max_tokens <= 4_096:
            raise ValueError("Bedrock max_tokens must be between 1 and 4096.")
        if not self.enabled:
            return
        if not _is_safe_configuration_text(self.model_id, max_length=256):
            raise ValueError("Enabled Bedrock configuration requires a model id.")
        if not _is_safe_configuration_text(self.region, max_length=64):
            raise ValueError("Enabled Bedrock configuration requires a region.")


class BedrockGenerationProvider:
    """Map the provider-neutral port to a caller-injected Bedrock client."""

    def __init__(
        self,
        config: BedrockProviderConfig,
        *,
        client_factory: BedrockClientFactory | None = None,
    ) -> None:
        self._config = config
        self._client_factory = client_factory

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        """Call Bedrock only after an explicit generation request."""

        if not self._config.enabled:
            raise ProviderDisabledError("Generation provider is disabled.")
        if self._client_factory is None:
            raise ProviderConfigurationError(
                "Generation provider client is not configured."
            )

        model_id = self._config.model_id
        region = self._config.region
        if model_id is None or region is None:
            raise ProviderConfigurationError(
                "Generation provider configuration is incomplete."
            )

        try:
            client = self._client_factory(region)
            response = client.converse(
                modelId=model_id,
                system=[{"text": request.system_prompt}],
                messages=[
                    {
                        "role": "user",
                        "content": [{"text": request.input_json}],
                    }
                ],
                inferenceConfig={
                    "maxTokens": self._config.max_tokens,
                    "temperature": 0.0,
                },
            )
        except Exception:
            raise ProviderExecutionError(
                "Generation provider request failed."
            ) from None

        return ProviderResponse(
            output_text=_extract_output_text(response),
            usage=_extract_usage(response),
        )


def _is_safe_configuration_text(value: object, *, max_length: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= max_length
        and all(character.isprintable() for character in value)
    )


def _extract_output_text(response: object) -> str:
    if not isinstance(response, Mapping):
        raise ProviderInvalidResponseError(
            "Generation provider returned an invalid response."
        )
    response_mapping = cast(Mapping[object, object], response)
    output = response_mapping.get("output")
    if not isinstance(output, Mapping):
        raise ProviderInvalidResponseError(
            "Generation provider returned an invalid response."
        )
    output_mapping = cast(Mapping[object, object], output)
    message = output_mapping.get("message")
    if not isinstance(message, Mapping):
        raise ProviderInvalidResponseError(
            "Generation provider returned an invalid response."
        )
    message_mapping = cast(Mapping[object, object], message)
    content = message_mapping.get("content")
    if not isinstance(content, Sequence) or isinstance(
        content, (str, bytes, bytearray)
    ):
        raise ProviderInvalidResponseError(
            "Generation provider returned an invalid response."
        )
    content_sequence = cast(Sequence[object], content)
    if len(content_sequence) != 1:
        raise ProviderInvalidResponseError(
            "Generation provider returned an invalid response."
        )
    block = content_sequence[0]
    if not isinstance(block, Mapping):
        raise ProviderInvalidResponseError(
            "Generation provider returned an invalid response."
        )
    block_mapping = cast(Mapping[object, object], block)
    text = block_mapping.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ProviderInvalidResponseError(
            "Generation provider returned an invalid response."
        )
    return text


def _sanitize_usage(raw_usage: object) -> ProviderUsage | None:
    if not isinstance(raw_usage, Mapping):
        return None
    usage = cast(Mapping[object, object], raw_usage)
    input_tokens = _safe_token_count(usage.get("inputTokens"))
    output_tokens = _safe_token_count(usage.get("outputTokens"))
    total_tokens = _safe_token_count(usage.get("totalTokens"))
    if input_tokens is None and output_tokens is None and total_tokens is None:
        return None
    if (
        input_tokens is not None
        and output_tokens is not None
        and total_tokens is not None
        and total_tokens != input_tokens + output_tokens
    ):
        total_tokens = None
    return ProviderUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def _extract_usage(response: object) -> ProviderUsage | None:
    if not isinstance(response, Mapping):
        return None
    response_mapping = cast(Mapping[object, object], response)
    return _sanitize_usage(response_mapping.get("usage"))


def _safe_token_count(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None
