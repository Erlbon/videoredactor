"""The pre-push hook runs the tests in this repo's own .venv, which redactor-build-tools/devenv/venv_sync.py keeps at the
redactor_common pin (2026-11-04: with one shared install, one repo's pin broke the others' pushes). Guards against a later
edit going back to the shared python."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
while not (ROOT / ".githooks").is_dir():
    ROOT = ROOT.parent


def test_the_pre_push_hook_tests_in_the_repos_own_venv():
    hook = (ROOT / ".githooks" / "pre-push").read_text(encoding="utf-8")
    assert "devenv/venv_sync.py" in hook and "--git-common-dir" in hook      # found from a worktree too
    assert '"$PY" -m pytest' in hook
    tests = [line for line in hook.splitlines() if "-m pytest" in line and not line.lstrip().startswith("#")]
    assert tests and all('"$PY"' in line for line in tests)                  # never the shared python


def test_the_venv_is_not_committed():
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
    assert {".venv", ".venv/", "/.venv", "/.venv/"} & set(lines)
