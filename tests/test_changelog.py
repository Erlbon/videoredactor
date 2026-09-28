"""The CHANGELOG has a section for the current APP_VERSION -- the release
workflow builds the GitHub Release notes from it (redactor_common's
core/release_notes.py) and fails without one."""

from redactor_common.core.release_notes import build_notes

from core.version import APP_VERSION


def test_changelog_has_a_section_for_the_current_version():
    tag = "v" + APP_VERSION.replace("#", "-")
    with open("CHANGELOG.md", encoding="utf-8") as handle:
        assert build_notes(handle.read(), tag, None).strip()
