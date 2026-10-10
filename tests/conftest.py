import threading

import pytest

from stridon.cache import TranslationCache
from stridon.engine import Engine, EngineOptions
from stridon.languages import Language
from stridon.translators.base import ServiceConfig, TranslationError, Translator


class FakeTranslator(Translator):
    """Translates by prefixing the text; can be told to fail."""

    name = "fake"

    def __init__(self, fail_after=None, prefix="FR:"):
        super().__init__(ServiceConfig(service="fake", language=Language("fr")))
        self.calls = []
        self.batches = []
        self.fail_after = fail_after
        self.prefix = prefix
        self._lock = threading.Lock()

    def translate(self, text, context=None):
        with self._lock:
            if self.fail_after is not None and len(self.calls) >= self.fail_after:
                raise TranslationError("simulated failure")
            self.calls.append(text)
        translation = f"{self.prefix} {text}"
        if context is not None:
            context.add(text, translation)
        return translation

    def translate_batch(self, texts, context=None):
        self.batches.append(list(texts))
        return super().translate_batch(texts, context)


@pytest.fixture
def fake():
    return FakeTranslator()


def make_engine(translator, cache_path=None, **options):
    return Engine(translator, TranslationCache(cache_path), EngineOptions(**options))


@pytest.fixture
def engine_factory():
    return make_engine
