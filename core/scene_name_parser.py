"""
core/scene_name_parser.py

The name parser behind the ThePornDB / StashDB search guess (2026-10-18). Qt-free, no network. Reads ONE name (a file
stem or a folder name) and returns what it can say about the scene as separate pieces, because ThePornDB's search takes
structured filters (title, date, site, performers) and a text blob wastes them:

    title        'Anal Oil Latex'   CamelCase split, release noise, studio, date, year, scene marker and performers gone
    studio       'Evil Angel'       a known studio at the start ('Evil Angel - ...', 'EvilAngel.14.02.17...') or a studio
                                    in parentheses / brackets anywhere ('(Evil Angel)', '[Evil Angel]')
    date / year  2014-02-17 / 2014  a leading date (2014-02-17, 14.02.17, 140217), an ISO date anywhere, '(2022)', a
                                    trailing 4-digit year 1990..today ('2 Girls 1 Cup' keeps its numbers: not a year)
    scene        3                  sNN, sceneNN, scene NN, scNN, ptNN, partNN, part NN, cdNN, discNN (a movie split over several files: the part number) ('eNN' only with episodes=True,
                                    for a folder that is clearly one movie)
    performers   ['Charlotte Sartre', 'Ramon Nomar']   CamelCase 'FirstLast' tokens after the scene marker or as a
                                    trailing run, 'Title - A B, C D', 'Title with / feat. A B and C D'

Noise (XXX, WEB-DL, NEW, REPACK, 480p..4K, x264/HEVC, '(Split Scenes)', '-GRP' / '[GRP]' ...) is dropped. It is a
heuristic: it has to get CLOSE, the dialog shows the guess in boxes the owner can edit, and the search ladder relaxes
step by step when a filter is wrong.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from typing import Optional

MIN_PLAUSIBLE_YEAR = 1990

# --- leading date / year, studio prefix (moved here from scene_lookup, which re-exports them) ---------------------------

_DATE_FULL = re.compile(r"^[\[(]?((?:19|20)\d{2})[-._ ](\d{2})[-._ ](\d{2})[\])]?(?![0-9])")
_DATE_SHORT = re.compile(r"^(\d{2})[-._ ](\d{2})[-._ ](\d{2})(?![0-9])")
_DATE_COMPACT = re.compile(r"^(\d{2})(\d{2})(\d{2})(?![0-9])")
_YEAR_LEAD = re.compile(r"^[\[(]?((?:19|20)\d{2})[\])]?(?=[\s._]|-(?![0-9])|$)")
_DATE_ANYWHERE = re.compile(r"(?<![0-9])((?:19|20)\d{2})-(\d{2})-(\d{2})(?![0-9])")


def _valid_date(year: int, month: int, day: int) -> str:
    try:
        found = datetime.date(year, month, day)
    except ValueError:
        return ""
    if found.year < MIN_PLAUSIBLE_YEAR or found > datetime.date.today():
        return ""
    return found.isoformat()


def plausible_year(text: str) -> bool:
    return bool(re.fullmatch(r"(?:19|20)\d{2}", text or "")) and MIN_PLAUSIBLE_YEAR <= int(text) <= datetime.date.today().year


def split_leading_date(text: str) -> tuple[str, str, str]:
    """(date 'YYYY-MM-DD' or '', year or '', the rest) of a name that STARTS with a date or a year: 2014-02-17,
    2014.02.17, 14.02.17, 140217 (YY.MM.DD, only when valid and 1990..today), '2014 ' and '(2014)'."""
    text = (text or "").strip()
    match = _DATE_FULL.match(text)
    if match:
        date = _valid_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if date:
            return date, date[:4], text[match.end():].strip(" ._-")
    for pattern in (_DATE_SHORT, _DATE_COMPACT):
        match = pattern.match(text)
        if match:
            yy = int(match.group(1))
            date = _valid_date((1900 if yy >= 90 else 2000) + yy, int(match.group(2)), int(match.group(3)))
            if date:
                return date, date[:4], text[match.end():].strip(" ._-")
    match = _YEAR_LEAD.match(text)
    if match and MIN_PLAUSIBLE_YEAR <= int(match.group(1)):
        rest = text[match.end():].strip(" ._-")
        if rest:
            return "", match.group(1), rest
    return "", "", text


def squash_name(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def studio_prefix(text: str, studios) -> tuple[str, str]:
    """(the studio, the rest) when `text` starts with a known studio (words may be run together: 'EvilAngel.14.02.17')
    and the studio ends at a word boundary; the longest studio wins. ('', text) otherwise."""
    text = (text or "").strip()
    spans = [m for m in re.finditer(r"[^\s._-]+", text)]
    best_studio, best_count, best_len = "", 0, 0
    for studio in studios:
        want = squash_name(studio)
        if len(want) < 3 or len(want) <= best_len:
            continue
        for count in range(1, min(len(spans), 5) + 1):
            if squash_name("".join(m.group() for m in spans[:count])) == want:
                best_studio, best_count, best_len = studio, count, len(want)
    if not best_studio:
        return "", text
    return best_studio, text[spans[best_count - 1].end():].strip(" ._-")


def known_studio_equal(text: str, studios) -> str:
    """The known studio whose squashed name equals `text`'s, else ''."""
    want = squash_name(text)
    if len(want) < 3:
        return ""
    for studio in studios:
        if squash_name(studio) == want:
            return studio
    return ""


