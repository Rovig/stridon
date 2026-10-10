"""EPUB books: translation (translation only or bilingual) and extraction
of the translation from a bilingual book."""

import copy
import re
from pathlib import Path

from bs4 import NavigableString, Tag
from bs4.element import Comment, ProcessingInstruction

from stridon.engine import Engine, Unit
from stridon.formats.common import (
    TRANSLATION_COLOR,
    DocumentOptions,
    finish,
    output_path,
    should_skip,
    translate,
)
from stridon.formats.epub_archive import EpubArchive, EpubError, is_well_formed, parse_document, serialize_document
from stridon.image_utils import recompress_jpeg
from stridon.translators.base import console

MARKER = "stridon-translation"  # class added to every inserted translation
SENTENCE = "stridon-sentence"  # extra class for sentence-mode translations
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'“‘(])")
_SKIP_PARENTS = {"script", "style", "head", "title", "svg", "math", "pre", "code"}


# -- selecting text ------------------------------------------------------------

_BREAKS = {"br", "p", "div", "li", "tr", "td", "th", "dt", "dd", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"}


def _inside(node, tags: set[str], stop: Tag) -> bool:
    parent = node.parent
    while parent is not None and parent is not stop:
        if parent.name in tags:
            return True
        parent = parent.parent
    return False


def _text_without(element: Tag, excluded: set[str]) -> str:
    """Element text without the content of excluded tags (code, footnotes)."""
    parts = []
    for node in element.descendants:
        if isinstance(node, Tag):
            if node.name in _BREAKS and not _inside(node, excluded, element):
                parts.append(" ")
        elif not isinstance(node, (Comment, ProcessingInstruction)) and not _inside(node, excluded, element):
            parts.append(str(node))
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _carried(element: Tag, excluded: set[str]) -> list[Tag]:
    """Copies of the parts of an element that a plain-text translation must
    keep: excluded tags (footnote marks, code), images and anchors."""
    kept = []
    for tag in element.find_all(True):
        if any(tag in k.descendants for k in kept):
            continue
        if tag.name in excluded or tag.name == "img" or (tag.name == "a" and tag.get("id")):
            kept.append(tag)
    return [copy.copy(tag) for tag in kept]


def _selected_elements(soup, tags: set[str]) -> list[Tag]:
    """Innermost elements whose tag is selected, in document order."""
    body = soup.find("body") or soup
    elements = body.find_all(lambda t: t.name in tags)
    return [e for e in elements if not e.find(lambda t: t.name in tags) and not _has_marker(e)]


def _classes(tag: Tag) -> list[str]:
    classes = tag.get("class") or []
    return classes.split() if isinstance(classes, str) else list(classes)


def _has_marker(element: Tag) -> bool:
    return MARKER in _classes(element)


def _split_sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_END.split(text) if s.strip()]


def _bare_strings(soup, tags: set[str], excluded: set[str]) -> list[NavigableString]:
    """Text nodes of the body that are not inside a selected element."""
    body = soup.find("body")
    if body is None:
        return []
    skip = tags | excluded | _SKIP_PARENTS
    found = []
    for string in body.find_all(string=True):
        if isinstance(string, (Comment, ProcessingInstruction)) or not string.strip():
            continue
        if any(p.name in skip or _has_marker(p) for p in string.parents if p.name):
            continue
        found.append(string)
    return found


class _Chapter:
    """What to translate in one chapter and how to put translations back."""

    def __init__(self, path: str, soup, options: DocumentOptions):
        self.path = path
        self.soup = soup
        self.items = []  # (kind, node, unit indexes)
        self.options = options

    def collect(self, units: list[Unit], tags: set[str], excluded: set[str]):
        """Add this chapter's passages to `units` and remember where each one goes."""
        opts = self.options
        for element in _selected_elements(self.soup, tags):
            text = _text_without(element, excluded)
            if should_skip(text):
                continue
            # Sentence mode rebuilds the element, so only plain-text elements use it
            plain = element.find(True) is None
            sentences = _split_sentences(text) if opts.sentence_mode and plain else []
            if len(sentences) > 1:
                start = len(units)
                group = (self.path, start)
                units.extend(Unit(s, scope=self.path, group=group) for s in sentences)
                self.items.append(("sentences", element, list(range(start, len(units))), sentences))
            else:
                units.append(Unit(text, scope=self.path))
                self.items.append(("element", element, [len(units) - 1], None))

        if opts.bare_text:
            for string in _bare_strings(self.soup, tags, excluded):
                text = re.sub(r"\s+", " ", str(string)).strip()
                if not should_skip(text):
                    units.append(Unit(text, scope=self.path))
                    self.items.append(("string", string, [len(units) - 1], None))

        if opts.images and opts.captions:
            for image in self.soup.find_all("img"):
                alt = (image.get("alt") or "").strip()
                if alt and not should_skip(alt):
                    units.append(Unit(alt, scope=self.path))
                    self.items.append(("alt", image, [len(units) - 1], None))

    def apply(self, results: list, excluded: set[str]) -> bool:
        """Insert the available translations; returns True if the chapter changed."""
        changed = False
        for kind, node, indexes, sentences in self.items:
            translations = [results[i] for i in indexes]
            if any(t is None for t in translations):
                continue
            changed = True
            if kind == "element":
                self._insert(node, translations[0], excluded)
            elif kind == "sentences":
                self._insert_sentences(node, sentences, translations)
            elif kind == "string":
                self._insert_string(node, translations[0])
            elif kind == "alt":
                node["alt"] = translations[0]
        return changed

    def _style(self, default: str = "") -> str:
        return self.options.style or default

    def _mark(self, tag: Tag):
        tag["class"] = " ".join([*_classes(tag), MARKER])
        if self._style():
            tag["style"] = self._style()

    def _insert(self, element: Tag, translation: str, excluded: set[str]):
        original = _text_without(element, excluded)
        if re.sub(r"\s", "", original) == re.sub(r"\s", "", translation):
            return
        if not self.options.bilingual:
            kept = _carried(element, excluded)
            element.clear()
            element.append(translation)
            for tag in kept:
                element.append(tag)
            return
        translated = self.soup.new_tag(element.name)
        for name, value in element.attrs.items():
            if name != "id":  # ids must stay unique
                translated[name] = value
        translated.string = translation
        self._mark(translated)
        element.insert_after(translated)

    def _insert_sentences(self, element: Tag, sentences: list[str], translations: list[str]):
        element.clear()
        if not self.options.bilingual:
            element.append(" ".join(translations))
            return
        for sentence, translation in zip(sentences, translations):
            element.append(sentence + " ")
            if translation.strip() != sentence.strip():
                span = self.soup.new_tag("span")
                span.string = translation
                span["class"] = f"{MARKER} {SENTENCE}"
                span["style"] = self._style(f"color: {TRANSLATION_COLOR};")
                element.append(span)
                element.append(" ")

    def _insert_string(self, string: NavigableString, translation: str):
        if not self.options.bilingual:
            string.replace_with(translation)
            return
        span = self.soup.new_tag("span")
        span.string = " " + translation
        span["class"] = MARKER
        if self._style():
            span["style"] = self._style()
        string.insert_after(span)


