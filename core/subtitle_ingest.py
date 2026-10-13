"""
core/subtitle_ingest.py

External SubRip (.srt) files beside a video, ingested INTO the container (and removed afterwards by the
caller, once the mux is verified). Pure logic: finding and decoding the files, tagging languages (ISO 639-2),
ordering, default / hearing-impaired flags, titles, and the "already embedded" duplicate check.

Rules (core/conversion_plan.ConversionSettings.subtitle_languages = the optional whitelist, default "all"):
  * only .srt is ingested; decoded to UTF-8 first (UTF-8/BOM, else the legacy codepage of the language);
  * muxed as "srt" in MKV; an MP4 source therefore becomes an MKV when external subtitles are added;
  * all supplied languages are ingested, ordered English first, then by language code;
  * default flag: the first plain English track, only when the video had no subtitles at all;
  * two tracks of one language: the one with more [sound tags] / music notes is "(SDH)" (hearing impaired);
    more than two: "(1)", "(2)", ...;
  * an external track over 90 percent identical to an embedded one is skipped.
"""

from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass, field

# ISO 639-1 (and common names) -> ISO 639-2/B codes, the table behind the language tags.
ISO_639_2 = {
    "en": "eng", "nl": "dut", "de": "ger", "fr": "fre", "es": "spa", "it": "ita", "pt": "por", "sv": "swe",
    "da": "dan", "no": "nor", "nb": "nor", "nn": "nor", "fi": "fin", "is": "ice", "pl": "pol", "cs": "cze",
    "sk": "slo", "hu": "hun", "ro": "rum", "bg": "bul", "hr": "hrv", "sr": "srp", "sl": "slv", "bs": "bos",
    "mk": "mac", "sq": "alb", "el": "gre", "tr": "tur", "ru": "rus", "uk": "ukr", "be": "bel", "et": "est",
    "lv": "lav", "lt": "lit", "he": "heb", "ar": "ara", "fa": "per", "hi": "hin", "bn": "ben", "ta": "tam",
    "te": "tel", "ur": "urd", "th": "tha", "vi": "vie", "id": "ind", "ms": "may", "zh": "chi", "ja": "jpn",
    "ko": "kor", "ca": "cat", "eu": "baq", "gl": "glg", "cy": "wel", "ga": "gle", "af": "afr", "ka": "geo",
    "hy": "arm", "az": "aze", "kk": "kaz", "mn": "mon", "ne": "nep", "si": "sin", "sw": "swa", "tl": "tgl",
    "fil": "fil", "pb": "por", "ze": "chi", "zt": "chi", "zh-cn": "chi", "zh-tw": "chi", "pt-br": "por",
}
_NAMES = {
    "english": "eng", "dutch": "dut", "german": "ger", "french": "fre", "spanish": "spa", "italian": "ita",
    "portuguese": "por", "swedish": "swe", "danish": "dan", "norwegian": "nor", "finnish": "fin",
    "icelandic": "ice", "polish": "pol", "czech": "cze", "slovak": "slo", "hungarian": "hun",
    "romanian": "rum", "bulgarian": "bul", "croatian": "hrv", "serbian": "srp", "slovenian": "slv",
    "greek": "gre", "turkish": "tur", "russian": "rus", "ukrainian": "ukr", "estonian": "est",
    "latvian": "lav", "lithuanian": "lit", "hebrew": "heb", "arabic": "ara", "persian": "per", "farsi": "per",
    "hindi": "hin", "thai": "tha", "vietnamese": "vie", "indonesian": "ind", "malay": "may", "chinese": "chi",
    "japanese": "jpn", "korean": "kor", "catalan": "cat", "bosnian": "bos", "macedonian": "mac",
    "albanian": "alb", "brazilian": "por",
}
# Already a 639-2 code (three letters): accepted as is.
_KNOWN_3 = frozenset(ISO_639_2.values()) | {"und", "mul"}
_SDH_TAGS = frozenset({"sdh", "hi", "cc", "hearing", "impaired"})
_IGNORED_TAGS = frozenset({"forced", "default", "srt"})

