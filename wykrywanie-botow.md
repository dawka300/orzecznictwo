# Jak strona poznaje, że pyta ją program, a nie człowiek

Notatka do nauki: po czym zapora aplikacyjna (WAF, ang. *web application firewall*) odróżnia
automat od człowieka, nawet gdy zmienia się adres IP. Na przykładzie CBOSA z 29.10.2026.
Powiązane: `transport.md` (jak nasze programy pobierają strony).

---

## 1. Co się stało z CBOSA (29.10.2026)

- **Nasz własny adres IP**: serwer zrywa połączenie już przy nawiązywaniu szyfrowania (TLS),
  zanim padnie jakiekolwiek zapytanie. Dotyczy całej domeny, także zwykłych stron orzeczeń
  `/doc/<ID>`. Najpewniej czarna lista po wcześniejszym zbieraniu numerów z wyszukiwarki.
- **Proxy-Seller**: bramka dostawcy sama odmawia połączenia z `orzeczenia.nsa.gov.pl`
  (`503 Target host denied`). Dostawcy proxy rezydencjalnych często blokują domeny rządowe.
- **FlameProxies, Python (`requests`)**: proxy z polskimi adresami domowymi; IP inne przy każdym
  zapytaniu. Mniej więcej połowa zapytań przechodziła, reszta była zrywana.
- **FlameProxies, Playwright (Chromium bez okna)**: 0 na 18 prób. Za każdym razem strona:

  > Strona została zablokowana! Nie można wyświetlać strony. Prosimy o kontakt z
  > administratorem. … Client IP: … Attack ID: 20000051 Message ID: …

  Najpierw zgadywałem F5 BIG-IP; sprawdzenie nagłówków (punkt 4, trzecia runda) wskazuje
  raczej **Fortinet (FortiWeb / FortiGate)**.
- **Przerwy między zapytaniami** (co najmniej 3 s) nie miały znaczenia: blokada była od
  pierwszego zapytania.

Wniosek: administrator celowo blokuje automatyczny ruch (robots.txt mówi to samo).
Dalsze próby byłyby już obchodzeniem zabezpieczenia - zostaje prośba do NSA o dane.

---

## 2. Co wysyłamy, nie wiedząc o tym

Adres IP to tylko jeden z wielu sygnałów. Zapora zwykle **ocenia punktowo**: każdy podejrzany
sygnał dodaje punkty, powyżej progu jest blokada. Zmiana IP zmienia jeden sygnał, reszta zostaje.

### 2.1. Nagłówek User-Agent

Każde zapytanie HTTP mówi, jakim programem jest klient. Chromium uruchomiony bez okna
(tryb *headless*, tak jak w Playwright) przedstawia się domyślnie jako `HeadlessChrome/...`,
czyli sam ogłasza, że jest automatem. To najprostsza reguła zapory i najpewniej powód 0/18.
Python `requests` przedstawia się jako `python-requests/...`, chyba że program poda inny.

### 2.2. Odcisk połączenia szyfrowanego (TLS)

Zanim pójdzie pierwszy bajt strony, klient wysyła powitanie TLS (*ClientHello*): listę
obsługiwanych szyfrów, rozszerzeń, krzywych i ich kolejność. Ta kombinacja jest
charakterystyczna dla biblioteki, z której idzie połączenie. Z niej liczy się „odcisk”
(znane metody: **JA3**, nowsza **JA4**).

- Python (OpenSSL przez `requests`), curl i Chrome mają różne odciski.
- **Zmiana IP odcisku nie zmienia**: proxy przekazuje powitanie TLS bez zmian (tunel `CONNECT`).
- Niespójność też jest sygnałem: odcisk biblioteki Pythona z domowego IP w Polsce wygląda
  inaczej niż ruch zwykłych ludzi z tych samych adresów.

### 2.3. Nagłówki HTTP i ich kolejność

Prawdziwy Chrome wysyła stały zestaw nagłówków w stałej kolejności, m.in. `Accept`,
`Accept-Language`, `Accept-Encoding`, `sec-ch-ua` (marka i wersja przeglądarki),
`sec-fetch-site` / `sec-fetch-mode` / `sec-fetch-dest` (skąd i po co zapytanie).
Biblioteki wysyłają kilka podstawowych, w innej kolejności, bez nagłówków `sec-*`.

