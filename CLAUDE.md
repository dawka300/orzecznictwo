# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Shared skeleton for projects that download court judgments into MariaDB and run unattended on shared hosting (seohost, Python, cron). Projects: `nsa/` (Polish administrative courts, CBOSA) and `etpc/` (European Court of Human Rights, HUDOC). Grew out of the local `orzeczenia_SAOS` project (SAOS in Postgres); this repo is MariaDB-only, vectors go to a separate dedicated store later. README.md explains the layout; each project's `notepad.md` has its state and next steps.

## Running

- Python: `.venv/Scripts/python.exe` locally (3.13). Code must stay compatible with **Python 3.9+** (hosting may be older): `from __future__ import annotations` in modules with type hints, no `match`, no `datetime.UTC` (use `timezone.utc`), no 3.10+ stdlib. Ruff targets py39 (`pyproject.toml`).
- Dependencies stay minimal and pure Python (`requests`, `PyMySQL`): no compiled packages on the hosting path. Dev-only tools go in `requirements-dev.txt`.
- `docker compose up -d`: local MariaDB 10.11 on 127.0.0.1:3306 (user/password `szkielet`, root `root`), databases `nsa`, `etpc`, `szkielet_test` (created by `docker/mariadb-init.sql` on first volume creation only); Qdrant 1.19 (vector store, local only) REST on 127.0.0.1:6333.
- Commands (from the repo root): `python -m nsa migracje | pobierz [--limit N] [--na-sucho] [--od-poczatku] [--budzet S] | stan | kopia`, same for `etpc`; `python -m etpc www` = search page on http://127.0.0.1:8080; locally it runs as the compose service `www` (restarts with docker, mounts the repo: `docker compose restart www` after code changes; a manual `www` would collide on port 8080). `python -m etpc wektory` = embeddings (LM Studio) into Qdrant. `kopia` (and, with `<PREFIX>_BACKUP_DIR` set, every pobierz/przetworz that changed something) dumps the DB to `<backup_dir>/<project>.sql.gz`; locally ETPC goes to `etpc/database/` (gitignored). Shared CLI in `judgments_core/cli.py`; a project's `__main__.py` only builds a `Project`.
- Tests: `.venv/Scripts/python.exe -m pytest`. DB tests use `TEST_DB_DSN` (default the compose `szkielet_test`), drop all its tables, and are skipped when MariaDB isn't running. Never point `TEST_DB_DSN` at a real database.
- Settings: `judgments_core/settings.py`, env vars `PREFIX_FIELD` (`NSA_DB_DSN`, `ETPC_PAUSE_S`, ...), from the environment or the repo-root `.env` (path is fixed, not cwd, because cron starts elsewhere). Empty DSN = local compose DB named after the project.

## Design rules

- **Raw first:** sources yield `RawRecord`s (JSON/HTML text as received) into `raw_records`; project tables/DTOs are derived from them by a parser, so parser changes never need a re-download. `changed_at` moves only when the body's sha256 changes.
- **Resumable:** `Importer` saves each page's records and the source's cursor in one transaction (`MariaDbStore.save_page`). The cursor is an opaque string the source defines. A pass may be killed anytime and the next resumes.
- **Cron, not a daemon:** each pass stops after `--budzet`/`*_TIME_BUDGET_S` seconds; `MariaDbStore.lock` (`GET_LOCK`) makes an overlapping pass exit with code 0.
- **Be polite to sources:** keep `PoliteClient`'s pause and backoff; set a contact in `*_USER_AGENT`.
- All MariaDB SQL of the core is in `judgments_core/db.py`. MariaDB, not MySQL 8: no `INSERT ... AS new` (use `VALUES(col)`), check features against the hosting's MariaDB version.
- `judgments_core` must not import from projects; projects import the core.
- **Core = common parent, projects = children.** When a project needs a core class to behave differently (another database, another save step, other paging ...), it gets a subclass in its own package (e.g. `nsa/importer.py: class NsaImporter(Importer)`, overriding only what changes) and its `__main__`/`Project` uses that child. Never put one project's case into a core file (no `if project == ...`); change the core only for what is common to all, and then keep every project working. `Project` takes the project's classes: `source`, `importer`, `deriver`, `embed_job`, `web` (children of `Source`, `Importer`, `Deriver`, `EmbedJob`, `WebApp`), plus `transports` (strategy chain of `judgments_core/transport.py`). Parents in the core: Transport, Source, Importer, Parser, TableWriter, Deriver, Chunker, EmbedJob, SemanticSearch, WebApp (map in the root `notepad.md`).

## Schema changes

- Core tables: `judgments_core/migrations/NNN_*.sql`; project tables: `<project>/migrations/NNN_*.sql`. Applied by `python -m <project> migracje`, recorded in `schema_migrations` as `core/001_core.sql`, `nsa/001_...`. Never edit an applied migration; add a new one.
- MariaDB commits each DDL statement immediately: a migration failing halfway stays half-applied. Keep migrations small and re-runnable (`IF NOT EXISTS`). Statements are split on `;` at line ends, so no procedures/triggers in migration files.
- Tables: `ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`; indexed VARCHAR max 191. DATETIME columns hold UTC.

## Conventions

- Code identifiers and docstrings are English. CLI commands and flags (`pobierz`, `--na-sucho`, ...), help text, log/printed messages and comments are Polish; flags map to English `args.*` via `dest=`.
- `ruff format` + `ruff check` before committing.
