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
3. ~~**Reranker**~~ zrobiony, domyślnie wyłączony (niżej „Reranker i ChatModel”).
4. ~~`ChatModel` + `RagPipeline` + `zapytaj`~~ zrobione; brak jeszcze próby z prawdziwym modelem czatu (niżej).
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

## Reranker i ChatModel (2026-10-19, ~21:20)
- **Reranker** `judgments_core/rerank.py`: `Reranker` (ABC) -> `LocalReranker` (sentence-transformers `CrossEncoder`, `BAAI/bge-reranker-v2-m3`, CPU, max 512 tokenów pary, model ~2.3 GB w cache HF, ładuje się 15-30 s, w tle). Ustawienia `<PREFIKS>_RERANKER` ("" = brak, domyślnie; `local`), `<PREFIKS>_RERANK_MODEL`. W `Retriever`: ocenia każdy fragment kandydata (sam fragment bez poszerzenia, `_Candidate.core`), orzeczenia wg najlepszego fragmentu, fragmenty w orzeczeniu wg oceny; błąd modelu -> kolejność wyszukiwarki + uwaga.
- **Pomiar `ocen` z rerankerem:** ETPC MRR 0.688 (bez: 0.668), w kontekście 17 z 23 (bez zmian): Kudła #9 -> #2 (wchodzi do kontekstu), Staroszczyk #1, ale Reczkowicz i Dolińska-Ficek wypadły z kontekstu; **~49 s na pytanie** (60 par na CPU, `nsa pobierz` w tle). NSA bez zmian, 13-45 s. -> **wyłączony domyślnie.** Jeśli wrócić: oceniać tylko najlepszy fragment każdego orzeczenia (20 par zamiast 60), krótsze `MAX_TOKENS`, albo GPU (torch tu tylko CPU; karta AMD na Windows).
- **ChatModel** `judgments_core/chat.py`: `ChatModel` (ABC: `stream`, `complete`) -> `HttpChat` (OpenAI-compatible `/v1/chat/completions`, strumień SSE, tylko requests; model pusty = pierwszy wczytany model czatu z `/v1/models`, modele `*embed*` pomijane); `without_thinking()` usuwa `<think>...</think>` (Qwen3). Ustawienia `<PREFIKS>_CHAT_URL` (LM Studio), `_CHAT_MODEL` ("" = wczytany), `_CHAT_TEMPERATURE` 0.2, `_CHAT_MAX_TOKENS` 2000, `_CHAT_TIMEOUT_S`. FallbackChat pominięty, dopóki jest jeden serwer.
- **RagPipeline** `judgments_core/pipeline.py` -> `EtpcRag` (`etpc/pipeline.py`: źródła EN/FR, odpowiedź po polsku, nazwy spraw w oryginale, naruszenie vs brak naruszenia), `NsaRag` (`nsa/pipeline.py`: sygnatura/sąd/data, waga uchwał, rozbieżne linie). Prompt: system = dziedzina projektu + wspólne zasady (tylko ze źródeł, [n] przy każdym twierdzeniu, „źródła nie wystarczają”, nic nie wymyślać, najpierw krótka odpowiedź); user = `render()` źródeł + pytanie. `check_citations()`: numery [n] użyte / bez źródła (zmyślone -> UWAGA pod odpowiedzią). `Project(rag=...)`.
- **`python -m etpc|nsa zapytaj "pytanie" [--limit-znakow N] [--tylko-zrodla]`**: odpowiedź strumieniem, potem lista źródeł (nieużyte oznaczone) i ostrzeżenie o zmyślonych numerach; `--tylko-zrodla` = pokaż prompt bez modelu. Sprawdzone na żywo: prompt OK; bez modelu czatu czytelny błąd („wczytaj go w LM Studio albo ustaw <PREFIKS>_CHAT_MODEL”). Testy `tests/test_chat.py`.
- Zauważone: dla pytania o kasację najlepszy fragment wyroku Kulikowski to koniec zdania odrębnego (opinie sędziów w kontekście bywają słabe) - do rozważenia: w `EtpcRetriever` niższy priorytet części `opinia` albo pomijanie jej w fragmentach.
- **Następne:** model czatu w LM Studio (propozycja Qwen3-14B Q4_K_M, kontekst 16k; karta liczy też `wektory`) -> pierwsze odpowiedzi na pytaniach wzorcowych, ocena ręczna (czy cytuje poprawnie, czy nie zmyśla); potem strona `/zapytaj` i endpoint OpenAI-compatible.

## Wektory NSA: skala i liczenie na kilku komputerach (2026-10-19, ~22:00)
- **Skala** (próbka 50k przetworzonych x45 = 2.25 mln orzeczeń): całość ~30 mld znaków, ~18 mln fragmentów -> na RX 6750 XT **3-6 tygodni**; Qdrant w int8 ~18 GB RAM (za dużo przy ~16 GB Dockera). Postanowienia = 46% orzeczeń, 17% tekstu (głównie formalne). „Sąd zważył, co następuje” jest w 96% wyroków; część od tego zwrotu (rozważania prawne) = ~40% tekstu wyroków (~12 mld znaków, ~7 mln fragmentów).
- **Propozycja zakresu** (do decyzji, nie zrobione): poziom 1 = jeden wektor na każde orzeczenie (teza + sentencja + początek „zważył”, ~2000 zn.; ~4.5 mld znaków, 2.25 mln wektorów, ~2.3 GB RAM); poziom 2 = pełne fragmenty „od zważył” dla uchwał, wyroków NSA i orzeczeń z tezą (~1.5 mln fragmentów); wyroki WSA później.
- **Zdalnie:** wynajęty GPU (RunPod/Vast, RTX 4090 ~$0.3-0.7/h) z tym samym bge-m3 (TEI, endpoint zgodny z OpenAI) - zgodne wektory, całość ~1-3 dni za ~$20-60 (szacunek); API embeddingów (OpenAI/Voyage/Cohere/Google) odradzone: inny model = zapytania też przez API, uzależnienie, drożej. Wybrane: **koledzy z kartami** (jeden ma RTX 5070 Ti 16 GB), liczenie w dockerze.
- **Paczki zrobione** (`judgments_core/packages.py`, migracja `core/003_packages.sql`: `vector_packages`, `vector_package_items`):
  - `python -m <projekt> paczki [--ile N] [--fragmentow 200000] [--od-konca]` -> `<projekt>/database/paczki/<nazwa>.jsonl.gz` (teksty do policzenia, ~0.37 KB na fragment gz -> 200k fragmentów ~80 MB) + `<nazwa>.meta.jsonl.gz` (zostaje: id, dokument, sha256, fragmenty z payloadem, 32 wektory kontrolne liczone tutaj modelem zapytań na CPU). Orzeczenia otwartej paczki pomija zwykłe `wektory` (`EmbedJob.run`), więc dzielimy: Ty liczysz kolejkę od początku, koledzy paczki (np. `--od-konca`).
  - `docker/licz/` dla kolegów: `licz_paczke.py` (samodzielny, numpy + sentence-transformers albo requests; silniki `cuda` fp16 / `cpu` / `http` = LM Studio; wznawia się częściami po 4096 fragmentów; `--limit N` = test szybkości), `Dockerfile` (python:3.12-slim + torch cu128 - obsługuje RTX 50xx, sterownik >= 570; sentence-transformers 6.1.0, transformers 5.20.1 jak lokalnie), `compose.yml` (GPU nvidia, wolumen modelu), `README.md` (instrukcja po polsku).
  - `python -m <projekt> paczka-import <nazwa>`: kontrola (cosinus wektorów kontrolnych >= 0.99, inaczej odrzuca: inny model/kwantyzacja; liczba wierszy = liczba fragmentów), orzeczenia ze zmienionym tekstem pominięte (policzy je `wektory`), reszta zapisana jak w `wektory` (Qdrant + tabela śledzenia). `paczki --lista`, `paczki --zwolnij <nazwa>` (porzucona paczka wraca do kolejki).
  - **Sprawdzone na żywo:** paczka testowa `nsa-0001` (105 orzeczeń, 2,152 fragmentów, 3 mln znaków, 0.8 MB) policzona skryptem przez LM Studio (~4.2k tok/s, ~13.8k znaków/s, karta dzielona z ETPC) i zaimportowana: kontrola 0.99971 (CPU sentence-transformers vs LM Studio GGUF). **Obraz dockera nie był budowany ani uruchamiany** (tu karta AMD) - pierwszy test u kolegi: `docker compose build`, potem `--limit 2000`.
