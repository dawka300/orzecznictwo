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

## Core = parents, projects = children (2026-10-19)
- Rule (also in CLAUDE.md): a project that needs a core class to behave differently gets a child class in its own package; no project cases in core files.
- **Stage 1 done:** `judgments_core/transport.py` = strategy pattern for HTTP: `Transport` (ABC) with `RequestsTransport`, `CurlTransport`, `PlaywrightTransport` (`browser.py`), and `FallbackTransport` (chain: the next strategy takes over after 2 Cloudflare challenges in a row, the request is repeated at once, event `transport` in fetch_events; transports created only when reached). `PoliteClient(transport=...)` keeps pauses/retries/events. `Source` is an ABC (`HudocSource`, `JuddgesSource`, `CbosaSource` are children). `Project(source=..., importer=..., transports=(...))`; `EtpcImporter`, `NsaImporter` children of `Importer` (empty for now - the place for project-specific import behaviour). ETPC chain: requests -> curl -> playwright (live check: switched twice, ~1.5 min overhead at the start); `ETPC_TRANSPORT=playwright` in .env skips the chain; the setting also takes a comma list (`curl,playwright`).
- **Stage 2 done:** `judgments_core/parser.py` `Parser` (ABC: load() JSON by default, parse()) -> `HudocParser`, `JuddgesParser`; `judgments_core/tables.py` `TableWriter` (ABC: SAVEPOINT per record, blad_parsera problems, start/write/finish hooks, insert_rows) -> `EtpcTables` (finish = rebuild touched rulings), `NsaTables`; `Deriver` has class attributes `source_name`, `writer_class` and the hook `after(cur)` -> `EtpcDeriver` (after = stats + ranks), `NsaDeriver`. `Project(deriver=...)` replaces `derive` + `after_derive`; `przetworz` no longer builds a source (and a transport) just for its name.
- **Stage 3 done:** `judgments_core/chunker.py` `Chunker` (packing, split_long, merge_short, context line, embedding_input; abstract blocks(), hook relabel()) -> `EtpcChunker` (EN/FR sections and headings; identical output on 3,000 documents: 43,189 chunks). `judgments_core/embed.py` `EmbedJob` (groups, embedding, Qdrant, tracking table, orphans, budget, `Progress`/`read_progress(cur, key)`; SQL in `db.py` `vectors_*`, `read_state`) -> `EtpcEmbedJob` (candidates SQL, load() with payload, tables ruling_vectors/rulings). `judgments_core/semantic.py` `SemanticSearch` (+ `Passage`, `SemanticUnavailable`) -> `EtpcSemantic` (payload_filters). `judgments_core/web.py` `WebApp` (favicon, connect(), 500 + log) -> `EtpcWebApp`. `Project(embed_job=..., web=...)`.
- Map of parents -> children: Transport -> Requests/Curl/Playwright/Fallback; Embedder -> HttpEmbedder/LocalEmbedder/FallbackEmbedder; Source -> HudocSource, JuddgesSource, CbosaSource; Importer -> EtpcImporter, NsaImporter; Parser -> HudocParser, JuddgesParser; TableWriter -> EtpcTables, NsaTables; Deriver -> EtpcDeriver, NsaDeriver; Chunker -> EtpcChunker, NsaChunker; EmbedJob -> EtpcEmbedJob, NsaEmbedJob; SemanticSearch -> EtpcSemantic, NsaSemantic; WebApp -> EtpcWebApp, NsaWebApp; McpServer -> EtpcMcp, NsaMcp; Retriever -> EtpcRetriever, NsaRetriever (2026-10-19, RAG niżej). Also common in the core now: `rrf()` (semantic.py), page look and helpers (`layout`, CSS, number/date_pl/highlight/text_html/vectors_card ... in web.py), `CollectionMissing` (no vectors yet -> a clear notice).
- **Query vectors without LM Studio (2026-10-19):** `judgments_core/embeddings.py` strategies like the transport: `HttpEmbedder` (LM Studio, used by `wektory` for the bulk), `LocalEmbedder` (sentence-transformers BAAI/bge-m3 in the process, CPU, loads ~15 s, ~1-2 GB RAM, lazily + `SemanticSearch.prepare_in_background()`), `FallbackEmbedder`; `make_embedder(settings, names)`; settings `query_embedders` (default `local,http`), `local_model`. Same results as LM Studio (cos 0.99986, identical top 10). The www image has torch CPU + sentence-transformers (1.43 GB), model cache in the compose volume `hfcache`; semantic query on the page ~1 s.
- **MCP (2026-10-19):** `judgments_core/mcp.py` `McpServer` (MCP SDK 2.x `MCPServer`, stdio, log on stderr; `cut()` gives long texts in parts with `od=`) -> `EtpcMcp`, `NsaMcp` (tools `szukaj` with the page filters, `orzeczenie`, `podobne`); `python -m etpc|nsa mcp`, `.mcp.json` in the repo root registers both for Claude Code. Search `_run` now also treats MariaDB error 188 (FULLTEXT aborted under load) as a timeout.
- **State 2026-10-19 evening:** both projects run on the parent/child structure; pages: ETPC http://127.0.0.1:8080, NSA http://127.0.0.1:8082 (compose services www, www-nsa). Background: ETPC `wektory`, NSA full import. 108 tests pass.

