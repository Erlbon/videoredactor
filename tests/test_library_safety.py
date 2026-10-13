"""core/library_safety: quarantine, undo log, verified temp swap, dry-run, recent-file guard."""

import os
import time
from datetime import date

import pytest

from core import library_safety as ls
from core.ffmpeg_backend import ffmpeg_command
from core.temp_names import is_app_temp_name

DAY = date(2026, 10, 13)
GOOD = ({"video": 1, "audio": 2, "subtitle": 1}, 100.0)


def _setup(tmp_path, dry_run=False, probe=None):
    lib = tmp_path / "lib"
    (lib / "Movies").mkdir(parents=True)
    run = ls.SafeRun(
        library_root=str(lib), quarantine_root=str(tmp_path / "q"), log=ls.UndoLog(str(tmp_path / "undo.tsv")),
        dry_run=dry_run, day=DAY, probe=probe or (lambda p: GOOD),
    )
    return lib, run


def _old(path, text="x"):
    path.write_text(text, encoding="utf-8")
    past = time.time() - 3600
    os.utime(path, (past, past))
    return str(path)


def test_quarantine_mirrors_relative_path_and_logs(tmp_path):
    lib, run = _setup(tmp_path)
    src = _old(lib / "Movies" / "a.mkv")
    dest = run.quarantine(src, ls.KIND_DUPLICATES, "dup")
    assert dest == str(tmp_path / "q" / "duplicates 2026-10-13" / "Movies" / "a.mkv")
    assert not os.path.exists(src) and os.path.isfile(dest)
    assert run.log.entries() == [(dest, src)]


def test_quarantine_never_overwrites(tmp_path):
    lib, run = _setup(tmp_path)
    a = _old(lib / "Movies" / "a.mkv", "one")
    first = run.quarantine(a, ls.KIND_CLEANUP, "r")
    b = _old(lib / "Movies" / "a.mkv", "two")
    second = run.quarantine(b, ls.KIND_CLEANUP, "r")
    assert first != second and os.path.isfile(first) and os.path.isfile(second)


def test_dry_run_touches_nothing_but_reports(tmp_path):
    lib, run = _setup(tmp_path, dry_run=True)
    src = _old(lib / "Movies" / "a.mkv")
    assert run.quarantine(src, ls.KIND_CLEANUP, "junk") is not None
    assert os.path.isfile(src)
    assert not (tmp_path / "q").exists() and not os.path.exists(run.log.path)
    assert run.actions[0].reason == "junk" and not run.actions[0].done


def test_recent_file_is_never_touched(tmp_path):
    lib, run = _setup(tmp_path)
    fresh = lib / "Movies" / "new.mkv"
    fresh.write_text("x")
    assert run.quarantine(str(fresh), ls.KIND_CLEANUP, "r") is None
    assert run.rename(str(fresh), str(fresh) + "2", "r") is False
    assert fresh.exists() and run.actions[0].verb == "skip"


def test_no_quarantine_folder_uses_the_recycle_bin(tmp_path):
    trashed = []
    run = ls.SafeRun(library_root=str(tmp_path), quarantine_root="", dry_run=False, trash=trashed.append, day=DAY)
    src = _old(tmp_path / "a.mkv")
    assert run.quarantine(src, ls.KIND_CLEANUP, "r") == "Recycle Bin"
    assert trashed == [src] and run.actions[0].verb == "recycle" and run.actions[0].done


def test_convert_without_quarantine_trashes_the_original_after_the_swap(tmp_path):
    trashed = []
    lib = tmp_path / "lib"
    lib.mkdir()
    run = ls.SafeRun(library_root=str(lib), quarantine_root="", dry_run=False, trash=trashed.append, day=DAY, probe=lambda p: GOOD)
    src = _old(lib / "a.mkv", "big")
    assert run.convert(src, src, _writer("small"), ls.KIND_RESIZED, "r", marker=ls.RESIZE_MARKER)
    assert open(src, encoding="utf-8").read() == "small"  # result in place under the original name
    assert len(trashed) == 1 and open(trashed[0], encoding="utf-8").read() == "big"
    assert os.path.basename(trashed[0]) == "a.redact-orig.mkv"


def test_trash_failure_keeps_the_original_beside_the_result(tmp_path):
    def broken(_p):
        raise OSError("bin unavailable")
    lib = tmp_path / "lib"
    lib.mkdir()
    run = ls.SafeRun(library_root=str(lib), quarantine_root="", dry_run=False, trash=broken, probe=lambda p: GOOD)
    src = _old(lib / "a.mkv", "big")
    assert run.convert(src, src, _writer("small"), ls.KIND_RESIZED, "r")
    assert (lib / "a.redact-orig.mkv").read_text() == "big" and "kept" in run.actions[0].detail


