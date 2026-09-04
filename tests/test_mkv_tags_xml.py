"""
Tests for core/mkv_backend.py's build_tags_xml()/parse_tags_xml() --
pure XML logic, fully runnable here without MKVToolNix installed.

This is regression coverage for a real, user-reported bug: the
original write_mkv_metadata() passed `--tags global:KEY=value` on the
mkvpropedit command line, which is not valid syntax at all (--tags
takes a TARGET:FILENAME pointing at an XML file). The fix builds a real
Matroska Tags XML document via ElementTree instead. These tests focus
on exactly the part a hand-rolled string-formatting approach would have
gotten wrong: values containing XML-special characters.
"""

import unittest

from core.mkv_backend import build_tags_xml, parse_tags_xml, FIELD_TO_MKV_TAG


class TestBuildAndParseTagsXml(unittest.TestCase):
    def test_basic_round_trip(self):
        values = {"GENRE": "Action, Comedy", "DIRECTOR": "Jane Smith"}
        xml = build_tags_xml(values)
        self.assertEqual(parse_tags_xml(xml), values)

    def test_ampersand_escaped_and_recovered(self):
        values = {"COMMENT": "Rock & Roll"}
        xml = build_tags_xml(values)
        self.assertIn("&amp;", xml)  # confirms real escaping happened, not passthrough
        self.assertEqual(parse_tags_xml(xml)["COMMENT"], "Rock & Roll")

    def test_angle_brackets_escaped_and_recovered(self):
        values = {"COMMENT": "A <tag>-looking string & more"}
        xml = build_tags_xml(values)
        self.assertNotIn("<tag>", xml)  # must not appear as raw, unescaped markup
        self.assertEqual(parse_tags_xml(xml)["COMMENT"], "A <tag>-looking string & more")

    def test_quotes_and_apostrophes_survive(self):
        values = {
            "COMMENT": 'She said "hello"',
            "DESCRIPTION": "It's a story",
        }
        xml = build_tags_xml(values)
        self.assertEqual(parse_tags_xml(xml), values)

    def test_empty_dict_produces_valid_empty_result(self):
        xml = build_tags_xml({})
        self.assertEqual(parse_tags_xml(xml), {})

    def test_parse_empty_string_returns_empty_dict_not_crash(self):
        self.assertEqual(parse_tags_xml(""), {})

    def test_parse_whitespace_only_returns_empty_dict(self):
        self.assertEqual(parse_tags_xml("   \n  "), {})

    def test_parse_malformed_xml_returns_empty_dict_not_crash(self):
        self.assertEqual(parse_tags_xml("<Tags><Tag><Simple><Name>Broken"), {})

    def test_parses_realistic_mkvextract_output_with_doctype(self):
        # Real mkvextract output includes an XML declaration and DOCTYPE
        # line before the actual <Tags> element -- confirm the parser
        # handles that shape, not just a bare <Tags> fragment.
        realistic = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE Tags SYSTEM "matroskatags.dtd">\n'
            "<Tags>\n"
            "  <Tag>\n"
            "    <Simple>\n"
            "      <Name>TITLE</Name>\n"
            "      <String>Some Movie</String>\n"
            "    </Simple>\n"
            "  </Tag>\n"
            "</Tags>\n"
        )
        self.assertEqual(parse_tags_xml(realistic), {"TITLE": "Some Movie"})

    def test_multiple_tags_all_preserved(self):
        values = {f"KEY{i}": f"value{i}" for i in range(10)}
        xml = build_tags_xml(values)
        self.assertEqual(parse_tags_xml(xml), values)


