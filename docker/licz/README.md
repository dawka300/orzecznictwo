# Liczenie wektorów paczki (dla kolegów z kartą NVIDIA)

Dostajesz plik `<nazwa>.jsonl.gz` (fragmenty orzeczeń). Liczysz dla nich wektory
modelem bge-m3 na swojej karcie i odsyłasz plik `<nazwa>.wektory.npy`.

## Raz na początku
1. Docker Desktop (Windows: z WSL2) i aktualny sterownik NVIDIA (>= 570; RTX 50xx też działa).
   Sprawdzenie: `docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi`
   ma pokazać kartę.
2. W tym katalogu: `docker compose build` (pobiera ~3 GB: Python, torch z CUDA).

## Liczenie
1. Paczkę włóż do podkatalogu `paczki/`.
2. Test szybkości (~2000 fragmentów, kilka minut; za pierwszym razem pobiera model ~2.3 GB):
   `docker compose run --rm licz nsa-0001.jsonl.gz --limit 2000`
   Podaj właścicielowi wynik „tokenów/s”.
3. Cała paczka: `docker compose run --rm licz nsa-0001.jsonl.gz`
   Można przerwać (Ctrl+C, wyłączenie komputera) - to samo polecenie liczy dalej.
4. Odeślij `paczki/nsa-0001.wektory.npy` (np. przez dysk w chmurze). Katalog
   `paczki/nsa-0001.czesci/` potem można usunąć.

Opcje: `--batch 64` (więcej naraz, jeśli karta ma zapas), `--silnik cpu` (bez karty, bardzo wolno),
`--silnik http --url http://host.docker.internal:1234/v1/embeddings` (zamiast torch: LM Studio).

Bez dockera: `pip install sentence-transformers numpy` (torch z CUDA:
`pip install torch --index-url https://download.pytorch.org/whl/cu128`) i
`python licz_paczke.py paczki/nsa-0001.jsonl.gz`.
