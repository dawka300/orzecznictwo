# Polecenia: ściąga

Wszystkie polecenia projektu w jednym miejscu: Docker, pobieranie, przetwarzanie, wektory (też w paczkach na innych komputerach), szukanie i RAG, strony, testy.

## Najczęstsze polecenia w całości (do skopiowania)

Z katalogu repo, w PowerShell albo Git Bash. Zamiast `etpc` można wpisać `nsa`.

```
# Docker: wstawanie i stan
docker compose ps                                      # co działa (db ma być "Up ... (healthy)")
docker compose start db qdrant www                     # NAJPIERW, gdy stoją: baza (3306), Qdrant (6333), portal http://127.0.0.1:8080
docker compose up -d                                   # to samo, gdy kontenerów nie ma (po down); może odtworzyć bazę
docker compose restart www                             # portal po zmianie kodu

# baza i stan
docker compose run --rm --no-deps www python -m etpc migracje            # tabele (po git pull / na nowej bazie)
docker compose run --rm --no-deps www python -m etpc stan                # gdzie stanęło, ile rekordów, blokady
docker compose run --rm --no-deps www python -m etpc stan --problemy     # plus lista problemów

# import ze źródła (HUDOC / CBOSA)
docker compose run --rm --no-deps www python -m etpc pobierz                     # dalej od kursora, do końca
docker compose run --rm --no-deps www python -m etpc pobierz --limit 20          # próba: 20 rekordów
docker compose run --rm --no-deps www python -m etpc pobierz --budzet 3600       # najwyżej godzinę
docker compose run --rm --no-deps www python -m etpc pobierz --na-sucho --limit 5   # tylko podgląd, bez bazy
docker compose run --rm --no-deps www python -m etpc pobierz --brakujace         # tylko braki znalezione przez sprawdz

# NSA: numery z wyszukiwarki CBOSA (osobne narzędzie, robots.txt tego zabrania - sekcja CBOSA niżej), potem orzeczenia
docker compose run --rm --no-deps www python narzedzia/cbosa_id.py --od 2025-03-05 --do 2025-12-31 --kontakt TWOJ@EMAIL
docker compose run --rm --no-deps www python -m nsa pobierz                      # strony /doc/<ID> z nsa/database/cbosa-id/

# przetwarzanie (raw_records -> tabele, bez pobierania)
docker compose run --rm --no-deps www python -m etpc przetworz                   # nowe i zmienione
docker compose run --rm --no-deps www python -m etpc przetworz --od-poczatku     # wszystko od nowa (po zmianie parsera)
docker compose run --rm --no-deps www python -m etpc przetworz --tylko-pochodne  # tylko statystyki/rankingi

# wektory (tylko LM Studio z bge-m3; zwykle zamiast tego paczki z Vast)
docker compose run --rm --no-deps www python -m etpc wektory                     # brakujące i zmienione
docker compose run --rm --no-deps www python -m etpc wektory --budzet 3600       # najwyżej godzinę
docker compose run --rm --no-deps www python -m etpc wektory --limit 50          # próba

# wektory podzielone na paczki (liczone na innym komputerze z kartą NVIDIA)
docker compose run --rm --no-deps www python -m nsa paczki                                 # 1 paczka 200000 fragmentów
docker compose run --rm --no-deps www python -m nsa paczki --ile 3 --fragmentow 100000     # 3 mniejsze paczki
docker compose run --rm --no-deps www python -m nsa paczki --od-konca                      # z końca kolejki
docker compose run --rm --no-deps www python -m nsa paczki --lista                         # wydane / wczytane / porzucone
docker compose run --rm --no-deps www python -m nsa paczki --zwolnij nsa-0002              # porzuć paczkę
docker compose run --rm --no-deps www python -m nsa paczka-import nsa-0001                 # wczytaj policzoną paczkę

# długie zadanie w tle (terminal można zamknąć)
docker compose run -d --rm --no-deps --name nsa-wektory www python -m nsa wektory
docker logs -f nsa-wektory        # podgląd
docker stop nsa-wektory           # zatrzymaj (wznawialne: to samo polecenie idzie dalej)

# tematy (BERTopic na wektorach; tylko ETPC; nazwy od modelu czatu w LM Studio)
docker compose run --rm --no-deps www python -m etpc tematy                      # A (orzeczenia) i B (fragmenty THE LAW) + nazwy
docker compose run --rm --no-deps www python -m etpc tematy --poziom A --bez-nazw   # tylko poziom A, bez modelu czatu
docker compose run --rm --no-deps www python -m etpc tematy --tylko-nazwy        # nazwij zapisane tematy (po wczytaniu modelu czatu)
docker compose run --rm --no-deps www python -m etpc tematy --tylko-nazwy --claude claude-sonnet-5-5 --max-usd 3   # nazwy od Claude (API, ~$2)
docker compose run --rm --no-deps www python -m etpc tematy --lista              # przejrzeć tematy: nazwa, słowa, artykuły, przykładowe sprawy

# formułki: zdania powtarzane w wielu orzeczeniach (pomijane we wszystkich zakładkach dokumentów)
docker compose run --rm --no-deps www python -m nsa formulki                     # próbka 50 000, próg 50 -> nsa/formulki.txt (~6 min)
docker compose run --rm --no-deps www python -m etpc formulki                    # wersje językowe HUDOC -> etpc/formulki.txt

# szukanie / RAG
docker compose run --rm --no-deps www python -m etpc fragmenty "odpowiedzialność portalu za komentarze"
docker compose run --rm --no-deps www python -m nsa zapytaj "czy wiata to budowla?" --limit-znakow 8000
docker compose run --rm --no-deps -T www python -m etpc podobne-do - --ile 10 < moje-orzeczenie.txt   # podobne do całego tekstu

# kopia bazy, testy, lint
docker compose run --rm --no-deps www python -m etpc kopia
docker compose run --rm --no-deps www python -m pytest
docker compose run --rm --no-deps www sh -c "ruff format . && ruff check ."
```

Niżej opis każdego polecenia i każdej opcji.

Wszystko z katalogu repo, Docker Desktop musi działać, a z nim `db`, `qdrant`, `www` (zwykle wstają same; jeśli `docker compose ps` pokazuje `Exited`, najpierw `docker compose start db qdrant www`).
Polecenia projektów idą w jednorazowym kontenerze:

