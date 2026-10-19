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

## 2. First check of CBOSA (2026-10-19, ~10 requests, Python requests)
- **No technical block seen:** Apache + WebSphere 6.1, no Cloudflare; plain `requests` gets 200 everywhere (0.6-1.2 s). Session cookies (`JSESSIONID`, `cookiesession1`) plus view preferences in cookies (`pola-orzeczenia`, `opcje-orzeczenia` = 10 results per page, sorted by date). IP blocking under load was NOT tested (deliberately: no bursts).
- **robots.txt** (all agents): `Disallow: /cbo/find`, `/cbo/search`, `/cbo/do/search`, `/cbo/do/find`, `/cbo/do/doc`, `/cbo/wsp`, `/cbo/pow`, `/cbo/*op=file`, `/cbo/*.txt`, `/doc/*.txt`, `/servlet`; MSNbot and GPTBot fully disallowed. **Detail pages `/doc/<ID>` are allowed; the search and result lists (the only way to discover IDs) are not.**
- **Sitemap** (`/sitemap.xml` -> `sitemap_d1..d3.xml.gz`): stale, lastmod 2009-06-28, only ~5,400 `/doc/<ID>` URLs. IDs are 10 hex chars (`846B7C2709`), not enumerable.
- **Detail page** `/doc/846B7C2709` (23 KB HTML): title "II SA/Bd 394/08 - Wyrok WSA w Bydgoszczy z 2008-07-21", table `info-list` with: Data orzeczenia, prawomocne, Data wpływu, Sąd, Sędziowie (with roles), Symbol z opisem, Hasła tematyczne, Skarżony organ, Treść wyniku, Powołane przepisy (Dz.U. + articles + act title), Sentencja, Uzasadnienie. Good for a parser.
- Pasted by the user: docs of the proxy-seller.com Python SDK (rotating paid proxies). **Not used:** rotating proxies to get around IP limits and the robots.txt ban on search would be circumventing the site's access rules. See options below.

## Options for getting the list of judgments (to decide)
1. **Ask NSA** (the data owner) - a request for re-use of public sector information (ustawa z 11.08.2021 o otwartych danych i ponownym wykorzystywaniu informacji sektora publicznego): an export/dump of CBOSA, or written consent to query `/cbo/find` slowly (e.g. 1 request / 5-10 s, at night, with a contact in the User-Agent). Best option; with consent no proxies are needed (one IP, polite pace).
2. **Look for existing open copies:** dane.gov.pl, other projects/datasets with CBOSA judgments (to check; SAOS has common courts/SN/TK/KIO, administrative courts probably not).
3. Meanwhile, independent of the source of IDs: parser of `/doc/<ID>` pages (allowed) on the ~5,400 sitemap IDs as fixtures/test data (slow pace).

## 3. JuDDGES/pl-nsa on Hugging Face (2026-10-19)
- https://huggingface.co/datasets/JuDDGES/pl-nsa (JuDDGES project, Wrocław University of Science and Technology / EU): CBOSA scraped up to 2025-03-05 (card), ~1.82M rows (datasets-server estimate), **46 parquet files, 31.3 GB**, no license on the card (judgments themselves: official documents, art. 4 of the copyright act; ask the authors before a public service). Not gated.
- **NSA + all WSA** despite the name ("source": "nsa" = orzeczenia.nsa.gov.pl). File `data_0.parquet` (684 MB, 50,000 rows): WSA 41,638 / NSA 8,362; Wyrok 26,880, Postanowienie 23,081, Uchwała 28; years mostly 2004-2022 (2023: 438, 2024: 3 in this file - check the other files for 2023-2025); 88% with reasoning, 1.8% with a thesis, 37 dissenting opinions.
- 26 columns: judgment_id (= CBOSA `/doc/<ID>`), docket_number, judgment_type, finality, judgment_date / submission_date (timestamp tz Europe/Warsaw - needs `tzdata` on Windows), court_name, judges, presiding_judge, judge_rapporteur, case_type_description[], keywords[], related_docket_numbers[] (judgment_id, docket_number, date, type), challenged_authority, decision[], extracted_legal_bases[] (link to ISAP, article, journal, law), official_collection[], glosa_information[], thesis, sentence, reasons_for_judgment, dissenting_opinion, full_text.
- **full_text = the parts again** (ratio 1.002: "SENTENCJA ..." + sentence + reasons ...): texts are stored twice. full_text median 7.8k chars, p90 33k, ~13.5k avg -> ~25 GB of text for 1.82M rows. Plan: keep raw rows without full_text (rebuildable from the parts), else ~50 GB raw. Sizes matter for the hosting quota (ETPC is ~10 GB).
- Locally: `pyarrow` 26 + `tzdata` in `.venv` (dev only; the hosting gets the finished database). File kept in `nsa/database/juddges/` (gitignored).

