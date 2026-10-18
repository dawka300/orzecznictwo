"""Shared command line: each project's __main__ only describes itself (Project).

python -m nsa migracje
python -m nsa pobierz --limit 10 --na-sucho
python -m nsa pobierz --budzet 240      # z crona na hostingu
python -m nsa stan
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path

from judgments_core import db
from judgments_core.http import PoliteClient, SourceError
from judgments_core.importer import Importer
from judgments_core.settings import Settings

log = logging.getLogger(__name__)


@dataclass
class Project:
    name: str  # "nsa": prefiks zmiennych NSA_*, nazwa blokady, plik logu
    title: str
    make_source: Callable  # (settings, client) -> Source
    migrations: Path  # katalog NNN_*.sql projektu (po migracjach rdzenia)


def build_parser(project):
    parser = argparse.ArgumentParser(prog=f"python -m {project.name}", description=project.title)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("migracje", help="utwórz/uzupełnij tabele w bazie")

    fetch = commands.add_parser("pobierz", help="pobierz rekordy ze źródła (wznawia od kursora)")
    fetch.add_argument("--limit", type=int, help="zakończ po N zapisanych rekordach")
    fetch.add_argument(
        "--na-sucho",
        dest="dry_run",
        action="store_true",
        help="tylko pobierz i wypisz, bez bazy (zawsze od początku)",
    )
    fetch.add_argument(
        "--od-poczatku",
        dest="from_start",
        action="store_true",
        help="zignoruj zapisany kursor (nowy kursor i tak się zapisze)",
    )
    fetch.add_argument(
        "--budzet",
        dest="time_budget_s",
        type=int,
        help="zakończ po tylu sekundach (domyślnie z ustawień, 0 = bez limitu)",
    )

    commands.add_parser("stan", help="pokaż zapisany stan i liczbę rekordów")
    return parser


def setup_logging(project, settings):
    handlers = [logging.StreamHandler(sys.stdout)]
    try:
        Path(settings.log_dir).mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                Path(settings.log_dir) / f"{project.name}.log",
                maxBytes=5_000_000,
                backupCount=5,
                encoding="utf-8",
            )
        )
    except OSError as e:
        print(f"Bez logu w pliku ({e})", file=sys.stderr)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
        force=True,
    )


def main(project, argv=None, settings=None):
    args = build_parser(project).parse_args(argv)
    settings = settings or Settings.from_env(project.name)
    setup_logging(project, settings)
    try:
        if args.command == "migracje":
            return _migrate(project, settings)
        if args.command == "pobierz":
            return _fetch(project, settings, args)
        return _status(project, settings)
    except db.LockedError as e:
        log.info("%s Kończę.", e)  # zwykła sytuacja przy cronie, nie błąd
        return 0
    except NotImplementedError as e:
        log.error("Jeszcze nie zrobione: %s", e)
        return 2
    except SourceError as e:
        log.error("%s", e)
        return 1


def _migrate(project, settings):
    with db.connect(settings.db_dsn) as conn:
        applied = db.migrate(conn, ("core", db.CORE_MIGRATIONS), (project.name, project.migrations))
    log.info("Zastosowano migracje: %s", ", ".join(applied) if applied else "brak nowych")
    return 0


def _fetch(project, settings, args):
    client = PoliteClient.from_settings(settings)
    source = project.make_source(settings, client)
    budget = settings.time_budget_s if args.time_budget_s is None else args.time_budget_s

    if args.dry_run:
        result = Importer(
            source, limit=args.limit, time_budget_s=budget, outputs=[_print_record]
        ).run()
        _report(result)
        return 0

    with db.connect(settings.db_dsn) as conn:
        store = db.MariaDbStore(conn)
        with store.lock(f"{project.name}:pobierz"):
            result = Importer(
                source,
                store,
                limit=args.limit,
                time_budget_s=budget,
                resume=not args.from_start,
            ).run()
    _report(result)
    return 0


def _print_record(record):
    print(f"  {record.source_id}  {len(record.body):>8} zn.  {record.url or ''}")


def _report(result):
    end = "źródło nie ma więcej stron" if result.finished else f"przerwano ({result.stopped_by})"
    log.info(
        "Gotowe: %d stron, zapisano %d, odrzucono %d; %s.",
        result.pages,
        result.saved,
        result.rejected,
        end,
    )


def _status(project, settings):
    with db.connect(settings.db_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT `key`, value, updated_at FROM sync_state ORDER BY `key`")
        for key, value, updated in cur.fetchall():
            print(f"{key} = {value}  ({updated:%Y-%m-%d %H:%M})")
        cur.execute("SELECT source, COUNT(*), MAX(fetched_at) FROM raw_records GROUP BY source")
        for source, count, last in cur.fetchall():
            print(f"raw_records[{source}]: {count} rekordów, ostatni pobrany {last}")
    return 0
