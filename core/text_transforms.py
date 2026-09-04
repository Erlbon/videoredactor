"""
Pure text-transformation functions backing three batch Operations
commands: Case Conversion, Search/Replace, and Auto-Numbering. Kept
separate from any GUI code specifically so this logic is testable
without PyQt6 -- the dialogs themselves are thin wrappers that build a
preview table from these functions and, on Apply, write the results
onto selected files' metadata (staged, not saved -- same "Apply
doesn't mean Save" principle as every other batch metadata tool in
this project).
"""

from __future__ import annotations
import re

# Small words conventionally left lowercase in title case, UNLESS
# they're the first or last word of the string -- matches the common
# "proper" title-casing convention (e.g. "The Lord of the Rings", not
# "The Lord Of The Rings"). Deliberately not exhaustive (full style
# guides disagree on the exact list) -- this covers the common English
# articles, coordinating conjunctions, and short prepositions, which
# covers the overwhelming majority of real file/show titles.
_TITLE_CASE_SMALL_WORDS = {
    "a", "an", "the", "and", "but", "or", "nor", "for", "so", "yet",
    "at", "by", "in", "into", "of", "off", "on", "onto", "to", "up",
    "as", "if", "vs", "via",
}

CASE_MODES = ("upper", "lower", "title", "sentence")


def apply_case_conversion(text: str, mode: str) -> str:
    """Convert `text`'s case per `mode`:
    - "upper": "the lord of the rings" -> "THE LORD OF THE RINGS"
    - "lower": "THE LORD OF THE RINGS" -> "the lord of the rings"
    - "title": "the lord of the rings" -> "The Lord of the Rings"
      (small words lowercase unless first/last word, OR immediately
      after a colon/dash -- "Mission: Impossible" and "Star Wars: A
      New Hope" both capitalize correctly, matching how movie/show
      subtitles are conventionally capitalized; see
      _TITLE_CASE_SMALL_WORDS above. Deliberately NOT Python's built-in
      str.title(), which mangles apostrophes: "don't stop" ->
      "Don'T Stop" is a real, well-known str.title() bug this avoids
      by capitalizing only each word's first alphabetic character
      rather than every character following a non-letter)
    - "sentence": "THE LORD OF THE RINGS" -> "The lord of the rings"
      (only the first letter of the whole string capitalized, matching
      normal sentence capitalization -- not every sentence within it,
      since metadata fields are typically titles/names, not paragraphs)

    Empty/whitespace-only input returns unchanged -- nothing meaningful
    to convert.
    """
    if not text or not text.strip():
        return text
    if mode == "upper":
        return text.upper()
    if mode == "lower":
        return text.lower()
    if mode == "sentence":
        stripped = text.lstrip()
        leading_ws = text[: len(text) - len(stripped)]
        if not stripped:
            return text
        return leading_ws + stripped[0].upper() + stripped[1:].lower()
    if mode == "title":
        return _title_case(text)
    raise ValueError(f"Unknown case mode: {mode!r} (expected one of {CASE_MODES})")


def _title_case(text: str) -> str:
    words = text.split(" ")
    result_words = []
    last_index = len(words) - 1
    # A word right after a colon/dash starts a new clause -- e.g. the
    # "A" in "Star Wars: A New Hope" or "Mission: Impossible" should
    # capitalize even though "a" is normally a small word, matching
    # how movie/show subtitles are conventionally capitalized. Without
    # this, only the very first word of the whole string would ever
    # force-capitalize a small word, which is wrong for the extremely
    # common "Title: Subtitle" pattern this app deals with constantly.
    force_capitalize_next = False
    for i, word in enumerate(words):
        if not word:
            result_words.append(word)  # preserve multiple consecutive spaces
            force_capitalize_next = False
            continue
        lower_word = word.lower()
        is_boundary = i == 0 or i == last_index or force_capitalize_next
        if not is_boundary and lower_word in _TITLE_CASE_SMALL_WORDS:
            result_words.append(lower_word)
        else:
            result_words.append(_capitalize_first_letter(word))
        force_capitalize_next = word.rstrip().endswith((":", "-", "\u2014", "\u2013"))
    return " ".join(result_words)


def _capitalize_first_letter(word: str) -> str:
    """Capitalize the first ALPHABETIC character, lowercase the rest --
    "don't" -> "Don't" (not "Don'T", str.title()'s bug), "O'Brien" ->
    "O'brien" is an accepted tradeoff (no dictionary of surname
    exceptions -- matches how mp3tag's own case-conversion behaves for
    the same reason).
    """
    for i, ch in enumerate(word):
        if ch.isalpha():
            return word[:i] + ch.upper() + word[i + 1:].lower()
    return word  # no alphabetic character at all (e.g. "123", "--")


def apply_search_replace(text: str, search: str, replace: str, case_sensitive: bool = True) -> str:
    """Plain substring replace (not regex -- see module docstring for
    why). Empty `search` returns `text` unchanged rather than raising
    or doing something surprising with Python's own str.replace("")
    behavior (which would insert `replace` between every character).
    """
    if not search:
        return text
    if case_sensitive:
        return text.replace(search, replace)
    # Case-insensitive replace via regex with re.escape() so special
    # regex characters in `search` are treated literally, not as
    # pattern syntax -- this function promises substring replace, not
    # regex replace, regardless of case sensitivity.
    pattern = re.compile(re.escape(search), re.IGNORECASE)
    return pattern.sub(lambda m: replace, text)


def generate_auto_number(index: int, start: int, increment: int, padding: int) -> str:
    """The i-th (0-indexed) number in an auto-numbering sequence,
    zero-padded to `padding` digits (padding=0 means no padding).
    generate_auto_number(0, start=1, increment=1, padding=2) -> "01"
    generate_auto_number(2, start=5, increment=10, padding=0) -> "25"
    """
    value = start + index * increment
    if padding <= 0:
        return str(value)
    # Negative numbers: zfill still pads correctly (Python's str.zfill
    # keeps the sign character and pads the digits after it), e.g.
    # str(-5).zfill(3) == "-05", not "0-5" -- confirmed by test.
    return str(value).zfill(padding)


def apply_auto_number_to_text_field(current_value: str, number_str: str, separator: str) -> str:
    """For a TEXT field (Title, Track Title, etc.): prefix the current
    value with the formatted number and separator -- "Pilot" + "01" +
    " - " -> "01 - Pilot". An empty current_value just gives the
    number alone (no dangling separator with nothing after it).
    """
    if not current_value:
        return number_str
    return f"{number_str}{separator}{current_value}"