# --- noise ---------------------------------------------------------------------------------------------------------------

_NOISE_WORDS = {
    "xxx", "web", "webdl", "web-dl", "webrip", "web-rip", "bluray", "blu-ray", "bdrip", "brrip", "dvdrip", "hdrip", "hdtv",
    "hevc", "x264", "x265", "h264", "h265", "h.264", "h.265", "avc", "av1", "xvid", "divx", "aac", "aac2", "ac3", "mp3",
    "repack", "proper", "rerip", "internal", "uncut", "unrated", "uncensored", "remastered", "complete", "scenes",
    "4k", "8k", "uhd", "fhd", "hd", "sd", "hq", "60fps", "30fps", "10bit", "8bit", "vr", "mkv", "mp4", "wmv", "avi",
    "split", "web-dl-rip",
}
_NOISE_RE = re.compile(r"(?:\d{3,4}[pi]|aac\d(?:\.\d)?|ddp?\d(?:\.\d)?)", re.IGNORECASE)
_SPLIT_SCENES = re.compile(r"(?<![A-Za-z0-9])split[\s._-]*scenes?(?![A-Za-z])", re.IGNORECASE)
_GROUP_TAIL = re.compile(r"^(.+?)-([A-Za-z0-9]{2,12})$")


def is_noise(token: str) -> bool:
    low = (token or "").strip().lower().strip(",;")
    return low in _NOISE_WORDS or bool(_NOISE_RE.fullmatch(low))


# --- scene markers -------------------------------------------------------------------------------------------------------

_SCENE_ONE = re.compile(r"(?:scene|sc|part|pt|cd|disc|disk)0*(\d{1,3})", re.IGNORECASE)
_SCENE_S = re.compile(r"s0*(\d{1,2})", re.IGNORECASE)
_EPISODE = re.compile(r"e(?:p|pisode)?0*(\d{1,3})", re.IGNORECASE)
_SCENE_WORD = re.compile(r"(?:scene|sc|part|pt|cd|disc|disk)", re.IGNORECASE)
_NUMBER_TOKEN = re.compile(r"#?0*(\d{1,3})")


def _scene_marker(tokens: list[str], i: int, episodes: bool) -> Optional[tuple[int, int]]:
    """(tokens used, scene number) when tokens[i] starts a scene/part marker, else None."""
    token = tokens[i].strip(",;")
    match = _SCENE_ONE.fullmatch(token)
    if match:
        return 1, int(match.group(1))
    match = _SCENE_S.fullmatch(token)
    if match:
        return 1, int(match.group(1))
    if episodes:
        match = _EPISODE.fullmatch(token)
        if match:
            return 1, int(match.group(1))
    if _SCENE_WORD.fullmatch(token) and i + 1 < len(tokens):
        nxt = _NUMBER_TOKEN.fullmatch(tokens[i + 1].strip(",;"))
        if nxt:
            return 2, int(nxt.group(1))
    return None


# --- CamelCase ------------------------------------------------------------------------------------------------------------

_CAMEL_SPLIT = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[A-Za-z])(?=\d)")
_HAS_CAMEL = re.compile(r"[A-Z][A-Za-z'’]*[a-z][A-Z]")
_CAMEL_TOKEN = re.compile(r"[A-Z][A-Za-z'’]*\d*")
_MERGE_PREFIXES = ("Mc", "Mac")


def camel_parts(token: str) -> list[str]:
    """'AnalOilLatex' -> ['Anal', 'Oil', 'Latex']; 'McDonald' stays one word, acronyms stay together ('XXXHub' -> 'XXX',
    'Hub'); a token without a lower-to-upper step is returned as it is."""
    if not _CAMEL_TOKEN.fullmatch(token) or not _HAS_CAMEL.search(token):
        return [token]
    parts = [p for p in _CAMEL_SPLIT.split(token) if p]
    merged: list[str] = []
    i = 0
    while i < len(parts):
        if parts[i] in _MERGE_PREFIXES and i + 1 < len(parts) and not parts[i + 1].isdigit() and len(parts[i + 1]) >= 3:
            merged.append(parts[i] + parts[i + 1])
            i += 2
        else:
            merged.append(parts[i])
            i += 1
    return merged


