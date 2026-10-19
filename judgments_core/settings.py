"""Settings from environment variables or a .env file, per project prefix.

No third-party dependency on purpose: shared hosting may have an old Python and
no compiler, so the core needs only requests and PyMySQL.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields, replace
from pathlib import Path

# .env w katalogu repozytorium, nie w bieżącym - cron uruchamia z katalogu domowego.
DOTENV_PATH = Path(__file__).resolve().parent.parent / ".env"
LOCAL_DSN = "mysql://szkielet:szkielet@127.0.0.1:3306/{name}"


def read_dotenv(path):
    """Parse KEY=VALUE lines; '#' starts a comment line, quotes are stripped."""
    values = {}
    path = Path(path)
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


@dataclass(frozen=True)
class Settings:
    """One project's settings; env var names are PREFIX_FIELD, e.g. NSA_DB_DSN."""

    # Puste = lokalna MariaDB z docker-compose, baza o nazwie projektu.
    db_dsn: str = ""
    user_agent: str = "orzecznictwo-szkielet/0.1 (projekt prywatny)"
    pause_s: float = 1.0  # uprzejma przerwa między żądaniami do źródła
    max_retries: int = 6
    timeout_s: float = 60.0
    # Ile sekund może trwać jeden przebieg pobierania (0 = bez limitu). Na
    # hostingu cron uruchamia przebieg co kilka minut, a hosting zabija zbyt
    # długie procesy - przebieg kończy się sam, a następny wznawia od kursora.
    time_budget_s: int = 0
    log_dir: str = "logs"
    # requests / curl / playwright; puste = domyślny transport projektu
    transport: str = ""
    # Katalog kopii bazy (względem katalogu repozytorium); puste = bez kopii.
    # Ustawiony: po pobierz/przetworz, które coś zmieniły, kopia jest odświeżana.
    backup_dir: str = ""
    # Program zrzutu z argumentami; lokalnie z dockera, np.
    # docker exec -i -e MYSQL_PWD orzecznictwo-szkielet-db-1 mariadb-dump
    dump_command: str = "mysqldump"
    # Wektory (polecenie wektory, tylko lokalnie): serwer embeddingów zgodny
    # z OpenAI (LM Studio) i Qdrant; kolekcja pusta = <projekt>_<model>.
    embed_url: str = "http://127.0.0.1:1234/v1/embeddings"
    embed_model: str = "text-embedding-bge-m3"
    embed_batch: int = 32
    embed_timeout_s: float = 600.0
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_collection: str = ""

    @classmethod
    def from_env(cls, prefix, environ=None, dotenv_path=DOTENV_PATH):
        """Environment wins over .env, which wins over the defaults."""
        source = {
            **read_dotenv(dotenv_path),
            **(os.environ if environ is None else environ),
        }
        settings = cls()
        changes = {}
        for f in fields(cls):
            key = f"{prefix.upper()}_{f.name.upper()}"
            if key in source:
                default = getattr(settings, f.name)
                try:
                    changes[f.name] = type(default)(source[key])
                except ValueError as e:
                    raise ValueError(f"Zła wartość {key}={source[key]!r}: {e}") from e
        if not changes.get("db_dsn"):
            changes["db_dsn"] = LOCAL_DSN.format(name=prefix.lower())
        return replace(settings, **changes)
