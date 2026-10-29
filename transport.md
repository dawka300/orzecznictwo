# Jak pobieramy strony: Python → curl → przeglądarka

Opis „strategii transportu”, czyli sposobu, w jaki program dostaje się do strony
źródła (HUDOC, CBOSA), także wtedy, gdy strona blokuje roboty (Cloudflare).
Dla każdego: co to jest, po co, w których plikach i jak tego używać.

---

## 1. W jednym zdaniu

Program próbuje pobrać stronę **zwykłym Pythonem** (`requests`). Gdy Cloudflare go
zatrzymuje, przechodzi na **curl** (systemowy program do pobierania, który Cloudflare
częściej przepuszcza). Gdy zatrzymany jest też curl, uruchamia **prawdziwą przeglądarkę**
Chromium bez okna (Playwright), a ona wchodzi na stronę tak jak człowiek.

```
requests (Python)  --blokada-->  curl  --blokada-->  playwright (Chromium)
   najszybszy                    szybki               wolny, ale najskuteczniejszy
```

## 2. Dlaczego trzy sposoby

Cloudflare ocenia, czy łączy się człowiek, czy robot. Patrzy m.in. na „odcisk palca”
połączenia szyfrowanego (TLS): każdy program przedstawia się trochę inaczej.

| Sposób | Jak wygląda dla Cloudflare | Szybkość | Kiedy wystarcza |
|---|---|---|---|
| `requests` | biblioteka Pythona: łatwo ją rozpoznać | najszybszy | strony bez Cloudflare (np. CBOSA) |
| `curl` | inny „odcisk” TLS, często przepuszczany | szybki (osobny proces na każde zapytanie) | HUDOC zwykle tak |
| `playwright` | prawdziwa przeglądarka Chromium, uruchamia skrypty strony | wolny, ~500 MB | gdy Cloudflare blokuje i curl |

Zaczynamy od najtańszego i schodzimy niżej tylko wtedy, gdy trzeba.

## 3. Kto za co odpowiada: dwa piętra

Pobieranie ma **dwa niezależne piętra** (to wzorzec projektowy „strategia”):

1. **Kiedy i ile razy pytać:** `PoliteClient` w `judgments_core/http.py`.
   Pilnuje przerwy między zapytaniami, ponawia po błędach, czeka po blokadzie,
   zapisuje zdarzenia. To samo dla każdego źródła.
2. **Jak zapytanie fizycznie dociera do strony:** *transport* w
   `judgments_core/transport.py` (oraz `judgments_core/browser.py`). Wykonuje jedno
   pobranie (GET) i niczego nie decyduje.

Dzięki temu można wymienić sposób pobierania bez ruszania logiki uprzejmości
i ponawiania. Źródło (np. `HudocSource`) w ogóle nie wie, którym transportem idzie.

## 4. Mapa plików

| Plik | Co jest w środku |
|---|---|
| `judgments_core/transport.py` | rodzic `Transport` i strategie: `RequestsTransport`, `CurlTransport`, łańcuch `FallbackTransport`; słownik `TRANSPORTS` (nazwa → klasa), `make_transport(nazwy)`, rozpoznanie blokady `is_cloudflare_challenge` |
| `judgments_core/browser.py` | `PlaywrightTransport`: przeglądarka Chromium bez okna; importowana dopiero wtedy, gdy łańcuch do niej dojdzie |
| `judgments_core/http.py` | `PoliteClient`: przerwy, ponawianie, czekanie po blokadzie, zdarzenia |
| `judgments_core/cli.py` | `Project.transports` (domyślnie tylko `requests`) i `_make_source`: buduje łańcuch, klienta i źródło |
| `etpc/__main__.py` | łańcuch ETPC: `transports=("requests", "curl", "playwright")` |
| `nsa/__main__.py` | NSA nie ustawia łańcucha, więc tylko `requests` (CBOSA nie stoi za Cloudflare) |
| `judgments_core/settings.py` | ustawienie `transport` (zmienna `<PREFIKS>_TRANSPORT`), `pause_s`, `max_retries`, `timeout_s` |
| `docker/www.Dockerfile` | instaluje `curl` oraz Playwright z Chromium w obrazie `www` |
| `requirements-dev.txt` | `playwright`: tylko lokalnie, nie na hostingu |
| `judgments_core/migrations/002_logs.sql` | tabela `fetch_events`: blokady, błędy i przełączenia transportu |
| `tests/test_http.py` | testy klienta, curl i łańcucha (bez sieci) |
| `narzedzia/cbosa_id.py` | osobne narzędzie użytkownika z **własnym**, podobnym łańcuchem (punkt 9) |

