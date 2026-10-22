# Liczenie paczek wektorów na Vast.ai (wynajęte GPU)

Vast.ai wynajmuje karty graficzne na godziny: dostajesz kontener Linuksa z GPU,
łączysz się przez SSH, wgrywasz paczki, liczysz, pobierasz wektory i **usuwasz
instancję**. Liczy ten sam skrypt co u kolegów (`licz_paczke.py`), ten sam model
bge-m3, więc wektory są zgodne z Twoimi (import sprawdza to wektorami kontrolnymi).

Na maszynę idą tylko pliki paczek (`*.jsonl.gz`: fragmenty publicznych orzeczeń).
Nigdy nie wgrywaj `.env`, kopii bazy ani kluczy prywatnych - host to obca osoba.

Wszystko przez stronę https://cloud.vast.ai (bez ich programu `vastai`). Polecenia
poniżej: PowerShell w katalogu repozytorium; `ssh` i `scp` są w Windows. Polecenia
projektu (`paczki`, `paczka-import`) idą w kontenerze, jak wszystko w repo:
`docker compose run --rm --no-deps www python -m nsa ...` (Docker Desktop musi działać).

## 1. Klucz SSH (raz)

Vast wpuszcza przez SSH po kluczu, nie po haśle. Klucz to para plików w `~\.ssh`:
`id_ed25519` (**prywatny** - nigdy nikomu) i `id_ed25519.pub` (**publiczny** - ten
wklejasz do Vast).

**Czy już masz klucz:**
```powershell
Test-Path $HOME\.ssh\id_ed25519.pub
```
`True` = masz, przejdź dalej. `False` = utwórz:
```powershell
ssh-keygen -t ed25519 -C "vast"
```
Na pytanie o miejsce zapisu naciśnij Enter (domyślnie `C:\Users\<Ty>\.ssh\id_ed25519`).
Hasło do klucza (passphrase): możesz podać (bezpieczniej; będziesz je wpisywać przy
każdym `ssh`/`scp`) albo zostawić puste (Enter dwa razy). Jeśli pyta „Overwrite?”,
odpowiedz `n` - klucz już był, nie nadpisuj go.

**Publiczny klucz do skopiowania:**
```powershell
Get-Content $HOME\.ssh\id_ed25519.pub
```
Cała jedna linia, zaczyna się od `ssh-ed25519 ...`.

## 2. Konto (raz)
1. Konto na https://cloud.vast.ai, doładuj kredyt (na próbę wystarczy $5-10).
2. **Account -> SSH Keys -> Add**: wklej linię z `id_ed25519.pub`.