Przy HTTP/2 dochodzi kolejny odcisk: ustawienia połączenia (ramka `SETTINGS`), priorytety
i kolejność pseudo-nagłówków (`:method`, `:authority`, …) różnią się między programami.

### 2.4. Sprawdzanie w JavaScripcie

Zapory tej klasy wstawiają do strony skrypt, który bada przeglądarkę, np.:

- `navigator.webdriver` - w przeglądarce sterowanej automatycznie ma wartość `true`,
- wtyczki, języki, strefa czasowa, rozmiar ekranu i okna,
- jak rysuje grafikę (*canvas*, WebGL) - inaczej na prawdziwej karcie, inaczej w kontenerze,
- czas wykonania i drobne różnice w działaniu funkcji.

Wynik wraca do serwera zwykle jako ciasteczko; bez poprawnego ciasteczka kolejne zapytania
są blokowane. Program bez JavaScriptu (Python) takiego sprawdzenia w ogóle nie przechodzi,
więc zapora może go łapać po samym braku ciasteczka.

### 2.5. Zachowanie

Człowiek wchodzi na stronę główną, ładuje obrazki, style i skrypty, ma ciasteczka z
poprzednich stron i nagłówek `Referer` (skąd przyszedł), klika w nierównych odstępach.
Program idzie prosto na `/doc/<ID>`, nie ładuje niczego wokół, bez `Referer`, równo co 3 s,
setki stron po kolei.

### 2.6. Zmiana IP przy każdym zapytaniu

Raczej szkodzi, niż pomaga:

- ten sam odcisk programu (a czasem to samo ciasteczko sesji) z kilkunastu różnych domowych
  IP w ciągu minuty wygląda jak **botnet**,
- adresy proxy rezydencjalnych są dzielone między wielu klientów, część jest już oznaczona
  w bazach reputacji (listy adresów znanych z nadużyć),
- sesja wyszukiwarki CBOSA żyje w ciasteczku; skoki IP w jednej sesji też są nienaturalne.

### 2.7. Reputacja i historia adresu

Zapora pamięta adresy, z których przyszły wcześniejsze „ataki”. Nasz adres odcina już na
poziomie połączenia (TLS), czyli wcześniej niż reguły z punktów 2.1-2.5.

---

## 3. Dlaczego Python przechodził w połowie, a przeglądarka wcale

- Python nie wykonuje JavaScriptu i nie przedstawia się jako `HeadlessChrome`, więc reguły
  zapory aplikacyjnej (strona blokady) go nie łapały; jego nieudane zapytania to zerwane
  połączenia, najpewniej odcięcie części rotowanych adresów po reputacji (punkt 4).
- Chromium bez okna sam ogłasza się nagłówkiem User-Agent i ma `navigator.webdriver = true`:
  zapora rozpoznaje go od pierwszego zapytania, z każdego adresu.

---

## 4. Poprawki po recenzji (29.10.2026)

Ktoś przejrzał tę notatkę; co się potwierdziło, a co nie:

- **`HeadlessChrome` w User-Agent - sprawdzone, u nas prawda.** Playwright 1.64.1 z obrazu `www`,
  domyślne `chromium.launch(headless=True)` (stary tryb, osobny `chromium-headless-shell`):
  `Mozilla/5.0 (X11; Linux x86_64) ... HeadlessChrome/156.0.8078.4 Safari/537.36`,
  `navigator.webdriver = true`. Recenzja ma rację tylko dla nowego trybu headless
  (`channel="chromium"`), który ma User-Agent jak zwykły Chrome - tego nie używaliśmy.
- **Telemetria JavaScript jako przyczyna 0/18 - mało prawdopodobne.** Strona blokady przyszła
  już jako odpowiedź na pierwsze zapytanie świeżej przeglądarki (prosto na `/doc/<ID>`), zanim
  jakikolwiek skrypt zapory mógł się wykonać i coś odesłać. Zapora zdecydowała po samym
  zapytaniu: User-Agent, nagłówki, odcisk TLS. `navigator.webdriver` ma znaczenie dopiero przy
  kolejnych stronach, gdy skrypt zapory już się wykonał.
