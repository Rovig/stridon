"""Target languages: codes, English names and lookups."""

# Language code -> English name
NAMES = {
    "en": "english", "zh-hans": "simplified chinese", "zh": "simplified chinese",
    "zh-hant": "traditional chinese", "zh-yue": "cantonese", "de": "german",
    "es": "spanish", "ru": "russian", "ko": "korean", "fr": "french",
    "ja": "japanese", "pt": "portuguese", "tr": "turkish", "pl": "polish",
    "ca": "catalan", "nl": "dutch", "ar": "arabic", "sv": "swedish",
    "it": "italian", "id": "indonesian", "hi": "hindi", "fi": "finnish",
    "vi": "vietnamese", "he": "hebrew", "uk": "ukrainian", "el": "greek",
    "ms": "malay", "cs": "czech", "ro": "romanian", "da": "danish",
    "hu": "hungarian", "ta": "tamil", "no": "norwegian", "th": "thai",
    "ur": "urdu", "hr": "croatian", "bg": "bulgarian", "lt": "lithuanian",
    "la": "latin", "mi": "maori", "ml": "malayalam", "cy": "welsh",
    "sk": "slovak", "te": "telugu", "fa": "persian", "lv": "latvian",
    "bn": "bengali", "sr": "serbian", "az": "azerbaijani", "sl": "slovenian",
    "kn": "kannada", "et": "estonian", "mk": "macedonian", "br": "breton",
    "eu": "basque", "is": "icelandic", "hy": "armenian", "ne": "nepali",
    "mn": "mongolian", "bs": "bosnian", "kk": "kazakh", "sq": "albanian",
    "sw": "swahili", "gl": "galician", "mr": "marathi", "pa": "punjabi",
    "si": "sinhala", "km": "khmer", "sn": "shona", "yo": "yoruba",
    "so": "somali", "af": "afrikaans", "oc": "occitan", "ka": "georgian",
    "be": "belarusian", "tg": "tajik", "sd": "sindhi", "gu": "gujarati",
    "am": "amharic", "yi": "yiddish", "lo": "lao", "uz": "uzbek",
    "fo": "faroese", "ht": "haitian creole", "ps": "pashto", "tk": "turkmen",
    "nn": "nynorsk", "mt": "maltese", "sa": "sanskrit", "lb": "luxembourgish",
    "my": "myanmar", "bo": "tibetan", "tl": "tagalog", "mg": "malagasy",
    "as": "assamese", "tt": "tatar", "haw": "hawaiian", "ln": "lingala",
    "ha": "hausa", "ba": "bashkir", "jw": "javanese", "su": "sundanese",
}

# English name -> language code (first code wins, plus common aliases)
CODES = {}
for _code, _name in NAMES.items():
    CODES.setdefault(_name, _code)
CODES.update({
    "simplified chinese": "zh", "chinese": "zh", "burmese": "my",
    "valencian": "ca", "flemish": "nl", "haitian": "ht",
    "letzeburgesch": "lb", "pushto": "ps", "panjabi": "pa",
    "moldavian": "ro", "moldovan": "ro", "sinhalese": "si", "castilian": "es",
})


class Language:
    """A target language, usable as a name in prompts or as a code for APIs."""

    def __init__(self, value: str):
        value = value.strip()
        key = value.lower()
        if key in NAMES:
            self.code = key
            self.name = NAMES[key]
        elif key in CODES:
            self.code = CODES[key]
            self.name = key
        else:
            # Unknown language: LLMs understand free text, code-based APIs get it as-is
            self.code = key
            self.name = value

    def __str__(self):
        return self.name

    def __repr__(self):
        return f"Language(code={self.code!r}, name={self.name!r})"