## 3. Paczki u Ciebie
```powershell
docker compose run --rm --no-deps www python -m nsa paczki --ile 12 --fragmentow 500000
docker compose run --rm --no-deps www python -m nsa paczki --lista
```
Pliki w `nsa\database\paczki\`: `nsa-NNNN.jsonl.gz` (do wysłania, ~180 MB na 500 tys.
fragmentów) i `nsa-NNNN.meta.jsonl.gz` (zostaje u Ciebie, potrzebny do importu).
Z powrotem przychodzi `nsa-NNNN.wektory.npy` (~1 GB na paczkę: float16, 2 KB na fragment).

## 4. Template, potem karta

**Najpierw template** (od niego zależy, które maszyny pasują):
- bierz **PyTorch** (oficjalny template Vast): ma CUDA i PyTorch, skrypt dociąga tylko
  sentence-transformers (~1 min);
- nie: **NVIDIA CUDA** (goły obraz - trzeba by ściągać PyTorch, 2-3 GB), **vLLM** (do modeli
  czatu, nie embeddingów), **all-in-one** (ciężki obraz, dłużej się pobiera, a płacisz od startu).

Ustawienia templatu **PyTorch (Vast)** (ołówek „Edit”):
1. Image `vastai/pytorch:@vastai-automatic-tag`, Version Tag **[Automatic]**, Docker Options i
   Ports - zostaw (Vast dobiera wersję obrazu do CUDA maszyny; Python z PyTorchem jest w
   `/venv/main`, `vast_start.sh` sam go znajduje);
2. Launch Mode: **„Interactive shell server, SSH”**; On-start Script (`entrypoint.sh`) zostaw;
3. Extra Filters: `cuda_max_good>=12.4` zmień na **`cuda_max_good>=12.8`** - karty Blackwell
   (RTX 50xx, RTX PRO 4000) potrzebują CUDA 12.8:
   `cpu_arch in ['amd64', 'arm64'] cuda_max_good>=12.8 compute_cap>=750`;
4. **Create & Use**, potem na liście ofert dysk (Disk Space) **40 GB**.

**Potem karta** (lista ofert, Search):
1. GPU, liczba kart 1, **Verified**, niezawodność (reliability) > 98%;
2. sortuj po cenie; w szczegółach oferty sprawdź **opłaty za transfer** (internet
   up/down, $/TB) - wektory to ~1 GB na paczkę, więc drogi transfer zjada oszczędność:
   wybieraj ~0;
3. **Rent**.

### Jaka karta
bge-m3 jest mały (~570 mln parametrów, 2-4 GB pamięci karty): liczy się moc fp16 na
dolara, nie ilość VRAM (8 GB wystarczy). Szacunkowo (sprawdź testem, krok 7):

| karta | szybkość | kiedy brać |
|---|---|---|
| RTX 5090 | ~1.3-1.6x 4090 | gdy nie jest dużo droższa od 4090 |
| **RTX 4090** | punkt odniesienia | zwykle dobra cena za token, dużo ofert |
| RTX 3090 | ~0.5-0.6x 4090 | za mniej niż połowę ceny 4090 |
| RTX PRO 4000 | ~0.4-0.6x 4090 | za mniej niż połowę ceny 4090 |
| RTX 5060 Ti | ~0.3x 4090 | najtańsza za godzinę; najlepiej kilka naraz (każda inne paczki) |
| A100 / H100 | szybkie | zwykle drożej za token |

Porównuj **koszt miliona tokenów**, nie cenę za godzinę:

    $ za 1M tokenów = cena_za_godzinę / (tokenów_na_s * 3600 / 1 000 000)

Np. 4090 za $0.40/h liczy 40 tys. tokenów/s -> 144 mln tokenów/h -> ~$0.003 za 1M tokenów.
Cały NSA (poziom 1, ~5.7 mln fragmentów, ~10 mld znaków) to ~3 mld tokenów (znaki / 3.3).

## 5. Start i połączenie
1. **Instances**: czekaj na status **running** (pobiera obraz, kilka minut). Opłata za
   dysk biegnie od utworzenia, za GPU od `running`. Status `exited`, `offline`, `unknown`
   = nie ruszy: usuń (krok 10) i weź inną ofertę.
2. Przycisk **Connect** (ikona terminala) przy instancji pokazuje polecenie, np.
   `ssh -p 41234 root@1.2.3.4` - to Twoje **PORT** i **HOST** do kroków niżej.
3. Pierwsze połączenie (pytanie o odcisk hosta: odpowiedz `yes`):
   ```powershell
   ssh -p PORT root@HOST
   ```
   Jesteś na maszynie, gdy widzisz `root@...:~#`; `exit` wraca do Windows.
   „Permission denied (publickey)” = Vast nie ma Twojego klucza (krok 2) albo dodałeś go
   po utworzeniu instancji - dodaj klucz w samej instancji (ikona klucza w Instances)
   albo utwórz instancję od nowa.

## 6. Wgranie skryptu i paczek (z PowerShell, w katalogu repo)
```powershell
ssh -p PORT root@HOST "mkdir -p /workspace/paczki"
scp -P PORT docker\licz\licz_paczke.py docker\licz\vast_start.sh root@HOST:/workspace/
scp -P PORT nsa\database\paczki\nsa-0002.jsonl.gz root@HOST:/workspace/paczki/
```
W `ssh` port to małe `-p`, w `scp` duże `-P`. Pliki `*.meta.jsonl.gz` zostają u Ciebie.

## 7. Test szybkości
```powershell
ssh -p PORT root@HOST "bash /workspace/vast_start.sh test"
```
Instaluje sentence-transformers (~1 min), pobiera model (~2.3 GB) i liczy 5000
fragmentów pierwszej paczki; podaje **tokenów/s**. Koszt: tokeny ≈ znaki / 3.3 (znaki
pokazuje `paczki --lista`), godziny = tokeny / (tokenów/s) / 3600, razy $/h.

## 8. Liczenie
Wgraj pozostałe paczki dla tej maszyny (krok 6) i:
```powershell
ssh -p PORT root@HOST "bash /workspace/vast_start.sh"
ssh -p PORT root@HOST "tail -n 5 /workspace/licz.log"    # postęp, kiedy chcesz
```
Liczy w tle wszystkie paczki po kolei; rozłączenie SSH ani wyłączenie Twojego komputera
nie przerywa. Koniec = w logu `WSZYSTKO GOTOWE`. Opcja: `BATCH=128 bash /workspace/vast_start.sh`
(większa porcja naraz, jeśli karta ma zapas pamięci).

