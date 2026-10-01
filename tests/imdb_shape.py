"""Generates SYNTHETIC IMDb-style dataset files that have the real SHAPE of the files (column order, the
literal backslash-N for missing values, the titleType / aka type / region / language mixes, the null patterns
seen in IMDb's published files) but contain only invented ids and titles made of invented syllables.
Nothing here is, or is copied from, IMDb data (which must not be redistributed)."""

import random

from tests.imdb_fixture import AKAS_HEADER, BASICS_HEADER, EPISODE_HEADER, RATINGS_HEADER, write_tsv

N = "\\N"  # backslash + N

_SYLLABLES = ["bra", "kel", "mor", "vin", "tha", "lur", "qui", "zep", "dor", "fen", "gal", "hus", "jin", "nox", "pru", "sol"]
_PUNCT = ["", "", "", ": ", " - ", "'s ", " & ", " 2", ", "]
_ACCENTED = ["é", "ü", "ø", "ñ", "ô", "å", "ß", "ï"]

# titleType weights, shaped like the start of title.basics: mostly films and shorts, some series and episodes
_TYPES = [("movie", 50), ("short", 30), ("tvSeries", 4), ("tvEpisode", 8), ("tvMovie", 2), ("tvMiniSeries", 1),
          ("tvSpecial", 1), ("video", 2), ("videoGame", 1), ("tvShort", 1)]
# aka mixes seen in title.akas: types and attributes are mostly missing, language mostly missing
_AKA_TYPES = [N, N, "imdbDisplay", "imdbDisplay", "imdbDisplay", "alternative", "working", "tv", "festival", "dvd", "video"]
_AKA_REGIONS = ["US", "GB", "DE", "FR", "IT", "NO", "XWW", "XWG", "SE", "JP", "CA", "SUHH", "XEU", "DDDE", "BR"]
_AKA_ATTRS = [N, N, N, N, "literal title", "reissue title", "alternative spelling", "informal literal title"]


def word(rng, accent=0.1):
    w = "".join(rng.choice(_SYLLABLES) for _ in range(rng.randint(1, 3))).capitalize()
    if rng.random() < accent:
        i = rng.randrange(len(w))
        w = w[:i] + rng.choice(_ACCENTED) + w[i + 1:]
    return w


def make_title(rng):
    parts = [word(rng)]
    for _ in range(rng.randint(0, 3)):
        parts.append(rng.choice(_PUNCT) + word(rng))
    title = "".join(p if p[:1] in ":-'&, " else " " + p for p in parts).strip()
    if rng.random() < 0.03:
        title += ' "quoted"'
    return title


def generate(folder, n=2000, seed=7) -> dict:
    """Writes the four gz files into `folder` and returns {"basics": path, ..., "truth": [...]}.
    `truth` lists (tconst, kind, title, year, votes) of every generated title for property checks."""
    rng = random.Random(seed)
    basics, ratings, episodes, akas, truth = [], [], [], [], []
    series_ids: list[str] = []
    for i in range(n):
        # a mix of 7-digit and 8-digit ids, as in the real files (invented numbers)
        number = 100 + i if i % 5 else 91000000 + i
        tconst = f"tt{number:07d}"
        kind = rng.choices([t for t, _w in _TYPES], [w for _t, w in _TYPES])[0]
        title = make_title(rng)
        original = title if rng.random() < 0.8 else make_title(rng)
        year = N if rng.random() < 0.002 else str(rng.randint(1890, 2025))
        end_year = str(int(year) + rng.randint(0, 9)) if kind in ("tvSeries", "tvMiniSeries") and year != N and rng.random() < 0.7 else N
        runtime = N if rng.random() < 0.17 else str(rng.randint(1, 240))
        genres = N if rng.random() < 0.06 else ",".join(rng.sample(["Drama", "Comedy", "Short", "Sci-Fi", "Talk-Show", "Reality-TV",
                                                                      "Film-Noir", "Documentary", "Adult"], rng.randint(1, 3)))
        adult = "1" if rng.random() < 0.01 else "0"
        basics.append((tconst, kind, title, original, adult, year, end_year, runtime, genres))
        votes = None
        if kind != "tvEpisode" and rng.random() < 0.6 or kind == "tvEpisode" and rng.random() < 0.3:
            votes = int(10 ** rng.uniform(0, 5))
            ratings.append((tconst, f"{rng.uniform(1, 10):.1f}", str(votes)))
        truth.append((tconst, kind, title, None if year == N else int(year), votes or 0, adult == "1"))
        if kind in ("tvSeries", "tvMiniSeries"):
            series_ids.append(tconst)
        for ordering in range(1, rng.choice([1, 1, 2, 3, 5])):
            region = rng.choice(_AKA_REGIONS + [N, N])
            is_original = region == N and rng.random() < 0.7
            akas.append((tconst, str(ordering), original if is_original else make_title(rng), region,
                         rng.choice([N, N, N, "en", "de", "ja"]), "original" if is_original else rng.choice(_AKA_TYPES),
                         rng.choice(_AKA_ATTRS), "1" if is_original else "0"))
    for row in basics:
        if row[1] == "tvEpisode" and series_ids:
            parent = rng.choice(series_ids)
            if rng.random() < 0.16:
                episodes.append((row[0], parent, N, N))  # IMDb leaves both numbers empty on ~16% of episodes
            else:
                episodes.append((row[0], parent, str(rng.randint(1, 9)), str(rng.randint(1, 30))))
    # series that are not in the basics file, and an episode of such a series (10-character parent id)
    episodes.append(("tt91999999", "tt92000000", "1", "1"))
    files = {
        "basics": write_tsv(f"{folder}/title.basics.tsv.gz", BASICS_HEADER, basics),
        "ratings": write_tsv(f"{folder}/title.ratings.tsv.gz", RATINGS_HEADER, ratings),
        "episodes": write_tsv(f"{folder}/title.episode.tsv.gz", EPISODE_HEADER, episodes),
        "akas": write_tsv(f"{folder}/title.akas.tsv.gz", AKAS_HEADER, akas),
    }
    files["truth"] = truth
    files["rows"] = {"basics": basics, "ratings": ratings, "episodes": episodes, "akas": akas}
    return files
