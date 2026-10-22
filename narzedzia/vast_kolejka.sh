#!/usr/bin/env bash
# Kolejka paczek wektorów na wynajętej maszynie (Vast.ai), uruchamiana u siebie (Git Bash).
#
# Na maszynie zawsze NAPRZOD paczek (domyślnie 2: liczona + jedna czekająca), więc karta nie
# stoi w czasie wysyłania. Gotowe wektory od razu: pobranie, import do bazy, usunięcie paczki
# z maszyny (dysk). Na maszynie liczy vast_start.sh z CZEKAJ_S (czeka na dosyłane paczki).
#
#   narzedzia/vast_kolejka.sh PORT HOST                      # wszystkie paczki bez wektorów
#   narzedzia/vast_kolejka.sh PORT HOST nsa-0003 nsa-0004    # tylko te
#   PROJEKT=etpc NAPRZOD=3 narzedzia/vast_kolejka.sh PORT HOST
#
# Wznawianie: przerwane (Ctrl+C, zamknięty terminal, restart) wystarczy uruchomić jeszcze raz.
# - paczka zaimportowana = plik <nazwa>.zaimportowano u Ciebie: pomijana;
# - wektory ściągnięte, import przerwany: tylko import, bez ponownego pobierania;
# - paczka już na maszynie: nie jest wysyłana drugi raz;
# - na maszynie licz_paczke.py wznawia od brakującej części (co 4096 fragmentów), a liczenie,
#   które przestało działać (restart maszyny), kolejka uruchamia znowu (co 2 min sprawdza).
# Log: logs/vast_kolejka.log, gdy uruchomione jak w docker/licz/VAST.md.
set -euo pipefail

PORT=${1:?podaj PORT i HOST z przycisku Connect (ssh -p PORT root@HOST)}
HOST=${2:?podaj HOST}
shift 2
PROJEKT=${PROJEKT:-nsa}
NAPRZOD=${NAPRZOD:-2}
cd "$(dirname "$0")/.."
LOKALNIE="$PROJEKT/database/paczki"
ZDALNIE=/workspace/paczki
SSH=(ssh -o BatchMode=yes -o ServerAliveInterval=30 -o ConnectTimeout=30 -p "$PORT" "root@$HOST")
SCP=(scp -o BatchMode=yes -o ServerAliveInterval=30 -o ConnectTimeout=30 -P "$PORT")

zdalnie() { "${SSH[@]}" "$@" 2>/dev/null; }
log() { echo "$(date '+%H:%M:%S') $*"; }