## 9. Pobranie wektorów i import
```powershell
scp -P PORT "root@HOST:/workspace/paczki/*.wektory.npy" nsa\database\paczki\
docker compose run --rm --no-deps www python -m nsa paczka-import nsa-0002
```
(Import każdej paczki osobno.) Import odrzuci paczkę, jeśli wektory kontrolne się nie
zgadzają (inny model) albo plik jest niepełny - wtedy policz ją jeszcze raz.

## 10. USUŃ INSTANCJĘ
**Instances -> Destroy** (kosz). **Stop** zatrzymuje tylko GPU - **dysk dalej kosztuje**.
Usuń, gdy wektory są u Ciebie i zaimportowane; potem sprawdź, że lista Instances jest pusta.

## Automatycznie: kolejka z paczką do przodu (`narzedzia/vast_kolejka.sh`)

Zamiast kroków 6-9 ręcznie: skrypt u Ciebie (Git Bash, w katalogu repo) trzyma na maszynie
zawsze 2 paczki (liczona + następna już wysłana, więc karta nie czeka na wysyłanie),
gotowe wektory od razu pobiera, importuje i usuwa z maszyny (dysk). Na maszynie liczy
`vast_start.sh` z `CZEKAJ_S=1800` (po każdej paczce na nowo przegląda katalog i czeka 30 min
na dosyłane); skrypt sam go uruchamia i wznawia, gdy przestanie działać.
```bash
nohup bash narzedzia/vast_kolejka.sh 10691 179.255.148.147 >> logs/vast_kolejka.log 2>&1 &
tail -f logs/vast_kolejka.log        # postęp (Ctrl+C kończy tylko podgląd)
```
Bez nazw paczek bierze wszystkie z `nsa/database/paczki/` bez znacznika `.zaimportowano`, a gdy
się skończą, dobiera nowo wyeksportowane (eksport `paczki` może iść w tym czasie). Opcje:
`PROJEKT=etpc`, `NAPRZOD=3`, albo nazwy paczek po HOST.

**Wznawianie** - przerwane (Ctrl+C, zamknięty terminal, restart komputera) uruchom tym samym
poleceniem jeszcze raz:
- zaimportowana paczka ma u Ciebie plik `<nazwa>.zaimportowano` - pomijana;
- wektory ściągnięte, import przerwany - tylko import, bez ponownego pobierania;
- paczka już na maszynie - nie jest wysyłana drugi raz (wysyłka idzie jako `.wysylane`
  i dopiero pełna dostaje właściwą nazwę, więc maszyna nie liczy niepełnej);
- na maszynie `licz_paczke.py` wznawia od brakującej części (co 4096 fragmentów); wynik
  zapisuje jako `.wektory.npy.zapis` i zmienia nazwę po zapisie, a kolejka bierze go dopiero
  po linii `Gotowe:` w logu;
- nieudany import zatrzymuje kolejkę (log w `<nazwa>.import.log`) - paczka zostaje na maszynie.

Nie edytuj `vast_kolejka.sh` ani `vast_start.sh`, gdy działają (bash czyta skrypt w trakcie):
zatrzymaj, popraw, uruchom znowu.

## Przykład krok po kroku (RTX 4070, 2026-10-23)

Prawdziwa sesja: instancja 57277216, RTX 4070 12 GB, ~$0.20/h. Z przycisku **Connect**
(Instances, ikona `>_`, linia „Direct ssh connect”):
`ssh -p 10691 root@179.255.148.147 -L 8080:localhost:8080` -> **PORT = 10691**,
**HOST = 179.255.148.147** (część `-L ...` niepotrzebna). Przy nowej instancji podstaw swoje.
Wszystko z PowerShell w katalogu repozytorium.

**1. Połączenie i sprawdzenie maszyny** (karta, sterownik, dysk, Python z PyTorchem):
```powershell
ssh -o StrictHostKeyChecking=accept-new -p 10691 root@179.255.148.147 "nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader; df -h /workspace | tail -1; ls /venv/main/bin/python"
```
`StrictHostKeyChecking=accept-new` = przy pierwszym połączeniu zapamiętaj odcisk hosta bez
pytania. Wynik: `NVIDIA GeForce RTX 4070, 12282 MiB, 580.178.04`, dysk 16 GB (nie 40 -
suwak Disk Space trzeba ustawić przed Rent), `/venv/main/bin/python` jest.
Same `ssh -p 10691 root@179.255.148.147` (bez polecenia w cudzysłowie) otwiera terminal na
maszynie; `exit` wraca.

