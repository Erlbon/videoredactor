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
    ("tt0087182", "movie", "Dune", "Dune", "0", "1984", N, "137", "Adventure,Drama,Sci-Fi"),
    ("tt1160419", "movie", "Dune", "Dune", "0", "2021", N, "155", "Action,Adventure,Drama"),
    ("tt0091605", "movie", "The Name of the Rose", "Der Name der Rose", "0", "1986", N, "130", "Crime,Drama,Mystery"),
    ("tt0903747", "tvSeries", "Breaking Bad", "Breaking Bad", "0", "2008", "2013", "49", "Crime,Drama,Thriller"),
    ("tt0959621", "tvEpisode", "Pilot", "Pilot", "0", "2008", N, "58", "Crime,Drama,Thriller"),
    ("tt1054724", "tvEpisode", "Cat's in the Bag...", "Cat's in the Bag...", "0", "2008", N, "48", "Crime,Drama"),
    ("tt0000002", "short", "Le clown et ses chiens", "Le clown et ses chiens", "0", "1892", N, "5", "Animation,Short"),
    ("tt9999990", "movie", "Adult Feature", "Adult Feature", "1", "2000", N, "80", "Adult"),
    ("tt8888881", "movie", "Obscure Film", "Obscure Film", "0", "1999", N, "90", "Drama"),
    ("tt7777771", "videoGame", "Some Game", "Some Game", "0", "2010", N, N, "Action"),
    ("tt3333331", "tvSpecial", "A Christmas Special", "A Christmas Special", "0", "1999", N, "45", "Comedy"),
    ("tt6666661", "tvSeries", "Unknown Show", "Unknown Show", "0", "2015", N, "30", "Drama"),
    ("tt6666662", "tvEpisode", "Lost Episode", "Lost Episode", "0", "2015", N, "30", "Drama"),
    ("tt5555551", "tvEpisode", "Orphan Episode", "Orphan Episode", "0", "2015", N, "30", "Drama"),
    ("tt0118115", "movie", "Amelie", "Le fabuleux destin d'Amelie Poulain", "0", "2001", N, "122", "Comedy,Romance"),
    ("tt0172495", "movie", "Law & Order: The Movie", "Law & Order: The Movie", "0", "2000", N, "100", "Crime"),
]
RATINGS = [
    ("tt0087182", "6.3", "150000"), ("tt1160419", "8.0", "700000"), ("tt0091605", "7.7", "120000"),
    ("tt0903747", "9.5", "2000000"), ("tt0959621", "9.0", "30000"), ("tt1054724", "8.6", "28000"),
    ("tt9999990", "5.0", "900"), ("tt8888881", "6.0", "2"), ("tt3333331", "7.0", "50"),
    ("tt6666661", "7.0", "1"), ("tt0118115", "8.3", "800000"), ("tt0172495", "6.0", "1500"),
    ("tt0000002", "5.6", "2000"),
]
EPISODES = [
    ("tt0959621", "tt0903747", "1", "1"), ("tt1054724", "tt0903747", "1", "2"),
    ("tt6666662", "tt6666661", "1", "1"), ("tt5555551", "tt4444441", "2", "3"),
]
AKAS = [
    # titleId, ordering, title, region, language, types, attributes, isOriginalTitle
    ("tt0091605", "1", "Il nome della rosa", "IT", N, N, N, "0"),
    ("tt0091605", "2", "Der Name der Rose", "DE", "de", "original", N, "1"),   # equals the original title
    ("tt0091605", "3", "The Name of the Rose", "US", N, N, N, "0"),            # equals the primary title
    ("tt0091605", "4", "Le nom de la rose", "FR", N, N, N, "0"),
    ("tt0091605", "5", "Namnet pa rosen", "SE", N, N, N, "0"),                  # region not chosen
    ("tt0091605", "6", "薔薇の名前", "JP", "ja", N, N, "0"),                     # region not chosen
    ("tt0087182", "1", "Dune - Der Wuestenplanet", "DE", N, N, N, "0"),
    ("tt0087182", "2", "Dune", "US", N, N, N, "0"),                            # equals the title
    ("tt0118115", "1", "Die fabelhafte Welt der Amelie", "DE", N, N, N, "0"),
    ("tt8888881", "1", "Obskur", "DE", N, N, N, "0"),                          # title not kept
    ("tt0903747", "1", "Breaking Bad - Reine Chemie", "DE", N, N, N, "0"),
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
