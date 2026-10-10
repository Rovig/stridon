"""Available translation services and how to build them."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from stridon.translators.base import (
    FatalTranslationError,
    KeyRing,
    ServiceConfig,
    TranslationError,
    Translator,
)


@dataclass(frozen=True)
class Service:
    """How to reach a translation service: implementation, URL, key variables, default model."""

    style: str  # which implementation handles it
    base: str | None = None
    env: tuple = ()
    model: str | None = None
    needs_key: bool = True
    needs_model: bool = False


SERVICES = {
    "openai": Service("openai", "https://api.openai.com/v1", ("OPENAI_API_KEY",), "gpt-5-mini"),
    "azure": Service("openai", None, ("AZURE_OPENAI_API_KEY",)),
    "xai": Service("openai", "https://api.x.ai/v1", ("XAI_API_KEY",), "grok-3-mini"),
    "groq": Service("openai", "https://api.groq.com/openai/v1", ("GROQ_API_KEY",), "llama-3.3-70b-versatile"),
    "deepseek": Service("openai", "https://api.deepseek.com/v1", ("DEEPSEEK_API_KEY",), "deepseek-chat"),
    "mistral": Service("openai", "https://api.mistral.ai/v1", ("MISTRAL_API_KEY",), "mistral-small-latest"),
    "openrouter": Service("openai", "https://openrouter.ai/api/v1", ("OPENROUTER_API_KEY",), needs_model=True),
    "claude": Service("claude", None, ("ANTHROPIC_API_KEY",)),
    "gemini": Service("gemini", None, ("GEMINI_API_KEY", "GOOGLE_API_KEY")),
    "qwen": Service("qwen", None, ("DASHSCOPE_API_KEY",)),
    "ollama": Service("ollama", None, (), needs_key=False),
    "google": Service("google", needs_key=False),
    "deepl": Service("deepl", None, ("RAPIDAPI_KEY",)),
    "caiyun": Service("caiyun", None, ("CAIYUN_TOKEN",)),
    "tencent": Service("tencent", needs_key=False),
    "custom": Service("custom", needs_key=False),
}

PROVIDER_STYLES = {"openai", "claude", "gemini", "qwen", "ollama"}
PROVIDER_FIELDS = {"api_style", "base_url", "default_models", "env_key"}
PROVIDER_FILES = [Path.home() / ".stridon" / "providers.json", Path("stridon_providers.json")]


def load_providers() -> dict:
    """Merge the global and local provider files (local entries win)."""
    providers = {}
    for path in PROVIDER_FILES:
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except ValueError as e:
                raise FatalTranslationError(f"{path}: invalid JSON ({e})") from e
            if not isinstance(data, dict) or not isinstance(data.get("providers", {}), dict):
                raise FatalTranslationError(f'{path}: expected {{"providers": {{...}}}}')
            providers.update(data.get("providers", {}))
    return providers


def _provider_service(name: str) -> Service:
    providers = load_providers()
    if name not in providers:
        available = ", ".join(sorted(providers)) or "none"
        raise FatalTranslationError(f"unknown provider '{name}' (available: {available})")
    entry = providers[name]
    if not isinstance(entry, dict):
        raise FatalTranslationError(f"provider '{name}' must be a JSON object")
    unknown = set(entry) - PROVIDER_FIELDS
    if unknown:
        raise FatalTranslationError(f"provider '{name}': unknown field(s) {', '.join(sorted(unknown))}")
    style = entry.get("api_style")
    if style not in PROVIDER_STYLES:
        raise FatalTranslationError(f"provider '{name}': api_style must be one of {', '.join(sorted(PROVIDER_STYLES))}")
    models = entry.get("default_models")
    if models is not None and (not isinstance(models, list) or not models or not all(isinstance(m, str) for m in models)):
        raise FatalTranslationError(f"provider '{name}': default_models must be a non-empty list of strings")
    env = (entry["env_key"],) if entry.get("env_key") else ()
    return Service(style, entry.get("base_url"), env, ",".join(models) if models else None,
                   needs_key=style not in ("ollama",), needs_model=not models)


def resolve_key(name: str, service: Service, api_key: str | None) -> str:
    """The API key from --api-key, STRIDON_<NAME>_KEY or the service's usual variable."""
    if api_key:
        return api_key
    for var in (f"STRIDON_{name.upper()}_KEY", *service.env):
        if os.environ.get(var):
            return os.environ[var]
    return ""


def build_translator(
    service_name: str,
    language,
    models: str | None = None,
    api_key: str | None = None,
    provider: str | None = None,
    **options,
) -> Translator:
    """Create the translator for a service name (or a --provider entry)."""
    if provider:
        service, name = _provider_service(provider), provider
    elif service_name in SERVICES:
        service, name = SERVICES[service_name], service_name
    else:
        raise FatalTranslationError(f"unknown service '{service_name}' (available: {', '.join(SERVICES)})")

    key = resolve_key(name, service, api_key)
    if service.needs_key and not key:
        hint = f" or set {' / '.join((f'STRIDON_{name.upper()}_KEY', *service.env))}"
        raise FatalTranslationError(f"{name} needs an API key: use --api-key{hint}")

    model_list = [m.strip() for m in (models or service.model or "").split(",") if m.strip()]
    if service.needs_model and not model_list:
        raise FatalTranslationError(f"{name} needs a model: use -m/--model")
    if name == "azure" and not (options.get("azure_deployment") and options.get("api_base")):
        raise FatalTranslationError("azure needs --azure-deployment and --api-base (your Azure endpoint)")

    config = ServiceConfig(
        service=name,
        language=language,
        models=list(dict.fromkeys(model_list)),
        keys=KeyRing(key),
        api_base=options.pop("api_base", None) or service.base,
        **options,
    )

    if service.style == "openai":
        from stridon.translators.openai_compat import OpenAICompatTranslator
        return OpenAICompatTranslator(config, name=name)
    if service.style == "claude":
        from stridon.translators.claude import ClaudeTranslator
        return ClaudeTranslator(config)
    if service.style == "gemini":
        from stridon.translators.gemini import GeminiTranslator
        return GeminiTranslator(config)
    if service.style == "qwen":
        from stridon.translators.qwen import QwenTranslator
        return QwenTranslator(config)
    if service.style == "ollama":
        from stridon.translators.ollama import OllamaTranslator
        return OllamaTranslator(config)

    from stridon.translators import web_services
    classes = {
        "google": web_services.GoogleTranslator,
        "deepl": web_services.DeepLTranslator,
        "caiyun": web_services.CaiyunTranslator,
        "tencent": web_services.TencentTranslator,
        "custom": web_services.CustomAPITranslator,
    }
    return classes[service.style](config)


__all__ = ["SERVICES", "build_translator", "Translator", "TranslationError", "FatalTranslationError"]
