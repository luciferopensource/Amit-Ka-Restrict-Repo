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
    apply_text_format,
    strip_existing_media_footers,
    parse_media_extension_and_split,
    balance_html_tags,
    clean_promotional_hyperlinks,
    append_media_footer
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

    def test_user_reported_promo_footer_stripped(self):
        # 1. Plain title line
        inp1 = (
            "Mr.Queen S01 [E16 - E20] COMBINED 720p AMZN WEB - DL x264 [Hindi + Tamil + Telugu + Korean (AAC 2.0)] ESubs ~ NΛKϟU ~ TMV.mkv\n"
            "💾 Size: 3.12 GB\n"
            "⚜️ Powered By : [\\[ ᴍᴏᴠɪᴇꜱ ʙᴏᴛᴢ ᴜᴘᴅᴀᴛᴇ \\]](https://t.me/+wiERZq-x3KBmOGU9)"
        )
        cap1 = smart_caption(inp1, file_size_bytes=int(3.12 * 1024 * 1024 * 1024))
        assert "movies botz" not in cap1.lower()
        assert "ᴍᴏᴠɪᴇꜱ" not in cap1
        assert "+wiERZq" not in cap1
        assert "luciferdatabase" not in cap1.lower()
        assert "<a href=" not in cap1.lower()
        assert "t.me" not in cap1.lower()
        assert "💾 Size: 3.12 GB" in cap1
        assert "Mr.Queen S01" in cap1

        # 2. Markdown-wrapped title with nested brackets and promotional link
        inp2 = (
            "[Mr.Queen S01 [E16 - E20] COMBINED 720p AMZN WEB - DL x264 [Hindi + Tamil + Telugu + Korean (AAC 2.0)] ESubs ~ NΛKϟU ~ TMV.mkv](https://t.me/+wiERZq-x3KBmOGU9)\n"
            "💾 Size: 3.12 GB\n"
            "⚜️ Powered By : [\\[ ᴍᴏᴠɪᴇꜱ ʙᴏᴛᴢ ᴜᴘᴅᴀᴛᴇ \\]](https://t.me/+wiERZq-x3KBmOGU9)"
        )
        cap2 = smart_caption(inp2, file_size_bytes=int(3.12 * 1024 * 1024 * 1024))
        assert "movies botz" not in cap2.lower()
        assert "ᴍᴏᴠɪᴇꜱ" not in cap2
        assert "+wiERZq" not in cap2
        assert "luciferdatabase" not in cap2.lower()
        assert "<a href=" not in cap2.lower()
        assert "t.me" not in cap2.lower()
        assert "💾 Size: 3.12 GB" in cap2
        assert "Mr.Queen S01" in cap2

    @pytest.mark.parametrize("footer_text", [
        "⚜️ Powered By : [\\[ ᴍᴏᴠɪᴇꜱ ʙᴏᴛᴢ ᴜᴘᴅᴀᴛᴇ \\]](https://t.me/+wiERZq-x3KBmOGU9)",
        "⚜️ Powered By : <a href=\"https://t.me/+wiERZq-x3KBmOGU9\">[ ᴍᴏᴠɪᴇꜱ ʙᴏᴛᴢ ᴜᴘᴅᴀᴛᴇ ]</a>",
        "⚜️ Powered By : [@luciferdatabase]",
        "Powered By : Unknown Bot",
        "💾 Size: 500 MB",
        "Uploaded By : SomeChannel",
        "🔗 Join @SomeChannel"
    ])
    def test_strip_existing_media_footers(self, footer_text):
        raw = f"Movie 1080p.mkv\n{footer_text}"
        res = strip_existing_media_footers(raw)
        assert res == "Movie 1080p.mkv"


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

    def test_movie_with_srt_in_caption_not_blocked(self):
        """
        Verify that a legitimate movie file is NOT blocked just because the
        description/caption mentions subtitles (e.g. 'Subtitles: English.srt').
        """
        msg = MockMessage(
            video=MockMedia(
                file_name="Dune.2021.1080p.mkv",
                attributes=[MockDocumentAttributeFilename(file_name="Dune.2021.1080p.mkv")]
            ),
            caption="🎬 Dune (2021) 1080p\nSubtitles: English.srt included."
        )
        blocked, reason, matched = is_message_blocked(msg)
        assert blocked is False
        assert reason == ""

    def test_text_post_mentioning_srt_not_blocked(self):
        """A plain text post that only mentions a .srt file has no media to block."""
        msg = MockMessage(text="Subtitles: English.srt available on request")
        blocked, reason, matched = is_message_blocked(msg)
        assert blocked is False

    def test_footer_never_contains_lucifer_branding(self):
        """White-label invariant: captions must never carry the personal channel branding."""
        out = append_media_footer(smart_caption("Neelpodmo (2025) 1080p.mkv"), 1024 ** 3)
        assert "luciferdatabase" not in out.lower()
        assert "<a href" not in out
        assert "💾 Size: 1.00 GB" in out

    def test_cloud_style_caption_fully_whitelabelled(self):
        """A caption copied from the personal cloud engine must arrive fully clean."""
        sample = (
            "🎬 File Name: Neelpodmo (2025) Bengali iScreen WEB-DL 1080p.mkv\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "💾 DB ID: [@LuciferDatabase] | 🌐 Source: WEB-DL\n"
            "⚖️ Size: 1.91 GB | 🎬 Format: MKV | ⚙️ Codec: WEB-DL\n"
            "📺 Resolution: 1080p\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "📢 Join: @LuciferDatabase for fast downloads & Database access.\n"
            "❓ Need a File? Request: [@RequestLuciferDatabase]"
        )
        out = append_media_footer(smart_caption(sample), 1024 ** 3)
        assert "luciferdatabase" not in out.lower()
        assert "<a href" not in out
        assert "Powered By" not in out
        assert "DB ID" not in out
        assert out.count("Size:") == 1

    def test_filename_containing_promo_word_is_preserved(self):
        sample = (
            "🎬 File Name: Request Movie (2024) 1080p.mkv\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "💾 DB ID: [@LuciferDatabase]\n"
            "⚖️ Size: 1.91 GB"
        )
        out = smart_caption(sample)
        assert "Request Movie (2024) 1080p.mkv" in out