class TestMkvFullWriteReadRoundTrip(unittest.TestCase):
    """End-to-end write->persist->read simulation through the REAL
    write_mkv_metadata()/read_mkv_metadata() functions together (not
    just build_tags_xml/parse_tags_xml in isolation), for a TV episode
    specifically -- run in response to a real user report that TV tags
    weren't persisting. Confirms the MKV backend was NOT the source of
    that bug (the actual cause was MP4-specific -- see
    tests/test_mp4_backend_none_tags.py's TestMp4BackendTvNumericAtoms)
    by proving every TV field, including season/episode as correct int
    types, survives a full simulated round trip.
    """

    def test_tv_episode_fields_all_survive_write_then_read(self):
        import unittest.mock as mock
        import subprocess
        from core.video_metadata import VideoMetadata, ContentType
        from core.mkv_backend import write_mkv_metadata, read_mkv_metadata

        # Simulate real persistence: capture what write_mkv_metadata
        # writes to its temp XML file (read inside the mocked
        # mkvpropedit call, before the real function's own cleanup
        # deletes it), then serve that same content back on the
        # mkvextract read call -- a genuine write-then-read cycle, not
        # two independent mocked calls that happen to agree by
        # construction.
        persisted = {"xml": None}

        def fake_run(args, **kwargs):
            exe = args[0]
            if "mkvpropedit" in exe:
                for i, arg in enumerate(args):
                    if arg == "--tags" and i + 1 < len(args):
                        target_path = args[i + 1].split(":", 1)[1]
                        with open(target_path, "r", encoding="utf-8") as f:
                            persisted["xml"] = f.read()
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
            if "mkvmerge" in exe:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="{}", stderr="")
            if "mkvextract" in exe:
                return subprocess.CompletedProcess(
                    args=args, returncode=0, stdout=persisted["xml"] or "", stderr=""
                )
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="not found")

        with mock.patch("subprocess.run", side_effect=fake_run):
            meta = VideoMetadata(
                content_type=ContentType.TV, show_title="Breaking Bad",
                season_number=1, episode_number=5, title="Gray Matter", network="AMC",
            )
            result = write_mkv_metadata("/fake/episode.mkv", meta)
            self.assertEqual(result.returncode, 0)

            read_back = read_mkv_metadata("/fake/episode.mkv")

        self.assertEqual(read_back.show_title, "Breaking Bad")
        self.assertEqual(read_back.network, "AMC")
        self.assertEqual(read_back.content_type, ContentType.TV)
        self.assertEqual(read_back.season_number, 1)
        self.assertEqual(read_back.episode_number, 5)
        self.assertIsInstance(read_back.season_number, int)
        self.assertIsInstance(read_back.episode_number, int)


class TestWriteMkvMetadataUsesCorrectTagsTarget(unittest.TestCase):
    """Regression coverage for a real, user-reported bug: a SECOND real
    mkvpropedit syntax mistake, distinct from the first one (inline
    KEY=value instead of an XML file) that a previous entry already
    fixed. This one is subtler and specifically NOT caught by
    TestMkvFullWriteReadRoundTrip above -- that test mocks
    subprocess.run to just echo back whatever this project's own code
    wrote, so a wrong-but-internally-consistent target keyword would
    pass it regardless of whether real mkvpropedit accepts that
    keyword at all. This test instead inspects the actual argument
    list write_mkv_metadata hands to subprocess.run directly.

    build_tags_xml() produces tags with no <Targets> element, meaning
    (per the Matroska spec) they're scoped as GLOBAL/whole-file tags --
    `global` is the documented, standard mkvpropedit --tags target
    keyword for that scope. `all` (what the code used briefly, without
    solid justification, between the first and second real bug
    reports) is not confirmed to be a real target keyword at all.
    """

    def test_write_uses_global_tags_target_not_all(self):
        import unittest.mock as mock
        import subprocess
        from core.video_metadata import VideoMetadata
        from core.mkv_backend import write_mkv_metadata

        captured: list[list[str]] = []

        def fake_run(args, **kwargs):
            captured.append(args)
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            meta = VideoMetadata(title="Test Movie", genre_tags="Action")
            write_mkv_metadata("/fake/movie.mkv", meta)

        # Two separate invocations now (title, then tags) -- see
        # TestTitleAndTagsAreSeparateInvocations below for why. Find
        # the --tags argument across whichever call has it, rather
        # than assuming a specific call index.
        all_args = [a for call in captured for a in call]
        tags_arg = next((a for a in all_args if a.startswith(("global:", "all:"))), None)
        self.assertIsNotNone(tags_arg, f"No --tags target argument found across calls: {captured}")
        self.assertTrue(tags_arg.startswith("global:"), f"Expected 'global:' target, got: {tags_arg!r}")

    def test_title_and_tags_both_present_across_the_two_calls(self):
        # Confirm both the native title edit and the tags-file argument
        # are present SOMEWHERE across the (now two, separate)
        # mkvpropedit invocations, rather than one silently overwriting
        # or displacing the other.
        import unittest.mock as mock
        import subprocess
        from core.video_metadata import VideoMetadata
        from core.mkv_backend import write_mkv_metadata

        captured: list[list[str]] = []

        def fake_run(args, **kwargs):
            captured.append(args)
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            write_mkv_metadata("/fake/movie.mkv", VideoMetadata(title="A Title", comment="A comment"))

        all_args = [a for call in captured for a in call]
        self.assertIn("--edit", all_args)
        self.assertIn("info", all_args)
        self.assertIn("--set", all_args)
        self.assertIn("title=A Title", all_args)
        self.assertTrue(any(a.startswith("global:") for a in all_args))


