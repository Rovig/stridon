# Stridon architecture

This document explains how the code is organised and how to extend it. For using Stridon, see the [README](../README.md).

## Overview

```
                 cli.py
                   │  options → DocumentOptions, EngineOptions, ServiceConfig
                   ▼
  formats/*.py  ── reads the document, builds a list of Units
                   │
                   ▼
  engine.py     ── cache lookup, test limit, grouping, context, workers
                   │            ▲
                   ▼            │ translations saved as they arrive
  translators/  ── one service  │
                   │            │
                   ▼            │
               cache.py ────────┘   .<name>.<hash>.stridon.jsonl
                   │
                   ▼
  formats/*.py  ── writes the output from the results (translation only or bilingual)
```

A run always goes through the same steps:

1. **`cli.py`** parses the options, builds the translator (`translators.build_translator`), the cache and the engine, then calls the function of the document format.
2. **The format** (`formats/epub.py`, `pdf.py`, `text.py`, `web.py`) reads the document and turns every passage to translate into a `Unit`.
3. **The engine** (`engine.py`) returns one translation per unit (`None` when a unit is not translated).
4. **The format** writes the output file from those results.

Formats never call translators directly, and translators know nothing about documents.

## Core concepts

### Units (`engine.Unit`)

| Field | Meaning |
|---|---|
| `text` | The text to translate. An empty text means "do not translate" (e.g. page numbers, code). |
| `scope` | Usually the chapter. Context (`--context`) is kept per scope, and requests never mix scopes. |
| `group` | Units with the same group are sent in one request (used by `--sentence-mode`). |

### Engine (`engine.Engine`)

`Engine.run(units)` does, in order:

1. Drops empty units and applies `--test N` (only the first N passages).
2. Marks the `--retranslate` range: those units ignore the cache.
3. Reads the cache. Identical texts are translated once.
4. Groups the remaining units into requests: `--block-size`, `--accumulated-tokens` or sentence groups.
5. Sends the requests, with `--workers` threads. With `--context`, each scope is translated in order by a single thread, so the context follows the reading order.
6. Saves each translation to the cache as soon as it arrives.

If anything fails, or on Ctrl+C, the workers stop after their current request and the engine raises `Interrupted`, which carries the results obtained so far. With `--batch-submit`, it raises `Collected` instead of translating.

### Cache and resume (`cache.py`)

- One JSON Lines file per document and per settings: `.<name>.<hash>.stridon.jsonl`, next to the input (in the current folder for URLs).
- The hash covers the service, models, language, prompt and source language. Changing any of them starts a new cache, so translations from different settings are never mixed.
- Keys are hashes of the source text. Resuming therefore works even if the document is split differently, and repeated passages are translated once.
- Lines are appended as translations arrive. A line cut by a crash is ignored when reading.

There is no resume option: every run reads the cache. `--fresh` deletes it. `--retranslate` bypasses it for a range. `--batch-fetch` fills it from OpenAI batch results before a normal run.

### Partial results

Each format calls `common.translate(engine, units)`, which returns `(results, error)` instead of raising. The format always writes its output; when `error` is set, the file name gets `_temp` and `common.finish` raises `Stopped`. The CLI prints the partial file and tells the user to run the same command again.

### Output layout

`DocumentOptions.bilingual` selects the layout:

- **Translation only (default):** the original text is replaced, the structure is kept. Output `<name>_<language>.<ext>`.
- **Bilingual (`--bilingual`):** each original passage is followed by its translation. Output `<name>_bilingual.<ext>`.

Both layouts use the same units and therefore the same cache: switching layout sends no request.

## Translators (`translators/`)

```
Translator (base.py)                  translate(), translate_batch()
├── LLMTranslator (llm.py)            prompts, context, multi-segment requests, retries
│   ├── OpenAICompatTranslator        OpenAI, Azure, xAI, Groq, DeepSeek, Mistral, OpenRouter, providers
│   ├── ClaudeTranslator              anthropic SDK, refusal fallback
│   ├── GeminiTranslator              REST, safety off, model switch when blocked
│   └── OllamaTranslator              native /api/chat
├── QwenTranslator                    Qwen-MT, translation memory instead of a prompt
└── _WebTranslator (web_services.py)  Google, DeepL (RapidAPI), Caiyun, Tencent, custom API
```

Shared building blocks (`base.py`):

| Name | Role |
|---|---|
| `ServiceConfig` | Language, models, keys, API base, temperature, prompt, extra body, interval |
| `KeyRing`, `Rotation` | Round-robin over comma-separated keys and models |
| `Context` | The last N (source, translation) pairs, sent to LLMs |
| `with_retries` | Exponential backoff on `RetryableError` and network errors |
| `TranslationError` | Stops the run; it can be resumed |
| `FatalTranslationError` | The service cannot work (bad key, region...): stop at once |

