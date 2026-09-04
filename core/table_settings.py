"""
core/table_settings.py

Thin project-specific wrapper around redactor_common.core.table_settings
-- the actual merge/visibility/sanitize logic now lives there (shared
with every other Redactor project) so a fix made once benefits all of
them. This file just binds this project's PROTECTED_COLUMNS and keeps
the original call signatures (no `protected_columns` argument) so
every existing call site here (main_window.py, tag_panel.py,
column_visibility_dialog.py) keeps working unchanged.
"""

from __future__ import annotations

from redactor_common.core import table_settings as _shared

# filename is the row-identity anchor (VideoFile lookups depend on it
# always being present) -- never hideable via the column context menu,
# regardless of what's in a persisted hidden-columns set (a stale/
# corrupted settings.ini should never be able to hide the one column
# that makes rows identifiable).
PROTECTED_COLUMNS = frozenset({"filename"})


def merge_column_order(persisted_order: list[str], current_fields: list[str]) -> list[str]:
    return _shared.merge_column_order(persisted_order, current_fields)


def is_column_visible(field_name: str, hidden_fields: set[str]) -> bool:
    return _shared.is_column_visible(field_name, hidden_fields, PROTECTED_COLUMNS)


def sanitize_hidden_fields(hidden_fields: set[str]) -> set[str]:
    return _shared.sanitize_hidden_fields(hidden_fields, PROTECTED_COLUMNS)