## Long jobs and Claude (2026-10-19, evening)
- Running now as background tasks of the Claude Code session: `python -m nsa pobierz` (file ~27 of 46; `przetworz` tomorrow) and `python -m etpc wektory` (4,520 / 82,976 rulings, 245k chunks at 19:57 local; ~15 h left at ~24k chars/s, old code in memory).
- They use no Claude tokens (local Python, docker, LM Studio); tokens only for chatting/checking, a short check when a task ends, and MCP tool results. Closing Claude Code probably stops them - both resume. To run them independent of Claude, start them in an own terminal: `.venv\Scripts\python.exe -m nsa pobierz`, `.venv\Scripts\python.exe -m etpc wektory`.

## RAG: plan i krok 1 (2026-10-19, wieczór)
Cel: RAG nie tylko przez MCP (Claude sam szuka i czyta), ale też dla **modeli lokalnych** (LM Studio na RX 6750 XT, 12 GB VRAM). Mały model słabo woła narzędzia wiele razy i ma mały kontekst (16-32k), więc dla niego program sam wybiera fragmenty, pakuje je w limit i woła model raz.

**Architektura: jeden retriever, trzech odbiorców**
- `Retriever` (rdzeń, `judgments_core/rag.py`) -> `EtpcRetriever` (`etpc/rag.py`), `NsaRetriever` (`nsa/rag.py`). Kolejno: wyszukiwarka projektu (hybryda słowa + znaczenie, filtry jak na stronie) układa orzeczenia -> dla pierwszych 20 Qdrant daje do 3 fragmentów najbliższych pytaniu (`SemanticSearch.passages_of`, `QdrantStore.search_judgments(per_judgment=...)`, filtr `judgment_id`); orzeczenie bez wektorów = okno 1500 zn. przy pierwszym słowie zapytania (albo od początku tekstu) -> każdy fragment poszerzony o 400 zn. z obu stron (do końca wiersza, inaczej do całych słów), nakładające się łączone -> pakowanie rundami (najlepszy fragment każdego orzeczenia po kolei, potem drugi ...) do `budget_chars` (16 000 zn. ~ 4-5k tokenów). Wynik `Retrieval` -> `render()`: `[n] sprawa (id X)`, dane, link HUDOC/CBOSA, pod spodem fragmenty z nagłówkiem i `znaki od-do` (te same przesunięcia co `orzeczenie(id, od=...)` w MCP).
- Odbiorcy: (1) MCP - nowe narzędzie **`fragmenty`** w obu serwerach (pytanie + główne filtry + `limit_znakow`); przydaje się Claude'owi (mniej tokenów niż `orzeczenie`) i modelom w LM Studio, które od 0.3.17 obsługuje serwery MCP (`mcp.json`); (2) `RagPipeline` + `ChatModel` (krok 3) - jedno wywołanie modelu lokalnego; (3) strona `/zapytaj` i endpoint OpenAI-compatible (krok 4).
- Polecenia: `python -m etpc|nsa fragmenty "pytanie" [--limit-znakow N]` (podgląd kontekstu), `python -m etpc|nsa ocen [--plik] [--limit-znakow]` (pytania wzorcowe). `Project(retriever=...)`; `Retriever.from_settings` / `SemanticSearch.from_settings` (model zapytań + kolekcja Qdranta); `SemanticSearch.query_vector` pamięta 32 ostatnie wektory zapytań (wyszukiwarka i fragmenty pytają o ten sam tekst).
- **Ewaluacja** (`judgments_core/evaluation.py`): `<projekt>/pytania.json` = `[{"pytanie", "oczekiwane": [klucze], "filtry": {pola SearchQuery}}]`; klucze: ETPC numer skargi (każde orzeczenie z tą skargą się liczy), NSA sygnatura. Mierzy miejsce w rankingu i czy orzeczenie weszło do kontekstu: pierwsze miejsce, top 5, MRR, % oczekiwanych w kontekście. Bez modelu językowego: tanio i powtarzalnie - baza do strojenia. Testy `tests/test_rag.py`; całość 120 testów przechodzi.