def _camel_person(token: str) -> str:
    """'CharlotteSartre' -> 'Charlotte Sartre' when the token is 2..4 alphabetic CamelCase words, else ''."""
    parts = camel_parts(token)
    if 2 <= len(parts) <= 4 and all(re.fullmatch(r"[A-Z][a-z'’]+|Mc[A-Z][a-z]+|Mac[A-Z][a-z]+", p) for p in parts):
        return " ".join(parts)
    return ""


# --- performer lists ------------------------------------------------------------------------------------------------------

_NAME_WORD = re.compile(r"[A-Z][A-Za-z'’-]*\.?")
_LIST_SPLIT = re.compile(r"\s*(?:,|&|;|\+|\band\b)\s*")
_FEAT = re.compile(r"\s(with|feat\.?|featuring|ft\.?)\s+(.+)$", re.IGNORECASE)
_NOT_STUDIO_WORDS = {"cut", "edition", "version", "remastered", "extended", "uncut", "unrated", "director", "directors",
                     "special", "collectors", "collector", "dvd", "vol", "volume", "disc", "disk", "part", "scene",
                     "scenes", "full", "movie", "film", "series", "season", "episode", "compilation", "bonus", "trailer"}


def names_from(text: str) -> list[str]:
    """The performer names in a comma / '&' / 'and' list of 2-4 capitalised words each ('Mia Malkova, Riley Reid');
    a CamelCase 'MiaMalkova' counts as one. [] when any part does not look like a name."""
    words = [w for w in (text or "").split() if not is_noise(w)]
    cleaned = " ".join(words).strip(" ,;&+-")
    if not cleaned:
        return []
    names: list[str] = []
    for part in _LIST_SPLIT.split(cleaned):
        part = part.strip()
        if not part:
            continue
        pieces = part.split()
        if len(pieces) == 1:
            person = _camel_person(pieces[0])
            if not person:
                return []
            names.append(person)
            continue
        if not 2 <= len(pieces) <= 4 or not all(_NAME_WORD.fullmatch(p) for p in pieces):
            return []
        names.append(" ".join(pieces))
    return list(dict.fromkeys(names))


# --- the parse ------------------------------------------------------------------------------------------------------------


@dataclass
class ParsedName:
    title: str = ""
    studio: str = ""
    studio_how: str = ""          # 'prefix' (known studio first), 'known' (in brackets), 'brackets' (unknown, in brackets)
    date: str = ""
    year: str = ""
    scene: Optional[int] = None
    performers: list = field(default_factory=list)


_BRACKET = re.compile(r"[(\[]([^()\[\]]*)[)\]]")


def _title_from(words: list[str]) -> str:
    out: list[str] = []
    for word in words:
        out.extend(camel_parts(word))
    return " ".join(w.strip(",;") for w in out).strip(" -_.,")


def _classify_brackets(text: str, studios, result: ParsedName, episodes: bool) -> str:
    """Takes every (...) / [...] out of `text`, using what it says: a known studio, a year, a date, a scene marker; noise
    and release groups are dropped; an unknown capitalised name is kept as a lower-priority studio."""
    unknown_studio = ""

    def take(match: re.Match) -> str:
        nonlocal unknown_studio
        inner = match.group(1).strip()
        if not inner:
            return " "
        known = known_studio_equal(inner, studios)
        if known:
            result.studio, result.studio_how = result.studio or known, result.studio_how or "known"
            return " "
        if plausible_year(inner):
            result.year = result.year or inner
            return " "
        date = _DATE_ANYWHERE.fullmatch(inner)
        if date:
            found = _valid_date(int(date.group(1)), int(date.group(2)), int(date.group(3)))
            if found:
                result.date, result.year = result.date or found, result.year or found[:4]
            return " "
        words = [w for w in re.split(r"[\s._]+", inner) if w]
        if all(is_noise(w) for w in words) or _SPLIT_SCENES.search(inner):
            return " "
        marker = _scene_marker(words, 0, episodes) if words else None
        if marker and marker[0] == len(words):
            result.scene = result.scene if result.scene is not None else marker[1]
            return " "
        spaced = match.group(0)[0] == "(" or " " in inner
        if (not unknown_studio and spaced and 1 <= len(words) <= 4
                and all(re.fullmatch(r"[A-Z][A-Za-z0-9'’&-]*", w) for w in words)
                and not any(w.lower() in _NOT_STUDIO_WORDS for w in words)):
            unknown_studio = " ".join(words)
        return " "

    text = _BRACKET.sub(take, text)
    if unknown_studio and not result.studio:
        result.studio, result.studio_how = unknown_studio, "brackets"
    return text