def _effective_tags(options: DocumentOptions) -> set[str]:
    tags = set(options.tags)
    if options.captions and options.images:
        tags.add("figcaption")
    else:
        tags.discard("figcaption")
    return tags


def _chapter_selected(path: str, options: DocumentOptions) -> bool:
    def matches(names):
        return any(path == n or path.endswith("/" + n) or path.rsplit("/", 1)[-1] == n for n in names)

    if options.only_files:
        return matches(options.only_files)
    return not matches(options.skip_files)


# -- commands ---------------------------------------------------------------------

def translate_epub(source: Path, engine: Engine, options: DocumentOptions) -> list[Path]:
    """Translate an EPUB; returns the output file (translation only, or bilingual)."""
    book = EpubArchive(source)
    tags = _effective_tags(options)
    excluded = set(options.exclude_tags)

    units: list[Unit] = []
    chapters = []
    for path in book.documents:
        if not _chapter_selected(path, options):
            continue
        chapter = _Chapter(path, parse_document(book.entries[path]), options)
        chapter.collect(units, tags, excluded)
        chapters.append(chapter)

    results, error = translate(engine, units)

    replacements = {}
    for chapter in chapters:
        if chapter.apply(results, excluded):
            data = serialize_document(chapter.soup)
            if is_well_formed(data.decode("utf-8")):
                replacements[chapter.path] = data
            else:
                console.print(f"[yellow]{chapter.path}: could not be rebuilt as valid XHTML, left untranslated[/yellow]")
    if options.image_quality < 100:
        for path in book.images():
            if path.lower().endswith((".jpg", ".jpeg")):
                replacements[path] = recompress_jpeg(book.entries[path], options.image_quality)

    out = output_path(source, ".epub", options, partial=error is not None)
    book.save(out, replacements)
    return finish([out], error)


def extract_translation(book_path: Path, output: Path | None = None) -> tuple[Path, int]:
    """Keep only the translations of a bilingual book made by Stridon."""
    book = EpubArchive(book_path)
    replacements, count = {}, 0
    for path in book.documents:
        soup = parse_document(book.entries[path])
        markers = soup.find_all(_has_marker)
        if not markers:
            continue
        # Sentence mode: the paragraph keeps only its translated sentences
        sentence_parents = []
        for marker in markers:
            if SENTENCE in _classes(marker) and marker.parent not in sentence_parents:
                sentence_parents.append(marker.parent)
        for parent in sentence_parents:
            text = " ".join(s.get_text().strip() for s in parent.find_all(_has_marker, recursive=False))
            parent.clear()
            parent.append(text)
            count += 1
        for marker in markers:
            if marker.name == "span":
                if SENTENCE not in _classes(marker):
                    # Bare text: drop the original text node, keep the translation
                    previous = marker.previous_sibling
                    if isinstance(previous, NavigableString):
                        previous.replace_with("")
                    marker.replace_with(marker.get_text().strip())
                    count += 1
                continue
            previous = marker.find_previous_sibling()
            if previous is not None and previous.name == marker.name and not _has_marker(previous):
                # Keep what the plain-text translation lacks: id, images, anchors, notes
                if previous.get("id"):
                    marker["id"] = previous["id"]
                for tag in _carried(previous, {"sup", "code"}):
                    marker.append(tag)
                previous.decompose()
            classes = [c for c in _classes(marker) if c != MARKER]
            if classes:
                marker["class"] = " ".join(classes)
            else:
                del marker["class"]
            if "style" in marker.attrs:
                del marker["style"]
            count += 1
        replacements[path] = serialize_document(soup)
    if not replacements:
        raise EpubError("this book contains no translation made by Stridon with --bilingual")
    out = Path(output) if output else book_path.with_name(f"{book_path.stem}_monolingual.epub")
    book.save(out, replacements)
    return out, count
