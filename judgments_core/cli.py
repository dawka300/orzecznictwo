"""Shared command line: each project's __main__ only describes itself (Project).

python -m nsa migracje
python -m nsa pobierz --limit 10 --na-sucho
python -m nsa pobierz --budzet 240      # z crona na hostingu
python -m nsa przetworz                 # raw_records -> tabele projektu
python -m nsa sprawdz                   # porównaj źródło z bazą, zapisz braki
python -m nsa pobierz --brakujace       # dociągnij braki znalezione przez sprawdz
python -m nsa stan                      # kursory, ostatnie przebiegi, blokady, problemy
python -m nsa kopia                     # zrzut bazy do <NSA_BACKUP_DIR>/nsa.sql.gz
python -m etpc www --port 8080          # wyszukiwarka w przeglądarce
python -m etpc wektory --budzet 3600    # embeddingi (LM Studio) do Qdranta, wznawia
python -m etpc mcp                      # baza jako narzędzia dla Claude'a (stdio, .mcp.json)
python -m etpc fragmenty "pytanie"      # kontekst RAG: najlepsze fragmenty do pytania
python -m etpc ocen                     # trafność fragmentów na pytaniach z etpc/pytania.json
python -m etpc zapytaj "pytanie"        # odpowiedź modelu lokalnego (LM Studio) ze źródłami
python -m etpc ocen-odpowiedzi          # odpowiedzi na pytania wzorcowe + automatyczna ocena
python -m nsa paczki --ile 2            # paczki fragmentów do policzenia na innym komputerze
python -m nsa paczka-import nsa-0001    # wektory policzone gdzie indziej -> Qdrant

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
from judgments_core.embeddings import make_embedder
from judgments_core.http import PoliteClient
from judgments_core.importer import Importer
from judgments_core.qdrant import QdrantStore, collection_name
from judgments_core.rag import render
from judgments_core.settings import DOTENV_PATH, Settings
from judgments_core.transport import make_transport
from judgments_core.web import serve

log = logging.getLogger(__name__)


@dataclass
class Project:
    name: str  # "nsa": prefiks zmiennych NSA_*, nazwa blokady, plik logu
    title: str
    # (settings, client) -> Source: klasa potomna judgments_core.source.Source
    source: Callable
    migrations: Path  # katalog NNN_*.sql projektu (po migracjach rdzenia)
    # klasa importu: Importer albo jego klasa potomna w projekcie
    importer: type = Importer
    # Strategie transportu po kolei (judgments_core/transport.py): następna
    # przejmuje, gdy Cloudflare blokuje bieżącą. <PREFIKS>_TRANSPORT (jedna
    # nazwa albo kilka po przecinku) ma pierwszeństwo.
    transports: tuple = ("requests",)
    # klasa potomna Deriver (źródło, TableWriter, after()): polecenie przetworz; None = brak
    deriver: type | None = None
    # verify(store, source, years) -> wyniki: porównanie źródła z bazą; None = brak
    verify: Callable | None = None
    # klasa potomna judgments_core.web.WebApp (settings) -> WSGI: polecenie www; None = brak
    web: Callable | None = None
    # klasa potomna judgments_core.embed.EmbedJob: polecenie wektory; None = brak
    embed_job: type | None = None
    # klasa potomna judgments_core.mcp.McpServer: polecenie mcp; None = brak
    mcp: type | None = None
    # klasa potomna judgments_core.rag.Retriever: polecenia fragmenty, ocen; None = brak
    retriever: type | None = None
    # klasa potomna judgments_core.pipeline.RagPipeline: polecenie zapytaj; None = brak
    rag: type | None = None


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
    www = commands.add_parser("www", help="wyszukiwarka w przeglądarce (serwer lokalny)")
    www.add_argument("--host", default="127.0.0.1", help="adres (domyślnie 127.0.0.1)")
    www.add_argument("--port", type=int, default=8080, help="port (domyślnie 8080)")
    vectors = commands.add_parser(
        "wektory", help="policz embeddingi brakujących/zmienionych orzeczeń do bazy wektorów"
    )
    vectors.add_argument("--limit", type=int, help="zakończ po N orzeczeniach")
    vectors.add_argument(
        "--budzet",
        dest="time_budget_s",
        type=int,
        help="zakończ po tylu sekundach (domyślnie z ustawień, 0 = bez limitu)",
    )
    vectors.add_argument(
        "--od-poczatku",
        dest="from_start",
        action="store_true",
        help="policz wszystko od nowa (np. po zmianie chunkera)",
    )
    commands.add_parser("mcp", help="serwer MCP dla Claude'a (stdio; uruchamia go Claude)")
    passages = commands.add_parser(
        "fragmenty", help="najlepsze fragmenty orzeczeń do pytania (kontekst dla modelu)"
    )
    passages.add_argument("question", metavar="pytanie")
    passages.add_argument(
        "--limit-znakow", dest="budget_chars", type=int, help="ile tekstu (domyślnie 16000)"
    )
    evaluate = commands.add_parser(
        "ocen", help="trafność fragmentów na pytaniach wzorcowych (<projekt>/pytania.json)"
    )
    evaluate.add_argument("--plik", dest="path", help="plik z pytaniami (JSON)")
    check = commands.add_parser(
        "ocen-odpowiedzi",
        help="odpowiedzi modelu na pytania wzorcowe: cytowania, wynik sprawy, model-sędzia",
    )
    check.add_argument("--plik", dest="path", help="plik z pytaniami (JSON)")
    check.add_argument("--pytania", dest="picks", help="numery pytań, np. 1,4,11")
    check.add_argument(
        "--bez-sedziego",
        dest="no_judge",
        action="store_true",
        help="tylko reguły, bez modelu-sędziego",
    )
    check.add_argument(
        "--limit-znakow", dest="budget_chars", type=int, help="ile tekstu (domyślnie 16000)"
    )
    packages = commands.add_parser(
        "paczki", help="paczki fragmentów do liczenia wektorów na innych komputerach"
    )
    packages.add_argument(
        "--ile", dest="count", type=int, default=1, help="ile paczek (domyślnie 1)"
    )
    packages.add_argument(
        "--fragmentow",
        dest="size_chunks",
        type=int,
        default=200_000,
        help="fragmentów w paczce (domyślnie 200000)",
    )
    packages.add_argument(
        "--od-konca",
        dest="from_end",
        action="store_true",
        help="z końca kolejki (najmniej ważne), początek zostaje dla wektory tutaj",
    )
    packages.add_argument("--lista", dest="list", action="store_true", help="pokaż paczki")
    packages.add_argument(
        "--zwolnij",
        dest="release",
        metavar="NAZWA",
        help="porzuć paczkę (wektory policzy je tutaj)",
    )
    package_import = commands.add_parser(
        "paczka-import", help="wczytaj wektory paczki policzonej gdzie indziej"
    )
    package_import.add_argument("name", metavar="nazwa", help="np. nsa-0001")
    ask = commands.add_parser(
        "zapytaj", help="odpowiedź modelu językowego (LM Studio) na podstawie fragmentów"
    )
    ask.add_argument("question", metavar="pytanie")
    ask.add_argument(
        "--limit-znakow", dest="budget_chars", type=int, help="ile tekstu (domyślnie 16000)"
    )
    ask.add_argument(
        "--tylko-zrodla",
        dest="sources_only",
        action="store_true",
        help="pokaż prompt ze źródłami, bez wołania modelu",
    )
    evaluate.add_argument(
        "--limit-znakow", dest="budget_chars", type=int, help="ile tekstu (domyślnie 16000)"
    )
    commands.add_parser(
        "kopia", help="zrzut bazy do <PREFIKS>_BACKUP_DIR (też sam po pobierz/przetworz)"
    )
    return parser


def setup_logging(project, settings, stream=None):
    handlers = [logging.StreamHandler(stream or sys.stdout)]
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
    # mcp rozmawia z Claude'em przez stdout - log tylko na stderr i do pliku
    setup_logging(project, settings, sys.stderr if args.command == "mcp" else None)
    try:
        if args.command == "migracje":
            return _migrate(project, settings)
        if args.command == "pobierz":
            return _fetch(project, settings, args)
        if args.command == "przetworz":
            return _derive(project, settings, args)
        if args.command == "sprawdz":
            return _verify(project, settings, args)
        if args.command == "www":
            return _www(project, settings, args)
        if args.command == "wektory":
            return _embed(project, settings, args)
        if args.command == "mcp":
            return _mcp(project, settings)
        if args.command == "fragmenty":
            return _passages(project, settings, args)
        if args.command == "ocen":
            return _evaluate(project, settings, args)
        if args.command == "zapytaj":
            return _ask(project, settings, args)
        if args.command == "ocen-odpowiedzi":
            return _check_answers(project, settings, args)
        if args.command == "paczki":
            return _packages(project, settings, args)
        if args.command == "paczka-import":
            return _package_import(project, settings, args)
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
    """The project's source over its transport strategy; closer closes it at the end."""
    names = settings.transport.split(",") if settings.transport else project.transports

    def switched(old, new, url):
        if on_event is not None:
            on_event("transport", f"{old} blokowany - przejście na {new}", url, None)

    transport = make_transport(names, on_switch=switched)
    log.info("transport: %s", " -> ".join(n.strip() for n in names))
    client = PoliteClient.from_settings(settings, transport=transport, on_event=on_event)
    source = project.source(settings, client)
    closer.callback(client.close)
    return source