- **Sprzeczność w punkcie 2.4 - słuszna.** Gdyby każde `/doc/<ID>` wymagało ciasteczka z
  JavaScriptu, Python miałby 0%, a nie ~50%. Lepsze wyjaśnienie: filtrowanie po reputacji
  adresu (część rotowanych adresów przechodzi, część nie). Ważne: Python **nigdy nie dostał
  strony blokady**, tylko zerwane połączenia - to inny mechanizm (odcięcie na brzegu sieci)
  niż strona blokady dla Chromium (reguła zapory aplikacyjnej).
- **F5 - po sprawdzeniu raczej nie** (trzecia runda niżej). Recenzja słusznie wskazała, jak
  to sprawdzić: F5 ASM ustawia ciasteczka `TS` + szesnastkowy ciąg (np. `TS01a2b3c4`), często
  też `BIGipServer...` (równoważenie obciążenia) - u CBOSA ich nie ma.
- **Doprecyzowanie TLS (2.2):** Python `requests` (urllib3) używa systemowego **OpenSSL**,
  Chrome - **BoringSSL** (odgałęzienie OpenSSL od Google). Domyślne listy szyfrów i rozszerzeń
  obu bibliotek są różne, więc odcisk OpenSSL na stronie dla ludzi to sygnatura skryptu.
- **Doprecyzowanie HTTP/2 (2.3):** Python `requests` obsługuje **tylko HTTP/1.1**. Przeglądarka
  w trakcie TLS uzgadnia HTTP/2 (rozszerzenie ALPN). HTTP/1.1 z domowego adresu, bez nagłówków
  `sec-fetch-*` - kolejny sygnał dla zapory, która łączy te cechy.

### Druga runda: dwa mechanizmy na dwóch warstwach

| | Chromium (Playwright) | Python przez proxy | nasze własne IP |
|---|---|---|---|
| co widać | strona blokady, **HTTP 500**, „Attack ID” | `ProxyError: Unable to connect to proxy, RemoteDisconnected` (~50%) | TCP się łączy, zerwanie przy TLS (Python, curl, schannel) |
| gdzie padło | warstwa aplikacji (L7): TLS i HTTP przeszły, zapora przeczytała nagłówki | przy zestawianiu tunelu `CONNECT` przez proxy, **zanim** poszło powitanie TLS do CBOSA | przy powitaniu TLS |
| najpewniej dlaczego | sygnatura w nagłówkach (`HeadlessChrome`) - decyzja od razu, bez skryptu | odcięcie części rotowanych adresów wyjściowych (reputacja IP) | adres na czarnej liście (trzy różne biblioteki TLS, ten sam wynik - więc nie odcisk) |

- Kolejność decyzji dla Chromium: TLS przechodzi (BoringSSL), zapora parsuje nagłówki, dopasowuje
  sygnaturę automatu, zwraca stronę blokady. Gdyby decydował skrypt JS, pierwszą odpowiedzią
  byłaby strona z wyzwaniem (*challenge*), a nie od razu blokada.
- Odcisk TLS (JA3/JA4) nie mógł zatrzymać Pythona przez proxy: połączenie padało, zanim Python
  wysłał do CBOSA swoje `ClientHello`.
### Trzecia runda: sprawdzenie nagłówków - to nie F5

Jedno zapytanie `HEAD https://orzeczenia.nsa.gov.pl/` przez proxy (bez treści strony),
29.10.2026:

```
HTTP 301 -> http://orzeczenia.nsa.gov.pl/cbo/query
Server: Apache
X-XSS-Protection: 1; mode=block
X-Frame-Options: SAMEORIGIN
Set-Cookie: cookiesession1=... (Expires, Path, HttpOnly)
```

- **Brak** ciasteczek F5 (`TS…`, `BIGipServer…`) - obie recenzje i ja założyliśmy F5 za szybko,
  po samym formacie komunikatu.
