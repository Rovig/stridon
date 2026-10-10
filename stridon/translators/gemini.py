"""Google Gemini through its REST API."""

import threading

import requests

from stridon.translators.base import (
    FatalTranslationError,
    RetryableError,
    ServiceConfig,
    http_error,
)
from stridon.translators.llm import LLMTranslator

DEFAULT_MODEL = "gemini-2.5-flash"
BASE = "https://generativelanguage.googleapis.com/v1beta"
SAFETY_CATEGORIES = [
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
]
# Gemini uses an OpenAPI-style schema
GEMINI_BATCH_SCHEMA = {
    "type": "OBJECT",
    "properties": {"translations": {"type": "ARRAY", "items": {"type": "STRING"}}},
    "required": ["translations"],
}


class BlockedError(RetryableError):
    """Gemini refused to answer; another model may accept the passage."""


class GeminiTranslator(LLMTranslator):
    name = "gemini"
    attempts = 7
    min_wait = 1.0

    def __init__(self, config: ServiceConfig):
        super().__init__(config)
        self.models = config.models or [DEFAULT_MODEL]
        self._model_index = 0
        self._lock = threading.Lock()

    @property
    def supports_json(self) -> bool:
        return True

    def _on_retry(self, error: Exception):
        if isinstance(error, BlockedError) and len(self.models) > 1:
            with self._lock:
                self._model_index = (self._model_index + 1) % len(self.models)

    def _complete(self, system: str, messages: list[dict], schema: dict | None) -> str:
        model = self.models[self._model_index]
        contents = [
            {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
            for m in messages
        ]
        config = {"topP": 1, "topK": 1, "maxOutputTokens": 32768}
        if self.config.temperature is not None:
            config["temperature"] = self.config.temperature
        if schema:
            config["responseMimeType"] = "application/json"
            config["responseSchema"] = GEMINI_BATCH_SCHEMA
        body = {
            "contents": contents,
            "generationConfig": config,
            "safetySettings": [{"category": c, "threshold": "BLOCK_NONE"} for c in SAFETY_CATEGORIES],
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        base = (self.config.api_base or BASE).rstrip("/")
        response = requests.post(
            f"{base}/models/{model}:generateContent",
            headers={"x-goog-api-key": self.config.keys.next()},
            json=body,
            timeout=300,
        )
        if not response.ok:
            if "FAILED_PRECONDITION" in response.text:
                raise FatalTranslationError(f"gemini: service unavailable for this account or region: {response.text[:200]}")
            raise http_error(response, self.name)

        data = response.json()
        candidates = data.get("candidates") or []
        if not candidates:
            reason = data.get("promptFeedback", {}).get("blockReason", "no answer")
            raise BlockedError(f"gemini: request blocked ({reason})")
        candidate = candidates[0]
        if candidate.get("finishReason") in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "RECITATION"):
            raise BlockedError(f"gemini: answer blocked ({candidate['finishReason']})")
        return "".join(part.get("text", "") for part in candidate.get("content", {}).get("parts", []))
