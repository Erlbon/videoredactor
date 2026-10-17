"""Tests for core/deep_scan.py, core/cut_damaged.py and their Check Files integration.

Synthetic files made with ffmpeg (testsrc2 + sine, H.264 + AAC, 60 s, a keyframe every 2 s), once with the index
(moov) at the start and once at the end; aligned 256 KiB blocks of the media data are overwritten with zero bytes,
which is what missing torrent pieces look like. One test runs against a real damaged file when it exists on this
machine (skipped otherwise)."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading

import pytest

from core import cut_damaged as cd
from core import deep_scan as ds
from core import file_check as fc

BLOCK = 256 * 1024
REAL_DIR = r"C:\Temp\Claude\savebug\real"
REAL_DAMAGED = os.path.join(REAL_DIR, "s01.mp4")


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True, capture_output=True)


def _make_video(dest, moov_at_start: bool):
    flags = ["-movflags", "+faststart"] if moov_at_start else []
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=duration=60:size=640x360:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=60:sample_rate=44100",
        "-c:v", "libx264", "-preset", "ultrafast", "-b:v", "1500k", "-g", "60", "-keyint_min", "60",
        "-sc_threshold", "0", "-bf", "2", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k",
        "-metadata:s:a:0", "language=eng",
        *flags, str(dest),
    )


@pytest.fixture(scope="module")
def pristine(tmp_path_factory):
    d = tmp_path_factory.mktemp("pristine")
    _make_video(d / "start.mp4", True)
    _make_video(d / "end.mp4", False)
    assert fc.mp4_index_at_end(str(d / "start.mp4")) is False
    assert fc.mp4_index_at_end(str(d / "end.mp4")) is True
    return d


def _mdat(path):
    ranges, container, _approx = ds.media_ranges(str(path))
    assert container == "mp4" and len(ranges) == 1
    return ranges[0]


def _zero(path, start, end):
    with open(path, "r+b") as handle:
        handle.seek(start)
        handle.write(b"\x00" * (end - start))


def _aligned_blocks(path, count, first_fraction=0.2, spacing_fraction=0.25):
    """`count` aligned 256 KiB blocks fully inside the media data, spread over it."""
    start, end = _mdat(path)
    blocks = []
    for i in range(count):
        offset = start + int((end - start) * (first_fraction + spacing_fraction * i))
        offset -= offset % BLOCK
        if offset < start:
            offset += BLOCK
        blocks.append((offset, offset + BLOCK))
    return blocks


def _copy(pristine, tmp_path, name, new_name=None):
    target = tmp_path / (new_name or name)
    shutil.copyfile(pristine / name, target)
    return target


def _decode_messages(path):
    done = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True, text=True)
    return [line for line in done.stderr.splitlines() if line.strip()]


def _duration(path):
    done = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    return float(json.loads(done.stdout)["format"]["duration"])


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _close(run, block, slack=64):
    """The zero run is the block plus at most a few legitimate zero bytes at its edges."""
    return block[0] - slack <= run.start <= block[0] and block[1] <= run.end <= block[1] + slack


# ---------------------------------------------------------------------------
# find_zero_runs: the streaming pass
# ---------------------------------------------------------------------------

def test_zero_runs_are_found_across_chunk_boundaries(tmp_path):
    data = bytearray(b"\x01" * 100_000)
    data[10_000:30_000] = bytes(20_000)      # inside one chunk
    data[39_990:60_010] = bytes(20_020)      # crosses two chunk boundaries (chunk = 16 KiB)
    data[70_000:70_900] = bytes(900)         # shorter than the minimum: ignored
    data[99_000:] = bytes(1000)              # ends at the end of the data, too short
    path = tmp_path / "blob.bin"
    path.write_bytes(bytes(data))
    runs = ds.find_zero_runs(str(path), [(0, len(data))], min_run=10_000, chunk_bytes=16 * 1024)
    assert runs == [(10_000, 30_000), (39_990, 60_010)]
    # A run that reaches the end of the range counts when long enough.
    path.write_bytes(b"\x01" * 50 + bytes(20_000))
    assert ds.find_zero_runs(str(path), [(0, 20_050)], min_run=10_000, chunk_bytes=4096) == [(50, 20_050)]


def test_zero_scan_reports_progress_and_cancels(tmp_path):
    path = tmp_path / "blob.bin"
    path.write_bytes(b"\x01" * 100_000)
    seen = []
    ds.find_zero_runs(str(path), [(0, 100_000)], progress=lambda d, t: seen.append((d, t)), chunk_bytes=30_000)
    assert seen[-1] == (100_000, 100_000) and len(seen) == 4
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ds.DeepScanCancelled):
        ds.find_zero_runs(str(path), [(0, 100_000)], cancel=cancel)


def test_a_zero_run_in_no_packet_is_harmless():
    packets = [ds._Packet(0, 100, 1000, 0.0, 0.0, 0.04, True), ds._Packet(0, 5000, 1000, 0.04, 0.04, 0.04, False)]
    runs = [ds.ZeroRun(2000, 4500, in_media=False)]

    class NoRead:
        def seek(self, *_a):
            raise AssertionError("no packet is hit, nothing is read")

    ds._mark_damaged(packets, {0: "video"}, runs, NoRead())
    assert not any(p.damaged for p in packets) and not runs[0].in_media


# ---------------------------------------------------------------------------
# The deep scan on real (synthetic) files
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["start.mp4", "end.mp4"])
def test_clean_file_gives_no_findings(pristine, name):
    result = ds.deep_scan(str(pristine / name))
    assert result.zero_runs == [] and result.spans == [] and not result.has_damage
    check = fc.quick_check(str(pristine / name), deep=True)
    # (the moov-at-end file is REPAIRABLE for its index position, nothing else)
    assert check.status == ("CHECKED OK" if name == "start.mp4" else "REPAIRABLE")
    assert check.deep is not None and not check.can_cut and not check.has_data_damage


@pytest.mark.parametrize("name", ["start.mp4", "end.mp4"])
def test_deep_scan_finds_exactly_the_zeroed_blocks(pristine, tmp_path, name):
    path = _copy(pristine, tmp_path, name)
    blocks = _aligned_blocks(path, 3)
    for start, end in blocks:
        _zero(path, start, end)
    result = ds.deep_scan(str(path))
    assert len(result.zero_runs) == 3
    for run, block in zip(result.zero_runs, blocks):
        assert _close(run, block) and run.in_media
    assert result.has_damage and result.packets_checked
    # Rough time: a block of 256 KiB at ~1.7 Mbit/s is ~1.2 s; each span covers its block, snapped to whole GOPs (2 s).
    media_start, media_end = _mdat(path)
    for span, block in zip(result.spans, blocks):
        approx_time = 60.0 * (block[0] - media_start) / (media_end - media_start)
        assert span.start_s - 6.0 <= approx_time <= span.end_s + 6.0
        assert span.start_s % 2 == pytest.approx(0, abs=0.1) or span.start_s % 2 == pytest.approx(2, abs=0.1)
    assert 0 < result.total_seconds < 60 and 0 < result.percent < 100
    assert result.video_seconds > 0
    check = fc.quick_check(str(path), deep=True)
    assert check.status == "DAMAGED" and not check.can_repair and check.can_cut
    finding = next(f for f in check.findings if f.code == "data_damage")
    assert finding.message.startswith("3 zero-filled block(s) inside the video: about ")
    assert "s of video and" in finding.message and "s of audio are missing (first at " in finding.message
    assert "REPAIRABLE" not in check.status
    assert "10 more" not in fc.deep_tooltip(check.deep) and fc.deep_tooltip(check.deep).count(" - ") == len(check.deep.spans)


def test_the_ordinary_check_sees_zero_blocks_at_the_edges_cheaply(pristine, tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "EDGE_BYTES", 1024 * 1024)  # the synthetic file is smaller than 2 x 8 MB
    path = _copy(pristine, tmp_path, "end.mp4")
    start, end = _mdat(path)
    first = start + BLOCK - (start % BLOCK)
    _zero(path, first, first + BLOCK)
    check = fc.quick_check(str(path))
    assert check.deep is None
    assert check.status == "DAMAGED" and not check.can_repair
    assert any(f.code == "zero_blocks_edge" and "run Deep scan" in f.message for f in check.findings)
    # A block in the middle is invisible to the cheap check, found by the deep one.
    middle = tmp_path / "middle.mp4"
    shutil.copyfile(pristine / "end.mp4", middle)
    block = _aligned_blocks(middle, 1, first_fraction=0.5)[0]
    _zero(middle, *block)
    assert not any(f.code == "zero_blocks_edge" for f in fc.quick_check(str(middle)).findings)
    assert fc.quick_check(str(middle), deep=True).status == "DAMAGED"


def test_free_atom_padding_is_not_flagged(pristine, tmp_path):
    """A 1 MiB 'free' box of zeros between the media data and the index is legitimate padding."""
    src = (pristine / "end.mp4").read_bytes()
    start, end = _mdat(pristine / "end.mp4")
    free = (1024 * 1024).to_bytes(4, "big") + b"free" + bytes(1024 * 1024 - 8)
    path = tmp_path / "padded.mp4"
    path.write_bytes(src[:end] + free + src[end:])
    result = ds.deep_scan(str(path))
    assert result.zero_runs == [] and not result.has_damage
    assert fc.quick_check(str(path), deep=True).status in ("CHECKED OK", "REPAIRABLE", "NOTE")


def _audio_only_region(path):
    """A byte range made of consecutive AUDIO packets only (the media data interleaves them with video)."""
    done = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "packet=stream_index,pos,size", "-of", "compact=p=0", str(path)],
        capture_output=True, text=True,
    )
    packets = []
    for line in done.stdout.splitlines():
        fields = dict(part.split("=") for part in line.split("|") if "=" in part)
        packets.append((int(fields["pos"]), int(fields["size"]), int(fields["stream_index"])))
    packets.sort()
    groups, current = [], []
    for pos, size, stream in packets:
        if stream == 1 and (not current or current[-1][0] + current[-1][1] == pos):
            current.append((pos, size))
        else:
            if current:
                groups.append(current)
            current = [(pos, size)] if stream == 1 else []
    best = max(groups, key=lambda g: sum(s for _p, s in g))
    return best[0][0], best[-1][0] + best[-1][1]


def test_damage_in_audio_only_is_reported_as_audio(pristine, tmp_path):
    path = _copy(pristine, tmp_path, "end.mp4")
    start, end = _audio_only_region(path)
    assert end - start >= 1024
    _zero(path, start, end)
    result = ds.deep_scan(str(path), min_run=1024)
    assert result.has_damage and result.audio_affected and result.audio_seconds > 0
    assert result.video_seconds == 0
    check = fc.quick_check(str(path))  # the ordinary check (64 KiB minimum) does not see so small a hole
    assert check.status in ("CHECKED OK", "REPAIRABLE")


def test_damage_at_the_first_keyframe_starts_the_span_at_zero(pristine, tmp_path):
    path = _copy(pristine, tmp_path, "end.mp4")
    start, end = _mdat(path)
    boundary = start + BLOCK - (start % BLOCK)
    _zero(path, start, boundary)  # from the very first byte of the media data to the next 256 KiB boundary
    result = ds.deep_scan(str(path))
    assert result.has_damage and result.spans[0].start_s == pytest.approx(0.0, abs=0.01)
    assert result.zero_runs[0].start <= start + 64


def test_damage_next_to_the_index(pristine, tmp_path):
    path = _copy(pristine, tmp_path, "end.mp4")
    start, end = _mdat(path)
    _zero(path, end - BLOCK, end)  # the last 256 KiB of the media data, right in front of the moov
    result = ds.deep_scan(str(path))
    assert result.has_damage and len(result.media_runs) == 1
    assert result.zero_runs[0].end == end
    assert result.spans[-1].end_s >= 59.0  # runs to the end of the file


# ---------------------------------------------------------------------------
# Cut Out Damaged Parts
# ---------------------------------------------------------------------------

def _bin(tmp_path):
    """A Recycle Bin test double: moves the file into a folder."""
    bin_dir = tmp_path / "recycle"
    bin_dir.mkdir(exist_ok=True)
    moved = []

    def trash(path):
        target = bin_dir / os.path.basename(path)
        shutil.move(path, target)
        moved.append(str(target))

    return trash, moved


def _tagged_copy(pristine, tmp_path, name):
    from core.mp4_backend import write_mp4_cover, write_mp4_metadata
    from core.video_metadata import VideoMetadata

    path = _copy(pristine, tmp_path, name)
    write_mp4_metadata(str(path), VideoMetadata(title="The Gift", director="Tom King", collection="Batman", genre_tags="Action"))
    _ffmpeg("-f", "lavfi", "-i", "color=red:s=32x32", "-frames:v", "1", str(tmp_path / "cover.jpg"))
    cover = (tmp_path / "cover.jpg").read_bytes()
    (tmp_path / "cover.jpg").unlink()
    write_mp4_cover(str(path), cover)
    return path, cover


@pytest.mark.parametrize("name", ["start.mp4", "end.mp4"])
def test_cut_out_removes_the_damage_keeps_tags_and_poster(pristine, tmp_path, name):
    from core.mp4_backend import read_mp4_cover, read_mp4_metadata

    path, cover = _tagged_copy(pristine, tmp_path, name)
    original_meta = read_mp4_metadata(str(path))
    blocks = _aligned_blocks(path, 2)
    for start, end in blocks:
        _zero(path, start, end)
    assert _decode_messages(path), "the damaged file must produce decode errors before the cut"
    before = fc.quick_check(str(path), deep=True)
    assert before.can_cut
    original_duration = _duration(path)
    trash, moved = _bin(tmp_path)
    outcome = cd.cut_out_damaged(str(path), before, trash=trash)
    assert moved == [str(tmp_path / "recycle" / path.name)] and os.path.exists(moved[0])  # the original is in the bin
    assert _decode_messages(path) == []                                                    # full decode: zero errors
    assert outcome.decode_errors == 0 and "0 errors" in outcome.verify
    expected = original_duration - sum(s.seconds for s in before.deep.spans)
    assert _duration(path) == pytest.approx(expected, abs=0.5 + 0.1 * len(before.deep.spans))
    assert outcome.new_duration == pytest.approx(_duration(path), abs=0.01)
    assert outcome.removed_seconds == pytest.approx(sum(s.seconds for s in before.deep.spans))
    assert read_mp4_metadata(str(path)) == original_meta
    assert read_mp4_cover(str(path)) == cover
    # Audio and video agree.
    info = cd._probe(str(path), fc._run)
    times = cd._stream_times(info)
    assert abs(times["video"][1] - times["audio"][1]) < 0.3
    # The result is clean for a fresh deep scan, and nothing else is left in the folder.
    assert fc.quick_check(str(path), deep=True).status in ("CHECKED OK", "NOTE")
    assert sorted(os.listdir(tmp_path)) == sorted([path.name, "recycle"])


def test_cut_out_when_the_first_keyframe_is_damaged_and_next_to_the_index(pristine, tmp_path):
    path = _copy(pristine, tmp_path, "end.mp4")
    start, end = _mdat(path)
    _zero(path, start, start + BLOCK - (start % BLOCK))
    _zero(path, end - BLOCK, end)
    before = fc.quick_check(str(path), deep=True)
    trash, moved = _bin(tmp_path)
    outcome = cd.cut_out_damaged(str(path), before, trash=trash)
    assert _decode_messages(path) == []
    assert 1.0 < outcome.new_duration < 58.0


def test_cut_out_of_audio_only_damage_keeps_streams_in_sync(pristine, tmp_path):
    path = _copy(pristine, tmp_path, "end.mp4")
    start, end = _audio_only_region(path)
    _zero(path, start, end)
    before = fc.quick_check(str(path), deep=True)  # 64 KiB minimum: invisible
    assert not before.can_cut
    deep = ds.deep_scan(str(path), min_run=1024)
    before.deep = deep
    before.findings.append(fc.Finding("data_damage", fc.DAMAGED, "test"))
    trash, _moved = _bin(tmp_path)
    cd.cut_out_damaged(str(path), before, trash=trash)
    assert _decode_messages(path) == []
    times = cd._stream_times(cd._probe(str(path), fc._run))
    assert abs(times["video"][1] - times["audio"][1]) < 0.3


def _damaged_with_check(pristine, tmp_path, name="end.mp4"):
    path = _copy(pristine, tmp_path, name)
    for start, end in _aligned_blocks(path, 2):
        _zero(path, start, end)
    return path, fc.quick_check(str(path), deep=True)


def test_a_failed_verification_leaves_the_original_untouched(pristine, tmp_path):
    path, before = _damaged_with_check(pristine, tmp_path)
    digest = _sha(path)

    def lying_ffmpeg(args, cancel, timeout):
        code, stderr = cd._run_ffmpeg(args, cancel, timeout)
        if "-f" in args and "null" in args:
            return 0, "[h264 @ 0x1] error while decoding MB 1 1"
        return code, stderr

    trash, moved = _bin(tmp_path)
    with pytest.raises(fc.RepairError, match="decode errors"):
        cd.cut_out_damaged(str(path), before, ffmpeg=lying_ffmpeg, trash=trash)
    assert moved == [] and _sha(path) == digest
    assert os.listdir(tmp_path) == [path.name, "recycle"] or sorted(os.listdir(tmp_path)) == sorted([path.name, "recycle"])


def test_cancel_leaves_the_original_untouched(pristine, tmp_path):
    path, before = _damaged_with_check(pristine, tmp_path)
    digest = _sha(path)
    cancel = threading.Event()
    calls = []

    def cancelling_ffmpeg(args, cancel_event, timeout):
        calls.append(args)
        if len(calls) == 2:
            cancel.set()  # the user clicks Cancel while the second part is copied
        return cd._run_ffmpeg(args, cancel_event, timeout)

    trash, moved = _bin(tmp_path)
    with pytest.raises(cd.CutCancelled):
        cd.cut_out_damaged(str(path), before, ffmpeg=cancelling_ffmpeg, trash=trash, cancel=cancel)
    assert moved == [] and _sha(path) == digest
    assert sorted(os.listdir(tmp_path)) == sorted([path.name, "recycle"])
    # Cancelled before anything ran, too.
    cancel2 = threading.Event()
    cancel2.set()
    with pytest.raises(cd.CutCancelled):
        cd.cut_out_damaged(str(path), before, trash=trash, cancel=cancel2)
    assert _sha(path) == digest


def test_a_file_changed_since_the_scan_is_refused(pristine, tmp_path):
    path, before = _damaged_with_check(pristine, tmp_path)
    with open(path, "ab") as handle:
        handle.write(b"x")
    trash, moved = _bin(tmp_path)
    with pytest.raises(fc.RepairError, match="changed since"):
        cd.cut_out_damaged(str(path), before, trash=trash)
    assert moved == []


def test_nothing_to_cut_is_refused(pristine, tmp_path):
    path = _copy(pristine, tmp_path, "end.mp4")
    with pytest.raises(fc.RepairError, match="no damaged parts"):
        cd.cut_out_damaged(str(path), fc.quick_check(str(path), deep=True), trash=lambda _p: None)


def test_plan_wording_and_the_quarter_warning():
    deep = ds.DeepScanResult(duration=100.0, spans=[ds.DamagedSpan(10, 20), ds.DamagedSpan(61, 66)])
    check = fc.CheckResult(deep=deep, declared_seconds=100.0)
    plan = cd.plan_cut(check)
    assert plan.removed_seconds == 15 and not plan.heavy
    text = plan.sentence()
    assert text.startswith("This removes 2 parts totalling 0:15 (0:10-0:20, 1:01-1:06) of video and audio")
    assert "the rest is kept unchanged. The original goes to the Recycle Bin." in text
    heavy = cd.plan_cut(fc.CheckResult(deep=ds.DeepScanResult(duration=100.0, spans=[ds.DamagedSpan(0, 26)]),
                                       declared_seconds=100.0))
    assert heavy.heavy
    assert not cd.plan_cut(fc.CheckResult(deep=ds.DeepScanResult(
        duration=100.0, spans=[ds.DamagedSpan(0, 25)]), declared_seconds=100.0)).heavy


# ---------------------------------------------------------------------------
# The real damaged file (skipped where the sample is not available)
# ---------------------------------------------------------------------------

REAL_OFFSETS = [1835008, 4194304, 4980736, 5505024, 12058624, 12845056, 23855104, 25952256]


@pytest.mark.skipif(not os.path.exists(REAL_DAMAGED), reason="the real damaged sample is not on this machine")
def test_real_file_deep_scan_and_cut(tmp_path, capsys):
    result = ds.deep_scan(REAL_DAMAGED)
    assert [r.start for r in result.zero_runs] == REAL_OFFSETS
    assert all(r.length == BLOCK and r.in_media for r in result.zero_runs)
    assert result.has_damage and result.audio_affected
    clean = os.path.join(REAL_DIR, "s04.mp4")
    if os.path.exists(clean):
        assert ds.deep_scan(clean).zero_runs == []
    copy = tmp_path / "s01.mp4"
    shutil.copyfile(REAL_DAMAGED, copy)
    before = fc.quick_check(str(copy), deep=True)
    assert before.status == "DAMAGED" and before.can_cut and not before.can_repair
    errors_before = len(_decode_messages(copy))
    trash, moved = _bin(tmp_path)
    original_duration = _duration(copy)
    outcome = cd.cut_out_damaged(str(copy), before, trash=trash)
    errors_after = len(_decode_messages(copy))
    with capsys.disabled():
        print(f"\nREAL FILE: {len(result.zero_runs)} zero runs; removed {outcome.removed_seconds:.1f} s in "
              f"{len(outcome.removed)} parts; duration {original_duration:.1f} -> {outcome.new_duration:.1f}; "
              f"decode messages {errors_before} -> {errors_after}")
    assert errors_before > 100 and errors_after == 0
    assert outcome.new_duration == pytest.approx(original_duration - outcome.removed_seconds,
                                                 abs=0.5 + 0.1 * len(outcome.removed))


# ---------------------------------------------------------------------------
# The Check Files dialog parts, in the real window (offscreen: nothing opens on the desktop)
# ---------------------------------------------------------------------------

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def window(monkeypatch, tmp_path):
    import core.config as config
    import gui.main_window as mw

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    monkeypatch.setattr(mw.MainWindow, "_restore_last_folder_on_startup", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: None)
    w = mw.MainWindow()
    yield w
    for vf in w.video_files:
        vf.dirty = False
    w.close()


def test_the_deep_scan_checkbox_defaults_off_and_is_remembered(window, monkeypatch):
    from gui import file_check_dialog as flow

    seen = []

    def fake_exec(self):
        seen.append(self.checkbox.isChecked())
        self.checkbox.setChecked(not seen[-1] if len(seen) == 1 else self.checkbox.isChecked())
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", fake_exec)
    assert flow.ask_check_options(window, 3) is True     # default off, the test ticks it
    assert flow.ask_check_options(window, 3) is True     # remembered: ticked when it opens again
    assert seen == [False, True]
    assert flow.DEEP_SCAN_LABEL == "Deep scan (reads the whole file)"
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    assert flow.ask_check_options(window, 3) is None     # cancelled: no check, nothing remembered changes


def test_the_deep_check_and_cut_flow_in_the_real_window(pristine, tmp_path, window, monkeypatch):
    import functools

    from core.video_file import VideoFile
    from gui import file_check_dialog as flow

    path = _copy(pristine, tmp_path, "end.mp4")
    for start, end in _aligned_blocks(path, 2):
        _zero(path, start, end)
    vf = VideoFile(path=path)
    vf.load()
    window.video_files = [vf]
    window._refresh_table_rows()
    trash, moved = _bin(tmp_path)
    monkeypatch.setattr(flow, "cut_out_damaged", functools.partial(cd.cut_out_damaged, trash=trash))
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Accepted)  # the options dialog: ticked below
    monkeypatch.setattr(flow, "ask_check_options", lambda w, n: True)
    shown = {}

    def results_exec(self):
        shown["cuttable"] = [f.path.name for f in self.cuttable]
        shown["repairable"] = [f.path.name for f in self.repairable]
        shown["cut_text"] = self.cut_button.text()
        shown["finding"] = self.table.item(0, 2).text()
        shown["tooltip"] = self.table.item(0, 2).toolTip()
        self.cut_button.click()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(flow.FileCheckResultsDialog, "exec", results_exec)
    confirmations = []

    def box_exec(self):
        confirmations.append((self.text(), self.defaultButton().text()))
        for button in self.buttons():
            if button.text() == flow.CUT_BUTTON:
                button.click()
        return 0

    said = []
    monkeypatch.setattr(QMessageBox, "exec", box_exec)
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text, *a, **k: said.append(text))
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a, **k: said.append("WARN " + text))
    window._on_check_files()

    assert shown["cuttable"] == [path.name] and shown["repairable"] == []  # a remux can't fix this
    assert shown["cut_text"] == "Cut Out Damaged Parts..."
    assert "zero-filled block(s) inside the video: about" in shown["finding"]
    assert shown["tooltip"].count(" - ") >= 1 and "damaged range(s)" in shown["tooltip"]
    assert len(confirmations) == 1
    text, default = confirmations[0]
    assert text.startswith("This removes 2 parts totalling ") and "Recycle Bin" in text and default == flow.CUT_BUTTON
    assert moved and _decode_messages(path) == []
    assert any("removed 2 parts" in line and "0 errors" in line for line in said), said
    assert vf.check.status != "DAMAGED"
    assert "Cut damaged parts out of 1 of 1" in window.status_bar.currentMessage()


def test_a_deep_scan_flags_the_status_column_red_with_ranges_in_the_tooltip(pristine, tmp_path, window):
    from PyQt6.QtCore import Qt

    from core.video_file import VideoFile
    from gui import file_check_dialog as flow

    path = _copy(pristine, tmp_path, "start.mp4")
    for start, end in _aligned_blocks(path, 3):
        _zero(path, start, end)
    vf = VideoFile(path=path)
    vf.load()
    window.video_files = [vf]
    flow.check_files(window, [vf], deep=True)
    window._refresh_table_rows()
    column = window._column_order.index("status")
    item = window.table.item(0, column)
    assert item.text().startswith(("DAMAGED", "UNSAVED")) and "DAMAGED" in item.text()
    assert "damaged range(s)" in item.toolTip() and "zero-filled block(s) inside the video" in item.toolTip()
    assert item.background().color().red() > item.background().color().green()  # the error red


def test_the_confirmation_names_the_loss_warns_over_a_quarter_and_lists_a_batch(tmp_path, window):
    from core.video_file import VideoFile
    from gui import file_check_dialog as flow

    def damaged(name, spans, duration=100.0):
        vf = VideoFile(path=tmp_path / name)
        vf.check = fc.CheckResult(deep=ds.DeepScanResult(duration=duration, spans=[ds.DamagedSpan(a, b) for a, b in spans]),
                                  declared_seconds=duration)
        return vf

    light = damaged("a.mp4", [(10, 20), (61, 66)])
    box, cut_button, cancel_button, heavy = flow.build_cut_confirmation(window, [light])
    assert not heavy and box.defaultButton() is cut_button
    assert "This removes 2 parts totalling 0:15 (0:10-0:20, 1:01-1:06) of video and audio from the file; the rest is kept unchanged. The original goes to the Recycle Bin." in box.text()
    assert [b.text() for b in box.buttons()] == ["Cut Out Damaged Parts", "Cancel"]

    big = damaged("b.mp4", [(0, 40)])
    box, cut_button, cancel_button, heavy = flow.build_cut_confirmation(window, [light, big])
    assert heavy and box.defaultButton() is cancel_button       # default Cancel above 25%
    assert "WARNING" in box.text() and "b.mp4" in box.text()
    assert "a.mp4: 2 parts, 15 s (15%)" in box.text() and "b.mp4: 1 part, 40 s (40%)" in box.text()
    assert box.icon() == QMessageBox.Icon.Warning


def test_unsaved_files_are_not_cut(pristine, tmp_path, window):
    from core.video_file import VideoFile
    from gui import file_check_dialog as flow

    path, before = _damaged_with_check(pristine, tmp_path)
    vf = VideoFile(path=path)
    vf.load()
    vf.check = before
    vf.dirty = True
    cut, errors, cancelled = flow.cut_files(window, [vf])
    assert cut == [] and not cancelled and "unsaved changes" in errors[0]
    vf.dirty = False
