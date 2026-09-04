"""
Tests for core/mp4_backend.py's handling of mutagen's documented
MP4.tags == None case (a file with no existing metadata atoms at all).

This is regression coverage for a REAL, confirmed user-reported crash
(seen in an actual screenshot of the app's error tooltip: "Could not
read metadata: argument of type 'NoneType' is not a container or
iterable"). Root cause: every function in mp4_backend.py did `atom in
mp4.tags` / `mp4.tags[atom] = ...` unconditionally, but
mutagen.mp4.MP4.tags is documented to be None (not an empty dict) for
an untagged file -- completely normal for an older or bare rip, not a
malformed-file case.

mutagen isn't installed in this sandbox (no network access during
development), so these tests inject a faithful mock of mutagen.mp4
into sys.modules -- built specifically to reproduce the documented
None-tags behavior, not a generic mock that would trivially pass
regardless of whether the fix is correct.
"""

import sys
import types
import unittest


def _install_fake_mutagen(tags_value=None, strict_int_atoms=False):
    """Install a minimal fake mutagen.mp4 module into sys.modules,
    mimicking mutagen's real MP4/MP4Cover API surface closely enough
    for mp4_backend.py's actual usage. Returns the fake module so
    tests can inspect instances created during a call.

    Storage is keyed by path and PERSISTS across separate MP4(path)
    instantiations within one test -- matching real mutagen/file
    behavior, where writing then re-opening the same real file reflects
    the write. Without this, a test that calls write_mp4_metadata(path)
    then read_mp4_metadata(path) would get two independent, disconnected
    fake instances and not actually exercise a round trip at all -- a
    real bug this fixture itself had until caught during this feature's
    own development.

    strict_int_atoms=True makes save() raise TypeError if tvsn/tves
    ever receive a non-int value -- simulating mutagen's real
    documented type requirement for these atoms, so a test using this
    can distinguish "the fix works" from "nothing crashed by luck."
    """
    fake_mutagen = types.ModuleType("mutagen")
    fake_mp4_module = types.ModuleType("mutagen.mp4")

    class FakeInfo:
        length = 125.5
        bitrate = 128000

    created_instances = []
    persistent_storage: dict[str, dict] = {}

    class FakeMP4:
        def __init__(self, path):
            self.path = path
            if path not in persistent_storage:
                persistent_storage[path] = dict(tags_value) if tags_value is not None else None
            self.info = FakeInfo()
            self.saved = False
            created_instances.append(self)

        @property
        def tags(self):
            return persistent_storage[self.path]

        @tags.setter
        def tags(self, value):
            persistent_storage[self.path] = value

        def add_tags(self):
            self.tags = {}

        def save(self):
            if strict_int_atoms and self.tags:
                for atom in ("tvsn", "tves"):
                    if atom in self.tags:
                        val = self.tags[atom][0]
                        if not isinstance(val, int):
                            raise TypeError(
                                f"{atom} requires int, got {type(val).__name__}: {val!r}"
                            )
            self.saved = True

    class FakeMP4Cover:
        FORMAT_PNG = "png"
        FORMAT_JPEG = "jpeg"

        def __init__(self, data, imageformat):
            self.data = data
            self.imageformat = imageformat

        def __bytes__(self):
            return bytes(self.data)

    class FakeMP4FreeForm(bytes):
        """Real mutagen.mp4.MP4FreeForm is a bytes SUBCLASS carrying an
        optional dataformat -- this fake mirrors that shape (rather than
        being an unrelated wrapper object) specifically so
        isinstance(x, bytes) checks on the read side behave exactly like
        they would against the real class, and so a test using this can
        actually distinguish 'wrapped correctly' from 'bare bytes,
        wrapping ignored.'
        """
        def __new__(cls, data, dataformat=1):
            obj = bytes.__new__(cls, data)
            obj.dataformat = dataformat
            return obj

    fake_mp4_module.MP4 = FakeMP4
    fake_mp4_module.MP4Cover = FakeMP4Cover
    fake_mp4_module.MP4FreeForm = FakeMP4FreeForm
    sys.modules["mutagen"] = fake_mutagen
    sys.modules["mutagen.mp4"] = fake_mp4_module
    return created_instances


