# Jak pobieramy strony: Proxy-Seller → Python → curl → przeglądarka

Opis „strategii transportu”, czyli sposobu, w jaki program dostaje się do strony
źródła (HUDOC, CBOSA), także wtedy, gdy strona blokuje roboty (Cloudflare).
Dla każdego: co to jest, po co, w których plikach i jak tego używać.

---

## 1. W jednym zdaniu

Program domyślnie próbuje pobrać stronę **zwykłym Pythonem** (`requests`). Gdy Cloudflare go
zatrzymuje, przechodzi na **curl** (systemowy program do pobierania, który Cloudflare
częściej przepuszcza). Gdy zatrzymany jest też curl, uruchamia **prawdziwą przeglądarkę**
Chromium bez okna (Playwright), a ona wchodzi na stronę tak jak człowiek.

Jeśli w konfiguracji podano klucz **Proxy-Seller** (`PROXY_SELLER_API_KEY`), system stawia go
**przed strategią**: sprawdza logowanie do API i stan konta. Jeśli są środki i lista proxy
rezydencjalnych, zapytania idą najpierw przez proxy rezydencjalne. Jeśli nie można się zalogować
z jakiegokolwiek powodu lub brak jest środków, system płynnie przechodzi do standardowych 3 strategii.

```
[Proxy-Seller] (rezydencjalne)
      | (brak środków / błąd logowania / blokada)
      v
requests (Python)  --blokada-->  curl  --blokada-->  playwright (Chromium)
   najszybszy                    szybki               wolny, ale najskuteczniejszy
```

## 2. Dlaczego te sposoby

Cloudflare ocenia, czy łączy się człowiek, czy robot. Patrzy m.in. na „odcisk palca”
połączenia szyfrowanego (TLS) oraz reputację adresu IP:

| Sposób | Jak wygląda dla Cloudflare | Szybkość | Kiedy wystarcza |
|---|---|---|---|
| `proxy_seller` | domowe IP rezydencjalne (Proxy-Seller v2) | szybki | omija blokady IP/datacenter; wchodzi przed łańcuchem gdy są środki |
| `requests` | biblioteka Pythona: łatwo ją rozpoznać | najszybszy | strony bez Cloudflare (np. CBOSA) |
| `curl` | inny „odcisk” TLS, często przepuszczany | szybki (osobny proces na każde zapytanie) | HUDOC zwykle tak |
| `playwright` | prawdziwa przeglądarka Chromium, uruchamia skrypty strony | wolny, ~500 MB | gdy Cloudflare blokuje i curl |

Zaczynamy od najtańszego / najmniej obciążającego i schodzimy niżej tylko wtedy, gdy trzeba.

## 3. Kto za co odpowiada: dwa piętra

Pobieranie ma **dwa niezależne piętra** (to wzorzec projektowy „strategia”):

1. **Kiedy i ile razy pytać:** `PoliteClient` w `judgments_core/http.py`.
   Pilnuje przerwy między zapytaniami, ponawia po błędach, czeka po blokadzie,
   zapisuje zdarzenia. To samo dla każdego źródła.
2. **Jak zapytanie fizycznie dociera do strony:** *transport* w
   `judgments_core/transport.py` (oraz `judgments_core/proxy_seller.py`, `judgments_core/browser.py`).
   Wykonuje jedno pobranie (GET) i niczego nie decyduje.

Dzięki temu można wymienić sposób pobierania bez ruszania logiki uprzejmości
i ponawiania. Źródło (np. `HudocSource`) w ogóle nie wie, którym transportem idzie.

## 4. Mapa plików

| Plik | Co jest w środku |
|---|---|
| `judgments_core/proxy_seller.py` | `ProxySellerTransport`: obsługa Proxy-Seller Client API v2 (`proxy-seller-user-api`), pobieranie proxy rezydencjalnych, rotacja adresów, fallback gdy brak środków lub błąd logowania |
| `judgments_core/transport.py` | rodzic `Transport` i strategie: `RequestsTransport`, `CurlTransport`, łańcuch `FallbackTransport`; słownik `TRANSPORTS` (nazwa → klasa), `make_transport(nazwy)`, rozpoznanie blokady `is_cloudflare_challenge` |
| `judgments_core/browser.py` | `PlaywrightTransport`: przeglądarka Chromium bez okna; importowana dopiero wtedy, gdy łańcuch do niej dojdzie |
| `judgments_core/http.py` | `PoliteClient`: przerwy, ponawianie, czekanie po blokadzie, zdarzenia |
| `judgments_core/cli.py` | `Project.transports` i `_make_source`: wstawia Proxy-Seller przed łańcuchem (jeśli są środki i poprawne logowanie), buduje klienta i źródło |
| `etpc/__main__.py` | łańcuch ETPC: `transports=("requests", "curl", "playwright")` |
| `nsa/__main__.py` | NSA nie ustawia łańcucha, więc tylko `requests` (CBOSA nie stoi za Cloudflare) |
| `judgments_core/settings.py` | ustawienia `transport`, `proxy_seller_api_key`, `proxy_seller_resident_list_id`, `pause_s`, `max_retries`, `timeout_s` |
| `docker/www.Dockerfile` | instaluje `curl` oraz Playwright z Chromium w obrazie `www` |
| `requirements-dev.txt` | `playwright`, `proxy-seller-user-api` (zależności opcjonalne / deweloperskie) |
| `judgments_core/migrations/002_logs.sql` | tabela `fetch_events`: blokady, błędy i przełączenia transportu |
| `tests/test_http.py`, `tests/test_proxy_seller.py` | testy klienta, curl, łańcucha i Proxy-Seller (bez sieci) |
| `narzedzia/cbosa_id.py` | osobne narzędzie użytkownika z **własnym**, podobnym łańcuchem (punkt 9) |

