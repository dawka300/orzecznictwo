#!/usr/bin/env bash
# Na wynajętej maszynie (Vast.ai, obraz pytorch/pytorch z CUDA): liczy wszystkie paczki
# z /workspace/paczki, w tle (rozłączenie SSH nie przerywa), na każdej karcie osobno.
#
#   bash /workspace/vast_start.sh          # instaluje i liczy wszystko w tle -> /workspace/licz.log
#   bash /workspace/vast_start.sh test     # test szybkości: 5000 fragmentów pierwszej paczki
#   CZEKAJ_S=1800 bash /workspace/vast_start.sh   # po ostatniej paczce czeka 30 min na nowe
#   KARTY=2 bash /workspace/vast_start.sh  # tylko 2 karty (domyślnie wszystkie z nvidia-smi)
#
# Każda karta bierze swoją paczkę (rezerwacja = katalog <nazwa>.zajeta), log karty N:
# /workspace/licz-gpuN.log. Paczka z gotowym <nazwa>.wektory.npy jest pomijana;
# przerwane liczenie (restart maszyny) wznawia się od brakującej części.
set -euo pipefail

KATALOG=${KATALOG:-/workspace}
SILNIK=${SILNIK:-cuda}
BATCH=${BATCH:-64}
export HF_HOME=${HF_HOME:-$KATALOG/hf}

# Python z PyTorchem: obraz vastai/pytorch trzyma go w /venv/main (ssh "polecenie"
# nie aktywuje środowiska), obraz pytorch/pytorch ma go na PATH.
if [ -z "${PY:-}" ]; then
    if [ -x /venv/main/bin/python ]; then PY=/venv/main/bin/python; else PY=$(command -v python3 || command -v python); fi
fi

if [ "${1:-}" = "test" ]; then
    "$PY" -c "import torch; print('torch', torch.__version__, 'CUDA', torch.cuda.is_available())"
    "$PY" -c "import sentence_transformers" 2>/dev/null \
        || "$PY" -m pip install --no-cache-dir sentence-transformers==6.1.0 transformers==5.20.1 requests
    pierwsza=$(ls "$KATALOG"/paczki/*.jsonl.gz | head -n 1)
    "$PY" "$KATALOG/licz_paczke.py" "$pierwsza" --silnik "$SILNIK" --batch "$BATCH" --limit 5000
    exit 0
fi

if [ "${1:-}" != "--w-tle" ] && [ "${1:-}" != "--karta" ]; then
    nohup bash "$0" --w-tle >> "$KATALOG/licz.log" 2>&1 &
    echo "Liczy w tle; postęp: tail -f $KATALOG/licz.log $KATALOG/licz-gpu*.log"
    exit 0
fi

# Pierwsza paczka bez wektorów i bez rezerwacji (po nazwie), zarezerwowana atomowo (mkdir);
# katalog przeglądany po każdej paczce, więc dosłane w trakcie (vast_kolejka.sh) też się policzą.
zarezerwuj() {
    local paczka
    for paczka in "$KATALOG"/paczki/*.jsonl.gz; do
        [ -e "$paczka" ] || continue
        [ -f "${paczka%.jsonl.gz}.wektory.npy" ] && continue
        mkdir "${paczka%.jsonl.gz}.zajeta" 2>/dev/null || continue
        echo "$paczka"
        return
    done
    return 0
}

if [ "$1" = "--karta" ]; then
    karta=$2
    export CUDA_VISIBLE_DEVICES=$karta
    echo "=== karta $karta: start $(date -u '+%Y-%m-%d %H:%M:%S') UTC"
    # CZEKAJ_S: tyle sekund czekać na nową paczkę, gdy nic nie zostało (0 = koniec od razu).
    bezczynnie=0
    while true; do
        paczka=$(zarezerwuj)
        if [ -z "$paczka" ]; then
            if [ "$bezczynnie" -ge "${CZEKAJ_S:-0}" ]; then
                break
            fi
            sleep 30
            bezczynnie=$((bezczynnie + 30))
            continue
        fi
        bezczynnie=0
        nazwa=$(basename "$paczka" .jsonl.gz)
        echo "--- $nazwa: liczę na karcie $karta"
        if "$PY" "$KATALOG/licz_paczke.py" "$paczka" --silnik "$SILNIK" --batch "$BATCH"; then
            rm -rf "$KATALOG/paczki/$nazwa.czesci"
            # Gotowe dla vast_kolejka.sh (szuka tej linii we wszystkich licz*.log).
            echo "Gotowe: $KATALOG/paczki/$nazwa.wektory.npy"
        else
            echo "--- $nazwa: BŁĄD liczenia na karcie $karta"
            sleep 60
        fi
        rmdir "$KATALOG/paczki/$nazwa.zajeta" 2>/dev/null || true
    done
    echo "=== karta $karta: koniec $(date -u '+%Y-%m-%d %H:%M:%S') UTC"
    exit 0
fi

echo "=== start $(date -u '+%Y-%m-%d %H:%M:%S') UTC"
nvidia-smi --query-gpu=index,name,memory.total,driver_version --format=csv,noheader || true
"$PY" -c "import torch; print('torch', torch.__version__, 'CUDA', torch.cuda.is_available())"
"$PY" -c "import sentence_transformers" 2>/dev/null \
    || "$PY" -m pip install --no-cache-dir sentence-transformers==6.1.0 transformers==5.20.1 requests
# Model raz, zanim karty ruszą razem (inaczej 4 procesy ściągają go naraz). Wczytanie
# zamiast snapshot_download: tylko potrzebne pliki (całe repo z ONNX = 6.4 GB na małym dysku).
"$PY" -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-m3', device='cpu')"

# Rezerwacje po poprzednim uruchomieniu (restart maszyny): nikt już nie liczy.
rm -rf "$KATALOG"/paczki/*.zajeta
KARTY=${KARTY:-$(nvidia-smi -L | wc -l)}
for karta in $(seq 0 $((KARTY - 1))); do
    bash "$0" --karta "$karta" >> "$KATALOG/licz-gpu$karta.log" 2>&1 &
    sleep 20  # model wczytywany po kolei, nie wszystkie naraz z dysku
done
wait
echo "=== WSZYSTKO GOTOWE $(date -u '+%Y-%m-%d %H:%M:%S') UTC"