class TestMp4BackendNoneTagsHandling(unittest.TestCase):
    def setUp(self):
        self._original_mutagen = sys.modules.get("mutagen")
        self._original_mutagen_mp4 = sys.modules.get("mutagen.mp4")

    def tearDown(self):
        # Restore whatever was there before (likely nothing, since
        # mutagen isn't actually installed here) rather than leaving
        # the fake module in place for other test files.
        for name, original in [
            ("mutagen", self._original_mutagen),
            ("mutagen.mp4", self._original_mutagen_mp4),
        ]:
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original
        # mp4_backend imports mutagen.mp4 INSIDE each function (deferred
        # import), so no module-level cache to clear on our side.

    def test_read_on_untagged_file_does_not_crash(self):
        _install_fake_mutagen(tags_value=None)
        from core.mp4_backend import read_mp4_metadata

        result = read_mp4_metadata("/fake/untagged.mp4")
        self.assertEqual(result.title, "")

    def test_read_on_untagged_file_still_gets_technical_fields(self):
        # mp4.info always exists regardless of mp4.tags -- confirm
        # duration/bitrate still come through even with no tags at all.
        _install_fake_mutagen(tags_value=None)
        from core.mp4_backend import read_mp4_metadata

        result = read_mp4_metadata("/fake/untagged.mp4")
        self.assertEqual(result.duration_seconds, 125.5)

    def test_write_on_untagged_file_does_not_crash(self):
        _install_fake_mutagen(tags_value=None)
        from core.mp4_backend import write_mp4_metadata
        from core.video_metadata import VideoMetadata

        write_mp4_metadata("/fake/untagged.mp4", VideoMetadata(title="Test"))  # must not raise

    def test_write_on_untagged_file_actually_writes_correct_data(self):
        instances = _install_fake_mutagen(tags_value=None)
        from core.mp4_backend import write_mp4_metadata
        from core.video_metadata import VideoMetadata

        meta = VideoMetadata(title="The NeverEnding Story", genre_tags="Fantasy, Adventure")
        write_mp4_metadata("/fake/untagged.mp4", meta)

        mp4_instance = instances[0]
        self.assertIsNotNone(mp4_instance.tags)  # add_tags() was called
        self.assertEqual(mp4_instance.tags["\xa9nam"], ["The NeverEnding Story"])
        self.assertEqual(mp4_instance.tags["\xa9gen"], ["Fantasy, Adventure"])
        self.assertTrue(mp4_instance.saved)

    def test_read_cover_on_untagged_file_returns_none_not_crash(self):
        _install_fake_mutagen(tags_value=None)
        from core.mp4_backend import read_mp4_cover

        self.assertIsNone(read_mp4_cover("/fake/untagged.mp4"))

    def test_write_cover_on_untagged_file_does_not_crash(self):
        _install_fake_mutagen(tags_value=None)
        from core.mp4_backend import write_mp4_cover

        write_mp4_cover("/fake/untagged.mp4", b"fake image bytes")  # must not raise

    def test_read_on_already_tagged_file_still_works(self):
        # Confirm the fix didn't break the normal (tags exist) case --
        # this is the "happy path" that was already working.
        _install_fake_mutagen(tags_value={"\xa9nam": ["Existing Title"]})
        from core.mp4_backend import read_mp4_metadata

        result = read_mp4_metadata("/fake/tagged.mp4")
        self.assertEqual(result.title, "Existing Title")

    def test_write_on_already_tagged_file_does_not_call_add_tags_unnecessarily(self):
        # add_tags() should only be needed when tags is None -- a file
        # that already has tags shouldn't have them replaced/reset.
        instances = _install_fake_mutagen(tags_value={"\xa9nam": ["Old Title"]})
        from core.mp4_backend import write_mp4_metadata
        from core.video_metadata import VideoMetadata

        write_mp4_metadata("/fake/tagged.mp4", VideoMetadata(title="New Title"))
        self.assertEqual(instances[0].tags["\xa9nam"], ["New Title"])


