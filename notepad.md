# Notepad: the skeleton (judgments_core)

Shared notes for the core. Each project has its own: [`nsa/notepad.md`](nsa/notepad.md), [`etpc/notepad.md`](etpc/notepad.md).

## 1. Created (2026-10-17)
- New repository next to `orzeczenia_SAOS`, built from what worked there: polite HTTP client with retries, resumable import, "keep the raw record, derive the rest", numbered migrations, Polish CLI.
- **One repository, two projects** (`nsa/`, `etpc/`) over a shared core (`judgments_core/`): a fix in the core reaches both; on the hosting you clone the whole repo and run one project (`python -m nsa ...`). If the projects drift far apart later, the core can become its own package.
- What works now: `python -m nsa|etpc migracje` (core tables `sync_state`, `raw_records`, `schema_migrations`), `pobierz` with resuming, `--limit`, `--budzet`, `--na-sucho`, a database lock against overlapping cron passes, `stan`. Only the sources themselves are missing (`NotImplementedError`, exit code 2).
- 28 tests pass, including the MariaDB ones against the local docker-compose MariaDB 10.11.
- Python 3.9 compatibility is checked only by ruff (target py39); nothing has been run on a real 3.9 (only 3.12/3.13 are installed here).

## Decisions
- **MariaDB** on the hosting for judgments; **vectors in a separate dedicated store** (not chosen). The core talks to it only through `judgments_core/vectors.py`.
- **Raw first:** every downloaded record goes to `raw_records` as received; DTOs and tables per project come after test queries.
- **Cron instead of a never-ending process:** a pass every few minutes with a time budget, resumed from the cursor. Reason: shared hosting usually kills long processes. To verify on seohost (below).
- **Minimal dependencies** (`requests`, `PyMySQL`), Python 3.9+.
- Language as in SAOS: English identifiers, Polish CLI/messages/comments.

## To check on seohost (before the first deployment)
- Is Python available (version? virtualenv? `pip install` allowed?), SSH access?
- Cron: allowed? minimum interval? time/CPU/memory limits per process (sets `*_TIME_BUDGET_S`)?
- MariaDB version (`SELECT VERSION();`, then set the same in `docker-compose.yml`), database size limit, disk quota - raw HTML/JSON takes space: estimate per project from test queries.
- Outbound HTTPS to orzeczenia.nsa.gov.pl and hudoc.echr.coe.int allowed?
- Remote access to MariaDB (for embedding and the vector store running elsewhere) or only localhost?

## Open questions
- **Vector store:** which one and where (e.g. Qdrant, or Postgres+pgvector on a VPS/managed service). Must be reachable from wherever embeddings are computed and from the search app.
- **Where embeddings are computed:** shared hosting has no GPU and little CPU, so most likely elsewhere (your PC with LM Studio, the same GGUF setup as decided in SAOS, or an embedding API), reading new judgments from MariaDB. Decide together with the vector store.
- **Search / web pages:** on the hosting (a Python app in the panel) or locally? Not needed until there is data.

## Next steps (core)
1. Hosting checks above; then deploy one project with an empty source to test cron, the lock and logs.
2. When the first project has a parser: a shared "re-derive tables from `raw_records`" command (like `rebuild_from_raw.py` in SAOS) - add to the core once its shape is clear from one real project.
3. Backups of the MariaDB database (`mysqldump`, or what the hosting panel offers).
4. Chunking + vector store implementation, when RAG starts.
