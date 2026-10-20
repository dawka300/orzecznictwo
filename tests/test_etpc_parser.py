import json
from datetime import date, datetime
from pathlib import Path

from etpc.parser import clean_body, is_wanted, parse_document, parse_ruling, split_list

FIXTURES = Path(__file__).parent / "fixtures" / "etpc"


def fixture_columns():
    data = json.loads((FIXTURES / "results_pol_judgments_2.json").read_text(encoding="utf-8"))
    return [r["columns"] for r in data["results"]]


def test_document_from_hudoc():
    d = parse_document(fixture_columns()[0])
    assert (d.itemid, d.group_key, d.language, d.doctype, d.is_translation) == (
        "001-252650",
        "ECLI:CE:ECHR:2026:1008JUD002271612",
        "ENG",
        "HEJUD",
        False,
    )
    assert d.title == "CASE OF ANDRZEJ RUCIŃSKI v. POLAND"
    assert d.body.startswith("FIRST SECTION\nCASE OF ANDRZEJ RUCIŃSKI v. POLAND\n")
    assert "\r" not in d.body
    assert d.source_updated_at == datetime(2026, 10, 8, 7, 51, 10)
    assert len(d.body_sha256) == 64


def test_ruling_from_hudoc():
    r = parse_ruling(fixture_columns()[0])
    assert (r.kind, r.formation, r.ruling_date, r.importance) == (
        "judgment",
        "CHAMBER",
        date(2026, 10, 8),
        4,
    )
    assert r.applications == ["22716/12"]
    assert r.respondents == ["POL"]
    assert r.articles == [("concerned", "41")]
    assert r.keywords == [216, 305, 577, 283]
    assert "22716/12" not in r.citations and "44277/98" in r.citations
    assert r.represented_by == "Barwina, Zbigniew"


def test_placeholders_and_other_collections_are_skipped():
    placeholder = fixture_columns()[1]
    assert placeholder["isplaceholder"] == "True"
    assert not is_wanted(placeholder)
    assert not is_wanted({"itemid": "x", "doctypetrunk": "COMMUNICATEDCASES"})
    assert is_wanted(fixture_columns()[0])


def test_ruling_lists_and_fallbacks():
    r = parse_ruling(
        {
            "itemid": "001-1",
            "ecli": "",
            "doctypetrunk": "DECISIONS",
            "doctypebranch": "ADMISSIBILITY",
            "kpdate": "",
            "decisiondate": "23/10/2003 00:00:00",
            "appno": "1/01;2/02",
            "respondent": "MDA;RUS",
            "violation": "14+P1-1;14;P1-1",
            "nonviolation": "6;6-1",
            "separateopinion": "TRUE",
            "representedby": "N/A",
            "judges": "A;B",
            "extractedappno": "2/02;3/03",
            "sclappnos": "3/03;5493/72",
        }
    )
    assert (r.group_key, r.ecli, r.kind, r.ruling_date) == (
        "itemid:001-1",
        None,
        "decision",
        date(2003, 10, 23),
    )
    assert r.respondents == ["MDA", "RUS"]
    assert r.articles == [
        ("violation", "14+P1-1"),
        ("violation", "14"),
        ("violation", "P1-1"),
        ("no_violation", "6"),
        ("no_violation", "6-1"),
    ]
    assert (r.separate_opinion, r.represented_by, r.judges) == (True, None, "A; B")
    assert r.citations == ["3/03", "5493/72"]


def test_helpers():
    assert split_list(" 6;6-1;;6; N/A") == ["6", "6-1"]
    assert clean_body("\r\n\r\nA  \r\n\r\n\r\n\r\nB\r\n") == "A\n\nB"
