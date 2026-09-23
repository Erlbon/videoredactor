"""Shared test setup: isolate tool lookup from whatever happens to be
installed on the machine running the tests. The well-known install
folder tier (core.external_tools._install_dirs) would otherwise find a
real MKVToolNix under Program Files and break tests that deliberately
simulate it being absent."""

import pytest


@pytest.fixture(autouse=True)
def _no_install_dir_tool_lookup(monkeypatch):
    import core.external_tools as external_tools

    monkeypatch.setattr(external_tools, "_install_dirs", lambda: [])