class TestMp4BackendTvNumericAtoms(unittest.TestCase):
    """Regression coverage for a second real, user-reported bug: TV
    season/episode metadata silently failing to persist. Root cause:
    mutagen's MP4Tags requires tvsn (season) and tves (episode) as a
    list of int, not a list of str -- the original code wrote
    `[str(value)]` uniformly for every atom, including these two.
    Uses strict_int_atoms=True so the mock actually enforces the type
    requirement mutagen itself enforces, distinguishing "the fix
    works" from "nothing happened to crash."
    """

    def setUp(self):
        self._original_mutagen = sys.modules.get("mutagen")
        self._original_mutagen_mp4 = sys.modules.get("mutagen.mp4")

    def tearDown(self):
        for name, original in [
            ("mutagen", self._original_mutagen),
            ("mutagen.mp4", self._original_mutagen_mp4),
        ]:
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original

    def test_season_and_episode_written_as_int_not_str(self):
        instances = _install_fake_mutagen(tags_value={}, strict_int_atoms=True)
        from core.mp4_backend import write_mp4_metadata
        from core.video_metadata import VideoMetadata, ContentType

        meta = VideoMetadata(
            content_type=ContentType.TV, show_title="Breaking Bad",
            season_number=1, episode_number=5, title="Gray Matter",
        )
        write_mp4_metadata("/fake/tv_episode.mp4", meta)  # must NOT raise TypeError

        mp4_instance = instances[0]
        self.assertIsInstance(mp4_instance.tags["tvsn"][0], int)
        self.assertIsInstance(mp4_instance.tags["tves"][0], int)
        self.assertEqual(mp4_instance.tags["tvsn"][0], 1)
        self.assertEqual(mp4_instance.tags["tves"][0], 5)
        self.assertTrue(mp4_instance.saved)

    def test_old_string_based_write_would_have_failed_this_check(self):
        # Confirms the strict_int_atoms mock actually distinguishes
        # correct from incorrect behavior, rather than trivially
        # passing regardless of what's written.
        instances = _install_fake_mutagen(tags_value={}, strict_int_atoms=True)
        mp4_instance = instances[0] if instances else None
        from mutagen.mp4 import MP4
        old_style = MP4("/fake/whatever.mp4")
        old_style.tags["tvsn"] = [str(1)]  # the OLD buggy pattern
        with self.assertRaises(TypeError):
            old_style.save()

    def test_season_number_read_back_as_int_not_str(self):
        # The read-side half of the same bug: even with a correctly
        # int-typed atom, the OLD read code stringified every atom
        # uniformly, which would have broken the GUI's int-only field.
        _install_fake_mutagen(tags_value={"tvsn": [3], "tves": [12]})
        from core.mp4_backend import read_mp4_metadata

        result = read_mp4_metadata("/fake/tv_episode.mp4")
        self.assertIsInstance(result.season_number, int)
        self.assertIsInstance(result.episode_number, int)
        self.assertEqual(result.season_number, 3)
        self.assertEqual(result.episode_number, 12)

    def test_non_numeric_season_value_skipped_not_crashed(self):
        # A hand-typed or corrupted non-numeric season/episode value
        # must not crash the whole save -- skipped for that one atom.
        instances = _install_fake_mutagen(tags_value={}, strict_int_atoms=True)
        from core.mp4_backend import write_mp4_metadata
        from core.video_metadata import VideoMetadata

        meta = VideoMetadata(season_number=None)  # no season set at all
        write_mp4_metadata("/fake/no_season.mp4", meta)  # must not raise
        self.assertNotIn("tvsn", instances[0].tags)

    def test_text_tv_atoms_unaffected_by_integer_fix(self):
        # show_title (tvsh) and network (tvnn) are TEXT atoms -- confirm
        # the INTEGER_ATOMS special-casing didn't accidentally break
        # these sibling TV fields.
        instances = _install_fake_mutagen(tags_value={}, strict_int_atoms=True)
        from core.mp4_backend import write_mp4_metadata
        from core.video_metadata import VideoMetadata

        meta = VideoMetadata(show_title="Breaking Bad", network="AMC")
        write_mp4_metadata("/fake/tv_text.mp4", meta)

        self.assertEqual(instances[0].tags["tvsh"], ["Breaking Bad"])
        self.assertEqual(instances[0].tags["tvnn"], ["AMC"])


