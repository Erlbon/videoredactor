"""
Tests for core/ffmpeg_backend.probe_technical_info() and _format_frame_rate().
Fully runnable here -- ffmpeg/ffprobe are real in this sandbox.
"""

import os
import shutil
import subprocess
import tempfile
import unittest

from core.ffmpeg_backend import probe_technical_info, _format_frame_rate


class TestProbeTechnicalInfo(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_extracts_correct_resolution_and_codecs(self):
        path = os.path.join(self.tmpdir, "720p.mp4")
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "testsrc=duration=2:size=1280x720:rate=30",
                "-f", "lavfi", "-i", "sine=frequency=1000:duration=2",
                "-c:v", "libx264", "-c:a", "aac", "-shortest", path,
            ],
            capture_output=True, check=True,
        )
        info = probe_technical_info(path)
        self.assertEqual(info["resolution"], "1280x720")
        self.assertEqual(info["video_codec"], "h264")
        self.assertEqual(info["audio_codec"], "aac")
        self.assertAlmostEqual(info["duration_seconds"], 2.0, delta=0.2)
        self.assertEqual(info["frame_rate"], "30")

    def test_ntsc_fractional_frame_rate_converts_correctly(self):
        path = os.path.join(self.tmpdir, "ntsc.mp4")
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=30000/1001",
                "-c:v", "libx264", path,
            ],
            capture_output=True, check=True,
        )
        info = probe_technical_info(path)
        self.assertEqual(info["frame_rate"], "29.97")

    def test_different_resolution_gives_different_result(self):
        path_a = os.path.join(self.tmpdir, "a.mp4")
        path_b = os.path.join(self.tmpdir, "b.mp4")
        for path, size in [(path_a, "320x240"), (path_b, "640x480")]:
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=duration=1:size={size}:rate=10",
                 "-c:v", "libx264", path],
                capture_output=True, check=True,
            )
        self.assertEqual(probe_technical_info(path_a)["resolution"], "320x240")
        self.assertEqual(probe_technical_info(path_b)["resolution"], "640x480")

    def test_missing_file_returns_empty_dict_not_exception(self):
        self.assertEqual(probe_technical_info("/nonexistent/path.mp4"), {})

    def test_audio_only_file_has_no_video_fields(self):
        path = os.path.join(self.tmpdir, "audio_only.mp4")
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=1000:duration=1",
             "-c:a", "aac", path],
            capture_output=True, check=True,
        )
        info = probe_technical_info(path)
        self.assertNotIn("resolution", info)
        self.assertNotIn("video_codec", info)
        self.assertEqual(info["audio_codec"], "aac")


class TestFormatFrameRate(unittest.TestCase):
    def test_whole_number_no_trailing_decimal(self):
        self.assertEqual(_format_frame_rate("25/1"), "25")

    def test_fractional_rounds_to_two_decimals(self):
        self.assertEqual(_format_frame_rate("30000/1001"), "29.97")

    def test_zero_denominator_returns_empty_not_crash(self):
        self.assertEqual(_format_frame_rate("0/0"), "")

    def test_empty_string(self):
        self.assertEqual(_format_frame_rate(""), "")

    def test_no_slash_passes_through(self):
        self.assertEqual(_format_frame_rate("garbage"), "garbage")


class TestProbeTechnicalInfoDefensiveness(unittest.TestCase):
    """Same defensive-coding fix as core/mkv_backend.py's
    read_mkv_metadata (see tests/test_mkv_tags_xml.py): dict.get(key,
    default) doesn't apply `default` when the key is present with an
    explicit JSON null. Mocks subprocess.run to simulate ffprobe output
    shapes that would have crashed the original code.
    """

    def _run_with_mocked_ffprobe(self, stdout: str) -> dict:
        import unittest.mock as mock
        import subprocess

        def fake_run(args, **kwargs):
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            return probe_technical_info("/tmp/fake.mp4")

    def test_format_key_present_but_null_does_not_crash(self):
        self.assertEqual(self._run_with_mocked_ffprobe('{"format": null, "streams": []}'), {})

    def test_streams_key_present_but_null_does_not_crash(self):
        self.assertEqual(self._run_with_mocked_ffprobe('{"format": {}, "streams": null}'), {})

    def test_both_null_does_not_crash(self):
        self.assertEqual(self._run_with_mocked_ffprobe('{"format": null, "streams": null}'), {})

    def test_non_dict_entries_in_streams_list_skipped_not_crashed(self):
        result = self._run_with_mocked_ffprobe('{"format": {}, "streams": [null, 5, "garbage"]}')
        self.assertEqual(result, {})

    def test_malformed_json_does_not_crash(self):
        self.assertEqual(self._run_with_mocked_ffprobe("{not valid!!"), {})

    def test_top_level_list_instead_of_object_does_not_crash(self):
        self.assertEqual(self._run_with_mocked_ffprobe("[1,2,3]"), {})

    def test_valid_data_still_extracted_correctly(self):
        # Confirm the defensive rewrite didn't break the happy path.
        result = self._run_with_mocked_ffprobe(
            '{"format": {"duration": "10.5", "bit_rate": "5000"}, '
            '"streams": [{"codec_type": "video", "codec_name": "h264", '
            '"width": 1920, "height": 1080, "avg_frame_rate": "25/1"}]}'
        )
        self.assertAlmostEqual(result["duration_seconds"], 10.5)
        self.assertEqual(result["video_codec"], "h264")
        self.assertEqual(result["resolution"], "1920x1080")
        self.assertEqual(result["frame_rate"], "25")


if __name__ == "__main__":
    unittest.main()
