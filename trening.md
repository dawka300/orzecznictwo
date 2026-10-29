# Trening modeli: co, gdzie, jak

Jedno miejsce o danych do trenowania: co chcemy trenować, gdzie leżą pliki, jak zbierać
nowe dane i w jakiej kolejności to robić. Stan na 2026-10-29. Szczegóły wyników są w
`NOTEPAD.md` (sekcje podane przy każdym punkcie).

Zasada ogólna: **wiedzy o orzeczeniach nie trenujemy** - daje ją RAG (nowe orzeczenia bez
trenowania, każde zdanie ze źródłem; `interview.md`). Trenujemy **umiejętności**: lepsze
wyszukiwanie, lepsze czytanie źródeł, styl i zachowanie modelu.

---

## 0. Kto co robi

### Ty (ręcznie)
| Co | Kiedy | Jak |
|---|---|---|
| Rozmawiać z modelem | na co dzień | `/etpc/zapytaj`, `/nsa/zapytaj`; wybrać tryb (Bezpośrednio / RAG / Pismo) |
| Zlecić ocenę rozmów | co kilka dni | napisać Claude'owi: „oceń rozmowy” |
| Wrzucać pisma do zakładki Trening | gdy masz dokumenty | `/etpc/trening`: plik, **rodzaj**, katalog |
| Zweryfikować oceny Claude'a | raz, ~2-3 h | `etpc/database/trening/weryfikacja/weryfikacja-ocen-etpc.csv` wg instrukcji obok |
| Zlecić nowe pytania wzorcowe i ich oceny | gdy chcemy więcej danych | napisać Claude'owi, np. „dopisz 100 pytań ETPC i oceń top 10” |
| Decyzje i koszty | przed treningiem | wynajem Vast (~$5), kiedy trenować, czy wymienić model w LM Studio |
| Kopia na dysk G: | co jakiś czas | `bash narzedzia/kopia-na-dysk.sh` |

### Automatycznie (aplikacja, nic nie trzeba robić)
- zapis każdej rozmowy i każdej odpowiedzi z pełnym wejściem modelu, źródłami i czasami ->
  `badania/rozmowy/`;
- OCR, tekst i wektory dokumentów wrzuconych do zakładki Trening -> baza `trening`;
- raporty „Zapytaj Claude” z Analizy dokumentu i Badania -> `badania/analiza/`, `badania/badanie/`;
- wynik `ocen-odpowiedzi` i `ocen --claude` -> `etpc/database/odpowiedzi/`, `etpc/database/oceny/`
  (gdy polecenie zostanie uruchomione).

### Claude w sesji (na Twoje polecenie, bez API i bez kosztów)
- ocena rozmów + odpowiedź wzorcowa -> `badania/rozmowy/oceny/`;
- pisanie pytań wzorcowych, przebiegi wyszukiwania, oceny 0-3 top 10 -> `*/database/trening/`;
- uruchamianie pomiarów (`ocen`, `ocen-odpowiedzi`) przed i po zmianach;
- kod z rozdz. 5 (zapis przebiegów, eksport rozmów i par do treningu, anonimizacja);
- przygotowanie danych i skryptu treningu; sam trening liczy karta (Vast albo 5070 Ti) po
  Twojej decyzji.

---

## 1. Co chcemy trenować

| # | Model | Po co | Dane (rozdz. 2) | Stan |
|---|---|---|---|---|
| A | **Embedder bge-m3** (wektory) | wyszukiwanie po znaczeniu trafia w sprawy, które prawnik by zacytował; mniej szumu (Kudła, Ukraine v. Russia...) | pary pytanie -> trafne orzeczenie + trudne negatywy; akapit cytujący -> cytowane (ETPC); teza -> uzasadnienie (NSA) | dane ETPC zaczęte (99 pytań, ~1 400 ocen), NSA tylko pytania |
| B | **Reranker** (cross-encoder) | dokładniejsza kolejność top 20; dziś wyłączony, bo psuł sprawy przełomowe | te same oceny 0-3 par pytanie-orzeczenie | jak A |
| C | **Model czatu** (Qwen 3.5 9B w LM Studio; później 14B/27B) | odpowiedzi tylko ze źródeł, poprawne cytowania i wyniki spraw, pisma, sensowne odmowy | rozmowy z zakładki „Zapytaj model” z oceną i odpowiedzią wzorcową; odpowiedzi `ocen-odpowiedzi` | zbieranie ruszyło 2026-10-29 (5 rozmów, 1 oceniona) |
| C1 | **Qwen 9B do pism** (QLoRA, osobny model w LM Studio) | pisma w układzie i stylu użytkownika: wnioski do WSA, o informację publiczną, do gminy ... | gotowe pisma z zakładki Trening -> pary „prośba -> pismo”; rozmowy w trybie „Pismo” | plan (rozdz. 4, C1); czeka na wybór typów pism i pierwsze pisma |
| D | **Wzorce dokumentów** (rodzaj pisma, formułki) | porównanie dokumentów odsiewa nagłówki, petitum, formułki sądu | dokumenty w bazie `trening` (zakładka „Trening”) | baza i eksport gotowe, dokumentów do dodania |
| E | **Miary psychologiczne** (głowica na wektorach) | emocje, zniekształcenia, osie na pismach | ręczne oznaczenia akapitów (kilkaset) | później; plan w `psychologia.md` pkt 6 |

---

## 2. Gdzie są pliki

Wszystkie katalogi `*/database/` i `badania/` są **lokalne** (w `.gitignore`), kopiuje je
`narzedzia/kopia-na-dysk.sh` na dysk G:. Pliki z pytaniami (`*/pytania*.json`) są w repo.

### Wyszukiwanie (cele A, B)

