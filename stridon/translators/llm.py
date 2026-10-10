"""Shared logic for LLM-based services: prompts, context and
multi-segment requests."""

import json
import re

from stridon.prompts import DEFAULT_USER, Prompt, clean_llm_output
from stridon.translators.base import (
    Context,
    FatalTranslationError,
    TranslationError,
    Translator,
    console,
    with_retries,
)

BATCH_SCHEMA = {
    "type": "object",
    "properties": {"translations": {"type": "array", "items": {"type": "string"}}},
    "required": ["translations"],
    "additionalProperties": False,
}

_DELIMITER = "@@"
_SPLIT = re.compile(r"^\s*@@\s*$", re.M)


class LLMTranslator(Translator):
    """Base for LLM services: subclasses only implement `_complete`."""

    is_llm = True
    default_system = ""
    attempts = 3
    min_wait = 2.0

    @property
    def supports_json(self) -> bool:
        """Whether the service can return output that follows a JSON schema."""
        return False

    def _complete(self, system: str, messages: list[dict], schema: dict | None) -> str:
        """Send one request and return the raw text answer."""
        raise NotImplementedError

    def _on_retry(self, error: Exception):
        """Hook called before a retry (e.g. to switch model)."""

    @property
    def prompt(self) -> Prompt:
        """The custom prompt, or the service's default one."""
        if self.config.prompt:
            prompt = self.config.prompt
            return Prompt(user=prompt.user, system=prompt.system or self.default_system)
        return Prompt(user=DEFAULT_USER, system=self.default_system)

    def _messages(self, user: str, context: Context | None) -> list[dict]:
        messages = []
        if context:
            sources = "\n\n".join(source for source, _ in context.pairs)
            translations = "\n\n".join(target for _, target in context.pairs)
            messages.append({"role": "user", "content": self.prompt.render_user(sources, self.language.name)})
            messages.append({"role": "assistant", "content": translations})
        messages.append({"role": "user", "content": user})
        return messages

    def _request(self, user: str, context: Context | None, schema: dict | None = None) -> str:
        system = self.prompt.render_system(self.language.name)
        messages = self._messages(user, context)
        return with_retries(
            lambda: self._complete(system, messages, schema),
            attempts=self.attempts,
            min_wait=self.min_wait,
            service=self.name,
            on_retry=self._on_retry,
        )

    def translate(self, text: str, context: Context | None = None) -> str:
        if not text.strip():
            return text
        answer = self._request(self.prompt.render_user(text, self.language.name), context)
        translation = clean_llm_output(answer)
        if not translation:
            raise TranslationError(f"{self.name}: empty answer")
        if context is not None:
            context.add(text, translation)
        return translation

    def translate_batch(self, texts: list[str], context: Context | None = None) -> list[str]:
        """Translate several texts in one request (JSON output or @@ separators), or one by one if the answer does not match."""
        if len(texts) == 1:
            return [self.translate(texts[0], context)]
        try:
            if self.supports_json:
                translations = self._batch_json(texts, context)
            else:
                translations = self._batch_delimited(texts, context)
        except FatalTranslationError:
            raise
        except (TranslationError, ValueError) as e:
            console.print(f"[yellow]{self.name}: grouped request failed ({e}), translating one by one[/yellow]")
            translations = None

        if translations is None or len(translations) != len(texts) or not all(t.strip() for t in translations):
            if translations is not None:
                console.print(
                    f"[yellow]{self.name}: expected {len(texts)} translations, got {len(translations)}; "
                    "translating one by one[/yellow]"
                )
            return [self.translate(text, context) for text in texts]

        if context is not None:
            for text, translation in zip(texts, translations):
                context.add(text, translation)
        return translations

    def _batch_json(self, texts: list[str], context: Context | None) -> list[str]:
        payload = json.dumps(texts, ensure_ascii=False)
        user = (
            self.prompt.render_user(payload, self.language.name)
            + f"\n\nThe text is a JSON array of {len(texts)} segments. Translate each segment and answer "
            f'with a JSON object {{"translations": [...]}} holding exactly {len(texts)} strings, in order.'
        )
        answer = self._request(user, context, schema=BATCH_SCHEMA)
        data = json.loads(clean_llm_output(answer))
        return [clean_llm_output(t) for t in data["translations"]]

    def _batch_delimited(self, texts: list[str], context: Context | None) -> list[str]:
        joined = f"\n\n{_DELIMITER}\n\n".join(text.strip() for text in texts)
        user = (
            f"The text has {len(texts)} segments separated by lines containing only {_DELIMITER}. "
            f"Translate every segment and keep the {_DELIMITER} lines between them, so that the answer "
            f"has exactly {len(texts)} segments in the same order.\n\n"
            + self.prompt.render_user(joined, self.language.name)
        )
        answer = clean_llm_output(self._request(user, context))
        parts = [part.strip() for part in _SPLIT.split(answer)]
        return [part for part in parts if part]
