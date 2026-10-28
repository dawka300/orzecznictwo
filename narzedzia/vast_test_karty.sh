#!/usr/bin/env bash
# Krótki test karty na wynajętej maszynie (Vast.ai), uruchamiany u siebie (Git Bash): kilka minut
# liczenia wektorów bge-m3 na stałej próbce, do porównań kart (graphic-card-rating.md).
#
#   narzedzia/vast_test_karty.sh PORT HOST                 # domyślne próby
#   PROBY="64 128" narzedzia/vast_test_karty.sh PORT HOST           # wielkości batcha
#   LIMIT=12288 narzedzia/vast_test_karty.sh PORT HOST     # krócej (wolne karty)
#   LOG=logs/test_karty_5080.log narzedzia/vast_test_karty.sh PORT HOST   # kilka maszyn naraz
#
# Co robi: zapisuje kartę, limit mocy, sterownik, procesor i RAM hosta; mierzy wysyłanie
# próbki (łącze); instaluje sentence-transformers, jeśli brak; dla każdej próby
# (wielkość batcha) liczy LIMIT fragmentów i w połowie odczytuje pobór i limit mocy,
# obciążenie, zegar (obecny i maksymalny) i powód dławienia karty (SW Power Cap = limit mocy
# hosta; CPU procesu python nic nie mówi: CUDA czeka na kartę, zajmując rdzeń). Wynik: tempo ostatniej części (bez
# rozgrzewki). Log: logs/test_karty.log (dopisywany). Pierwsza próba pobiera model (~2.3 GB).
#
# Próbka: pierwsze LIMIT fragmentów paczki ZRODLO (typowa długość ~1400 znaków), zapisana raz
# w nsa/database/paczki/test-karty-<LIMIT>.jsonl.gz. Na końcu: Destroy instancji w panelu Vast.
set -euo pipefail

PORT=${1:?podaj PORT i HOST z przycisku Connect (ssh -p PORT root@HOST)}
HOST=${2:?podaj HOST}
LIMIT=${LIMIT:-24576}
PROBY=${PROBY:-"64 128"}
ZRODLO=${ZRODLO:-nsa/database/paczki/nsa-0041.jsonl.gz}
cd "$(dirname "$0")/.."
SSH=(ssh -o BatchMode=yes -o ServerAliveInterval=30 -o ConnectTimeout=30 -o StrictHostKeyChecking=accept-new -p "$PORT" "root@$HOST")
SCP=(scp -q -o BatchMode=yes -o ConnectTimeout=30 -P "$PORT")
PROBKA="nsa/database/paczki/test-karty-$LIMIT.jsonl.gz"
LOG=${LOG:-logs/test_karty.log}
mkdir -p logs

zdalnie() { "${SSH[@]}" "$@" 2>/dev/null; }
log() { echo "$(date '+%H:%M:%S') $*" | tee -a "$LOG"; }

if [ ! -f "$PROBKA" ]; then
    # Nagłówek z nową liczbą fragmentów, potem LIMIT wierszy (awk kończy wcześniej, więc bez pipefail).
    set +o pipefail
    zcat "$ZRODLO" | awk -v n="$LIMIT" \
        'NR == 1 { sub(/"count": [0-9]+/, "\"count\": " n) } { print } NR > n { exit }' \
        | gzip > "$PROBKA.zapis"
    set -o pipefail
    mv "$PROBKA.zapis" "$PROBKA"
fi

log "===== test $HOST:$PORT, $LIMIT fragmentów, próby: $PROBY"
zdalnie "nvidia-smi --query-gpu=name,memory.total,power.limit,power.default_limit,driver_version --format=csv,noheader" |
    while IFS= read -r karta; do log "Karta: $karta (nazwa, pamięć, limit mocy, fabrycznie, sterownik)"; done
log "Host: $(zdalnie 'echo "$(lscpu | sed -n "s/^Model name: *//p" | head -n 1), $(nproc) wątków, $(free -g | awk "/Mem/{print \$2}") GB RAM"')"

zdalnie "mkdir -p /workspace/test"
start=$(date +%s.%N)
"${SCP[@]}" "$PROBKA" docker/licz/licz_paczke.py "root@$HOST:/workspace/test/"
mb=$(du -m "$PROBKA" | cut -f1)
log "Wysłane $mb MB w $(awk -v a="$start" -v b="$(date +%s.%N)" 'BEGIN { printf "%.1f", b - a }') s"

PY='$( [ -x /venv/main/bin/python ] && echo /venv/main/bin/python || command -v python3 )'
zdalnie "P=$PY; \$P -c 'import sentence_transformers' 2>/dev/null || \$P -m pip install -q --no-cache-dir sentence-transformers==6.1.0 transformers==5.20.1 requests >/dev/null 2>&1; \$P -c 'import torch, sentence_transformers as s; print(\"torch\", torch.__version__, \"st\", s.__version__)'" |
    while IFS= read -r w; do log "$w"; done

for proba in $PROBY; do
    batch=$proba
    log "-- batch $batch"
    # W tle na maszynie; odczyt karty, gdy liczenie trwa (po pierwszej części), potem wynik.
    zdalnie "cd /workspace/test; export HF_HOME=/workspace/hf; P=$PY; rm -f proba.log
        \$P licz_paczke.py $(basename "$PROBKA") --batch $batch --limit $LIMIT > proba.log 2>&1 &
        pid=\$!
        until grep -q ' / ' proba.log 2>/dev/null || ! kill -0 \$pid 2>/dev/null; do sleep 2; done
        sleep 15
        nvidia-smi --query-gpu=power.draw,power.limit,utilization.gpu,clocks.sm,clocks.max.sm,temperature.gpu,memory.used --format=csv,noheader | sed 's/^/   karta: /'
        nvidia-smi -q -d PERFORMANCE | awk '/: Active/ { sub(/^ */, \"\"); print \"   dławienie: \" \$0; n++ } END { if (!n) print \"   dławienie: brak\" }'
        wait \$pid || { tail -n 20 proba.log; exit 1; }
        grep ' / ' proba.log | tail -n 1" |
        while IFS= read -r w; do log "$w"; done
done
log "Koniec. Pamiętaj: Destroy instancji."
