"""Command line interface: stridon INPUT [options]."""

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from stridon import batch, kobo
from stridon.cache import TranslationCache
from stridon.engine import Collected, Engine, EngineOptions, RangeNotFound
from stridon.formats.common import DocumentOptions, Stopped
from stridon.formats.epub_archive import EpubError
from stridon.formats.web import WebError, is_url
from stridon.languages import Language
from stridon.prompts import PromptError, load_prompt
from stridon.translators import SERVICES, build_translator
from stridon.translators.base import FatalTranslationError, TranslationError, console

FORMATS = {".epub", ".txt", ".md", ".srt", ".pdf"}
DEFAULT_TAGS = "p,h1,h2,h3,h4,h5,h6,li,blockquote,dt,dd"


def build_parser() -> argparse.ArgumentParser:
    """Command line options."""
    parser = argparse.ArgumentParser(
        prog="stridon",
        description="Translate EPUB, PDF, TXT, Markdown and SRT files or web articles into bilingual editions.",
    )
    parser.add_argument("input", nargs="?", help="file to translate (epub, pdf, txt, md, srt) or article URL")

    general = parser.add_argument_group("general")
    general.add_argument("-l", "--language", default="fr", help="target language, as a code (fr) or a name (French); default: fr")
    general.add_argument("-o", "--output", type=Path, help="output file (default: <name>_<language>.<ext> next to the input)")
    general.add_argument("--bilingual", action="store_true", help="alternate each original paragraph and its translation (default: translation only)")
    general.add_argument("--test", nargs="?", type=int, const=10, metavar="N", help="translate only the first N passages (default 10)")
    general.add_argument("--workers", type=int, default=1, metavar="N", help="number of requests sent in parallel")
    general.add_argument("--fresh", action="store_true", help="ignore translations saved by earlier runs")
    general.add_argument("--verbose", action="store_true", help="print each passage and its translation")
    general.add_argument("--proxy", help="HTTP(S) proxy URL")

    service = parser.add_argument_group("translation service")
    service.add_argument("-s", "--service", default="ollama", choices=list(SERVICES), help="translation service (default: ollama)")
    service.add_argument("-m", "--model", help="model name; several comma-separated models are used in turn")
    service.add_argument("--api-key", help="API key; several comma-separated keys are used in turn")
    service.add_argument("--api-base", help="API base URL (Azure endpoint, Ollama server, custom API URL...)")
    service.add_argument("--provider", help="custom provider from ~/.stridon/providers.json or ./stridon_providers.json")
    service.add_argument("--azure-deployment", help="Azure OpenAI deployment name (with -s azure)")
    service.add_argument("--temperature", type=float, help="sampling temperature")
    service.add_argument("--source-lang", default="auto", help="source language, for services that use it (default: auto)")
    service.add_argument("--interval", type=float, default=0.0, help="seconds to wait after each request")
    service.add_argument("--extra-body", help="JSON object added to each request (OpenAI-compatible services, Ollama)")
    service.add_argument("--prompt", help="custom prompt: text with {text}, or a .txt, .json or PromptDown .md file")

    grouping = parser.add_argument_group("requests and context")
    grouping.add_argument("--block-size", type=int, default=1, metavar="N", help="send N paragraphs per request")
    grouping.add_argument("--accumulated-tokens", type=int, default=0, metavar="N", help="group paragraphs into requests of up to N tokens")
    grouping.add_argument("--sentence-mode", action="store_true", help="EPUB: show each sentence followed by its translation (implies --bilingual)")
    grouping.add_argument("--context", nargs="?", type=int, const=3, default=0, metavar="N", help="send the last N translated paragraphs to the model (default 3)")
    grouping.add_argument("--batch-lines", type=int, default=10, metavar="N", help="TXT/PDF without blank lines: lines per passage (default 10)")
    grouping.add_argument("--batch-submit", action="store_true", help="OpenAI Batch API: submit all passages (half price, results within 24 h)")
    grouping.add_argument("--batch-fetch", action="store_true", help="OpenAI Batch API: load the results, then build the document")

    epub = parser.add_argument_group("EPUB")
    epub.add_argument("--tags", default=DEFAULT_TAGS, help=f"HTML tags to translate (default: {DEFAULT_TAGS})")
    epub.add_argument("--exclude-tags", default="sup,code", help="tags whose content is never translated (default: sup,code)")
    epub.add_argument("--bare-text", action="store_true", help="also translate text that is outside the selected tags")
    epub.add_argument("--only-files", default="", help="translate only these chapter files (comma-separated)")
    epub.add_argument("--skip-files", default="", help="leave these chapter files untranslated (comma-separated)")
    epub.add_argument("--style", default="", help="with --bilingual: CSS style for the translations, e.g. 'color: #555'")
    epub.add_argument("--color", help="with --bilingual: color of the translations, e.g. '#1e90ff'")
    epub.add_argument("--retranslate", nargs="+", metavar="TEXT", help="translate again from the passage containing START to the one containing END, then rebuild the output (any format)")
    epub.add_argument("--chapter", help="with --retranslate on an EPUB: chapter file to search")
    epub.add_argument("--extract", action="store_true", help="make a translation-only EPUB from a bilingual EPUB made by Stridon")
    epub.add_argument("--kobo", nargs="?", const="", metavar="FOLDER", help="pick a DRM-free book from a Kobo e-reader (its folder) or the Kobo app")

    images = parser.add_argument_group("images")
    images.add_argument("--no-images", action="store_true", help="drop images (PDF) and do not translate alt texts")
    images.add_argument("--no-captions", action="store_true", help="do not translate figure captions and alt texts")
    images.add_argument("--image-quality", type=int, default=100, metavar="1-100", help="re-encode JPEG images at this quality to reduce size (100 = keep originals)")
    images.add_argument("--min-image-size", default="50x50", metavar="WxH", help="PDF: ignore images smaller than this (default 50x50)")

    other = parser.add_argument_group("PDF and web")
    other.add_argument("--pdf-mode", choices=["enhanced", "simple"], default="enhanced", help="enhanced: bilingual PDF with images; simple: text to .txt and .epub")
    other.add_argument("-f", "--format", dest="output_format", choices=["pdf", "epub", "both"], default="pdf", help="web articles: output format (default: pdf)")
    other.add_argument("--user-agent", default="Mozilla/5.0 (compatible; Stridon/1.0)", help="web articles: browser identity used to download the page")
    return parser


