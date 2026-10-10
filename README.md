**English | [中文](./README-CN.md)**

# Stridon

Stridon turns books, documents and web articles into **bilingual editions**: every paragraph is followed by its translation, made by an AI model or an online translator.

- **Formats:** EPUB, PDF (with images), TXT, Markdown, SRT subtitles, web articles (to PDF and/or EPUB)
- **Services:** Ollama (local, free), OpenAI, Azure OpenAI, Claude, Gemini, DeepSeek, Groq, Mistral, OpenRouter, xAI, Qwen-MT, Google Translate, DeepL, Caiyun, Tencent TranSmart, your own API
- **Every language and script**, including Chinese, Japanese, Arabic and Hebrew in PDFs
- **Automatic resume:** stop at any time, run the same command again, and it continues where it stopped

Use it only for texts you are allowed to translate (your own documents, public domain works, articles for personal reading).

## Installation

Python 3.10 or later.

```shell
git clone git@github.com:Rovig/stridon.git
cd stridon
pip install .
```

Optional extras: `pip install ".[tokens]"` for exact token counts with `--accumulated-tokens`, and `pip install newspaper3k readability-lxml` for more ways to extract web articles.

## Quick start

```shell
# EPUB to French with a local model (Ollama, default service)
stridon book.epub -l fr

# PDF to Chinese with Claude
stridon report.pdf -l zh -s claude

# Web article to PDF and EPUB with Google Translate
stridon https://example.com/article -l fr -s google -f both

# Try on the first 10 passages only
stridon book.epub -l fr --test
```

The result is written next to the input as `<name>_bilingual.<ext>` (web articles: in the current folder, named after the title). Use `-o` to choose another file.

If the run stops (error, network loss, Ctrl+C), a partial `<name>_bilingual_temp.<ext>` is written. **Run the same command again** to continue: passages already translated are reused and not sent to the service again. Use `--fresh` to start over.

## Translation services

Choose with `-s/--service` and optionally `-m/--model`. Keys go in `--api-key` or in an environment variable. Several comma-separated keys or models are used in turn.

| Service | `-s` | Default model | Key (environment variable) |
|---|---|---|---|
| Ollama (local) | `ollama` (default) | `aya` | none; server `http://localhost:11434` or `--api-base` |
| OpenAI | `openai` | `gpt-5-mini` | `OPENAI_API_KEY` |
| Azure OpenAI | `azure` | your deployment | `AZURE_OPENAI_API_KEY`, plus `--api-base` and `--azure-deployment` |
| Anthropic Claude | `claude` | `claude-opus-5-5` | `ANTHROPIC_API_KEY` |
| Google Gemini | `gemini` | `gemini-2.5-flash` | `GEMINI_API_KEY` or `GOOGLE_API_KEY` |
| DeepSeek | `deepseek` | `deepseek-chat` | `DEEPSEEK_API_KEY` |
| Groq | `groq` | `llama-3.3-70b-versatile` | `GROQ_API_KEY` |
| Mistral | `mistral` | `mistral-small-latest` | `MISTRAL_API_KEY` |
| OpenRouter | `openrouter` | required (`-m`) | `OPENROUTER_API_KEY` |
| xAI | `xai` | `grok-3-mini` | `XAI_API_KEY` |
| Qwen-MT (Alibaba) | `qwen` | `qwen-mt-turbo` | `DASHSCOPE_API_KEY` |
| Google Translate | `google` | — | none |
| DeepL (RapidAPI "DeepL Translator") | `deepl` | — | `RAPIDAPI_KEY` |
| Caiyun | `caiyun` | — | `CAIYUN_TOKEN` |
| Tencent TranSmart | `tencent` | — | none |
| Your own API | `custom` | — | URL in `--api-base` |

Every service also reads `STRIDON_<SERVICE>_KEY` (for example `STRIDON_CLAUDE_KEY`).

Notes:
- **Claude:** if the model declines a passage, the request is automatically re-run on Anthropic's recommended fallback model.
- **Gemini:** safety blocking is turned off; a blocked passage switches to the next model of `-m`. Use `--interval` to slow down requests.
- **Qwen-MT:** a dedicated translation model; it ignores `--prompt` and uses `--context` as translation memory.
- **Custom API:** Stridon sends `POST {"text", "source_lang", "target_lang"}` and reads `{"data": "<translation>"}`.
- `--temperature`, `--source-lang`, `--extra-body '{"key": "value"}'` (added to each request of OpenAI-compatible services and Ollama) and `--proxy` are available for every service where they apply.

### Your own providers