- **`cookiesession1`** - z pamięci (w sesji niesprawdzone): ciasteczko utrwalania sesji
  w równoważeniu obciążenia urządzeń **Fortinet** (FortiGate / FortiWeb).
- Strona blokady z polami „Client IP”, „Attack ID”, „Message ID” i numerem w postaci 2000xxxx
  pasuje do domyślnej strony blokady **FortiWeb** (też z pamięci, niesprawdzone).
- Nauka: rozpoznanie po wyglądzie komunikatu to hipoteza; nagłówki odpowiedzi to dowód.
  Warto je zapisywać od razu przy pierwszej blokadzie.
- Nadal niewiadome: co dokładnie znaczy „Attack ID 20000051” w regułach FortiWeb.

**Uzupełnienie z dokumentacji Fortinetu** (sprawdził recenzent, nie ja):

- **`cookiesession1` to ciasteczko FortiWeb - potwierdzone.** FortiWeb dodaje je przy pierwszym
  żądaniu, gdy włączone jest zarządzanie sesjami (*Session Management*). Na jego podstawie
  egzekwuje reguły zależne od historii żądań: strony startowe, kolejność odwiedzanych stron,
  limit żądań na sesję; identyfikator sesji trafia do logów ruchu. To tłumaczy, dlaczego
  wejście prosto na `/doc/<ID>` bez strony startowej bywa podejrzane (punkt 2.5).
- **„Attack ID” - potwierdzone częściowo.** Szablon strony blokady FortiWeb ma makro
  `%%EVENT_ID%%`, opisane jako „numer identyfikujący typ ataku”; pod nim administrator znajdzie
  wpis w logu ataków. „Attack ID” najpewniej pokazuje tę wartość. „Message ID” dokumentacja nie
  wymienia; znaczenie 20000051 - nadal niesprawdzone.
- **Lekcja dla obrońcy:** domyślna nazwa `cookiesession1` zdradza producenta zapory, tak jak
  `BIGipServer...` przy F5. Obie da się przemianować (Fortinet ma na to instrukcję); u CBOSA
  nazwa jest domyślna, więc producenta widać z jednego zapytania.

### Oś czasu i wnioski z całej sytuacji

| Kiedy | Co | Wynik |
|---|---|---|
| 22.10 | zbieranie numerów z wyszukiwarki z naszego IP (`cbosa_id.py`) | urwane po 270 numerach - najpewniej wtedy nasz adres trafił na czarną listę |
| 29.10, pierwsze zapytanie | wyszukiwarka z naszego IP (requests, curl, Playwright) | zerwanie przy TLS od pierwszej próby - adres był martwy, zanim cokolwiek dziś zrobiliśmy |
| | oba serwery (194.181.28.1, 195.117.224.237), Windows i kontener, HTTPS i HTTP | wszędzie to samo - blokada adresu, nie awaria jednego serwera |
| | Proxy-Seller (~10 prób) | bramka dostawcy: `503 Target host denied` - do CBOSA nie doszło |
| | FlameProxies, Python: formularz + orzeczenie | przeszło (zwykłe polskie adresy domowe) |
| | FlameProxies, Python: liczba wyników sty-lip 2025 (7 zapytań) | przeszło: ~57 tys. orzeczeń; sierpień - zerwany tunel proxy |
| | FlameProxies, Python: 5 stron wyników + orzeczenia od razu po nich | wyniki przeszły, orzeczenia zaczęły padać; numerów nie zapisano (błąd: trzeba było zapisać i pobierać osobno) |
| | FlameProxies, Python: 8 rund ipify / gov.pl / CBOSA `/doc` | ipify i gov.pl 8/8, CBOSA 4/8 - także samo `/doc` bez wyszukiwarki |
| | FlameProxies, Playwright (25 zapytań) | 0/25: strona blokady „Attack ID 20000051” - `HeadlessChrome` w User-Agent (błąd: nie sprawdzone przed użyciem) |
| | `HEAD` na stronę główną | `cookiesession1` - FortiWeb, nie F5 |

Razem przez proxy ~60 zapytań do CBOSA, rozłożonych na kilkanaście minut i kilkadziesiąt
adresów - za mało na jakiekolwiek przeciążenie; blokada wynikała z oceny sygnałów, nie z liczby.