## 5. Jak działa łańcuch krok po kroku

`FallbackTransport` w `judgments_core/transport.py`:

1. Na starcie tworzy **tylko pierwszy** transport (`requests`). Następne powstają
   dopiero, gdy są potrzebne, więc przeglądarka nie startuje bez potrzeby.
2. Każda odpowiedź jest sprawdzana: **blokada Cloudflare** = HTTP 403 z nagłówkiem
   `cf-mitigated: challenge` (`is_cloudflare_challenge`).
3. Pierwsza blokada: łańcuch oddaje odpowiedź do `PoliteClient`, który odczekuje
   (30 s × numer próby) i ponawia tym samym transportem.
4. **Dwie blokady z rzędu** (`switch_after=2`): łańcuch zamyka bieżący transport,
   przechodzi na następny i **od razu powtarza to samo zapytanie** nowym sposobem.
   W logu pojawia się: `transport: requests blokowany przez Cloudflare - przechodzę na curl`,
   a w `fetch_events` zdarzenie typu `transport`.
5. Udane pobranie zeruje licznik blokad.
6. W trakcie jednego przebiegu łańcuch **nie wraca** do wcześniejszego sposobu.
   Następny przebieg (np. z crona) zaczyna znowu od `requests`.
7. Na ostatnim transporcie (`playwright`) nie ma już dokąd przejść: `PoliteClient`
   ponawia do `max_retries` (domyślnie 6), a potem przerywa przebieg błędem
   `SourceError`. Dzięki zapisywaniu stron w transakcjach następny przebieg zacznie
   od miejsca, w którym ten skończył.

Co robi `PoliteClient` z innymi odpowiedziami (to samo dla każdego transportu):

| Odpowiedź | Reakcja |
|---|---|
| 200 itp. | zwraca stronę |
| 429, 500, 502, 503, 504 | czeka (nagłówek `Retry-After` albo 2, 4, 8 … s, max 120 s) i ponawia |
| błąd sieci (także curl lub przeglądarki) | jak wyżej |
| blokada Cloudflare | czeka 30 s × numer próby i ponawia; łańcuch może zmienić transport |
| 400, 403 bez Cloudflare, 404 … | **nie ponawia**: od razu `SourceError` (tego ponowienie nie naprawi) |

## 6. Szczegóły każdego transportu

**`requests`** (`RequestsTransport`): zwykła sesja `requests.Session`. Zachowuje
ciasteczka i połączenia między zapytaniami.

**`curl`** (`CurlTransport`): każde zapytanie to osobne uruchomienie programu
`curl --silent --show-error --include --max-time … --header …`. Wynik (nagłówki + treść)
rozbiera `_parse_curl_output`. Celowo **bez** `--compressed`, bo z tą opcją Cloudflare
HUDOC zatrzymywał curla na Windows. Gdy curla nie ma w systemie, jest to traktowane jak
błąd sieci.

**`playwright`** (`PlaywrightTransport` w `judgments_core/browser.py`):
- uruchamia Chromium bez okna (headless);
- najpierw otwiera stronę główną źródła (np. `https://hudoc.echr.coe.int`) i czeka
  3 s, żeby Cloudflare sprawdził „gościa” i dał ciasteczka;
- potem każde zapytanie wysyła **z wnętrza tej strony** przez `fetch()`, tak jak robią to
  skrypty samej strony;
- zostawia przeglądarce **jej własny User-Agent** (podmieniony nie pasowałby do
  odcisku Chromium i wyglądałby podejrzanie);
- po blokadzie albo błędzie przed następnym zapytaniem otwiera stronę główną od nowa;
- Playwright jest **zależnością opcjonalną**: importuje się dopiero przy pierwszym
  użyciu. Na hostingu go nie ma, więc tam łańcuch kończy się na curl.

## 7. Jak tego używać

Łańcuch projektu jest zapisany w kodzie (`Project.transports`). Można go nadpisać
zmienną **`<PREFIKS>_TRANSPORT`** (w `.env`, w środowisku albo jednorazowo przez `-e`):
jedna nazwa = tylko ten sposób, kilka po przecinku = własny łańcuch.

