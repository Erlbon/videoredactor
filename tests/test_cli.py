"""The command line (videocli/): every command on real small videos made with ffmpeg, text and --json output,
exit codes, --dry-run and sidecar files. Settings are test-isolated."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from core import config
from core.filename_pattern import field_text
from core.video_file import VideoFile
from redactor_common.cli import CliError
from videocli import cmd_files, cmd_redact
from videocli import files as cli_files
from videocli.main import main

SOURCES = [
    "-f", "lavfi", "-i", "testsrc=duration=3:size=160x120:rate=10",
    "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
]
ENCODE = ["-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest"]

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True, capture_output=True)


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("cli-videos")
    lang = ["-metadata:s:a:0", "language=eng"]  # without it the check adds a harmless NOTE
    _ffmpeg(*SOURCES, *ENCODE, *lang, "-movflags", "+faststart", str(d / "good.mp4"))
    _ffmpeg(*SOURCES, *ENCODE, *lang, str(d / "late_index.mp4"))  # index at the end: repairable
    data = (d / "good.mp4").read_bytes()
    (d / "truncated.mp4").write_bytes(data[: int(len(data) * 0.55)])  # damaged
    return d


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """A private settings file."""
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "videoredactor_settings.ini")


@pytest.fixture
def video(media, tmp_path):
    path = tmp_path / "show.mp4"
    shutil.copyfile(media / "good.mp4", path)
    return str(path)


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def run_json(capsys, *argv):
    code, out, _err = run(capsys, *argv, "--json")
    return code, json.loads(out)


def read(path):
    v = VideoFile(path=Path(path))
    v.load()
    return v


# --- info ---------------------------------------------------------------------------------------


def test_info_shows_technical_details_and_tags(video, capsys):
    run(capsys, "set", video, "-s", "show_title=Dune", "-s", "season=1", "-s", "episode=3", "-s", "title=Arrival")
    code, out, _ = run(capsys, "info", video)
    assert code == 0 and "mp4" in out and "160x120" in out and "show_title: Dune" in out and "episode_number: 3" in out


def test_info_json_fields_all_and_technical(video, tmp_path, capsys):
    run(capsys, "set", video, "-s", "title=Arrival", "-s", "genre=Drama", "-s", "type=tv")
    code, document = run_json(capsys, "info", str(tmp_path), "--fields", "title,Genre")
    row = document["results"][0]
    assert code == 0 and document["files"] == 1 and row["fields"] == {"title": "Arrival", "genre_tags": "Drama"}
    assert row["technical"]["container"] in ("mp4", "mov") and row["technical"]["resolution"] == "160x120"
    _code, document = run_json(capsys, "info", video, "--all")
    assert document["results"][0]["fields"]["content_type"] == "TV"


def test_info_with_nothing_found_is_a_usage_error(tmp_path):
    with pytest.raises(CliError, match="no video files"):
        main(["info", str(tmp_path / "nope.mp4")])


def test_info_reports_an_unreadable_video_and_exits_1(tmp_path, capsys):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"this is not a video")
    code, document = run_json(capsys, "info", str(bad))
    assert code == 1 and document["results"][0]["status"] != "ok"


# --- set --------------------------------------------------------------------------------------------


def test_set_changes_fields_and_saves_leaving_the_picture_alone(video, capsys):
    code, document = run_json(
        capsys, "set", video, "-s", "show_title=Dune", "-s", "Season Number=2", "-s", "type=TV", "-s", "year=2021",
        "-s", "rating=4", "--clear", "comment",
    )
    assert code == 0 and document["results"][0]["status"] == "changed"
    md = read(video).metadata
    assert (md.show_title, md.season_number, md.content_type.value, md.release_date, md.personal_rating) == ("Dune", 2, "TV", "2021", 4)
    assert md.resolution == "160x120" and md.duration_seconds


def test_set_dry_run_writes_nothing_and_same_value_is_unchanged(video, capsys):
    before = open(video, "rb").read()
    code, out, _ = run(capsys, "set", video, "-s", "title=Else", "-n")
    assert code == 0 and "planned" in out and "'' -> 'Else'" in out and open(video, "rb").read() == before
    run(capsys, "set", video, "-s", "title=Same")
    _code, document = run_json(capsys, "set", video, "-s", "title=Same")
    assert document["results"][0]["status"] == "unchanged"


@pytest.mark.parametrize("argv, message", [
    (["-s", "Nonsense=1"], "unknown field"),
    (["-s", "season=abc"], "whole number"),
    (["-s", "rating=9"], "1 to 5"),
    (["-s", "type=Documentary"], "must be one of"),
    (["-s", "year=21"], "YYYY"),
    (["-s", "language=english"], "language code"),
    (["-s", "novalue"], "FIELD=VALUE"),
    ([], "nothing to change"),
])
def test_set_refuses_bad_input_before_touching_anything(video, argv, message):
    before = open(video, "rb").read()
    with pytest.raises(CliError, match=message):
        main(["set", video, *argv])
    assert open(video, "rb").read() == before


def test_set_on_an_unreadable_video_fails_with_exit_1(tmp_path, capsys):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    code, document = run_json(capsys, "set", str(bad), "-s", "title=X")
    assert code == 1 and document["results"][0]["status"] == "failed"


# --- rename / move -----------------------------------------------------------------------------------------


def _tagged(video, capsys):
    run(capsys, "set", video, "-s", "show_title=Dune", "-s", "season=1", "-s", "episode=3", "-s", "title=Arrival")


def test_rename_with_padding_carries_the_sidecars(video, tmp_path, capsys):
    _tagged(video, capsys)
    poster = tmp_path / "show-poster.jpg"
    subtitle = tmp_path / "show.en.srt"
    poster.write_bytes(b"p")
    subtitle.write_bytes(b"s")
    code, document = run_json(capsys, "rename", video, "--zero-pad", "2")
    new_video = tmp_path / "Dune - S1E03 - Arrival.mp4"
    assert code == 0 and document["results"][0]["status"] == "renamed" and new_video.exists()
    assert (tmp_path / "Dune - S1E03 - Arrival-poster.jpg").exists() and (tmp_path / "Dune - S1E03 - Arrival.en.srt").exists()
    assert not poster.exists() and not subtitle.exists()
    assert "+2 companion" in document["results"][0]["message"]


def test_rename_dry_run_collisions_and_a_pattern_with_an_empty_required_field(media, tmp_path, capsys):
    a, b, untagged = (str(tmp_path / n) for n in ("a.mp4", "b.mp4", "u.mp4"))
    for p in (a, b, untagged):
        shutil.copyfile(media / "good.mp4", p)
    for p in (a, b):
        run(capsys, "set", p, "-s", "title=Same")
    _code, dry = run_json(capsys, "rename", a, b, "-p", "%title%", "-n")
    assert sorted(os.path.basename(r["new_path"]) for r in dry["results"]) == ["Same (2).mp4", "Same.mp4"]
    assert os.path.exists(a) and os.path.exists(b)
    _code, document = run_json(capsys, "rename", untagged, "-p", "%show_title% - %title%")
    row = document["results"][0]
    assert row["status"] == "skipped" and "empty %show_title%" in row["message"] and os.path.exists(untagged)


def test_move_into_folders_with_sidecars_then_copy(video, tmp_path, capsys):
    _tagged(video, capsys)
    (tmp_path / "show-poster.jpg").write_bytes(b"p")
    lib = tmp_path / "library"
    lib.mkdir()
    pattern = "%show_title%/Season %season_number%/%title%"
    _code, dry = run_json(capsys, "move", video, "-p", pattern, "--root", str(lib), "-n")
    assert dry["results"][0]["status"] == "planned" and os.path.exists(video)
    code, document = run_json(capsys, "move", video, "-p", pattern, "--root", str(lib))
    folder = lib / "Dune" / "Season 1"
    assert code == 0 and document["results"][0]["status"] == "moved"
    assert (folder / "Arrival.mp4").exists() and (folder / "Arrival-poster.jpg").exists() and not os.path.exists(video)
    _code, document = run_json(capsys, "move", str(folder / "Arrival.mp4"), "-p", "Copies/%title%", "--root", str(lib), "--copy")
    assert document["results"][0]["status"] == "copied" and (lib / "Copies" / "Arrival.mp4").exists()


def test_move_checks_the_library_folder_and_reads_the_saved_one(video, tmp_path):
    with pytest.raises(CliError, match="--root"):
        main(["move", video, "-p", "%title%"])
    with pytest.raises(CliError, match="does not exist"):
        main(["move", video, "-p", "%title%", "--root", str(tmp_path / "missing")])
    lib = tmp_path / "lib"
    lib.mkdir()
    config.set_setting("rename", "library_root", str(lib))
    assert main(["move", video, "-p", "%title%", "-n", "-q"]) == 0


# --- check -------------------------------------------------------------------------------------------------


def test_check_reports_ok_and_a_problem_with_the_exit_code(media, capsys):
    code, document = run_json(capsys, "check", str(media / "good.mp4"))
    assert code == 0 and document["problems"] == 0 and document["results"][0]["status"] == "CHECKED OK"
    code, document = run_json(capsys, "check", str(media / "truncated.mp4"))
    row = document["results"][0]
    assert code == 1 and row["problem"] is True and row["status"] in ("DAMAGED", "REPAIRABLE") and row["findings"]


def test_check_repair_fixes_a_repairable_file_and_dry_run_only_says_so(media, tmp_path, capsys):
    path = tmp_path / "late.mp4"
    shutil.copyfile(media / "late_index.mp4", path)
    code, document = run_json(capsys, "check", str(path))
    assert code == 1 and document["results"][0]["status"] == "REPAIRABLE"
    before = path.read_bytes()
    _code, dry = run_json(capsys, "check", str(path), "--repair", "-n")
    assert "would be repaired" in dry["results"][0]["message"] and path.read_bytes() == before
    bin_dir = tmp_path / "trash"
    code, document = run_json(capsys, "check", str(path), "--repair", "--trash-dir", str(bin_dir))
    row = document["results"][0]
    assert code == 0 and row["repaired"] is True and row["status"] == "CHECKED OK" and row["problem"] is False
    assert len(os.listdir(bin_dir)) == 1  # the original, kept


def test_check_never_repairs_a_damaged_file(media, tmp_path, capsys):
    path = tmp_path / "damaged.mp4"
    shutil.copyfile(media / "truncated.mp4", path)
    before = path.read_bytes()
    bin_dir = tmp_path / "trash"
    code, document = run_json(capsys, "check", str(path), "--repair", "--trash-dir", str(bin_dir))
    row = document["results"][0]
    assert row["repaired"] is False and path.read_bytes() == before and not bin_dir.exists()
    assert code == 1 and row["problem"] is True


def test_check_stamp_records_the_result_in_the_file(video, capsys):
    code, document = run_json(capsys, "check", video, "--stamp")
    assert code == 0 and document["results"][0]["stamped"] is True
    assert read(video).metadata.scan_stamp.startswith("CHECKED OK")
    _code, info = run_json(capsys, "info", video)
    assert info["results"][0]["check"] in ("CHECKED OK", "")  # the stamp is read back


def test_check_text_output(media, capsys):
    code, out, _ = run(capsys, "check", str(media / "good.mp4"))
    assert code == 0 and "[CHECKED OK]" in out


# --- redact --------------------------------------------------------------------------------------------------


def test_redact_lists_its_steps(capsys):
    code, document = run_json(capsys, "redact", "--list-steps")
    steps = {r["step"]: r["enabled"] for r in document["results"]}
    assert code == 0 and steps["check_repair"] is True and steps["move_into_folders"] is False
    _code, document = run_json(capsys, "redact", "--list-steps", "--disable", "check_repair", "--enable", "move_into_folders")
    steps = {r["step"]: r["enabled"] for r in document["results"]}
    assert steps["check_repair"] is False and steps["move_into_folders"] is True


def _only(capsys, step):
    steps = [r["step"] for r in run_json(capsys, "redact", "--list-steps")[1]["results"]]
    return [arg for s in steps if s != step for arg in ("--disable", s)] + ["--enable", step]


def test_redact_with_every_step_off_changes_nothing(video, tmp_path, capsys):
    before = open(video, "rb").read()
    steps = [r["step"] for r in run_json(capsys, "redact", "--list-steps")[1]["results"]]
    argv = ["redact", video, "--trash-dir", str(tmp_path / "trash")]
    for step in steps:
        argv += ["--disable", step]
    code, document = run_json(capsys, *argv)
    assert code == 0 and document["files"] == 1 and document["failed"] == 0
    assert document["results"][0]["status"] == "unchanged" and open(video, "rb").read() == before


def test_redact_runs_a_step_saves_in_place_and_keeps_the_original(media, tmp_path, capsys):
    path = tmp_path / "late.mp4"
    shutil.copyfile(media / "late_index.mp4", path)
    bin_dir = tmp_path / "trash"
    code, document = run_json(capsys, "redact", str(path), *_only(capsys, "check_repair"), "--trash-dir", str(bin_dir))
    entry = document["results"][0]
    assert code == 0 and entry["status"] == "changed" and entry["applied"]
    assert len(os.listdir(bin_dir)) >= 1
    _code, again = run_json(capsys, "check", str(path))
    assert again["results"][0]["status"] == "CHECKED OK"


def test_redact_rejects_unknown_steps_and_thresholds(video):
    with pytest.raises(CliError, match="unknown step"):
        main(["redact", video, "--disable", "nonsense"])
    with pytest.raises(CliError, match="threshold"):
        main(["redact", video, "--threshold", "250"])
    with pytest.raises(CliError, match="give the video"):
        main(["redact"])


# --- one exe ------------------------------------------------------------------------------------------


def test_a_command_name_starts_the_command_line_and_a_path_starts_the_window():
    from videocli import COMMANDS, cli_requested

    assert set(COMMANDS) == {"info", "set", "rename", "move", "check", "redact"}
    assert cli_requested(["videoredactor.exe", "info", "x.mp4"]) and cli_requested(["videoredactor.exe", "--version"])
    assert not cli_requested(["videoredactor.exe"]) and not cli_requested(["videoredactor.exe", "D:/Films/a.mkv"])


def test_the_app_entry_point_runs_the_command_line_without_a_window(video, monkeypatch, capsys):
    import main as app

    monkeypatch.setattr(app.sys, "argv", ["videoredactor", "info", video, "--json"])
    monkeypatch.setattr("redactor_common.gui.app_bootstrap.run_app", lambda **kw: pytest.fail("the window was started"))
    monkeypatch.setattr(app, "run_app", lambda **kw: pytest.fail("the window was started"))
    assert app.main() == 0
    assert json.loads(capsys.readouterr().out)["results"][0]["technical"]["container"] in ("mp4", "mov")


def test_the_app_entry_point_still_starts_the_window_for_no_command(monkeypatch):
    import main as app

    started = []
    monkeypatch.setattr(app.sys, "argv", ["videoredactor"])
    monkeypatch.setattr(app, "run_app", lambda **kw: started.append(kw["app_name"]) or 0)
    monkeypatch.setattr(app.api_keys, "migrate_legacy_keys", lambda: None)
    monkeypatch.setitem(__import__("sys").modules, "gui.main_window", type("M", (), {"MainWindow": object}))
    assert app.main() == 0 and started


def test_output_writes_the_result_to_a_file_for_scripts(video, tmp_path, capsys):
    target = tmp_path / "result.json"
    assert main(["info", video, "--json", "--output", str(target)]) == 0 and capsys.readouterr().out == ""
    assert json.loads(target.read_text(encoding="utf-8"))["results"][0]["technical"]["container"] in ("mp4", "mov")


# --- the documentation covers every option --------------------------------------------------------------


def _readme_cli_section() -> str:
    text = (Path(__file__).parent.parent / "ABOUT.md").read_text(encoding="utf-8")
    start = text.index("## Command line")
    end = text.find("\n## ", start + 5)
    return text[start:end if end != -1 else None]


def test_the_readme_documents_every_command_option_step_and_field():
    import argparse

    from core.redact_steps import build_catalogue
    from videocli.fields import FIELD_NAMES
    from videocli.main import build_parser

    section = _readme_cli_section()
    parser = build_parser()
    missing = [o for action in parser._actions for o in action.option_strings if o not in section]
    subparsers = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    for name, sub in subparsers.choices.items():
        if f"### {name}" not in section:
            missing.append(f"### {name}")
        missing += [f"{name} {o}" for action in sub._actions for o in action.option_strings if o not in section]
    missing += [f"step {s.key}" for s in build_catalogue() if not s.hidden and s.key not in section]
    missing += [f"field {f}" for f in FIELD_NAMES if f not in section]
    assert missing == [], f"the Command line section of ABOUT.md does not mention: {missing}"


def test_the_readme_lists_the_exit_codes_and_the_scripting_ways():
    section = _readme_cli_section()
    for code in ("| 0 |", "| 1 |", "| 2 |", "| 70 |", "| 130 |"):
        assert code in section
    for way in ("start /wait", "Start-Process", "Out-Null", "--output"):
        assert way in section


# --- second review ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("argv,message", [
    (["-s", "title=bad\x01char"], "control character"),
    (["-s", "season=\u00b2"], "whole number"),
    (["-s", "year=2026-02-30"], "real date"),
    (["-s", "episode=100000"], "too large"),
])
def test_set_refuses_values_no_file_can_store(video, argv, message):
    before = open(video, "rb").read()
    with pytest.raises(CliError, match=message):
        main(["set", video, *argv])
    assert open(video, "rb").read() == before


def test_set_treats_a_padded_number_as_the_number(video, capsys):
    run_json(capsys, "set", video, "-s", "season=7")
    _code, document = run_json(capsys, "set", video, "-s", "season=007")
    assert document["results"][0]["status"] == "unchanged"


def test_an_explicitly_named_temp_style_file_is_kept(media, tmp_path, capsys):
    name = ".show.redact-abcd1234.mp4"
    path = tmp_path / name
    shutil.copyfile(media / "good.mp4", path)
    code, document = run_json(capsys, "info", str(path))
    assert code == 0 and document["files"] == 1
    with pytest.raises(CliError, match="no video files"):
        main(["info", str(tmp_path)])  # found through a folder, it is the app's own leftover and is left out


def test_rename_defaults_come_from_the_saved_settings(video, tmp_path, capsys):
    config.set_setting("rename", "zero_pad", "1")
    config.set_setting("rename", "zero_pad_width", "3")
    run_json(capsys, "set", video, "-s", "show=Show", "-s", "season=1", "-s", "episode=4", "-s", "title=Pilot")
    _code, document = run_json(capsys, "rename", video, "-p", "%show_title% %episode_number%", "-n")
    assert document["results"][0]["new_path"].endswith("Show 004.mp4")
    _code, document = run_json(capsys, "rename", video, "-p", "%show_title% %episode_number%", "--zero-pad", "2", "-n")
    assert document["results"][0]["new_path"].endswith("Show 04.mp4")


def test_check_repair_with_an_unusable_trash_dir_is_a_clean_failure_and_no_stamp(media, tmp_path, capsys):
    path = tmp_path / "late.mp4"
    shutil.copyfile(media / "late_index.mp4", path)
    blocker = tmp_path / "bin"
    blocker.write_bytes(b"a file, not a folder")
    before = path.read_bytes()
    code, document = run_json(capsys, "check", str(path), "--repair", "--stamp", "--trash-dir", str(blocker))
    row = document["results"][0]
    assert code == 1 and row["repaired"] is False and row["stamped"] is False
    assert "repair failed" in row["message"] and path.exists()
    assert path.read_bytes() == before  # nothing stamped onto the failed file
