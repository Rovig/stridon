"""Runs the translation of a document: cache, test limit, request
grouping, context, parallelism and progress display."""

import threading
import time
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from dataclasses import dataclass, field

from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeRemainingColumn

from stridon.cache import TranslationCache
from stridon.translators.base import Context, Translator, console

try:
    import tiktoken

    _ENCODING = tiktoken.get_encoding("cl100k_base")

    def count_tokens(text: str) -> int:
        return len(_ENCODING.encode(text))
except Exception:  # tiktoken missing or its data cannot be downloaded
    def count_tokens(text: str) -> int:
        return max(1, len(text) // 4)


@dataclass
class Unit:
    """A piece of text to translate."""

    text: str
    scope: str = ""  # e.g. the chapter: context is kept per scope
    group: object = None  # units with the same group are sent in one request


class RangeNotFound(Exception):
    """--retranslate: the start or end text is not in the document."""


class Collected(Exception):
    """Batch submit mode: the texts were collected, nothing is translated."""


class Interrupted(Exception):
    """Translation stopped (error or Ctrl+C). `results` holds what is done."""

    def __init__(self, results: list, cause: BaseException):
        super().__init__(str(cause))
        self.results = results
        self.cause = cause


@dataclass
class EngineOptions:
    """How the engine groups, schedules and limits translation requests."""

    workers: int = 1
    block_size: int = 1
    token_budget: int = 0  # > 0: group paragraphs up to this many tokens
    context_limit: int = 0  # > 0: send the last N translated paragraphs as context
    test_limit: int | None = None
    verbose: bool = False
    collect: list | None = field(default=None)  # batch submit: gather texts instead of translating
    retranslate: tuple | None = None  # (start text, end text or None, chapter or None)


class Engine:
    """Translates the units of a document with a translator and a cache."""

    def __init__(self, translator: Translator, cache: TranslationCache, options: EngineOptions | None = None):
        self.translator = translator
        self.cache = cache
        self.options = options or EngineOptions()
        self._test_used = 0

    # -- planning -----------------------------------------------------------

    def _groups(self, units: list[Unit], indexes: list[int]) -> list[list[int]]:
        """Split pending units into requests, never mixing scopes."""
        opts = self.options
        groups, current, tokens = [], [], 0

        def flush():
            nonlocal current, tokens
            if current:
                groups.append(current)
            current, tokens = [], 0

        for i in indexes:
            unit = units[i]
            if current and units[current[0]].scope != unit.scope:
                flush()
            if unit.group is not None:
                if current and units[current[0]].group != unit.group:
                    flush()
                current.append(i)
                continue
            if current and units[current[0]].group is not None:
                flush()
            if opts.token_budget > 0:
                size = count_tokens(unit.text)
                if size >= opts.token_budget:
                    flush()
                    groups.append([i])
                    continue
                if tokens + size > opts.token_budget:
                    flush()
                current.append(i)
                tokens += size
            else:
                current.append(i)
                if len(current) >= max(1, opts.block_size):
                    flush()
        flush()
        return groups

    # -- execution ----------------------------------------------------------

    def run(self, units: list[Unit]) -> list[str | None]:
        """Translate the units; returns translations (None = untranslated)."""
        results: list[str | None] = [None] * len(units)
        eligible = [i for i, unit in enumerate(units) if unit.text.strip()]
        if self.options.test_limit is not None:
            remaining = max(0, self.options.test_limit - self._test_used)
            eligible = eligible[:remaining]
            self._test_used += len(eligible)

        forced = self._retranslate_range(units, eligible)
        first_index: dict[str, int] = {}
        pending = []
        for i in eligible:
            cached = None if units[i].text in forced else self.cache.get(units[i].text)
            if cached is not None:
                results[i] = cached
            elif units[i].text not in first_index:
                first_index[units[i].text] = i
                pending.append(i)

        if self.options.collect is not None:
            self.options.collect.extend(units[i].text for i in pending)
            raise Collected()

        def fill_repeats():  # repeated texts reuse the first translation
            for i in eligible:
                if results[i] is None and units[i].text in first_index:
                    results[i] = results[first_index[units[i].text]]

        done_before = sum(1 for i in eligible if results[i] is not None)
        if pending:
            try:
                # Repeated texts are translated once, so they are not counted twice
                self._translate(units, pending, results, done_before + len(pending), done_before)
            except Interrupted:
                fill_repeats()
                raise
        fill_repeats()
        return results

    def _retranslate_range(self, units: list[Unit], eligible: list[int]) -> set[str]:
        """Texts from the passage containing `start` to the one containing
        `end`: they are translated again instead of read from the cache."""
        if not self.options.retranslate:
            return set()
        start, end, chapter = self.options.retranslate
        candidates = [
            i for i in eligible
            if not chapter or units[i].scope == chapter or units[i].scope.endswith("/" + chapter)
        ]
        first = next((k for k, i in enumerate(candidates) if start in units[i].text), None)
        if first is None:
            raise RangeNotFound(f"no passage containing {start!r} was found")
        end = end or start
        last = next((k for k in range(first, len(candidates)) if end in units[candidates[k]].text), None)
        if last is None:
            raise RangeNotFound(f"no passage containing {end!r} was found after {start!r}")
        forced = {units[i].text for i in candidates[first:last + 1]}
        console.print(f"[cyan]{len(forced)} passage(s) will be translated again[/cyan]")
        return forced

    def _translate(self, units, pending, results, total, done_before):
        groups = self._groups(units, pending)
        opts = self.options

        if opts.context_limit > 0:
            # Context must follow the reading order: one sequential task per scope
            by_scope: dict[str, list[list[int]]] = {}
            for group in groups:
                by_scope.setdefault(units[group[0]].scope, []).append(group)
            tasks = list(by_scope.values())
        else:
            tasks = [[group] for group in groups]

        progress = Progress(
            TextColumn("[cyan]Translating"), BarColumn(), MofNCompleteColumn(), TimeRemainingColumn(),
            console=console, transient=False,
        )
        with progress:
            bar = progress.add_task("translate", total=total, completed=done_before)

            stop = threading.Event()
            interval = getattr(self.translator.config, "interval", 0) or 0

            def run_task(task_groups):
                context = Context(opts.context_limit) if opts.context_limit > 0 else None
                for group in task_groups:
                    if stop.is_set():
                        return
                    texts = [units[i].text for i in group]
                    translations = self.translator.translate_batch(texts, context)
                    if interval:
                        time.sleep(interval)
                    for i, text, translation in zip(group, texts, translations):
                        results[i] = translation
                        self.cache.put(text, translation)
                        if opts.verbose:
                            progress.console.print(f"{text}\n[green]{translation}[/green]\n")
                    progress.advance(bar, len(group))

            try:
                if opts.workers <= 1 or len(tasks) == 1:
                    for task in tasks:
                        run_task(task)
                else:
                    pool = ThreadPoolExecutor(max_workers=opts.workers)
                    futures = [pool.submit(run_task, task) for task in tasks]
                    try:
                        finished, _ = wait(futures, return_when=FIRST_EXCEPTION)
                        errors = [f.exception() for f in finished if f.exception()]
                        if errors:
                            raise errors[0]
                    finally:
                        # On error or Ctrl+C, workers stop after their current request
                        stop.set()
                        pool.shutdown(wait=False, cancel_futures=True)
            except (Exception, KeyboardInterrupt) as e:
                raise Interrupted(results, e) from e