- **Następne:** `nsa przetworz` (wszystkie 2.25 mln do tabel, ~6 h), decyzja o zakresie (poziomy 1/2), paczka testowa dla kolegi z 5070 Ti (szybkość), potem podział kolejki.
- **Vast.ai (2026-10-19, ~22:10):** instrukcja `docker/licz/VAST.md` (konto, klucz API i SSH, `vastai search offers` z filtrami `cuda_vers>=12.8`, opłaty za transfer `inet_up_cost`/`inet_down_cost`, `create instance` z obrazem `pytorch/pytorch:2.11.0-cuda12.8-cudnn9-runtime` - ostatni tag z CUDA 12.8 - `scp` paczek, `vast_start.sh test` = 5000 fragmentów i tokenów/s, `vast_start.sh` = wszystkie paczki po kolei w tle, log `/workspace/licz.log`, pobranie `*.wektory.npy`, `paczka-import`, **`destroy instance`** - `stop` dalej liczy za dysk). `docker/licz/vast_start.sh` sprawdzony lokalnie na CPU (test, pętla, pomijanie policzonych); na Vast.ai nie uruchamiany (wymaga konta i kredytu). `.gitattributes`: `*.sh` z LF (bash na Linuksie).

## Pierwsze odpowiedzi modelu lokalnego (2026-10-19, ~23:00-23:40)
- LM Studio: `qwen3-14b` (Q4_K_M, kontekst 8192, wczytany w zakładce czatu - serwer widzi te same modele) obok bge-m3. `zapytaj` / skrypt pytań wzorcowych ETPC 1, 4, 11, 12.
- Błąd znaleziony i poprawiony: `requests.iter_lines(decode_unicode=True)` dzieli też na U+2028/U+0085 w treści -> ucięty JSON strumienia; teraz bajty, potem UTF-8 (`judgments_core/chat.py`, test).
- **Runda 1** (przed poprawkami): wyszukiwanie trafia (oczekiwane sprawy w źródłach), numery [n] zawsze istniejące; błędy merytoryczne: Guz v. Poland jako „sankcja uzasadniona” (HUDOC: naruszenie art. 10), Szafrański jako naruszenie art. 3 (HUDOC: brak naruszenia art. 3, naruszenie art. 8), „KlimaSeniorinnen … przeciwko Szwecji” (Szwajcaria), wewnętrzne id w tekście, literówki w nazwach, zdanie odrębne Bonello zamiast uzasadnienia Kulikowskiego.
- **Poprawki:** (1) `render(..., ids=False)` dla modelu lokalnego (MCP dalej z id); (2) ETPC: pole `WYNIK:` = wnioski HUDOC bez zadośćuczynień/kosztów (`etpc.rag.outcome`), NSA: `WYNIK:` = treść wyniku; zasady w prompcie: o wyniku tylko z pola WYNIK, nazwy spraw/państw/sądów dokładnie ze źródła; (3) `Retriever.last_parts` - ETPC `("opinia",)`: zdania odrębne na koniec kolejki fragmentów orzeczenia; (4) ustawienie `<PREFIKS>_CHAT_SUFFIX` (np. `/no_think` dla Qwen3) dopisywane po pytaniu.
- **Runda 2:** z myśleniem 84-113 s do pierwszego tekstu, 109-149 s całość; Szwajcaria i Szafrański już dobrze, **Guz nadal źle** (model „przemyślał” wynik wbrew polu WYNIK). **`/no_think`: 55-75 s / 70-113 s, Guz dobrze (naruszenie art. 10), Morawiec/Kozan/Kunstelj z poprawnymi wynikami**, przeludnienie czysto; zostają drobne ozdobniki (Guz „w mediach społecznościowych”) i słownictwo („prawo humanitarne”, „dobra wiarygodność” = due diligence). -> zalecane `ETPC_CHAT_SUFFIX=/no_think` dla Qwen3.
- **Szybkość:** nawet bez myślenia pierwsze słowo po ~1 min = wczytanie ~5k tokenów promptu ~100 tok/s - za wolno jak na 14B na karcie: sprawdzić GPU Offload (model wczytany w czacie z ustawieniami domyślnymi?) i czy `wektory` dzieli kartę. Następna próba: model ~27B częściowo na karcie (użytkownik pobiera), z `/no_think`, `ETPC_CHAT_MODEL` wprost, gdy wczytane są dwa modele czatu.
- Wniosek ogólny: odpowiedź 14B = szkic ze źródłami, nie zastępuje lektury; lista źródeł z linkami pod odpowiedzią jest kluczowa.