**2. Katalog i wysłanie plików** (skrypt, potem paczka):
```powershell
ssh -p 10691 root@179.255.148.147 "mkdir -p /workspace/paczki"
scp -P 10691 docker\licz\licz_paczke.py docker\licz\vast_start.sh root@179.255.148.147:/workspace/
scp -P 10691 nsa\database\paczki\nsa-0002.jsonl.gz root@179.255.148.147:/workspace/paczki/
ssh -p 10691 root@179.255.148.147 "ls -la /workspace /workspace/paczki"
```
Paczka 176 MB szła 73 s (~2.4 MB/s wysyłania z domu).

**3. Instalacja i test szybkości - jednym poleceniem:**
```powershell
ssh -p 10691 root@179.255.148.147 "bash /workspace/vast_start.sh test"
```
Co robi `vast_start.sh` (nic nie trzeba instalować ręcznie):
- znajduje Pythona z PyTorchem (`/venv/main/bin/python` w obrazie Vast) i wypisuje
  `torch ... CUDA True` (karta widoczna);
- jeśli brak: `python -m pip install sentence-transformers==6.1.0 transformers==5.20.1 requests`
  (~1-2 min);
- `licz_paczke.py` pobiera model BAAI/bge-m3 z Hugging Face do `/workspace/hf` (~2.3 GB, raz)
  i liczy 5000 pierwszych fragmentów, bez zapisu wyniku.
Wynik na 4070: **~64 000 tokenów/s, ~211 000 znaków/s** -> paczka 500 tys. fragmentów
(~700 mln znaków) ~55 min, cały NSA ~12 h, ~$2.5.

**4. Liczenie (w tle, można się rozłączyć):**
```powershell
ssh -p 10691 root@179.255.148.147 "bash /workspace/vast_start.sh"
```
Odpowiada `Liczy w tle; postęp: tail -f /workspace/licz.log` i od razu wraca.

**5. Ile jeszcze:**
```powershell
ssh -p 10691 root@179.255.148.147 "tail -n 3 /workspace/licz.log"
```
Linie postępu: `49,152 / 500,173  ~65,781 tokenów/s` = policzone / wszystkie fragmenty.
Zostało ≈ (wszystkie - policzone) / wszystkie × czas całej paczki (znaki paczki / znaki na s).
Podgląd na żywo: `ssh -p 10691 root@179.255.148.147 "tail -f /workspace/licz.log"` (Ctrl+C
kończy tylko podgląd, liczenie trwa). Koniec: w logu `WSZYSTKO GOTOWE`.
Czekanie na koniec w pętli (Git Bash; sprawdza co 2 min):
```bash
until ssh -o BatchMode=yes -p 10691 root@179.255.148.147 "grep -q 'WSZYSTKO GOTOWE' /workspace/licz.log"; do sleep 120; done
```

**6. Pobranie wektorów i import** (po `WSZYSTKO GOTOWE`):
```powershell
scp -P 10691 "root@179.255.148.147:/workspace/paczki/nsa-0002.wektory.npy" nsa\database\paczki\
docker compose run --rm --no-deps www python -m nsa paczka-import nsa-0002
```
Plik ~1 GB (float16, 2 KB na fragment). Import sprawdza wektory kontrolne i zapisuje do
Qdranta oraz `judgment_vectors`.

**7. Miejsce na następne paczki** (dysk 16 GB; po udanym imporcie):
```powershell
ssh -p 10691 root@179.255.148.147 "rm /workspace/paczki/nsa-0002.*; df -h /workspace | tail -1"
```
Potem kolejne paczki: krok 2 (tylko `scp` paczek) i krok 4. Model i biblioteki zostają na
maszynie, więc start kolejnej paczki trwa sekundy.

**8. Koniec:** Instances -> **Destroy** (nie Stop).

## Uwagi
- Kilka instancji naraz = szybciej w czasie, koszt ten sam: każdej daj inne paczki.
- Paczka, której nie dokończysz: `docker compose run --rm --no-deps www python -m nsa paczki --zwolnij nsa-0003`
  - wraca do kolejki zwykłego `wektory`.
- Maszyna padła w trakcie: nowa instancja, wgraj te same paczki, `vast_start.sh` liczy od nowa
  (części zostały na starej maszynie).