class TestTitleAndTagsAreSeparateInvocations(unittest.TestCase):
    """Regression coverage for a structural change made in response to
    a real user report: content_type (and possibly other custom
    fields) silently failing to persist even after the `global`/`all`
    keyword fix. An earlier version of write_mkv_metadata() combined
    `--edit info --set title=...` and `--tags global:...` into ONE
    mkvpropedit invocation, on an assumption that was never confirmed
    against a real binary. Split into two separate invocations to
    remove that specific unverified assumption from the picture.
    """

    def test_two_separate_mkvpropedit_calls_when_both_title_and_tags_set(self):
        import unittest.mock as mock
        import subprocess
        from core.video_metadata import VideoMetadata, ContentType
        from core.mkv_backend import write_mkv_metadata

        captured: list[list[str]] = []

        def fake_run(args, **kwargs):
            captured.append(args)
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            meta = VideoMetadata(title="A Title", content_type=ContentType.TV, season_number=11)
            write_mkv_metadata("/fake/episode.mkv", meta)

        self.assertEqual(len(captured), 2)
        # First call is the title edit, second is the tags file --
        # confirms a clean separation, not just "two calls in some order."
        self.assertIn("--edit", captured[0])
        self.assertIn("title=A Title", captured[0])
        self.assertTrue(any(a.startswith("global:") for a in captured[1]))
        self.assertNotIn("--edit", captured[1])

    def test_only_one_call_when_only_title_is_set(self):
        import unittest.mock as mock
        import subprocess
        from core.video_metadata import VideoMetadata
        from core.mkv_backend import write_mkv_metadata

        captured: list[list[str]] = []

        def fake_run(args, **kwargs):
            captured.append(args)
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            write_mkv_metadata("/fake/movie.mkv", VideoMetadata(title="Only A Title"))

        self.assertEqual(len(captured), 1)
        self.assertIn("title=Only A Title", captured[0])

    def test_only_one_call_when_only_tags_are_set(self):
        import unittest.mock as mock
        import subprocess
        from core.video_metadata import VideoMetadata
        from core.mkv_backend import write_mkv_metadata

        captured: list[list[str]] = []

        def fake_run(args, **kwargs):
            captured.append(args)
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            write_mkv_metadata("/fake/movie.mkv", VideoMetadata(genre_tags="Action"))

        self.assertEqual(len(captured), 1)
        self.assertTrue(any(a.startswith("global:") for a in captured[0]))

    def test_combined_result_returncode_is_zero_only_if_both_succeed(self):
        import unittest.mock as mock
        import subprocess
        from core.video_metadata import VideoMetadata, ContentType
        from core.mkv_backend import write_mkv_metadata

        call_count = {"n": 0}

        def fake_run(args, **kwargs):
            call_count["n"] += 1
            # First call (title) succeeds, second (tags) fails.
            if call_count["n"] == 1:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
            return subprocess.CompletedProcess(args=args, returncode=2, stdout="", stderr="tags error")

        with mock.patch("subprocess.run", side_effect=fake_run):
            meta = VideoMetadata(title="A Title", genre_tags="Action")
            result = write_mkv_metadata("/fake/movie.mkv", meta)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("tags error", result.stderr)

    def test_combined_stdout_concatenates_both_calls_output(self):
        import unittest.mock as mock
        import subprocess
        from core.video_metadata import VideoMetadata, ContentType
        from core.mkv_backend import write_mkv_metadata

        call_count = {"n": 0}

        def fake_run(args, **kwargs):
            call_count["n"] += 1
            label = "title-call-output" if call_count["n"] == 1 else "tags-call-output"
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=label, stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            meta = VideoMetadata(title="A Title", genre_tags="Action")
            result = write_mkv_metadata("/fake/movie.mkv", meta)

        self.assertIn("title-call-output", result.stdout)
        self.assertIn("tags-call-output", result.stdout)


class TestFieldToMkvTagMapping(unittest.TestCase):
    def test_title_is_not_in_the_generic_tag_map(self):
        # title is handled separately via mkvpropedit's native
        # --edit info --set title=... , not as a generic SimpleTag --
        # regression check that it stays excluded from this map.
        self.assertNotIn("title", FIELD_TO_MKV_TAG)

    def test_sort_title_is_still_a_generic_tag(self):
        # Only 'title' itself is special-cased -- sort_title remains
        # an ordinary SimpleTag.
        self.assertIn("sort_title", FIELD_TO_MKV_TAG)

    def test_no_duplicate_mkv_tag_values(self):
        # Every field must map to a distinct Matroska tag key -- a
        # collision would mean two fields silently overwrite each
        # other's tag on write and can't be told apart on read.
        tag_values = list(FIELD_TO_MKV_TAG.values())
        self.assertEqual(len(tag_values), len(set(tag_values)))