## Automatyczna ocena odpowiedzi (2026-10-19, ~23:50)
- `python -m etpc|nsa ocen-odpowiedzi [--plik] [--pytania 1,4,11] [--bez-sedziego] [--limit-znakow N]` (`judgments_core/answer_eval.py`): pytania wzorcowe przez cały RAG, potem trzy poziomy sprawdzania:
  1. **cytowania**: numery [n] bez źródła;
  2. **reguła wyniku (bez modelu)**: zdanie z dokładnie jednym źródłem [n], w którym pada wynik, porównane z `Reference.result` (ETPC: wnioski HUDOC przez `etpc.rag.outcome`; NSA: treść wyniku CBOSA). Słowa wyniku są w projekcie: `RagPipeline.outcomes` = [(etykieta, regex w odpowiedzi, regex w metadanych)]; ETPC „brak naruszenia” (brak/bez naruszenia, nie stwierdził/nie doszło do naruszenia, nie naruszył, niedopuszczalna) przed „naruszenie” (stwierdził naruszenie, doszło do naruszenia, naruszył, naruszono); NSA oddalenie/uchylenie/odrzucenie/umorzenie po rdzeniach. Etykieta, której nie ma w wyniku źródła = „możliwa sprzeczność” (zdanie może opisywać rozstrzygnięcie niższej instancji). Wynik spoza słownika = bez oceny. Dzielenie na zdania omija skróty (v., art., ust., nr, pkt ...) i inicjały (R.R.);
  3. **model-sędzia**: dostaje tylko źródła cytowane w odpowiedzi + odpowiedź, zwraca listę JSON ocen zdań z [n]: poparte / niepoparte / sprzeczne + dlaczego. Ustawienia `<PREFIKS>_JUDGE_URL`, `_JUDGE_MODEL`, `_JUDGE_API_KEY` (puste = model czatu z LM Studio; API zgodne z OpenAI, nagłówek Bearer), temperatura 0, `chat_suffix` (np. /no_think) dopisywany także sędziemu.
