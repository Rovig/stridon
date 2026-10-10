"""Saved translations, so an interrupted run continues where it stopped.

Translations are appended to a JSON Lines file as soon as they arrive,
keyed by a hash of the source text. The file name includes a hash of the
settings that change the result (service, models, language, prompt), so
changing them starts a new cache instead of reusing old translations.
"""

import hashlib
import json
import threading
from pathlib import Path


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


class TranslationCache:
    """Translations of one document, kept in memory and appended to a JSON Lines file."""

    def __init__(self, path: Path | None):
        self.path = path
        self.entries: dict[str, str] = {}
        self._lock = threading.Lock()
        if path and path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    item = json.loads(line)
                    self.entries[item["k"]] = item["t"]
                except (ValueError, KeyError):
                    continue  # a line cut by a crash

    @classmethod
    def for_document(cls, directory: Path, name: str, settings: str) -> "TranslationCache":
        """Cache file `.<name>.<settings hash>.stridon.jsonl` in `directory`."""
        return cls(directory / f".{name}.{_hash(settings)[:8]}.stridon.jsonl")

    def __len__(self):
        return len(self.entries)

    def get(self, text: str) -> str | None:
        """Saved translation of `text`, or None."""
        return self.entries.get(_hash(text))

    def put(self, text: str, translation: str):
        """Save a translation (thread-safe); it is written to disk immediately."""
        key = _hash(text)
        with self._lock:
            self.entries[key] = translation
            if self.path:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"k": key, "t": translation}, ensure_ascii=False) + "\n")

    def clear(self):
        """Forget every saved translation and delete the cache file."""
        with self._lock:
            self.entries.clear()
            if self.path and self.path.exists():
                self.path.unlink()
