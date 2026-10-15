"""
core/translation_settings.py

Settings of the subtitle translation (Tools > Preferences > Translation) and the engine they select. Off by
default everywhere: nothing translates unless the user starts it (Media > Translate Subtitles to English...) or
turns on the Redact step.

  [translation] engine = libretranslate | argos   (default libretranslate)
                url    = the LibreTranslate-compatible server (default http://localhost:5000)
                target = the language to translate to (default en)
  [tools]       argos-translate = the command (External Tools / this page); else found on PATH
  API key       redactor_common secret store, "libretranslate" (optional), like the other keys
"""

from __future__ import annotations

from redactor_common.core.translate import (
    DEFAULT_URL, ArgosCliTranslator, LibreTranslateTranslator, Translator,
)

from core import api_keys
from core.config import get_setting
from core.external_tools import get_executable_path
from core.version import APP_VERSION

SECTION = "translation"
ENGINE_LIBRETRANSLATE = "libretranslate"
ENGINE_ARGOS = "argos"
ENGINE_CHOICES = ((ENGINE_LIBRETRANSLATE, "LibreTranslate server"), (ENGINE_ARGOS, "argos-translate command"))
DEFAULT_TARGET = "en"
USER_AGENT = f"TheVideoRedactor/{APP_VERSION} (https://github.com/Erlbon)"

_factory = None  # tests / e2e: a callable returning a Translator


def set_translator_factory(factory) -> None:
    global _factory
    _factory = factory


def engine() -> str:
    value = get_setting(SECTION, "engine", ENGINE_LIBRETRANSLATE)
    return value if value in (ENGINE_LIBRETRANSLATE, ENGINE_ARGOS) else ENGINE_LIBRETRANSLATE


def server_url() -> str:
    return get_setting(SECTION, "url", "") or DEFAULT_URL


def target_language() -> str:
    return (get_setting(SECTION, "target", "") or DEFAULT_TARGET).strip().lower() or DEFAULT_TARGET


def make_translator() -> Translator:
    """The engine the settings select."""
    if _factory is not None:
        return _factory()
    if engine() == ENGINE_ARGOS:
        return ArgosCliTranslator(get_executable_path("argos-translate"))
    return LibreTranslateTranslator(server_url(), api_key=api_keys.get_key("libretranslate") or "", user_agent=USER_AGENT)


def argos_installed_pairs(command: str) -> list[str]:
    """["ru -> en", ...] from the argospm command that sits beside the argos-translate command ("argospm list"),
    [] when it cannot be found or run."""
    import os
    import re
    from redactor_common.core.subprocess_utils import run_tool

    folder, name = os.path.split(command)
    exe = "argospm.exe" if name.lower().endswith(".exe") else "argospm"
    path = os.path.join(folder, exe) if folder else exe
    try:
        result = run_tool([path, "list"], timeout=60)
    except Exception:  # noqa: BLE001 -- best effort
        return []
    if result.returncode != 0:
        return []
    return [f"{a} -> {b}" for a, b in re.findall(r"translate-([a-z]{2,3})_([a-z]{2,3})", result.stdout)]


def check_engine(translator: Translator, source: str = "", target: str = "") -> str:
    """One line for the Preferences "Check the engine" button: reachable or runs, and the language pairs it has.
    Raises TranslateError (a message fit to show) when the engine cannot be used."""
    target = target or target_language()
    if isinstance(translator, ArgosCliTranslator):
        from redactor_common.core.subprocess_utils import run_tool
        from redactor_common.core.translate import TranslateUnavailable

        command = translator.command if isinstance(translator.command, str) else translator.command[-1]
        try:
            result = run_tool([command, "--help"], timeout=60)
        except Exception as exc:  # noqa: BLE001
            raise TranslateUnavailable(f"The command {command!r} could not be run: {exc}") from exc
        if result.returncode != 0:
            raise TranslateUnavailable(f"The command {command!r} did not run (exit code {result.returncode}).")
        pairs = argos_installed_pairs(command)
        if not pairs:
            return f"{command} runs, but no language model is installed (argospm install translate-xx_{target})."
        return f"{command} runs. Installed language pairs: {', '.join(pairs)}."
    return translator.check(source, target)