```
docker compose run --rm --no-deps www python -m <projekt> <polecenie> [opcje]
```

`<projekt>` = `etpc` albo `nsa`. W tabelach niżej jest tylko `<polecenie> [opcje]`.

- **`--rm`** = usuń kontener zaraz po zakończeniu polecenia. `run` za każdym razem tworzy nowy, jednorazowy kontener; bez `--rm` po każdym `stan` czy `pobierz` zostaje zatrzymany `...-www-run-<id>` (lista w `docker ps -a`, miejsce na dysku). Logi i dane są poza kontenerem (`logs/www/`, baza, Qdrant), więc nic się nie traci. Wyjątek: po zamknięciu terminala / zabiciu klienta `docker compose` kontener działa dalej, ale `--rm` go już nie usunie (sprzątanie niżej).
- **`--no-deps`** = nie ruszaj usług, od których zależy `www` (`db`, `qdrant`); uruchom tylko ten kontener. **Zawsze.** Bez tego compose sprawdza `db` i po każdej zmianie usługi `db` w `docker-compose.yml` odtwarza kontener bazy: MariaDB zabita w trakcie pracy (SIGKILL), odzyskiwanie po awarii, wszystkie przebiegi i połączenia zerwane (stało się 2026-10-20). `db` i `qdrant` i tak wstają z Docker Desktop. Skutek uboczny: gdy `db` akurat nie działa (np. Docker Desktop wstał, a kontenery nie), polecenie kończy się błędem `Can't connect to MySQL server on 'db' ([Errno -2] Name or service not known)`. Wtedy najpierw `docker compose start db qdrant www`, sprawdzić `docker compose ps` (`db` = `Up ... (healthy)`) i dopiero potem polecenie projektu.

**Sugestia: skrót `orz` w PowerShell** (jeszcze nie dodany do `$PROFILE`). Zamiast całego przedrostka pisze się `orz etpc stan`, `orz nsa pobierz --limit 20` itd.
```powershell
# 1. raz: pozwolić PowerShellowi wczytywać profil (domyślnie skrypty są zablokowane)
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

# 2. otworzyć profil (utworzyć, jeśli go nie ma)
if (-not (Test-Path $PROFILE)) { New-Item -ItemType File -Path $PROFILE -Force }
notepad $PROFILE

# 3. wkleić do profilu i zapisać
function orz {
    docker compose -f "C:\Users\dawaw\PycharmProjects\orzecznictwo-szkielet\docker-compose.yml" `
        run --rm --no-deps www python -m @args
}

# 4. nowe okno PowerShell (albo: . $PROFILE) i sprawdzić
orz etpc stan
```
`-f` z pełną ścieżką: skrót działa z każdego katalogu, nie tylko z repo. W tle albo z `-e` dalej pełne polecenie (`docker compose run -d --rm --no-deps --name ... www ...`).

## Docker: usługi
| Polecenie | Co robi |
|---|---|
| `docker compose start db qdrant www` | włącz zatrzymane `db` (MariaDB 3306), `qdrant` (6333), `www` (portal 8080); nigdy nie odtwarza kontenerów, bezpieczne na co dzień |
| `docker compose up -d` | to samo, ale tworzy brakujące kontenery (po `down`) i odtwarza te, których konfiguracja w `docker-compose.yml` się zmieniła (dla `db` = przerwane przebiegi i połączenia) |
| `docker compose ps` | co działa |
| `docker compose restart www` | portal widzi zmiany w kodzie (przerywa zadania z `exec` w `www-1`) |
| `docker compose up -d www` | portal z nowym obrazem / nowymi zmiennymi z compose |
| `docker compose build www` | przebuduj obraz po zmianie `docker/www.Dockerfile` albo `requirements.txt` |
| `docker compose logs -f www` | log portalu (też `logs/www/`) |

## Docker: profile pamięci (użytkownik 32 GB / deweloper 64 GB)
`docker-compose.yml` = **profil użytkownika** (komputer z 32 GB RAM): bufor MariaDB 3G, 2 połączenia czytające w `przetworz`. `docker-compose.dev.yml` = **profil deweloperski** (64 GB): nadpisuje tylko różnice (bufor 8G, ustawienia masowego zapisu, 4 połączenia); `docker-compose.prod.yml` = bez zmian. Profil wybiera **jedna zmienna `APP_ENV`** w `.env` (compose czyta ją sam, polecenia się nie zmieniają):
```
APP_ENV=dev
COMPOSE_FILE=docker-compose.yml;docker-compose.${APP_ENV:-prod}.yml
```
`APP_ENV` (dev / prod, bez prefiksu projektu) trafia też do kontenera `www`, a kod czyta ją przez `Settings.app_env` / `settings.dev`: funkcje tylko dla dewelopera sprawdzają `settings.dev`. Na hostingu bez dockera `APP_ENV` też z `.env` (brak = prod). Nazwa `APP_ENV`, nie `ENV`: `sh` w kontenerze traktuje `ENV` jako plik startowy.
| Polecenie | Co robi |
|---|---|
| `docker compose config \| grep buffer-pool` | który profil jest włączony (3G = użytkownik, 8G = dev) |
| `docker compose config \| grep APP_ENV` | które środowisko widzi `www` |
| `docker compose up -d db www` | **przełącza profil** po zmianie `APP_ENV` w `.env`: odtwarza bazę (przerywa przebiegi i połączenia; po ~1 min działa) - w spokojnej chwili |
| `$env:APP_ENV="prod"; docker compose up -d db www` | jednorazowo profil użytkownika (np. pomiar czasu `ocen` jak na 32 GB), bez ruszania `.env`; powrót: `Remove-Item Env:APP_ENV; docker compose up -d db www` |

Pamięć dla Dockera ustawia `%USERPROFILE%\.wslconfig` (poza repo; po zmianie `wsl --shutdown` i Docker od nowa): użytkownik `[wsl2] memory=22GB`, dev `memory=52GB` + `swap=8GB`. Qdrant: kolekcje int8 w RAM (ustawienie kolekcji, nie compose); na 32 GB NSA będzie potrzebować kwantyzacji binarnej albo wektorów na dysku (do zrobienia).

## Docker: sposoby uruchamiania zadań
```
# na pierwszym planie (Ctrl+C przerywa)
docker compose run --rm --no-deps www python -m nsa przetworz

