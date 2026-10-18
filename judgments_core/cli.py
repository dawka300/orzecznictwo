"""Shared command line: each project's __main__ only describes itself (Project).

python -m nsa migracje
python -m nsa pobierz --limit 10 --na-sucho
python -m nsa pobierz --budzet 240      # z crona na hostingu
python -m nsa przetworz                 # raw_records -> tabele projektu
python -m nsa sprawdz                   # porównaj źródło z bazą, zapisz braki
python -m nsa pobierz --brakujace       # dociągnij braki znalezione przez sprawdz
python -m nsa stan                      # kursory, ostatnie przebiegi, blokady, problemy
python -m nsa kopia                     # zrzut bazy do <NSA_BACKUP_DIR>/nsa.sql.gz

Każdy przebieg pobierz/przetworz/sprawdz zapisuje się w fetch_runs, blokady
i błędy źródła w fetch_events, rekordy z problemem w record_problems.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import shlex
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path

from judgments_core import db
from judgments_core.backup import dump_database
from judgments_core.browser import PlaywrightSession
from judgments_core.deriver import Deriver
from judgments_core.http import CurlSession, PoliteClient
from judgments_core.importer import Importer
from judgments_core.settings import DOTENV_PATH, Settings

log = logging.getLogger(__name__)


@dataclass
class Project:
    name: str  # "nsa": prefiks zmiennych NSA_*, nazwa blokady, plik logu
    title: str
    make_source: Callable  # (settings, client) -> Source
    migrations: Path  # katalog NNN_*.sql projektu (po migracjach rdzenia)
    # requests / curl (Cloudflare blokuje TLS Pythona) / playwright (prawdziwa
    # przeglądarka, tylko lokalnie); ustawienie <PREFIKS>_TRANSPORT ma pierwszeństwo
    transport: str = "requests"
    # derive(cursor, records) -> problemy: tabele projektu z raw_records; None = brak parsera
    derive: Callable | None = None
    # verify(store, source, years) -> wyniki: porównanie źródła z bazą; None = brak
    verify: Callable | None = None


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
    fetch.add_argument(
        "--brakujace",
        dest="missing",
        action="store_true",
        help="pobierz tylko rekordy oznaczone przez 'sprawdz' jako brakujące",
    )

    derive = commands.add_parser(
        "przetworz", help="wypełnij tabele projektu z raw_records (tylko zmienione)"
    )
    derive.add_argument("--limit", type=int, help="zakończ po N rekordach")
    derive.add_argument(
        "--od-poczatku",
        dest="from_start",
        action="store_true",
        help="przetwórz wszystkie rekordy od nowa (np. po zmianie parsera)",
    )
    derive.add_argument(
        "--budzet",
        dest="time_budget_s",
        type=int,
        help="zakończ po tylu sekundach (domyślnie z ustawień, 0 = bez limitu)",
    )

    check = commands.add_parser(
        "sprawdz", help="porównaj źródło z bazą (rok po roku), braki zapisz jako problemy"
    )
    check.add_argument("--od", dest="year_from", type=int, help="od roku")
    check.add_argument("--do", dest="year_to", type=int, help="do roku (włącznie)")

    status = commands.add_parser(
        "stan", help="kursory, liczba rekordów, ostatnie przebiegi, blokady, problemy"
    )
    status.add_argument(
        "--problemy", dest="list_problems", action="store_true", help="wypisz otwarte problemy"
    )
    commands.add_parser(
        "kopia", help="zrzut bazy do <PREFIKS>_BACKUP_DIR (też sam po pobierz/przetworz)"
    )
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
        if args.command == "przetworz":
            return _derive(project, settings, args)
        if args.command == "sprawdz":
            return _verify(project, settings, args)
        if args.command == "kopia":
            return _backup(project, settings, required=True)
        return _status(project, settings, args)
    except db.LockedError as e:
        log.info("%s Kończę.", e)  # zwykła sytuacja przy cronie, nie błąd
        return 0
    except NotImplementedError as e:
        log.error("Jeszcze nie zrobione: %s", e)
        return 2
    except RuntimeError as e:  # SourceError, brak Playwrighta ...
        log.error("%s", e)
        return 1


def _migrate(project, settings):
    with db.connect(settings.db_dsn) as conn:
        applied = db.migrate(conn, ("core", db.CORE_MIGRATIONS), (project.name, project.migrations))
    log.info("Zastosowano migracje: %s", ", ".join(applied) if applied else "brak nowych")
    return 0


def _make_source(project, settings, closer, on_event=None):
    """The project's source over the chosen transport; closer closes the session at the end."""
    transport = settings.transport or project.transport
    if transport == "curl":
        session = CurlSession()
    elif transport == "playwright":
        session = PlaywrightSession()
    elif transport == "requests":
        session = None
    else:
        raise ValueError(f"nieznany transport: {transport} (requests / curl / playwright)")
    if session is not None:
        closer.callback(session.close)
    log.info("transport: %s", transport)
    client = PoliteClient.from_settings(settings, session=session, on_event=on_event)
    return project.make_source(settings, client)


