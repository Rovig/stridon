"""Web articles: fetch a page, extract the article, write a bilingual
PDF and/or EPUB."""

from pathlib import Path
from urllib.parse import urlparse

import pymupdf
import requests
from bs4 import BeautifulSoup

from stridon.engine import Engine, Unit
from stridon.formats.common import (
    TRANSLATION_COLOR,
    DocumentOptions,
    escape,
    finish,
    safe_filename,
    translate,
)
from stridon.formats.epub_writer import write_epub
from stridon.translators.base import console

TEXT_TAGS = ["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote"]


class WebError(Exception):
    pass


def is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def fetch(url: str, user_agent: str) -> str:
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
    except requests.Timeout as e:
        raise WebError(f"timeout while fetching {url}") from e
    except requests.RequestException as e:
        raise WebError(f"cannot fetch {url}: {e}") from e
    if "html" not in response.headers.get("content-type", "html").lower():
        console.print(f"[yellow]Warning: the page is {response.headers.get('content-type')}, not HTML[/yellow]")
    return response.text


def page_title(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    og = soup.find("meta", property="og:title")
    if og and og.get("content", "").strip():
        return og["content"].strip()
    if soup.title and soup.title.get_text().strip():
        return soup.title.get_text().strip()
    return "Untitled article"


def _with_trafilatura(html: str, url: str):
    import trafilatura

    return trafilatura.extract(html, url=url, output_format="html", include_tables=True,
                               include_comments=False, include_images=False)


def _with_newspaper(html: str, url: str):
    from newspaper import Article

    article = Article(url)
    article.set_html(html)
    article.parse()
    if not article.text:
        return None
    return "".join(f"<p>{escape(p.strip())}</p>" for p in article.text.split("\n\n") if p.strip())


def _with_readability(html: str, url: str):
    from readability import Document

    return Document(html).summary()


def _with_heuristics(html: str, url: str):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
        tag.decompose()
    container = None
    for selector in ["article", "main", '[role="main"]', ".article-content", ".post-content"]:
        container = soup.select_one(selector)
        if container:
            break
    if container is None:
        candidates = soup.find_all(["div", "section"])
        container = max(candidates, key=lambda c: len(c.get_text()), default=None)
    return str(container) if container else None


EXTRACTORS = [
    ("trafilatura", _with_trafilatura),
    ("newspaper3k", _with_newspaper),
    ("readability", _with_readability),
    ("heuristics", _with_heuristics),
]


def extract_article(html: str, url: str) -> list[tuple[str, str]]:
    """Paragraphs of the article as (tag, text), from the first extractor
    that finds any."""
    for name, extractor in EXTRACTORS:
        try:
            content = extractor(html, url)
        except ImportError:
            continue
        except Exception as e:
            console.print(f"[yellow]{name} extraction failed: {e}[/yellow]")
            continue
        if not content:
            continue
        soup = BeautifulSoup(content, "html.parser")
        paragraphs = []
        for element in soup.find_all(TEXT_TAGS):
            if element.find(TEXT_TAGS):  # keep innermost elements only
                continue
            text = " ".join(element.get_text(" ").split())
            if text and (element.name != "p" or len(text) > 10):
                paragraphs.append((element.name, text))
        if paragraphs:
            console.print(f"[green]Article extracted with {name}: {len(paragraphs)} paragraphs[/green]")
            return paragraphs
    raise WebError("no article found on this page (it may be behind a paywall or need JavaScript)")


# -- outputs ---------------------------------------------------------------------

PDF_CSS = f"""
body {{ font-family: serif; font-size: 11pt; line-height: 1.5; color: #333; }}
h1 {{ font-size: 22pt; color: #1a1a1a; margin: 0 0 6pt 0; }}
h2 {{ font-size: 17pt; }} h3 {{ font-size: 14pt; }} h4, h5, h6 {{ font-size: 12pt; }}
.source {{ color: #666; font-style: italic; font-size: 9pt; margin-bottom: 14pt; }}
.original {{ color: #2a2a2a; margin: 8pt 0 3pt 0; }}
.translation {{ color: {TRANSLATION_COLOR}; font-style: italic; margin: 0 0 8pt 0; padding-left: 8pt; }}
h1.translation, h2.translation, h3.translation, h4.translation, h5.translation, h6.translation {{ font-style: normal; }}
"""


def _article_html(title, url, paragraphs, results, only_translation) -> str:
    parts = [f"<h1>{escape(title)}</h1>", f'<p class="source">Source: {escape(url)}</p>']
    for (tag, text), translation in zip(paragraphs, results):
        tag = "p" if tag in ("li", "blockquote") else tag
        if translation is None:
            parts.append(f'<{tag} class="original">{escape(text)}</{tag}>')
            continue
        if not only_translation:
            parts.append(f'<{tag} class="original">{escape(text)}</{tag}>')
        parts.append(f'<{tag} class="translation">{escape(translation)}</{tag}>')
    return "\n".join(parts)


def write_pdf(path: Path, title: str, body_html: str):
    """Lay out HTML on A4 pages, then add the title and page numbers."""
    page_rect = pymupdf.paper_rect("a4")
    area = page_rect + (57, 71, -57, -71)  # 2 cm sides, 2.5 cm top and bottom
    story = pymupdf.Story(html=f"<body>{body_html}</body>", user_css=PDF_CSS)
    writer = pymupdf.DocumentWriter(str(path))
    more = True
    while more:
        device = writer.begin_page(page_rect)
        more, _ = story.place(area)
        story.draw(device)
        writer.end_page()
    writer.close()

    doc = pymupdf.open(path)
    total = len(doc)
    for number, page in enumerate(doc, start=1):
        header = pymupdf.Rect(57, 30, page_rect.width - 57, 60)
        page.insert_htmlbox(header, f'<p style="text-align:center;font-size:9pt;color:#666">{escape(title)}</p>')
        footer = pymupdf.Rect(57, page_rect.height - 50, page_rect.width - 57, page_rect.height - 25)
        page.insert_htmlbox(footer, f'<p style="text-align:center;font-size:9pt;color:#666">{number} / {total}</p>')
    doc.saveIncr()
    doc.close()


def _epub_body(title, url, paragraphs, results, only_translation) -> str:
    parts = [f"<h1>{escape(title)}</h1>", f'<p class="source">Source: <a href="{escape(url)}">{escape(url)}</a></p>', "<hr/>"]
    for (tag, text), translation in zip(paragraphs, results):
        tag = "p" if tag in ("li", "blockquote") else tag
        if translation is None or not only_translation:
            parts.append(f"<{tag}>{escape(text)}</{tag}>")
        if translation is not None:
            parts.append(f'<{tag} class="stridon-translation">{escape(translation)}</{tag}>')
    return "\n".join(parts)


def translate_url(url: str, engine: Engine, options: DocumentOptions) -> list[Path]:
    html = fetch(url, options.user_agent)
    title = page_title(html)
    paragraphs = extract_article(html, url)
    results, error = translate(engine, [Unit(text) for _, text in paragraphs])

    suffix = "_bilingual_temp" if error is not None else "_bilingual"
    if options.output:
        output = Path(options.output)
        stem = output.stem if output.suffix.lower() in (".pdf", ".epub") else output.name
        base = output.parent / (stem + ("_temp" if error is not None else ""))
    else:
        base = Path.cwd() / f"{safe_filename(title, 'article')}{suffix}"

    outputs = []
    if options.output_format in ("pdf", "both"):
        pdf = base.parent / (base.name + ".pdf")
        write_pdf(pdf, title, _article_html(title, url, paragraphs, results, options.only_translation))
        outputs.append(pdf)
    if options.output_format in ("epub", "both"):
        epub = base.parent / (base.name + ".epub")
        write_epub(epub, title, engine.translator.language.code,
                   [(title, _epub_body(title, url, paragraphs, results, options.only_translation))],
                   identifier=url)
        outputs.append(epub)
    return finish(outputs, error)
