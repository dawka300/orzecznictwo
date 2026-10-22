"""narzedzia/cbosa_id.py without the network: periods, file names, paging, transport switch."""

import importlib.util
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "cbosa_id", Path(__file__).parent.parent / "narzedzia" / "cbosa_id.py"
)
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)


def args(**kwargs):
    names = ("court", "kind", "words", "docket", "symbols", "judge", "keywords", "reasons")
    return SimpleNamespace(**{**dict.fromkeys(names), "where": "gdziekolwiek", **kwargs})


def test_periods_and_file_names():
    months = list(tool.periods(date(2025, 3, 5), date(2025, 5, 31), "miesiac"))
    assert months == [
        (date(2025, 3, 5), date(2025, 3, 31)),
        (date(2025, 4, 1), date(2025, 4, 30)),
        (date(2025, 5, 1), date(2025, 5, 31)),
    ]
    assert len(list(tool.periods(date(2025, 1, 1), date(2025, 1, 10), "tydzien"))) == 2
    assert tool.file_name(*months[0], args()) == "2025-03-05_2025-03-31"
    assert tool.file_name(*months[1], args(court="NSA", kind="wyrok")) == "2025-04_nsa_wyrok"
    assert tool.file_name(*months[1], args(court="WSA w Łodzi", reasons="tak")) == (
        "2025-04_wsa-w-lodzi_uzasadnienie-tak"
    )


def test_search_form():
    form = tool.search_form(
        args(court="WSA w Krakowie", kind="uchwala", reasons="nie"),
        date(2025, 3, 1),
        date(2025, 3, 31),
    )
    assert form["sad"] == "Wojewódzki Sąd Administracyjny w Krakowie"
    assert form["rodzaj"] == "Uchwała" and form["nieUzasadnienie"] == "on"
    assert (form["odDaty"], form["doDaty"]) == ("2025-03-01", "2025-03-31")
    assert tool.court_name("NSA") == "Naczelny Sąd Administracyjny"


def links(*ids):
    return "".join(f'<a href="/doc/{i}">x</a>' for i in ids) + " " * tool.MIN_PAGE_CHARS


class FakeHttp:
    """Pages of a search: POST = page 1, GET find?p=N = page N; scripted failures."""

    name = "fake"

    def __init__(self, pages, failures=0):
        self.pages = pages
        self.failures = failures
        self.calls = []

    def fetch(self, url, data=None):
        self.calls.append("POST" if data is not None else url.rsplit("=", 1)[-1])
        if self.failures:
            self.failures -= 1
            raise OSError("zerwane połączenie")
        page = 1 if data is not None else int(url.rsplit("=", 1)[-1])
        return 200, links(*self.pages[page - 1]) if page <= len(self.pages) else links()

    def close(self):
        pass


def chain_of(*https, monkeypatch):
    monkeypatch.setattr(tool.time, "sleep", lambda s: None)
    queue = list(https)
    monkeypatch.setattr(
        tool, "TRANSPORTS", {f"t{i}": (lambda ua, h=h: h) for i, h in enumerate(queue)}
    )
    return tool.Chain([f"t{i}" for i in range(len(queue))], "test", 0)


def test_collect_all_pages(monkeypatch):
    http = FakeHttp([["AAAAAAAAA1", "AAAAAAAAA2"], ["AAAAAAAAA3"]])
    written = []
    count = tool.collect(chain_of(http, monkeypatch=monkeypatch), {}, 0, written.extend)
    assert count == 3 and written == ["AAAAAAAAA1", "AAAAAAAAA2", "AAAAAAAAA3"]
    # pusta strona 3: jeszcze raz wyszukanie (może wygasła sesja), potem koniec
    assert http.calls == ["POST", "2", "3", "POST", "3"]


def test_switches_transport_and_searches_again(monkeypatch):
    broken = FakeHttp([["AAAAAAAAA1"]], failures=99)
    working = FakeHttp([["AAAAAAAAA1"], ["AAAAAAAAA2"]])
    written = []
    chain = chain_of(broken, working, monkeypatch=monkeypatch)
    assert tool.collect(chain, {}, 0, written.extend) == 2
    assert broken.calls == ["POST"] * tool.SWITCH_AFTER
    assert working.calls[:2] == ["POST", "2"]


def test_last_transport_failing_ends(monkeypatch):
    chain = chain_of(FakeHttp([], failures=99), monkeypatch=monkeypatch)
    with pytest.raises(SystemExit):
        tool.collect(chain, {}, 0, lambda ids: None)


def test_dry_run_lists_periods_and_skips_done(tmp_path, capsys):
    (tmp_path / "2025").mkdir()
    (tmp_path / "2025" / "2025-04.txt").write_text("", encoding="utf-8")
    tool.main(
        ["--od", "2025-03-05", "--do", "2025-05-31", "--katalog", str(tmp_path), "--na-sucho"]
    )
    out = capsys.readouterr().out
    assert "2025-03-05_2025-03-31.txt" in out and "2025-05.txt" in out and "2025-04.txt" not in out


def test_results_page_without_related():
    body = (Path(__file__).parent / "fixtures" / "nsa" / "cbosa" / "wyniki.html").read_text(
        encoding="utf-8"
    )
    ids = tool.result_ids(body)
    assert len(ids) == 10 and ids[0] == "5F4CD1D316"
    assert "6B39365D39" not in ids  # powiązane pod wynikiem
    assert tool.result_count(body) == (8350, 835)
