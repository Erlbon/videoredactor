"""core/conversion_plan: the library conversion rules as a pure decision engine."""

from core import conversion_plan as cp

GB = 1073741824
MB = 1048576


def info(ext=".mkv", size=2 * GB, dur=3000.0, vcodec="h264", w=1920, h=1080, audio=("aac",), subs=()):
    streams = [cp.StreamInfo(0, "video", vcodec, width=w, height=h)]
    for codec in audio:
        streams.append(cp.StreamInfo(len(streams), "audio", codec, bit_rate=160_000))
    for codec in subs:
        streams.append(cp.StreamInfo(len(streams), "subtitle", codec))
    return cp.MediaInfo("F:/Video/Show/ep" + ext, size, dur, streams)


def test_parse_ffprobe_reads_bps_tag_cover_art_and_nulls():
    data = {
        "format": {"size": "1000", "duration": "10.5"},
        "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720},
            {"index": 1, "codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
            {"index": 2, "codec_type": "audio", "codec_name": "AC3", "tags": {"BPS": "384000", "language": "eng"}},
            None,
        ],
    }
    m = cp.parse_ffprobe("a.mkv", data)
    assert m.size_bytes == 1000 and m.duration == 10.5
    assert len(m.of("video")) == 1  # cover art is not video
    assert m.of("audio")[0].bit_rate == 384000 and m.of("audio")[0].codec == "ac3"
    assert cp.parse_ffprobe("a.mkv", {"format": None, "streams": None}).duration is None


def test_sane_files_are_left_alone():
    p = cp.plan_conversion(info(".mp4", size=300 * MB, h=720, w=1280))
    assert not p.does_something and p.container == ".mp4"
    p = cp.plan_conversion(info(".mkv", size=300 * MB, h=720, w=1280, audio=("dts",)))
    assert not p.does_something and p.container == ".mkv"


def test_legacy_container_choice_mp4_or_mkv():
    def plan(ext, **kw):
        kw.setdefault("size", 300 * MB)
        return cp.plan_conversion(info(ext, h=480, w=640, **kw))

    assert plan(".avi", vcodec="mpeg4", audio=("mp3",)).container == ".mp4"
    assert plan(".avi", vcodec="mpeg4", audio=("vorbis",)).container == ".mkv"
    assert plan(".avi", vcodec="msmpeg4v3", audio=("mp3",)).container == ".mkv"
    assert plan(".mov", audio=("pcm_s16le",)).container == ".mkv"
    assert plan(".avi", vcodec="mpeg4", audio=("mp3",), subs=("subrip",) * 4).container == ".mkv"
    for ext in (".m4v", ".wmv", ".flv", ".3gp", ".rmvb", ".f4v", ".webm"):
        assert plan(ext).does_something


def test_unconvertible_video_is_the_only_lossy_case():
    m = info(".rmvb", size=300 * MB, vcodec="rv40", h=480, w=640, audio=("cook",))
    p = cp.plan_conversion(m)
    assert p.video == cp.V_X265 and p.audio_reencode
    args = cp.build_ffmpeg_args(m, p, "in.rmvb", "out.mp4")
    i = args.index("-c:v")
    assert args[i:i + 6] == ["-c:v", "libx265", "-crf", "23", "-tag:v", "hvc1"]
    assert "160k" in args
    assert cp.plan_conversion(info(".avi", vcodec="weirdcodec")).skip_reason


def test_disc_images_default_untouched_with_options():
    vob = info(".vob")
    assert cp.plan_conversion(vob).skip_reason and not cp.plan_conversion(vob).needs_review
    listed = cp.plan_conversion(info(".iso"), cp.ConversionSettings(disc_images=cp.DISC_LIST))
    assert listed.skip_reason and "iso" in listed.needs_review
    remux = cp.plan_conversion(vob, cp.ConversionSettings(disc_images=cp.DISC_REMUX_VOB))
    assert remux.container == ".mkv" and remux.video == cp.V_COPY and remux.does_something
    assert cp.plan_conversion(info(".iso"), cp.ConversionSettings(disc_images=cp.DISC_REMUX_VOB)).skip_reason


def test_bitrate_rules_tall_files():
    assert cp.plan_conversion(info(size=2 * GB)).video == cp.V_NVENC_SCALE  # size trigger
    assert cp.plan_conversion(info(size=500 * MB, dur=3000)).video == cp.V_COPY  # ~1.2 Mb/s, under 1 GB
    assert cp.plan_conversion(info(size=900 * MB, dur=1000)).video == cp.V_NVENC_SCALE  # 7 Mb/s > 4
    assert cp.plan_conversion(info(size=2 * GB, w=1920, h=800)).scales  # width > 1300 counts as tall


