import sqlite3
import zipfile

import pymupdf
import pytest
from lxml import etree

from stridon import kobo
from stridon.formats import web
from stridon.formats.common import DocumentOptions
from stridon.formats.pdf import looks_like_caption, translate_pdf
from stridon.formats.text import split_markdown, translate_markdown, translate_srt, translate_txt
from tests.conftest import FakeTranslator, make_engine


# -- TXT / Markdown / SRT ----------------------------------------------------------

def test_txt_paragraphs(tmp_path, fake):
    source = tmp_path / "notes.txt"
    source.write_text("First paragraph\nstill first.\n\nSecond.\n\n\n12\n")
    out = translate_txt(source, make_engine(fake), DocumentOptions(bilingual=True))[0]
    assert out.read_text() == (
        "First paragraph\nstill first.\nFR: First paragraph\nstill first.\n\nSecond.\nFR: Second.\n\n12\n"
    )


def test_txt_without_blank_lines_uses_line_groups(tmp_path, fake):
    source = tmp_path / "lines.txt"
    source.write_text("\n".join(f"line {i}" for i in range(5)))
    translate_txt(source, make_engine(fake), DocumentOptions(batch_lines=2))
    assert fake.calls == ["line 0\nline 1", "line 2\nline 3", "line 4"]


def test_markdown_keeps_code(tmp_path, fake):
    source = tmp_path / "doc.md"
    source.write_text("# Title\nIntro text.\n\n```python\n# not a heading\n\nprint(1)\n```\n\n![img](a.png)\n")
    assert [ok for _, ok in split_markdown(source.read_text())] == [True, True, False, False]
    out = translate_markdown(source, make_engine(fake), DocumentOptions(bilingual=True))[0]
    text = out.read_text()
    assert "# Title\n\nFR: # Title" in text and text.count("print(1)") == 1
    assert fake.calls == ["# Title", "Intro text."]


def test_srt(tmp_path, fake):
    source = tmp_path / "film.srt"
    source.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\nthere\n\n2\n00:00:03,000 --> 00:00:04,000\nBye\n")
    out = translate_srt(source, make_engine(fake), DocumentOptions(bilingual=True))[0]
    assert out.read_text() == (
        "1\n00:00:01,000 --> 00:00:02,000\nHello\nthere\nFR: Hello\nthere\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\nBye\nFR: Bye\n"
    )


# -- PDF ------------------------------------------------------------------------

@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "report.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(50, 50, 545, 200), "A paragraph that wraps over several lines " * 5, fontsize=10)
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 100), False)
    pixmap.set_rect(pixmap.irect, (200, 30, 30))
    page.insert_image(pymupdf.Rect(150, 250, 450, 400), stream=pixmap.tobytes("png"))
    page.insert_text((150, 420), "Figure 1: a red block")
    small = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 10, 10), False)
    page.insert_image(pymupdf.Rect(500, 500, 510, 510), stream=small.tobytes("png"))
    doc.save(path)
    return path


def test_pdf_enhanced(pdf, fake):
    out = translate_pdf(pdf, make_engine(fake), DocumentOptions(bilingual=True, output_suffix="_bilingual"))[0]
    result = pymupdf.open(out)
    text = " ".join(page.get_text() for page in result).replace("\n", " ")
    assert out.name == "report_bilingual.pdf"
    assert "FR: A paragraph that wraps" in text and "FR: Figure 1: a red block" in text
    assert sum(len(page.get_images()) for page in result) == 1  # the 10x10 image is dropped
    assert len(fake.calls) == 2  # one request per block, not per line


def test_pdf_cjk_text_is_rendered(pdf):
    out = translate_pdf(pdf, make_engine(FakeTranslator(prefix="中文翻译")), DocumentOptions())[0]
    assert "中文翻译" in pymupdf.open(out)[0].get_text()


def test_pdf_simple_mode(pdf, fake):
    outputs = translate_pdf(pdf, make_engine(fake), DocumentOptions(pdf_mode="simple", bilingual=True, output_suffix="_bilingual"))
    assert [p.name for p in outputs] == ["report_bilingual.txt", "report_bilingual.epub"]
    assert "FR: A paragraph" in outputs[0].read_text()
    with zipfile.ZipFile(outputs[1]) as z:
        etree.fromstring(z.read("OEBPS/chapter_000.xhtml"))


def test_pdf_translation_flows_across_pages(tmp_path, fake):
    source = tmp_path / "pages.pdf"
    doc = pymupdf.open()
    for number in range(1, 4):
        page = doc.new_page()
        page.insert_text((72, 72), f"Short text of page {number}.")
        page.insert_text((300, 800), str(number))  # page number
    doc.save(source)
    out = translate_pdf(source, make_engine(fake), DocumentOptions(output_suffix="_fr"))[0]
    result = pymupdf.open(out)
    text = result[0].get_text()
    assert out.name == "pages_fr.pdf" and len(result) == 1
    assert text.split("\n")[:3] == [f"FR: Short text of page {n}." for n in (1, 2, 3)]  # no originals
    assert "\n2\n" not in f"\n{text}"