# w tle, ze stałą nazwą (terminal można zamknąć, kontener sam znika po końcu)
docker compose run -d --rm --no-deps --name nsa-przetworz www python -m nsa przetworz
docker logs -f nsa-przetworz            # podgląd (Ctrl+C kończy tylko podgląd)
docker ps --filter name=nsa-przetworz   # czy jeszcze działa
docker stop nsa-przetworz               # zatrzymaj (~10 s; od razu: docker kill)

# jednorazowo inne zmienne: -e przed "www"
docker compose run --rm --no-deps -e ETPC_BACKUP_DIR= www python -m etpc pobierz   # bez kopii
docker compose run --rm --no-deps -e ETPC_LOG_DIR=logs www python -m etpc stan       # log gdzie indziej

# sprzątanie po zabitym kliencie compose (kontener ...-www-run-<id> został)
docker ps -a --filter status=exited
docker rm <nazwa>
```
Przebiegi są wznawialne: to samo polecenie po przerwaniu = kontynuacja. Drugi taki sam przebieg sam się kończy (blokada); `wektory` mogą iść obok `przetworz`. Nie używać `docker compose exec www ...` do długich zadań (ginie przy restarcie `www`).

## Baza i stan
| Polecenie | Co daje | Kiedy / przykład |
|---|---|---|
| `migracje` | tworzy brakujące tabele i kolumny; już zastosowane migracje pomija, więc można puszczać zawsze | po `git pull`, w którym doszedł plik w `*/migrations/`; na nowej bazie przed pierwszym `pobierz` |
| `stan` | kursor (gdzie stanęło pobieranie), ile rekordów surowych i przetworzonych, ostatnie przebiegi z czasem i wynikiem, kto trzyma blokadę, liczba problemów | „czy nocny przebieg doszedł do końca?”, „czy coś jeszcze liczy?”: `stan` |
| `stan --problemy` | to samo plus lista otwartych problemów (brakujące rekordy, błędy parsera) | po `sprawdz`, żeby zobaczyć, czego brakuje |
| `sprawdz` | dla każdego roku porównuje liczbę orzeczeń w źródle z bazą; różnice zapisuje jako problemy | raz na jakiś czas, czy pobieranie niczego nie zgubiło |
| `sprawdz --od 2020 --do 2022` | to samo tylko dla tych lat (szybciej, mniej zapytań do źródła) | podejrzenie dziury w jednym okresie: `sprawdz --od 2021 --do 2021` |
| `kopia` | zrzut całej bazy projektu do `<PREFIKS>_BACKUP_DIR/<projekt>.sql.gz` (nadpisuje poprzedni) | przed ryzykowną migracją albo `przetworz --od-poczatku`; ETPC lokalnie trafia do `etpc/database/` |

## Pobieranie (`pobierz`)
Bez opcji pobiera dalej od miejsca, w którym skończył poprzedni przebieg (kursor w bazie). Domyślnie bez limitu czasu (`<PREFIKS>_TIME_BUDGET_S=0`).

| Opcja | Co daje | Kiedy / przykład |
|---|---|---|
| `--limit N` | koniec po N zapisanych rekordach (kursor zapisany, następny przebieg pójdzie dalej) | szybka próba po zmianie źródła: `pobierz --limit 20` |
| `--budzet S` | koniec po S sekundach, równo po zapisanej stronie; nic się nie gubi | cron na hostingu co kilka minut: `pobierz --budzet 240`; lokalnie „tylko godzinę”: `--budzet 3600` |
| `--od-poczatku` | ignoruje kursor i idzie od początku źródła; niezmienione rekordy tylko się porównują (zmienione dostają nowe `changed_at`) | źródło poprawiło stare orzeczenia albo zmienił się zakres zapytania |
| `--na-sucho` | pobiera i wypisuje rekordy na ekran, nic nie zapisuje do bazy (zawsze od początku) | sprawdzenie, czy źródło odpowiada i co zwraca: `pobierz --na-sucho --limit 5` |
| `--brakujace` | pobiera tylko rekordy, które `sprawdz` oznaczył jako brakujące; kursora nie rusza | po `sprawdz` z brakami: `pobierz --brakujace` |

NSA: `pobierz` bierze numery orzeczeń ze wszystkich plików w `nsa/database/cbosa-id/` (też podkatalogi i pliki `.part` w trakcie zbierania) i pobiera strony `/doc/<ID>` z CBOSA, co najmniej 3 s przerwy. Bez kursora: pomija numery już pobrane z CBOSA, więc przerwany przebieg albo strona, która nie przyszła, idą przy następnym. `--od-poczatku` = pobierz jeszcze raz wszystkie z plików (np. po miesiącach, gdy mogły dojść uzasadnienia). `--limit N` = N orzeczeń. Zbiór JuDDGES (CBOSA do 5.03.2025) jest już wczytany; `przetworz` bierze oba źródła, strona z CBOSA wygrywa z JuDDGES dla tego samego orzeczenia.

ETPC: sposób łączenia z HUDOC z `ETPC_TRANSPORT` (`requests`, `curl`, `playwright`; puste = domyślny łańcuch projektu po kolei, następny przy blokadzie Cloudflare; w `.env` jest `playwright`). Jednorazowo inny: `docker compose run --rm --no-deps -e ETPC_TRANSPORT=curl www python -m etpc pobierz --limit 10`.

## Przetwarzanie (`przetworz`: raw_records -> tabele projektu)
Bez opcji bierze tylko rekordy nowe i zmienione od ostatniego przetworzenia. Niczego nie pobiera ze źródła.

| Opcja | Co daje | Kiedy / przykład |
|---|---|---|
| `--od-poczatku` | przetwarza wszystkie rekordy jeszcze raz | po zmianie parsera (nowe pole, poprawka wyciągania sygnatury); NSA to wiele godzin, najlepiej w tle z `--name` |
| `--tylko-pochodne` | nie rusza rekordów, tylko przelicza dane pochodne (ETPC: statystyki, ranking cytowań, wagi orzeczeń; NSA: statystyki) | zmienił się sposób liczenia statystyk: `przetworz --tylko-pochodne` (minuty zamiast godzin) |
| `--porcja N` | ile rekordów idzie w jednej transakcji (domyślnie 100); większa = szybciej, ale dłuższe transakcje i więcej pamięci | szybka maszyna lokalnie: `--porcja 500`; słaby hosting: `--porcja 50` |
| `--limit N` | koniec po N rekordach | test nowego parsera: `przetworz --od-poczatku --limit 200`, potem obejrzeć wynik na stronie |
| `--budzet S` | koniec po S sekundach (wznawialne) | cron: `przetworz --budzet 240` |

## Wektory (Qdrant, kolekcje `etpc_<model>`, `nsa_<model>`)
`wektory` bez opcji liczy embeddingi orzeczeń, które jeszcze ich nie mają albo zmieniły się od ostatniego liczenia. `wektory` liczy **tylko** LM Studio z bge-m3 (karta graficzna), nigdy CPU; zwykle hurt liczy się na CUDA gdzie indziej (Vast, `paczki`). Pozostałe wektory (wyszukiwanie, MCP, zakładki dokumentów, teczka, trening) liczą się tak samo: jeśli LM Studio działa i ma wczytany bge-m3 (`<PREFIKS>_EMBED_MODEL`), liczy LM Studio, inaczej bge-m3 na CPU w procesie; sprawdzane co minutę (`<PREFIKS>_QUERY_EMBEDDERS=auto`, `local` / `http` = tylko jedno). Ma osobną blokadę, więc może iść równolegle z `przetworz`.

NSA ma dwa poziomy (`nsa/embed.py` `full_chunks`): **pełne fragmenty** tylko dla uchwał, orzeczeń z tezą lub publikacją i wyroków NSA (~270 tys. orzeczeń, ~3,7 mln wektorów); **reszta (postanowienia, wyroki WSA, ~2 mln) po jednym wektorze skrótu**: teza + koniec sentencji (rozstrzygnięcie) + początek „zważył, co następuje” (~2000 znaków). Razem ~5,7 mln wektorów zamiast ~18 mln; liczone w paczkach na wynajętych kartach (`docker/licz/VAST.md`).

| Polecenie / opcja | Co daje | Kiedy / przykład |
|---|---|---|
| `wektory --budzet 3600` | liczy najwyżej godzinę, potem koniec; następny przebieg idzie dalej | żeby komputer nie mielił całą noc: `wektory --budzet 3600` |
| `wektory --limit N` | koniec po N orzeczeniach | test po zmianie modelu / LM Studio: `wektory --limit 50` |
| `wektory --od-poczatku` | liczy wszystko od nowa | po zmianie krojenia na fragmenty (chunker); po zmianie modelu i tak powstaje nowa kolekcja |
| `paczki` | wycina 1 paczkę fragmentów (200000) do policzenia na innym komputerze; te orzeczenia `wektory` tutaj pomija | kolega z kartą NVIDIA ma czas: `paczki`, potem wysłać plik `<projekt>/database/paczki/nsa-0001.jsonl.gz` |
| `paczki --ile 3 --fragmentow 100000` | 3 mniejsze paczki | kilku kolegów albo słabsze karty: mniejsze paczki = krótsze liczenie i mniej strat przy przerwaniu |
| `paczki --od-konca` | paczka z końca kolejki (najmniej ważne orzeczenia); najważniejsze zostają dla `wektory` tutaj | wektory liczą się tu i tam naraz: tutaj od góry, kolega od dołu |
| `paczki --lista` | wszystkie paczki: wydane, wczytane, porzucone | „czyja paczka jeszcze nie wróciła?” |
| `paczki --zwolnij nsa-0002` | porzuca paczkę: jej orzeczenia wracają do kolejki `wektory` tutaj | kolega nie odda paczki |
| `paczka-import nsa-0001` | wczytuje plik `nsa-0001.wektory.npy` do Qdranta (z kontrolą, czy liczył ten sam model) | po otrzymaniu pliku od kolegi: włożyć go do `nsa/database/paczki/` i wtedy `paczka-import nsa-0001` |

Liczenie paczki u kolegi z kartą NVIDIA: `docker/licz/README.md`, wynajęte GPU: `docker/licz/VAST.md`. Panel Qdranta: http://127.0.0.1:6333/dashboard

Stan indeksu kolekcji (Git Bash): `curl -s localhost:6333/collections/nsa_text-embedding-bge-m3 | grep -o '"status":"[a-z]*"\|"indexed_vectors_count":[0-9]*'` (`yellow` = Qdrant buduje indeks; `green` + `indexed_vectors_count` ~ liczba punktów = gotowe); postęp segmentów: `curl -s localhost:6333/collections/nsa_text-embedding-bge-m3/optimizations | grep -o '"queued_[a-z]*":[0-9]*'`. Przy masowym imporcie paczek indeksowanie można wyłączyć (`PATCH .../collections/<kolekcja>` z `{"optimizers_config":{"indexing_threshold":0}}`, import ~10x szybszy) i po imporcie włączyć z powrotem (`10000`); pusty `{"optimizers_config":{}}` budzi optymalizator.

## Szukanie i RAG
| Polecenie / opcja | Co daje | Kiedy / przykład |
|---|---|---|
| `fragmenty "pytanie"` | sam kontekst bez modelu: najlepsze fragmenty orzeczeń w limicie 16000 znaków, z nagłówkami `[n] sprawa, data, link` | sprawdzić, co dostałby model: `etpc fragmenty "odpowiedzialność portalu za komentarze"` |
| `--limit-znakow N` (przy `fragmenty`, `zapytaj`, `ocen`, `ocen-odpowiedzi`) | ile tekstu źródeł; więcej = więcej orzeczeń, ale mały model gubi się w długim kontekście | model 14B: `zapytaj "..." --limit-znakow 8000`; Claude / duży model: `--limit-znakow 30000` |
| `zapytaj "pytanie"` | pełna odpowiedź RAG: fragmenty + model czatu z LM Studio (`<PREFIKS>_CHAT_*`), z cytowaniami `[n]` | `nsa zapytaj "czy wiata to budowla w rozumieniu prawa budowlanego?"` (LM Studio z modelem musi działać; minuty) |
| `zapytaj "..." --tylko-zrodla` | wypisuje gotowy prompt (polecenie + źródła), modelu nie woła | wkleić prompt do innego czatu albo sprawdzić, czy źródła są dobre, zanim się poczeka na model |
| `ocen` | dla każdego pytania z `<projekt>/pytania.json`: na którym miejscu jest oczekiwane orzeczenie (MRR) i ile zmieściło się w kontekście; opcjonalne pole `"szum": [numery]` przy pytaniu = sprawy, które nie powinny wyjść - `ocen` liczy, ile weszło do kontekstu („szum w kontekście”, mniej = lepiej); bez modelu, sekundy | po zmianie pokrętła w `judgments_core/rag.py` albo słowniczka: `ocen` przed i po, porównać |
| `ocen --claude [MODEL]` | Claude (API, płatne) ocenia 0-3 każde z pierwszych orzeczeń kontekstu (do 10) pod kątem pytania, z krótkim uzasadnieniem: średnia ocena, % trafnych (>= 2), nDCG@10, czy „3” jest na 1. miejscu; oceny oczekiwanych = sprawdzian sędziego. Widzi też dobre wyniki spoza listy wzorcowej. Zapis: `<projekt>/database/oceny/<czas>-<model>.json` | przed i po większej zmianie wyszukiwania: `etpc ocen --claude claude-sonnet-5-5 --max-usd 1` (~$0,01 na pytanie) |
| `ocen --pytania 1,4` | tylko te pytania (numery z pliku), też z `--claude` | |
| `ocen --plik P` | inne pytania wzorcowe | własny zestaw (ten sam format co `pytania.json`), np. `etpc ocen --plik etpc/pytania-art8.json` |
| `ocen-odpowiedzi` | wszystkie pytania przez `zapytaj`: czy cytowania istnieją, czy podany wynik sprawy zgadza się z bazą, a model-sędzia sprawdza zdania ze źródłami | ocena nowego modelu czatu (długo: kilka minut na pytanie) |
| `ocen-odpowiedzi --pytania 1,4,11` | tylko te pytania (numery z pliku) | szybki test na kilku pytaniach zamiast całego zestawu |
| `ocen-odpowiedzi --bez-sedziego` | tylko reguły (cytowania, wynik), bez drugiego wołania modelu (szybciej) | gdy sędzia = ten sam słaby model albo chodzi tylko o cytowania |
| `podobne-do plik` | orzeczenia najbliższe **całemu tekstowi** (np. własnemu orzeczeniu z uzasadnieniem): tekst cięty jak baza, każdy fragment szuka osobno po znaczeniu, wyniki łączone; przy każdym orzeczeniu „Twoje fragmenty 2, 5 ~ …” = które Twoje akapity pasują; na końcu liczba wyników (oddalono / uchylono, Violation / No violation). Tekst zostaje na komputerze (model lokalny). `-` = plik ze standardowego wejścia (może leżeć poza repo, potrzebne `-T`) | `docker compose run --rm --no-deps -T www python -m nsa podobne-do - < moje.txt` |
| `podobne-do plik --ile N` | ile orzeczeń (domyślnie 10) | `--ile 30` |
| `podobne-do plik --wynik TEKST` | tylko z takim wynikiem (podciąg, bez wielkości liter): przeciwne rozstrzygnięcia tego samego problemu są znaczeniowo tak samo bliskie, więc tu się je rozdziela. Uwaga: `violation` łapie też „No violation” | NSA: `--wynik oddal`, `--wynik uchyl`; ETPC: `--wynik "no violation"` |
| `mcp` | serwer MCP po stdio: narzędzia `szukaj`, `fragmenty`, `orzeczenie`, `podobne` | ręcznie niepotrzebne: Claude Code / LM Studio uruchamia go sam z `.mcp.json` |

## Tematy (`tematy`, BERTopic: UMAP + HDBSCAN + słowa c-TF-IDF)
Na razie tylko ETPC. Liczy lokalnie na CPU (minuty do kilkudziesięciu minut). Wektory czyta z Qdranta raz i trzyma w `<projekt>/database/tematy/<kolekcja>/` (~5 GB, poza gitem). Czyta je od nowa, gdy w Qdrancie zmieni się liczba punktów. Każde przeliczenie poziomu zastępuje jego tematy i numery (tabele `topics`, `ruling_topics`, `chunk_topics`).

| Polecenie / opcja | Co daje | Kiedy / przykład |
|---|---|---|
| `tematy` | poziom A = tematy orzeczeń (wektor orzeczenia = średnia fragmentów bez zdań odrębnych i sentencji), poziom B = tematy fragmentów części THE LAW (zagadnienia prawne przecinające sprawy); potem nazwy po polsku od modelu czatu | pierwszy raz albo po dużej dostawie nowych wektorów |
| `--poziom A` / `B` / `oba` | tylko jeden poziom (domyślnie oba) | strojenie jednego poziomu |
| `--min-temat N` | najmniejszy temat HDBSCAN (domyślnie A 40, B 150): mniej = więcej, drobniejszych tematów | `--poziom A --min-temat 80`, gdy tematów za dużo |
| `--min-probek N` | `min_samples` HDBSCAN (domyślnie A 10, B 15): więcej = ostrożniej, więcej elementów poza tematami w dopasowaniu (i tak trafiają potem do najbliższego tematu) | |
| `--probka N` | na ilu elementach dopasować (domyślnie A wszystkie, B 150000; 0 = wszystkie); reszta idzie do najbliższego środka tematu | B na całości: `--poziom B --probka 0` (dużo RAM i czasu) |
| `--odswiez` | wektory z Qdranta od nowa, bez pamięci podręcznej | gdy liczba punktów się nie zmieniła, a wektory tak (np. `wektory --od-poczatku`) |
| `--bez-nazw` | bez modelu czatu: tematy mają tylko słowa kluczowe | LM Studio nie ma wczytanego modelu czatu |
| `--tylko-nazwy` | nic nie liczy, nazywa zapisane tematy bez nazwy (wznawia: każda nazwa zapisuje się od razu) | po `--bez-nazw` albo po przerwaniu |
| `--tylko-nazwy --nazwy-od-nowa` | nazywa wszystkie od nowa | inny, lepszy model czatu |
| `--claude [MODEL]` | nazwy od Claude'a przez API zamiast LM Studio (klucz `ANTHROPIC_API_KEY` w `.env`, płatne kredytami z platform.claude.com); bez nazwy modelu = `<PREFIKS>_CLAUDE_MODEL`; na końcu tokeny i szacowany koszt | `tematy --tylko-nazwy --claude claude-sonnet-5-5 --max-usd 3` (424 tematy ETPC ~$2,25; Opus 5.5 ~$4,50) |
| `--max-usd USD` | z `--claude`: przerywa, gdy szacowany koszt przekroczy kwotę (nazwane zostają, reszta przy następnym `--tylko-nazwy`) | bezpiecznik przy małym saldzie |
| `--ile N` | nazywa najwyżej N tematów na poziom (największe najpierw) | próba modelu: `--tylko-nazwy --poziom A --ile 5 --claude claude-sonnet-5-5` (~$0,03); porównanie dwóch modeli: drugi raz z `--nazwy-od-nowa` |
| `--lista` | wypisuje tematy: numer (`A12`), liczba, nazwa, opis, słowa, najczęstsze artykuły i państwa, 3 sprawy najbliższe środkowi | ręczna ocena jakości: `tematy --lista > tematy.txt` |

## Strony WWW
| Polecenie | Co robi |
|---|---|
| http://127.0.0.1:8080 | portal (usługa `www`, działa sama): `/etpc/`, `/nsa/`, statystyki `/etpc/statystyki`, `/nsa/statystyki` |
| `www --host 0.0.0.0 --port 8081` | jeden projekt sam; z kontenera trzeba wystawić port: `docker compose run --rm --no-deps -p 127.0.0.1:8081:8081 www python -m etpc www --host 0.0.0.0 --port 8081` (8080 zajmuje `www`) |

## Formułki (`formulki`: zdania pomijane w zakładkach dokumentów, ETPC i NSA)
Formułka nie jest ani argumentem, ani odpowiedzią na argument, i nie mówi nic o autorze. Analiza dokumentu (`/analiza`: wyszukiwanie podobnych orzeczeń i znaczniki bez formułek, które ściągałyby orzeczenia z tą samą formułką), Porównanie dokumentów, Analiza sprawy (`/sprawa`) i Badanie (`/badanie`: miary, znaczniki, styl i tekst dla Claude'a liczone bez formułek; w CSV dokumentów kolumna `pominiete_formulki`; tekst bez roli = orzeczenie, gdy ma „zważył, co następuje” / „THE LAW” / „EN DROIT” / „PRAWO”) pomijają je i wypisują w raporcie z powodem („pominięte jako formułka”); w formularzach Analizy dokumentu, Porównania i Badania pole „pomiń formułki” (domyślnie zaznaczone). Trzy sposoby: (1) **lista** - zdanie powtarzane dosłownie w wielu orzeczeniach bazy (`nsa/formulki.txt`, `etpc/formulki.txt`, niżej); (2) **wzorce** całych zdań/wierszy w `<projekt>/formulas.py` (ETPC po angielsku, francusku i dla polskich tłumaczeń: nagłówek, skład, procedura, dopuszczalność, art. 41, sentencja, „Done in English…”; części listu wspólne w `boilerplate.LETTER_PATTERNS`); NSA: osobno dla sądu (`COURT_PATTERNS`: nagłówek, skład, sentencja, ocena środka, „zważył”, art. 1 p.u.s.a./3, 134, 174, 183 p.p.s.a., koszty, zakończenie) i dla pisma strony / decyzji organu (`BRIEF_PATTERNS`: nagłówki, wstęp, petitum, podpis, załączniki, pouczenie) - wzorzec musi objąć całe zdanie, więc „Skarga nie zasługuje na uwzględnienie, bo …” zostaje; (3) **podobne** (tylko tekst sądu): zdanie z wektorem bge-m3 bliskim zdaniu listy (cosinus >= 0,88, `boilerplate.NEAR`); wektory listy liczone w tle przy starcie portalu (~1 min CPU). Porównanie po normalizacji: małe litery, bez nawiasów (publikator, sygnatury), liczby jako 0, jednakowe cudzysłowy i myślniki. Lista: `nsa/formulki.txt` (w repo, `<w ilu orzeczeniach próbki>	<zdanie>`); usunięty wiersz = zdanie przestaje być pomijane. Po zmianie listy: `docker compose restart www`. Kod: rdzeń `judgments_core/boilerplate.py` (`Formulas`, `FormulaCorpus` z `load()`), dzieci `NsaFormulas`, `EtpcFormulas`; zakładki dokumentów składa wspólnie `judgments_core/documents.py` `DocumentPages`.

| Polecenie | Co robi |
|---|---|
| `formulki` | losowa próbka uzasadnień (po kluczu, bez skanowania tabeli), zapis listy; na koniec 20 najczęstszych |
| `--probka N` | ile orzeczeń przeczytać (domyślnie 50 000) |
| `--min N` | formułka = zdanie w co najmniej N orzeczeniach próbki (domyślnie 50 = 0,1%; niżej wchodzą zdania merytoryczne serii spraw) |
| `--ziarno N` | inne losowanie próbki (domyślnie 1) |

## Teczka sprawy (`/etpc/teczka`, `/nsa/teczka`: ta sama baza)
Dokumenty użytkownika zapisane raz: tekst odczytany przy imporcie (Word, PDF, skan i zdjęcie przez OCR, tekst), oryginał pliku i **wektory fragmentów** (akapity, rozważania sądu, fragmenty jak w bazie projektu; tabela `document_vectors`, klucz = skrót tekstu fragmentu), w zagnieżdżonych katalogach. Analizy dokumentów z teczki biorą gotowe wektory (brakujące liczą i dopisują); dokumenty wrzucone wprost do analizy liczą się na bieżąco i nic nie zostawiają. Na stronie dokumentu „Policz wektory” = brakujące (np. dla dokumentów sprzed 2026-10-29). W Analizie dokumentu, Porównaniu, Analizie sprawy (`/sprawa`, kilka dokumentów bez zapisu) i Badaniu wybierasz je z listy „albo z teczki” - bez ponownego OCR. „Analizuj zaznaczone” = Analiza sprawy zaznaczonych dokumentów w kolejności dat (rola i data ustawiane na stronie dokumentu). Baza MariaDB `teczki` (`<PREFIKS>_LIBRARY_DSN`, puste = `teczki` na serwerze z `DB_DSN`), tylko lokalnie, poza gitem i poza `narzedzia/kopia-na-dysk.sh`.

| Polecenie | Co robi |
|---|---|
| `docker exec orzecznictwo-szkielet-db-1 mariadb -uroot -proot -e "CREATE DATABASE IF NOT EXISTS teczki CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; GRANT ALL PRIVILEGES ON teczki.* TO 'szkielet'@'%';"` | raz, na bazie utworzonej przed 2026-10-28 (nowe wolumeny dostają ją z `docker/mariadb-init.sql`) |
| `docker compose run --rm --no-deps www python -m etpc migracje` | tworzy też tabele teczki (strona robi to sama przy pierwszym wejściu) |
| `docker exec orzecznictwo-szkielet-db-1 mariadb-dump -uroot -proot teczki \| gzip > teczki.sql.gz` | ręczna kopia teczki (trzymać zaszyfrowaną: dokumenty klientów) |

## Trening modeli (`/etpc/trening`, `/nsa/trening`: ta sama baza, tylko `APP_ENV=dev`)
Dokumenty do nauki wzorców (jak zaczyna się i kończy skarga do WSA, odpowiedź SKO, wyrok ...), żeby porównanie wiedziało, co odsiać: skargi, orzeczenia, odpowiedzi na skargę, pisma organu i stron, w osobnej bazie MariaDB `trening` (`<PREFIKS>_TRAINING_DSN`, puste = `trening` na serwerze z `DB_DSN`). Zakładka „Trening” pojawia się obok „Statystyk” tylko przy `APP_ENV=dev`; przy `prod` jej adresy dają 404. Działa jak Teczka sprawy (import Word/PDF/skanu przez OCR, katalogi, oryginał pliku), ale zamiast „kto pisze” jest **rodzaj** dokumentu i bez analizy sprawy. Przy imporcie od razu: OCR, tekst i oryginał do bazy oraz **wektory fragmentów do bazy** (`document_vectors` w bazie `trening`; „Policz wektory” na stronie dokumentu = brakujące). **Eksport JSONL** (link na stronie; `/trening/eksport.jsonl`, `?k=<katalog>` = katalog z podkatalogami): jeden dokument na wiersz, pola `id, katalog, tytul, rodzaj, rodzaj_opis, data, plik, uwaga, tekst`.

| Polecenie | Co robi |
|---|---|
| `docker exec orzecznictwo-szkielet-db-1 mariadb -uroot -proot -e "CREATE DATABASE IF NOT EXISTS trening CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; GRANT ALL PRIVILEGES ON trening.* TO 'szkielet'@'%';"` | raz, na bazie utworzonej przed 2026-10-29 (nowe wolumeny dostają ją z `docker/mariadb-init.sql`) |
| `docker compose run --rm --no-deps www python -m etpc migracje` | przy `APP_ENV=dev` tworzy też tabele bazy `trening` (strona robi to sama przy pierwszym wejściu) |

## Typowe sekwencje (`orz` = skrót z góry)
```
# nowe orzeczenia od źródła aż do wyszukiwarki
orz etpc pobierz;  orz etpc przetworz;  orz etpc wektory