# Legacy codepage per language, used when the file isn't UTF-8.
_CODEPAGE = {
    "pol": "cp1250", "cze": "cp1250", "slo": "cp1250", "hun": "cp1250", "rum": "cp1250", "hrv": "cp1250",
    "slv": "cp1250", "bos": "cp1250", "alb": "cp1250", "srp": "cp1250",
    "rus": "cp1251", "ukr": "cp1251", "bul": "cp1251", "bel": "cp1251", "mac": "cp1251",
    "gre": "cp1253", "tur": "cp1254", "heb": "cp1255", "ara": "cp1256", "per": "cp1256",
    "est": "cp1257", "lav": "cp1257", "lit": "cp1257", "tha": "cp874", "vie": "cp1258",
    "jpn": "cp932", "kor": "cp949", "chi": "gb18030",
}
DEFAULT_CODEPAGE = "cp1252"  # Western European: English, Dutch, German, French, Nordic...


def language_code(token: str) -> str:
    """"en" / "English" / "eng" / "pt-BR" -> an ISO 639-2 code, "" when it isn't a language."""
    t = (token or "").strip().lower().replace("_", "-")
    if t in ISO_639_2:
        return ISO_639_2[t]
    if t in _NAMES:
        return _NAMES[t]
    if len(t) == 3 and t in _KNOWN_3:
        return t
    return ""


@dataclass
class ExternalSub:
    path: str
    language: str  # ISO 639-2, "und" when the name doesn't say
    hearing_impaired_hint: bool = False  # ".sdh" / ".hi" in the name
    text: str = ""  # decoded UTF-8 text (filled by load())
    title: str = ""
    default: bool = False
    hearing_impaired: bool = False


def parse_subtitle_name(video_path: str, sub_path: str) -> ExternalSub | None:
    """The language of `sub_path` from its name beside `video_path`: "<stem>.srt", "<stem>.<lang>.srt",
    "<stem>.<lang>.sdh.srt", or "<stem> - <Language>.srt". None when it isn't an .srt of this video."""
    if os.path.splitext(sub_path)[1].lower() != ".srt":
        return None
    stem = os.path.splitext(os.path.basename(video_path))[0]
    name = os.path.splitext(os.path.basename(sub_path))[0]
    if name.lower() == stem.lower():
        return ExternalSub(sub_path, "und")
    if not name.lower().startswith(stem.lower()):
        return None
    rest = name[len(stem):].lstrip(" ._-")
    lang, sdh = "", False
    for token in re.split(r"[ ._\-]+", rest):
        low = token.lower()
        if low in _SDH_TAGS:
            sdh = True
        elif low in _IGNORED_TAGS or not low:
            continue
        else:
            code = language_code(token)
            if code and not lang:
                lang = code
            elif not code and not lang:
                return None  # something else after the stem: not this video's subtitle
    return ExternalSub(sub_path, lang or "und", hearing_impaired_hint=sdh)


def find_external_subs(video_path: str, listdir=os.listdir) -> list[ExternalSub]:
    """The .srt files beside the video that belong to it (see parse_subtitle_name), sorted by name."""
    folder = os.path.dirname(os.path.abspath(video_path))
    try:
        names = sorted(listdir(folder))
    except OSError:
        return []
    out = []
    for name in names:
        sub = parse_subtitle_name(video_path, os.path.join(folder, name))
        if sub is not None:
            out.append(sub)
    return out


class SubtitleDecodeError(ValueError):
    """The bytes are not readable subtitle text in any encoding this module knows: the file is left alone."""


_BOMS = (  # longest first: the UTF-32 LE BOM starts with the UTF-16 LE one
    (b"\xff\xfe\x00\x00", "utf-32"),
    (b"\x00\x00\xfe\xff", "utf-32"),
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
)
_REPLACEMENT_RUN = "�" * 3


def _looks_broken(text: str) -> bool:
    """A NUL (UTF-16 read as a single-byte codepage) or a run of replacement characters means the
    decoding went wrong, whatever the codec said."""
    return "\x00" in text or _REPLACEMENT_RUN in text


def decode_srt(data: bytes, language: str = "und") -> str:
    """Bytes -> text: a UTF-16/UTF-32 BOM wins; else UTF-8 (with or without BOM) when it decodes, else the
    language's legacy codepage, else cp1252. Raises SubtitleDecodeError when the result still contains NUL
    characters or runs of U+FFFD: that file is not ingested (a garbled track must never replace a readable
    .srt)."""
    text = None
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            try:
                text = data.decode(encoding)
            except UnicodeDecodeError as exc:
                raise SubtitleDecodeError(f"{encoding} with a BOM, but the content doesn't decode: {exc}") from exc
            break
    if text is None:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            page = _CODEPAGE.get(language, DEFAULT_CODEPAGE)
            for encoding in (page, DEFAULT_CODEPAGE):
                try:
                    text = data.decode(encoding)
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
            else:
                text = data.decode(DEFAULT_CODEPAGE, errors="replace")
    if _looks_broken(text):
        raise SubtitleDecodeError("not readable text in any known encoding (NUL or replacement characters)")
    return text