| Plik | Co zawiera | Skąd |
|---|---|---|
| `etpc/pytania.json` (repo) | 21 pytań o znane sprawy + pole `szum` (sprawy-szum = trudne negatywy) | ręcznie, z realnych wyszukiwań |
| `etpc/pytania-panstwa.json` (repo) | 8 pytań z filtrem państwa (sprawy przełomowe) | ręcznie |
| `etpc/pytania-100.json` (repo) | 99 pytań (59 roboczych / 40 testowych, pole `czesc`) | Claude w sesji, 2026-10-29 |
| `etpc/database/trening/pytania-100-zrodla.jsonl` | do każdego pytania: orzeczenie, metadane, temat A, **wycinki, z których powstało pytanie**, autor/metoda | j.w. |
| `etpc/database/trening/wyniki-<czas>-<wariant>-<zestaw>.jsonl` | każdy przebieg wyszukiwania: top 20, fragmenty kontekstu, miejsce oczekiwanego (= pozytywy i trudne negatywy); warianty `cw0/cw1` (cytowania), `idf`, `lift` | przebiegi `ocen` z zapisem (skrypt - patrz rozdz. 5, brak 1) |
| `etpc/database/trening/oceny-top10-20261029*.jsonl` | **oceny 0-3** top 10 dla 99 pytań (~1 400 par pytanie-orzeczenie); `-cw0`, `-idf` = dodatkowe warianty | Claude w sesji (sędzia) |
| `etpc/database/trening/robocze/` | szkice pytań, wybór orzeczeń, surowe oceny `.txt` | robocze, nie usuwać |
| `etpc/database/trening/weryfikacja/` | arkusz `weryfikacja-ocen-etpc.csv` (100 wierszy do Twojej oceny 0-3) + instrukcja `.md` | do ręcznej weryfikacji ocen Claude'a |
| `etpc/database/oceny/<czas>-claude-sonnet-5-5.json` | ocena trafności przez Claude API (`ocen --claude`) | `python -m etpc ocen --claude` |
| `nsa/pytania.json`, `nsa/pytania-zagadnienia.json` (repo) | 6 pytań o uchwały; zagadnienia (np. dobre sąsiedztwo) | ręcznie |
| `nsa/pytania-100.json` (repo) | 100 pytań NSA (60/40) | Claude w sesji, 2026-10-29 |
| `nsa/database/trening/pytania-100-zrodla.jsonl` | pytanie + metadane + wycinki + autor/metoda | j.w. |
| `nsa/database/trening/zagadnienia.jsonl` | linie orzecznicze (zagadnienie, przepis, teza linii) | Claude w sesji |
| `nsa/database/trening/robocze/` | szkice, wybór | robocze |
| baza ETPC, tabela `ruling_links` | kto kogo cytuje = pary akapit cytujący -> cytowane (jeszcze nie wyeksportowane) | `przetworz` |
| baza NSA, `thesis` + uzasadnienie | pary teza -> uzasadnienie (~1,8% orzeczeń ma tezę; jeszcze nie wyeksportowane) | `przetworz` |

### Model czatu (cel C)

| Plik | Co zawiera | Skąd |
|---|---|---|
| `badania/rozmowy/<id>.json` | rozmowa z zakładki **Zapytaj model**; w `tury` przy każdej odpowiedzi: **dokładnie to, co dostał model** (`wyslane`), odpowiedź, tryb (bezposrednio / rag / pismo), źródła z fragmentami, ranking, cytowania, ustawienia, czasy | zapis automatyczny po każdej odpowiedzi |
| `badania/rozmowy/oceny/<id>.ocena.json` | ocena rozmowy: oceny 1-5, błędy, **odpowiedź wzorcowa** (= przykład do treningu) | Claude w sesji („oceń rozmowę ...”) |
| `etpc/database/odpowiedzi/<czas>-<model>.json` | odpowiedzi na pytania wzorcowe + ocena automatyczna (cytowania, reguła wyniku, model-sędzia); qwen3-14b, qwen3.8-27b (2026-10-19/20) | `python -m etpc ocen-odpowiedzi` |

### Dokumenty i badania (cele D, E)

| Gdzie | Co | Skąd |
|---|---|---|
| baza MariaDB `trening` | skargi, orzeczenia, odpowiedzi, pisma z **rodzajem**; tekst po OCR i wektory fragmentów | zakładka **Trening** (`/etpc/trening`, tylko `APP_ENV=dev`) |
| `/trening/eksport.jsonl` | eksport tej bazy, jeden dokument na wiersz | link na stronie zakładki |
| `badania/analiza/`, `badania/badanie/` | raporty Claude'a z Analizy dokumentu i Badania + to, co wysłano (`wyslane.md`), CSV akapitów | przycisk „Zapytaj Claude” |
| `nsa/formulki.txt`, `etpc/formulki.txt` (repo) | zdania powtarzane w wielu orzeczeniach | `python -m etpc|nsa formulki` |

---

## 3. Jak zbierać dane

### 3.1 Rozmowy z modelem (cel C) - na co dzień
1. Rozmawiaj na `http://127.0.0.1:8080/etpc/zapytaj` albo `/nsa/zapytaj`. Wszystko zapisuje
   się samo do `badania/rozmowy/`.
2. Używaj trybów świadomie: **Szukaj w bazie (RAG)** do pytań o orzecznictwo, **Pismo** do
   pism, **Bezpośrednio** do reszty. Każdy tryb uczy co innego.
3. Pytaj o realne sprawy, także trudne (wąskie kwestie procesowe, sprzeczne linie
   orzecznicze) - z porażek jest najwięcej nauki.
4. Co jakiś czas napisz do Claude'a: **„oceń rozmowy”** (albo jedną: „oceń rozmowę
   <tytuł>”). Ocena trafia do `badania/rozmowy/oceny/` z odpowiedzią wzorcową.
5. Imion, nazwisk i adresów nie trzeba unikać, ale **przed treningiem trzeba je
   zanonimizować** (rozdz. 4).

### 3.2 Pytania wzorcowe i oceny wyszukiwania (cele A, B)
1. Nowe pytania pisze Claude w sesji (bez API, bez kosztów): czyta wycinki orzeczenia,
   pisze pytanie prawnika bez nazwy sprawy i bez słów z tekstu. Zapis: pytania do
   `<projekt>/pytania-*.json` (repo), źródła pytań do `<projekt>/database/trening/`.