**Pierwszy pomiar (2026-10-19 20:10, w tle `etpc wektory` i `nsa pobierz`)**
- ETPC, 12 pytań (wektory ma ~5k z 83k orzeczeń, najważniejsze): pierwsze miejsce 58%, top 5 83%, MRR 0.670, w kontekście 17 z 23 (74%). Pudła: Kauczor i Kudła przy „zbyt długim areszcie” (bez wektorów), Advance Pharma i Xero Flor za kontekstem (#20, #10).
- NSA, 6 pytań o uchwały (50k orzeczeń, wektory tylko 5): pierwsze miejsce 67%, MRR 0.667, 4 z 6 w kontekście; FPK 2/02 i FPS 14/00 (stare uchwały) nie znalezione.
- **Czas:** ETPC średnio 61 s na pytanie, najgorsze 268 s. Długie polskie pytania -> glosariusz daje duże grupy OR -> FULLTEXT przy obciążonej bazie wisi w stanie „Fulltext initialization”, którego `max_statement_time` (10 s w hybrydzie) nie przerywa; tryb kończy jako „znaczeniowe”. Krótkie pytania 3-17 s. NSA 0.3-30 s. Do naprawy przed modelem lokalnym (niżej).

**Modele czatu do wypróbowania w LM Studio** (12 GB VRAM; bge-m3 ~1.2 GB obok; sprawdzić najnowsze wersje w LM Studio):
- ETPC (źródła EN/FR -> odpowiedź po polsku): **Qwen3-14B Q4_K_M** (~9 GB, dobry wielojęzycznie, `/no_think` dla szybkości) albo **Gemma 3 12B QAT Q4_0** (~8 GB, dobra polszczyzna, długi kontekst).
- NSA (polskie teksty prawnicze): **Bielik-11B v2.x Instruct Q4_K_M** (~7 GB, polski model) albo PLLuM; porównać z Qwen3-14B.
- Do MCP w LM Studio (model sam woła narzędzia): Qwen3-30B-A3B (MoE, ~18 GB w Q4 - część warstw na CPU, 3B aktywnych, więc dalej szybki).
- Kontekst modelu ustawić 16-32k; `limit_znakow` dobrać do niego (16k zn. ~ 4-5k tokenów EN, polski tekst więcej tokenów na znak).
- Karta jest zajęta przez `wektory` jeszcze wiele godzin: model czatu testować po ETPC albo w przerwie (`wektory` się wznawia).

**Następne kroki**
1. ~~Retriever + `fragmenty` (MCP) + `ocen`~~ zrobione (wyżej).
2. ~~**Czas pytania**~~ zrobione (2026-10-19 ~21:00, niżej „Czas pytania RAG”).
3. **Reranker** `LocalReranker` (`BAAI/bge-reranker-v2-m3` przez `sentence_transformers.CrossEncoder`, CPU, ~50 kandydatów -> 8-12), strategia jak `Embedder` (`NoReranker` domyślnie na hostingu); zysk mierzyć `ocen`.
4. `ChatModel` (`HttpChat`: OpenAI-compatible `/v1/chat/completions`, tylko requests; `FallbackChat`; ustawienia `<PREFIKS>_CHAT_URL`, `<PREFIKS>_CHAT_MODEL`) + `RagPipeline` (prompt po polsku: tylko ze źródeł, każde twierdzenie z [n], „źródła nie wystarczają” gdy brak; klasa potomna: ETPC tłumaczy z EN/FR, NSA sygnatura/sąd; kontrola numerów [n]) + polecenie `zapytaj`.
5. Strona `/zapytaj` (SSE) na 8080/8082, potem endpoint OpenAI-compatible (Open WebUI / Jan / AnythingLLM widzą „model” `etpc-rag`).
6. Pytania wzorcowe rozbudowywać przy każdym ciekawym wyszukiwaniu (`etpc/orzeczenia.txt`), NSA po pełnym `przetworz` + wektorach.

## Wektory: rozmiar batcha (2026-10-19, wieczór)
- Pytanie użytkownika: `wektory` liczy z batchem 32 (`<PREFIKS>_EMBED_BATCH`, domyślnie 32), a LM Studio zajmuje tylko ~2.6 GB z 12 GB VRAM - czy batch 64/128 przyspieszy?
- Pętla w `EmbedJob.run` jest szeregowa: grupa ~256 fragmentów -> żądania po `batch_size` jedno po drugim -> zapis do Qdranta i MariaDB; w tym czasie karta stoi.
- **Pomiar** (skrypt w scratchpadzie sesji, niezachowany: 512 prawdziwych fragmentów ETPC ENG, 756k znaków, te same teksty dla każdego ustawienia; **szło równolegle z działającym `etpc wektory`**, bo zatrzymanie go zostało zablokowane - liczby względne, nie absolutne):

  | batch | równolegle | czas | ~tok/s |
  |---|---|---|---|
  | 16 | 1 | 81.5 s | 2,319 |
  | 32 | 1 | 54.5 s | 3,467 |
  | 64 | 1 | 41.0 s | 4,607 |
  | 128 | 1 | 32.9 s | 5,748 |
  | 32 | 2 | 41.0 s | 4,607 |
  | 64 | 2 | 33.4 s | 5,667 |
  | 32 (powtórka) | 1 | 54.4 s | 3,477 |

- ~~Wniosek wstępny: większy batch pomaga~~ - **błędny**: zysk brał się z zabierania karty działającemu `wektory`.
- **Czysty pomiar** (`wektory` zatrzymane, tylko `nsa pobierz` w tle; 512 fragmentów / 189k tokenów, potem 1024 / 384k):

  | batch | równolegle | ~tok/s |
  |---|---|---|
  | 32 | 1 | 6,955 / 6,996 (powtórka) |
  | 64 | 1 | 6,928 |
  | 128 | 1 | 7,008 |
  | 256 | 1 | 6,933 / 6,996 |
  | 512 | 1 | 7,025 |
  | 1024 | 1 | 6,937 |
  | 64 / 128 | 2 | 7,030 / 7,008 |

- **Wniosek:** batch żądania i równoległość nie zmieniają nic - karta jest ograniczona mocą obliczeniową (bge-m3 F16), nie pamięcią; 2.6 GB VRAM to po prostu mały model. `wektory` w logu robiło ~6,600 tok/s = ~95% tego maksimum, więc zapis do Qdranta/MariaDB kosztuje tylko ~5% (pipelining w `EmbedJob` dałby najwyżej tyle). `ETPC_EMBED_BATCH` zostaje 32.
- **Co może realnie przyspieszyć (do sprawdzenia):** (1) bge-m3 w **Q8_0** zamiast F16 (na AMD często 1.3-1.8x; sprawdzić jakość: cos z F16 ~0.999, `ocen` bez zmian - uwaga: nowy model = nowa kolekcja, wektory nie mieszają się z F16, więc decyzja przed dalszym liczeniem albo liczenie od nowa); (2) silnik LM Studio **ROCm vs Vulkan** (Settings -> Runtime) na RX 6750 XT; (3) flash attention w ustawieniach modelu; (4) zakres: najpierw wyroki, decyzje później.

## Czas pytania RAG (2026-10-19, ~21:00)
- Pomiar części hybrydy osobno (pytania wzorcowe ETPC, `wektory` zatrzymane, `nsa pobierz` w tle): znaczeniowe 0.8-7.7 s; FULLTEXT 0.1-12 s, ale **124 s** (pytanie o pełnomocnika i kasację: 4 wymagane grupy ze słowniczka - "legal aid", "cassation", "mandatory legal representation", "right to a court" - każda w dziesiątkach tysięcy dokumentów) i **67 s** (przewlekłość + skuteczny środek). `max_statement_time` tego nie przerywa (stan „Fulltext initialization”). Bez frazy ze słowniczka FULLTEXT wymaga wszystkich polskich słów w angielskich tekstach -> 0 wyników („klimat”, „samobójstwo”).
- `ocen` w trybie samym znaczeniowym: ETPC MRR 0.668 (hybryda 0.670), w kontekście 17 z 23 (to samo), **średnio 1.8-2.7 s zamiast 61 s**. NSA bez zmian, ale niemiarodajne: 5 policzonych wektorów NSA to akurat uchwały z pytań.
- Zmiana: `Retriever.mode` (tryb wyszukiwarki dla fragmentów, filtr `mode` w zapytaniu wygrywa): **ETPC `znaczeniowe`**, **NSA hybrydowe** (do czasu wektorów NSA FULLTEXT jest niezbędny i w NSA był szybki: 0.3-30 s). Narzędzie MCP `fragmenty` ma parametr `tryb`.
- Otwarte (dotyczy też strony www i MCP `szukaj`): limit czasu FULLTEXT w hybrydzie nie działa w fazie inicjalizacji -> ewentualnie termin po stronie klienta (zapytanie w osobnym połączeniu/wątku, po N s porzucone + KILL QUERY) albo w słowniczku pomijać grupy bardzo częstych fraz. Orzeczenia bez wektorów (Kauczor, Antonicelli) znajdzie dopiero dokończone `wektory`.
