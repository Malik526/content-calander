"""Tests for transcript_metrics.py: WEBVTT normalization and WER/CER math.
Pure functions, no I/O, no mocking needed."""

import transcript_metrics as tm


# ---------------------------------------------------------------------------
# strip_webvtt
# ---------------------------------------------------------------------------

def test_strip_webvtt_extracts_plain_text():
    vtt = (
        "WEBVTT\n\n"
        "1\n"
        "00:00:00.000 --> 00:00:02.500\n"
        "Hello there, welcome back.\n\n"
        "2\n"
        "00:00:02.500 --> 00:00:05.000\n"
        "Today we're going to talk about something.\n"
    )
    assert tm.strip_webvtt(vtt) == "Hello there, welcome back. Today we're going to talk about something."


def test_strip_webvtt_ignores_note_and_style_blocks():
    vtt = (
        "WEBVTT\n\n"
        "NOTE this is a comment\nspanning two lines\n\n"
        "STYLE\n::cue { color: white; }\n\n"
        "00:00:00.000 --> 00:00:01.000\n"
        "Only this counts.\n"
    )
    assert tm.strip_webvtt(vtt) == "Only this counts."


def test_strip_webvtt_strips_inline_tags():
    vtt = (
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:01.000\n"
        "<v Speaker>Hello <00:00:00.500><c> world</c></v>\n"
    )
    result = tm.strip_webvtt(vtt)
    assert "<" not in result
    assert "Hello" in result and "world" in result


def test_strip_webvtt_handles_no_header():
    vtt = "00:00:00.000 --> 00:00:01.000\nNo header line here.\n"
    assert tm.strip_webvtt(vtt) == "No header line here."


def test_strip_webvtt_empty_input_returns_empty_string():
    assert tm.strip_webvtt("") == ""
    assert tm.strip_webvtt("   \n  ") == ""


def test_strip_webvtt_ignores_blocks_without_timestamp():
    vtt = "WEBVTT\n\njust some stray text with no timestamp\n"
    assert tm.strip_webvtt(vtt) == ""


# ---------------------------------------------------------------------------
# normalize_for_wer
# ---------------------------------------------------------------------------

def test_normalize_for_wer_lowercases_and_strips_punctuation():
    assert tm.normalize_for_wer("Hello, World!!  It's Great.") == "hello world it's great"


def test_normalize_for_wer_empty_string():
    assert tm.normalize_for_wer("") == ""
    assert tm.normalize_for_wer(None) == ""


# ---------------------------------------------------------------------------
# word_error_rate
# ---------------------------------------------------------------------------

def test_wer_identical_strings_is_zero():
    assert tm.word_error_rate("hello world", "hello world") == 0.0


def test_wer_completely_different_is_one():
    assert tm.word_error_rate("hello world", "goodbye moon") == 1.0


def test_wer_single_substitution():
    # 1 substitution / 2 reference words
    assert tm.word_error_rate("hello world", "hello there") == 0.5


def test_wer_insertion_in_hypothesis():
    # reference has 2 words; hypothesis adds one extra word (1 insertion)
    assert tm.word_error_rate("hello world", "hello big world") == 0.5


def test_wer_deletion_in_hypothesis():
    # reference has 3 words; hypothesis drops one (1 deletion)
    assert tm.word_error_rate("hello big world", "hello world") == 1 / 3


def test_wer_ignores_case_and_punctuation():
    assert tm.word_error_rate("Hello, World!", "hello world") == 0.0


def test_wer_empty_reference_returns_none():
    assert tm.word_error_rate("", "anything") is None
    assert tm.word_error_rate("   ", "") is None


def test_wer_empty_hypothesis_with_real_reference_is_one():
    assert tm.word_error_rate("hello world", "") == 1.0


# ---------------------------------------------------------------------------
# character_error_rate
# ---------------------------------------------------------------------------

def test_cer_identical_strings_is_zero():
    assert tm.character_error_rate("hello", "hello") == 0.0


def test_cer_single_character_substitution():
    assert tm.character_error_rate("cat", "cap") == 1 / 3


def test_cer_empty_reference_returns_none():
    assert tm.character_error_rate("", "anything") is None
