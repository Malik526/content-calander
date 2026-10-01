"""Tests for media.hashtags.extract_hashtags (Milestone 3.10.1: Caption
Hashtag Parsing + Structured Metadata) — the deterministic token rules
documented in that module's docstring."""

import unicodedata

import pytest

from content_automation.media.hashtags import extract_hashtags


def test_extracts_one_hashtag():
    assert extract_hashtags("Shipped it #coding") == ["#coding"]


def test_extracts_multiple_hashtags_in_order():
    assert extract_hashtags("#saas then #coding then #buildinpublic") == ["#saas", "#coding", "#buildinpublic"]


def test_handles_hashtags_across_line_breaks():
    caption = "I built this today.\n\n#coding #saas\n#buildinpublic"
    assert extract_hashtags(caption) == ["#coding", "#saas", "#buildinpublic"]


def test_does_not_modify_the_caption():
    caption = "  I built this today.\n\n#coding   #saas \n"
    original = str(caption)
    extract_hashtags(caption)
    assert caption == original


@pytest.mark.parametrize(
    "caption, expected",
    [
        ("#développement", ["#développement"]),
        ("#東京 trip", ["#東京"]),
        ("#123challenge", ["#123challenge"]),
        ("#build_in_public", ["#build_in_public"]),
        ("#हिंदी", ["#हिंदी"]),  # Devanagari vowel signs are combining marks
        (unicodedata.normalize("NFD", "#café"), [unicodedata.normalize("NFD", "#café")]),
    ],
)
def test_handles_unicode_and_mixed_tags(caption, expected):
    assert extract_hashtags(caption) == expected


@pytest.mark.parametrize("caption", ["#", "a # b", "# coding", "#!", "trailing #", "", None])
def test_ignores_bare_hash_with_no_tag(caption):
    assert extract_hashtags(caption) == []


@pytest.mark.parametrize("caption", ["#1 fan", "since #2024"])
def test_ignores_all_digit_tags(caption):
    assert extract_hashtags(caption) == []


@pytest.mark.parametrize("caption", ["I write C# daily", "abc#def", "see example.com/page#section"])
def test_ignores_mid_word_hash(caption):
    assert extract_hashtags(caption) == []


def test_punctuation_and_emoji_end_a_tag():
    assert extract_hashtags("Love #coding, #saas! (#dev) #fun🔥") == ["#coding", "#saas", "#dev", "#fun"]


def test_chained_hashtags_without_spaces_are_split():
    assert extract_hashtags("#coding#saas") == ["#coding", "#saas"]
    # ...but only after a real hashtag, not after an ignored all-digit one.
    assert extract_hashtags("#1#fan") == []


def test_preserves_case_and_duplicate_occurrences():
    assert extract_hashtags("#Coding #coding #Coding") == ["#Coding", "#coding", "#Coding"]