```bash
# domyślny łańcuch projektu (ETPC: requests -> curl -> playwright)
docker compose run --rm --no-deps www python -m etpc pobierz --limit 10

# jednorazowo tylko curl
docker compose run --rm --no-deps -e ETPC_TRANSPORT=curl www python -m etpc pobierz --limit 10

# od razu przeglądarka (tak jest teraz lokalnie w .env: ETPC_TRANSPORT=playwright)
docker compose run --rm --no-deps -e ETPC_TRANSPORT=playwright www python -m etpc pobierz --limit 10

# własny łańcuch bez requests
docker compose run --rm --no-deps -e ETPC_TRANSPORT=curl,playwright www python -m etpc pobierz
```

Dozwolone nazwy: `requests`, `curl`, `playwright`. Nieznana nazwa daje błąd
`nieznany transport: …` od razu przy starcie.

**Jak sprawdzić, co się działo:**
- log przebiegu (`logs/www/…`): linia `transport: requests -> curl -> playwright` na
  starcie i komunikaty o przełączeniu;
- `python -m etpc stan`: sekcja „Blokady i błędy źródła, ostatnie 7 dni”
  (rodzaje: `cloudflare`, `http_429`, `http_5xx`, `siec`, `odrzucone`, `przerwano`, `transport`);
- tabela `fetch_events` w bazie projektu (każde zdarzenie z adresem i czasem czekania).

## 8. Gdzie co działa

| Miejsce | requests | curl | playwright |
|---|---|---|---|
| kontener `www` (lokalnie, Docker) | tak | tak (w obrazie) | tak (Chromium w obrazie) |
| hosting (seohost, cron) | tak | jeśli jest w systemie | **nie** (zależność tylko deweloperska) |

Po zmianie `docker/www.Dockerfile` trzeba przebudować obraz: `docker compose build www`.

## 9. Osobny łańcuch w `narzedzia/cbosa_id.py`

Narzędzie użytkownika do zbierania identyfikatorów orzeczeń z wyszukiwarki CBOSA ma
**własną, uproszczoną kopię** tego pomysłu (klasy `RequestsHttp`, `CurlHttp`,
`PlaywrightHttp`; nie korzysta z `judgments_core/transport.py`). Różnice:

- przełącza się po **3 nieudanych zapytaniach z rzędu** (`SWITCH_AFTER = 3`), czyli po
  dowolnym błędzie: sieci, 5xx, pustej odpowiedzi, a nie tylko po blokadzie Cloudflare;
- po przełączeniu wysyła wyszukiwanie od nowa (nowy transport ma nowe ciasteczka)
  i pobiera tę samą stronę wyników;
- gdy nie działa żaden sposób: koniec, a zebrane okresy zostają;
- łańcuch ustawia się flagą `--transport` (domyślnie `requests,curl,playwright`),
  np. `python narzedzia/cbosa_id.py --od 2025-03-05 --transport curl,playwright`.

Wyszukiwarka CBOSA jest zabroniona w `robots.txt`, dlatego to narzędzie celowo
**nie jest** częścią kodu projektu (szczegóły w `komendy.md`).

## 10. Jak dodać nowy sposób pobierania

1. Klasa potomna `Transport` (w `judgments_core/transport.py` albo w osobnym module,
   jak `browser.py`) z metodą `get(url, params, timeout)`, która zwraca obiekt z polami
   `status_code`, `headers`, `content`, `text`, `json()`, `url` (np. `transport.Response`),
   a błędy sieci zgłasza jako `requests.RequestException`, żeby `PoliteClient` je ponawiał.
   Jeśli coś trzyma (proces, przeglądarkę), zwalnia to w `close()`.
2. Dopisanie nazwy do słownika `TRANSPORTS`. Ciężkie zależności importuje się
   w funkcji-fabryce (jak `_playwright()`), nie na górze modułu.
3. Użycie: w łańcuchu projektu (`transports=(…)` w `<projekt>/__main__.py`) albo przez
   `<PREFIKS>_TRANSPORT`.
4. Test bez sieci w `tests/test_http.py` (wzór: testy curl z podstawionym `run`).

## 11. Uprzejmość wobec źródeł

Łańcuch służy temu, żeby **w ogóle** dostać się do publicznych orzeczeń, a nie żeby
pobierać szybciej. Przerwa między zapytaniami (`<PREFIKS>_PAUSE_S`, CBOSA min. 3 s),
ponawianie z narastającym czekaniem i kontakt w `<PREFIKS>_USER_AGENT` obowiązują przy
każdym transporcie (zasada z `CLAUDE.md`: *Be polite to sources*).