# zmiana parsera
orz nsa kopia;  orz nsa przetworz --od-poczatku --limit 200   # próba, obejrzeć na stronie
orz nsa przetworz --od-poczatku                                # całość (w tle: run -d --name)

# dziury w bazie
orz nsa sprawdz --od 2015;  orz nsa stan --problemy;  orz nsa pobierz --brakujace;  orz nsa przetworz

# strojenie RAG
orz etpc ocen   # przed zmianą, zapisać wynik; zmienić jedno pokrętło; orz etpc ocen ponownie
```

## Testy, lint, kopia na dysk
```
docker compose run --rm --no-deps www python -m pytest                          # ~4 min
docker compose run --rm --no-deps www sh -c "ruff format . && ruff check ."     # przed commitem
narzedzia/kopia-na-dysk.sh "/g/projekty AI/orzecznictwo-szkielet" nsa etpc      # Git Bash: lustro, bundle, Qdrant, bazy
```

## CBOSA: zbieranie numerów orzeczeń skryptem (`narzedzia/cbosa_id.py`)
**Uwaga: administrator strony tego zabrania, technicznie jest to możliwe** (szczegóły w sekcji o Postmanie niżej: `robots.txt` wyklucza wyszukiwarkę i listy wyników dla automatów, skrypt to automat tak samo jak Postman). Osobne narzędzie uruchamiane przez użytkownika, nie część projektu; projekt sam pobiera tylko dozwolone `/doc/<ID>`.

Zakres dat dzieli na okresy (domyślnie miesiące), każdy okres = jedno wyszukiwanie w CBOSA, przejście po wszystkich stronach wyników i plik z numerami:
```
nsa/database/cbosa-id/
  2025/
    2025-03-05_2025-03-31.txt     # niepełny miesiąc: daty w nazwie
    2025-04.txt                   # cały miesiąc
    2025-05.txt.part              # w trakcie zbierania (pobierz już go czyta)
    2025-06_nsa_wyrok.txt         # z filtrami: --sad NSA --rodzaj wyrok