- Wynik: zestawienie (średni czas, zmyślone cytowania, sprzeczności reguły, % ocen sędziego) + plik `<projekt>/database/odpowiedzi/<UTC>-<model>.json` (odpowiedzi, źródła z wynikami, oceny) do porównań modeli/ustawień na tych samych pytaniach.
- `Reference.result` / `Retriever.result(hit)` - wynik sprawy z metadanych przy każdym źródle.
- Zastrzeżenie: sędzia = ten sam 14B ocenia sam siebie (przepuści część błędów); reguła pewniejsza, ale wąska. Lepszy sędzia: większy model (27B) albo API.
- Uruchomione: pełna ocena 12 pytań ETPC, qwen3-14b, `/no_think` (wynik do dopisania) - punkt odniesienia dla modelu ~27B, który użytkownik pobiera.
- Testy: 139.
- **Wynik odniesienia (2026-10-20 00:14), qwen3-14b, `/no_think`, sędzia = ten sam model, 12 pytań ETPC:** średnio 80 s na odpowiedź (62-143 s), nieistniejące cytowania 0, sprzeczności wyniku (reguła) 0, sędzia: poparte 70 (86%), niepoparte 8 (10%), sprzeczne 3 (4%). Słabość modelu: uogólnienia ponad źródło (#2: „skuteczność ustawy z 2004 r. ograniczona”, #12: dopowiedziane uzasadnienie Guz); 7 z 12 odpowiedzi bez uwag. Sędzia 14B sam się myli (#2: „źródło [1] nie dotyczy Kudły”, #6: sformułowanie uznane za sprzeczność) - liczby sędziego = przybliżenie, do porównań na tych samych pytaniach. Plik: `etpc/database/odpowiedzi/20261019-221359-qwen3-14b.json`. Pierwsza próba tej oceny przerwana o 23:43 (modele wyładowane w LM Studio) -> poprawka: błąd modelu zapisany przy pytaniu, dwa z rzędu = koniec z zapisem.

## RAG w czacie LM Studio i w Claude (2026-10-20)
- **LM Studio:** serwery MCP `etpc` i `nsa` wpisane do `C:\Users\dawaw\.cache\lm-studio\mcp.json` (python z `.venv`, `-m etpc|nsa mcp`, env `PYTHONPATH` = repozytorium - LM Studio uruchamia z innego katalogu; `PYTHONIOENCODING=utf-8`; `<PREFIKS>_LOG_DIR` = `logs` w repo). Sprawdzone: z innego katalogu oba serwery mają 4 narzędzia. W czacie: włączyć narzędzia, kontekst 16384 (K/V cache Q8_0), prompt systemowy: najpierw `fragmenty` z `limit_znakow=8000`, potem `orzeczenie`, odpowiedź tylko z narzędzi, z nazwą/sygnaturą/linkiem, `/no_think`. Tu nie ma kontroli cytowań `zapytaj` - model sam decyduje o narzędziach.
- **Claude Code** (ta aplikacja): `.mcp.json` w repo rejestruje oba serwery; włączyć przez `/mcp`.
- **Claude Desktop - zwykły czat:** wersja ze Sklepu, plik `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json` (teraz tylko `preferences`); dopisać `mcpServers` przez Settings -> Developer -> Edit Config i zrestartować aplikację (restart zamyka też sesje Claude Code; aplikacja nadpisuje plik preferencjami, więc nie edytować go przy działającej aplikacji).
- **claude.ai w przeglądarce i wtyczki Office (Word, Excel, PowerPoint, Outlook):** tylko zdalne serwery MCP (custom connector z adresem URL, wspólne dla konta i wtyczek), łączone z chmury Anthropic -> potrzebny tryb HTTP serwera (streamable HTTP w MCP SDK) + publiczny HTTPS (tunel Cloudflare/ngrok albo VPS) + zabezpieczenie (tajny token / OAuth). Nie zrobione; pomysł: w Wordzie „dopisz akapit z orzecznictwem ETPC o ...”.

## Model qwen/qwen3.8-27b (2026-10-20, ~00:30)
- Wczytany w LM Studio jako `qwen/qwen3.8-27b`, Q4_K_M, kontekst 8192, częściowo na karcie (12 GB) - reszta warstw na CPU, wolne wczytywanie promptu.
- **Pierwsza próba (pytanie 12): 450 s i pusta odpowiedź.** Przyczyna: model **ignoruje `/no_think`** i myśli; LM Studio odsyła myślenie osobno (`reasoning_content`, `usage.completion_tokens_details.reasoning_tokens`), a tokeny myślenia liczą się do `max_tokens` -> przy ~5k tokenów źródeł myślenie zjadło całe 2000 tokenów. Sprawdzone na „2+2”: `chat_template_kwargs.enable_thinking=false` i `reasoning: {effort: none}` nie działają, **`reasoning_effort: "none"` działa (0 tokenów myślenia)**, `low` ogranicza.
- Zmiany: ustawienie `<PREFIKS>_CHAT_REASONING_EFFORT` (wysyłane tylko gdy ustawione; sędzia dostaje to samo, chyba że ma własny `JUDGE_URL`); `HttpChat` zgłasza `ChatError` „model zużył limit tokenów, zanim zaczął odpowiadać”, gdy nie było treści, a `finish_reason=length`; nazwa pliku wyników bez `/` z nazwy modelu (wcześniej powstawał podkatalog). Zapis ustawień w pliku wyników: suffix, reasoning_effort, temperatura, limit znaków. Testy: 141.
- Ustawienia per model: Qwen3-14B -> `ETPC_CHAT_SUFFIX=/no_think`; qwen3.8-27b -> `ETPC_CHAT_REASONING_EFFORT=none`. W czacie LM Studio z 27B myślenie wyłączać przełącznikiem modelu (prompt `/no_think` nie działa).
- W toku: pytania 12, 1, 4 na 27B z `reasoning_effort=none`, sędzia = 27B, 16k znaków (porównanie z 14B).
- **Wynik 27B (2026-10-20 ~01:10), `ETPC_CHAT_REASONING_EFFORT=none`, pytania 12, 1, 4, 16k znaków:** 243-329 s na odpowiedź (14B: 69-143 s), 2.2-3.2 tys. znaków; nieistniejące cytowania 0, sprzeczności wyniku 0. Jakość wyraźnie wyższa niż 14B: #12 wszystkie wyniki spraw dobrze (Danileţ GC 2025 i Izba 2024, Kozan, Morawiec, **Guz - naruszenie, z poprawnym opisem upomnienia**, Kunstelj - brak naruszenia z trafnym rozróżnieniem); #1 niuans zgodny z linią orzecznictwa (sama odmowa nie narusza, brak mechanizmów tak; Siałkowska/Staroszczyk naruszenie, Bąkowska brak), jedna nieścisłość: Kulikowski przytoczony jako zasada bez wyniku (tam naruszenie); #4 konkretne liczby ze źródeł (2.1-2.2 m², Płock 19-36%, Kurkowski 8 dni). Sędzia (27B) przy #1: 17/17 poparte; przy #12 i #4 lista JSON urwana (limit tokenów przy długich odpowiedziach) -> poprawka: sędzia parsuje kompletne obiekty z urwanej listy, pole „zdanie” tylko 6-8 słów. Plik: `etpc/database/odpowiedzi/20261019-230848-qwen-qwen3.8-27b.json`.
- Wniosek: 14B do szybkich odpowiedzi (~80 s), 27B gdy liczy się jakość (~5 min przy częściowym offloadzie; na karcie z >= 20 GB cały model na GPU).

## Plan rozwoju i sprzęt (2026-10-20)
**Sprzęt teraz:** 32 GB RAM, RX 6750 XT 12 GB (Docker/WSL widzi ~15.6 GB RAM - domyślny limit WSL). Najbliżej: **RAM do 64 GB**; karta może w tym roku, może nie -> **model czatu: Qwen3-14B** (`ETPC_CHAT_SUFFIX=/no_think`, ~80 s na odpowiedź), 27B tylko do prób jakości (~5 min). Po dołożeniu RAM: `%USERPROFILE%\.wslconfig` z `[wsl2] memory=40GB` (więcej dla MariaDB i Qdranta; buffer pool MariaDB może urosnąć z 4 GB), restart WSL/Dockera.
Rozważane karty (do decyzji): Radeon AI PRO R9700 32 GB (27B w całości, bez CUDA - do LM Studio wystarczy), używana RTX 3090 24 GB (CUDA, 27B Q4 w całości), RTX 5060 Ti 16 GB (CUDA, za mało na 27B). Trenowanie/dostrajanie i tak zdalnie (Vast.ai, HF), lokalnie tylko uruchamianie modeli.
Docelowa skala (szacunek): ETPC + NSA + SAOS + przepisy = ~0.5-0.8 TB (bazy, wektory, modele, kopie) -> dysk NVMe 2-4 TB; RAM 128 GB przy dużych modelach MoE z ekspertami w RAM, 64 GB wystarczy przy 14B/27B.

**Kolejność prac:**
1. **NSA:** `python -m nsa przetworz` (2.25 mln, ~6 h), potem wektory poziom 1 (wektor na orzeczenie: teza + sentencja + początek „zważył”) i poziom 2 (pełne fragmenty od „zważył” dla uchwał, wyroków NSA, orzeczeń z tezą) - lokalnie i paczkami (koledzy, Vast.ai).
2. **pgvector obok Qdranta:** `PgVectorStore` jako druga strategia `VectorStore` (ustawienie `<PREFIKS>_VECTOR_STORE=qdrant|pgvector`), PostgreSQL z pgvector w naszym docker-compose (port inny niż 5432 - zajęty przez stary SAOS), wektory ETPC skopiowane z Qdranta (bez liczenia), porównanie: czas zapytania, RAM, `ocen` (MRR, kontekst), filtry. Wynik do notatek i `interview.md`. MariaDB zostaje źródłem prawdy; pełnej wersji relacyjnej w PostgreSQL nie utrzymujemy (podwójny SQL każdej zmiany) - w razie potrzeby eksport w jedną stronę z raw_records/tabel.
3. **SAOS jako trzecie dziecko (`saos/`):** stary projekt `../orzeczenia_SAOS` (PostgreSQL, 222 360 orzeczeń sądów powszechnych/SN/TK/KIO, 10 GB, 728 759 `referenced_regulations`, kolumna `raw` jsonb = pełny rekord SAOS) -> jednorazowo `raw` do `raw_records` MariaDB (źródło `saos`, bez ponownego pobierania), `SaosSource` (aktualizacje z API SAOS, kod w starym projekcie), parser i tabele na MariaDB, wyszukiwarka, chunker, wektory `saos_<model>`, MCP. Stary projekt zostaje jako kopia do czasu sprawdzenia.
4. **Projekt `przepisy`:** ustawy z API ELI Sejmu (api.sejm.gov.pl/eli), podział na artykuły, **wersje w czasie** (wyrok odnosi się do brzmienia z daty orzekania).
5. **Powiązania orzeczenie <-> przepis:** wspólny identyfikator (akt ELI + artykuł + ustęp + wersja), normalizacja: NSA `legal_bases` (Dz.U. + artykuł + link ISAP), SAOS `referenced_regulations`, ETPC artykuły Konwencji (osobny słownik); parser odwołań z tekstu („art. 15 ust. 1 ustawy z dnia ... (Dz. U. ...)”) dla reszty. Jedna instancja MariaDB, baza na projekt + baza `przepisy`; zapytania między bazami na tym samym serwerze (`nsa.x JOIN przepisy.y`). Efekt: „orzecznictwo do przepisu”, RAG przepis + orzeczenia, statystyki.
6. **Wyszukiwanie po wszystkich źródłach:** w rdzeniu retriever łączący retrievery projektów przez RRF (z oznaczeniem źródła), wspólny serwer MCP „orzecznictwo”.
7. Później: serwer MCP po HTTP (claude.ai, wtyczka Word) z tokenem i tunelem/VPS; strona `/zapytaj`; endpoint OpenAI-compatible.

## Waga orzeczenia (ustalenie 2026-10-20, do zrobienia po wektorach i przetworz NSA)
Przy każdym orzeczeniu poziom wagi (A/B/C/D) z widocznym powodem („uchwała 7 sędziów”, „cytowane 34 razy”, „glosa: ...”), nie jedna liczba z czarnej skrzynki. Proste progi, bez oceny przez LLM (drogo, niestabilnie); LLM tylko do streszczeń poziomów A-B (plan „perełek”). Użycie: sortowanie i filtr na stronach, kolejność wektorów, sygnał w RAG/MCP.
- **ETPC (prawie gotowe):** `importance` z HUDOC (Key/1/2/3, ocena redakcji), Wielka Izba (`formation`), ranking cytowań (`ruling_citations` z `scl`, już na stronie i w MCP). Brakuje kolumny wagi liczonej w `przetworz` (`EtpcDeriver`). Cytowania faworyzują stare wyroki -> liczyć względem wieku albo cytowania z ostatnich 10 lat.
- **NSA (po pełnym `przetworz`):** sygnały: uchwała (OPS/FPS/GPS, `kind = 'uchwala'`) > teza (~1.8%, wybór redakcji CBOSA) > publikacje i glosy (`publications`) > wyrok NSA > reszta; zdanie odrębne = sporność. **Cytowania:** regex sygnatur w uzasadnieniach (`II FSK 123/19`, `I OPS 1/20`) -> dopasowanie do `docket_number` -> nowa tabela `judgment_citations` (migracja `nsa/`). `related` się nie nadaje (powiązania instancyjne w tej samej sprawie). Na części zbioru liczby cytowań byłyby przypadkowe -> dopiero na pełnym. Punkty bez cytowań można policzyć od razu.
- Każdy projekt liczy wagę w swoim `Deriver` (dziecko); w rdzeniu najwyżej wspólna nazwa kolumny / filtr strony.

## Jeden adres dla wszystkich stron (2026-10-20)
- **http://127.0.0.1:8080**: `/etpc/` (wyszukiwarka + `/etpc/statystyki`), `/nsa/` (+ `/nsa/statystyki`), `/` = lista projektów; stare adresy bez prefiksu (`/orzeczenie/1`, `/?q=...`) -> 301 do `/etpc/...`. Port 8082 i usługa `www-nsa` usunięte (compose: jedna usługa `www`, `python -m portal`, zmienne ETPC_* i NSA_*, log `logs/www`).
- Rdzeń: `judgments_core.web.Portal` (WSGI: montuje aplikacje projektów pod `/<nazwa>/`, ustawia `SCRIPT_NAME`); `WebApp` dopisuje `SCRIPT_NAME` do linków od korzenia (`href|action|src="/..."`) w odpowiedziach HTML - strony projektów bez zmian, to samo zadziała na hostingu w podkatalogu. `LocalEmbedder` o tym samym modelu i urządzeniu dzieli w procesie jeden wczytany model (bge-m3 raz, ~2.3 GB, zamiast raz na projekt). Złożenie portalu w `portal/__main__.py` (poza rdzeniem: importuje projekty). `python -m etpc|nsa www` nadal serwuje jeden projekt.
- Sprawdzone: strony i statystyki obu projektów, przekierowania, linki z prefiksem; testy `tests/test_portal.py` (145 razem).
- Zauważone: ETPC „prawo do sądu” na stronie (tryb hybrydowy) 59-218 s - znany problem FULLTEXT z grupami słowniczka (sekcja „Czas pytania RAG”); RAG ma już tryb znaczeniowy, strona jeszcze nie.
- **Strona ETPC: domyślnie tryb znaczeniowy** (2026-10-20): `tryb` w adresie: brak = znaczenie, `hybrydowe` = słowa + znaczenie, `pelnotekstowe` = same słowa (`etpc/web.py`: `DEFAULT_MODE`, `_PAGE_MODES`, `_mode_param`). „prawo do sądu”: 14 s (pierwsze zapytanie po restarcie, z ładowaniem modelu) zamiast 59-218 s; hybryda z adresu nadal dostępna (62 s, FULLTEXT ucięty limitem -> wynik znaczeniowy z uwagą). NSA bez zmian (hybryda, bo NSA nie ma jeszcze wektorów). `.gitignore`: `/interview.md` (notatka do rozmów, tylko lokalnie).
- **27B, druga runda z poprawionym sędzią (2026-10-20):** te same pytania 12, 1, 4: 327 / 233 / 321 s, 3.2 / 2.3 / 3.5 tys. znaków, sędzia (27B) bez urwań: 18 / 22 / 24 zdania poparte, 0 niepopartych, 0 sprzecznych; cytowania i reguła wyniku 0 / 0. Odpowiedzi i czasy zbliżone do pierwszej rundy. 100% od sędziego = ten sam model, raczej łagodnie (ręcznie: 1 nieścisłość na 3 odpowiedzi). Wynik dopisany do `interview.md`.

## Polecenia z kontenera (2026-10-20)
Lokalny Python (`.venv`) usunięty: polecenia projektów uruchamiamy w kontenerze obrazu `www` (repo zamontowane w `/app`, zmienne `ETPC_*`/`NSA_*` z compose: baza `db`, Qdrant `qdrant`, LM Studio `host.docker.internal:1234`).
- **Wektory ETPC:** `docker compose run --rm www python -m etpc wektory` (opcjonalnie `--budzet 3600`; wznawia od niepoliczonych, Ctrl+C przerywa).
- `run --rm` = osobny, jednorazowy kontener: nie ginie przy `docker compose restart www`, nie zajmuje portu 8080. Wymaga działających `db` i `qdrant` (wstaną same przez `depends_on`) oraz LM Studio z bge-m3 na komputerze.
- Log idzie do `logs/www` (zmienna `ETPC_LOG_DIR` z usługi `www`); inny katalog: `-e ETPC_LOG_DIR=logs`.
- Tak samo inne polecenia: `docker compose run --rm www python -m nsa przetworz`, `... python -m etpc stan`. Testy: `docker compose run --rm www python -m pytest` (obraz ma pytest i ruff, `TEST_DB_DSN` w compose = `db:3306/szkielet_test`; 144 zaliczone, 1 pominięty - pyarrow). MCP: `.mcp.json` = `docker compose run --rm -T www python -m etpc|nsa mcp` (pakiet `mcp` w obrazie; sprawdzone po stdio: narzędzia szukaj, fragmenty, orzeczenie, podobne; wymaga działającego Dockera).

## MCP a RAG - czym się różnią (2026-10-20)
Dwie różne warstwy, jedno nie zastępuje drugiego.
- **RAG = sposób odpowiadania:** znajdź właściwe fragmenty, włóż je do kontekstu modelu, każ odpowiadać tylko z nich z cytowaniem. U nas: `judgments_core/rag.py` `Retriever` (wyszukiwanie znaczeniowe + po słowach, fragmenty zamiast całych orzeczeń, reranker, pakowanie w limit znaków), `pipeline.py` (polecenie dla modelu), `ocen` / `ocen-odpowiedzi` (jakość).
- **MCP = protokół:** standardowy sposób, w jaki aplikacja z modelem (Claude Code, LM Studio, Claude Desktop) widzi nasze narzędzia i je wywołuje. Sam niczego nie szuka.
- Retriever ma kilka wyjść: MCP `szukaj`/`orzeczenie`/`podobne`/`fragmenty` (Claude Code, LM Studio), CLI `fragmenty` (sam kontekst), CLI `zapytaj` -> LM Studio przez HTTP (`/v1/chat/completions`) **bez MCP**. RAG nie musi więc iść przez MCP.
- **`zapytaj` (RAG bez MCP):** steruje nasz kod (zawsze jedno wyszukanie, potem odpowiedź), ścieżka powtarzalna i oceniana; dla lokalnych 14B/27B, słabych w używaniu narzędzi. **Czat przez MCP:** model sam decyduje, czego, kiedy i ile razy szukać, doczytuje orzeczenia; dobre dla Claude'a, słaby model zrobi jedno marne wyszukanie.
- Kolejność powstania: pierwszy MCP miał tylko `szukaj` + `orzeczenie` (lista i cały tekst, ~100 tys. znaków na orzeczenie: drogo w tokenach Claude'a, przepełnienie kontekstu modelu lokalnego). Retriever dodał właściwą część RAG (akapit zamiast wyroku, w limicie) i od razu zasilił trzy drogi: MCP `fragmenty`, CLI `fragmenty`, `zapytaj`.
- Wniosek: RAG = co dać modelowi, MCP = którędy model może po to sięgnąć. Ulepszenie retrievera (waga orzeczeń, ranking cytowań) działa wszędzie naraz.

## Jak retriever wybiera fragmenty - przykład na 10 orzeczeniach (2026-10-20)
Zgodnie z kodem (`judgments_core/rag.py`, ETPC). Baza zabawkowa: Delfi, MTE i Index, Sanchez, Magyar Jeti, Lingens, Kudła, Handyside, Bladet Tromsø, Frydlender, Pravoye Delo. Pytanie: „czy portal odpowiada za komentarze czytelników?”.
- **Krok 0, raz (`wektory`):** chunker kroi orzeczenie na kawałki ~2000 znaków (max 3200) bez przekraczania sekcji, z linijką kontekstu (`DELFI AS v. ESTONIA | THE LAW | C. The Court's assessment`); bge-m3 zamienia każdy kawałek na wektor 1024 liczb (podobne znaczenie = podobne liczby, także między językami); Qdrant trzyma wektory z odnośnikiem do orzeczenia i miejsca w tekście.
- **1. Pytanie -> wektor** tym samym bge-m3 (polskie pytanie blisko angielskiego „liability of a news portal for third-party comments”).
- **2. Ranking orzeczeń:** Qdrant szuka kawałków najbliższych pytaniu, orzeczenie liczy się według najlepszego kawałka (Delfi, Sanchez, MTE, Jeti, Pravoye Delo ... Kudła, Frydlender na końcu). Tryb hybrydowy: druga lista z FULLTEXT (słowniczek pl -> en), łączenie RRF (punkty 1/(60 + miejsce)). ETPC w `fragmenty` domyślnie samo znaczenie (FULLTEXT bywa bardzo wolny).
- **3.** 20 najlepszych orzeczeń (`CANDIDATES`), z każdego do 3 kawałków najbliższych pytaniu (`PER_JUDGMENT`).
- **4. Poszerzenie** o 400 znaków z każdej strony do końca wiersza (`WIDEN_CHARS`); zachodzące kawałki się scalają.
- **5. Reranker (opcjonalnie):** cross-encoder ocenia parę pytanie + kawałek dokładniej niż odległość wektorów; lokalnie wyłączony (puste `<PREFIKS>_RERANKER`).
- **6. Pakowanie w 16 000 znaków** (`BUDGET_CHARS`) na zmianę: najlepszy kawałek każdego orzeczenia w kolejności rankingu, potem drugi najlepszy ... Dzięki temu model widzi kilka orzeczeń (zasada z Delfi, rozwinięcie w Sanchez, kontrast MTE), a słabe (Kudła, Frydlender) wypadają za limitem.
- **7. Podanie modelowi:** bloki `[n] sprawa, data, skarga, skład, link HUDOC` + fragmenty z nagłówkiem i znakami od-do + polecenie „tylko z tych źródeł, cytuj [n], podaj wynik sprawy”. `zapytaj` wysyła to do LM Studio; przez MCP Claude dostaje ten sam blok z narzędzia `fragmenty`.
- **Skąd „wie”:** retriever mierzy tylko odległość liczb; rozumienie siedzi w bge-m3, reszta to nasze reguły (ile orzeczeń, ile kawałków, szerokość, limit, kolejka na zmianę). Słabe punkty: kawałek o podobnym słownictwie z innej sprawy (lepiej odsiewa reranker); wektory nie wiedzą, które orzeczenie jest ważne (Izba i Wielka Izba w Delfi brzmią podobnie) -> waga orzeczeń z planu.

## Gdzie jest wybieranie orzeczeń i na czym polega strojenie RAG (2026-10-20)
**Ścieżka w kodzie** (pytanie -> kontekst):
- Wejście: MCP `fragmenty` / CLI `fragmenty` / `zapytaj` (`etpc/mcp.py`, `judgments_core/cli.py`) -> `Retriever.retrieve`.
- Główna funkcja, wszystkie kroki: `judgments_core/rag.py` `retrieve()`.
- Ranking orzeczeń ETPC: `etpc/rag.py` `ranking()` -> `etpc/search.py` `search()`; hybryda `_hybrid()`, łączenie `judgments_core/semantic.py` `rrf()`.
- Qdrant: orzeczenia najbliższe znaczeniem `semantic.py` `judgments()`, do 3 kawałków z orzeczenia `passages_of()`.
- `rag.py`: kandydaci `_candidates()` (bez wektorów okno wokół słowa z pytania), `widen()`, `merge()`, reranker `_rerank()` + `judgments_core/rerank.py`, pakowanie `pack()`, tekst dla modelu `render()`.
- Krojenie przy `wektory`: `judgments_core/chunker.py` (`target_chars = 2000`), granice sekcji `etpc/chunker.py`.
- Pokrętła: góra `judgments_core/rag.py` (`CANDIDATES`, `PER_JUDGMENT`, `BUDGET_CHARS`, `WIDEN_CHARS`); projekt nadpisuje je w potomku (np. `EtpcRetriever.mode = "znaczeniowe"`).

**Strojenie** = zmień jedno pokrętło, zmierz, zostaw tylko to, co poprawia.
- **Miarka:** `<projekt>/pytania.json` (pytanie + orzeczenia, które powinny się znaleźć) i `python -m <projekt> ocen` (`judgments_core/evaluation.py`): **MRR** (1 / miejsce pierwszego właściwego, średnia) i **w kontekście** (ile oczekiwanych zmieściło się w limicie - ważniejsze, model widzi tylko to). Bez modelu językowego: sekundy, tanio, powtarzalnie. `ocen-odpowiedzi` ocenia całe odpowiedzi (minuty, mniej stabilne).
- **Pokrętła i koszty:**
  - wielkość kawałka: pełniejszy wywód, ale wektor rozmywa kilka wątków;
  - `CANDIDATES`: łapie orzeczenia nisko w rankingu, ale wolniej i więcej śmieci;
  - `PER_JUDGMENT`: głębiej w jedno orzeczenie, ale mniej różnych orzeczeń;
  - `WIDEN_CHARS`: więcej kontekstu wokół akapitu, ale mniej kawałków w limicie;
  - `BUDGET_CHARS`: więcej źródeł, ale mały model gubi się w długim kontekście;
  - tryb znaczeniowy / hybrydowy: hybryda łapie nazwy i numery, ale FULLTEXT bywa bardzo wolny;
  - reranker: lepsza kolejność, ale ~49 s na pytanie na CPU;
  - model wektorów: lepsze rozumienie, ale przeliczenie wszystkich wektorów;
  - słowniczek pl -> en (`etpc/glossary.py`): polskie pytania trafiają w angielskie teksty, ale to ręczna praca;
  - waga orzeczeń (plan): ważne wyżej, trzeba dobrać, ile waży wobec podobieństwa znaczenia.
- **Dotychczasowe pomiary** (12 pytań ETPC): hybryda MRR 0.670, 17/23 w kontekście, 61 s; samo znaczenie 0.668, 17/23, 2 s (domyślne); reranker 0.688, ale dwa orzeczenia wypadły z kontekstu i 49 s (wyłączony).
- **Zastrzeżenie:** 12 pytań to mało (jedno pytanie przesuwa MRR o kilka setnych, różnice ~0.02 mogą być przypadkiem). Najważniejszy krok: 50-100 pytań wzorcowych z różnych dziedzin, najlepiej od kogoś, kto zna orzecznictwo.

## Transporty w kontenerze (2026-10-20)
- Strategia pobierania: `judgments_core/transport.py` (`Transport`, `RequestsTransport`, `CurlTransport`, `FallbackTransport`, rejestr `TRANSPORTS`, `make_transport()`, `is_cloudflare_challenge()`), `judgments_core/browser.py` (`PlaywrightTransport`); łańcuch projektu `etpc/__main__.py` (`requests`, `curl`, `playwright`), nadpisanie `<PREFIKS>_TRANSPORT` (`cli.py`); przerwy i ponawianie w `judgments_core/http.py` `PoliteClient`.
- Obraz `www` (`python:3.13-slim`) nie miał curla ani Playwrighta, a `.env` ma `ETPC_TRANSPORT=playwright` -> `pobierz` ETPC w kontenerze by nie zadziałał. Dodane do `docker/www.Dockerfile`: curl (apt) i `playwright` + `playwright install --with-deps chromium` (~500 MB).
- Sprawdzone z kontenera: zapytanie do HUDOC (`/app/query/results`, Handyside 001-57499) przez każdy transport osobno: requests, curl, playwright - wszystkie 200 z danymi, bez Cloudflare.

## Kopia bazy z kontenera (2026-10-20)
- Było: `.env` `ETPC_DUMP_COMMAND=docker exec -i -e MYSQL_PWD orzecznictwo-szkielet-db-1 mariadb-dump` - w kontenerze nie ma dockera, więc `kopia` i kopia po `pobierz`/`przetworz` (przy `ETPC_BACKUP_DIR`) kończyły się błędem.
- Teraz: obraz `www` ma `mariadb-client` (Debian trixie: mariadb-dump 11.8, zrzuca serwer 10.11 bez problemu); compose ustawia `ETPC_DUMP_COMMAND` i `NSA_DUMP_COMMAND` = `mariadb-dump --skip-ssl` (środowisko wygrywa z `.env`; host `db` z DSN; `--skip-ssl`, bo klient 11.8 ostrzega przy każdej kopii o certyfikacie). Sprawdzone `dump_database` na `szkielet_test`.
- Zrzut zaczyna się od `/*M!999999\- enable the sandbox mode */` (tak samo robił mariadb-dump 10.11.19 z kontenera db) - przy wczytywaniu na hostingu sprawdzić wersję klienta (starsze niż 10.5.25 / 10.6.18 / 10.11.8 tej linii nie rozumieją; wtedy usunąć pierwszą linię).
- **Uwaga:** działający kontener `www-1` ma stary obraz i stare zmienne. `docker compose exec www ...` dostanie nowe dopiero po `docker compose up -d www`, a to zabija przebiegi uruchomione przez `exec` w `www-1`. Długie zadania lepiej przez `docker compose run --rm www ...` (osobny kontener, zawsze bieżący obraz i zmienne).

## Długie zadania przez `docker compose run` (2026-10-20)
**Uruchomienie w tle** (terminal można zamknąć, kontener po zakończeniu sam znika):
```
docker compose run -d --rm --name nsa-przetworz www python -m nsa przetworz
docker compose run -d --rm --name etpc-wektory  www python -m etpc wektory --budzet 3600
```
- `-d` = w tle, `--rm` = usuń kontener po zakończeniu, `--name` = stała nazwa do podglądu i zatrzymania (dwa zadania nie mogą mieć tej samej nazwy).
- **Podgląd:** `docker logs -f nsa-przetworz` (Ctrl+C kończy tylko podgląd, nie zadanie); log też w `logs/www/`. Czy działa: `docker ps --filter name=nsa-przetworz`. Stan bazy: `docker compose run --rm www python -m nsa stan`; przebiegi w tabeli `fetch_runs`.
- **Zatrzymanie:** `docker stop nsa-przetworz` - trwa ~10 s (python jako proces nr 1 w kontenerze ignoruje SIGTERM, Docker dobija go po 10 s), albo od razu `docker kill nsa-przetworz`. Bezpieczne: przebiegi są wznawialne (strona/porcja w jednej transakcji), to samo polecenie ponownie = kontynuacja.
- **Na pierwszym planie** (bez `-d`): `docker compose run --rm www python -m nsa przetworz`; Ctrl+C przerywa zadanie. Gdy zamknie się terminal albo zabije sam klient `docker compose` (tak Claude Code ubił klienta przy braku pamięci), kontener **działa dalej**, ale `--rm` już go nie usunie: po końcu został zatrzymany kontener `...-www-run-<id>` -> `docker ps -a --filter status=exited` i `docker rm <nazwa>`.
- **Dlaczego nie `docker compose exec www ...`:** exec działa w kontenerze strony `www-1`: ma obraz i zmienne z chwili jego startu (nie widzi przebudowy obrazu ani zmian w compose), a `docker compose up -d www` / restart po zmianie kodu zabija wszystkie zadania uruchomione przez exec.
- Nakładanie się: drugi taki sam przebieg kończy się sam („Poprzedni przebieg (nsa:baza) jeszcze trwa”, blokada `GET_LOCK`); `wektory` mają osobną blokadę i mogą iść obok `przetworz`. Wyłączenie Docker Desktop zatrzymuje wszystko (wznowić tym samym poleceniem).
- Kopia po przebiegu: z `<PREFIKS>_BACKUP_DIR` w `.env` robi się sama; wyłączenie dla jednego przebiegu: `-e ETPC_BACKUP_DIR=` przed `www`.

## Strona główna i zakładki portalu (2026-10-20)
- `/` = strona główna: **jedno wyszukiwanie z wyborem bazy** (ETPC / NSA jako przełącznik; formularz -> `/szukaj?baza=nsa&q=...` -> przekierowanie do `/nsa/?q=...`), pod nim **lista baz w jednym panelu** (nazwa, opis, liczby, linki do wyszukiwania z filtrami i statystyk). Pierwsza wersja (osobna karta z wyszukiwaniem na projekt) wyglądała jak dwa oddzielne pudełka. Każda strona ma u góry pasek zakładek **⌂ Strona główna | ETPC | NSA** z zaznaczoną bieżącą (wcześniej napis „Orzecznictwo” nie wyglądał jak powrót na główną).
- Rdzeń (`judgments_core/web.py`): `Portal.tabs()` buduje pasek, `Portal._with_tabs()` dokleja go za `<body>` w odpowiedziach HTML projektów (po przepisaniu ich linków, z poprawionym Content-Length; strony projektów bez zmian); `Portal.home()` / `_row()`, przekierowanie `/szukaj`; `layout(..., header=False)` dla strony głównej; klasa CSS `.portal-tabs` (`.tabs` jest zajęte przez zakładki wersji językowych orzeczenia ETPC).
- Projekty: `WebApp.description` (opis karty) i `WebApp.facts(cur)` -> `[(liczba, opis)]` (ETPC: orzeczenia, wyroki, decyzje z `ruling_stats`; NSA: orzeczenia, wyroki, NSA z `judgment_stats`); `WebApp.summary()` w rdzeniu łączy się z bazą tylko, gdy projekt nadpisał `facts`, a błąd bazy = karta bez liczb.
- Pod przedrostkiem (hosting w podkatalogu) linki zakładek i strony głównej dostają go raz (test `test_under_a_prefix_tabs_and_home_links_get_it_once`). Sprawdzone zrzutami Playwrighta: jasny i ciemny motyw, telefon (390 px), strona orzeczenia ETPC.
- Usługa `www` widzi zmianę po `docker compose restart www` (przerywa zadania uruchomione przez `exec` w `www-1`).

## Tłumaczenie w przeglądarce (2026-10-20)
- Wszystkie strony miały `<html lang=pl>`, więc Chrome/Edge uznawały angielski wyrok za polski i nie proponowały tłumaczenia.
- Teraz strona orzeczenia ETPC ma `<html lang>` = język wyświetlanej wersji (`etpc/labels.py` `LANG_CODES`: kody HUDOC -> BCP 47, ENG -> en, FRE -> fr, POL -> pl ...), tekst `<div class=text lang=..>`; polski interfejs (`header`, pasek zakładek portalu, zakładki wersji językowych, „wróć”, metadane po prawej) ma `lang=pl translate=no`, więc tłumacz go nie rusza. Fragmenty w wynikach wyszukiwania mają `lang` swojego języka (prawy przycisk -> „Przetłumacz zaznaczenie”).
- Rdzeń: `layout(..., lang="pl")`. NSA bez zmian (teksty po polsku).
- Sprawdzone na podglądzie: Delfi (84294) ENG -> `lang=en`, FRE -> `lang=fr`, POL -> `lang=pl`. Samego paska tłumaczenia nie da się sprawdzić w przeglądarce bez okna - do obejrzenia w Chrome/Edge.

## Stan na 2026-10-20 ~20:30 UTC: co czeka na koniec `nsa przetworz`
- **`nsa przetworz`** (uruchomione przez `docker compose exec www` w `www-1`, start 13:45 UTC): ~1.04 mln z ~2.25 mln rekordów po 6.5 h, zostało ~7 h. Wznawialne (kursor w `sync_state`).
- **Strona 8080 jeszcze w starej wersji** (stary obraz, stare zmienne, stary kod - bez strony głównej, zakładek, tłumaczenia). Nie restartowana, bo `docker compose up -d www` / restart zabija przebiegi z `exec` w `www-1`. Po zakończeniu NSA: `docker compose up -d www` (nowy obraz z curl/Playwright/mariadb-client, `*_DUMP_COMMAND` z compose, nowy kod). Do tego czasu nowa wersja na podglądzie **http://127.0.0.1:8090** (kontener `portal-podglad`, bez modelu bge-m3 w procesie: zapytania znaczeniowe przez LM Studio); potem `docker stop portal-podglad`.
- **Kopia ETPC:** `docker compose run -d --rm --name etpc-kopia www python -m etpc kopia` (po przeliczeniu rankingu cytowań) -> `etpc/database/etpc.sql.gz`, stara kopia z 19.10 podmieniana dopiero po udanym zrzucie.
- **Kopia NSA:** jeszcze nie - baza 67 GB w trakcie `przetworz`; najpierw ustawić `NSA_BACKUP_DIR` w `.env` (katalog na duży plik), potem `docker compose run -d --rm --name nsa-kopia www python -m nsa kopia`.
- Następne długie zadania tylko przez `docker compose run -d --rm --name ...` (sekcja „Długie zadania przez `docker compose run`”), żeby restart strony ich nie przerywał.