_SOUND_TAG_RE = re.compile(r"\[[^\]\n]{1,60}\]|\([A-Z][A-Z ,.'-]{2,40}\)|[♪♫]")


def sound_tag_count(text: str) -> int:
    """How many [sound tags], (UPPERCASE CUES) and music notes the subtitle text has: the SDH signal."""
    return len(_SOUND_TAG_RE.findall(text))


def subtitle_plain_text(srt: str) -> str:
    """The spoken text of an SRT without numbers and timings, lowercased with collapsed whitespace."""
    lines = []
    for line in srt.splitlines():
        line = line.strip()
        if not line or line.isdigit() or "-->" in line:
            continue
        lines.append(re.sub(r"<[^>]+>", "", line))
    return re.sub(r"\s+", " ", " ".join(lines)).strip().lower()


def similarity(a: str, b: str) -> float:
    """0..1, how alike two subtitles' spoken text is. Compares word sequences (fast enough for a whole file)."""
    wa, wb = subtitle_plain_text(a).split(), subtitle_plain_text(b).split()
    if not wa or not wb:
        return 0.0
    return difflib.SequenceMatcher(None, wa, wb, autojunk=False).ratio()


def duplicate_of_embedded(sub_text: str, embedded_texts: list[str], threshold: float = 0.9) -> bool:
    return any(similarity(sub_text, e) > threshold for e in embedded_texts)


def language_whitelist(setting: str) -> set[str] | None:
    """"all" / "" -> None (no filter); "en, nl, ger" -> {"eng", "dut", "ger"}."""
    if not setting.strip() or setting.strip().lower() == "all":
        return None
    return {c for c in (language_code(t) for t in re.split(r"[,; ]+", setting)) if c}


def order_key(sub: ExternalSub):
    return (sub.language != "eng", sub.language, sub.title, sub.path.lower())


def prepare(subs: list[ExternalSub], *, video_had_subtitles: bool, whitelist: set[str] | None = None) -> list[ExternalSub]:
    """Filters (whitelist), orders (English first, then by code), labels SDH / numbering within a language and
    sets the default flag. `subs` must have .text filled in. Returns the tracks to mux, in order."""
    chosen = [s for s in subs if whitelist is None or s.language in whitelist]
    chosen.sort(key=order_key)
    by_language: dict[str, list[ExternalSub]] = {}
    for s in chosen:
        by_language.setdefault(s.language, []).append(s)
    for group in by_language.values():
        if len(group) == 2:
            a, b = group

            def score(s: ExternalSub) -> int:  # a name tagged .sdh/.hi counts above any tag count
                return sound_tag_count(s.text) + (1000 if s.hearing_impaired_hint else 0)

            richer, plain = (a, b) if score(a) >= score(b) else (b, a)
            if score(richer) > score(plain):
                richer.title, richer.hearing_impaired = "(SDH)", True
            else:  # no difference to go by: keep file order, no SDH claim
                a.title, b.title = "(1)", "(2)"
        elif len(group) > 2:
            for n, s in enumerate(group, start=1):
                s.title = f"({n})"
        elif group[0].hearing_impaired_hint:
            group[0].hearing_impaired = True
            group[0].title = "(SDH)"
    if not video_had_subtitles:
        for s in chosen:
            if s.language == "eng" and not s.hearing_impaired:
                s.default = True  # the first plain English track
                break
    return chosen


def load(sub: ExternalSub, reader=None) -> ExternalSub:
    """Reads and decodes the file into sub.text (`reader` = path -> bytes, injectable)."""
    if reader is None:
        def reader(p):  # noqa: E306
            with open(p, "rb") as f:
                return f.read()
    sub.text = decode_srt(reader(sub.path), sub.language)
    return sub


@dataclass
class IngestTrack:
    """One external subtitle ready for the mux: its UTF-8 temp file plus the stream tags."""

    source: ExternalSub
    temp_path: str = ""
    tags: dict = field(default_factory=dict)
