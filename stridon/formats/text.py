"""Plain text, Markdown and SRT subtitles."""

import re
from pathlib import Path

from stridon.engine import Engine, Unit
from stridon.formats.common import DocumentOptions, finish, output_path, should_skip, translate


def _read(path: Path) -> str:
    data = path.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")  # common for older subtitles and text files
    return text.replace("\r\n", "\n")


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


# -- TXT ----------------------------------------------------------------------

def split_text(text: str, batch_lines: int) -> list[str]:
    """Paragraphs separated by blank lines; a file without blank lines is
    cut into groups of `batch_lines` lines."""
    if re.search(r"\n\s*\n", text.strip()):
        return [b.strip("\n") for b in re.split(r"\n\s*\n", text) if b.strip()]
    lines = [line for line in text.split("\n") if line.strip()]
    size = max(1, batch_lines)
    return ["\n".join(lines[i:i + size]) for i in range(0, len(lines), size)]


def translate_txt(source: Path, engine: Engine, options: DocumentOptions) -> list[Path]:
    blocks = split_text(_read(source), options.batch_lines)
    units = [Unit("" if should_skip(b) else b) for b in blocks]
    results, error = translate(engine, units)

    parts = []
    for block, translation in zip(blocks, results):
        if not options.bilingual:
            parts.append(translation if translation is not None else block)
        else:
            parts.append(block if translation is None else f"{block}\n{translation}")
    out = output_path(source, ".txt", options, partial=error is not None)
    return finish([_write(out, "\n\n".join(parts) + "\n")], error)


# -- Markdown -----------------------------------------------------------------

_FENCE = re.compile(r"^\s*(```|~~~)")
_IMAGE_ONLY = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$")


def split_markdown(text: str) -> list[tuple[str, bool]]:
    """Blocks of a Markdown document as (text, translatable). Code fences
    are kept whole and never translated; headings are their own block."""
    blocks, current, fence = [], [], None

    def flush():
        if current:
            blocks.append(("\n".join(current), True))
            current.clear()

    for line in text.split("\n"):
        if fence:
            current.append(line)
            if line.strip().startswith(fence):
                blocks.append(("\n".join(current), False))
                current.clear()
                fence = None
            continue
        match = _FENCE.match(line)
        if match:
            flush()
            fence = match.group(1)
            current.append(line)
        elif not line.strip():
            flush()
        elif line.lstrip().startswith("#"):
            flush()
            blocks.append((line, True))
        else:
            current.append(line)
    if fence:
        blocks.append(("\n".join(current), False))
    else:
        flush()
    return [(b, ok and not should_skip(b) and not _IMAGE_ONLY.match(b) and not b.startswith("<!--")) for b, ok in blocks]


def translate_markdown(source: Path, engine: Engine, options: DocumentOptions) -> list[Path]:
    blocks = split_markdown(_read(source))
    units = [Unit(text if ok else "") for text, ok in blocks]
    results, error = translate(engine, units)

    parts = []
    for (block, _), translation in zip(blocks, results):
        if translation is None:
            parts.append(block)
        elif not options.bilingual:
            parts.append(translation)
        else:
            parts.extend([block, translation])
    out = output_path(source, ".md", options, partial=error is not None)
    return finish([_write(out, "\n\n".join(parts) + "\n")], error)


# -- SRT ----------------------------------------------------------------------

def parse_srt(text: str) -> list[tuple[str, str, str]]:
    """Cues as (number, timing line, text)."""
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.strip("\n").split("\n")
        if len(lines) >= 2 and "-->" in lines[1]:
            cues.append((lines[0].strip(), lines[1].strip(), "\n".join(lines[2:]).strip()))
        elif len(lines) >= 1 and "-->" in lines[0]:  # cue without a number
            cues.append((str(len(cues) + 1), lines[0].strip(), "\n".join(lines[1:]).strip()))
    return cues


def translate_srt(source: Path, engine: Engine, options: DocumentOptions) -> list[Path]:
    cues = parse_srt(_read(source))
    units = [Unit("" if should_skip(text) else text) for _, _, text in cues]
    results, error = translate(engine, units)

    blocks = []
    for (number, timing, text), translation in zip(cues, results):
        if translation is None:
            content = text
        elif not options.bilingual:
            content = translation
        else:
            content = f"{text}\n{translation}"
        blocks.append(f"{number}\n{timing}\n{content}".rstrip("\n"))
    out = output_path(source, ".srt", options, partial=error is not None)
    return finish([_write(out, "\n\n".join(blocks) + "\n")], error)
