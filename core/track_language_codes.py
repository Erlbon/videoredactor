"""
core/track_language_codes.py

The language list of Metadata > Set Track Language: ISO 639-2/B three-letter codes (what MP4's mdhd atom and
Matroska's legacy track language hold) with their English names, a 639-1 two-letter code where there is one (for
Matroska's language-ietf), and 'und' (undetermined). A small constant table, no network. English and Norwegian first,
then Swedish, Danish, German, French, Spanish, Italian, Japanese, Korean, Chinese, Russian, then the rest by name.
"""

from __future__ import annotations

UNDETERMINED = "und"

# (639-2/B code, 639-1 code or "", English name)
_FIRST = (
    ("eng", "en", "English"),
    ("nor", "no", "Norwegian"),
    ("swe", "sv", "Swedish"),
    ("dan", "da", "Danish"),
    ("ger", "de", "German"),
    ("fre", "fr", "French"),
    ("spa", "es", "Spanish"),
    ("ita", "it", "Italian"),
    ("jpn", "ja", "Japanese"),
    ("kor", "ko", "Korean"),
    ("chi", "zh", "Chinese"),
    ("rus", "ru", "Russian"),
)

_REST = (
    ("afr", "af", "Afrikaans"), ("alb", "sq", "Albanian"), ("ara", "ar", "Arabic"), ("arm", "hy", "Armenian"),
    ("baq", "eu", "Basque"), ("ben", "bn", "Bengali"), ("bos", "bs", "Bosnian"), ("bul", "bg", "Bulgarian"),
    ("cat", "ca", "Catalan"), ("cze", "cs", "Czech"), ("dut", "nl", "Dutch"), ("est", "et", "Estonian"),
    ("fil", "", "Filipino"), ("fin", "fi", "Finnish"), ("geo", "ka", "Georgian"), ("gre", "el", "Greek"),
    ("heb", "he", "Hebrew"), ("hin", "hi", "Hindi"), ("hrv", "hr", "Croatian"), ("hun", "hu", "Hungarian"),
    ("ice", "is", "Icelandic"), ("ind", "id", "Indonesian"), ("gle", "ga", "Irish"), ("lav", "lv", "Latvian"),
    ("lit", "lt", "Lithuanian"), ("mac", "mk", "Macedonian"), ("may", "ms", "Malay"), ("mao", "mi", "Maori"),
    ("per", "fa", "Persian"), ("pol", "pl", "Polish"), ("por", "pt", "Portuguese"), ("rum", "ro", "Romanian"),
    ("slo", "sk", "Slovak"), ("slv", "sl", "Slovenian"), ("srp", "sr", "Serbian"), ("tam", "ta", "Tamil"),
    ("tha", "th", "Thai"), ("tur", "tr", "Turkish"), ("ukr", "uk", "Ukrainian"), ("urd", "ur", "Urdu"),
    ("vie", "vi", "Vietnamese"), ("wel", "cy", "Welsh"), ("lat", "la", "Latin"), ("mul", "", "Multiple languages"),
    ("zxx", "", "No linguistic content"),
)

LANGUAGES: tuple[tuple[str, str, str], ...] = _FIRST + tuple(sorted(_REST, key=lambda row: row[2]))
_BY_CODE = {row[0]: row for row in LANGUAGES}

# 639-2/T and a few other spellings seen in files, mapped to the /B code this table uses.
_ALIASES = {
    "nob": "nor", "nno": "nor", "deu": "ger", "fra": "fre", "zho": "chi", "nld": "dut", "ces": "cze", "ell": "gre",
    "fas": "per", "isl": "ice", "mkd": "mac", "msa": "may", "mri": "mao", "ron": "rum", "slk": "slo", "sqi": "alb",
    "hye": "arm", "eus": "baq", "kat": "geo", "bod": "tib", "cym": "wel", "": UNDETERMINED,
}


def normalize(code: str) -> str:
    """A language tag as ffprobe reports it, as this table's 639-2/B code ('und' for none; an unknown three-letter
    code is kept as it is, lower case)."""
    code = (code or "").strip().lower()
    code = _ALIASES.get(code, code)
    if len(code) == 2:
        for row in LANGUAGES:
            if row[1] == code:
                return row[0]
    return code


def is_known(code: str) -> bool:
    return code == UNDETERMINED or code in _BY_CODE


def name_of(code: str) -> str:
    row = _BY_CODE.get(normalize(code))
    if row:
        return row[2]
    return "undetermined" if normalize(code) == UNDETERMINED else ""


def ietf_of(code: str) -> str:
    """The BCP 47 form for Matroska's language-ietf: the two-letter code where there is one, else the three-letter one."""
    code = normalize(code)
    row = _BY_CODE.get(code)
    return (row[1] or code) if row else code


def choices() -> list[tuple[str, str]]:
    """[(code, label)] for a combo box: 'eng - English' ... and 'und (undetermined)' last."""
    return [(row[0], f"{row[0]} - {row[2]}") for row in LANGUAGES] + [(UNDETERMINED, "und (undetermined)")]


def label(code: str) -> str:
    code = normalize(code)
    if code == UNDETERMINED:
        return "und (undetermined)"
    name = name_of(code)
    return f"{code} - {name}" if name else code
