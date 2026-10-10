"""OpenAI Batch API: submit every passage at once (half price, results
within 24 h), then load the results into the translation cache.

--batch-submit collects the passages of the document and creates the
batch jobs. --batch-fetch downloads the results into the cache and then
runs a normal translation, which reuses them (anything missing is
translated live).
"""

import json
import time
from datetime import datetime
from pathlib import Path

from stridon.cache import TranslationCache
from stridon.prompts import clean_llm_output
from stridon.translators.base import TranslationError, console

MAX_REQUESTS_PER_FILE = 40_000


class BatchNotReady(Exception):
    pass


def _check(translator):
    if not hasattr(translator, "batch_line") or translator.config.azure_deployment:
        raise TranslationError("the Batch API is only available with OpenAI-compatible services (not Azure)")


def submit(translator, texts: list[str], info_path: Path, name: str):
    _check(translator)
    texts = list(dict.fromkeys(texts))
    if not texts:
        console.print("[green]Nothing to submit: every passage is already translated.[/green]")
        return
    ids = {f"p{i}": text for i, text in enumerate(texts)}
    items = list(ids.items())
    batches = []
    for start in range(0, len(items), MAX_REQUESTS_PER_FILE):
        chunk = items[start:start + MAX_REQUESTS_PER_FILE]
        jsonl = "\n".join(json.dumps(translator.batch_line(cid, text), ensure_ascii=False) for cid, text in chunk)
        file_id = translator.batch_upload(jsonl.encode("utf-8"))
        batch_id = translator.batch_create(file_id, f"Stridon: {name} at {datetime.now():%Y-%m-%d %H:%M:%S}")
        batches.append({"batch_id": batch_id, "input_file_id": file_id})
        console.print(f"Batch {batch_id} created ({len(chunk)} passages)")
    info_path.write_text(json.dumps({
        "name": name,
        "created": datetime.now().isoformat(timespec="seconds"),
        "batches": batches,
        "texts": ids,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    console.print("[green]Submitted. Run the same command with --batch-fetch once the batch is done (up to 24 h).[/green]")


def fetch(translator, info_path: Path, cache: TranslationCache, wait_seconds: int = 300):
    _check(translator)
    if not info_path.is_file():
        raise TranslationError(f"no submitted batch found ({info_path.name} is missing); run --batch-submit first")
    info = json.loads(info_path.read_text(encoding="utf-8"))
    texts = info["texts"]

    deadline = time.time() + wait_seconds
    while True:
        statuses = [translator.batch_status(b["batch_id"]) for b in info["batches"]]
        states = [s.get("status") for s in statuses]
        if all(state in ("completed", "failed", "expired", "cancelled") for state in states):
            break
        if time.time() > deadline:
            raise BatchNotReady(f"batch not finished yet ({', '.join(states)}); try again later")
        console.print(f"[dim]Batch status: {', '.join(states)} — waiting...[/dim]")
        time.sleep(10)

    loaded = 0
    for status in statuses:
        if not status.get("output_file_id"):
            console.print(f"[yellow]Batch {status.get('id')} ended as {status.get('status')} with no results[/yellow]")
            continue
        for line in translator.batch_download(status["output_file_id"]).splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            response = item.get("response") or {}
            text = texts.get(item.get("custom_id"))
            if text is None or response.get("status_code") != 200:
                continue
            content = response["body"]["choices"][0]["message"].get("content") or ""
            translation = clean_llm_output(content)
            if translation:
                cache.put(text, translation)
                loaded += 1
    console.print(f"[green]{loaded} translations loaded from the batch results.[/green]")
