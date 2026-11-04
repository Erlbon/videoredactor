"""
The app's core package. Importing it sets up the shared lookup clients (redactor_common.core.lookups) the way this
app uses them, so every caller -- the GUI, the CLI and the tests -- gets the same User-Agent and key source.
"""

from redactor_common.core.lookups import tmdb as _tmdb


def _tmdb_key():
    from core import api_keys  # here, not at the top: api_keys reads the settings file

    return api_keys.get_key("tmdb")


_tmdb.configure("TheVideoRedactor", key_source=_tmdb_key)