def _lock_name(project):
    # Jedna blokada dla pobierania i przetwarzania: przetwarzanie w trakcie
    # pobierania mogłoby minąć rekord zapisany z wcześniejszym fetched_at.
    return f"{project.name}:baza"


@contextlib.contextmanager
def _logged_run(project, settings, command, cursor_start=None):
    """Connection, lock and a fetch_runs row; the row is closed also on errors.

    Yields (store, run); the body sets run.status and run.counts.
    """
    with db.connect(settings.db_dsn) as conn:
        store = db.MariaDbStore(conn)
        with store.lock(_lock_name(project)):
            run = RunLog(store.start_run(command, cursor_start))
            try:
                yield store, run
            except BaseException as e:
                # Ctrl+C też zostawia ślad w dzienniku.
                run.status = "blad" if isinstance(e, Exception) else "przerwano"
                run.counts["error"] = f"{type(e).__name__}: {e}"
                raise
            finally:
                store.finish_run(run.id, run.status, **run.counts)


@dataclass
class RunLog:
    id: int
    status: str = "ok"
    counts: dict = field(default_factory=dict)

    def event_saver(self, store):
        def save(kind, message, url, wait_s):
            store.add_event(self.id, kind, message, url, wait_s)

        return save


def _fetch(project, settings, args):
    budget = settings.time_budget_s if args.time_budget_s is None else args.time_budget_s

    if args.dry_run:
        with contextlib.ExitStack() as closer:
            source = _make_source(project, settings, closer)
            result = Importer(
                source, limit=args.limit, time_budget_s=budget, outputs=[_print_record]
            ).run()
        _report(result)
        return 0

    with (
        _logged_run(project, settings, "pobierz") as (store, run),
        contextlib.ExitStack() as closer,
    ):
        source = _make_source(project, settings, closer, run.event_saver(store))
        if args.missing:
            ids = store.open_problems(source.name, "pobierz", "brak_w_bazie", limit=args.limit)
            log.info("brakujących do pobrania: %d", len(ids))
            source = _IdsSource(source, ids)
        importer = Importer(
            source,
            store,
            limit=args.limit,
            time_budget_s=budget,
            resume=not args.from_start and not args.missing,
        )
        try:
            result = importer.run()
        finally:
            r = importer.result
            run.counts.update(
                pages=r.pages,
                records=r.saved,
                records_new=r.new,
                records_changed=r.changed,
                cursor_end=r.cursor,
            )
        run.status = _status_of(result)
    _report(result)
    if r.new or r.changed:
        return _backup(project, settings)
    return 0


class _IdsSource:
    """Pages of chosen records (source.fetch_ids), without a cursor."""

    def __init__(self, source, ids):
        if not hasattr(source, "fetch_ids"):
            raise NotImplementedError(f"źródło {source.name} nie umie pobierać po identyfikatorach")
        self.name = source.name
        self.source = source
        self.ids = ids

    def iter_pages(self, cursor):
        return self.source.fetch_ids(self.ids)


def _derive(project, settings, args):
    if project.derive is None:
        raise NotImplementedError(f"projekt {project.name} nie ma jeszcze parsera")
    with contextlib.ExitStack() as closer:
        source = _make_source(project, settings, closer)  # tylko nazwa źródła
    budget = settings.time_budget_s if args.time_budget_s is None else args.time_budget_s
    with _logged_run(project, settings, "przetworz") as (store, run):
        deriver = Deriver(
            store,
            source.name,
            project.derive,
            from_start=args.from_start,
            limit=args.limit,
            time_budget_s=budget,
        )
        try:
            result = deriver.run()
        finally:
            r = deriver.result
            run.counts.update(pages=r.batches, records=r.records, problems=r.problems)
        run.status = _status_of(result)
    end = "wszystko przetworzone" if result.finished else f"przerwano ({result.stopped_by})"
    log.info(
        "Gotowe: %d porcji, %d rekordów, problemów %d; %s.",
        result.batches,
        result.records,
        result.problems,
        end,
    )
    if result.records:
        return _backup(project, settings)
    return 0


