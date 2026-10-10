"""Translation prompts for LLM services.

A prompt has an optional system message and a user template. Templates
use the placeholders {text} (required in the user template), {language}
and {crlf} (a newline). Other braces are left untouched, so templates
never need escaping.
"""

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path


class PromptError(ValueError):
    pass


@dataclass
class Prompt:
    user: str
    system: str = ""

    def render_user(self, text: str, language: str) -> str:
        return _fill(self.user, text=text, language=language)

    def render_system(self, language: str) -> str:
        return _fill(self.system, text="", language=language)


def _fill(template: str, text: str, language: str) -> str:
    # Replace placeholders in a single pass so that "{language}" inside the
    # text to translate is never substituted.
    values = {"text": text, "language": language, "crlf": "\n"}
    return re.sub(r"\{(text|language|crlf)\}", lambda m: values[m.group(1)], template)


def _check(prompt: Prompt) -> Prompt:
    if "{text}" not in prompt.user:
        raise PromptError("the user prompt must contain {text}")
    return prompt


def _from_mapping(data: dict) -> Prompt:
    unknown = set(data) - {"user", "system"}
    if unknown:
        raise PromptError(f"unknown prompt keys: {', '.join(sorted(unknown))} (allowed: user, system)")
    if "user" not in data:
        raise PromptError("the prompt needs a 'user' entry")
    return _check(Prompt(user=data["user"], system=data.get("system", "") or ""))


def _from_promptdown(content: str) -> Prompt:
    """Read a PromptDown file: a System or Developer Message section and a
    Conversation table whose first User row is the template."""
    sections = {}
    current = None
    for line in content.splitlines():
        heading = re.match(r"^#{2,}\s+(.+?)\s*$", line)
        if heading:
            current = heading.group(1).strip().lower()
            sections[current] = []
        elif current:
            sections[current].append(line)

    system = "\n".join(sections.get("developer message") or sections.get("system message") or []).strip()

    user = None
    rows = [l for l in sections.get("conversation", []) if l.strip().startswith("|")]
    for row in rows:
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0].lower() == "user":
            user = cells[1].replace("<br>", "\n").replace("\\n", "\n")
            break
    if user is None:
        raise PromptError("the PromptDown file has no User message in its Conversation table")
    return _check(Prompt(user=user, system=system))


def load_prompt(arg: str | None) -> Prompt | None:
    """Build a prompt from --prompt: a .md (PromptDown), .txt or .json file,
    a JSON string, or a plain template string. Falls back to the
    STRIDON_USER_PROMPT / STRIDON_SYSTEM_PROMPT environment variables."""
    prompt = None
    if arg:
        path = Path(arg)
        suffix = path.suffix.lower()
        if suffix in (".md", ".txt", ".json"):
            if not path.is_file():
                raise PromptError(f"{arg} not found")
            content = path.read_text(encoding="utf-8")
            if suffix == ".md":
                prompt = _from_promptdown(content)
            elif suffix == ".txt":
                prompt = _check(Prompt(user=content))
            else:
                prompt = _from_mapping(json.loads(content))
        else:
            try:
                data = json.loads(arg)
            except json.JSONDecodeError:
                data = None
            prompt = _from_mapping(data) if isinstance(data, dict) else _check(Prompt(user=arg))

    env_user = os.environ.get("STRIDON_USER_PROMPT")
    env_system = os.environ.get("STRIDON_SYSTEM_PROMPT")
    if prompt is None and (env_user or env_system):
        prompt = _check(Prompt(user=env_user or DEFAULT_USER, system=env_system or ""))
    return prompt


DEFAULT_USER = (
    "Translate the text between triple backticks into {language}. "
    "Reply with the translation only, without the original text, quotes or comments."
    "{crlf}```{crlf}{text}{crlf}```"
)

_STEP3 = re.compile(r"<step3_refined_translation>(.*?)</step3_refined_translation>", re.S)
_THINK = re.compile(r"<think>.*?</think>", re.S)


def clean_llm_output(text: str) -> str:
    """Remove reasoning blocks and keep only the final step of the
    3-step reflective prompts."""
    text = _THINK.sub("", text or "")
    match = _STEP3.search(text)
    if match:
        text = match.group(1)
    text = text.strip()
    # Some models echo the backtick fence of the default prompt
    if text.startswith("```") and text.endswith("```") and len(text) >= 6:
        text = text[3:-3].strip("\n")
    return text.strip()
