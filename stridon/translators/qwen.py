"""Alibaba Qwen-MT translation models (DashScope, OpenAI-compatible)."""

import requests

from stridon.translators.base import (
    Context,
    Rotation,
    ServiceConfig,
    TranslationError,
    Translator,
    http_error,
    with_retries,
)

DEFAULT_MODEL = "qwen-mt-turbo"
BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# Language names as Qwen-MT expects them
QWEN_NAMES = {
    "english": "English", "simplified chinese": "Chinese", "chinese": "Chinese",
    "traditional chinese": "Traditional Chinese", "norwegian": "Norwegian Bokmål",
}


def qwen_language(name: str) -> str:
    """The language name that Qwen-MT expects."""
    key = name.lower()
    if key in QWEN_NAMES:
        return QWEN_NAMES[key]
    for known, value in QWEN_NAMES.items():
        if key in known or known in key:
            return value
    return name.title()


class QwenTranslator(Translator):
    """Alibaba Qwen-MT: raw text plus translation options, earlier translations as memory."""

    name = "qwen"

    def __init__(self, config: ServiceConfig):
        super().__init__(config)
        self.models = Rotation(config.models or [DEFAULT_MODEL])
        self.base = (config.api_base or BASE).rstrip("/")

    def _post(self, text: str, context: Context | None) -> str:
        options = {
            "source_lang": self.config.source_lang or "auto",
            "target_lang": qwen_language(self.language.name),
        }
        if context:
            # Earlier translations act as translation memory
            options["tm_list"] = [{"source": s, "target": t} for s, t in context.pairs]
        response = requests.post(
            f"{self.base}/chat/completions",
            headers={"Authorization": f"Bearer {self.config.keys.next()}"},
            json={
                "model": self.models.next(),
                "messages": [{"role": "user", "content": text}],
                "translation_options": options,
            },
            timeout=60,
        )
        if not response.ok:
            raise http_error(response, self.name)
        return (response.json()["choices"][0]["message"].get("content") or "").strip()

    def translate(self, text: str, context: Context | None = None) -> str:
        if not text.strip():
            return text
        translation = with_retries(lambda: self._post(text, context), attempts=3, min_wait=1.0, service=self.name)
        if not translation:
            raise TranslationError("qwen: empty answer")
        if context is not None:
            context.add(text, translation)
        return translation