def test_bitrate_rules_720p_and_below():
    assert cp.plan_conversion(info(size=900 * MB, dur=3000, h=720, w=1280)).video == cp.V_COPY  # ~2.3 Mb/s
    over = cp.plan_conversion(info(size=1500 * MB, dur=3000, h=720, w=1280))  # ~4 Mb/s > 3
    assert over.video == cp.V_NVENC and not over.scales
    assert cp.plan_conversion(info(size=900 * MB, dur=3000, h=576, w=720)).video == cp.V_NVENC  # > 2 when < 650


def test_movies_and_small_files_are_exempt():
    big = info(size=3 * GB)
    assert cp.plan_conversion(big, is_series=False).video == cp.V_COPY
    assert cp.plan_conversion(big, cp.ConversionSettings(apply_to_movies=True), is_series=False).scales
    assert cp.plan_conversion(info(size=399 * MB, dur=300)).video == cp.V_COPY  # under 400 MB: ignored


def test_video_bitrate_subtracts_audio_estimate():
    m = info(size=GB, dur=1000.0, audio=("ac3",))
    m.streams[1].bit_rate = None  # estimate ac3 448k
    expected = (GB * 8 / 1000 - 448_000) / 1e6
    assert abs(cp.video_mbps(m) - expected) < 1e-9
    assert cp.video_mbps(info(dur=None)) is None


def test_heavy_audio_flag():
    s = cp.ConversionSettings()
    assert not cp.is_heavy_audio(info(audio=("aac",)), s)
    assert cp.is_heavy_audio(info(audio=("aac", "aac", "aac")), s)
    assert cp.is_heavy_audio(info(audio=("dts",)), s)
    assert cp.is_heavy_audio(info(audio=("pcm_s16le",)), s)


def test_external_subtitles_turn_an_mp4_into_mkv():
    p = cp.plan_conversion(info(".mp4", size=300 * MB, h=720, w=1280), extra_subtitles=2)
    assert p.container == ".mkv" and p.does_something


NVENC_GPU = cp.ConversionSettings(encoder="nvenc", hw_decode=True)


def test_ffmpeg_args_nvenc_scale_cuda_and_fallback():
    m = info(size=2 * GB)
    p = cp.plan_conversion(m)
    a = cp.build_ffmpeg_args(m, p, "in.mkv", "out.mkv", NVENC_GPU)
    assert a[:4] == ["ffmpeg", "-v", "error", "-y"] and a[a.index("-hwaccel") + 1] == "cuda"
    assert "scale_cuda=-2:720" in a and a[a.index("-cq") + 1] == "30"
    assert a[a.index("-c:v") + 1] == "hevc_nvenc"
    assert a[a.index("-c:a") + 1] == "copy" and "-dn" in a and a[-1] == "out.mkv"
    soft = cp.build_ffmpeg_args(m, p, "in.mkv", "out.mkv", NVENC_GPU, cuda=False)
    assert "-hwaccel" not in soft and "scale=-2:720" in soft and soft[soft.index("-c:v") + 1] == "hevc_nvenc"
    assert a.index("-fflags") < a.index("-i")


def test_the_encoder_defaults_are_auto_software_decode_and_crf_27():
    s = cp.ConversionSettings()
    assert s.encoder == "auto" and s.hw_decode is False and s.x265_crf == 27
    assert cp.ENCODERS == ("auto", "nvenc", "x265")


def test_hw_decode_off_means_no_hwaccel_even_with_nvenc():
    m = info(size=2 * GB)
    p = cp.plan_conversion(m)
    a = cp.build_ffmpeg_args(m, p, "in.mkv", "out.mkv", cp.ConversionSettings(encoder="nvenc"))
    assert "-hwaccel" not in a and "scale_cuda=-2:720" not in a and "scale=-2:720" in a
    assert a[a.index("-c:v") + 1] == "hevc_nvenc"


def test_hw_decode_on_with_x265_still_has_no_cuda_flags():
    m = info(size=2 * GB)
    p = cp.plan_conversion(m)
    a = cp.build_ffmpeg_args(m, p, "in.mkv", "out.mkv", cp.ConversionSettings(hw_decode=True), encoder="x265")
    assert "-hwaccel" not in a and "scale=-2:720" in a


def test_x265_args_for_the_series_reencodes():
    m = info(size=2 * GB, subs=("subrip",))
    p = cp.plan_conversion(m)
    a = cp.build_ffmpeg_args(m, p, "in.mkv", "out.mkv", cp.ConversionSettings(x265_crf=25), encoder="x265")
    i = a.index("-c:v")
    assert a[i:i + 8] == ["-c:v", "libx265", "-preset", "medium", "-crf", "25", "-tag:v", "hvc1"]
    assert "hevc_nvenc" not in a and "-cq" not in a and "scale=-2:720" in a
    assert a[a.index("-c:a") + 1] == "copy" and a[a.index("-c:s:0") + 1] == "copy"  # audio, subtitles copied
    # an unresolved "auto" builds the encoder that needs no particular hardware
    assert cp.build_ffmpeg_args(m, p, "in.mkv", "out.mkv")[i + 1] == "libx265"