## 4. Import of file 0 (2026-10-19)
- Written: `nsa/juddges.py` (`JuddgesSource`: parquet files from HF into `nsa/database/juddges/`, downloaded on demand via a .part file; batches of 500 rows; cursor `{"file": n, "row": k}`; RawRecord = row JSON without full_text, source_id = CBOSA id, url = CBOSA /doc/ page), `nsa/parser.py` (pure DTO), `nsa/tables.py` (derive: upsert by cbosa_id, side tables replaced; no text -> skipped + problem `pusty_tekst`; no docket -> saved with "" + problem `brak_sygnatury`), migration `nsa/001_judgments.sql` (judgments + judges, symbols, keywords, results, legal_bases, related, publications; FULLTEXT on thesis+sentence+reasons+dissenting_opinion). Tests `tests/test_nsa.py` (fixture: 5 real rows, texts cut, `tests/fixtures/nsa/juddges_rows.json`).
- `python -m nsa pobierz --limit 50000` = exactly file 0: 71 s. `przetworz`: 8 min 15 s. Result: 49,999 judgments (wyrok 26,871 incl. NSA 4,833; postanowienie 23,078; uchwała 28; inne 11), judges 104k, legal bases 60k, related 26k; 11 without docket, 1 without text.
- **Size for 50k: ~3.4 GB** (raw_records 1.08 GB, judgments 0.87 GB, FULLTEXT ~1.2 GB, side tables ~40 MB, plus free space in tablespaces) -> **~125 GB for all 1.82M** + 31 GB of parquet files. Disk C: 236 GB free. Full derive ~5 h (8 min per file). To decide before the full import: keep texts in raw_records or not (parquet files are an immutable copy), InnoDB page compression, FULLTEXT on reasons or only thesis+sentence.
- A test (`test_cli`) still used the NSA project as "source not written" and started a real dry run: it downloaded data_1..3 (kept, valid) - the test now uses a stub project.
- Files present: data_0..data_3 (not yet imported beyond file 0).
- **Compression** (`nsa/002_compress.sql`, `PAGE_COMPRESSED=1 PAGE_COMPRESSION_LEVEL=6`, zlib, punch hole on ext4): judgments.ibd 949 MB -> 390 MB on disk (du; ls/data_length unchanged). ALTER took 2 min 19 s for 50k rows. FULLTEXT phrase query still ~0.2 s. Check punch-hole support on the hosting. Texts stay in raw_records (user: disk space is not a concern).
- Full import started 2026-10-19 ~17:00 in the background: `pobierz` (all 46 files, downloads the missing ones) then `przetworz`.

## 5. NSA classes and pages (2026-10-19)
- Children of the core parents: `NsaChunker` (parts TEZA / SENTENCJA / UZASADNIENIE / ZDANIE ODRĘBNE from `nsa.parser.judgment_body`, paragraphs packed, ~1700 chars), `NsaEmbedJob` (`judgment_vectors`, migration 003; order: uchwały, with thesis/publication, NSA wyroki, WSA wyroki with reasons, NSA rest, rest; payload kind, court, is_nsa, year, symbols, keywords, cbosa_id, docket), `NsaSemantic` (filters), `NsaWebApp` (`nsa/web.py`; search with filters court/instance/kind/years/symbol/keyword/judge/authority/result/finality/thesis, judgment page with related, legal bases (ISAP), CBOSA link, similar; statistics + vector progress), `nsa/search.py` (FULLTEXT with Polish stems: words >= 5 letters searched as stem* (kasacyjna -> kasacyj*), "bez odmiany" switches it off; hybrid RRF like ETPC), `nsa/stats.py` + migration 004 (`judgment_stats`, refreshed by `NsaDeriver.after`, 0.5 s for 50k).
- Pages: http://127.0.0.1:8082 (compose service `www-nsa`). Trial `python -m nsa wektory --limit 5`: 5 NSA resolutions, 166 chunks; hybrid "czy wydatki na aplikację radcowską są kosztem uzyskania przychodu" finds I FPS 3/10, II FPS 5/16 ...; similar works.

## Where we stopped (2026-10-19, evening)
- **Only `pobierz` running in the background** (the user's decision 2026-10-19: download first, `przetworz` the next day). At 19:34: file 27 of 46, ~1.36M records in raw_records, 0 rejected; ~50k rows per file (so more than the ~1.82M estimated - count when done); ~5 min per file when the PC is busy. Judgment tables still 50,299 (file 0 + part of 1).
- **Tomorrow:** `python -m nsa przetworz` (~8 min per 50k, several hours, resumable), then statistics are refreshed by it.
- MCP (`python -m nsa mcp`) and the page already work on the 50,299; full set after przetworz, meaning search after `wektory`.
- Stop/resume: Ctrl+C or Stop-Process on the python process; resume with the same commands (cursor `juddges:cursor` = {"file", "row"}, processing by changed_at). Closing Docker Desktop stops it too.
- **NSA vectors not started** (only the 5-judgment trial): the GPU is busy with ETPC. Start after ETPC: `python -m nsa wektory` (resolutions and judgments with a thesis first; ~15M chunks for everything - decide later how far to go).
- **Next:** check the imported data (counts per year 2023-2025, the gap after 2025-03), the NSA page on 8082 with the full set (FULLTEXT speed on ~1.8M judgments, buffer pool), then the gap after 2025-03 (ask the JuDDGES authors / NSA).

## RAG (2026-10-19)
- `nsa/rag.py` (retriever, potomek `Retriever`), narzędzie MCP `fragmenty`, `python -m nsa fragmenty "pytanie"`, `python -m nsa ocen` na `nsa/pytania.json`. Pierwszy pomiar: NSA 6 pytań o uchwały: MRR 0.667, 4 z 6 w kontekście (wektory tylko 5). Plan i następne kroki: główny [`../notepad.md`](../notepad.md), sekcja RAG.
