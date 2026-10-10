import json
from types import SimpleNamespace

import pytest
import requests

from stridon.languages import Language
from stridon.translators import build_translator
from stridon.translators.base import Context, FatalTranslationError, KeyRing, ServiceConfig
from stridon.translators.llm import LLMTranslator


class FakeResponse:
    def __init__(self, data=None, status=200, text=None):
        self._data = data
        self.status_code = status
        self.ok = status < 400
        self.text = text if text is not None else json.dumps(data)
        self.headers = {}

    def json(self):
        return self._data


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)


# -- shared LLM behavior --------------------------------------------------------

class EchoLLM(LLMTranslator):
    name = "echo"

    def __init__(self, answers, json_ok=False):
        super().__init__(ServiceConfig(service="echo", language=Language("fr")))
        self.answers = list(answers)
        self.requests = []
        self.json_ok = json_ok

    @property
    def supports_json(self):
        return self.json_ok

    def _complete(self, system, messages, schema):
        self.requests.append((system, messages, schema))
        return self.answers.pop(0)


def test_llm_delimited_batch():
    llm = EchoLLM(["Un\n\n@@\n\nDeux"])
    assert llm.translate_batch(["One", "Two"]) == ["Un", "Deux"]
    assert "@@" in llm.requests[0][1][-1]["content"]


def test_llm_batch_falls_back_one_by_one_on_mismatch():
    llm = EchoLLM(["Un seul", "Un", "Deux"])
    assert llm.translate_batch(["One", "Two"]) == ["Un", "Deux"]


def test_llm_json_batch():
    llm = EchoLLM([json.dumps({"translations": ["Un", "Deux"]})], json_ok=True)
    assert llm.translate_batch(["One", "Two"]) == ["Un", "Deux"]
    assert llm.requests[0][2]["required"] == ["translations"]


def test_llm_sends_context():
    llm = EchoLLM(["Un", "Deux"])
    context = Context(3)
    llm.translate("One", context)
    llm.translate("Two", context)
    messages = llm.requests[1][1]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[1]["content"] == "Un"


# -- service construction -------------------------------------------------------

def test_missing_key_is_reported(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("STRIDON_OPENAI_KEY", raising=False)
    with pytest.raises(FatalTranslationError, match="needs an API key"):
        build_translator("openai", Language("fr"))


def test_key_from_environment_and_rotation(monkeypatch):
    monkeypatch.setenv("STRIDON_OPENAI_KEY", "k1,k2")
    translator = build_translator("openai", Language("fr"))
    assert [translator.config.keys.next() for _ in range(3)] == ["k1", "k2", "k1"]
    assert translator.config.models == ["gpt-5-mini"]


def test_provider_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("stridon.translators.PROVIDER_FILES", [tmp_path / "stridon_providers.json"])
    (tmp_path / "stridon_providers.json").write_text(json.dumps({"providers": {
        "local": {"api_style": "openai", "base_url": "http://x/v1", "default_models": ["m1"], "env_key": "LOCAL_KEY"},
        "bad": {"api_style": "nope"},
    }}))
    monkeypatch.setenv("LOCAL_KEY", "secret")
    translator = build_translator("openai", Language("fr"), provider="local")
    assert translator.base == "http://x/v1" and translator.config.models == ["m1"]
    with pytest.raises(FatalTranslationError, match="api_style"):
        build_translator("openai", Language("fr"), provider="bad")


# -- HTTP services --------------------------------------------------------------

def test_openai_request(monkeypatch):
    sent = []

    def post(url, headers=None, json=None, timeout=None, **kw):
        sent.append((url, headers, json))
        return FakeResponse({"choices": [{"message": {"content": "Bonjour"}}]})

    monkeypatch.setattr(requests, "post", post)
    translator = build_translator("deepseek", Language("fr"), api_key="k", extra_body={"top_p": 0.5}, temperature=0.2)
    assert translator.translate("Hello") == "Bonjour"
    url, headers, body = sent[0]
    assert url == "https://api.deepseek.com/v1/chat/completions"
    assert headers["Authorization"] == "Bearer k"
    assert body["model"] == "deepseek-chat" and body["top_p"] == 0.5 and body["temperature"] == 0.2


def test_azure_request(monkeypatch):
    sent = []
    monkeypatch.setattr(requests, "post", lambda url, headers=None, json=None, timeout=None: (
        sent.append((url, headers, json)) or FakeResponse({"choices": [{"message": {"content": "Salut"}}]})))
    translator = build_translator("azure", Language("fr"), api_key="k", api_base="https://res.openai.azure.com",
                                  azure_deployment="dep")
    translator.translate("Hi")
    url, headers, body = sent[0]
    assert "/openai/deployments/dep/chat/completions?api-version=" in url
    assert headers == {"api-key": "k"} and "model" not in body


def test_retry_then_success(monkeypatch, no_sleep):
    answers = [FakeResponse({}, status=429, text="slow down"),
               FakeResponse({"choices": [{"message": {"content": "Ok"}}]})]
    monkeypatch.setattr(requests, "post", lambda *a, **k: answers.pop(0))
    assert build_translator("openai", Language("fr"), api_key="k").translate("x") == "Ok"


def test_auth_error_is_fatal(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse({}, status=401, text="bad key"))
    with pytest.raises(FatalTranslationError):
        build_translator("openai", Language("fr"), api_key="k").translate("x")


def test_ollama_request(monkeypatch):
    sent = []
    monkeypatch.setattr(requests, "post", lambda url, json=None, timeout=None: (
        sent.append((url, json)) or FakeResponse({"message": {"content": " Bonjour "}})))
    translator = build_translator("ollama", Language("fr"), api_base="http://box:11434/v1")
    assert translator.translate("Hello") == "Bonjour"
    url, body = sent[0]
    assert url == "http://box:11434/api/chat"
    assert body["model"] == "aya" and body["options"]["num_ctx"] == 4096
    assert body["messages"][0]["role"] == "system" and "Hello" in body["messages"][1]["content"]


def test_gemini_request_and_block(monkeypatch, no_sleep):
    answers = [
        FakeResponse({"promptFeedback": {"blockReason": "SAFETY"}}),
        FakeResponse({"candidates": [{"content": {"parts": [{"text": "Bonjour"}]}}]}),
    ]
    sent = []
    monkeypatch.setattr(requests, "post", lambda url, headers=None, json=None, timeout=None: (
        sent.append(url) or answers.pop(0)))
    translator = build_translator("gemini", Language("fr"), api_key="k", models="m1,m2")
    assert translator.translate("Hello") == "Bonjour"
    assert sent[0].endswith("/models/m1:generateContent") and sent[1].endswith("/models/m2:generateContent")


def test_gemini_region_error_is_fatal(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse({}, status=400, text="FAILED_PRECONDITION"))
    with pytest.raises(FatalTranslationError):
        build_translator("gemini", Language("fr"), api_key="k").translate("x")


def test_claude_request(monkeypatch):
    calls = []

    class Messages:
        def create(self, **params):
            calls.append(params)
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="Bonjour")])

    fake_client = SimpleNamespace(messages=Messages(), beta=SimpleNamespace(messages=Messages()))
    monkeypatch.setattr("anthropic.Anthropic", lambda **kw: fake_client)
    translator = build_translator("claude", Language("fr"), api_key="k")
    assert translator.translate("Hello") == "Bonjour"
    params = calls[0]
    assert params["model"] == "claude-opus-5-5"
    assert params["betas"] == ["server-side-fallback-2026-07-01"]
    assert params["extra_body"]["fallbacks"] == "default"
    assert "temperature" not in params