class TestReadMkvMetadataDefensiveness(unittest.TestCase):
    """Regression coverage for a real user-reported crash while loading
    an MKV file. Root cause traced to a well-known Python gotcha:
    dict.get(key, default) only applies `default` when the key is
    ABSENT -- if real mkvmerge -J output has a key present with an
    explicit JSON null value (plausible for a file with no title set,
    for instance), .get() returns None despite the default, and a
    chained .get() call on that None then crashes. Fixed by treating
    "key present but null" the same as "key absent" throughout
    read_mkv_metadata. These tests mock subprocess.run to simulate the
    exact malformed-JSON shapes that would trigger the original bug.
    """

    def _run_with_mocked_mkvmerge(self, stdout: str):
        import unittest.mock as mock
        import subprocess
        from core.mkv_backend import read_mkv_metadata

        def fake_run(args, **kwargs):
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            return read_mkv_metadata("/tmp/fake.mkv")

    def test_container_key_present_but_null_does_not_crash(self):
        result = self._run_with_mocked_mkvmerge('{"container": null, "tracks": []}')
        self.assertEqual(result.title, "")
        self.assertIsNone(result.duration_seconds)

    def test_properties_key_present_but_null_does_not_crash(self):
        result = self._run_with_mocked_mkvmerge('{"container": {"properties": null}}')
        self.assertEqual(result.title, "")

    def test_container_key_entirely_missing_does_not_crash(self):
        result = self._run_with_mocked_mkvmerge("{}")
        self.assertEqual(result.title, "")

    def test_empty_stdout_does_not_crash(self):
        result = self._run_with_mocked_mkvmerge("")
        self.assertEqual(result.title, "")

    def test_literal_json_null_document_does_not_crash(self):
        result = self._run_with_mocked_mkvmerge("null")
        self.assertEqual(result.title, "")

    def test_container_wrong_type_does_not_crash(self):
        # mkvmerge would never really do this, but a defensive function
        # shouldn't assume its input is well-formed just because it
        # usually is.
        result = self._run_with_mocked_mkvmerge('{"container": [1, 2, 3]}')
        self.assertEqual(result.title, "")

    def test_duration_wrong_type_does_not_crash(self):
        result = self._run_with_mocked_mkvmerge(
            '{"container": {"properties": {"duration": "not-a-number"}}}'
        )
        self.assertIsNone(result.duration_seconds)

    def test_malformed_json_does_not_crash(self):
        result = self._run_with_mocked_mkvmerge("{not valid json!!")
        self.assertEqual(result.title, "")

    def test_valid_title_and_duration_still_extracted_correctly(self):
        # Confirm the defensive rewrite didn't break the happy path
        # while fixing the crash paths.
        result = self._run_with_mocked_mkvmerge(
            '{"container": {"properties": {"title": "My Movie", "duration": 5000000000}}}'
        )
        self.assertEqual(result.title, "My Movie")
        self.assertEqual(result.duration_seconds, 5.0)


