"""Read and write EPUB files at the archive level.

Only the chapters that are translated are rewritten; every other entry
(metadata, navigation, styles, fonts, images) is copied byte for byte.
"""

import html.entities
import posixpath
import re
import zipfile
from pathlib import Path

from bs4 import BeautifulSoup
from lxml import etree

DOCUMENT_TYPES = {"application/xhtml+xml", "text/html"}
_XML_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}
_ENTITY = re.compile(r"&([A-Za-z][A-Za-z0-9]*);")


class EpubError(Exception):
    """The file is not a usable EPUB, or an EPUB operation failed."""


class EpubArchive:
    """The entries of an EPUB file, its package (manifest, spine) and chapters in reading order."""

    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            with zipfile.ZipFile(self.path) as archive:
                self.names = archive.namelist()
                self.entries = {name: archive.read(name) for name in self.names}
        except zipfile.BadZipFile as e:
            raise EpubError(f"{path} is not a valid EPUB file") from e
        self._read_package()

    def _read_package(self):
        container = self.entries.get("META-INF/container.xml")
        if container is None:
            raise EpubError("META-INF/container.xml is missing")
        rootfile = BeautifulSoup(container, "xml").find("rootfile")
        if rootfile is None or not rootfile.get("full-path"):
            raise EpubError("the EPUB container does not name its package file")
        self.opf_path = rootfile["full-path"]
        if self.opf_path not in self.entries:
            raise EpubError(f"the package file {self.opf_path} is missing")
        opf = BeautifulSoup(self.entries[self.opf_path], "xml")
        base = posixpath.dirname(self.opf_path)

        self.manifest = {}  # id -> (path, media type, properties)
        for item in opf.find_all("item"):
            href = item.get("href", "")
            path = posixpath.normpath(posixpath.join(base, _unquote(href)))
            self.manifest[item.get("id")] = (path, item.get("media-type", ""), item.get("properties", ""))

        self.documents = []  # chapter paths in reading order
        for itemref in opf.find_all("itemref"):
            entry = self.manifest.get(itemref.get("idref"))
            if entry and entry[1] in DOCUMENT_TYPES and "nav" not in entry[2].split():
                if entry[0] in self.entries and entry[0] not in self.documents:
                    self.documents.append(entry[0])

        title = opf.find("title")
        self.title = title.get_text(strip=True) if title else self.path.stem

    def images(self) -> list[str]:
        """Paths of the image entries listed in the manifest."""
        return [path for path, media, _ in self.manifest.values() if media.startswith("image/") and path in self.entries]

    def save(self, path: Path, replacements: dict[str, bytes]):
        """Write a copy of the book with some entries replaced."""
        path = Path(path)
        tmp = path.with_name(path.name + ".part")
        with zipfile.ZipFile(tmp, "w") as out:
            if "mimetype" in self.entries:
                out.writestr(zipfile.ZipInfo("mimetype"), self.entries["mimetype"], compress_type=zipfile.ZIP_STORED)
            for name in self.names:
                if name == "mimetype":
                    continue
                data = replacements.get(name, self.entries[name])
                out.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
        tmp.replace(path)


def _unquote(href: str) -> str:
    from urllib.parse import unquote

    return unquote(href.split("#")[0])


_DECLARED = re.compile(rb"""^\s*<\?xml[^>]*encoding=["']([A-Za-z0-9._-]+)["']""")
_DECLARATION = re.compile(r"^\s*<\?xml[^>]*\?>")


def _decode(data: bytes) -> str:
    """Decode a chapter using its BOM or XML declaration (default UTF-8)."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    match = _DECLARED.match(data[:200])
    encoding = match.group(1).decode() if match else "utf-8"
    try:
        return data.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return data.decode("utf-8", errors="replace")


def is_well_formed(text: str) -> bool:
    """Whether the text parses as strict XML."""
    try:
        etree.fromstring(_DECLARATION.sub("", text, count=1).encode("utf-8"), etree.XMLParser(recover=False))
        return True
    except etree.XMLSyntaxError:
        return False


def parse_document(data: bytes) -> BeautifulSoup:
    """Parse an XHTML chapter. Well-formed chapters are read as XML so they
    are written back unchanged apart from the translations; others are
    read as HTML, which keeps all their text."""
    text = _decode(data)

    def numeric(match):
        name = match.group(1)
        if name in _XML_ENTITIES or name not in html.entities.name2codepoint:
            return match.group(0)
        return f"&#{html.entities.name2codepoint[name]};"

    text = _ENTITY.sub(numeric, text)
    if is_well_formed(text):
        return BeautifulSoup(text, "xml")
    return BeautifulSoup(text, "html.parser")


def serialize_document(soup: BeautifulSoup) -> bytes:
    """The chapter as UTF-8 bytes, with its XML declaration set to UTF-8."""
    text = str(soup)
    # The chapter is always written as UTF-8
    text = re.sub(r"""^(\s*<\?xml[^>]*encoding=["'])[^"']+(["'])""", r"\g<1>utf-8\g<2>", text, count=1)
    return text.encode("utf-8")
