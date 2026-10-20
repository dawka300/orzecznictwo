from dataclasses import replace

from fakes import FakeSource

from judgments_core.cli import Project, main
from judgments_core.http import SourceError
from judgments_core.settings import Settings


def settings(tmp_path):
    return Settings(log_dir=str(tmp_path / "logs"), pause_s=0)


def test_dry_run_prints_records_without_database(tmp_path, capsys):
    project = Project(
        name="fake",
        title="test",
        source=lambda s, c: FakeSource([["a", "b"], ["c"]]),
        migrations=tmp_path,
    )
    assert main(project, ["pobierz", "--na-sucho", "--limit", "2"], settings(tmp_path)) == 0
    out = capsys.readouterr().out
    assert "  a " in out and "  b " in out and "  c " not in out
    assert (tmp_path / "logs" / "fake.log").exists()


class UnwrittenSource:
    name = "unwritten"

    def __init__(self, settings, client):
        pass

    def iter_pages(self, cursor):
        raise NotImplementedError("źródło jeszcze nie napisane")


def unwritten_project(tmp_path):
    return Project(name="unwritten", title="test", source=UnwrittenSource, migrations=tmp_path)


def test_unwritten_source_exits_with_2(tmp_path):
    assert main(unwritten_project(tmp_path), ["pobierz", "--na-sucho"], settings(tmp_path)) == 2


def test_project_without_parser_exits_with_2(tmp_path):
    assert main(unwritten_project(tmp_path), ["przetworz"], settings(tmp_path)) == 2


class FailingSource:
    """First page fine, then the source gives up (as PoliteClient does)."""

    name = "fake"

    def iter_pages(self, cursor):
        yield from FakeSource([["a"]]).iter_pages(cursor)
        raise SourceError("Źródło nie odpowiada (Cloudflare challenge)")


def test_passes_are_logged_in_the_database(conn, tmp_path):
    from conftest import TEST_DSN

    s = replace(settings(tmp_path), db_dsn=TEST_DSN)
    sources = iter([FakeSource([["a", "b"], ["c"]]), FailingSource()])
    project = Project(
        name="fake",
        title="test",
        source=lambda settings, client: next(sources),
        migrations=tmp_path,
    )
    assert main(project, ["migracje"], s) == 0
    assert main(project, ["pobierz"], s) == 0
    assert main(project, ["pobierz", "--od-poczatku"], s) == 1
    with conn.cursor() as cur:
        cur.execute(
            "SELECT command, status, pages, records, records_new, cursor_end, error"
            " FROM fetch_runs ORDER BY id"
        )
        assert cur.fetchall() == (
            ("pobierz", "ok", 2, 3, 3, "2", None),
            (
                "pobierz",
                "blad",
                1,
                1,
                0,
                "1",
                "SourceError: Źródło nie odpowiada (Cloudflare challenge)",
            ),
        )
    conn.commit()
