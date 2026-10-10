from types import SimpleNamespace

import pytest

from stridon import cli
from tests.conftest import FakeTranslator


@pytest.fixture
def patched(monkeypatch):
    """Make the CLI use fake translators."""
    state = SimpleNamespace(created=[], fail_after=None)

    def build(service, language, **kwargs):
        translator = FakeTranslator(fail_after=state.fail_after)
        translator.language = language
        translator.config.models = [kwargs.get("models") or "m"]
        state.created.append(translator)
        return translator

    monkeypatch.setattr(cli, "build_translator", build)
    return state


def test_cli_translates_and_resumes(tmp_path, patched):
    source = tmp_path / "story.txt"
    source.write_text("One.\n\nTwo.\n\nThree.\n")

    patched.fail_after = 2
    assert cli.main([str(source), "-l", "fr"]) == 1
    assert (tmp_path / "story_fr_temp.txt").exists()

    patched.fail_after = None
    assert cli.main([str(source), "-l", "fr"]) == 0
    assert patched.created[-1].calls == ["Three."]  # the first two came from the cache
    assert (tmp_path / "story_fr.txt").read_text() == "FR: One.\n\nFR: Two.\n\nFR: Three.\n"

    assert cli.main([str(source), "-l", "fr", "--fresh"]) == 0
    assert patched.created[-1].calls == ["One.", "Two.", "Three."]


def test_cli_language_change_uses_new_cache(tmp_path, patched):
    source = tmp_path / "s.txt"
    source.write_text("Hello.\n")
    cli.main([str(source), "-l", "fr"])
    cli.main([str(source), "-l", "de"])
    assert patched.created[-1].calls == ["Hello."]


def test_cli_errors(tmp_path, patched, capsys):
    with pytest.raises(SystemExit):
        cli.main([str(tmp_path / "missing.epub")])
    (tmp_path / "a.docx").write_text("x")
    with pytest.raises(SystemExit):
        cli.main([str(tmp_path / "a.docx")])
    (tmp_path / "a.txt").write_text("x")
    with pytest.raises(SystemExit):
        cli.main([str(tmp_path / "a.txt"), "--image-quality", "0"])
    assert cli.main([str(tmp_path / "a.txt"), "--prompt", "no placeholder"]) == 1


def test_cli_test_option_and_output(tmp_path, patched):
    source = tmp_path / "s.txt"
    source.write_text("A.\n\nB.\n\nC.\n")
    out = tmp_path / "result.txt"
    assert cli.main([str(source), "--test", "1", "-o", str(out)]) == 0
    assert patched.created[-1].calls == ["A."]
    assert out.read_text() == "FR: A.\n\nB.\n\nC.\n"


def test_cli_bilingual_and_retranslate(tmp_path, patched):
    source = tmp_path / "s.txt"
    source.write_text("Alpha.\n\nBeta.\n\nGamma.\n")
    assert cli.main([str(source), "--bilingual"]) == 0
    assert (tmp_path / "s_bilingual.txt").read_text().startswith("Alpha.\nFR: Alpha.")
    assert cli.main([str(source), "--retranslate", "Beta"]) == 0
    assert patched.created[-1].calls == ["Beta."]
    assert cli.main([str(source), "--retranslate", "Nowhere"]) == 1


def test_cli_extract_requires_stridon_book(tmp_path, patched):
    (tmp_path / "x.epub").write_bytes(b"not a zip")
    assert cli.main([str(tmp_path / "x.epub"), "--extract"]) == 1
