"""
Tests for core/transcode_settings.py. Uses a temp CONFIG_PATH per test
(via monkeypatching core.config's module attribute, same pattern as
test_config.py) rather than touching the real project's settings.ini.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

import core.config as config
from core.transcode_settings import (
    get_transcode_settings, set_transcode_settings, TranscodeSettings,
    DEFAULT_CRF, DEFAULT_AUDIO_BITRATE, DEFAULT_THREADS,
)


class TestTranscodeSettings(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._original_config_path = config.CONFIG_PATH
        config.CONFIG_PATH = Path(self.tmpdir) / "settings.ini"

    def tearDown(self):
        config.CONFIG_PATH = self._original_config_path
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_defaults_before_anything_saved(self):
        settings = get_transcode_settings()
        self.assertEqual(settings.crf, DEFAULT_CRF)
        self.assertEqual(settings.audio_bitrate, DEFAULT_AUDIO_BITRATE)
        self.assertEqual(settings.threads, DEFAULT_THREADS)

    def test_round_trip(self):
        set_transcode_settings(TranscodeSettings(crf=18, audio_bitrate="192k", threads=8))
        settings = get_transcode_settings()
        self.assertEqual(settings.crf, 18)
        self.assertEqual(settings.audio_bitrate, "192k")
        self.assertEqual(settings.threads, 8)

    def test_corrupt_crf_falls_back_to_default(self):
        config.set_setting("transcode", "crf", "not_a_number")
        settings = get_transcode_settings()
        self.assertEqual(settings.crf, DEFAULT_CRF)

    def test_corrupt_threads_falls_back_to_default(self):
        config.set_setting("transcode", "threads", "not_a_number")
        settings = get_transcode_settings()
        self.assertEqual(settings.threads, DEFAULT_THREADS)

    def test_empty_audio_bitrate_falls_back_to_default(self):
        config.set_setting("transcode", "audio_bitrate", "")
        settings = get_transcode_settings()
        self.assertEqual(settings.audio_bitrate, DEFAULT_AUDIO_BITRATE)

    def test_zero_threads_means_no_flag_downstream(self):
        # 0 is the sentinel for "let ffmpeg decide" (see
        # ffmpeg_backend.transcode_to_mp4's `if threads:` check) --
        # confirmed here as a settings-layer contract, not just an
        # implementation detail of the caller.
        set_transcode_settings(TranscodeSettings(threads=0))
        self.assertEqual(get_transcode_settings().threads, 0)


if __name__ == "__main__":
    unittest.main()