`LLMTranslator` turns `translate_batch(texts)` into one request. It uses JSON output when the service supports a schema (`supports_json`), otherwise segments separated by `@@` lines. If the answer does not have the right number of translations, it falls back to one request per text. Reasoning blocks (`<think>`) and 3-step prompts (`<step3_refined_translation>`) are cleaned by `prompts.clean_llm_output`.

### Adding a service

1. For an OpenAI-compatible server, add a line to `SERVICES` in `translators/__init__.py` with its URL, key variables and default model. Nothing else is needed.
2. Otherwise:
   - For an LLM, subclass `LLMTranslator` and implement `_complete(system, messages, schema) -> str`. Raise `RetryableError` for temporary failures and `FatalTranslationError` for authentication errors; `base.http_error` maps HTTP status codes to these.
   - For a plain translation API, subclass `_WebTranslator` and implement `_translate(text) -> str`.
3. Register a new `style` in `build_translator`.
4. Add a test in `tests/test_translators.py` that replaces `requests.post` and checks the request sent.

## Formats (`formats/`)

| Module | Input | Notes |
|---|---|---|
| `epub.py` + `epub_archive.py` | EPUB | Works on the zip archive: only the chapters that change are rewritten, every other entry is copied byte for byte |
| `pdf.py` | PDF | `enhanced`: rebuilds pages with PyMuPDF, images included; `simple`: text to `.txt` and `.epub` |
| `text.py` | TXT, Markdown, SRT | Markdown code fences are never translated; SRT numbers and timings come from the source |
| `web.py` | URL | Extractors tried in order (trafilatura, newspaper3k, readability, heuristics); PDF via `pymupdf.Story` |
| `epub_writer.py` | — | Minimal EPUB 3 writer for web articles and simple PDFs |

### EPUB details

- **Reading chapters:** chapters are parsed as XML when they are well-formed, so they are written back unchanged apart from the translations. Otherwise they are parsed as HTML, which keeps all the text. The declared encoding is honoured and the output is always UTF-8. A chapter that cannot be rebuilt as valid XHTML is left untranslated, with a warning.
- **Selecting text:** only the innermost selected tags are translated (`--tags`). The content of `--exclude-tags` (footnote marks, code) is not sent, but it is kept in the output.
- **Marking translations:** in bilingual books, every inserted translation has the class `stridon-translation` (plus `stridon-sentence` in sentence mode). `--extract` relies on these classes.
- **Translation-only output:** the paragraph text is replaced, and images, anchors with an `id` and excluded tags are kept, so links and the table of contents still work.

### PDF details

Text blocks are read with their position and font size, and images with their displayed size. Everything is sorted top to bottom. `_Layout` writes text with `insert_htmlbox`, which picks a built-in font for every script (CJK, Arabic, Hebrew...). Text that does not fit moves to a new page; text longer than a page is split. In translation-only mode the text flows across pages; in bilingual mode each source page starts a new page.

### Adding a format

1. Write `translate_<format>(source, engine, options) -> list[Path]`:
   - build the units;
   - call `results, error = translate(engine, units)`;
   - write the output with `output_path(source, ext, options, partial=error is not None)`;
   - `return finish([path], error)`.
2. Handle both layouts (`options.bilingual`).
3. Register the file extension in `FORMATS` and in `_run_document` in `cli.py`.
4. Add tests in `tests/test_formats.py` using `FakeTranslator` from `tests/conftest.py`.

## Other modules

| Module | Role |
|---|---|
| `languages.py` | Language codes and names; `Language("fr")` gives the name for prompts and the code for APIs |
| `prompts.py` | `--prompt` loading (text, JSON, .txt, .json, PromptDown .md) and `{text}`, `{language}`, `{crlf}` placeholders |
| `batch.py` | OpenAI Batch API: `submit` uploads every uncached passage; `fetch` loads results into the cache |
| `kobo.py` | Lists books from a Kobo e-reader or the desktop app and copies a DRM-free one. Protected books are refused |
| `image_utils.py` | JPEG recompression for `--image-quality` |

## Tests

```shell
pip install -e ".[dev]"
pytest
```

The tests never use the network:

- `FakeTranslator` (in `tests/conftest.py`) prefixes texts with `FR:` and can be told to fail after N calls.
- Service tests replace `requests.post`, or the `anthropic.Anthropic` client.
- EPUB and PDF fixtures are built in the tests themselves.

GitHub Actions runs the suite on Python 3.10, 3.12, 3.13 and 3.14, and checks that the Docker image builds.