def _lock_name(project):
    # Jedna blokada dla pobierania i przetwarzania: przetwarzanie w trakcie
    # pobierania mogłoby minąć rekord zapisany z wcześniejszym fetched_at.
    return f"{project.name}:baza"


@contextlib.contextmanager
def _logged_run(project, settings, command, cursor_start=None, lock=None):
    """Connection, lock and a fetch_runs row; the row is closed also on errors.

    Yields (store, run); the body sets run.status and run.counts.
    """
    with db.connect(settings.db_dsn) as conn:
        store = db.MariaDbStore(conn)
        with store.lock(lock or _lock_name(project)):
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
            result = project.importer(
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
        importer = project.importer(
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
    if project.deriver is None:
        raise NotImplementedError(f"projekt {project.name} nie ma jeszcze parsera")
    budget = settings.time_budget_s if args.time_budget_s is None else args.time_budget_s
    with _logged_run(project, settings, "przetworz") as (store, run):
        deriver = project.deriver(
            store,
            from_start=args.from_start,
            limit=args.limit,
            time_budget_s=budget,
        )
        try:
            result = deriver.run()
        finally:
            r = deriver.result
            run.counts.update(pages=r.batches, records=r.records, problems=r.problems)
        if result.records:
            with store.conn.cursor() as cur:
                deriver.after(cur)
            store.conn.commit()
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


def _embed(project, settings, args):
    if project.embed_job is None:
        raise NotImplementedError(f"projekt {project.name} nie ma jeszcze wektorów")
    budget = settings.time_budget_s if args.time_budget_s is None else args.time_budget_s
    # Hurtem liczy LM Studio na karcie graficznej (CPU byłby kilka razy wolniejszy).
    embedder = make_embedder(settings, "http")
    collection = settings.qdrant_collection or collection_name(project.name, settings.embed_model)
    vectors = QdrantStore(settings.qdrant_url, collection, settings.embed_model)
    log.info(
        "model %s -> Qdrant %s, kolekcja %s",
        embedder.describe(),
        vectors.url,
        collection,
    )
    # Własna blokada: liczenie trwa godzinami i tylko czyta orzeczenia, więc
    # pobierz/przetworz mogą iść obok (zmieniony tekst złapie następny przebieg).
    with _logged_run(project, settings, "wektory", lock=f"{project.name}:wektory") as (store, run):
        job = project.embed_job(
            store.conn,
            embedder,
            vectors,
            limit=args.limit,
            time_budget_s=budget,
            from_start=args.from_start,
            batch_size=settings.embed_batch,
        )
        result = job.run()
        run.counts.update(pages=result.groups, records=result.judgments)
        run.status = _status_of(result)
    end = (
        "wszystko policzone"
        if result.finished
        else f"przerwano ({result.stopped_by}), zostało {result.remaining}"
    )
    log.info(
        "Gotowe: %d orzeczeń, %d fragmentów, usunięto wektory %d orzeczeń; w kolekcji %d punktów; %s.",
        result.judgments,
        result.chunks,
        result.removed,
        vectors.count(),
        end,
    )
    return 0


def _www(project, settings, args):
    if project.web is None:
        raise NotImplementedError(f"projekt {project.name} nie ma jeszcze stron www")
    serve(project.web(settings), args.host, args.port)
    return 0


def _mcp(project, settings):
    if project.mcp is None:
        raise NotImplementedError(f"projekt {project.name} nie ma jeszcze serwera MCP")
    project.mcp(settings).run()
    return 0


def _retriever(project, settings):
    if project.retriever is None:
        raise NotImplementedError(f"projekt {project.name} nie ma jeszcze fragmentów (RAG)")
    return project.retriever.from_settings(settings, project.name)


def _passages(project, settings, args):
    retriever = _retriever(project, settings)
    with db.connect(settings.db_dsn, autocommit=True) as conn, conn.cursor() as cur:
        result = retriever.retrieve(cur, retriever.query(args.question), args.budget_chars)
    if result.notice:
        log.info("%s", result.notice)
    print(render(result))
    log.info(
        "%d orzeczeń, %d znaków (tryb %s)", len(result.sources), result.chars, result.mode or "?"
    )
    return 0


def _evaluate(project, settings, args):
    from judgments_core import evaluation

    path = Path(args.path) if args.path else project.migrations.parent / "pytania.json"
    questions = evaluation.load(path)
    retriever = _retriever(project, settings)
    with db.connect(settings.db_dsn, autocommit=True) as conn, conn.cursor() as cur:
        scores = evaluation.evaluate(retriever, cur, questions, args.budget_chars)
    print("\n".join(evaluation.report(scores)))
    return 0


def _ask(project, settings, args):
    if project.rag is None or project.retriever is None:
        raise NotImplementedError(f"projekt {project.name} nie ma jeszcze odpowiedzi (RAG)")
    pipeline = project.rag.from_settings(settings, project.name, project.retriever)
    with db.connect(settings.db_dsn, autocommit=True) as conn, conn.cursor() as cur:
        retrieval = pipeline.retrieve(cur, args.question, args.budget_chars)
    if retrieval.notice:
        log.info("%s", retrieval.notice)
    log.info(
        "%d orzeczeń, %d znaków (tryb %s); model %s",
        len(retrieval.sources),
        retrieval.chars,
        retrieval.mode or "?",
        pipeline.chat.describe(),
    )
    if args.sources_only:
        for message in pipeline.messages(args.question, retrieval):
            print(f"=== {message['role']}\n{message['content']}\n")
        return 0
    pieces = []
    for piece in pipeline.stream(args.question, retrieval):
        pieces.append(piece)
        print(piece, end="", flush=True)
    answer = pipeline.answer(args.question, retrieval, pieces)
    print("\n\nŹródła:")
    for s in retrieval.sources:
        mark = "" if s.number in answer.cited else " (nieużyte)"
        print(f"[{s.number}] {s.title} - {s.details}{mark}\n    {s.url}")
    if answer.unknown:
        print(f"\nUWAGA: odpowiedź powołuje się na nieistniejące źródła: {answer.unknown}")
    return 0


def _check_answers(project, settings, args):
    from datetime import datetime, timezone

    from judgments_core import answer_eval, evaluation
    from judgments_core.chat import make_judge

    if project.rag is None or project.retriever is None:
        raise NotImplementedError(f"projekt {project.name} nie ma jeszcze odpowiedzi (RAG)")
    path = Path(args.path) if args.path else project.migrations.parent / "pytania.json"
    questions = evaluation.load(path)
    if args.picks:
        questions = [questions[int(n) - 1] for n in args.picks.split(",")]
    pipeline = project.rag.from_settings(settings, project.name, project.retriever)
    judge = None if args.no_judge else make_judge(settings)
    checks = []
    with db.connect(settings.db_dsn, autocommit=True) as conn, conn.cursor() as cur:
        for n, q in enumerate(questions, 1):
            log.info("pytanie %d/%d: %s", n, len(questions), q.text[:80])
            checks.append(
                answer_eval.check(pipeline, cur, q.text, q.filters, judge, args.budget_chars)
            )
    model = pipeline.chat.model or "?"
    print("\n".join(answer_eval.report(checks, model)))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = project.migrations.parent / "database" / "odpowiedzi" / f"{stamp}-{model}.json"
    note = f"suffix={settings.chat_suffix!r} temperature={settings.chat_temperature}"
    answer_eval.save(checks, out, model, note)
    log.info("Odpowiedzi zapisane: %s", out)
    return 0


def _package_dir(project):
    return project.migrations.parent / "database" / "paczki"


def _package_job(project, settings, conn):
    if project.embed_job is None:
        raise NotImplementedError(f"projekt {project.name} nie ma jeszcze wektorów")
    collection = settings.qdrant_collection or collection_name(project.name, settings.embed_model)
    return project.embed_job(
        conn,
        make_embedder(settings, "http"),  # tylko nazwa modelu: nic tu nie liczy
        QdrantStore(settings.qdrant_url, collection, settings.embed_model),
    )


def _packages(project, settings, args):
    from judgments_core import packages

    with db.connect(settings.db_dsn) as conn:
        store = db.MariaDbStore(conn)
        if args.list:
            for name, model, judgments, chunks, chars, created, imported in store.packages_list():
                state = f"zaimportowana {imported:%Y-%m-%d %H:%M}" if imported else "u kogoś"
                print(
                    f"{name}  {created:%Y-%m-%d %H:%M}  {judgments:>8,} orzeczeń"
                    f"  {chunks:>9,} fragmentów  {chars / 1e6:>7,.0f} mln znaków  {state}"
                )
            return 0
        if args.release:
            store.packages_close(args.release, imported=False)
            log.info("Paczka %s porzucona: jej orzeczenia policzy wektory.", args.release)
            return 0
        job = _package_job(project, settings, conn)
        # Wektory kontrolne liczy tutaj model zapytań (bez LM Studio, na CPU).
        controls = make_embedder(settings)
        with store.lock(f"{project.name}:paczki"):
            for _ in range(args.count):
                name = packages.next_name(store, project.name)
                done = packages.export(
                    job, controls, _package_dir(project), name, args.size_chunks, args.from_end
                )
                if done is None:
                    log.info("Nie ma już nic do policzenia.")
                    break
                log.info(
                    "Paczka %s: %d orzeczeń, %d fragmentów, %.0f mln znaków -> %s",
                    done.name,
                    done.judgments,
                    done.chunks,
                    done.chars / 1e6,
                    done.path,
                )
    return 0


def _package_import(project, settings, args):
    from judgments_core import packages

    with db.connect(settings.db_dsn) as conn:
        store = db.MariaDbStore(conn)
        job = _package_job(project, settings, conn)
        with store.lock(f"{project.name}:paczki"):
            saved, skipped = packages.import_package(job, _package_dir(project), args.name)
    log.info(
        "Paczka %s: zapisano wektory %d orzeczeń, pominięto %d (tekst zmieniony albo już policzone).",
        args.name,
        saved,
        skipped,
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
