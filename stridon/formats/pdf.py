"""PDF documents.

enhanced (default): rebuilds a bilingual PDF page by page, with the
images at their place; any script (CJK, Arabic, ...) is rendered with a
suitable built-in font.
simple: extracts the text and writes a bilingual .txt and .epub.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from stridon.engine import Engine, Unit
from stridon.formats.common import (
    TRANSLATION_COLOR,
    DocumentOptions,
    escape,
    finish,
    output_path,
    should_skip,
    translate,
)
from stridon.formats.epub_writer import write_epub
from stridon.formats.text import split_text
from stridon.image_utils import recompress_jpeg
from stridon.translators.base import console

MARGIN = 50  # points
SPACING = 6  # points after each block
MAX_FONT_SIZE = 11  # bilingual pages hold twice the text
MAX_FLOW_FONT_SIZE = 20  # translation only: keeps headings larger than body text

_CAPTION_LABEL = re.compile(
    r"^(Figure|Fig\.|Table|Image|Illustration|Diagram|Chart|Graph|Photo|Tableau|Figura|Tabla|Imagen|"
    r"Abbildung|Abb\.|Tabelle|Bild)\s+\d", re.I,
)
_CREDIT = re.compile(r"^(Source:|Credit:|Image (source|credit):|\(Source:|Photo by|©)", re.I)
_DESCRIPTIVE = ("shows", "depicts", "illustrates", "demonstrates", "displays", "presents", "represents", "compares")


def looks_like_caption(text: str) -> bool:
    """Whether a text block looks like a figure caption or an image credit."""
    if _CAPTION_LABEL.match(text) or _CREDIT.match(text):
        return True
    return len(text) < 200 and any(word in text.lower() for word in _DESCRIPTIVE)


@dataclass
class Block:
    """A text block or an image of a source page, in reading order."""

    page: int
    y: float
    kind: str  # "text" or "image"
    text: str = ""
    font_size: float = MAX_FONT_SIZE
    image: bytes = b""
    width: float = 0  # displayed size in points
    height: float = 0
    align: str = "left"


def _text_blocks(page: pymupdf.Page) -> list[Block]:
    blocks = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        lines, sizes = [], []
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(span.get("text", "") for span in spans).strip()
            if text:
                lines.append(text)
                sizes.extend(span.get("size", MAX_FONT_SIZE) for span in spans)
        if not lines:
            continue
        text = lines[0]
        for line in lines[1:]:
            if text.endswith("-") and line[:1].islower():  # word hyphenated at line end
                text = text[:-1] + line
            else:
                text += " " + line
        blocks.append(Block(page.number, block["bbox"][1], "text", text=text, font_size=max(sizes)))
    return blocks


def _alignment(x: float, width: float, page_width: float) -> str:
    if abs(x + width / 2 - page_width / 2) < page_width * 0.1:
        return "center"
    if x < page_width * 0.3:
        return "left"
    return "right" if x > page_width * 0.7 else "center"


def _image_blocks(doc: pymupdf.Document, page: pymupdf.Page, options: DocumentOptions) -> list[Block]:
    blocks = []
    min_width, min_height = options.min_image_size
    for info in page.get_images(full=True):
        xref = info[0]
        try:
            image = doc.extract_image(xref)
            if image["width"] < min_width or image["height"] < min_height:
                continue
            rects = page.get_image_rects(xref)
            if not rects:
                continue
            rect = rects[0]
            if rect.width <= 0 or rect.height <= 0:
                continue
            data = image["image"]
            if image["ext"] in ("jpg", "jpeg"):
                data = recompress_jpeg(data, options.image_quality)
            blocks.append(Block(
                page.number, rect.y0, "image", image=data, width=rect.width, height=rect.height,
                align=_alignment(rect.x0, rect.width, page.rect.width),
            ))
        except Exception as e:
            console.print(f"[yellow]Image {xref} on page {page.number + 1} skipped: {e}[/yellow]")
    return blocks


class _Layout:
    """Writes blocks top to bottom, adding pages when the space runs out."""

    def __init__(self, out: pymupdf.Document):
        self.out = out
        self.page = None
        self.width = self.height = 0
        self.y = MARGIN

    def new_page(self, width: float, height: float):
        """Start a new output page of the given size."""
        self.width, self.height = width, height
        self.page = self.out.new_page(width=width, height=height)
        self.y = MARGIN

    def text(self, text: str, size: float, color: str, italic: bool = False):
        """Write text at the current position."""
        bottom = self.height - MARGIN
        rect = pymupdf.Rect(MARGIN, self.y, self.width - MARGIN, bottom)
        if rect.height > 0:
            style = f"margin:0;font-family:sans-serif;font-size:{size}pt;color:{color}" + (";font-style:italic" if italic else "")
            spare, _ = self.page.insert_htmlbox(rect, f'<p style="{style}">{escape(text)}</p>', scale_low=1)
            if spare >= 0:
                self.y = bottom - spare + SPACING
                return
        if self.y > MARGIN:  # does not fit here: continue on a new page
            self.new_page(self.width, self.height)
            return self.text(text, size, color, italic)
        words = text.split(" ")  # longer than a whole page: split it
        if len(words) < 2:
            console.print(f"[yellow]Text too long to fit on a page, skipped: {text[:50]}...[/yellow]")
            return
        middle = len(words) // 2
        self.text(" ".join(words[:middle]), size, color, italic)
        self.text(" ".join(words[middle:]), size, color, italic)

    def image(self, block: Block):
        """Draw an image at the current position, scaled down to fit."""
        area_width, area_height = self.width - 2 * MARGIN, self.height - 2 * MARGIN
        scale = min(1.0, area_width / block.width, area_height / block.height)
        width, height = block.width * scale, block.height * scale
        if self.y + height > self.height - MARGIN:
            self.new_page(self.width, self.height)
        x = {"center": MARGIN + (area_width - width) / 2, "right": MARGIN + area_width - width}.get(block.align, MARGIN)
        try:
            self.page.insert_image(pymupdf.Rect(x, self.y, x + width, self.y + height), stream=block.image)
        except Exception as e:
            console.print(f"[yellow]Image could not be inserted: {e}[/yellow]")
        self.y += height + SPACING


def translate_pdf(source: Path, engine: Engine, options: DocumentOptions) -> list[Path]:
    """Translate a PDF; returns the output files (a PDF, or a .txt and .epub in simple mode)."""
    if options.pdf_mode == "simple":
        return _translate_simple(source, engine, options)

    doc = pymupdf.open(source)
    pages = []
    for page in doc:
        blocks = _text_blocks(page)
        if options.images:
            blocks += _image_blocks(doc, page, options)
        blocks.sort(key=lambda b: b.y)  # reading order: top to bottom
        pages.append((page.rect.width, page.rect.height, blocks))

    text_blocks = [b for _, _, blocks in pages for b in blocks if b.kind == "text"]
    # Page numbers, ISBNs, bare links... are kept as they are
    results, error = translate(engine, [Unit("" if should_skip(b.text) else b.text) for b in text_blocks])
    translations = {id(b): t for b, t in zip(text_blocks, results)}

    # In test mode, stop after the last page that has a translation
    last_page = len(pages) - 1
    if engine.options.test_limit is not None:
        translated = [b.page for b in text_blocks if translations[id(b)] is not None]
        last_page = max(translated) if translated else 0

    out = pymupdf.open()
    layout = _Layout(out)
    for width, height, blocks in pages[:last_page + 1]:
        # Bilingual: one output page per source page. Translation only: the
        # text flows from page to page like a reflowed book.
        if options.bilingual or layout.page is None:
            layout.new_page(width, height)
        for block in blocks:
            if block.kind == "image":
                layout.image(block)
                continue
            translation = translations[id(block)]
            italic = options.captions and looks_like_caption(block.text)
            if options.bilingual:
                size = min(block.font_size, MAX_FONT_SIZE)
                layout.text(block.text, size, "#000000")
                if translation is not None:
                    layout.text(translation, size, TRANSLATION_COLOR, italic=italic)
            elif translation is not None:
                layout.text(translation, min(block.font_size, MAX_FLOW_FONT_SIZE), "#000000", italic=italic)
            elif not block.text.replace(" ", "").isdigit():  # source page numbers are dropped
                layout.text(block.text, min(block.font_size, MAX_FLOW_FONT_SIZE), "#000000")

    path = output_path(source, ".pdf", options, partial=error is not None)
    out.save(path, garbage=3, deflate=True)
    out.close()
    doc.close()
    return finish([path], error)


def _translate_simple(source: Path, engine: Engine, options: DocumentOptions) -> list[Path]:
    with pymupdf.open(source) as doc:
        text = "\n".join(page.get_text() for page in doc)
    blocks = split_text(text, options.batch_lines)
    results, error = translate(engine, [Unit(b) for b in blocks])
    partial = error is not None

    parts, html = [], []
    for block, translation in zip(blocks, results):
        if not options.bilingual:
            parts.append(translation if translation is not None else block)
            html.append(f"<p>{escape(translation or block)}</p>")
        else:
            parts.append(block if translation is None else f"{block}\n{translation}")
            html.append(f"<p>{escape(block)}</p>".replace("\n", "<br/>"))
            if translation is not None:
                html.append(f'<p class="stridon-translation">{escape(translation)}</p>'.replace("\n", "<br/>"))

    base = output_path(source, "", options, partial)
    if base.suffix.lower() in (".txt", ".epub", ".pdf"):
        base = base.with_suffix("")
    txt, epub = base.parent / (base.name + ".txt"), base.parent / (base.name + ".epub")
    txt.write_text("\n\n".join(parts) + "\n", encoding="utf-8")
    write_epub(epub, source.stem, engine.translator.language.code, [(source.stem, "\n".join(html))])
    return finish([txt, epub], error)