2. Przebieg wyszukiwania z zapisem rankingu -> `wyniki-*.jsonl`.
3. Claude ocenia top 10 w skali 0-3 -> `oceny-top10-*.jsonl`.
4. Ty weryfikujesz próbkę: `etpc/database/trening/weryfikacja/` (instrukcja w środku).
   Zgodność Twoich ocen z Claude'em mówi, ile warte są pozostałe.
5. Cel: ~1 000 pytań ETPC (plan: `NOTEPAD.md`, „Plan na kolejny przebieg”), NSA podobnie.

### 3.3 Dokumenty (cel D)
Zakładka **Trening**: wrzucaj pisma (Word, PDF, skan), ustaw **rodzaj**, katalogi według
spraw. Eksport JSONL z linku na stronie.

### 3.4 Zasady zapisu (wszystkie cele)
- **Nic nie nadpisujemy.** Nowy przebieg = nowy plik z datą; oceny obok oryginału.
- **Pochodzenie przy każdym pliku:** kto (model, człowiek), kiedy, jak (metoda, wersja
  ustawień).
- **Podział robocza / testowa:** na testowej tylko mierzymy, nigdy nie stroimy ani nie
  trenujemy - inaczej wynik jest zawyżony.
- **Kopia:** `narzedzia/kopia-na-dysk.sh` (lustro całego katalogu na G:).

---

## 4. Jak trenować (plan)

### A + B. Embedder i reranker
1. **Dane:** z `oceny-top10-*.jsonl`: ocena 2-3 = pozytyw, 0 = trudny negatyw (z naszej
   wyszukiwarki, więc „podchwytliwy”); dołożyć pary z `ruling_links` (ETPC) i teza ->
   uzasadnienie (NSA); tylko część **robocza**.
2. **Gdzie:** Vast.ai (~$5 za eksperyment; instrukcja wynajmu w `docker/licz/VAST.md`) albo
   własna RTX 5070 Ti (koniec listopada).
3. **Jak:** sentence-transformers, strata z negatywami w paczce + trudne negatywy; reranker
   jako cross-encoder na parach z ocenami.
4. **Pomiar przed/po** na części **testowej**: `python -m etpc ocen --plik etpc/pytania-100.json`
   (MRR, w kontekście) + oceny top 10.
5. **Uwaga:** nowy embedder = nowe wektory całej bazy (ETPC ~1-1,5 h na 5070 Ti; NSA 23,7
   mln fragmentów - paczki / Vast). Najpierw zysk na ETPC, potem decyzja o NSA.

### C. Model czatu
1. **Dane:** z `badania/rozmowy/*.json` każda tura = przykład: wejście = `tury[].wyslane`
   (dokładnie to, co widział model), wyjście = `odpowiedz` (gdy ocena dobra) albo
   `odpowiedz_wzorcowa` z pliku oceny (gdy model się pomylił). Format: JSONL czatu
   (`messages`: system / user / assistant). Do tego odpowiedzi z `ocen-odpowiedzi`.
2. **Ile:** sensowny początek to kilkaset dobrych przykładów (różne tryby, obie bazy,
   także poprawne odmowy).
3. **Jak:** LoRA na Qwen (np. Unsloth / PEFT), na 5070 Ti albo Vast; potem eksport do
   GGUF i wczytanie w LM Studio jako nowy model.
4. **Pomiar przed/po:** `python -m etpc ocen-odpowiedzi` na tych samych pytaniach
   (zmyślone cytowania, sprzeczności wyniku, ocena sędziego) + ocena kilku rozmów przez
   Claude'a.
5. **Czego uczymy** (z ocen do tej pory): odróżniać stanowisko strony od poglądu sądu;
   podawać sygnaturę, sąd i datę w tekście; przy odmowie proponować drogę dalej; nie
   powoływać orzeczeń spoza źródeł; trzymać się pola WYNIK.

### C1. Pisma: QLoRA modelu 9B tylko do pisania pism (plan z 2026-10-29)

**Cel:** model pisze pisma tak, jak chce użytkownik (wnioski do WSA, wnioski o informację
publiczną, wnioski do gminy, skargi na bezczynność ...): układ, sformułowania, czego nie
pomijać, `[___]` zamiast zmyślonych danych. **Uczymy formy i stylu, nie prawa:** przepisy i
orzeczenia dalej daje baza przez tryb „Pismo” (model wytrenowany na starych pismach
powtarzałby nieaktualne podstawy prawne).

**Czy 9B wystarczy:** tak - do jednego wąskiego zadania mały model z LoRA uczy się dobrze
(układ, styl, stałe elementy pisma, trzymanie się danych z prośby). Słabsze zostaje
rozumowanie prawne w nietypowych sprawach - to część źródeł z bazy i przeglądu człowieka.
LoRA to osobny mały „dodatek” do modelu: bazowy Qwen zostaje do rozmów, dostrojony
wczytujemy w LM Studio jako drugi model tylko do pism.

**Dane - trzy źródła:**
1. **Gotowe pisma użytkownika** (najcenniejsze): zakładka Trening, rodzaj + uwagi (typ,
   adresat, co ważne, czy przepisy aktualne); 30-50 pism na każdy typ. Claude w sesji robi z
   każdego 3-5 próśb, które mogłyby do niego prowadzić (krótka / z danymi / bez danych) ->
   pary „prośba -> pismo”, dane osobowe zamienione na fikcyjne albo `[___]`; użytkownik
   przegląda próbkę ~20.
2. **Rozmowy w trybie „Pismo”** (`badania/rozmowy/`): dobre tury bez zmian, złe -> wersja
   poprawiona z oceny Claude'a (`badania/rozmowy/oceny/`).
3. **Warianty** wzoru (inna gmina, inny zakres, inny etap) - ostrożnie, nie więcej niż
   prawdziwych pism (inaczej model uczy się sztucznego szablonu).

