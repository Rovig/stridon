"""Machine translation services that are not LLMs: Google Translate,
DeepL (RapidAPI), Caiyun, Tencent TranSmart and custom HTTP APIs."""

import time
import uuid
from urllib.parse import urlencode

import requests

from stridon.translators.base import (
    Context,
    FatalTranslationError,
    RetryableError,
    ServiceConfig,
    TranslationError,
    Translator,
    http_error,
    with_retries,
)


class _WebTranslator(Translator):
    attempts = 3
    min_wait = 2.0

    def _translate(self, text: str) -> str:
        raise NotImplementedError

    def translate(self, text: str, context: Context | None = None) -> str:
        if not text.strip():
            return text
        translation = with_retries(
            lambda: self._translate(text), attempts=self.attempts, min_wait=self.min_wait, service=self.name
        )
        if not translation.strip():
            raise TranslationError(f"{self.name}: empty answer")
        return translation


# -- Google Translate (free endpoint) ---------------------------------------

GOOGLE_CODES = {"zh-hans": "zh-CN", "zh": "zh-CN", "zh-hant": "zh-TW", "zh-yue": "yue", "he": "iw", "jw": "jv"}


class GoogleTranslator(_WebTranslator):
    name = "google"
    attempts = 4

    def _translate(self, text: str) -> str:
        params = [
            ("client", "it"), ("otf", "2"), ("dj", "1"), ("hl", "en"), ("ie", "UTF-8"), ("oe", "UTF-8"),
            ("sl", self.config.source_lang if self.config.source_lang not in (None, "", "auto") else "auto"),
            ("tl", GOOGLE_CODES.get(self.language.code, self.language.code)),
        ] + [("dt", dt) for dt in ("t", "bd", "qca", "rm", "ld")]
        response = requests.post(
            "https://translate.google.com/translate_a/single?" + urlencode(params),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "GoogleTranslate/6.29.59279 (iPhone; iOS 15.4; en; iPhone14,2)",
            },
            data={"q": text},
            timeout=15,
        )
        if not response.ok:
            raise RetryableError(f"google: HTTP {response.status_code}")
        return "".join(s.get("trans", "") for s in response.json().get("sentences", []))


# -- DeepL through the RapidAPI "DeepL Translator" ------------------------

DEEPL_TARGETS = {
    "bg", "zh", "cs", "da", "nl", "en-US", "en-GB", "et", "fi", "fr", "de", "el", "hu", "id", "it", "ja",
    "lv", "lt", "pl", "pt-PT", "pt-BR", "ro", "ru", "sk", "sl", "es", "sv", "tr", "uk", "ko", "nb",
}
DEEPL_ALIASES = {"en": "en-US", "pt": "pt-PT", "no": "nb", "zh-hans": "zh", "zh-hant": "zh"}


class DeepLTranslator(_WebTranslator):
    name = "deepl"
    attempts = 2
    min_wait = 30.0

    def __init__(self, config: ServiceConfig):
        super().__init__(config)
        code = DEEPL_ALIASES.get(self.language.code, self.language.code)
        match = {t.lower(): t for t in DEEPL_TARGETS}.get(code.lower())
        if not match:
            raise FatalTranslationError(f"DeepL does not support the language '{self.language.name}'")
        self.target = match

    def _body(self, text: str) -> dict:
        body = {"text": text, "target": self.target}
        source = (self.config.source_lang or "auto").lower()
        if source != "auto":
            body["source"] = source.upper()
        return body

    def _translate(self, text: str) -> str:
        host = "dpl-translator.p.rapidapi.com"
        response = requests.post(
            f"https://{host}/translate",
            headers={"X-RapidAPI-Key": self.config.keys.next(), "X-RapidAPI-Host": host},
            json=self._body(text),
            timeout=60,
        )
        if not response.ok:
            raise http_error(response, self.name)
        return response.json().get("text", "")


# -- Caiyun ------------------------------------------------------------------

CAIYUN_TYPES = {"zh": "auto2zh", "zh-hans": "auto2zh", "en": "auto2en", "ja": "auto2ja"}


class CaiyunTranslator(_WebTranslator):
    name = "caiyun"
    attempts = 2
    min_wait = 60.0

    def __init__(self, config: ServiceConfig):
        super().__init__(config)
        if self.language.code not in CAIYUN_TYPES:
            raise FatalTranslationError("Caiyun only translates into Simplified Chinese, English or Japanese")
        self.trans_type = CAIYUN_TYPES[self.language.code]

    def _translate(self, text: str) -> str:
        trans_type = self.trans_type
        response = requests.post(
            "https://api.interpreter.caiyunai.com/v1/translator",
            headers={"x-authorization": f"token {self.config.keys.next()}"},
            json={"source": text, "trans_type": trans_type, "request_id": "stridon", "detect": True},
            timeout=60,
        )
        if not response.ok:
            raise http_error(response, self.name)
        data = response.json()
        if "target" not in data:
            raise RetryableError(f"caiyun: {data.get('message', 'unexpected answer')}")
        return data["target"]


# -- Tencent TranSmart (free web endpoint) -----------------------------------

class TencentTranslator(_WebTranslator):
    name = "tencent"
    url = "https://transmart.qq.com/api/imt"

    def __init__(self, config: ServiceConfig):
        super().__init__(config)
        if self.language.code not in ("en", "zh", "zh-hans"):
            raise FatalTranslationError("Tencent TranSmart only translates into Simplified Chinese or English")
        self.session_id = str(uuid.uuid4())

    def _post(self, body: dict) -> dict:
        client_key = f"browser-chrome-121.0.0-Windows_10-{self.session_id}-{int(time.time() * 1000)}"
        body["header"]["client_key"] = client_key
        response = requests.post(
            self.url,
            json=body,
            headers={
                "origin": "https://transmart.qq.com",
                "referer": "https://transmart.qq.com/zh-CN/index",
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            },
            cookies={"TSMT_CLIENT_KEY": client_key},
            timeout=30,
        )
        if not response.ok:
            raise http_error(response, self.name)
        return response.json()

    def _translate(self, text: str) -> str:
        analysis = self._post({
            "header": {"fn": "text_analysis", "session": "", "user": ""},
            "text": text,
            "type": "plain",
            "normalize": {"merge_broken_line": "false"},
        })
        segments = [s["tgt_str"] for s in analysis.get("sentence_list", [])] or [text]
        target = "en" if self.language.code == "en" else "zh"
        result = self._post({
            "header": {"fn": "auto_translation"},
            "type": "plain",
            "model_category": "normal",
            "source": {"lang": analysis.get("language", "auto"), "text_list": ["", *segments, ""]},
            "target": {"lang": target},
        })
        return "".join(result.get("auto_translation", []))


# -- Custom HTTP API ---------------------------------------------------------

class CustomAPITranslator(_WebTranslator):
    """POST {"text", "source_lang", "target_lang"} to a URL that answers {"data": translation}."""

    name = "custom"
    attempts = 2

    def __init__(self, config: ServiceConfig):
        super().__init__(config)
        if not config.api_base:
            raise FatalTranslationError("the custom service needs the API URL in --api-base")

    def _translate(self, text: str) -> str:
        response = requests.post(
            self.config.api_base,
            json={"text": text, "source_lang": self.config.source_lang or "auto", "target_lang": self.language.name},
            timeout=30,
        )
        if not response.ok:
            raise http_error(response, self.name)
        return response.json().get("data", "")
