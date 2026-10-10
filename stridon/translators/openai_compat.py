"""Services that speak the OpenAI chat completions API: OpenAI, Azure
OpenAI, xAI, Groq, DeepSeek, Mistral, OpenRouter and custom providers."""

import json
import threading

import requests

from stridon.translators.base import (
    Rotation,
    ServiceConfig,
    TranslationError,
    console,
    http_error,
)
from stridon.translators.llm import BATCH_SCHEMA, LLMTranslator

AZURE_API_VERSION = "2024-10-21"


class OpenAICompatTranslator(LLMTranslator):
    """Any server implementing the OpenAI chat completions API (and Azure OpenAI)."""

    timeout = 120

    def __init__(self, config: ServiceConfig, name: str = "openai"):
        super().__init__(config)
        self.name = name
        self.base = (config.api_base or "https://api.openai.com/v1").rstrip("/")
        self.models = Rotation(config.models or [""])
        self._json_support = None
        self._probe_lock = threading.Lock()

    # -- HTTP -------------------------------------------------------------

    def _headers(self) -> dict:
        key = self.config.keys.next()
        if self.config.azure_deployment:
            return {"api-key": key}
        return {"Authorization": f"Bearer {key}"} if key else {}

    def _chat_url(self) -> str:
        if self.config.azure_deployment:
            return (
                f"{self.base}/openai/deployments/{self.config.azure_deployment}"
                f"/chat/completions?api-version={AZURE_API_VERSION}"
            )
        return f"{self.base}/chat/completions"

    def build_body(self, system: str, messages: list[dict], schema: dict | None = None, model: str | None = None) -> dict:
        """Request body of a chat completion (also used for Batch API lines)."""
        model = model if model is not None else self.models.next()
        all_messages = ([{"role": "system", "content": system}] if system else []) + messages
        body = {"messages": all_messages}
        if not self.config.azure_deployment:
            body["model"] = model
        if self.config.temperature is not None:
            body["temperature"] = self.config.temperature
        if schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "translation", "strict": True, "schema": schema},
            }
        if self.config.extra_body:
            body.update(self.config.extra_body)
        return body

    def _post(self, body: dict) -> str:
        response = requests.post(self._chat_url(), headers=self._headers(), json=body, timeout=self.timeout)
        if not response.ok:
            raise http_error(response, self.name)
        choice = response.json()["choices"][0]
        return choice["message"].get("content") or ""

    def _complete(self, system: str, messages: list[dict], schema: dict | None) -> str:
        return self._post(self.build_body(system, messages, schema))

    # -- capabilities -----------------------------------------------------

    @property
    def supports_json(self) -> bool:
        """Probe once whether the server accepts JSON schema output."""
        with self._probe_lock:
            if self._json_support is None:
                body = self.build_body("", [{"role": "user", "content": 'Answer with {"translations": ["ok"]}'}], BATCH_SCHEMA)
                try:
                    json.loads(self._post(body))
                    self._json_support = True
                except (TranslationError, ValueError, KeyError, requests.RequestException):
                    console.print(f"[dim]{self.name}: JSON output not supported, grouped requests use separators[/dim]")
                    self._json_support = False
            return self._json_support

    def check_models(self):
        """Warn about requested models the server does not list."""
        if self.config.azure_deployment or not self.config.models:
            return
        try:
            response = requests.get(f"{self.base}/models", headers=self._headers(), timeout=30)
            if not response.ok:
                return
            available = {m.get("id") for m in response.json().get("data", [])}
        except (requests.RequestException, ValueError):
            return
        missing = [m for m in self.config.models if m not in available]
        if missing and available:
            console.print(f"[yellow]{self.name}: model(s) not listed by the server: {', '.join(missing)}[/yellow]")

    # -- OpenAI Batch API -------------------------------------------------

    def batch_line(self, custom_id: str, text: str) -> dict:
        """One request line of a Batch API input file."""
        user = self.prompt.render_user(text, self.language.name)
        body = self.build_body(self.prompt.render_system(self.language.name), [{"role": "user", "content": user}])
        return {"custom_id": custom_id, "method": "POST", "url": "/v1/chat/completions", "body": body}

    def batch_upload(self, jsonl: bytes) -> str:
        """Upload a Batch API input file; returns its file id."""
        response = requests.post(
            f"{self.base}/files",
            headers=self._headers(),
            data={"purpose": "batch"},
            files={"file": ("batch.jsonl", jsonl, "application/jsonl")},
            timeout=300,
        )
        if not response.ok:
            raise http_error(response, self.name)
        return response.json()["id"]

    def batch_create(self, file_id: str, description: str) -> str:
        """Start a batch job for an uploaded file; returns the batch id."""
        response = requests.post(
            f"{self.base}/batches",
            headers=self._headers(),
            json={
                "input_file_id": file_id,
                "endpoint": "/v1/chat/completions",
                "completion_window": "24h",
                "metadata": {"description": description},
            },
            timeout=60,
        )
        if not response.ok:
            raise http_error(response, self.name)
        return response.json()["id"]

    def batch_status(self, batch_id: str) -> dict:
        """Status of a batch job."""
        response = requests.get(f"{self.base}/batches/{batch_id}", headers=self._headers(), timeout=60)
        if not response.ok:
            raise http_error(response, self.name)
        return response.json()

    def batch_download(self, file_id: str) -> str:
        """Content of a batch output file (JSON Lines)."""
        response = requests.get(f"{self.base}/files/{file_id}/content", headers=self._headers(), timeout=300)
        if not response.ok:
            raise http_error(response, self.name)
        return response.text
