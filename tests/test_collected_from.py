"""Collected from: where this copy came from (core/collected_from.py; owner's decisions 6A, 7B, 8A of 2026-11-18)."""

from __future__ import annotations

import pytest

from core import collected_from
from core import redact_steps as rs
from core.video_metadata import EDITABLE_FIELDS, ContentType, VideoMetadata, fields_for_content_type


@pytest.mark.parametrize("url, kind", [
    ("https://www.youtube.com/watch?v=zwT6DZCQi9k", "YouTube"),
    ("https://youtu.be/zwT6DZCQi9k", "YouTube"),
    ("https://music.youtube.com/watch?v=x", "YouTube"),
    ("https://vimeo.com/12345", "Vimeo"),
    ("https://www.dailymotion.com/video/x7", "Other website"),
    ("https://notyoutube.com.evil.example/x", "Other website"),
])
def test_kind_for_url(url, kind):
    assert collected_from.kind_for_url(url) == kind


def test_suggest_from_the_comment_yt_dlp_writes():
    md = VideoMetadata(comment="https://www.youtube.com/watch?v=zwT6DZCQi9k")
    assert collected_from.suggest(md)
    assert (md.collected_from, md.collected_from_detail, md.collected_from_suggested) == (
        "YouTube", "https://www.youtube.com/watch?v=zwT6DZCQi9k", True)


def test_suggest_never_overrides_a_value_of_its_own():
    md = VideoMetadata(comment="https://youtu.be/x", collected_from="TV broadcast")
    assert not collected_from.suggest(md)
    assert md.collected_from == "TV broadcast" and md.collected_from_detail == ""
    md = VideoMetadata(comment="https://youtu.be/x", collected_from_detail="MTV Europe, 1990")
    assert not collected_from.suggest(md)


def test_no_address_no_suggestion():
    md = VideoMetadata(comment="ripped from my own DVD")
    assert not collected_from.suggest(md) and md.collected_from == ""


def test_url_trailing_punctuation_is_dropped():
    assert collected_from.first_url("Source: https://vimeo.com/123.") == "https://vimeo.com/123"


@pytest.mark.parametrize("ctype", list(ContentType))
def test_every_content_type_shows_it_7b(ctype):
    assert {"collected_from", "collected_from_detail"} <= set(fields_for_content_type(ctype))


def test_it_is_an_editable_field_written_by_both_backends():
    from core import mkv_backend, mp4_backend

    assert "collected_from" in EDITABLE_FIELDS and "collected_from_detail" in EDITABLE_FIELDS
    assert {"collected_from", "collected_from_detail"} <= set(mp4_backend.CUSTOM_FIELDS)
    assert mkv_backend_tag_names(mkv_backend) >= {"COLLECTED_FROM", "COLLECTED_FROM_DETAIL"}


def mkv_backend_tag_names(module) -> set:
    names = set()
    for value in vars(module).values():
        if isinstance(value, dict) and value.get("collected_from") == "COLLECTED_FROM":
            names |= set(value.values())
    return names


def test_cli_accepts_only_the_pick_list():
    from videocli.fields import CliError, check_value

    assert check_value("collected_from", "youtube") == "YouTube"
    with pytest.raises(CliError):
        check_value("collected_from", "my mate's hard disk")
    assert check_value("collected_from_detail", "MTV Europe, 1990") == "MTV Europe, 1990"


def test_redact_step_is_a_review_only_suggestion(monkeypatch):
    from types import SimpleNamespace

    md = VideoMetadata(comment="https://vimeo.com/123")
    ctx = SimpleNamespace(work=SimpleNamespace(metadata=md, dirty=False))
    step = rs.CollectedFromStep()
    assert step.default_enabled is False
    result = step.execute(ctx)
    assert result.value.kind == "Vimeo" and result.value.detail == "https://vimeo.com/123"
    lines = step.apply_suggestion(ctx, result)
    assert ctx.work.dirty and not md.collected_from_suggested and lines


def test_redact_step_leaves_a_file_with_its_own_value_alone():
    from types import SimpleNamespace

    md = VideoMetadata(comment="https://vimeo.com/123", collected_from="DVD")
    ctx = SimpleNamespace(work=SimpleNamespace(metadata=md, dirty=False))
    assert rs.CollectedFromStep().execute(ctx).value is None