def parse_scene_name(name: str, studios=(), episodes: bool = False) -> ParsedName:
    """Everything a file stem or a folder name says about the scene, see the module docstring. `studios` are the known
    studio names (the metadata's, the loaded files', COMMON_STUDIOS); `episodes` makes 'eNN' a scene marker."""
    result = ParsedName()
    text = (name or "").strip()
    text = _classify_brackets(text, studios, result, episodes)
    text = text.replace("_", " ")
    text = _SPLIT_SCENES.sub(" ", text)
    plain = not re.search(r"[\s._]", text.strip())  # no separator at all: hyphens may be the separators
    if not re.search(r"\s", text.strip()):
        text = text.replace(".", " ")
        if plain and text.count("-") >= 2:
            text = text.replace("-", " ")
    text = " ".join(text.split())

    # studio prefix, date, (studio prefix again when the date came first)
    for _round in range(2):
        found, after = studio_prefix(text, studios)
        if found and not result.studio:
            result.studio, result.studio_how = found, "prefix"
        elif found:
            result.studio_how = result.studio_how or "prefix"
        text = after
        date, year, text = split_leading_date(text)
        if date or year:
            result.date, result.year = result.date or date, result.year or year
        if found:
            break
    anywhere = _DATE_ANYWHERE.search(text)
    if anywhere:
        found_date = _valid_date(int(anywhere.group(1)), int(anywhere.group(2)), int(anywhere.group(3)))
        if found_date:
            result.date, result.year = result.date or found_date, result.year or found_date[:4]
            text = (text[:anywhere.start()] + " " + text[anywhere.end():]).strip()

    # performers: 'Title - A B, C D' and 'Title with / feat. A B and C D'
    segments = re.split(r"\s+-\s+", text)
    if len(segments) >= 2 and " ".join(segments[:-1]).strip():
        names = names_from(segments[-1])
        if names and (len(names) >= 2 or result.studio_how in ("prefix", "known", "brackets") or _LIST_SPLIT.search(segments[-1])):
            result.performers = names
            text = " ".join(segments[:-1])
    if not result.performers:
        feat = _FEAT.search(text)
        if feat and text[:feat.start()].strip():
            names = names_from(feat.group(2))
            if names and (len(names) >= 2 or feat.group(1).lower() != "with"):
                result.performers = names
                text = text[:feat.start()]

    # tokens
    tokens = [t for t in text.split() if t.strip("-.,;")]
    tokens = [t.rstrip(".") if not re.fullmatch(r"[A-Za-z]\.", t) else t for t in tokens]
    if tokens:
        tail = _GROUP_TAIL.match(tokens[-1])
        if tail and (is_noise(tail.group(1)) or plausible_year(tail.group(1))):
            tokens[-1] = tail.group(1)
    words: list[str] = []
    after_words: list[str] = []
    scene_seen = False
    i = 0
    while i < len(tokens):
        token = tokens[i]
        marker = _scene_marker(tokens, i, episodes)
        if marker and not scene_seen:
            scene_seen = True
            result.scene = result.scene if result.scene is not None else marker[1]
            i += marker[0]
            continue
        if marker:  # a second marker: dropped
            i += marker[0]
            continue
        if is_noise(token) or (token == "NEW" and (words or scene_seen)):
            i += 1
            continue
        (after_words if scene_seen else words).append(token)
        i += 1

    if scene_seen:
        for token in list(after_words):
            if plausible_year(token) and not result.year:
                result.year = token
                after_words.remove(token)
        people = [(_camel_person(t), t) for t in after_words]
        rest = [t for person, t in people if not person]
        result.performers = list(dict.fromkeys(result.performers + [p for p, _t in people if p]))
        if rest:
            result.performers = list(dict.fromkeys(result.performers + names_from(" ".join(rest))))
    else:
        run = 0
        while run < len(words) - 1 and _camel_person(words[len(words) - 1 - run]):
            run += 1
        if run >= 2:
            result.performers = list(dict.fromkeys(result.performers + [_camel_person(w) for w in words[len(words) - run:]]))
            words = words[: len(words) - run]
    # the year: the last word of the title part, when it is a plausible year and not the only word
    if len(words) > 1 and plausible_year(words[-1]):
        year = words.pop()
        result.year = result.year or year
    result.title = _title_from(words)
    return result
