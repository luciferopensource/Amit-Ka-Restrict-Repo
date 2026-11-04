# -*- coding: utf-8 -*-
"""
Unit tests for Telegram Media Forwarder & Caption Sanitiser Engine (Amit Variant)
Verifies strict blocklist, safe whitelist, metadata inspection, caption/filename sanitisation,
and strictly verifies NO @luciferdatabase branding or hyperlinks are present.
"""
import asyncio
import os
import sys
import re
import pytest

# Ensure parent directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Initialize event loop for Pyrofork/Pyrogram imports on Python 3.10-3.14+
try:
    loop = asyncio.get_running_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

from restrict_bot import (
    BLOCK_PART_REGEX,
    WATERMARK_CLEAN_REGEX,
    clean_promotional_watermarks,
    is_standalone_subtitle_file,
    should_drop_file,
    extract_telegram_file_names,
    is_message_blocked,
    sanitize_filename,
    smart_caption,
    parse_media_extension_and_split
)

# --- MOCK MESSAGE HELPERS FOR METADATA INSPECTION ---
class MockDocumentAttributeFilename:
    def __init__(self, file_name):
        self.file_name = file_name

class MockMedia:
    def __init__(self, file_name=None, attributes=None):
        self.file_name = file_name
        self.attributes = attributes or []

class MockMessage:
    def __init__(self, document=None, video=None, audio=None, caption=None, text=None, raw_attributes=None):
        self.document = document
        self.video = video
        self.audio = audio
        self.caption = caption
        self.text = text
        if raw_attributes:
            class RawDoc:
                attributes = raw_attributes
            class RawMedia:
                document = RawDoc()
            self._raw = RawMedia()
        else:
            self._raw = None

# ==============================================================================
# --- TEST SUITE ---
# ==============================================================================

class TestStrictBlocklist:
    """
    1. STRICT BLOCKLIST — DO NOT FORWARD (SKIP / DROP)
    A. All 'Part' Keyword Split Files (001 to 999) — WITH OR WITHOUT EXTENSION
    B. Standalone Subtitle Files (*.srt)
    """

    @pytest.mark.parametrize("filename", [
        "Movie.part001.mkv",
        "Series.part050.rar",
        "Moviepart001.mkv",
        "Avatarpart042.rar",
        "Movie part001.mp4",
        "Game part010.zip",
        "Movie.part001",
        "Moviepart001",
        "Movie part001",
        "part001",
        "part999",
        "Series.PART123.mkv",
        "Movie part 001.mkv",
        "Movie.part.050.rar",
        "Archive.part002"
    ])
    def test_part_keyword_splits_blocked(self, filename):
        blocked, reason = should_drop_file(filename)
        assert blocked is True
        assert reason == "part_keyword_split"

    @pytest.mark.parametrize("filename", [
        "English.srt",
        "Movie.eng.srt",
        "subtitles.srt",
        "AVATAR.2009.SRT",
        "series.s01e01.en.srt"
    ])
    def test_standalone_subtitles_blocked(self, filename):
        blocked, reason = should_drop_file(filename)
        assert blocked is True
        assert reason == "standalone_subtitle_srt"


class TestSafeWhitelist:
    """
    2. ALLOWED FILES TO FORWARD (SAFE WHITELIST)
    1. Full Movies & Web Series (single video/audio files)
    2. Raw Numeric Splits After Extension (.001 to .999)
    3. Raw Numeric Splits Without Extension (No 'part' keyword)
    4. Movies with 'Part' in Title (Single digit / Words)
    """

    @pytest.mark.parametrize("filename", [
        "Movie.mkv",
        "Movie.mp4",
        "Avatar.avi",
        "Series.S01E01.mkv",
        "Song.mp3",
        "Document.pdf"
    ])
    def test_full_movies_and_series_allowed(self, filename):
        blocked, _ = should_drop_file(filename)
        assert blocked is False

    @pytest.mark.parametrize("filename", [
        "Movie.mkv.001",
        "Movie.mkv.999",
        "Avatar.mp4.002",
        "Game.rar.010",
        "Archive.7z.005",
        "Film.zip.050"
    ])
    def test_raw_numeric_splits_after_extension_allowed(self, filename):
        blocked, _ = should_drop_file(filename)
        assert blocked is False

    @pytest.mark.parametrize("filename", [
        "movie 001",
        "movie 999",
        "movie001",
        "movie999",
        "movie.001",
        "movie.999",
        "Series 042",
        "Video 100"
    ])
    def test_raw_numeric_splits_without_extension_allowed(self, filename):
        blocked, _ = should_drop_file(filename)
        assert blocked is False

    @pytest.mark.parametrize("filename", [
        "Harry Potter and the Deathly Hallows Part 1.mkv",
        "Dune Part 2.mkv",
        "Rebel Moon - Part One A Child of Fire.mkv",
        "Baahubali The Beginning Part 1.mp4",
        "Fast & Furious 10 Part 1.mp4"
    ])
    def test_movies_with_part_in_title_allowed(self, filename):
        blocked, _ = should_drop_file(filename)
        assert blocked is False


