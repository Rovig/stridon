"""Local models served by Ollama (native /api/chat endpoint)."""

import requests

from stridon.translators.base import (
    FatalTranslationError,
    RetryableError,
    Rotation,
    ServiceConfig,
    http_error,
)
from stridon.translators.llm import LLMTranslator

DEFAULT_MODEL = "aya"
DEFAULT_BASE = "http://localhost:11434"

SYSTEM = (
    "You are a professional literary translator. Keep the author's tone, style and intent, "
    "keep formatting, punctuation and paragraph structure, and write fluent, natural text."
)
USER = (
    "Translate the following text to {language}:{crlf}{crlf}{text}{crlf}{crlf}"
    "Provide only the translation, with no explanations or notes."
)


class OllamaTranslator(LLMTranslator):
    name = "ollama"
    default_system = SYSTEM
    min_wait = 2.0

    def __init__(self, config: ServiceConfig):
        super().__init__(config)
        base = (config.api_base or DEFAULT_BASE).rstrip("/")
        # Accept the OpenAI-compatible URL form too
        self.base = base[:-3] if base.endswith("/v1") else base
        self.models = Rotation(config.models or [DEFAULT_MODEL])
        self.temperature = 0.3 if config.temperature is None else config.temperature

    @property
    def prompt(self):
        prompt = super().prompt
        if not self.config.prompt:
            prompt.user = USER
        return prompt

    def _complete(self, system: str, messages: list[dict], schema: dict | None) -> str:
        model = self.models.next()
        body = {
            "model": model,
            "messages": ([{"role": "system", "content": system}] if system else []) + messages,
            "stream": False,
            "options": {"temperature": self.temperature, "top_p": 0.9, "repeat_penalty": 1.1, "num_ctx": 4096},
        }
        if self.config.extra_body:
            body.update(self.config.extra_body)
        try:
            response = requests.post(f"{self.base}/api/chat", json=body, timeout=600)
        except requests.ConnectionError as e:
            raise RetryableError(f"ollama: cannot reach {self.base} — is `ollama serve` running?") from e
        if response.status_code == 404:
            raise FatalTranslationError(f"ollama: model '{model}' not found — check `ollama list` or run `ollama pull {model}`")
        if not response.ok:
            raise http_error(response, self.name)
        text = response.json().get("message", {}).get("content", "")
        if not text.strip():
            raise RetryableError("ollama: empty answer")
        return text