Any OpenAI-compatible server (LM Studio, vLLM, a company gateway...) can be declared in `~/.stridon/providers.json` or `./stridon_providers.json` (the local file wins):

```json
{
  "providers": {
    "lmstudio": {
      "api_style": "openai",
      "base_url": "http://localhost:1234/v1",
      "default_models": ["qwen2.5-7b-instruct"],
      "env_key": "LMSTUDIO_KEY"
    }
  }
}
```

Then `stridon book.epub --provider lmstudio`. `api_style` is one of `openai`, `claude`, `gemini`, `qwen`, `ollama`.

## Options

### General

| Option | Description |
|---|---|
| `-l, --language` | Target language, code (`fr`, `zh-hant`, `pt`...) or English name (`French`); default `fr` |
| `-o, --output` | Output file |
| `--only-translation` | Keep only the translation, not the original |
| `--test [N]` | Translate only the first N passages (default 10) |
| `--workers N` | Send N requests in parallel |
| `--fresh` | Ignore translations saved by earlier runs |
| `--verbose` | Print each passage and its translation |

### Prompts and context (AI services)

| Option | Description |
|---|---|
| `--prompt` | Custom prompt: a text containing `{text}` (and optionally `{language}`, `{crlf}` for a newline), a JSON string `{"system": "...", "user": "...{text}..."}`, or a `.txt`, `.json` or PromptDown `.md` file. Environment variables `STRIDON_USER_PROMPT` / `STRIDON_SYSTEM_PROMPT` work too |
| `--context [N]` | Send the last N translated paragraphs (default 3) so names, tone and vocabulary stay consistent; chapters are then translated in order |
| `--block-size N` | Send N paragraphs per request |
| `--accumulated-tokens N` | Group paragraphs into requests of up to N tokens |
| `--sentence-mode` | EPUB: each sentence is followed by its translation |

Prompts using the 3-step format (`<step3_refined_translation>`) and reasoning models (`<think>`) are handled: only the final translation is kept.

### OpenAI Batch API (half price)

```shell
stridon book.epub -s openai --batch-submit   # sends every passage, returns at once
stridon book.epub -s openai --batch-fetch    # within 24 h: loads the results and builds the book
```

### EPUB

| Option | Description |
|---|---|
| `--tags` | Tags to translate (default `p,h1,h2,h3,h4,h5,h6,li,blockquote,dt,dd`; figure captions are included unless `--no-captions`) |
| `--exclude-tags` | Tags never translated, such as footnote marks and code (default `sup,code`) |
| `--bare-text` | Also translate text that is not inside those tags |
| `--only-files`, `--skip-files` | Translate only, or skip, some chapter files (e.g. `--skip-files toc.xhtml`) |
| `--style`, `--color` | Style of the translations, e.g. `--color "#1e90ff"` |
| `--retranslate START [END]` | In a bilingual EPUB made by Stridon, translate again from the paragraph containing START to the one containing END (`--chapter` to choose the chapter file) |
| `--extract` | Make a translation-only EPUB (`<name>_monolingual.epub`) from a bilingual EPUB made by Stridon |
| `--kobo [FOLDER]` | Choose a book from a Kobo e-reader (its folder) or from the Kobo desktop app. Only DRM-free books can be translated |

Image descriptions (alt text) are translated too, unless `--no-captions` or `--no-images`.

### PDF

| Option | Description |
|---|---|
| `--pdf-mode enhanced` | Default: a new bilingual PDF, page by page, with the images in place |
| `--pdf-mode simple` | Text only, written as `<name>_bilingual.txt` and `.epub` |
| `--no-images` | Leave images out |
| `--min-image-size WxH` | Ignore images smaller than this (default `50x50`) |
| `--image-quality 1-100` | Re-encode JPEG images to reduce size (also for EPUB); 100 keeps them unchanged |
| `--batch-lines N` | Text without blank lines: lines per passage (default 10; also for TXT) |

### Web articles

| Option | Description |
|---|---|
| `-f, --format` | `pdf` (default), `epub` or `both` |
| `--user-agent` | Browser identity used to download the page |

### Other formats

- **TXT:** paragraphs are the blocks separated by blank lines (files without blank lines are cut into groups of `--batch-lines` lines).
- **Markdown:** code blocks and images are kept as they are; headings and paragraphs are translated.
- **SRT:** numbers and timings are kept; each subtitle shows the original then the translation.

## Docker

```shell
docker build -t stridon .
docker run --rm -v "$PWD:/work" -e ANTHROPIC_API_KEY stridon book.epub -l fr -s claude
```

To use Ollama running on the host: `--api-base http://host.docker.internal:11434`.

## Development

```shell
pip install -e ".[dev]"
pytest
```