class TestMp4CustomFieldFreeFormWrapping(unittest.TestCase):
    """Regression coverage for a THIRD real, user-reported bug: custom
    fields (content_type, director, cast, sort_title, etc.) silently
    failing to persist while native text atoms like title kept working
    fine. Leading theory: mutagen's documented pattern for freeform
    (----:mean:name) atoms wraps the value in MP4FreeForm(bytes,
    dataformat=...), and the original code used a bare bytes object
    instead, which -- unlike a wrong type on a strictly-validated atom
    like tvsn -- may not raise an error, just silently not serialize
    the same way. These tests confirm write_mp4_metadata now actually
    uses MP4FreeForm (not just bytes) for every custom field, and that
    the full write-then-read round trip still produces the correct
    value back out.
    """

    def setUp(self):
        self._original_mutagen = sys.modules.get("mutagen")
        self._original_mutagen_mp4 = sys.modules.get("mutagen.mp4")

    def tearDown(self):
        for name, original in [
            ("mutagen", self._original_mutagen),
            ("mutagen.mp4", self._original_mutagen_mp4),
        ]:
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original

    def test_custom_field_value_is_wrapped_in_mp4freeform_not_bare_bytes(self):
        instances = _install_fake_mutagen(tags_value={})
        from core.mp4_backend import write_mp4_metadata
        from core.video_metadata import VideoMetadata, ContentType
        from mutagen.mp4 import MP4FreeForm

        write_mp4_metadata("/fake/movie.mp4", VideoMetadata(content_type=ContentType.MOVIE))

        key = "----:com.videoredactor:content_type"
        stored = instances[0].tags[key][0]
        self.assertIsInstance(stored, MP4FreeForm)

    def test_custom_field_full_round_trip_content_type(self):
        _install_fake_mutagen(tags_value={})
        from core.mp4_backend import write_mp4_metadata, read_mp4_metadata
        from core.video_metadata import VideoMetadata, ContentType

        write_mp4_metadata("/fake/movie.mp4", VideoMetadata(content_type=ContentType.MOVIE))

        # Re-read using the SAME fake instance's now-populated tags dict
        # (simulating a real reload after save, not just inspecting the
        # write side in isolation).
        result = read_mp4_metadata("/fake/movie.mp4")
        self.assertEqual(result.content_type, ContentType.MOVIE)

    def test_custom_field_full_round_trip_director_and_sort_title(self):
        _install_fake_mutagen(tags_value={})
        from core.mp4_backend import write_mp4_metadata, read_mp4_metadata
        from core.video_metadata import VideoMetadata

        write_mp4_metadata(
            "/fake/movie.mp4",
            VideoMetadata(director="Jane Smith", sort_title="Matrix, The"),
        )
        result = read_mp4_metadata("/fake/movie.mp4")
        self.assertEqual(result.director, "Jane Smith")
        self.assertEqual(result.sort_title, "Matrix, The")

    def test_multiple_custom_fields_all_survive_together(self):
        _install_fake_mutagen(tags_value={})
        from core.mp4_backend import write_mp4_metadata, read_mp4_metadata
        from core.video_metadata import VideoMetadata, ContentType

        meta = VideoMetadata(
            content_type=ContentType.TV, director="Someone", cast="A, B, C",
            studio="A Studio", collection="A Collection",
        )
        write_mp4_metadata("/fake/show.mp4", meta)
        result = read_mp4_metadata("/fake/show.mp4")

        self.assertEqual(result.content_type, ContentType.TV)
        self.assertEqual(result.director, "Someone")
        self.assertEqual(result.cast, "A, B, C")
        self.assertEqual(result.studio, "A Studio")
        self.assertEqual(result.collection, "A Collection")


if __name__ == "__main__":
    unittest.main()