# Paczki do zrobienia: podane albo lokalne bez .wektory.npy, po nazwie; bez podanych
# lista jest odświeżana, gdy się skończy (eksport paczek może iść w tym czasie).
podane=$#
zrobione=" "
lokalne() {
    local plik nazwa
    for plik in "$LOKALNIE"/*.jsonl.gz; do
        [[ $plik == *.meta.jsonl.gz ]] && continue
        [ -e "$plik" ] || continue
        nazwa=$(basename "$plik" .jsonl.gz)
        [ -f "$LOKALNIE/$nazwa.zaimportowano" ] && continue
        [[ $zrobione == *" $nazwa "* ]] && continue
        echo "$nazwa"
    done
}
if [ "$podane" -gt 0 ]; then
    kolejka=("$@")
else
    mapfile -t kolejka < <(lokalne)
fi
[ ${#kolejka[@]} -gt 0 ] || { log "Nic do zrobienia."; exit 0; }
log "Do policzenia: ${kolejka[*]}"

# Skrypty na maszynie: pod inną nazwą, potem zmiana (działający vast_start.sh czyta swój
# plik w trakcie - nadpisanie w miejscu by go zepsuło).
zdalnie "mkdir -p $ZDALNIE"
for skrypt in licz_paczke.py vast_start.sh; do
    "${SCP[@]}" "docker/licz/$skrypt" "root@$HOST:/workspace/$skrypt.nowy" 2>/dev/null
    zdalnie "mv /workspace/$skrypt.nowy /workspace/$skrypt"
done
uruchom() {
    zdalnie "pgrep -f 'vast_start.sh --w-tle' >/dev/null || CZEKAJ_S=1800 bash /workspace/vast_start.sh" || true
}

wyslij() {
    local nazwa=$1
    if zdalnie "test -f $ZDALNIE/$nazwa.jsonl.gz"; then
        log "$nazwa: już jest na maszynie"
        return
    fi
    log "$nazwa: wysyłam"
    # Pod inną nazwą, potem zmiana: maszyna nie zacznie liczyć niepełnego pliku.
    "${SCP[@]}" "$LOKALNIE/$nazwa.jsonl.gz" "root@$HOST:$ZDALNIE/$nazwa.jsonl.gz.wysylane" 2>/dev/null
    zdalnie "mv $ZDALNIE/$nazwa.jsonl.gz.wysylane $ZDALNIE/$nazwa.jsonl.gz"
    log "$nazwa: wysłana"
}

importuj() {
    local nazwa=$1
    log "$nazwa: import"
    if docker compose run --rm --no-deps www python -m "$PROJEKT" paczka-import "$nazwa" \
        > "$LOKALNIE/$nazwa.import.log" 2>&1; then
        touch "$LOKALNIE/$nazwa.zaimportowano"
        grep -E "kontrola|zapisano" "$LOKALNIE/$nazwa.import.log" | tail -n 2
        return 0
    fi
    log "$nazwa: IMPORT NIEUDANY - zob. $LOKALNIE/$nazwa.import.log; paczka zostaje na maszynie"
    tail -n 5 "$LOKALNIE/$nazwa.import.log"
    return 1
}

odbierz() {
    local nazwa=$1
    # Wektory już ściągnięte, a import przerwany: tylko import (po ponownym uruchomieniu).
    if [ ! -f "$LOKALNIE/$nazwa.wektory.npy" ]; then
        log "$nazwa: policzona, pobieram wektory"
        "${SCP[@]}" "root@$HOST:$ZDALNIE/$nazwa.wektory.npy" "$LOKALNIE/$nazwa.wektory.npy.pobierane" 2>/dev/null
        mv "$LOKALNIE/$nazwa.wektory.npy.pobierane" "$LOKALNIE/$nazwa.wektory.npy"
    fi
    importuj "$nazwa" || return 1
    zdalnie "rm -f $ZDALNIE/$nazwa.jsonl.gz $ZDALNIE/$nazwa.wektory.npy"
    log "$nazwa: gotowe, usunięta z maszyny"
}

na_maszynie=()
while true; do
    if [ ${#kolejka[@]} -eq 0 ] && [ "$podane" -eq 0 ]; then
        mapfile -t kolejka < <(lokalne | grep -v -x -F -e "${na_maszynie[@]:-__}" || true)
    fi
    [ ${#kolejka[@]} -gt 0 ] || [ ${#na_maszynie[@]} -gt 0 ] || break
    while [ ${#na_maszynie[@]} -lt "$NAPRZOD" ] && [ ${#kolejka[@]} -gt 0 ]; do
        wyslij "${kolejka[0]}"
        na_maszynie+=("${kolejka[0]}")
        kolejka=("${kolejka[@]:1}")
    done
    uruchom
    pierwsza=${na_maszynie[0]}
    # Gotowa = licz_paczke.py wypisał "Gotowe: <plik>" (zapis pliku zakończony).
    if zdalnie "test -f $ZDALNIE/$pierwsza.wektory.npy && grep -q 'Gotowe: $ZDALNIE/$pierwsza.wektory.npy' /workspace/licz.log"; then
        if ! odbierz "$pierwsza"; then
            log "Zatrzymuję kolejkę: najpierw trzeba wyjaśnić nieudany import."
            exit 1
        fi
        zrobione+="$pierwsza "
        na_maszynie=("${na_maszynie[@]:1}")
    else
        postep=$(zdalnie "tail -c 300 /workspace/licz.log | tr '\r' '\n' | grep '/' | tail -n 1" || true)
        log "czekam: $pierwsza ${postep:+- $postep}"
        sleep 120
    fi
done
log "Wszystkie paczki policzone i zaimportowane. Pamiętaj: Instances -> Destroy."