def test_caption_detection():
    assert looks_like_caption("Fig. 2 Bee anatomy") and looks_like_caption("This chart shows sales")
    assert not looks_like_caption("An ordinary sentence about bees.")


# -- web ------------------------------------------------------------------------

ARTICLE = """<html><head><title>Fallback</title><meta property="og:title" content="Bees &amp; Hives"/></head>
<body><nav>Menu</nav><article><h1>Bees &amp; Hives</h1>
<p>Honey bees live in colonies with a single queen.</p><h2>Summary</h2>
<p>Workers forage for nectar &amp; pollen &lt; 5 km away.</p><p>Short</p></article></body></html>"""


def test_web_article(tmp_path, monkeypatch, fake):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(web, "fetch", lambda url, agent: ARTICLE)
    monkeypatch.setattr(web, "EXTRACTORS", [("heuristics", web._with_heuristics)])
    outputs = web.translate_url("https://example.com/a", make_engine(fake),
                                DocumentOptions(output_format="both", bilingual=True, output_suffix="_bilingual"))
    assert [p.name for p in outputs] == ["Bees  Hives_bilingual.pdf", "Bees  Hives_bilingual.epub"]
    assert fake.calls == ["Bees & Hives", "Honey bees live in colonies with a single queen.", "Summary",
                          "Workers forage for nectar & pollen < 5 km away."]
    pdf_text = pymupdf.open(outputs[0])[0].get_text()
    assert "FR: Summary" in pdf_text and "1 / 1" in pdf_text
    with zipfile.ZipFile(outputs[1]) as z:
        chapter = z.read("OEBPS/chapter_000.xhtml")
    etree.fromstring(chapter)
    assert b"&amp; pollen &lt; 5 km" in chapter


def test_web_translation_only(tmp_path, monkeypatch, fake):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(web, "fetch", lambda url, agent: ARTICLE)
    monkeypatch.setattr(web, "EXTRACTORS", [("heuristics", web._with_heuristics)])
    outputs = web.translate_url("https://example.com/a", make_engine(fake),
                                DocumentOptions(output_format="epub", output_suffix="_fr"))
    with zipfile.ZipFile(outputs[0]) as z:
        chapter = z.read("OEBPS/chapter_000.xhtml").decode()
    assert outputs[0].name == "Bees  Hives_fr.epub"
    assert "<h1>FR: Bees &amp; Hives</h1>" in chapter and "<h1>Bees &amp; Hives</h1>" not in chapter
    assert "<p>FR: Honey bees" in chapter and "<p>Honey bees" not in chapter


def test_web_output_option(tmp_path, monkeypatch, fake):
    monkeypatch.setattr(web, "fetch", lambda url, agent: ARTICLE)
    monkeypatch.setattr(web, "EXTRACTORS", [("heuristics", web._with_heuristics)])
    outputs = web.translate_url("https://example.com/a", make_engine(fake),
                                DocumentOptions(output_format="both", output=tmp_path / "report.v2.pdf"))
    assert [p.name for p in outputs] == ["report.v2.pdf", "report.v2.epub"]


def test_cp1252_subtitles(tmp_path, fake):
    source = tmp_path / "old.srt"
    source.write_bytes("1\n00:00:01,000 --> 00:00:02,000\nCaf\xe9 cr\xe8me\n".encode("cp1252"))
    translate_srt(source, make_engine(fake), DocumentOptions())
    assert fake.calls == ["Café crème"]


def test_web_url_check():
    assert web.is_url("https://example.com/x") and not web.is_url("book.epub")


# -- Kobo -----------------------------------------------------------------------

def test_kobo_lists_and_copies_drm_free_books(tmp_path, monkeypatch):
    device = tmp_path / "KOBOeReader"
    (device / ".kobo" / "kepub").mkdir(parents=True)
    (device / "books").mkdir()
    db = sqlite3.connect(device / ".kobo" / "KoboReader.sqlite")
    db.execute("CREATE TABLE content (ContentID TEXT, Title TEXT, Attribution TEXT, ContentType INT)")
    db.execute("CREATE TABLE content_keys (volumeid TEXT, elementid TEXT, elementkey TEXT)")
    db.executemany("INSERT INTO content VALUES (?, ?, ?, 6)", [
        ("file:///mnt/onboard/books/free.epub", "Free Book", "Ann"),
        ("kepub-1", "Locked Book", "Bob"),
    ])
    db.execute("INSERT INTO content_keys VALUES ('kepub-1', 'x', 'y')")
    db.commit()
    db.close()
    (device / "books" / "free.epub").write_bytes(b"epub")
    (device / ".kobo" / "kepub" / "kepub-1").write_bytes(b"locked")

    books = kobo.list_books(str(device))
    assert [(b.title, b.protected) for b in books] == [("Free Book", False), ("Locked Book", True)]

    monkeypatch.setattr("builtins.input", lambda prompt: "1")
    copied = kobo.choose_book(str(device), destination=tmp_path)
    assert copied.read_bytes() == b"epub"
    monkeypatch.setattr("builtins.input", lambda prompt: "2")
    with pytest.raises(kobo.KoboError, match="DRM"):
        kobo.choose_book(str(device), destination=tmp_path)
