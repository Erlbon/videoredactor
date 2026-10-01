"""Tiny SYNTHETIC IMDb-style dataset files (gzipped TSV, header line, backslash-N for missing values)
for the importer and lookup tests. Nothing here is real IMDb data; the ids and titles only mimic its shape."""

import gzip
import os

from core import imdb_import

N = "\\N"  # backslash + N

BASICS_HEADER = ["tconst", "titleType", "primaryTitle", "originalTitle", "isAdult", "startYear", "endYear",
                 "runtimeMinutes", "genres"]
RATINGS_HEADER = ["tconst", "averageRating", "numVotes"]
EPISODE_HEADER = ["tconst", "parentTconst", "seasonNumber", "episodeNumber"]
AKAS_HEADER = ["titleId", "ordering", "title", "region", "language", "types", "attributes", "isOriginalTitle"]

BASICS = [
    # tconst, type, primary, original, adult, start, end, runtime, genres
    ("tt90000001", "movie", "Zarnak", "Zarnak", "0", "1984", N, "137", "Adventure,Drama,Sci-Fi"),
    ("tt90000002", "movie", "Zarnak", "Zarnak", "0", "2021", N, "155", "Action,Adventure,Drama"),
    ("tt90000003", "movie", "The Ember Cipher", "Das Ember-Siegel", "0", "1986", N, "130", "Crime,Drama,Mystery"),
    ("tt90000004", "tvSeries", "Quillfeather", "Quillfeather", "0", "2008", "2013", "49", "Crime,Drama,Thriller"),
    ("tt90000005", "tvEpisode", "Hatching", "Hatching", "0", "2008", N, "58", "Crime,Drama,Thriller"),
    ("tt90000006", "tvEpisode", "Sparrow's Dilemma...", "Sparrow's Dilemma...", "0", "2008", N, "48", "Crime,Drama"),
    ("tt90000007", "short", "Le jongleur et ses chiens", "Le jongleur et ses chiens", "0", "1892", N, "5", "Animation,Short"),
    ("tt90000008", "movie", "Adult Feature", "Adult Feature", "1", "2000", N, "80", "Adult"),
    ("tt90000009", "movie", "Obscure Film", "Obscure Film", "0", "1999", N, "90", "Drama"),
    ("tt90000010", "videoGame", "Some Game", "Some Game", "0", "2010", N, N, "Action"),
    ("tt90000011", "tvSpecial", "A Christmas Special", "A Christmas Special", "0", "1999", N, "45", "Comedy"),
    ("tt90000012", "tvSeries", "Unknown Show", "Unknown Show", "0", "2015", N, "30", "Drama"),
    ("tt90000013", "tvEpisode", "Lost Episode", "Lost Episode", "0", "2015", N, "30", "Drama"),
    ("tt90000014", "tvEpisode", "Orphan Episode", "Orphan Episode", "0", "2015", N, "30", "Drama"),
    ("tt90000015", "movie", "Mirabelle", "Le jardin secret de Mirabelle", "0", "2001", N, "122", "Comedy,Romance"),
    ("tt90000016", "movie", "Salt & Ember: The Film", "Salt & Ember: The Film", "0", "2000", N, "100", "Crime"),
]
RATINGS = [
    ("tt90000001", "6.1", "151234"), ("tt90000002", "8.0", "700000"), ("tt90000003", "7.7", "120000"),
    ("tt90000004", "9.5", "2000000"), ("tt90000005", "9.0", "30000"), ("tt90000006", "8.6", "28000"),
    ("tt90000008", "5.0", "900"), ("tt90000009", "6.0", "2"), ("tt90000011", "7.0", "50"),
    ("tt90000012", "7.0", "1"), ("tt90000015", "8.3", "800000"), ("tt90000016", "6.0", "1500"),
    ("tt90000007", "5.6", "2000"),
]
EPISODES = [
    ("tt90000005", "tt90000004", "1", "1"), ("tt90000006", "tt90000004", "1", "2"),
    ("tt90000013", "tt90000012", "1", "1"), ("tt90000014", "tt90000099", "2", "3"),
]
AKAS = [
    # titleId, ordering, title, region, language, types, attributes, isOriginalTitle
    ("tt90000003", "1", "Il codice della brace", "IT", N, N, N, "0"),
    ("tt90000003", "2", "Das Ember-Siegel", "DE", "de", "original", N, "1"),   # equals the original title
    ("tt90000003", "3", "The Ember Cipher", "US", N, N, N, "0"),            # equals the primary title
    ("tt90000003", "4", "Le chiffre de la braise", "FR", N, N, N, "0"),
    ("tt90000003", "5", "Chiffer pa glod", "SE", N, N, N, "0"),                  # region not chosen
    ("tt90000003", "6", "琥珀の暗号", "JP", "ja", N, N, "0"),                     # region not chosen
    ("tt90000001", "1", "Zarnak - Der Sandplanet", "DE", N, N, N, "0"),
    ("tt90000001", "2", "Zarnak", "US", N, N, N, "0"),                            # equals the title
    ("tt90000015", "1", "Die geheime Welt der Mirabelle", "DE", N, N, N, "0"),
    ("tt90000009", "1", "Obskur", "DE", N, N, N, "0"),                          # title not kept
    ("tt90000004", "1", "Quillfeather - Die Brutzeit", "DE", N, N, N, "0"),
]


def write_tsv(path, header, rows, newline="\n", bom=False) -> str:
    text = newline.join("\t".join(r) for r in [header, *rows]) + newline
    with gzip.open(path, "wb") as f:
        f.write((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))
    return str(path)


def make_dataset(folder, *, basics=None, ratings=None, episodes=None, akas=None) -> dict:
    """The four gzipped files in `folder`; returns {"basics": path, ...}."""
    folder = str(folder)
    os.makedirs(folder, exist_ok=True)
    return {
        "basics": write_tsv(os.path.join(folder, "title.basics.tsv.gz"), BASICS_HEADER, BASICS if basics is None else basics),
        "ratings": write_tsv(os.path.join(folder, "title.ratings.tsv.gz"), RATINGS_HEADER,
                             RATINGS if ratings is None else ratings),
        "episodes": write_tsv(os.path.join(folder, "title.episode.tsv.gz"), EPISODE_HEADER,
                              EPISODES if episodes is None else episodes),
        "akas": write_tsv(os.path.join(folder, "title.akas.tsv.gz"), AKAS_HEADER, AKAS if akas is None else akas),
    }


def build(tmp_path, options=None, **overrides):
    """A built database in tmp_path; returns (path, summary, files)."""
    files = make_dataset(tmp_path / "dump", **overrides)
    dest = str(tmp_path / "imdb.db")
    summary = imdb_import.build_imdb_database(
        files["basics"], dest, files["ratings"], files["episodes"], files["akas"], options
    )
    return dest, summary, files
