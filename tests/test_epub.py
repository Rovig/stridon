import re
import zipfile

import pymupdf
import pytest
from lxml import etree

from stridon.formats.common import DocumentOptions, Stopped
from stridon.formats.epub import extract_translation, retranslate_epub, translate_epub
from stridon.formats.epub_archive import EpubArchive, EpubError
from tests.conftest import FakeTranslator, make_engine

CHAPTER = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>Chapter</title></head>
<body>
  <h1 id="title">The Hive</h1>
  <div class="text">
    <p id="p1">Bees live in colonies.<sup>1</sup></p>
    <p>Use <code>hive.open()</code>&nbsp;carefully. Then wait.</p>
    <p>42</p>
  </div>
  <figure><img src="../images/bee.jpg" alt="A bee on a flower"/><figcaption>Figure 1: A worker bee</figcaption></figure>
  <p>Stay calm. Bees know.</p>
  <p>Roses are red,<br/>violets are blue <a id="r1" href="notes.xhtml#n1"><sup>2</sup></a></p>
  <p>Last paragraph.</p>
</body>
</html>"""

OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">x</dc:identifier>
  <dc:title>Bees</dc:title><dc:language>en</dc:language></metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="c1" href="text/ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="text/ch2.xhtml" media-type="application/xhtml+xml"/>
    <item id="img" href="images/bee.jpg" media-type="image/jpeg"/>
  </manifest>
  <spine><itemref idref="nav"/><itemref idref="c1"/><itemref idref="c2"/></spine>
</package>"""


def _jpeg() -> bytes:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 300, 200), False)
    for x in range(0, 300, 5):
        pixmap.set_rect(pymupdf.IRect(x, 0, x + 3, 200), (x % 256, 80, 160))
    return pixmap.tobytes("jpeg", jpg_quality=100)