class TestCaptionAndFilenameSanitisation:
    """
    3. CAPTION & FILENAME SANITISATION (FOR FORWARDED FILES)
    Target Watermark Words: HDHub4u, Hub4u, HDHub, HDHu
    AND verify NO luciferdatabase or personal hyperlinks are added.
    """

    @pytest.mark.parametrize("inp, expected", [
        ("movie 1080p hdhub4u.mkv", "movie 1080p.mkv"),
        ("movie 1080p HDHub.mkv.001", "movie 1080p.mkv.001"),
        ("[HDHub] Avatar 2009.mkv.002", "Avatar 2009.mkv.002"),
        ("Avatar 2009 @hub4u 001", "Avatar 2009 001"),
        ("Avatar 2009 @HDHUB 001", "Avatar 2009 001"),
        ("movie 1080p [hdhub4u].mkv", "movie 1080p.mkv"),
        ("movie 1080p - hdhub - .mkv.001", "movie 1080p.mkv.001"),
        ("(hdhu) Avatar 2009.mkv.002", "Avatar 2009.mkv.002"),
        ("Movie.1080p.HDHub4u.top.mkv", "Movie.1080p.mkv"),
        ("Series S01E01 [Hub4u].mkv", "Series S01E01.mkv"),
        ("Show 720p - HDHu - .mp4", "Show 720p.mp4")
    ])
    def test_filename_sanitisation(self, inp, expected):
        out = sanitize_filename(inp)
        assert out == expected

    @pytest.mark.parametrize("inp, expected_contains", [
        ("movie 1080p hdhub4u.mkv", "movie 1080p.mkv"),
        ("movie 1080p HDHub.mkv.001", "movie 1080p.mkv.001"),
        ("[HDHub] Avatar 2009.mkv.002", "Avatar 2009.mkv.002"),
        ("Avatar 2009 @hub4u 001", "Avatar 2009 001")
    ])
    def test_smart_caption_sanitisation_and_zero_lucifer_branding(self, inp, expected_contains):
        cap = smart_caption(inp)
        assert expected_contains in cap
        assert "hdhub" not in cap.lower()
        assert "hub4u" not in cap.lower()
        assert "hdhu" not in cap.lower()
        # Strictly verify no luciferdatabase branding or hyperlinks
        assert "luciferdatabase" not in cap.lower()
        assert "<a href=" not in cap.lower()
        assert "t.me" not in cap.lower()


class TestMetadataInspection:
    """
    4. TECHNICAL GUIDELINES
    Inspect actual Telegram document metadata (DocumentAttributeFilename)
    """

    def test_document_attribute_filename_blocked(self):
        msg = MockMessage(
            document=MockMedia(
                file_name="innocent_title.dat",
                attributes=[MockDocumentAttributeFilename(file_name="Movie.part001.mkv")]
            )
        )
        blocked, reason, matched = is_message_blocked(msg)
        assert blocked is True
        assert reason == "part_keyword_split"
        assert matched == "Movie.part001.mkv"

    def test_raw_mtproto_attribute_blocked(self):
        msg = MockMessage(
            raw_attributes=[MockDocumentAttributeFilename(file_name="English.srt")]
        )
        blocked, reason, matched = is_message_blocked(msg)
        assert blocked is True
        assert reason == "standalone_subtitle_srt"
        assert matched == "English.srt"

    def test_video_metadata_whitelisted(self):
        msg = MockMessage(
            video=MockMedia(
                file_name="Movie.mkv.001",
                attributes=[MockDocumentAttributeFilename(file_name="Movie.mkv.001")]
            )
        )
        blocked, _, _ = is_message_blocked(msg)
        assert blocked is False

    def test_movies_with_part_single_digit_whitelisted(self):
        msg = MockMessage(
            video=MockMedia(
                file_name="Harry Potter and the Deathly Hallows Part 1.mkv",
                attributes=[MockDocumentAttributeFilename(file_name="Harry Potter and the Deathly Hallows Part 1.mkv")]
            )
        )
        blocked, _, _ = is_message_blocked(msg)
        assert blocked is False
