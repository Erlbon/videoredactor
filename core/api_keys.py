"""
The TMDB / TheTVDB / OpenSubtitles API keys, kept in redactor_common's
secret store (OS credential store) instead of settings.ini.

Each client's get_api_key() goes through get_key() here: the env var
still wins, then the store, then the old plaintext settings.ini value
(`legacy`), so an install that hasn't migrated yet keeps working.
migrate_legacy_keys() moves the old values into the store at startup and
removes them from settings.ini, only after reading them back from the
store. With no usable keyring it leaves them where they are.

The service/entry names below are permanent: renaming one orphans every
user's stored key.
"""

from __future__ import annotations
from typing import Optional

from core.config import get_setting, set_setting, remove_setting
from redactor_common.core import secret_store

APP = "videoredactor"

# key id -> (secret name, env var, legacy settings.ini section, label)
KEYS = {
    "tmdb": ("tmdb_api_key", "TMDB_API_KEY", "tmdb", "TMDB API Key"),
    "tvdb": ("tvdb_api_key", "TVDB_API_KEY", "tvdb", "TheTVDB API Key"),
    "opensubtitles": ("opensubtitles_api_key", "OPENSUBTITLES_API_KEY", "opensubtitles",
                      "OpenSubtitles API Key"),
}

# The one non-secret setting here: did the user agree to the UNENCRYPTED
# fallback file on a machine with no credential store?
_FALLBACK_SECTION = "secrets"
_FALLBACK_KEY = "allow_unencrypted_fallback"


def _legacy(key_id: str) -> str:
    return get_setting(KEYS[key_id][2], "api_key", "")


def get_key(key_id: str) -> Optional[str]:
    name, env_var, _, _ = KEYS[key_id]
    value = secret_store.get_secret(APP, name, env_var=env_var, legacy=lambda: _legacy(key_id))
    return value or None


def remember_unencrypted_fallback(allowed: bool) -> None:
    """Persist the user's yes (never a silent no) and apply it now."""
    secret_store.set_allow_unencrypted_fallback(allowed)
    if allowed:
        set_setting(_FALLBACK_SECTION, _FALLBACK_KEY, "1")


def apply_fallback_preference() -> None:
    secret_store.set_allow_unencrypted_fallback(
        get_setting(_FALLBACK_SECTION, _FALLBACK_KEY, "") == "1"
    )


def migrate_legacy_keys() -> None:
    """Startup: settings.ini api_key entries -> secret store. Never raises."""
    try:
        apply_fallback_preference()
        for key_id, (name, _, section, _) in KEYS.items():
            secret_store.migrate_legacy_secret(
                APP, name,
                read_legacy=lambda k=key_id: _legacy(k),
                clear_legacy=lambda s=section: remove_setting(s, "api_key"),
            )
    except Exception:
        pass  # a settings/keyring hiccup must not block launch; legacy values still work
