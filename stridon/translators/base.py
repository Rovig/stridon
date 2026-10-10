"""Common building blocks for translation services."""

import itertools
import random
import threading
import time
from collections import deque
from dataclasses import dataclass, field

import requests
from rich.console import Console

from stridon.languages import Language
from stridon.prompts import Prompt

console = Console(stderr=True)


class TranslationError(Exception):
    """A request failed; the run stops and can be resumed later."""


class FatalTranslationError(TranslationError):
    """The service cannot work at all (bad key, region restriction, ...)."""


class RetryableError(TranslationError):
    """A temporary failure worth retrying (rate limit, server error, network)."""


class KeyRing:
    """Round-robin over one or more comma-separated API keys."""

    def __init__(self, keys: str | None):
        self.keys = [k.strip() for k in (keys or "").split(",") if k.strip()]
        self._cycle = itertools.cycle(self.keys) if self.keys else None
        self._lock = threading.Lock()

    def __bool__(self):
        return bool(self.keys)

    def next(self) -> str:
        if not self._cycle:
            return ""
        with self._lock:
            return next(self._cycle)


class Rotation:
    """Thread-safe round-robin over a list of values (models)."""

    def __init__(self, values: list[str]):
        self.values = values
        self._index = 0
        self._lock = threading.Lock()

    def next(self) -> str:
        with self._lock:
            value = self.values[self._index % len(self.values)]
            self._index += 1
            return value


class Context:
    """The last translated (source, translation) pairs, sent to LLMs so that
    names, tone and vocabulary stay consistent."""

    def __init__(self, limit: int):
        self.pairs = deque(maxlen=limit)

    def add(self, source: str, translation: str):
        if self.pairs.maxlen:
            self.pairs.append((source, translation))

    def __bool__(self):
        return bool(self.pairs)


@dataclass
class ServiceConfig:
    service: str
    language: Language
    models: list[str] = field(default_factory=list)
    keys: KeyRing = field(default_factory=lambda: KeyRing(None))
    api_base: str | None = None
    source_lang: str = "auto"
    temperature: float | None = None
    prompt: Prompt | None = None
    extra_body: dict | None = None
    interval: float = 0.0
    azure_deployment: str | None = None
    api_style: str | None = None  # for --provider


def http_error(response: requests.Response, service: str) -> TranslationError:
    """Turn an HTTP error response into the right error type."""
    detail = response.text[:300].strip()
    message = f"{service}: HTTP {response.status_code} {detail}"
    if response.status_code in (401, 403):
        return FatalTranslationError(message)
    if response.status_code in (408, 409, 425, 429) or response.status_code >= 500:
        return RetryableError(message)
    return TranslationError(message)


def with_retries(call, attempts=3, min_wait=2.0, max_wait=60.0, service="service", on_retry=None):
    """Run call() and retry temporary failures with exponential backoff."""
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except (requests.ConnectionError, requests.Timeout) as e:
            error = RetryableError(f"{service}: network error: {e}")
        except RetryableError as e:
            error = e
        if attempt == attempts:
            raise error
        wait = min(max_wait, min_wait * 2 ** (attempt - 1)) + random.uniform(0, 1)
        console.print(f"[yellow]{error} — retry {attempt}/{attempts - 1} in {wait:.0f}s[/yellow]")
        if on_retry:
            on_retry(error)
        time.sleep(wait)


class Translator:
    """A translation service. Subclasses implement translate()."""

    name = "translator"
    is_llm = False

    def __init__(self, config: ServiceConfig):
        self.config = config
        self.language = config.language

    def translate(self, text: str, context: Context | None = None) -> str:
        raise NotImplementedError

    def translate_batch(self, texts: list[str], context: Context | None = None) -> list[str]:
        """Translate several texts; services without a multi-text protocol
        translate them one by one."""
        return [self.translate(text, context) for text in texts]

    def describe(self) -> str:
        models = ", ".join(self.config.models)
        return f"{self.name}" + (f" ({models})" if models else "")