```
Okresy z gotowym `.txt` są przy następnym uruchomieniu pomijane, więc przerwane zbieranie wystarczy puścić jeszcze raz (niedokończony okres zbiera się od nowa). `python -m nsa pobierz` można puszczać w trakcie, obok.

Transport: łańcuch `requests` -> `curl` -> `playwright`; gdy bieżący 3 razy z rzędu nie dostanie poprawnej strony (błąd sieci, 5xx, pusta odpowiedź), przejmuje następny (z nową sesją, wyszukanie od nowa i ta sama strona). Gdy żaden nie działa: koniec, zebrane okresy zostają.

Przykłady (każdy z przedrostkiem `docker compose run --rm --no-deps www`):
```
python narzedzia/cbosa_id.py --od 2025-03-05 --do 2025-12-31 --kontakt TWOJ@EMAIL   # wszystko od końca zbioru JuDDGES
python narzedzia/cbosa_id.py --od 2025-03-05 --do 2025-12-31 --na-sucho             # tylko pokaż okresy i pliki
python narzedzia/cbosa_id.py --od 2025-03-01 --do 2025-03-31 --maks-stron 2         # próba: 2 strony wyników
python narzedzia/cbosa_id.py --od 2023-01-01 --do 2024-12-31 --rodzaj wyrok --uzasadnienie tak   # wyroki z uzasadnieniem
python narzedzia/cbosa_id.py --od 2025-01-01 --sad "WSA w Krakowie" --okres tydzien  # jeden sąd, tygodniami, do dziś
python narzedzia/cbosa_id.py --od 2025-03-05 --transport curl,playwright            # bez requests
```

| Opcja | Co daje |
|---|---|
| `--od RRRR-MM-DD` | data orzeczenia od (wymagane) |
| `--do RRRR-MM-DD` | data orzeczenia do; domyślnie dziś |
| `--okres miesiac\|tydzien\|dzien` | na jakie kawałki dzielić zakres; jedno wyszukiwanie i jeden plik na kawałek (domyślnie `miesiac`; `tydzien`/`dzien` gdy miesiąc ma bardzo dużo wyników albo wyszukiwanie się urywa) |
| `--sad` | `NSA`, `WSA w Krakowie` (skrót rozwijany do pełnej nazwy) albo pełna nazwa z formularza CBOSA, np. `NSA oz. w Krakowie` |
| `--rodzaj wyrok\|postanowienie\|uchwala` | tylko ten rodzaj |
| `--uzasadnienie tak\|nie` | tylko z uzasadnieniem / tylko bez |
| `--slowa`, `--gdzie` | słowa w treści; gdzie: `gdziekolwiek` (domyślnie), `w sentencji`, `w tezach`, `w uzasadnieniu` |
| `--sygnatura`, `--symbol`, `--sedzia`, `--hasla` | jak pola formularza CBOSA (np. `--sygnatura "II SA/Wa"`, `--symbol 6110`) |
| `--maks-stron N` | najwyżej N stron wyników na okres (próba); plik zostaje jako `.part`, więc następne uruchomienie zbierze okres w całości |
| `--przerwa S` | sekundy między zapytaniami, najmniej 3 (domyślnie 3) |
| `--transport` | łańcuch po przecinku, domyślnie `requests,curl,playwright` |
| `--kontakt EMAIL` | dopisany do User-Agent (zalecane; `playwright` ma własny User-Agent przeglądarki) |
| `--katalog` | gdzie zapisywać; domyślnie `nsa/database/cbosa-id` (tam czyta `pobierz`) |
| `--nadpisz` | zbierz jeszcze raz także okresy z gotowym plikiem |
| `--na-sucho` | tylko lista okresów i plików, bez zapytań do CBOSA |

Strona wyników CBOSA ma 10 orzeczeń: rok to kilkadziesiąt tysięcy orzeczeń = kilka tysięcy stron = kilka godzin przy 3 s. Długie zbieranie najlepiej w tle: `docker compose run -d --rm --no-deps --name cbosa-id www python narzedzia/cbosa_id.py --od ...`, podgląd `docker logs -f cbosa-id`. Adres stron wyników (`/cbo/find?p=N`) wzięty z linku „Powrót do listy” na stronie orzeczenia; samego przechodzenia po wynikach nie sprawdzałem na żywo (zgodnie z podziałem: to uruchamia użytkownik) - przy pierwszym użyciu najpierw próba z `--maks-stron 2`.

## CBOSA: zbieranie numerów orzeczeń w Postmanie
Kolekcja `narzedzia/cbosa.postman_collection.json`: wyszukuje w CBOSA i zbiera numery `/doc/<ID>` ze wszystkich stron wyników. Same orzeczenia pobiera potem projekt (`/doc/<ID>` jest dozwolone w `robots.txt`).

**Uwaga: administrator strony tego zabrania, technicznie jest to możliwe.** `robots.txt` CBOSA (sprawdzony 2026-10-22) ma `Disallow: /cbo/search`, `/cbo/find`, `/cbo/do/search`, `/cbo/do/find` dla wszystkich automatów; Collection Runner przechodzący po stronach wyników to właśnie automat (ręczne pojedyncze wyszukiwanie w przeglądarce już nie). Nic tego technicznie nie blokuje (brak Cloudflare, logowania, limitu widzianego przy spokojnym tempie). Decyzja i odpowiedzialność użytkownika; jeśli już, to wolno (3 s+ między stronami), z kontaktem w User-Agent, bez proxy i rotacji IP. Strony `/doc/<ID>` są dozwolone.

**Zanim uruchomisz:**
- W zmiennej `user_agent` wpisz swój kontakt (domyślnie jest `UZUPELNIJ-EMAIL`).
- Zakres dat (`odDaty`, `doDaty`) najlepiej ustawiać po jednym miesiącu.
- Gdy trafisz na zepsuty serwer (błędy TLS albo kod 500): w pliku `C:\Windows\System32\drivers\etc\hosts` (Notatnik uruchomiony jako administrator) dopisz linię `194.181.28.1 orzeczenia.nsa.gov.pl`, po zbieraniu ją usuń.

1. Postman: **Import** -> plik kolekcji.
2. Kolekcja -> **Variables**: ustawić `user_agent` (kontakt), zakres dat `odDaty`/`doDaty` (RRRR-MM-DD, domyślnie marzec 2025), ewentualnie `sad` (pełna nazwa z formularza, np. `Wojewódzki Sąd Administracyjny w Warszawie`), `rodzaj` (`Wyrok`, `Postanowienie`, `Uchwała`), `slowa`, `maks_stron` (0 = wszystkie). Filtry uzasadnienia/prawomocności: włączyć wiersz w Body zapytania „1. Wyszukaj”.
3. **Run collection** (Runner), oba zapytania po kolei, **Delay 3000 ms**. Postęp w konsoli (View -> Show Postman Console): `strona N: ... nowych, razem`.
4. Po „KONIEC”: Variables -> wartość `ids` (jedno ID w linii) skopiować do pliku `nsa/database/cbosa-id/<rok>/<zakres>.txt`, potem `python -m nsa pobierz`.

Zmienne:
| Zmienna | Co daje |
|---|---|
| `odDaty`, `doDaty` | zakres daty orzeczenia; lepiej po miesiącu (krótsze przebiegi, mniej do powtórzenia po błędzie) |
| `sad`, `rodzaj`, `slowa`, `sygnatura` | jak w formularzu CBOSA; `dowolny` = bez filtra |
| `maks_stron` | zatrzymaj po tylu stronach (0 = do końca wyników) |
| `przerwa_ms` | dodatkowa przerwa przed każdą stroną (domyślnie 3000) |
| `ids` | wynik; czyszczony przy każdym „1. Wyszukaj” |

Kończy się samo, gdy strona nie da nowych ID, powtórzy poprzednią albo zwróci błąd. Jeden z dwóch serwerów CBOSA jest zepsuty (błędy TLS / 500): wtedy w pliku `C:\Windows\System32\drivers\etc\hosts` (jako administrator) dopisać `194.181.28.1 orzeczenia.nsa.gov.pl` i usunąć po zbieraniu.