def test_qwen_uses_translation_options(monkeypatch):
    sent = []
    monkeypatch.setattr(requests, "post", lambda url, headers=None, json=None, timeout=None: (
        sent.append(json) or FakeResponse({"choices": [{"message": {"content": "你好"}}]})))
    translator = build_translator("qwen", Language("zh-hans"), api_key="k")
    context = Context(2)
    translator.translate("Hello", context)
    translator.translate("Hi", context)
    options = sent[1]["translation_options"]
    assert options["target_lang"] == "Chinese" and options["tm_list"] == [{"source": "Hello", "target": "你好"}]


def test_google_parsing(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda url, **kw: FakeResponse({"sentences": [{"trans": "Bon"}, {"trans": "jour"}]}))
    assert build_translator("google", Language("fr")).translate("Hello") == "Bonjour"


def test_deepl_language_check():
    assert build_translator("deepl", Language("en"), api_key="k").target == "en-US"
    with pytest.raises(FatalTranslationError):
        build_translator("deepl", Language("hi"), api_key="k")


def test_keyring_empty():
    assert KeyRing(None).next() == "" and not KeyRing(" , ")


def test_unsupported_targets_are_rejected():
    with pytest.raises(FatalTranslationError):
        build_translator("caiyun", Language("fr"), api_key="k")
    with pytest.raises(FatalTranslationError):
        build_translator("tencent", Language("de"))
    assert build_translator("caiyun", Language("ja"), api_key="k").trans_type == "auto2ja"


def test_deepl_source_language():
    assert "source" not in build_translator("deepl", Language("de"), api_key="k")._body("x")
    assert build_translator("deepl", Language("de"), api_key="k", source_lang="fr")._body("x")["source"] == "FR"


def test_claude_drops_temperature_for_models_that_reject_it(monkeypatch):
    calls = []

    class Messages:
        def create(self, **params):
            calls.append(params)
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="ok")])

    monkeypatch.setattr("anthropic.Anthropic", lambda **kw: SimpleNamespace(messages=Messages(), beta=SimpleNamespace(messages=Messages())))
    build_translator("claude", Language("fr"), api_key="k", temperature=0.3).translate("x")
    build_translator("claude", Language("fr"), api_key="k", temperature=0.3, models="claude-haiku-4-5").translate("x")
    assert "temperature" not in calls[0] and calls[1]["temperature"] == 0.3