def test_the_unplayable_codec_fallback_is_unchanged():
    m = info(".rmvb", size=300 * MB, h=480, w=640, vcodec="rv40")
    p = cp.plan_conversion(m)
    for encoder in ("nvenc", "x265"):
        a = cp.build_ffmpeg_args(m, p, "in.rmvb", "out.mp4", NVENC_GPU, encoder=encoder)
        assert a[a.index("-c:v") + 1: a.index("-c:v") + 5] == ["libx265", "-crf", "23", "-tag:v"]
        assert "-hwaccel" not in a and "-preset" not in a


def test_ffmpeg_args_map_streams_explicitly_and_drop_bitmap_subs_in_mp4():
    m = info(".mkv", size=300 * MB, h=480, w=640, subs=("subrip", "hdmv_pgs_subtitle"))
    m.streams.append(cp.StreamInfo(9, "video", "mjpeg", attached_pic=True))

    def maps(args):
        return [args[i + 1] for i, v in enumerate(args) if v == "-map"]

    a = cp.build_ffmpeg_args(m, cp.Plan(container=".mp4", reasons=["x"]), "in", "out.mp4")
    assert "0:9" not in maps(a) and "0:2" in maps(a) and "0:3" not in maps(a)  # cover art and PGS dropped
    assert a[a.index("-c:s") + 1] == "mov_text"
    mkv = cp.build_ffmpeg_args(m, cp.Plan(container=".mkv", reasons=["x"]), "in", "out.mkv")
    assert "0:3" in maps(mkv) and mkv[mkv.index("-c:s:0") + 1] == "copy" and mkv[mkv.index("-c:s:1") + 1] == "copy"


def test_output_must_be_under_85_percent():
    assert cp.output_size_ok(1000, 849) and not cp.output_size_ok(1000, 850) and not cp.output_size_ok(0, 0)


def test_conversion_settings_roundtrip_defaults_and_damage():
    store = {}
    get = lambda sec, k, d="": store.get((sec, k), d)  # noqa: E731
    put = lambda sec, k, v: store.__setitem__((sec, k), v)  # noqa: E731
    assert cp.get_conversion_settings(get) == cp.ConversionSettings()
    custom = cp.ConversionSettings(apply_to_movies=True, nvenc_cq=27, scale_video_mbps=5.5, disc_images=cp.DISC_LIST, subtitle_languages="en,nl")
    cp.set_conversion_settings(custom, put)
    assert cp.get_conversion_settings(get) == custom
    store[("conversion", "nvenc_cq")] = "abc"
    store[("conversion", "disc_images")] = "bogus"
    back = cp.get_conversion_settings(get)
    assert back.nvenc_cq == 30 and back.disc_images == cp.DISC_LEAVE
    gpu = cp.ConversionSettings(encoder="nvenc", hw_decode=True, x265_crf=24)
    cp.set_conversion_settings(gpu, put)
    assert store[("conversion", "encoder")] == "nvenc" and store[("conversion", "hw_decode")] == "1"
    assert cp.get_conversion_settings(get) == gpu
    store[("conversion", "encoder")] = "quantum"
    store[("conversion", "x265_crf")] = "99"
    back = cp.get_conversion_settings(get)
    assert back.encoder == "auto" and back.x265_crf == 27 and back.hw_decode is True


def test_ffmpeg_args_added_subtitles():
    m = info(".mkv", size=300 * MB, h=480, w=640, subs=("subrip",))
    plan = cp.Plan(container=".mkv", reasons=["x"])
    added = [
        cp.AddedSub("C:/t/en.srt", "eng", "", default=False),
        cp.AddedSub("C:/t/en2.srt", "eng", "(SDH)", hearing_impaired=True),
    ]
    a = cp.build_ffmpeg_args(m, plan, "in.mkv", "out.mkv", added=added)
    assert a.count("-i") == 3 and "1:0" in a and "2:0" in a
    assert a.index("-i") < a.index("1:0")
    assert "-metadata:s:s:1" in a and "language=eng" in a and "title=(SDH)" in a  # position after the kept one
    assert a[a.index("-disposition:s:2") + 1] == "hearing_impaired" and a[a.index("-disposition:s:1") + 1] == "0"
    assert a[a.index("-c:s:0") + 1] == "copy" and a[a.index("-c:s:1") + 1] == "copy" and a[a.index("-c:s:2") + 1] == "copy"
    import pytest
    with pytest.raises(ValueError):
        cp.build_ffmpeg_args(m, cp.Plan(container=".mp4", reasons=["x"]), "in", "out.mp4", added=added)