## 5. Jak działa łańcuch krok po kroku

1. Jeśli podano `PROXY_SELLER_API_KEY`, program łączy się z API Proxy-Seller i sprawdza `balance()`:
   - Jeśli zalogowano pomyślnie i są środki (`balance > 0`), pobiera pulę proxy rezydencjalnych (`residentLists` / `proxyDownloadResident`).
   - Transport `proxy_seller` jest wstawiany jako pierwszy w łańcuchu.
   - W przypadku pierwszego wyzwania Cloudflare następuje rotacja adresu proxy (`rotate()`).
   - Jeśli nie uda się połączyć (błędny klucz, brak biblioteki, błąd sieci) lub brak środków: `proxy_seller` jest pomijany, a program startuje ze standardowych strategii (`requests` -> `curl` -> `playwright`).
2. Na starcie `FallbackTransport` tworzy **tylko bieżący** transport. Następne powstają dopiero, gdy są potrzebne.
3. Każda odpowiedź jest sprawdzana pod kątem **blokady Cloudflare** (HTTP 403 z nagłówkiem `cf-mitigated: challenge`).
4. **Dwie blokady z rzędu** (`switch_after=2`): łańcuch zamyka bieżący transport, przechodzi na następny i **od razu powtarza to samo zapytanie** nowym sposobem.
5. Udane pobranie zeruje licznik blokad.
6. Na ostatnim transporcie (`playwright`) `PoliteClient` ponawia do `max_retries` (domyślnie 6), a potem przerywa przebieg błędem `SourceError`.

## 6. Szczegóły każdego transportu

**`proxy_seller`** (`ProxySellerTransport` w `judgments_core/proxy_seller.py`):
- Sesja Pythona z ustawionym proxy rezydencjalnym HTTP/SOCKS5.
- Adresy pobierane z Proxy-Seller Client API v2 przez bibliotekę `proxy-seller-user-api`.
- Metoda `rotate()` podmienia adres na kolejny z pobranej puli przy ponowieniach po blokadzie.
- Hasła w adresach proxy są automatycznie maskowane w logach (`current_proxy_safe`).

**`requests`** (`RequestsTransport`): zwykła sesja `requests.Session`. Zachowuje
ciasteczka i połączenia między zapytaniami.

**`curl`** (`CurlTransport`): każde zapytanie to osobne uruchomienie programu
`curl --silent --show-error --include --max-time … --header …`. Wynik (nagłówki + treść)
rozbiera `_parse_curl_output`.

**`playwright`** (`PlaywrightTransport` w `judgments_core/browser.py`):
- uruchamia Chromium bez okna (headless);
- najpierw otwiera stronę główną źródła i czeka 3 s na ciasteczka Cloudflare;
- potem każde zapytanie wysyła z wnętrza tej strony przez `fetch()`.

## 7. Jak tego używać

W pliku `.env` (lub w środowisku):
```bash
# Klucz API do Proxy-Seller (opcjonalny)
PROXY_SELLER_API_KEY=twoj_klucz_api

# Opcjonalnie: konkretna lista rezydencjalna lub brama
# PROXY_SELLER_RESIDENT_LIST_ID=12345
# PROXY_SELLER_GATEWAY=res.proxy-seller.com
# PROXY_SELLER_PROTOCOL=http
```

Nadpisywanie łańcucha zmienną **`<PREFIKS>_TRANSPORT`**:
```bash
# Użyj jawnie łańcucha z proxy_seller
ETPC_TRANSPORT=proxy_seller,requests,curl,playwright

# Jeśli PROXY_SELLER_API_KEY jest ustawiony, proxy_seller wejdzie automatycznie przed strategiami,
# chyba że brakuje środków lub logowanie się nie powiedzie - wtedy zadziała czysty łańcuch.
```

## 8. Gdzie co działa

| Miejsce | proxy_seller | requests | curl | playwright |
|---|---|---|---|---|
| lokalnie (komputer dewelopera) | tak (wymaga klucza) | tak | tak | tak |
| kontener `www` (Docker) | tak (jeśli zainstalowane w obrazie) | tak | tak | tak |
| hosting (seohost, cron) | tak (jeśli zainstalowane i są środki) | tak | jeśli jest w systemie | **nie** |

## 9. Osobny łańcuch w `narzedzia/cbosa_id.py`

Narzędzie użytkownika do zbierania identyfikatorów orzeczeń z wyszukiwarki CBOSA ma
własną, uproszczoną kopię tego pomysłu (klasy `RequestsHttp`, `CurlHttp`, `PlaywrightHttp`).

## 10. Uprzejmość wobec źródeł

Łańcuch służy temu, żeby dostać się do publicznych orzeczeń, a nie żeby pobierać agresywnie.
Przerwa między zapytaniami (`<PREFIKS>_PAUSE_S`, CBOSA min. 3 s), ponawianie z narastającym
czekaniem i kontakt w `<PREFIKS>_USER_AGENT` obowiązują przy każdym transporcie (*Be polite to sources*).
