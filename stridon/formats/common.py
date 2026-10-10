"""Helpers shared by the document formats."""

import html
import re
from dataclasses import dataclass, field
from pathlib import Path

from stridon.engine import Engine, Interrupted, Unit

TRANSLATION_COLOR = "#1e90ff"


@dataclass
class DocumentOptions:
    output: Path | None = None
    bilingual: bool = False  # True: original and translation alternate
    output_suffix: str = "_translated"  # e.g. "_fr", or "_bilingual"
    style: str = ""  # CSS for translated paragraphs (bilingual EPUB)
    # EPUB
    tags: list[str] = field(default_factory=lambda: ["p"])
    exclude_tags: list[str] = field(default_factory=lambda: ["sup", "code"])
    bare_text: bool = False
    only_files: list[str] = field(default_factory=list)
    skip_files: list[str] = field(default_factory=list)
    sentence_mode: bool = False
    # images
    images: bool = True
    captions: bool = True
    image_quality: int = 100
    min_image_size: tuple[int, int] = (50, 50)
    # PDF / text
    pdf_mode: str = "enhanced"
    batch_lines: int = 10
    # web
    output_format: str = "pdf"
    user_agent: str = "Mozilla/5.0 (compatible; Stridon/1.0)"


class Stopped(Exception):
    """Translation stopped before the end; partial files were written."""

    def __init__(self, cause: BaseException, outputs: list[Path]):
        super().__init__(str(cause))
        self.cause = cause
        self.outputs = outputs


def translate(engine: Engine, units: list[Unit]) -> tuple[list, BaseException | None]:
    """Run the engine and return (results, error) instead of raising."""
    try:
        return engine.run(units), None
    except Interrupted as e:
        return e.results, e.cause


def finish(outputs: list[Path], error: BaseException | None) -> list[Path]:
    if error is not None:
        raise Stopped(error, outputs)
    return outputs


def output_path(source: Path, extension: str, options: DocumentOptions, partial: bool) -> Path:
    """<dir>/<stem>_<lang><ext> (or _bilingual), or --output; '_temp' is added when partial."""
    if options.output:
        path = Path(options.output)
        return path.with_name(f"{path.stem}_temp{path.suffix}") if partial else path
    suffix = options.output_suffix + ("_temp" if partial else "")
    return source.with_name(f"{source.stem}{suffix}{extension}")


def safe_filename(title: str, fallback: str = "document") -> str:
    name = re.sub(r"[^\w\s-]", "", title, flags=re.UNICODE).strip()[:50].strip()
    return name or fallback


def escape(text: str) -> str:
    return html.escape(text or "", quote=True)


_URL_START = re.compile(r"^(https?://|www\.)\S+")
_ONLY_PUNCTUATION = re.compile(r"^[\s!-/:-@\[-`{-~]+$")
_LABEL = re.compile(r"^(Listing|Figure)\s*\d+", re.I)
_ISBN = re.compile(r"^[Ee]?ISBN[\s:]*[\d\s-]+X?$")


def should_skip(text: str) -> bool:
    """Text that is never sent for translation: numbers, punctuation,
    bare links, short figure/listing labels, ISBNs."""
    stripped = text.strip()
    if not stripped or stripped.replace(" ", "").isdigit():
        return True
    if _ONLY_PUNCTUATION.match(stripped) or _ISBN.match(stripped):
        return True
    if _URL_START.match(stripped) and len(stripped.split()) == 1:
        return True
    if len(stripped) < 80 and (_LABEL.match(stripped) and len(stripped.split()) <= 2):
        return True
    return stripped.startswith("Source: http")