**Wnioski:**

- **Numery zapisywać od razu, pobierać osobno i powoli.** Narzędzie tak działa (`cbosa_id.py`
  -> pliki -> `nsa pobierz`); test to ominął.
- **Sprawdzić własne narzędzie, zanim pójdzie do cudzego serwera** - jedno polecenie pokazałoby
  `HeadlessChrome`; te 25 zapytań zapora zapisała jako ataki.
- **Hipoteza z wyglądu to nie dowód.** Dwie recenzje i ja założyliśmy F5 po formacie komunikatu;
  nagłówki pokazały Fortinet. Ładnie brzmiąca analiza („fazy spalania IP”, „`/doc` ma luźniejsze
  limity”) rozjechała się z danymi: nasz adres był zablokowany od rana, a `/doc` padało tak samo.
- **Gdy administrator świadomie blokuje automaty** (robots.txt + zapora), technika przestaje być
  pytaniem; dane - oficjalnym wnioskiem (informacja publiczna / ponowne wykorzystywanie).

---

## 5. Gdzie się tego uczyć (obie strony)

**Testery wykrywania automatu** - po czym strona poznaje, że to nie człowiek:

- **bot.sannysoft.com** - klasyczna tabela testów; od razu widać, co zdradza Playwrighta czy
  Selenium (np. `navigator.webdriver`).
- **CreepJS** (abrahamjuliot.github.io/creepjs) - bardzo szczegółowy odcisk przeglądarki, wykrywa
  nawet próby maskowania.
- **browserleaks.com**, **pixelscan.net** - co przeglądarka ujawnia o sobie: TLS, canvas, WebGL,
  czcionki, strefa czasowa.

**Strony do ćwiczenia scrapingu** (legalne z założenia):

- **toscrape.com** (books / quotes) - podstawy, wersje z JavaScriptem, logowaniem i stronicowaniem.
- **scrapethissite.com** - zadania o rosnącej trudności.
- **scrapingcourse.com** - także strony z wyzwaniami antybotowymi, zrobione do ćwiczeń.

**Własna zapora** - najlepsze dla przyszłej pracy po stronie obrony: konfigurujesz ochronę,
potem sam ją atakujesz:

- **SafeLine** (otwartoźródłowy WAF z ochroną przed botami) albo **ModSecurity + OWASP Core Rule
  Set** przed prostą stroną w Dockerze.
- **Anubis** - lekka zapora z wyzwaniem *proof-of-work*, coraz popularniejsza do ochrony przed
  scraperami AI.
- **Cloudflare na własnej domenie** - strona działki: darmowy plan z „Bot Fight Mode” i testy na
  własnym serwisie; do tego panel z logami, czyli dokładnie to, co widzi obrońca.

**Teoria:** **OWASP Automated Threats to Web Applications** - katalog typów automatycznych
zagrożeń i metod obrony; porządkuje to, co widać w praktyce.

---

## 6. Słowniczek

| Pojęcie | Znaczenie |
|---|---|
| WAF | zapora aplikacyjna: ogląda zapytania HTTP i blokuje te, które uzna za atak lub automat |
| TLS, ClientHello | szyfrowanie połączenia (https); ClientHello = pierwsza wiadomość klienta z listą szyfrów |
| JA3 / JA4 | sposoby liczenia odcisku klienta z jego powitania TLS |
| headless | przeglądarka bez okna, sterowana programem (Playwright, Selenium) |
| proxy rezydencjalne | pośrednik z adresami IP domowych łączy (zwykle cudze urządzenia w sieci dostawcy) |
| robots.txt | plik, w którym właściciel strony mówi automatom, czego im nie wolno |
| reputacja IP | ocena adresu na podstawie jego historii (spam, ataki, znane proxy) |


SafeLine to otwarty projekt z oficjalną dokumentacją instalacji w Dockerze, więc możesz go postawić sam według ich instrukcji. Jeśli przy tym coś się wysypie, np. port albo sieć w Docker Desktop, z samą konfiguracją Dockera chętnie pomogę.