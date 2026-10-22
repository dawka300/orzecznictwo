#!/usr/bin/env bash
# Na wynajętej maszynie (Vast.ai, obraz pytorch/pytorch z CUDA): liczy po kolei
# wszystkie paczki z /workspace/paczki, w tle (rozłączenie SSH nie przerywa).
#
#   bash /workspace/vast_start.sh          # instaluje i liczy wszystko w tle -> /workspace/licz.log
#   bash /workspace/vast_start.sh test     # test szybkości: 5000 fragmentów pierwszej paczki
#   CZEKAJ_S=1800 bash /workspace/vast_start.sh   # po ostatniej paczce czeka 30 min na nowe
#
# Paczka z gotowym <nazwa>.wektory.npy jest pomijana; przerwane liczenie
# (restart maszyny) wznawia się od brakującej części.
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
"$PY" -c "import torch; print('torch', torch.__version__, 'CUDA', torch.cuda.is_available())"

if ! "$PY" -c "import sentence_transformers" 2>/dev/null; then
    "$PY" -m pip install --no-cache-dir sentence-transformers==6.1.0 transformers==5.20.1 requests
fi

if [ "${1:-}" = "test" ]; then
    pierwsza=$(ls "$KATALOG"/paczki/*.jsonl.gz | head -n 1)
    "$PY" "$KATALOG/licz_paczke.py" "$pierwsza" --silnik "$SILNIK" --batch "$BATCH" --limit 5000
    exit 0
fi

if [ "${1:-}" != "--w-tle" ]; then
    nohup bash "$0" --w-tle >> "$KATALOG/licz.log" 2>&1 &
    echo "Liczy w tle; postęp: tail -f $KATALOG/licz.log"
    exit 0
fi

echo "=== start $(date -u '+%Y-%m-%d %H:%M:%S') UTC"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader || true

# Pierwsza paczka bez wektorów (po nazwie); katalog przeglądany po każdej paczce,
# więc paczki dosłane w trakcie (narzedzia/vast_kolejka.sh) też się policzą.
nastepna() {
    for paczka in "$KATALOG"/paczki/*.jsonl.gz; do
        [ -e "$paczka" ] || continue
        [ -f "${paczka%.jsonl.gz}.wektory.npy" ] && continue
        echo "$paczka"
        return
    done
}

# CZEKAJ_S: tyle sekund czekać na nową paczkę, gdy nic nie zostało (0 = koniec od razu).
bezczynnie=0
while true; do
    paczka=$(nastepna)
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
    echo "--- $nazwa: liczę"
    "$PY" "$KATALOG/licz_paczke.py" "$paczka" --silnik "$SILNIK" --batch "$BATCH"
    rm -rf "$KATALOG/paczki/$nazwa.czesci"
done
echo "=== WSZYSTKO GOTOWE $(date -u '+%Y-%m-%d %H:%M:%S') UTC"