**Kształt przykładu = kształt wejścia w aplikacji:** polecenie systemowe trybu „Pismo” +
źródła z bazy + „Polecenie: ...” (jak w `tury[].wyslane`), inaczej dostrojony model pisze
dobrze tylko poza naszą zakładką. Eksporter robi to sam.

**Ile:** ~200-500 dobrych przykładów łącznie na start; jakość ważniejsza niż liczba.
**~10% odłożone na test**, nigdy do treningu.

**Kroki:**

| # | Kto | Co |
|---|---|---|
| 1 | użytkownik | wybór 2-3 typów pism na start |
| 2 | Claude | nowe rodzaje w zakładce Trening (`judgments_core/training.py`, `KINDS`) |
| 3 | użytkownik | 30-50 pism każdego typu z uwagami |
| 4 | Claude | pary „prośba -> pismo” z anonimizacją -> `etpc/database/trening/pisma/` (do ustalenia); przegląd próbki przez użytkownika |
| 5 | oboje | rozmowy w trybie „Pismo” + „oceń rozmowy” na bieżąco |
| 6 | Claude | eksporter: baza `trening` + pary + ocenione rozmowy -> JSONL czatu (`messages`), podział trening / test |
| 7 | karta | QLoRA Qwen 3.5 9B (4-bit; Unsloth albo PEFT - obsługę Qwen 3.5 sprawdzić przed startem), kilka epok; RTX 5070 Ti 16 GB albo wcześniej Vast (kilka $); Radeon pod Windows słabo do treningu |
| 8 | Claude | eksport GGUF -> LM Studio jako drugi model |
| 9 | oboje | porównanie na odłożonych 10%: te same prośby do bazowego i dostrojonego, ocena na ślepo (układ, podstawy prawne, `[___]` zamiast zmyśleń, zgodność ze źródłami) + „które pismo byś wysłał” |

**Uwaga:** dane osobowe tylko w oryginałach w bazie `trening`; do treningu wyłącznie po
anonimizacji. Pisma z nieaktualnym przepisem oznaczyć w uwagach (poprawić albo pominąć).

**Porównanie wariantów treningu** (te same dane, ten sam odłożony zestaw testowy):

| | QLoRA | LoRA 16-bit | Pełny fine-tuning |
|---|---|---|---|
| Co się zmienia | mały dodatek, model bazowy w 4 bitach | mały dodatek, model bazowy w pełnej precyzji | wszystkie 9 mld wag |
| Pamięć karty (9B) | ~12-16 GB | ~30-40 GB | ~100-160 GB (wagi + gradienty + stan optymalizatora) |
| Gdzie | RTX 5070 Ti albo tani Vast | Vast, 1 karta 48 GB | Vast, 2× 80 GB |
| Koszt treningu (szacunek) | ~$1-5 | ~$3-10 | rzędu kilkudziesięciu $ |
| Wynik | dodatek kilkaset MB | dodatek kilkaset MB | cały nowy model (GGUF Q4 ~5-6 GB) |
| Ryzyko przy 200-500 przykładach | małe | małe | większe: przeuczenie, „zapominanie” innych umiejętności |

Przy małych danych i zadaniu „forma i styl” LoRA zwykle wypada podobnie jak pełny
fine-tuning - sprawdzamy to na naszych danych. **Kolejność:** najpierw QLoRA (tanio,
pokazuje, czy dane są dobre - słaby wynik to prawie zawsze dane, nie metoda); gdy działa,
na Vast LoRA 16-bit i pełny fine-tuning na tych samych danych; ewentualnie dwie rangi LoRA
(np. 16 i 64). **Ocena:** warianty (+ model bazowy bez treningu) piszą pisma na te same
prośby testowe, ocena na ślepo (Claude: układ, podstawy prawne, `[___]`, zgodność ze
źródłami; użytkownik: które pismo by wysłał) + kilka zwykłych pytań RAG przed/po (czy
model nie zepsuł się w innych zadaniach).

### C2. Zachowanie modelu: SFT + DPO (dane „jak się zachować”, nie „co wiedzieć”)

**Dwa rodzaje danych o zachowaniu:**
1. **Pokazy (SFT):** wejście -> wzorcowa odpowiedź. Model naśladuje.
2. **Pary preferencji (DPO):** to samo wejście, odpowiedź **lepsza** (`chosen`) i **gorsza**
   (`rejected`). Model uczy się, czego unikać - najskuteczniejsze na konkretne błędy.

**W publicznych zbiorach (sprawdzone 2026-10-29):** `NASK-PIB/PLLuM-Align` to polskie pary
`chosen` / `rejected` (format DPO; pliki `dialogs.jsonl`, `ranking.jsonl`, `rating.jsonl`),
ale **ogólne** - głównie bezpieczeństwo i uprzejmość (przykład: „jak dopiec koledze” ->
odmowa z radami). `pelcra/PLLuMIC` - polskie instrukcje (dostęp na wniosek). **Żaden zbiór
nie uczy naszych zachowań:** odpowiadać tylko ze źródeł, cytować [n] i sygnatury, odróżniać
stanowisko strony od poglądu sądu, wynik tylko z pola WYNIK, `[___]` zamiast zmyśleń,
odmowa z propozycją drogi dalej. PLLuM-Align co najwyżej jako mała domieszka (~10%),
ostrożnie: jego odmowy mogą nauczyć model odmawiać za często.

**Najlepsze dane zachowania mamy sami - z ocen rozmów:**
- każda oceniona tura, w której model się pomylił = **gotowa para DPO**: `rejected` =
  odpowiedź modelu (`tury[].odpowiedz`), `chosen` = odpowiedź wzorcowa z oceny
  (`badania/rozmowy/oceny/`), wejście = `tury[].wyslane`; dobre tury = pokazy SFT;