@pytest.fixture
def book(tmp_path):
    path = tmp_path / "bees.epub"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf", OPF)
        z.writestr("OEBPS/nav.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><body><nav><ol><li>Contents</li></ol></nav></body></html>')
        z.writestr("OEBPS/text/ch1.xhtml", CHAPTER)
        z.writestr("OEBPS/text/ch2.xhtml", CHAPTER.replace("The Hive", "Second").replace("Bees live", "Wasps live"))
        z.writestr("OEBPS/images/bee.jpg", _jpeg())
    return path


def _chapter(path, name="OEBPS/text/ch1.xhtml") -> str:
    with zipfile.ZipFile(path) as z:
        return z.read(name).decode()


def _options(**kwargs):
    return DocumentOptions(tags="p,h1,h2,li".split(","), **kwargs)


def test_bilingual_epub(book, fake):
    out = translate_epub(book, make_engine(fake), _options())[0]
    html = _chapter(out)
    assert out.name == "bees_bilingual.epub"
    assert "FR: Bees live in colonies." in html  # footnote marker not sent
    assert "FR: Use carefully. Then wait." in html  # code not sent
    assert "FR: 42" not in html  # numbers are skipped
    assert "FR: Figure 1: A worker bee" in html  # captions translated
    assert 'alt="FR: A bee on a flower"' in html
    assert html.count('id="p1"') == 1  # ids are not duplicated
    assert 'class="stridon-translation"' in html
    assert " " in html or "&#160;" in html  # &nbsp; survived
    with zipfile.ZipFile(out) as z:
        assert z.namelist()[0] == "mimetype"
        assert z.read("OEBPS/nav.xhtml") == zipfile.ZipFile(book).read("OEBPS/nav.xhtml")
    assert "Contents" not in fake.calls  # navigation is not translated
    EpubArchive(out)  # still a readable EPUB
    etree.fromstring(html.encode())  # chapters stay well-formed XHTML


def test_only_translation_keeps_excluded_tags(book, fake):
    html = _chapter(translate_epub(book, make_engine(fake), _options(only_translation=True))[0])
    assert '<p id="p1">Bees live' not in html
    assert '<p id="p1">FR: Bees live in colonies.<sup>1</sup></p>' in html


def test_style_skip_files_and_no_captions(book, fake):
    out = translate_epub(book, make_engine(fake), _options(style="color: red;", skip_files=["ch2.xhtml"], captions=False))[0]
    html = _chapter(out)
    assert 'style="color: red;"' in html and "FR: Figure 1" not in html and "FR: A bee" not in html
    assert "FR:" not in _chapter(out, "OEBPS/text/ch2.xhtml")


def test_sentence_mode(book, fake):
    html = _chapter(translate_epub(book, make_engine(fake), _options(sentence_mode=True))[0])
    assert "Stay calm. " in html and "FR: Stay calm." in html and "FR: Bees know." in html
    assert ["Stay calm.", "Bees know."] in fake.batches
    # paragraphs with inline tags keep them and are translated as a whole
    assert "FR: Use carefully. Then wait." in html and "<code>hive.open()</code>" in html


def test_line_breaks_separate_words(book, fake):
    translate_epub(book, make_engine(fake), _options())
    assert "Roses are red, violets are blue" in fake.calls


def test_only_translation_keeps_anchors(book, fake):
    html = _chapter(translate_epub(book, make_engine(fake), _options(only_translation=True))[0])
    assert '<a href="notes.xhtml#n1" id="r1"><sup>2</sup></a>' in html


def test_malformed_and_latin1_chapters(tmp_path, book, fake):
    with zipfile.ZipFile(book) as z:
        entries = {n: z.read(n) for n in z.namelist()}
    entries["OEBPS/text/ch1.xhtml"] = b"<html><body><p>a < b and more</p><p>Tom & Jerry</p><p>x<br>y</p></body></html>"
    entries["OEBPS/text/ch2.xhtml"] = ('<?xml version="1.0" encoding="iso-8859-1"?><html xmlns="http://www.w3.org/1999/xhtml">'
                                       "<body><p>Un caf\xe9&nbsp;noir</p></body></html>").encode("latin-1")
    broken = tmp_path / "broken.epub"
    with zipfile.ZipFile(broken, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    out = translate_epub(broken, make_engine(fake), _options())[0]
    first, second = _chapter(out), _chapter(out, "OEBPS/text/ch2.xhtml")
    assert "FR: a &lt; b and more" in first and "FR: Tom &amp; Jerry" in first and "FR: x y" in first
    etree.fromstring(first.encode())
    assert "FR: Un café noir" in second and 'encoding="utf-8"' in second


def test_bare_text(tmp_path, book, fake):
    with zipfile.ZipFile(book) as z:
        entries = {n: z.read(n) for n in z.namelist()}
    entries["OEBPS/text/ch1.xhtml"] = CHAPTER.replace("<p>Last paragraph.</p>", "<div>Loose text here</div>").encode()
    bare = tmp_path / "bare.epub"
    with zipfile.ZipFile(bare, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    html = _chapter(translate_epub(bare, make_engine(fake), _options(bare_text=True))[0])
    assert "Loose text here" in html and "FR: Loose text here" in html
    assert "hive.open()" not in " ".join(fake.calls)


def test_test_limit_and_partial_output(book):
    failing = FakeTranslator(fail_after=3)
    with pytest.raises(Stopped) as info:
        translate_epub(book, make_engine(failing), _options())
    partial = info.value.outputs[0]
    assert partial.name == "bees_bilingual_temp.epub"
    assert _chapter(partial).count("stridon-translation") == 3


def test_jpeg_recompression(book, fake):
    out = translate_epub(book, make_engine(fake), _options(image_quality=30))[0]
    original = zipfile.ZipFile(book).read("OEBPS/images/bee.jpg")
    assert len(zipfile.ZipFile(out).read("OEBPS/images/bee.jpg")) < len(original)


def test_extract_translation(book, fake):
    bilingual = translate_epub(book, make_engine(fake), _options())[0]
    out, count = extract_translation(bilingual)
    html = _chapter(out)
    assert out.name == "bees_bilingual_monolingual.epub"
    assert "FR: Bees live in colonies." in html and "<p id=\"p1\">Bees live" not in html
    assert re.search(r'<p[^>]*id="p1"[^>]*>FR: Bees live in colonies.<sup>1</sup>', html)  # anchors survive
    assert 'id="title"' in html and 'id="r1"' in html
    assert "stridon-translation" not in html and count > 3
    with pytest.raises(EpubError):
        extract_translation(book)


def test_retranslate(book, fake):
    bilingual = translate_epub(book, make_engine(fake), _options())[0]
    again = FakeTranslator(prefix="NEW:")
    retranslate_epub(bilingual, make_engine(again), _options(), "Bees live", "Use")
    html = _chapter(bilingual)
    assert again.calls == ["Bees live in colonies.", "Use carefully. Then wait."]
    assert "NEW: Bees live in colonies." in html and "FR: Bees live in colonies." not in html
    assert "FR: Last paragraph." in html  # outside the range: unchanged
    with pytest.raises(EpubError, match="Nowhere"):
        retranslate_epub(bilingual, make_engine(again), _options(), "Bees live", "Nowhere")
