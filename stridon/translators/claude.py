"""Anthropic Claude, through the official anthropic SDK."""

import threading

import anthropic

from stridon.translators.base import (
    FatalTranslationError,
    RetryableError,
    Rotation,
    ServiceConfig,
    TranslationError,
    console,
)
from stridon.translators.llm import LLMTranslator

DEFAULT_MODEL = "claude-opus-5-5"
# Models that accept server-side refusal fallbacks on the Claude API
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that reject sampling parameters such as temperature
NO_SAMPLING = ("claude-opus-5", "claude-sonnet-5", "claude-haiku-5", "claude-fable", "claude-mythos",
               "claude-opus-4-7", "claude-opus-4-8")


class ClaudeTranslator(LLMTranslator):
    name = "claude"

    def __init__(self, config: ServiceConfig):
        super().__init__(config)
        self.models = Rotation(config.models or [DEFAULT_MODEL])
        self._clients = {}
        self._lock = threading.Lock()
        self._warned = False

    @property
    def supports_json(self) -> bool:
        return True

    def _client(self) -> anthropic.Anthropic:
        key = self.config.keys.next()
        with self._lock:
            if key not in self._clients:
                self._clients[key] = anthropic.Anthropic(
                    api_key=key or None,
                    base_url=self.config.api_base or None,
                    max_retries=0,  # retries are handled by LLMTranslator
                    timeout=300.0,
                )
            return self._clients[key]

    def _complete(self, system: str, messages: list[dict], schema: dict | None) -> str:
        model = self.models.next()
        params = {"model": model, "max_tokens": 16000, "messages": messages}
        if system:
            params["system"] = system
        if self.config.temperature is not None:
            if model.startswith(NO_SAMPLING):
                if not self._warned:
                    console.print(f"[yellow]claude: {model} does not accept --temperature, ignoring it[/yellow]")
                    self._warned = True
            else:
                params["temperature"] = self.config.temperature
        extra_body = {}
        if schema:
            extra_body["output_config"] = {"format": {"type": "json_schema", "schema": schema}}

        try:
            client = self._client()
            if model in FALLBACK_MODELS and not self.config.api_base:
                # Re-run a declined request on Anthropic's recommended fallback model
                extra_body["fallbacks"] = "default"
                response = client.beta.messages.create(betas=[FALLBACK_BETA], extra_body=extra_body, **params)
            else:
                response = client.messages.create(extra_body=extra_body or None, **params)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise FatalTranslationError(f"claude: {e.message}") from e
        except (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError) as e:
            raise RetryableError(f"claude: {e}") from e
        except anthropic.APIStatusError as e:
            if e.status_code >= 500 or e.status_code == 529:
                raise RetryableError(f"claude: {e.message}") from e
            raise TranslationError(f"claude: {e.message}") from e

        if response.stop_reason == "refusal":
            raise TranslationError("claude: the model declined to translate this passage")
        return "".join(block.text for block in response.content if block.type == "text")
