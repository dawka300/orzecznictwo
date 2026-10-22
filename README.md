# orzecznictwo-szkielet

Wspólny szkielet projektów, które pobierają orzeczenia sądów z publicznych baz do własnej bazy **MariaDB** i mają działać bez przerwy na zwykłym hostingu z Pythonem (seohost). Wyrósł z lokalnego projektu `orzeczenia_SAOS` (polskie orzeczenia z SAOS w Postgresie).

**Polecenia** (Docker, pobieranie, przetwarzanie, wektory, paczki, szukanie, testy) z opisem opcji: [`komendy.md`](komendy.md).

Projekty w tym repozytorium:

| Katalog | Co pobiera | Źródło | Stan |
|---|---|---|---|
| `nsa/` | orzeczenia sądów administracyjnych (NSA i WSA) | [CBOSA](https://orzeczenia.nsa.gov.pl) | szkielet, najpierw próbne zapytania – [`nsa/notepad.md`](nsa/notepad.md) |
| `etpc/` | orzeczenia Europejskiego Trybunału Praw Człowieka | [HUDOC](https://hudoc.echr.coe.int) | szkielet, najpierw próbne zapytania – [`etpc/notepad.md`](etpc/notepad.md) |

Struktura bazy i klasy danych (DTO) każdego projektu ustalimy po próbnych zapytaniach do jego źródła. Wyszukiwanie semantyczne (RAG) będzie korzystać z **osobnej bazy wektorowej**; MariaDB trzyma same orzeczenia.

## Jak to działa

```
źródło (CBOSA / HUDOC)                 MariaDB projektu
        │                      ┌───────────────────────────────┐
  Source.iter_pages(kursor) ──►│ raw_records  – surowy JSON/HTML│
        │  strona po stronie   │ sync_state   – kursor          │
        ▼                      │ tabele projektu (później)      │
  Importer: zapisuje stronę    └───────────────────────────────┘
  i kursor w jednej transakcji          │ parser projektu (później)
                                        ▼
                               baza wektorowa (osobna, później)
```

- **`judgments_core/`** – rdzeń wspólny dla projektów, nic nie wie o konkretnym źródle:
  - `http.py` – `PoliteClient`: przerwa między żądaniami, ponawianie z odczekaniem (429/5xx, `Retry-After`);
  - `importer.py` – pętla pobierania: po każdej stronie zapisuje rekordy i kursor w jednej transakcji, więc przerwany przebieg wznawia się od ostatniej strony; kończy się po `--limit` rekordach albo po `--budzet` sekundach;
  - `db.py` – MariaDB: połączenie z DSN, migracje `NNN_*.sql`, `sync_state`, `raw_records`, blokada `GET_LOCK` (drugi przebieg z crona kończy się od razu);
  - `source.py` – interfejs `Source`, `RawRecord`, `Page` – to jedyne, co projekt musi napisać;
  - `settings.py` – ustawienia z `.env`/zmiennych środowiska z prefiksem projektu (`NSA_*`, `ETPC_*`);
  - `cli.py` – wspólne polecenia `migracje`, `pobierz`, `stan`;
  - `text.py` – czyszczenie HTML; `vectors.py` – interfejs bazy wektorowej (bez implementacji).
- **Najpierw surowe dane:** każdy rekord trafia do `raw_records` dokładnie tak, jak przyszedł. Tabele projektu wyprowadza się z nich, więc poprawka parsera nie wymaga ponownego pobierania (lekcja z SAOS).
- **Projekt** (`nsa/`, `etpc/`) to `source.py` (klasa źródła), `migrations/` (jego tabele), `__main__.py` (kilka linijek opisu) i `notepad.md`.
- Zależności są celowo minimalne (`requests`, `PyMySQL` – czysty Python, bez kompilacji), a kod jest zgodny z Pythonem 3.9+, bo na hostingu może nie być nowszego.

## Uruchomienie lokalnie

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt   # Linux/macOS: .venv/bin/python
cp .env.example .env                 # i uzupełnij (kontakt w User-Agent)
docker compose up -d                 # MariaDB 10.11 na 127.0.0.1:3306, bazy nsa, etpc, szkielet_test
python -m nsa migracje               # tabele rdzenia + projektu
python -m nsa pobierz --limit 10 --na-sucho   # tylko pobierz i wypisz (bez bazy)
python -m nsa pobierz --limit 10     # zapisz w bazie
python -m nsa stan                   # kursor i liczba rekordów
python -m pytest                     # testy (bazodanowe pomijane bez MariaDB)
```

To samo dla `etpc`. Polecenia uruchamia się z katalogu głównego repozytorium.

## Na hostingu (seohost)

Szkic – szczegóły do sprawdzenia w panelu (lista w notatnikach projektów):

1. Sklonuj repozytorium, utwórz venv i `pip install -r requirements.txt`.
2. Utwórz bazę MariaDB w panelu, wpisz `NSA_DB_DSN=mysql://użytkownik:hasło@localhost:3306/baza` do `.env`.
3. `python -m nsa migracje`.
4. Cron co 5 minut, każdy przebieg ograniczony czasem i wznawiany od kursora:

   ```
   */5 * * * * cd ~/orzecznictwo-szkielet && .venv/bin/python -m nsa pobierz --budzet 240 >> logs/cron.log 2>&1
   ```

   "Ciągłe działanie" to na zwykłym hostingu raczej cron niż proces działający bez końca (hostingi zabijają długie procesy). Blokada w bazie pilnuje, żeby dwa przebiegi nie szły naraz.

## Nowy projekt na tym szkielecie

1. Skopiuj `nsa/` jako `nowy/`, zmień nazwę w `__main__.py` (`name` = prefiks zmiennych w `.env`).
2. Napisz klasę źródła z `name` i `iter_pages(cursor)` zwracającą `Page(records, cursor)`.
3. Tabele projektu dodaj jako `nowy/migrations/001_*.sql`.
4. Dopisz bazę w `docker/mariadb-init.sql` i zmienne w `.env.example`.
