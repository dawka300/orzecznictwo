# Notepad: ETPC / HUDOC (European Court of Human Rights)

Judgments and decisions of the European Court of Human Rights from HUDOC, https://hudoc.echr.coe.int. Core notes: [`../notepad.md`](../notepad.md).

## 1. State (2026-10-18)
- `etpc/migrations/001_rulings.sql` applied locally. `HudocSource` written (`etpc/source.py`): judgments + decisions, all languages, no placeholders, `select=*`, pages of 50, keyset cursor `{"since": updateddate, "seen": [itemids at that timestamp]}` (stable order `updateddate, itemid`; `>=` with ms doesn't work, the range `updateddate:[... TO *]` does).
- First test: `python -m etpc pobierz --limit 10` saved 10 documents to `raw_records` (ENG/FRE/TUR, 2.6-62 KB of text each).
- **Parser** done: `python -m etpc przetworz [--od-poczatku] [--limit N] [--budzet S]` (core `Deriver` + `etpc/parser.py` pure DTOs + `etpc/tables.py` SQL). Processes raw records changed since the last pass (`hudoc:derived` in `sync_state`), oldest first, only those from before the current second (no record can slip behind the mark). Each document is upserted into `ruling_documents` under its ECLI; each touched ruling is rebuilt from its best version (ENG > FRE > any): `rulings` row + side tables replaced. A document that changed ECLI moves; a ruling left without documents is deleted. The 10 test documents -> 10 rulings locally.
- `pobierz` and `przetworz` share one lock (`etpc:baza`). Cron: `python -m etpc pobierz --budzet 240 && python -m etpc przetworz --budzet 60` (to tune on the hosting).
- **Cloudflare:** Python's TLS (requests/urllib, Windows and Linux) gets `403` + `cf-mitigated: challenge` for every request; curl (Windows Schannel and Linux OpenSSL) passes, but part of its requests are challenged too, seemingly at random (more after a burst of test requests). Hence: the ETPC project uses `transport="curl"` (`CurlSession` in `judgments_core/http.py`, system curl through subprocess), and `PoliteClient` retries a challenge after 30 s, 60 s, ... **To check on the hosting:** curl present and passing from its IP; pace (`ETPC_PAUSE_S`) maybe higher.
- Test queries done 2026-10-18 (~100 requests overall). Samples in `tests/fixtures/etpc/`.

## Decisions (2026-10-18)
- **Collections:** JUDGMENTS and DECISIONS first (~154k documents incl. placeholders). Communicated cases, resolutions, advisory opinions maybe later (`rulings.kind` is VARCHAR for that).
- **Languages:** all. Placeholders are skipped.
- **No HTML:** only `contentbody` (plain text); rendering is ours if ever needed.
- **Schema** (`001_rulings.sql`): `rulings` = one ECLI (date, kind, formation, importance, title from ENG > FRE > any, conclusion, ...); `ruling_documents` = one row per itemid/language with the full text, FULLTEXT on `title` and `body`, `body_sha256` for RAG re-embedding; side tables for filters: `ruling_applications` (appno), `ruling_respondents`, `ruling_articles` (concerned/violation/no_violation), `ruling_keywords` (thesaurus ids, labels later), `ruling_citations` (cited appno). All derived from `raw_records` by the parser; child rows cascade on delete, so the parser can replace a ruling's rows.
- Tested locally: FULLTEXT + filters in one query works, accent-insensitive (`rucinski` finds `RUCIŃSKI`, utf8mb4_unicode_ci).
- **Polish questions:** the search layer (later) translates a Polish phrase to EN/FR (LLM/API) and runs FULLTEXT on those languages plus POL; RAG later with a multilingual embedding model. No schema change needed for it.

## Open
- **Size:** the text is stored twice (`raw_records.body` JSON and `ruling_documents.body`) plus the FULLTEXT index. Estimate after the first import (avg text size x ~125k real documents); if the hosting quota is tight: InnoDB page compression or dropping `contentbody` from old raw records.
- MariaDB FULLTEXT: no stemming, min word length 3 (`innodb_ft_min_token_size`, can't change on shared hosting), stopword list English. Good enough for a start; real semantic search comes with RAG.
- Thesaurus labels for `kpthesaurus` ids: source not found yet.
- Codes without a dictionary yet: `originatingbody`, `typedescription`, `applicability`.

## 2. HUDOC findings (2026-10-18)
- No `robots.txt` (the SPA page is returned). Not an official API; no login, no key.
- **Search:** `GET /app/query/results?query=...&select=...&sort=...&start=N&length=M`, JSON `{"resultcount": N, "results": [{"columns": {...}}]}`.
  - Query syntax: `contentsitename:ECHR AND (NOT (doctype=PR OR doctype=HFCOMOLD OR doctype=HECOMOLD)) AND documentcollectionid="JUDGMENTS" AND respondent="POL" AND languageisocode="ENG"`; ranges `updateddate>="2026-10-15T00:00:00.0Z"` or `updateddate:[2026-10-15T00:00:00Z TO *]`.
  - `select=*` returns ~150 fields **including `contentbody` = full plain text** (no separate request needed for text). `select=itemid,docname,...` for light lists.
  - `sort=updateddate Ascending` works. `length` up to at least 2000.
  - **Window limit: `start + length <= 10000`** (start=10000 returns `resultcount: 0`). So paging = keyset: filter `updateddate>=last`, sort ascending, skip itemids already seen at the boundary timestamp. Timestamps are ms and nearly unique (max 16 identical in a 2000 sample).
- **Fields** (useful): `itemid` (001-252650), `docname`, `appno` (`;`-separated), `respondent`, `doctype` (HEJUD/HFJUD/HEDEC/HFDEC/HJUDPOL ...), `documentcollectionid2` (`CASELAW;JUDGMENTS;CHAMBER;ENG`), `languageisocode`, `kpdate` (decision date), `judgementdate`, `importance` (1-4, key cases lower), `article`, `conclusion`, `violation`/`nonviolation`, `separateopinion`, `ecli`, `representedby`, `kpthesaurus` (ids), `extractedappno` (cited cases), `isplaceholder`, `createddate`, `updateddate`. Many duplicated as `echr*`. Dates mixed: ISO and `dd/mm/yyyy hh:mm:ss`.
- **Placeholders:** `isplaceholder="True"` records exist for language versions with no document (e.g. 26,815 JUDGMENTS, FRE placeholders for English-only judgments, `application: NULL`). Skip or mark them.
- **Cloudflare:** one of ~35 requests got a Cloudflare "Just a moment..." HTML challenge instead of JSON (the next one worked). The source must detect non-JSON answers and back off, not save them.
- **Grouping:** language versions of one ruling share the `ecli` (ENG, FRE placeholder, POL translation of Ruciński v. Poland 2023: same ECLI; its just-satisfaction judgment 2026 has its own ECLI). In a 2000-document sample from 2004 every document had an ECLI and one ECLI never had differing appno.
- **Full text HTML:** `GET /app/conversion/docx/html/body?library=ECHR&id=<itemid>` - HTML from Word with generated CSS classes (`.s800EAC49`); structure (headings, numbered paragraphs, opinions) only from text/format. Plain `contentbody` may be enough; HTML only if formatting matters.
- **Size (2026-10-18):** all ECHR 212,554; JUDGMENTS 90,372 (HEJUD 29,357, HFJUD 29,357, rest translations), DECISIONS 63,467, COMMUNICATEDCASES 19,480, RESOLUTIONS 21,767, CLIN 13,090, REPORTS 4,261, ADVISORYOPINIONS 93. Polish language 1,316 (JUD 1,220); against Poland JUDGMENTS 3,862. Oldest `kpdate` 1955; `updateddate` min 2023-12-04 (bulk re-index), ~21k updated since mid-2024.

## Next steps
1. ~~**`HudocSource`**~~ done (see State): query `(documentcollectionid="JUDGMENTS" OR documentcollectionid="DECISIONS")`, `select=*`, `sort=updateddate Ascending`, keyset cursor `{"since": "<updateddate>", "seen": [itemids at that timestamp]}`, page ~200-500 (texts are large). One `RawRecord` per document (`source_id` = itemid, body = the `columns` JSON). Detect the Cloudflare challenge (non-JSON) -> back off/stop the pass.
2. ~~**Parser**~~ done (see State): `raw_records` -> tables, grouped by ECLI; a command to re-run it over stored raw records (incremental by `raw_records.changed_at`, state in `sync_state`). Tests on `tests/fixtures/etpc/`.
3. **First import** locally for a small slice, check data and sizes, then the whole set; then the hosting with cron.
4. Later: search page (filters + FULLTEXT + Polish->EN/FR translation), RAG (chunks of `ruling_documents.body`, multilingual embeddings, vector store from the core notes).

## Work in progress (2026-10-18, stopped by the user before the live test)
- Per-year counts in HUDOC (judgments / decisions, no placeholders): decisions from 1955 (Commission), judgments from 1960; hundreds per year until the 1990s, 3-5k per year after 2000; max 2009 = 5,383 < the 10,000 window. Total ~127k. 1 Cloudflare challenge in 144 requests. **`sort` parameter is required** (without it HUDOC answers 404; may be empty).
- Written and unit-tested (53 tests pass), **not yet run against HUDOC**:
  - `HudocSource`: first download year by year from 1955 (`{"mode": "lata", "year", "start", "since"}`, sort `itemid`), then changes by `updateddate` from the download start minus 1 day (`{"mode": "zmiany", ...}`); `fetch_ids`, `count_year`, `itemids_of_year`.
  - Core migration `002_logs.sql`: `fetch_runs` (every pobierz/przetworz/sprawdz pass), `fetch_events` (Cloudflare, 429, 5xx, network, rejected, given up - with time, URL, wait), `record_problems` (brak_w_bazie, nie_ma_w_hudoc, blad_parsera, pusty_tekst; resolved automatically when fixed).
  - `python -m etpc sprawdz [--od ROK] [--do ROK]` (`etpc/verify.py`): HUDOC vs raw_records per year, missing itemids -> problems; `pobierz --brakujace` fetches them; `stan [--problemy]` shows cursors, last runs, blocks (7 days), open problems.
  - Parser: one bad document no longer stops a batch (SAVEPOINT per document, problem recorded).
- Next: `python -m etpc migracje` (applies core/002), `pobierz --limit 150` (years 1955-...), `sprawdz --od 1955 --do 1960`, `stan`; then the full local download.

## Playwright (2026-10-18)
- After ~250 requests in one evening Cloudflare challenged **every** curl request (5 challenges in a row, 30-120 s waits, the user's pass stalled at 1957). Headless Chromium via Playwright from the same IP: 40/40 full-text requests OK (65 MB in 72 s), then the live run below without a single challenge.
- New transport `playwright` (`judgments_core/browser.py`, `PlaywrightSession`): opens the source's home page in headless Chromium, then `fetch()` from inside the page (like the site's own scripts); after a Cloudflare 403 it reopens the page. Optional dependency (requirements-dev.txt + `python -m playwright install chromium`), imported only when used. Chosen by `ETPC_TRANSPORT=playwright` (setting `transport` overrides the project default `curl`). Locally: put `ETPC_TRANSPORT=playwright` in `.env`.
- Live run OK: `pobierz --limit 200` (years 1955-1967, 14 s), `sprawdz --od 1955 --do 1967` (12 years equal; 1967 short by the 10 cut off by --limit -> problems), `pobierz --brakujace` fetched them and closed the problems, `przetworz` filled the tables, `stan` shows runs, the 5 earlier Cloudflare blocks and open problems.
- **PDF documents:** `application = ACROBAT` (old Commission decisions, translations). HUDOC gives text for some; 30 of 64 so far have an empty `contentbody` (scans?) -> problem `pusty_tekst`. Later: download the PDF (`/app/conversion/pdf/?library=ECHR&id=<itemid>`, to verify) and OCR if needed.
- Next: full local download with `ETPC_TRANSPORT=playwright python -m etpc pobierz --budzet 0`, then `sprawdz`, `przetworz`.