class TestAuditedEdgeCases:
    """
    Unit tests verifying fixes for the 12 audited codebase defects in white-label environment.
    """

    def test_balance_html_tags_with_anchor_and_orphans(self):
        # 1. Unclosed <a>
        raw = '<b>Hello <a href="https://example.com">World'
        balanced = balance_html_tags(raw)
        assert balanced == '<b>Hello <a href="https://example.com">World</a></b>'

        # 2. Orphaned closing tag
        raw_orphan = 'Hello</b> World'
        balanced_orphan = balance_html_tags(raw_orphan)
        assert balanced_orphan == 'Hello World'

    def test_clean_promotional_hyperlinks_no_dangling_https(self):
        raw = "Check this movie out: https://t.me/somechannel or https://hdhub4u.com/download"
        res = clean_promotional_hyperlinks(raw)
        assert "https://" not in res
        assert "t.me" not in res
        assert "hdhub4u" not in res
        assert "@luciferdatabase" not in res

    def test_multi_markdown_links_on_single_line_not_corrupted(self):
        raw = "[Link1](https://t.me/channel1) and [Link2](https://t.me/channel2)"
        res = clean_promotional_hyperlinks(raw)
        assert "t.me" not in res
        assert "https://" not in res

    def test_caption_truncation_rebalances_html(self):
        long_text = "<b><a href=\"https://example.com\">" + ("A" * 1050) + "</a></b>"
        res = append_media_footer(long_text, file_size_bytes=1024*1024*50)
        assert len(res) <= 1024
        assert "💾 Size: 50.00 MB" in res
        assert res.count("<b>") == res.count("</b>")
        assert res.count("<a") == res.count("</a>")
        assert "@luciferdatabase" not in res

    def test_text_format_bold_italic_wraps_caption(self):
        out = apply_text_format("Movie.mkv", ["Bold", "Italic"])
        assert out == "<b><i>Movie.mkv</i></b>"

    def test_text_format_mono_strips_generic_links(self):
        base = '<a href="https://example.com/x">Movie.mkv</a>'
        out = apply_text_format(base, ["Mono"])
        assert out == "<code>Movie.mkv</code>"
        assert "<a href" not in out