- **trudne przypadki do wygenerowania** przez Claude'a w sesji, z prawdziwych fragmentów
  naszych baz: brak źródeł (poprawna odmowa z podpowiedzią), fragment ze stanowiskiem
  strony (przypisać stronie), źródło sprzeczne z „wiedzą” modelu (trzymać się źródła i
  pola WYNIK), prośba o pismo bez danych (`[___]`), pytanie poza bazą (powiedzieć wprost);
  do każdego odpowiedź dobra i typowa zła = para DPO.

**Przepis:** najpierw SFT na dobrych przykładach (~200-500), potem DPO na parach (~200+),
oba jako LoRA (np. TRL / Unsloth - obsługę Qwen 3.5 sprawdzić przed startem). Pomiar jak w
C1 (odłożony zestaw, ocena na ślepo) + `ocen-odpowiedzi` (zmyślone cytowania, sprzeczności
wyniku). Każdy błąd znaleziony w ocenach rozmów -> nowa para -> następna runda.

### D. Wzorce dokumentów
Na eksporcie bazy `trening`: najpierw bez trenowania (wzorce regex w `nsa/formulas.py`,
podobieństwo do formułek), trening klasyfikatora rodzaju / zdań-formułek dopiero przy
kilkuset oznaczonych dokumentach.

---

## 4a. Rozmiary modeli: 9B / 14B / 27-32B (do testów po zakupie RTX 5070 Ti 16 GB)

„B” = miliardy wag. Większy model lepiej rozumuje, lepiej pisze po polsku, dokładniej
trzyma się długich poleceń i źródeł, mniej zmyśla - ale potrzebuje więcej pamięci karty i
jest wolniejszy. Liczby niżej to **szacunki** (Q4 = kwantyzacja 4-bit w LM Studio) - do
zmierzenia na karcie.

