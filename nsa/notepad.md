# Notepad: NSA / CBOSA (administrative courts)

Judgments of the Supreme Administrative Court (NSA) and the voivodeship administrative courts (WSA) from CBOSA, https://orzeczenia.nsa.gov.pl. Core notes: [`../notepad.md`](../notepad.md).

## 1. State (2026-10-17)
- Skeleton only: `python -m nsa migracje` creates the core tables in the `nsa` database; `python -m nsa pobierz` stops with "Jeszcze nie zrobione" (exit 2), because `nsa/source.py` (`CbosaSource`) is a stub.
- No project tables yet (`nsa/migrations/` empty): DTO and structure come after the test queries.
- Nothing has been requested from CBOSA yet. What `nsa/source.py` says about the site is from memory and **must be verified**.

## Next steps
1. **Test queries (read-only, a few requests, by hand or a scratch script with `PoliteClient`):**
   - `robots.txt` and the site's terms: is automated downloading allowed, at what pace? Set `NSA_PAUSE_S` accordingly.
   - Search: which form parameters (court, date range, judgment type, case number, "with reasoning"), GET or POST, sessions/cookies, how many results per page, a limit on the number of result pages?
   - Detail page `/doc/<id>`: which metadata fields (case number, court, date, judges, symbol of the case type, keywords, related judgments, cited regulations, is the judgment final), how the full text is structured (sentence / reasoning), encoding.
   - Size: how many judgments in total and per day (for disk space on the hosting and for the first full download time).
   - Updates: how to find new and changed judgments (by date added? by judgment date only?). Decides the cursor.
   - Save 20-50 sample detail pages to `tests/fixtures/nsa/` for parser tests (real records, as in SAOS).
2. **Decide with you:** which judgments to keep (all, or only with reasoning like in SAOS?), DTO fields, then `nsa/migrations/001_judgments.sql`.
3. **Write `CbosaSource`:** probably two stages - result lists by date (ids) and detail pages; both raw HTML into `raw_records` (`source` e.g. `cbosa`). Cursor e.g. `{"date": "...", "page": N}`.
4. **Parser:** `raw_records` -> project tables (HTML parsing: stdlib `html.parser`, or add a pure-Python dependency if needed; no compiled packages like lxml unless the hosting has them), plus a command to re-run it over stored raw records.
5. **First import** locally for a short period, check the data, then on the hosting with cron (`--budzet`).
6. Later: search, web pages, RAG (vector store from the core notes).
