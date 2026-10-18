# Notepad: ETPC / HUDOC (European Court of Human Rights)

Judgments and decisions of the European Court of Human Rights from HUDOC, https://hudoc.echr.coe.int. Core notes: [`../notepad.md`](../notepad.md).

## 1. State (2026-10-17)
- Skeleton only: `python -m etpc migracje` creates the core tables in the `etpc` database; `python -m etpc pobierz` stops with "Jeszcze nie zrobione" (exit 2), because `etpc/source.py` (`HudocSource`) is a stub.
- No project tables yet (`etpc/migrations/` empty): DTO and structure come after the test queries.
- Nothing has been requested from HUDOC yet. What `etpc/source.py` says (JSON search endpoint used by the site, full text per itemid) is from memory and **must be verified**; it's not an official, documented API.

## Next steps
1. **Test queries (read-only, a few requests):**
   - Terms of use and `robots.txt`; pace (`ETPC_PAUSE_S`).
   - The search endpoint the HUDOC page calls (browser dev tools, Network tab): query syntax, available fields (itemid, docname, appno, respondent state, dates, document type, language, importance level, Convention articles, conclusion, ECLI ...), paging (start/length), sorting, maximum page size.
   - Full text: how to get it per itemid (HTML? Word?), its structure (procedure, facts, law, operative part, separate opinions).
   - Size: number of documents per collection and language.
   - Updates: is there a "date added/modified" field to sort and resume by? Decides the cursor.
   - Save sample metadata JSON and full texts to `tests/fixtures/etpc/` for parser tests.
2. **Decide with you:**
   - Which collections: Grand Chamber/Chamber judgments, decisions, communicated cases, advisory opinions, resolutions ...?
   - Which languages: English/French originals, Polish translations, cases against Poland only or all states?
   - DTO fields, then `etpc/migrations/001_judgments.sql`. One case has many documents and language versions - probably a case table plus a document table.
3. **Write `HudocSource`:** metadata pages from the search endpoint plus full texts per itemid; both into `raw_records` (`source` e.g. `hudoc`, `hudoc-text`). Cursor e.g. `{"since": "...", "start": N}`.
4. **Parser:** `raw_records` -> project tables, plus a command to re-run it over stored raw records.
5. **First import** locally for a small slice (e.g. judgments against Poland, one year), check the data, then on the hosting with cron.
6. Later: search, web pages, RAG (vector store from the core notes; texts are mostly English/French, so the embedding model must handle them, and Polish questions if you'll ask in Polish).
