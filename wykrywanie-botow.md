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

  Format komunikatu („Attack ID”, „Message ID”) wygląda jak zapora F5 BIG-IP (ASM / Advanced
  WAF), często używana w polskich urzędach (niesprawdzone).
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

- Python nie wykonuje JavaScriptu i nie przedstawia się jako `HeadlessChrome`, więc część reguł
  go nie łapie; inne (odcisk TLS, brak ciasteczka z JavaScriptu, nagłówki) tak - stąd mniej
  więcej połowa.
- Chromium bez okna sam ogłasza się nagłówkiem User-Agent i ma `navigator.webdriver = true`:
  zapora rozpoznaje go od pierwszego zapytania, z każdego adresu.

---

## 4. Słowniczek

| Pojęcie | Znaczenie |
|---|---|
| WAF | zapora aplikacyjna: ogląda zapytania HTTP i blokuje te, które uzna za atak lub automat |
| TLS, ClientHello | szyfrowanie połączenia (https); ClientHello = pierwsza wiadomość klienta z listą szyfrów |
| JA3 / JA4 | sposoby liczenia odcisku klienta z jego powitania TLS |
| headless | przeglądarka bez okna, sterowana programem (Playwright, Selenium) |
| proxy rezydencjalne | pośrednik z adresami IP domowych łączy (zwykle cudze urządzenia w sieci dostawcy) |
| robots.txt | plik, w którym właściciel strony mówi automatom, czego im nie wolno |
| reputacja IP | ocena adresu na podstawie jego historii (spam, ataki, znane proxy) |