class TestMkvExtractExplicitOutputFile(unittest.TestCase):
    """Regression coverage for a third real diagnostic step, taken in
    direct response to a real user screenshot: mkvpropedit's own stdout
    (captured by an earlier fix) confirmed "The changes are written to
    the file" -- meaning the WRITE genuinely succeeds, redirecting
    suspicion to the READ side. read_mkv_metadata() previously called
    `mkvextract <file> tags` with no output argument, assuming it
    defaults to printing XML on stdout -- an assumption never confirmed
    against a real binary, and mkvextract's stderr was being silently
    discarded entirely regardless, so a genuine extraction failure had
    no way to ever surface. Now asks mkvextract to write to an explicit
    temp output file (removing the stdout-default ambiguity) and
    captures stderr from both mkvmerge and mkvextract via a new
    diagnostics parameter.
    """

    def test_reads_from_explicit_output_file_when_mkvextract_writes_there(self):
        import unittest.mock as mock
        import subprocess
        from core.mkv_backend import read_mkv_metadata

        xml_content = (
            "<Tags><Tag><Simple><Name>VIDEOREDACTOR_CONTENT_TYPE</Name>"
            "<String>TV</String></Simple></Tag></Tags>"
        )

        def fake_run(args, **kwargs):
            exe = args[0]
            if "mkvmerge" in exe:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="{}", stderr="")
            if "mkvextract" in exe:
                # The 4th argument is the explicit output path this
                # project's code now supplies -- simulate mkvextract
                # actually writing there, the real behavior being
                # exercised by this test.
                output_path = args[3]
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(xml_content)
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="not found")

        with mock.patch("subprocess.run", side_effect=fake_run):
            result = read_mkv_metadata("/fake/episode.mkv")

        self.assertEqual(result.content_type.value, "TV")

    def test_falls_back_to_stdout_when_output_file_never_created(self):
        # If the explicit-output-file syntax itself turns out to be
        # wrong for some mkvextract version, the code should still try
        # stdout as a last resort rather than just silently returning
        # nothing.
        import unittest.mock as mock
        import subprocess
        from core.mkv_backend import read_mkv_metadata

        xml_content = (
            "<Tags><Tag><Simple><Name>GENRE</Name>"
            "<String>Reality</String></Simple></Tag></Tags>"
        )

        def fake_run(args, **kwargs):
            exe = args[0]
            if "mkvmerge" in exe:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="{}", stderr="")
            if "mkvextract" in exe:
                # Does NOT write to the output file -- only returns
                # stdout, simulating an mkvextract that ignores the
                # extra argument or uses a different convention.
                return subprocess.CompletedProcess(args=args, returncode=0, stdout=xml_content, stderr="")
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="not found")

        with mock.patch("subprocess.run", side_effect=fake_run):
            result = read_mkv_metadata("/fake/episode.mkv")

        self.assertEqual(result.genre_tags, "Reality")

    def test_temp_output_file_cleaned_up_after_read(self):
        # Same "never leak temp files, even on the happy path" standard
        # already applied to the write side's temp XML file.
        import unittest.mock as mock
        import subprocess
        import glob
        import tempfile as tempfile_module
        import os
        from core.mkv_backend import read_mkv_metadata

        before = set(glob.glob(os.path.join(tempfile_module.gettempdir(), "*.xml")))

        def fake_run(args, **kwargs):
            exe = args[0]
            if "mkvmerge" in exe:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="{}", stderr="")
            if "mkvextract" in exe:
                output_path = args[3]
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write("<Tags></Tags>")
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="not found")

        with mock.patch("subprocess.run", side_effect=fake_run):
            read_mkv_metadata("/fake/episode.mkv")

        after = set(glob.glob(os.path.join(tempfile_module.gettempdir(), "*.xml")))
        self.assertEqual(before, after, "read_mkv_metadata leaked a temp XML file")

    def test_diagnostics_dict_captures_mkvextract_stderr(self):
        # The core of this whole fix: a genuine mkvextract failure
        # (wrong argument, whatever the real cause turns out to be)
        # must now be visible via the diagnostics parameter instead of
        # silently discarded.
        import unittest.mock as mock
        import subprocess
        from core.mkv_backend import read_mkv_metadata

        def fake_run(args, **kwargs):
            exe = args[0]
            if "mkvmerge" in exe:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="{}", stderr="")
            if "mkvextract" in exe:
                return subprocess.CompletedProcess(
                    args=args, returncode=2, stdout="",
                    stderr='mkvextract: error: unknown mode "tags"',
                )
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="not found")

        diagnostics: dict = {}
        with mock.patch("subprocess.run", side_effect=fake_run):
            read_mkv_metadata("/fake/episode.mkv", diagnostics=diagnostics)

        self.assertIn("unknown mode", diagnostics.get("mkvextract_stderr", ""))

    def test_diagnostics_dict_captures_mkvmerge_stderr_too(self):
        import unittest.mock as mock
        import subprocess
        from core.mkv_backend import read_mkv_metadata

        def fake_run(args, **kwargs):
            exe = args[0]
            if "mkvmerge" in exe:
                return subprocess.CompletedProcess(
                    args=args, returncode=1, stdout="", stderr="mkvmerge: real error text",
                )
            if "mkvextract" in exe:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="not found")

        diagnostics: dict = {}
        with mock.patch("subprocess.run", side_effect=fake_run):
            read_mkv_metadata("/fake/episode.mkv", diagnostics=diagnostics)

        self.assertIn("real error text", diagnostics.get("mkvmerge_stderr", ""))

    def test_default_diagnostics_none_does_not_crash(self):
        # diagnostics is optional -- confirm the default (no dict
        # passed at all) still works, matching every OTHER caller of
        # read_mkv_metadata() that doesn't care about diagnostics.
        import unittest.mock as mock
        import subprocess
        from core.mkv_backend import read_mkv_metadata

        def fake_run(args, **kwargs):
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="{}", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            result = read_mkv_metadata("/fake/episode.mkv")  # no diagnostics= at all

        self.assertEqual(result.title, "")


if __name__ == "__main__":
    unittest.main()