def _verify(project, settings, args):
    if project.verify is None:
        raise NotImplementedError(f"projekt {project.name} nie ma jeszcze sprawdzania")
    with (
        _logged_run(project, settings, "sprawdz") as (store, run),
        contextlib.ExitStack() as closer,
    ):
        source = _make_source(project, settings, closer, run.event_saver(store))
        years = None
        if args.year_from or args.year_to:
            years = range(args.year_from or 1900, (args.year_to or source.now().year) + 1)
        checks = project.verify(store, source, years)
        missing = sum(c.missing for c in checks)
        extra = sum(c.extra for c in checks)
        run.counts.update(pages=len(checks), records=missing + extra, problems=missing + extra)
    differ = [c for c in checks if c.hudoc != c.ours]
    log.info(
        "Gotowe: %d lat, zgodnych %d; brakuje %d, nadmiarowych %d.%s",
        len(checks),
        len(checks) - len(differ),
        missing,
        extra,
        " Braki pobierze: pobierz --brakujace" if missing else "",
    )
    return 0


def _backup(project, settings, required=False):
    """Dump the database into the backup directory, if one is set."""
    if not settings.backup_dir:
        if required:
            log.error("Brak katalogu kopii: ustaw %s_BACKUP_DIR", project.name.upper())
            return 2
        return 0
    path = DOTENV_PATH.parent / settings.backup_dir / f"{project.name}.sql.gz"
    # Pod blokadą: kopia nie wypada w połowie pobierania z innego procesu.
    with db.connect(settings.db_dsn) as conn, db.MariaDbStore(conn).lock(_lock_name(project)):
        dump_database(settings.db_dsn, path, shlex.split(settings.dump_command))
    return 0


def _status_of(result):
    return "ok" if result.finished else (result.stopped_by or "ok")


def _print_record(record):
    print(f"  {record.source_id}  {len(record.body):>8} zn.  {record.url or ''}")


def _report(result):
    end = "źródło nie ma więcej stron" if result.finished else f"przerwano ({result.stopped_by})"
    log.info(
        "Gotowe: %d stron, zapisano %d (nowych %d, zmienionych %d), odrzucono %d; %s.",
        result.pages,
        result.saved,
        result.new,
        result.changed,
        result.rejected,
        end,
    )


def _status(project, settings, args):
    with db.connect(settings.db_dsn) as conn, conn.cursor() as cur:
        print("Kursory:")
        cur.execute("SELECT `key`, value, updated_at FROM sync_state ORDER BY `key`")
        for key, value, updated in cur.fetchall():
            print(f"  {key} = {value}  ({updated:%Y-%m-%d %H:%M})")
        cur.execute("SELECT source, COUNT(*), MAX(fetched_at) FROM raw_records GROUP BY source")
        for source, count, last in cur.fetchall():
            print(f"raw_records[{source}]: {count} rekordów, ostatni pobrany {last}")

        print("\nOstatnie przebiegi (czas UTC):")
        cur.execute(
            "SELECT id, command, started_at, finished_at, status, pages, records,"
            " records_new, records_changed, problems, events, error"
            " FROM fetch_runs ORDER BY id DESC LIMIT 10"
        )
        for row in cur.fetchall():
            run_id, command, started, finished, status, pages, records = row[:7]
            new, changed, problems, events, error = row[7:]
            took = f"{(finished - started).total_seconds():.0f}s" if finished else "trwa/zabity"
            print(
                f"  #{run_id} {command:9} {started:%Y-%m-%d %H:%M} {took:>8} {status:7}"
                f" stron {pages}, rekordów {records} (nowych {new}, zmienionych {changed}),"
                f" problemów {problems}, zdarzeń {events}"
                + (f"\n      błąd: {error}" if error else "")
            )

        print("\nBlokady i błędy źródła, ostatnie 7 dni:")
        cur.execute(
            "SELECT kind, COUNT(*), MIN(at), MAX(at) FROM fetch_events"
            " WHERE at >= UTC_TIMESTAMP() - INTERVAL 7 DAY GROUP BY kind ORDER BY 2 DESC"
        )
        events = cur.fetchall()
        for kind, count, first, last in events:
            print(f"  {kind:12} {count:>5}x  od {first:%Y-%m-%d %H:%M} do {last:%Y-%m-%d %H:%M}")
        if not events:
            print("  brak")

        print("\nOtwarte problemy rekordów:")
        cur.execute(
            "SELECT stage, problem, COUNT(*) FROM record_problems WHERE resolved_at IS NULL"
            " GROUP BY stage, problem ORDER BY stage, problem"
        )
        problems = cur.fetchall()
        for stage, problem, count in problems:
            print(f"  {stage:10} {problem:16} {count:>6}")
        if not problems:
            print("  brak")
        if args.list_problems and problems:
            cur.execute(
                "SELECT source_id, stage, problem, details, times, last_seen FROM record_problems"
                " WHERE resolved_at IS NULL ORDER BY stage, problem, source_id LIMIT 500"
            )
            for source_id, stage, problem, details, times, last in cur.fetchall():
                print(f"  {source_id} {stage}/{problem} x{times} ({last:%Y-%m-%d}): {details}")
    return 0
