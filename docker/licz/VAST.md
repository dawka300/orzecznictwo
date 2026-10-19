# Liczenie paczek wektorów na Vast.ai (wynajęte GPU)

Vast.ai wynajmuje karty graficzne na godziny: dostajesz kontener Linuksa z GPU,
łączysz się przez SSH, wgrywasz paczki, liczysz, pobierasz wektory i **usuwasz
instancję**. Liczy ten sam skrypt co u kolegów (`licz_paczke.py`), ten sam model
bge-m3, więc wektory są zgodne z Twoimi (import sprawdza to wektorami kontrolnymi).

Na maszynę idą tylko pliki paczek (`*.jsonl.gz`: fragmenty publicznych orzeczeń).
Nigdy nie wgrywaj `.env`, kopii bazy ani kluczy - host to obca osoba.

Polecenia poniżej: PowerShell w katalogu repozytorium (`ssh` i `scp` są w Windows).

## 1. Raz na początku
1. Konto na https://cloud.vast.ai, doładuj kredyt (na próbę wystarczy $5-10).
2. Klucz API: Account -> Keys. Potem:
   ```powershell
   .venv\Scripts\python.exe -m pip install vastai
   .venv\Scripts\vastai.exe set api-key TWOJ_KLUCZ
   ```
3. Klucz SSH (jeśli nie masz `~\.ssh\id_ed25519.pub`, najpierw `ssh-keygen -t ed25519`):
   ```powershell
   .venv\Scripts\vastai.exe create ssh-key $HOME\.ssh\id_ed25519.pub
   ```

## 2. Paczki u Ciebie
```powershell
.venv\Scripts\python.exe -m nsa paczki --ile 4 --fragmentow 500000
.venv\Scripts\python.exe -m nsa paczki --lista
```
Paczka 500 tys. fragmentów = ~190 MB do wysłania i ~1 GB wektorów z powrotem
(float16: 2 KB na fragment). Większe paczki = mniej przesyłania plików.

## 3. Wybór maszyny
```powershell
.venv\Scripts\vastai.exe search offers 'gpu_name=RTX_4090 num_gpus=1 verified=true rentable=true reliability>0.98 cuda_vers>=12.8 direct_port_count>=1 disk_space>=40 inet_down>300' -o 'dph'
```
- `dph` = $/godz. (sortowanie: najtańsze najpierw). RTX 4090 zwykle ~$0.3-0.6/h; można też
  `gpu_name=RTX_5090` albo `RTX_3090` (wolniejsza, tańsza).
- W kolumnach sprawdź **opłaty za transfer** (`inet_up_cost`, `inet_down_cost` w $/GB): przy
  dziesiątkach GB wektorów drogi transfer zjada oszczędność; wybieraj ~0.
- `cuda_vers>=12.8`: sterownik hosta musi obsłużyć obraz z CUDA 12.8.

## 4. Start instancji
```powershell
.venv\Scripts\vastai.exe create instance ID_OFERTY --image pytorch/pytorch:2.11.0-cuda12.8-cudnn9-runtime --disk 40 --ssh --direct
.venv\Scripts\vastai.exe show instances          # czekaj na status "running" (pobiera obraz, kilka minut)
.venv\Scripts\vastai.exe ssh-url ID_INSTANCJI    # np. ssh://root@1.2.3.4:41234 -> HOST i PORT
```
Opłata za dysk biegnie od utworzenia, za GPU od statusu `running`. Status `exited`,
`offline`, `unknown` = nie ruszy: usuń (krok 8) i weź inną ofertę.

## 5. Wgranie skryptu i paczek
```powershell
ssh -p PORT root@HOST "mkdir -p /workspace/paczki"
scp -P PORT docker\licz\licz_paczke.py docker\licz\vast_start.sh root@HOST:/workspace/
scp -P PORT nsa\database\paczki\nsa-0002.jsonl.gz nsa\database\paczki\nsa-0003.jsonl.gz root@HOST:/workspace/paczki/
```
(Pliki `*.meta.jsonl.gz` zostają u Ciebie - nie są potrzebne do liczenia.)

## 6. Test szybkości, potem całość
```powershell
ssh -p PORT root@HOST "bash /workspace/vast_start.sh test"
```
Instaluje sentence-transformers (~1 min), pobiera model (~2.3 GB) i liczy 5000 fragmentów;
podaje **tokenów/s**. Koszt całości: tokeny ≈ znaki / 3.3 (znaki pokazuje `paczki --lista`),
godziny = tokeny / (tokenów/s) / 3600, razy $/h. Jeśli się opłaca:
```powershell
ssh -p PORT root@HOST "bash /workspace/vast_start.sh"
ssh -p PORT root@HOST "tail -n 5 /workspace/licz.log"    # postęp, kiedy chcesz
```
Liczy w tle wszystkie paczki po kolei; rozłączenie SSH ani zamknięcie Twojego komputera
nie przerywa. Koniec = w logu `WSZYSTKO GOTOWE`. Opcja: `BATCH=128 bash /workspace/vast_start.sh`
(większa porcja naraz, jeśli karta ma zapas pamięci).

## 7. Pobranie wektorów i import
```powershell
scp -P PORT "root@HOST:/workspace/paczki/*.wektory.npy" nsa\database\paczki\
.venv\Scripts\python.exe -m nsa paczka-import nsa-0002
.venv\Scripts\python.exe -m nsa paczka-import nsa-0003
```
Import odrzuci paczkę, jeśli wektory kontrolne się nie zgadzają (inny model) albo plik jest
niepełny - wtedy policz ją jeszcze raz.

## 8. USUŃ INSTANCJĘ
```powershell
.venv\Scripts\vastai.exe destroy instance ID_INSTANCJI
.venv\Scripts\vastai.exe show instances      # ma być pusto
```
`stop` zatrzymuje tylko GPU - **dysk dalej kosztuje**. Usuń, gdy wektory są u Ciebie
i zaimportowane.

## Uwagi
- Kilka instancji naraz = szybciej w czasie, koszt ten sam: każdej daj inne paczki.
- Paczka, której nie dokończysz: `python -m nsa paczki --zwolnij nsa-0003` - wraca do kolejki
  zwykłego `wektory`.
- Maszyna padła w trakcie: nowa instancja, wgraj te same paczki, `vast_start.sh` liczy od nowa
  (części zostały na starej maszynie).
