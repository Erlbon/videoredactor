"""The name-parser corpus (tests/data/scene_names.tsv): every row is one parametrized case through guess_search, the
same call the ThePornDB / StashDB dialog makes. Columns: folder, file name, expected title, studio, date or year,
performers (';'), scene number. An empty cell means "nothing found". Plus direct checks of the parser's pieces."""

import csv
from pathlib import Path

import pytest

from core.scene_lookup import guess_search
from core.scene_name_parser import camel_parts, parse_scene_name

CORPUS = Path(__file__).parent / "data" / "scene_names.tsv"


def _rows():
    with open(CORPUS, encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle, delimiter="\t", quoting=csv.QUOTE_NONE))
    header, body = rows[0], rows[1:]
    assert header == ["folder", "file", "title", "studio", "date_or_year", "performers", "scene"]
    return [[cell.strip() for cell in (row + [""] * 7)[:7]] for row in body if any(c.strip() for c in row)]


ROWS = _rows()


def test_the_corpus_has_at_least_fifty_rows():
    assert len(ROWS) >= 50


@pytest.mark.parametrize("folder,file,title,studio,when,performers,scene", ROWS, ids=[f"{r[0][:18]}|{r[1][:40]}" for r in ROWS])
def test_corpus_row(folder, file, title, studio, when, performers, scene):
    path = Path("D:/lib") / folder / file if folder else Path("D:/lib") / file
    g = guess_search(path)
    assert g.text == title
    assert g.studio == studio
    assert (g.date if len(when) == 10 else g.year if when else (g.date or g.year)) == when
    assert g.performers == [p for p in performers.split(";") if p]
    assert g.scene_number == (int(scene) if scene else None)


def test_the_cast_tag_wins_and_the_name_adds_what_it_lacks():
    from core.video_metadata import VideoMetadata

    meta = VideoMetadata(cast="Charlotte Sartre")
    g = guess_search("D:/x/AnalOilLatex_s01_CharlotteSartre_RamonNomar_480p.mp4", meta)
    assert g.performers == ["Charlotte Sartre", "Ramon Nomar"]


@pytest.mark.parametrize("token,parts", [
    ("AnalOilLatex", ["Anal", "Oil", "Latex"]), ("McDonald", ["McDonald"]), ("McKenzieAdventures", ["McKenzie", "Adventures"]),
    ("TheDPAffair", ["The", "DP", "Affair"]), ("StrapSomeBoyz2", ["Strap", "Some", "Boyz", "2"]), ("XXX", ["XXX"]),
    ("anal", ["anal"]), ("iPhone", ["iPhone"]), ("Boyz", ["Boyz"]),
])
def test_camel_case_split_keeps_acronyms_and_mc_names(token, parts):
    assert camel_parts(token) == parts


def test_episode_markers_count_only_when_asked():
    assert parse_scene_name("Hot Summer Nights E03").scene is None
    assert parse_scene_name("Hot Summer Nights E03", episodes=True).scene == 3


def test_a_plausible_year_is_kept_out_of_the_title_but_not_a_title_number():
    assert parse_scene_name("Class Of 1985").year == ""
    assert parse_scene_name("Big Wet Butts 1999").year == "1999"
    assert parse_scene_name("2 Girls 1 Cup").title == "2 Girls 1 Cup"