def _split(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _document_options(args, parser, language: Language) -> DocumentOptions:
    try:
        width, height = (int(v) for v in args.min_image_size.lower().split("x"))
    except ValueError:
        parser.error("--min-image-size must look like 50x50")
    if not 1 <= args.image_quality <= 100:
        parser.error("--image-quality must be between 1 and 100")
    style = args.style or (f"color: {args.color};" if args.color else "")
    bilingual = args.bilingual or args.sentence_mode
    if args.sentence_mode and not args.bilingual:
        console.print("[dim]--sentence-mode shows the original sentences: bilingual output[/dim]")
    language_tag = re.sub(r"[^\w-]+", "-", language.code).strip("-") or "translated"
    return DocumentOptions(
        output=args.output,
        bilingual=bilingual,
        output_suffix="_bilingual" if bilingual else f"_{language_tag}",
        style=style,
        tags=_split(args.tags),
        exclude_tags=_split(args.exclude_tags),
        bare_text=args.bare_text,
        only_files=_split(args.only_files),
        skip_files=_split(args.skip_files),
        sentence_mode=args.sentence_mode,
        images=not args.no_images,
        captions=not args.no_captions,
        image_quality=args.image_quality,
        min_image_size=(width, height),
        pdf_mode=args.pdf_mode,
        batch_lines=args.batch_lines,
        output_format=args.output_format,
        user_agent=args.user_agent,
    )


def _cache_for(source: str, settings: str) -> tuple[TranslationCache, Path]:
    """Translation cache and batch info file of a document."""
    if is_url(source):
        directory, name = Path.cwd(), "url-" + hashlib.md5(source.encode()).hexdigest()[:12]
    else:
        path = Path(source)
        directory, name = path.parent, path.stem
    cache = TranslationCache.for_document(directory, name, settings)
    return cache, cache.path.with_name(cache.path.name.replace(".stridon.jsonl", ".batch.json"))


def _run_document(source: str, engine: Engine, options: DocumentOptions, args) -> list[Path]:
    from stridon.formats import epub, pdf, text, web

    if is_url(source):
        return web.translate_url(source, engine, options)
    path = Path(source)
    suffix = path.suffix.lower()
    handlers = {
        ".epub": epub.translate_epub,
        ".pdf": pdf.translate_pdf,
        ".txt": text.translate_txt,
        ".md": text.translate_markdown,
        ".srt": text.translate_srt,
    }
    return handlers[suffix](path, engine, options)


def main(argv=None) -> int:
    """Run the command line; returns the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.proxy:
        os.environ["http_proxy"] = os.environ["https_proxy"] = args.proxy
    if args.retranslate and len(args.retranslate) > 2:
        parser.error("--retranslate takes START and an optional END")

    try:
        if args.extract:
            if not args.input:
                parser.error("--extract needs a bilingual EPUB")
            from stridon.formats.epub import extract_translation

            out, count = extract_translation(Path(args.input), args.output)
            console.print(f"[green]✓ {count} translated passages kept: {out}[/green]")
            return 0

        source = args.input
        if args.kobo is not None:
            source = str(kobo.choose_book(args.kobo or None))
        if not source:
            parser.error("give a file or a URL to translate")
        if not is_url(source):
            path = Path(source)
            if not path.is_file():
                parser.error(f"file not found: {source}")
            if path.suffix.lower() not in FORMATS:
                parser.error(f"unsupported file type '{path.suffix}' (supported: {', '.join(sorted(FORMATS))})")

        language = Language(args.language)
        options = _document_options(args, parser, language)
        prompt = load_prompt(args.prompt)
        extra_body = json.loads(args.extra_body) if args.extra_body else None
        if extra_body is not None and not isinstance(extra_body, dict):
            parser.error("--extra-body must be a JSON object")

        translator = build_translator(
            args.service,
            language,
            models=args.model,
            api_key=args.api_key,
            provider=args.provider,
            api_base=args.api_base,
            azure_deployment=args.azure_deployment,
            temperature=args.temperature,
            source_lang=args.source_lang,
            interval=args.interval,
            extra_body=extra_body,
            prompt=prompt,
        )
        if args.model and hasattr(translator, "check_models"):
            translator.check_models()
        console.print(f"[cyan]{translator.describe()} → {language.name}[/cyan]")

        settings = "|".join([args.provider or args.service, ",".join(translator.config.models),
                             language.code, repr(prompt), str(args.source_lang)])
        cache, batch_info = _cache_for(source, settings)
        if args.fresh:
            cache.clear()
        elif len(cache):
            console.print(f"[dim]{len(cache)} translations saved by an earlier run will be reused[/dim]")
        engine = Engine(translator, cache, EngineOptions(
            workers=max(1, args.workers),
            block_size=max(1, args.block_size),
            token_budget=max(0, args.accumulated_tokens),
            context_limit=max(0, args.context or 0),
            test_limit=args.test,
            verbose=args.verbose,
            retranslate=(args.retranslate[0], args.retranslate[1] if len(args.retranslate) > 1 else None,
                         args.chapter) if args.retranslate else None,
        ))

        if args.batch_fetch:
            batch.fetch(translator, batch_info, cache)
        if args.batch_submit:
            engine.options.collect = []
            try:
                _run_document(source, engine, options, args)
            except Collected:
                pass
            batch.submit(translator, engine.options.collect, batch_info, Path(source).name)
            return 0

        outputs = _run_document(source, engine, options, args)
        for out in outputs:
            console.print(f"[green]✓ {out}[/green]")
        return 0

    except Stopped as e:
        for out in e.outputs:
            console.print(f"[yellow]Partial result: {out}[/yellow]")
        if isinstance(e.cause, KeyboardInterrupt):
            console.print("[yellow]Interrupted. Run the same command again to continue where it stopped.[/yellow]")
            return 130
        console.print(f"[red]✗ {e.cause}[/red]")
        console.print("[yellow]Translations done so far are saved: run the same command again to continue.[/yellow]")
        return 1
    except KeyboardInterrupt:
        console.print("[yellow]Interrupted.[/yellow]")
        return 130
    except (TranslationError, FatalTranslationError, PromptError, EpubError, WebError, RangeNotFound,
            kobo.KoboError, batch.BatchNotReady, json.JSONDecodeError, OSError) as e:
        console.print(f"[red]✗ {e}[/red]")
        return 1
    except Exception as e:  # unreadable or unexpected input (corrupt PDF, database...)
        if args.verbose:
            console.print_exception()
        console.print(f"[red]✗ {type(e).__name__}: {e}[/red]")
        return 1


if __name__ == "__main__":
    sys.exit(main())
