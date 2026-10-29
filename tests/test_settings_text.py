import pytest

from judgments_core.settings import Settings, read_dotenv
from judgments_core.text import clean_html


def test_env_wins_over_dotenv_and_defaults(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        '# komentarz\nNSA_DB_DSN="mysql://a:b@h/nsa"\nNSA_PAUSE_S=2.5\nETPC_PAUSE_S=9\n',
        encoding="utf-8",
    )
    s = Settings.from_env("nsa", environ={"NSA_PAUSE_S": "3"}, dotenv_path=dotenv)
    assert s.db_dsn == "mysql://a:b@h/nsa"
    assert s.pause_s == 3.0
    assert s.max_retries == Settings().max_retries


def test_bad_number_names_the_variable(tmp_path):
    with pytest.raises(ValueError, match="NSA_MAX_RETRIES"):
        Settings.from_env("nsa", environ={"NSA_MAX_RETRIES": "dużo"}, dotenv_path=tmp_path / "x")


def test_app_env_is_common_to_projects(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("APP_ENV=dev\n", encoding="utf-8")
    assert Settings.from_env("nsa", environ={}, dotenv_path=dotenv).dev
    assert (
        Settings.from_env("etpc", environ={"APP_ENV": "PROD"}, dotenv_path=dotenv).app_env == "prod"
    )
    assert not Settings.from_env("etpc", environ={}, dotenv_path=tmp_path / "brak").dev
    with pytest.raises(ValueError, match="APP_ENV"):
        Settings.from_env("nsa", environ={"APP_ENV": "test"}, dotenv_path=dotenv)


def test_missing_dotenv_is_fine(tmp_path):
    assert read_dotenv(tmp_path / "brak") == {}


def test_clean_html():
    assert (
        clean_html("<p>Sąd&nbsp; zważył,</p><p>  że wprowa-\ndzający</p>")
        == "Sąd zważył,\n\nże wprowadzający"
    )
    assert clean_html("") is None
