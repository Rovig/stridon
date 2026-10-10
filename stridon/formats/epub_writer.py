"""Create simple EPUB 3 books (used for web articles and PDF text)."""

import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from stridon.formats.common import escape

CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

CSS = """body { font-family: serif; line-height: 1.5; }
.source { color: #666; font-style: italic; }
.stridon-translation { color: #1e90ff; }
p.stridon-translation { font-style: italic; }
"""


def chapter_xhtml(title: str, body: str, language: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{escape(language)}">\n'
        f'<head><meta charset="utf-8"/><title>{escape(title)}</title>'
        '<link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
        f"<body>\n{body}\n</body>\n</html>"
    )


def write_epub(path: Path, title: str, language: str, chapters: list[tuple[str, str]],
               authors: list[str] | None = None, identifier: str | None = None):
    """chapters: list of (title, XHTML body fragment)."""
    identifier = identifier or f"urn:uuid:{uuid.uuid4()}"
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    names = [f"chapter_{i:03d}.xhtml" for i in range(len(chapters))]

    manifest = "\n".join(
        f'    <item id="c{i}" href="{name}" media-type="application/xhtml+xml"/>' for i, name in enumerate(names)
    )
    spine = "\n".join(f'    <itemref idref="c{i}"/>' for i in range(len(names)))
    creators = "\n".join(f"    <dc:creator>{escape(a)}</dc:creator>" for a in authors or [])
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="id">{escape(identifier)}</dc:identifier>
    <dc:title>{escape(title)}</dc:title>
    <dc:language>{escape(language)}</dc:language>
{creators}
    <meta property="dcterms:modified">{modified}</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="css" href="style.css" media-type="text/css"/>
{manifest}
  </manifest>
  <spine>
{spine}
  </spine>
</package>"""

    toc = "\n".join(
        f'<li><a href="{name}">{escape(chapter_title)}</a></li>' for name, (chapter_title, _) in zip(names, chapters)
    )
    nav = chapter_xhtml(title, f'<nav epub:type="toc"><h1>{escape(title)}</h1><ol>\n{toc}\n</ol></nav>', language)

    path = Path(path)
    with zipfile.ZipFile(path, "w") as out:
        out.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        out.writestr("META-INF/container.xml", CONTAINER, compress_type=zipfile.ZIP_DEFLATED)
        out.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        out.writestr("OEBPS/nav.xhtml", nav, compress_type=zipfile.ZIP_DEFLATED)
        out.writestr("OEBPS/style.css", CSS, compress_type=zipfile.ZIP_DEFLATED)
        for name, (chapter_title, body) in zip(names, chapters):
            out.writestr(f"OEBPS/{name}", chapter_xhtml(chapter_title, body, language), compress_type=zipfile.ZIP_DEFLATED)