| | **9B** (Qwen 3.5 9B, obecny) | **14B** (np. Qwen3-14B) | **27-32B** (np. Qwen 27B / 32B) |
|---|---|---|---|
| Plik Q4 | ~5,5-6 GB | ~9 GB | ~16-20 GB |
| Na 16 GB karty | cały, z dużym kontekstem (32k+) | cały, kontekst ~16-32k | **nie mieści się cały**: część warstw w RAM (64 GB) -> kilka razy wolniej; albo Q3 z krótkim kontekstem (gorsza jakość) |
| Szybkość (szac.) | najszybszy | ~1,5-2× wolniej niż 9B | z częścią w RAM ~5-15 tok/s |
| Jakość RAG (odpowiedzi ze źródeł EN/FR/PL) | poprawna, myli się przy niuansach (stanowisko strony vs sąd, wyniki spraw) | wyraźnie lepsza; nasz pomiar qwen3-14b: 86% zdań popartych źródłem (sędzia), 0 zmyślonych cytowań | najlepsza; mniej uogólnień ponad źródło, lepsza polszczyzna prawnicza |
| Pisanie pism | po dostrojeniu (C1) dobre w formie; słabsze w doborze argumentów | lepszy dobór argumentów | najlepszy, ale wolny |
| QLoRA na 16 GB | **tak** | na granicy (krótkie przykłady) albo Vast | tylko Vast (24-48 GB) |
| Do czego | pisma po dostrojeniu, szybkie rozmowy | RAG na co dzień | trudne sprawy, sędzia ocen (zamiast Claude'a), gdy czas nie gra roli |

**Decyzja (2026-10-29):** do zakupu karty wyciskamy ile się da z 9B (polecenia, RAG, dane,
pierwsza QLoRA na Vast); po zakupie RTX 5070 Ti 16 GB przejście na 14B i testy z planu niżej.

**Trzecia droga - MoE** (np. Qwen3-30B-A3B): 30 mld wag, ale przy każdym słowie liczy
tylko ~3 mld. Nie mieści się cały na karcie, ale z częścią w RAM i tak jest szybki;
jakość między 14B a 32B. Wart sprawdzenia przy 64 GB RAM.

**Plan testów po zakupie karty** (wszystko na tych samych zestawach):
1. Modele: 9B, 9B + LoRA do pism (C1), 14B, 27-32B (część w RAM), MoE 30B-A3B.
2. Miary: czas do pierwszego słowa przy promptach RAG (~5k tokenów), tokeny/s, pamięć
   karty; jakość RAG: `python -m etpc ocen-odpowiedzi` (cytowania, reguła wyniku, sędzia);
   pisma: odłożony zestaw testowy z C1, ocena na ślepo.
3. Hipotezy do sprawdzenia: dostrojony 9B pisze pisma lepiej niż niedostrojony 14B;
   27-32B daje najlepsze odpowiedzi RAG, ale czy różnica jest warta czasu; MoE jako
   kompromis.
4. Wynik: który model do czego (rozmowy / RAG / pisma / sędzia) - ustawienie w LM Studio
   i `<PREFIKS>_CHAT_MODEL`.

## 4b. Publiczne zbiory danych (Hugging Face) - sprawdzone 2026-10-29

### W skrócie

**Najciekawsze dla nas:**
- **`JuDDGES/juddges-pl`** (Politechnika Wrocławska, wersja z 2026-10-22): **orzeczenia z
  pełnym tekstem** (sądy powszechne + całe NSA/WSA), a do każdego pola wyciągnięte przez
  Gemini 2.5 Pro: stan faktyczny, stan prawny, streszczenie, **teza**, podstawy prawne,
  wynik. CC BY 4.0. **Pola z Gemini ma tylko ok. 14% orzeczeń NSA** (sprawdzone w pliku
  `nsa-00000.parquet`: teza od Gemini w 6 790 z 50 000; zakładając podobnie w reszcie, ok.
  300 tys. z 2,25 mln). W CBOSA tezę ma ~1,8% -> i tak kilka razy więcej tez, gotowe pary
  „stan faktyczny -> teza” dla embeddera. Zastrzeżenia: pola z Gemini to etykiety „srebrne”
  (bez ręcznej weryfikacji; autorzy: nie do testów i nie do decyzji prawnych); warunki
  użycia wyników Gemini do treningu - sprawdzić.
- **`overthelex/pl-court-decisions`** (2026-05): 2,83 mln orzeczeń, w tym **SN, TK i KIO z
  SAOS** (brak ich w JuDDGES). CC BY 4.0. Do porównania z planem „SAOS jako trzecie dziecko”.
- **PolEval 2022**, podzbiór `legal-questions`: polskie pytania prawne + trafne fragmenty,
  do treningu wyszukiwania. Licencji brak w metadanych - sprawdzić.

**Czy uzupełnią naszą bazę NSA (mamy do 2025-03)? Nie.** Część NSA w `juddges-pl` ma
dokładnie **2 254 392** wiersze = `JuDDGES/pl-nsa-enriched`, zbudowany z `JuDDGES/pl-nsa`
(CBOSA do 2025-03-05) - to ten sam zrzut, z którego jest nasza baza. Nowe są tylko pola z
Gemini (teza, stan faktyczny ...), nie orzeczenia. `overthelex` ma NSA też z `pl-nsa`
(2004-2025); nowsze (do 2026) są u niego tylko SN, TK, KIO i sądy powszechne z SAOS.
Lukę po 2025-03 wypełnia dalej tylko CBOSA (`nsa pobierz` z numerami ID). Maksymalnej daty
nie dało się odczytać wprost (indeks wyszukiwania Hugging Face był 2026-10-29 uszkodzony)
- sprawdzić ponownie; wniosek opiera się na liczbie wierszy i źródle zbioru.
**Pola z Gemini możemy dołączyć do naszych orzeczeń** (klucz: sygnatura): tezy i stan
faktyczny dla orzeczeń bez tezy - do wyszukiwania i do par treningowych. **Nie trzeba
pobierać 32 GB:** parquet czyta się kolumnami; same kolumny `extracted_*` + sygnatura to
ok. 20 MB na 50 tys. orzeczeń, czyli **ok. 1 GB dla całego NSA** (pełne teksty = 95%
rozmiaru, a te już mamy).

**Do domieszki polszczyzny przy QLoRA** (żeby model nie „zapomniał” języka):
`NASK-PIB/PLLuM-Align` (CC BY-SA); `pelcra/PLLuMIC` (dostęp na wniosek).

**Do wyszukiwania po polsku:** `ipipan/polqa` (ręcznie oznaczony), `ipipan/maupqa` (1 mln
pytań z trudnymi negatywami).

**Do ETPC:** `coastalcph/lex_glue` (CC BY), `AUEB-NLP/ecthr_cases` (tylko niekomercyjnie).

**Poprawki względem listy z pamięci:** `speakleash/PES-2018-2022` to egzaminy lekarskie, nie
prawnicze; korpusów SpeakLeash nie ma na Hugging Face jako zbioru. LEXTREME i Multi Legal
Pile nie mają polskich orzeczeń (tylko ustawodawstwo i prawo UE).
`Robert19001/pl-legal-instruct-sample` to darmowa próbka 50 par, pełny zbiór płatny - sami
zrobimy lepsze dane z naszych baz.

**Licencje (ważne przy produkcie dla kancelarii):** CC BY-NC-SA = tylko niekomercyjnie
(`ecthr_cases`, `Multi_Legal_Pile` - komercyjna jest wersja `_Commercial`,
`abusive-clauses-pl`). CC BY-SA = pochodne na tej samej licencji; czy obejmuje to wagi
modelu wytrenowanego na takich danych - sporne. Same orzeczenia i ustawy są w Polsce wolne
od praw autorskich (dokumenty urzędowe, art. 4 pr. aut.); licencja dotyczy opracowania zbioru.

**Wniosek:** do pisania pism w stylu użytkownika żaden publiczny zbiór nie pomoże (polskich
wniosków do gmin, o informację publiczną, do WSA tam nie ma). Publiczne zbiory - do
wyszukiwania (embedder) i jako domieszka języka; pisma muszą pochodzić od użytkownika.

**Źródła:** huggingface.co/datasets/ + `JuDDGES/juddges-pl`, `JuDDGES/pl-nsa-enriched`,
`JuDDGES/pl-court-raw`, `JuDDGES/pl-court-instruct`, `overthelex/pl-court-decisions`,
`piotr-rybak/poleval2022-passage-retrieval-dataset`, `ipipan/polqa`, `ipipan/maupqa`,
`NASK-PIB/PLLuM-Align`, `coastalcph/lex_glue`, `AUEB-NLP/ecthr_cases`,
`joelniklaus/Multi_Legal_Pile`, `speakleash/PES-2018-2022`, `Robert19001/pl-legal-instruct-sample`.

### Plan importu pól Gemini (`JuDDGES/juddges-pl`) do bazy NSA

**Kiedy:** po końcu przebiegu tematów NSA (import obciąża bazę NSA). **Kto:** kod Claude,
uruchomienie po decyzji użytkownika.

**Co mamy dostać:** dla ok. 300 tys. orzeczeń NSA/WSA (~14%) pola wygenerowane przez Gemini
2.5 Pro: `extracted_thesis`, `extracted_factual_state`, `extracted_legal_state`,
`extracted_summary`, `extracted_outcome`, `extracted_legal_bases`,
`extracted_legal_references`, `extracted_keywords`, `extracted_legal_concepts`,
`extracted_parties`, `extracted_legal_analysis`, `extracted_judgment_specific`. Orzeczeń
nie dostajemy nowych - ten sam zrzut CBOSA co nasza baza.

**Kroki** (zgodnie z zasadami projektu: najpierw surowe dane, wznawialnie, dziecko klasy
z rdzenia, bez zmian w rdzeniu):
1. **Źródło:** `JuddgesGeminiSource(JuddgesSource)` w `nsa/juddges.py` - zbiór
   `JuDDGES/juddges-pl`, pliki `data/nsa-*.parquet`, czytane **tylko kolumnami**
   `judgment_id` (= numer CBOSA, ten sam klucz co nasze `raw_records` źródła `juddges`),
   `docket_number` i `extracted_*` (~1 GB zamiast 32 GB; odczyt zakresami HTTP albo
   pobranie plików i usunięcie po imporcie). Wiersze bez żadnego pola `extracted_*`
   pomijane. Kursor jak dziś `{"file": n, "row": k}`; wersja zbioru (commit Hugging Face)
   zapisana, żeby wiedzieć, z czego jest import.
2. **Surowe:** każdy wiersz = `RawRecord` źródła **`juddges-gemini`** (JSON pól), w
   `raw_records` obok `juddges` i `cbosa` - zmiana parsera nie wymaga ponownego pobierania.
3. **Tabela:** migracja `nsa/migrations/006_gemini.sql` (005 zajmują tematy) - np.
   `judgment_generated` (id orzeczenia, teza, stan faktyczny, stan prawny, streszczenie,
   wynik, podstawy, słowa kluczowe, `model = 'gemini-2.5-pro'`, wersja zbioru, data
   importu). **Osobno od prawdziwych tez z CBOSA** - nigdy nie mieszać.
4. **Przetwarzanie:** krok w `NsaDeriver` (dziecko w `nsa/`): `juddges-gemini` -> tabela,
   dopasowanie po numerze CBOSA (zapasowo po sygnaturze).
5. **Uruchomienie:** w kontenerze z pyarrow (nie ma go w obrazie; jak przy pierwszym
   imporcie JuDDGES - tylko tam, gdzie działa import), `--budzet`, wznawialnie.
6. **Sprawdzenie:**
   - ile orzeczeń dostało pola (czy ~14% w każdym pliku, czy inaczej);
   - **tam, gdzie jest też prawdziwa teza z CBOSA** (~40 tys.): porównanie tezy Gemini z
     tezą sądu na próbce ~50 (Claude w sesji) - miara, ile warte są pozostałe;
   - najpóźniejsza data orzeczenia (potwierdzenie, że nic po 2025-03).
7. **Licencja i warunki:** zbiór CC BY 4.0 (cytować JuDDGES-pl i model Gemini 2.5 Pro);
   **przed treningiem** sprawdzić warunki Google co do używania wyników Gemini do trenowania
   modeli.

**Do czego potem** (każde osobno, po sprawdzeniu jakości):
- **pary treningowe** embeddera / rerankera: stan faktyczny -> teza, pytanie -> orzeczenie
  (A, B) - główny cel;
- ewentualnie **wyszukiwanie**: teza Gemini jako krótki tekst do wektora („poziom 1”
  NSA) albo do pełnotekstowego - zmierzyć `ocen` NSA przed/po;
- ewentualnie **RAG i strona orzeczenia**: pokazywać tylko z wyraźnym oznaczeniem „teza
  wygenerowana (Gemini), nie sądu”.

**Rozmiar w bazie (szacunek):** ~1-2 GB tekstu dla ~300 tys. orzeczeń.

### Szczegóły (tabele)

Sprawdzone w API Hugging Face (istnienie, licencja, rozmiar, dostęp, ostatnia zmiana).
**Do pisania pism w stylu użytkownika publiczne zbiory prawie nic nie dadzą** - polskich
pism administracyjnych (wnioski do gminy, o informację publiczną, do WSA) w otwartych
zbiorach nie ma; tu liczą się własne dane (C1).

**Licencje:** CC BY = wolno wszystko z podaniem autora; CC BY-SA = pochodne na tej samej
licencji; **CC BY-NC(-SA) = tylko niekomercyjnie** (ważne przy produkcie dla kancelarii). Same
orzeczenia i akty prawne są w Polsce wolne od praw autorskich (dokumenty urzędowe, art. 4
pr. aut.); licencja dotyczy opracowania zbioru. Pola wygenerowane przez Gemini (JuDDGES
„enriched”) - przed użyciem do treningu sprawdzić warunki ich użycia.

**Polskie orzecznictwo i prawo**

| Zbiór | Co | Licencja | Rozmiar | Do czego u nas |
|---|---|---|---|---|
| `JuDDGES/juddges-pl` (PWr, wersja z 2026-10-22, zgłoszenie NeurIPS 2026) | orzeczenia sądów powszechnych (`pl-court`) i administracyjnych (`pl-nsa`) + pola z Gemini 2.5 Pro: stan faktyczny, stan prawny, streszczenie, **teza**, podstawy prawne, słowa kluczowe, wynik | CC BY 4.0 (pola LLM = „srebrne”, nie ręczne) | 1-10 mln | pary **stan faktyczny -> teza / uzasadnienie** dla embeddera (A) i do czytania orzeczeń (C); pola z Gemini ma ok. 14% orzeczeń NSA (~300 tys.) |
| `JuDDGES/pl-nsa`, `JuDDGES/pl-nsa-enriched` | to samo dla NSA osobno (surowe ~1,8 mln; wzbogacone ~69 GB) | CC BY 4.0 | 1-10 mln | już z `pl-nsa` czerpiemy (`nsa/juddges`) |
| `JuDDGES/pl-court-raw`, `JuDDGES/pl-court-instruct` | sądy powszechne (~437 tys., ~26 GB) + instrukcje (wyciąganie informacji z wyroków) | CC BY 4.0 | 100 tys.-1 mln | instrukcje: wzór formatu do C; sądy powszechne = poza naszymi bazami |
| `JuDDGES/pl-swiss-franc-loans`, `JuDDGES/pl-appealcourt-criminal` | oznaczone sprawy frankowe; karne apelacyjne | CC BY 4.0; „other” | 1-10 tys. | przykłady oznaczania (wzór), mało dla nas |
| `overthelex/pl-court-decisions` (arXiv 2605.29738, 2026-05) | **2,83 mln** orzeczeń: powszechne + administracyjne + **SN, TK, KIO z SAOS** (brak ich w JuDDGES) | CC BY 4.0 | 1-10 mln | gotowe SAOS (SN/TK/KIO) - do porównania z naszym planem „SAOS jako trzecie dziecko” |
| `radlab/legal-mc4-pl` | polskie teksty prawne z sieci (z `legal-mc4`) | CC BY 4.0 | 100 mln-1 mld | domieszka polszczyzny prawniczej; jakość nieznana (strony WWW) |
| `piotr-rybak/poleval2022-passage-retrieval-dataset` (podzbiór `legal-questions`) | polskie pytania prawne + trafne fragmenty | brak w metadanych (sprawdzić) | - | trening i ocena embeddera (A) |
| `laugustyniak/abusive-clauses-pl`; `allegro/abusive-clauses-pl-en` | klauzule niedozwolone (benchmark LEPISZCZE) | **CC BY-NC-SA**; Apache 2.0 | do 10 tys.; < 1 tys. | sprawdzian po polsku |
| `Robert19001/pl-legal-instruct-sample` | 50 par instrukcja-odpowiedź z orzeczeń SAOS (próbka; pełne 3,6 tys. **płatne**) | Apache 2.0 (próbka) | 50 | tylko podgląd formatu; samemu zrobimy lepsze z naszych baz |

**Polskie ogólne (domieszka języka, wyszukiwanie)**

| Zbiór | Co | Licencja | Do czego |
|---|---|---|---|
| `ipipan/polqa` | 7 tys. pytań, 87 tys. ręcznie oznaczonych fragmentów | CC BY-SA 4.0 | wyszukiwanie po polsku (A) |
| `ipipan/maupqa` | 1 mln pytań, 7 mln trudnych negatywów (głównie maszynowe) | CC BY-SA 4.0 | trening embeddera po polsku (A) |
| `NASK-PIB/PLLuM-Align` | dane dopasowania modelu PLLuM (opis do przeczytania) | CC BY-SA 4.0 | domieszka polskich instrukcji przy QLoRA (żeby model nie „zapomniał” języka) |
| `pelcra/PLLuMIC`, `pelcra/PLLuMIC-syn-ext` | polskie instrukcje PLLuM | CC BY-SA 4.0, **dostęp na wniosek** | j.w. |

**Angielskie / wielojęzyczne**

| Zbiór | Co | Licencja | Do czego |
|---|---|---|---|
| `coastalcph/lex_glue` (`ecthr_a`, `ecthr_b`) | sprawy ETPC: fakty -> zarzucane / naruszone artykuły | CC BY 4.0 | sprawdzian i trening rozumienia ETPC (A, C) |
| `AUEB-NLP/ecthr_cases` | sprawy ETPC z artykułami i uzasadnieniami | **CC BY-NC-SA 4.0** | j.w., tylko niekomercyjnie |
| `coastalcph/multi_eurlex` | prawo UE w 23 językach (też PL), etykiety EuroVoc | CC BY 4.0 | wyszukiwanie wielojęzyczne (A) |
| `joelniklaus/lextreme` | benchmark prawny 24 języków (polski = tylko MultiEURLEX) | CC BY 4.0 | ogólna ocena |
| `joelniklaus/Multi_Legal_Pile` / `..._Commercial` | korpus tekstów prawnych (polskie: ustawodawstwo, **bez orzeczeń**) | **CC BY-NC-SA** / CC BY-SA | ewentualnie dalsze uczenie; wersja `_Commercial` do produktu |
| `nguha/legalbench` | rozumowanie prawne (USA) | CC BY 4.0 | tylko ogólny sprawdzian |

**Odpadło po sprawdzeniu:** `speakleash/PES-2018-2022` to egzaminy **lekarskie** (nie
prawnicze); korpusów SpeakLeash nie ma na HF jako zbioru (pobiera się je ich biblioteką).
`Gitbart/Polish_law` - dostęp zamknięty, < 1 tys. przykładów.

**Nasza przewaga:** własna baza jest większa i lepiej opisana niż większość publicznych
zbiorów (2,25 mln orzeczeń NSA/WSA, 83 tys. ETPC z cytowaniami) - pary teza -> uzasadnienie,
akapit cytujący -> cytowane, pytania od Claude'a z ocenami 0-3 robimy sami (rozdz. 5).
**Uwaga przy ocenie:** model mógł widzieć publiczne zbiory w czasie pierwotnego treningu
- wtedy wynik na nich jest zawyżony; miarodajny jest nasz odłożony zestaw testowy.

## 5. Czego brakuje (do zrobienia)

1. **Skrypt przebiegu z zapisem rankingu** (`wyniki-*.jsonl`) jest tylko w scratchpadzie
   starej sesji, nie w repo -> przenieść jako `ocen --zapisz`.
2. **Eksport rozmów do treningu**: polecenie, które z `badania/rozmowy/` + `oceny/` robi
   JSONL czatu (z anonimizacją imion, nazwisk, adresów).
3. **Eksport par** z `ruling_links` (ETPC) i teza -> uzasadnienie (NSA).
4. **`ocen` NSA** jeszcze nie uruchomiony (czeka na koniec przebiegu tematów NSA).
5. **Więcej pytań:** ETPC 99 -> ~1 000; NSA 100 + oceny top 10.
6. **Więcej ocenionych rozmów** (dziś 1).
7. **Twoja weryfikacja** arkusza `weryfikacja-ocen-etpc.csv`.
8. **Import pól Gemini z `JuDDGES/juddges-pl`** do bazy NSA (plan w rozdz. 4b) - po
   przebiegu tematów NSA.

## 6. Kolejność

1. Teraz, na co dzień: rozmowy w zakładce + „oceń rozmowy” co kilka dni; dokumenty w
   zakładce Trening.
2. Po przebiegu tematów NSA: `ocen` NSA i oceny top 10 NSA.
3. Braki 1-3 z rozdz. 5 (kod, małe).
4. Duży zestaw pytań ETPC (~1 000).
5. Pierwszy eksperyment: embedder na Vast (A), pomiar na części testowej.
6. Po zebraniu kilkuset przykładów: LoRA modelu czatu (C), najlepiej na własnej karcie.