def test_file_outside_library_still_quarantined(tmp_path):
    lib, run = _setup(tmp_path)
    other = tmp_path / "elsewhere"
    other.mkdir()
    src = _old(other / "z.mkv")
    dest = run.quarantine(src, ls.KIND_CLEANUP, "r")
    assert dest.endswith(os.path.join("_outside library", "z.mkv")) and os.path.isfile(dest)


def test_rename_does_not_clobber(tmp_path):
    lib, run = _setup(tmp_path)
    a, b = _old(lib / "a.mkv"), _old(lib / "b.mkv")
    assert run.rename(a, b, "r") is False
    assert os.path.exists(a) and os.path.exists(b)


def _writer(content="new"):
    def build(src, temp):
        with open(temp, "w", encoding="utf-8") as f:
            f.write(content)
        return True, ""
    return build


def test_convert_swaps_after_verification(tmp_path):
    lib, run = _setup(tmp_path)
    src = _old(lib / "Movies" / "a.avi", "old")
    final = str(lib / "Movies" / "a.mkv")
    assert run.convert(src, final, _writer(), ls.KIND_REMUXED, "avi to mkv")
    assert open(final, encoding="utf-8").read() == "new"
    q = tmp_path / "q" / "remuxed originals 2026-10-13" / "Movies" / "a.avi"
    assert q.read_text() == "old" and not os.path.exists(src)
    assert not os.path.exists(ls.temp_name(final))
    # log order: original moved, then the new file created; reverse order restores
    assert run.log.entries() == [(str(q), src), (final, ls.UNDO_CREATED)]
    assert ls.undo_log(run.log.path, str(tmp_path / "q"), str(lib), DAY) == []
    assert open(src, encoding="utf-8").read() == "old" and not os.path.exists(final)
    assert (tmp_path / "q" / "undone 2026-10-13" / "Movies" / "a.mkv").exists()  # created file kept, not deleted


def test_convert_same_name_resize(tmp_path):
    lib, run = _setup(tmp_path)
    src = _old(lib / "a.mkv", "big")
    assert run.convert(src, src, _writer("small"), ls.KIND_RESIZED, "too big", marker=ls.RESIZE_MARKER)
    assert open(src, encoding="utf-8").read() == "small"
    assert (tmp_path / "q" / "resized originals 2026-10-13" / "a.mkv").read_text() == "big"


def test_failed_verification_keeps_original_and_deletes_only_temp(tmp_path):
    lib, run = _setup(tmp_path, probe=lambda p: GOOD if p.endswith("a.mkv") else ({"video": 1, "audio": 1, "subtitle": 1}, 100.0))
    src = _old(lib / "a.mkv", "orig")
    assert run.convert(src, src, _writer(), ls.KIND_RESIZED, "r") is False
    assert open(src, encoding="utf-8").read() == "orig"
    assert not os.path.exists(ls.temp_name(src)) and not (tmp_path / "q").exists()
    assert run.actions[0].verb == "skip" and "audio tracks" in run.actions[0].detail


def test_failed_build_keeps_original(tmp_path):
    lib, run = _setup(tmp_path)
    src = _old(lib / "a.avi", "orig")
    assert run.convert(src, str(lib / "a.mkv"), lambda s, t: (False, "boom"), ls.KIND_REMUXED, "r") is False
    assert open(src, encoding="utf-8").read() == "orig" and not (lib / "a.mkv").exists()


def test_convert_never_replaces_existing_final(tmp_path):
    lib, run = _setup(tmp_path)
    src = _old(lib / "a.avi")
    other = _old(lib / "a.mkv", "other")
    assert run.convert(src, other, _writer(), ls.KIND_REMUXED, "r") is False
    assert open(other, encoding="utf-8").read() == "other"


def test_convert_dry_run_writes_nothing(tmp_path):
    lib, run = _setup(tmp_path, dry_run=True)
    src = _old(lib / "a.avi")
    assert run.convert(src, str(lib / "a.mkv"), _writer(), ls.KIND_REMUXED, "r")
    assert os.listdir(lib) == ["Movies", "a.avi"] or sorted(os.listdir(lib)) == ["Movies", "a.avi"]


