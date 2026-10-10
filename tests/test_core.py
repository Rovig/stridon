import json

import pytest

from stridon.cache import TranslationCache
from stridon.engine import Interrupted, Unit
from stridon.languages import Language
from stridon.prompts import Prompt, PromptError, clean_llm_output, load_prompt
from tests.conftest import FakeTranslator, make_engine


# -- languages ------------------------------------------------------------------

def test_language_from_code_and_name():
    assert Language("fr").name == "french"
    assert Language("French").code == "fr"
    assert Language("zh-hans").name == "simplified chinese"
    assert Language("simplified chinese").code == "zh"
    unknown = Language("Klingon")
    assert unknown.name == "Klingon" and unknown.code == "klingon"


# -- prompts --------------------------------------------------------------------

def test_prompt_placeholders_are_not_replaced_inside_text():
    prompt = Prompt(user="To {language}:{crlf}{text}", system="Into {language}")
    assert prompt.render_user("keep {language} and {x}", "french") == "To french:\nkeep {language} and {x}"
    assert prompt.render_system("french") == "Into french"


def test_prompt_forms(tmp_path):
    assert load_prompt("Translate {text}").user == "Translate {text}"
    assert load_prompt('{"user": "U {text}", "system": "S"}').system == "S"
    (tmp_path / "p.txt").write_text("From file {text}")
    assert load_prompt(str(tmp_path / "p.txt")).user == "From file {text}"
    (tmp_path / "p.json").write_text(json.dumps({"user": "J {text}"}))
    assert load_prompt(str(tmp_path / "p.json")).user == "J {text}"
    (tmp_path / "p.md").write_text(
        "# Prompt\n\n## Developer Message\nYou translate.\n\n## Conversation\n\n"
        "| Role | Content |\n|---|---|\n| User | Into {language}: {text} |\n"
    )
    prompt = load_prompt(str(tmp_path / "p.md"))
    assert prompt.system == "You translate." and prompt.user == "Into {language}: {text}"


@pytest.mark.parametrize("arg", ["no placeholder", '{"user": "x {text}", "other": 1}', "missing.json"])
def test_prompt_errors(arg):
    with pytest.raises(PromptError):
        load_prompt(arg)


def test_clean_llm_output():
    assert clean_llm_output("<think>hmm</think>\n Bonjour ") == "Bonjour"
    assert clean_llm_output("a<step3_refined_translation> Final </step3_refined_translation>") == "Final"
    assert clean_llm_output("```\nBonjour\n```") == "Bonjour"


# -- engine ---------------------------------------------------------------------

def test_engine_translates_skips_empty_and_dedupes(fake):
    results = make_engine(fake).run([Unit("a"), Unit(""), Unit("b"), Unit("a")])
    assert results == ["FR: a", None, "FR: b", "FR: a"]
    assert fake.calls == ["a", "b"]


def test_engine_block_size_and_scopes(fake):
    make_engine(fake, block_size=2).run([Unit("a", "c1"), Unit("b", "c1"), Unit("c", "c1"), Unit("d", "c2")])
    assert fake.batches == [["a", "b"], ["c"], ["d"]]


def test_engine_token_budget(fake):
    units = [Unit("one two"), Unit("three four"), Unit("x " * 200), Unit("five")]
    make_engine(fake, token_budget=10).run(units)
    assert fake.batches == [["one two", "three four"], ["x " * 200], ["five"]]


def test_engine_groups_sentences_together(fake):
    units = [Unit("s1", group="p1"), Unit("s2", group="p1"), Unit("other")]
    make_engine(fake).run(units)
    assert fake.batches == [["s1", "s2"], ["other"]]


def test_engine_test_limit(fake):
    results = make_engine(fake, test_limit=2).run([Unit("a"), Unit("b"), Unit("c")])
    assert results == ["FR: a", "FR: b", None]


def test_engine_resumes_from_cache(tmp_path):
    cache_path = tmp_path / "cache.jsonl"
    failing = FakeTranslator(fail_after=2)
    with pytest.raises(Interrupted) as info:
        make_engine(failing, cache_path).run([Unit(t) for t in "abcd"])
    assert info.value.results[:2] == ["FR: a", "FR: b"]

    again = FakeTranslator()
    results = make_engine(again, cache_path).run([Unit(t) for t in "abcd"])
    assert results == ["FR: a", "FR: b", "FR: c", "FR: d"]
    assert again.calls == ["c", "d"]


def test_cache_ignores_truncated_line(tmp_path):
    path = tmp_path / "c.jsonl"
    cache = TranslationCache(path)
    cache.put("hello", "bonjour")
    with path.open("a") as f:
        f.write('{"k": "broken')
    assert TranslationCache(path).get("hello") == "bonjour"


def test_engine_parallel_workers(fake):
    units = [Unit(f"t{i}") for i in range(20)]
    results = make_engine(fake, workers=4).run(units)
    assert results == [f"FR: t{i}" for i in range(20)]


def test_engine_context_is_sequential_per_scope():
    seen = []

    class Recorder(FakeTranslator):
        def translate(self, text, context=None):
            seen.append((text, [s for s, _ in context.pairs]))
            return super().translate(text, context)

    make_engine(Recorder(), context_limit=2, workers=2).run([Unit(t, "c1") for t in "abc"])
    assert seen == [("a", []), ("b", ["a"]), ("c", ["a", "b"])]


def test_workers_stop_after_an_error():
    import time

    class Slow(FakeTranslator):
        def translate(self, text, context=None):
            time.sleep(0.05)
            return super().translate(text, context)

    slow = Slow(fail_after=3)
    with pytest.raises(Interrupted):
        make_engine(slow, workers=2, context_limit=1).run([Unit(f"t{i}", scope=f"c{i % 2}") for i in range(40)])
    time.sleep(0.3)
    assert len(slow.calls) <= 5  # workers do not keep translating after the failure


def test_partial_results_fill_repeated_texts():
    failing = FakeTranslator(fail_after=1)
    with pytest.raises(Interrupted) as info:
        make_engine(failing).run([Unit("a"), Unit("b"), Unit("a")])
    assert info.value.results == ["FR: a", None, "FR: a"]


def test_interval_is_applied(monkeypatch, fake):
    sleeps = []
    monkeypatch.setattr("stridon.engine.time.sleep", sleeps.append)
    fake.config.interval = 0.5
    make_engine(fake).run([Unit("a"), Unit("b")])
    assert sleeps == [0.5, 0.5]
