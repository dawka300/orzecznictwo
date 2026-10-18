from fakes import FakeSource

from judgments_core.cli import Project, main
from judgments_core.settings import Settings
from nsa.__main__ import PROJECT as NSA


def settings(tmp_path):
    return Settings(log_dir=str(tmp_path / "logs"), pause_s=0)


def test_dry_run_prints_records_without_database(tmp_path, capsys):
    project = Project(
        name="fake",
        title="test",
        make_source=lambda s, c: FakeSource([["a", "b"], ["c"]]),
        migrations=tmp_path,
    )
    assert main(project, ["pobierz", "--na-sucho", "--limit", "2"], settings(tmp_path)) == 0
    out = capsys.readouterr().out
    assert "  a " in out and "  b " in out and "  c " not in out
    assert (tmp_path / "logs" / "fake.log").exists()


def test_unwritten_source_exits_with_2(tmp_path):
    assert main(NSA, ["pobierz", "--na-sucho"], settings(tmp_path)) == 2


def test_project_without_parser_exits_with_2(tmp_path):
    assert main(NSA, ["przetworz"], settings(tmp_path)) == 2