def test_verify_streams_rules():
    ok = ls.verify_streams(GOOD, GOOD)
    assert ok == ""
    assert "no video" in ls.verify_streams(GOOD, ({"audio": 2, "subtitle": 1}, 100.0))
    assert "subtitle" in ls.verify_streams(GOOD, ({"video": 1, "audio": 2}, 100.0))
    assert "duration" in ls.verify_streams(GOOD, ({"video": 1, "audio": 2, "subtitle": 1}, 90.0))  # shorter
    assert "duration" in ls.verify_streams(GOOD, ({"video": 1, "audio": 2, "subtitle": 1}, 110.0))  # longer too
    assert ls.verify_streams(GOOD, ({"video": 1, "audio": 2, "subtitle": 1}, 101.0)) == ""
    assert "ffprobe" in ls.verify_streams(None, GOOD)


def test_undo_log_is_utf8_and_refuses_tabs(tmp_path):
    log = ls.UndoLog(str(tmp_path / "u.tsv"))
    log.moved("C:\\x\\Amélie – 日本.mkv", "C:\\y\\a.mkv")
    assert log.entries() == [("C:\\x\\Amélie – 日本.mkv", "C:\\y\\a.mkv")]
    with pytest.raises(ls.SafetyError):
        log.created("bad\tname")


def test_settings_roundtrip_and_defaults():
    store = {}
    get = lambda s, k, d="": store.get((s, k), d)  # noqa: E731
    put = lambda s, k, v: store.__setitem__((s, k), v)  # noqa: E731
    d = ls.get_safety_settings(get)
    assert d.dry_run is True and d.recent_minutes == 10 and d.quarantine_root == ""
    ls.set_safety_settings(ls.SafetySettings(quarantine_root="F:\\_Quarantine\\Video", dry_run=False, recent_minutes=30), put)
    back = ls.get_safety_settings(get)
    assert (back.quarantine_root, back.dry_run, back.recent_minutes) == ("F:\\_Quarantine\\Video", False, 30)
    store[("safety", "recent_minutes")] = "junk"
    assert ls.get_safety_settings(get).recent_minutes == 10


def test_temp_names_recognised():
    assert is_app_temp_name("a.part.mkv") and is_app_temp_name("a.resize.mp4")
    assert not is_app_temp_name("My.Partial.Cut.mkv") and not is_app_temp_name("Part.mkv")


def test_ffmpeg_command_is_a_list_with_seek_before_input():
    cmd = ffmpeg_command("F:\\Video\\a.mkv", "-frames:v", "1", "out.jpg", seek=12.5)
    assert cmd[:4] == ["ffmpeg", "-v", "error", "-y"]
    assert cmd.index("-ss") < cmd.index("-i")
    assert cmd[cmd.index("-i") + 1] == "F:\\Video\\a.mkv"


def test_delete_after_conversion_setting_deletes_only_when_on_and_verified(tmp_path):
    lib, run = _setup(tmp_path)
    run.delete_after_conversion = True
    src = _old(lib / "a.avi", "old")
    final = str(lib / "a.mkv")
    assert run.convert(src, final, _writer(), ls.KIND_REMUXED, "r")
    assert not os.path.exists(src) and open(final, encoding="utf-8").read() == "new"
    assert not (tmp_path / "q").exists()  # nothing quarantined
    # a failed verification still keeps the original even with deletion on
    run2 = ls.SafeRun(library_root=str(lib), quarantine_root="", dry_run=False, delete_after_conversion=True, probe=lambda p: GOOD if "bad" not in p else None)
    bad = _old(lib / "bad.avi", "keep")
    assert run2.convert(bad, str(lib / "bad.mkv"), _writer(), ls.KIND_REMUXED, "r") is False
    assert os.path.exists(bad)


def test_delete_setting_defaults_off_and_roundtrips():
    store = {}
    get = lambda s, k, d="": store.get((s, k), d)  # noqa: E731
    assert ls.get_safety_settings(get).delete_after_conversion is False
    ls.set_safety_settings(ls.SafetySettings(delete_after_conversion=True), lambda s, k, v: store.__setitem__((s, k), v))
    assert ls.get_safety_settings(get).delete_after_conversion is True


def test_only_mp4_and_mkv_are_produced(tmp_path):
    lib, run = _setup(tmp_path)
    src = _old(lib / "a.avi")
    with pytest.raises(ls.SafetyError):
        run.convert(src, str(lib / "a.avi.webm"), _writer(), ls.KIND_REMUXED, "r")
    assert os.path.exists(src)
