#!/usr/bin/env bash
# Kolejka paczek wektorów na wynajętej maszynie (Vast.ai), uruchamiana u siebie (Git Bash).
#
# Na maszynie zawsze NAPRZOD paczek (domyślnie liczba kart + 2: każda karta liczy swoją, dwie
# czekają), więc karty nie stoją w czasie wysyłania. Policzona paczka (dowolna, karty kończą
# w różnej kolejności): pobranie wektorów, usunięcie z maszyny (dysk), import do bazy w tle
# (po jednym naraz), a w tym czasie dalej wysyłanie i pobieranie. Na maszynie liczy
# vast_start.sh z CZEKAJ_S (czeka na dosyłane paczki).
#
#   narzedzia/vast_kolejka.sh PORT HOST                      # wszystkie paczki bez wektorów
#   narzedzia/vast_kolejka.sh PORT HOST nsa-0003 nsa-0004    # tylko te
#   PROJEKT=etpc NAPRZOD=3 narzedzia/vast_kolejka.sh PORT HOST
#   NAPRZOD=8 WOLNE_GB=4 narzedzia/vast_kolejka.sh PORT HOST  # 4 karty + 4 czekające
#
# Wznawianie: przerwane (Ctrl+C, zamknięty terminal, restart) wystarczy uruchomić jeszcze raz.
# - paczka zaimportowana = plik <nazwa>.zaimportowano u Ciebie: pomijana;
# - wektory ściągnięte, import przerwany: tylko import, bez ponownego pobierania;
# - paczka już na maszynie: nie jest wysyłana drugi raz;
# - na maszynie licz_paczke.py wznawia od brakującej części (co 4096 fragmentów), a liczenie,
#   które przestało działać (restart maszyny), kolejka uruchamia znowu.
# Import czeka, gdy blokadę paczek trzyma eksport (`paczki`), i próbuje znowu.
# Log: logs/vast_kolejka.log, gdy uruchomione jak w docker/licz/VAST.md.
set -euo pipefail

PORT=${1:?podaj PORT i HOST z przycisku Connect (ssh -p PORT root@HOST)}
HOST=${2:?podaj HOST}
shift 2
PROJEKT=${PROJEKT:-nsa}
cd "$(dirname "$0")/.."
LOKALNIE="$PROJEKT/database/paczki"
ZDALNIE=/workspace/paczki
SSH=(ssh -o BatchMode=yes -o ServerAliveInterval=30 -o ConnectTimeout=30 -p "$PORT" "root@$HOST")
SCP=(scp -o BatchMode=yes -o ServerAliveInterval=30 -o ConnectTimeout=30 -P "$PORT")

zdalnie() { "${SSH[@]}" "$@" 2>/dev/null; }
log() { echo "$(date '+%H:%M:%S') $*"; }
# Polecenie, które musi się udać (scp, mv/rm na maszynie): zerwane połączenie = do 5 prób
# co 30 s, zanim kolejka się zatrzyma.
ponow() {
    local proba
    for proba in 1 2 3 4 5; do
        "$@" && return 0
        log "nie wyszło (próba $proba z 5): ${*: -1}"
        sleep 30
    done
    return 1
}

if [ -z "${NAPRZOD:-}" ]; then
    karty=$(zdalnie "nvidia-smi -L | wc -l" || echo 1)
    NAPRZOD=$((karty + 2))
fi
WOLNE_GB=${WOLNE_GB:-4}
log "Na maszynie naraz: $NAPRZOD paczek (wysyłanie tylko przy >= $WOLNE_GB GB wolnego)"

# Stan: kolejka (do wysłania), na_maszynie (wysłane, liczone), do_importu (wektory u nas),
# importowana (import w tle), zrobione (zaimportowane w tym uruchomieniu).
kolejka=()
na_maszynie=()
do_importu=()
importowana=""
import_pid=""
import_po=0  # po zajętej blokadzie: następna próba importu nie wcześniej (sekundy epoki)
zrobione=" "

w_toku() { echo " ${na_maszynie[*]:-} ${do_importu[*]:-} $importowana $zrobione "; }

# Paczki do zrobienia: podane albo lokalne bez .zaimportowano, po nazwie; bez podanych
# lista jest odświeżana, gdy się skończy (eksport paczek może iść w tym czasie).
lokalne() {
    local plik nazwa zajete
    zajete=$(w_toku)
    for plik in "$LOKALNIE"/*.jsonl.gz; do
        [[ $plik == *.meta.jsonl.gz ]] && continue
        [ -e "$plik" ] || continue
        nazwa=$(basename "$plik" .jsonl.gz)
        [ -f "$LOKALNIE/$nazwa.zaimportowano" ] && continue
        [[ $zajete == *" $nazwa "* ]] && continue
        echo "$nazwa"
    done
}
# Wektory już ściągnięte (import przerwany): od razu do importu, bez wysyłania.
rozdziel() {
    local nazwa
    for nazwa in "$@"; do
        if [ -f "$LOKALNIE/$nazwa.zaimportowano" ]; then
            continue  # podana jawnie, ale już w bazie
        elif [ -f "$LOKALNIE/$nazwa.wektory.npy" ]; then
            do_importu+=("$nazwa")
        else
            kolejka+=("$nazwa")
        fi
    done
}
podane=$#
if [ "$podane" -gt 0 ]; then
    rozdziel "$@"
else
    mapfile -t nowe < <(lokalne)
    rozdziel "${nowe[@]}"
fi
[ $((${#kolejka[@]} + ${#do_importu[@]})) -gt 0 ] || { log "Nic do zrobienia."; exit 0; }
log "Do policzenia: ${kolejka[*]:-}${do_importu[*]:+ (tylko import: ${do_importu[*]})}"

# Skrypty na maszynie: pod inną nazwą, potem zmiana (działający vast_start.sh czyta swój
# plik w trakcie - nadpisanie w miejscu by go zepsuło).
zdalnie "mkdir -p $ZDALNIE"
for skrypt in licz_paczke.py vast_start.sh; do
    "${SCP[@]}" "docker/licz/$skrypt" "root@$HOST:/workspace/$skrypt.nowy" 2>/dev/null
    zdalnie "mv /workspace/$skrypt.nowy /workspace/$skrypt"
done
# Paczki zostawione na maszynie przez poprzednie uruchomienie: liczą się do NAPRZOD.
na_zdalnej=" $(zdalnie "ls $ZDALNIE" | sed -n 's/\.jsonl\.gz$//p' | tr '\n' ' ' || true) "
reszta=()
for nazwa in "${kolejka[@]}"; do
    if [[ $na_zdalnej == *" $nazwa "* ]]; then
        na_maszynie+=("$nazwa")
    else
        reszta+=("$nazwa")
    fi
done
kolejka=("${reszta[@]}")

uruchom() {
    zdalnie "pgrep -f '[v]ast_start.sh --' >/dev/null || CZEKAJ_S=1800 bash /workspace/vast_start.sh" || true
}

wyslij() {
    local nazwa=$1
    if zdalnie "test -f $ZDALNIE/$nazwa.jsonl.gz"; then
        log "$nazwa: już jest na maszynie"
        return
    fi
    log "$nazwa: wysyłam"
    # Pod inną nazwą, potem zmiana: maszyna nie zacznie liczyć niepełnego pliku.
    ponow "${SCP[@]}" -q "$LOKALNIE/$nazwa.jsonl.gz" "root@$HOST:$ZDALNIE/$nazwa.jsonl.gz.wysylane"
    ponow zdalnie "mv $ZDALNIE/$nazwa.jsonl.gz.wysylane $ZDALNIE/$nazwa.jsonl.gz"
    log "$nazwa: wysłana"
}

# Import w tle; kod wyjścia: 0 = zaimportowana, 2 = blokada zajęta (eksport), 1 = błąd.
importuj() {
    local nazwa=$1
    # Kod wyjścia nie wystarcza: zajęta blokada (inny import, eksport) też kończy się kodem 0.
    docker compose run --rm --no-deps www python -m "$PROJEKT" paczka-import "$nazwa" \
        > "$LOKALNIE/$nazwa.import.log" 2>&1 || true
    if grep -q "zapisano wektory" "$LOKALNIE/$nazwa.import.log"; then
        touch "$LOKALNIE/$nazwa.zaimportowano"
        return 0
    fi
    grep -q "jeszcze trwa" "$LOKALNIE/$nazwa.import.log" && return 2
    return 1
}

# Pobieranie wznawialne: po zerwanym połączeniu (niektóre hosty zrywają po kilku minutach)
# dociąga tylko brakujące bajty (tail -c +N), aż rozmiar zgodzi się z plikiem na maszynie;
# także po ponownym uruchomieniu kolejki (zostaje <nazwa>.wektory.npy.pobierane).
pobierz() {
    local nazwa=$1 zdalny="$ZDALNIE/$1.wektory.npy" cel="$LOKALNIE/$1.wektory.npy.pobierane"
    local rozmiar="" mam proba
    log "$nazwa: policzona, pobieram wektory"
    for proba in 1 2 3 4 5; do
        rozmiar=$(zdalnie "stat -c %s $zdalny" || true)
        [ -n "$rozmiar" ] && break
        sleep 30
    done
    [ -n "$rozmiar" ] || { log "$nazwa: brak odpowiedzi maszyny (rozmiar pliku)"; return 1; }
    mam=$(stat -c %s "$cel" 2>/dev/null || echo 0)
    [ "$mam" -gt "$rozmiar" ] && { rm -f "$cel"; mam=0; }
    for proba in $(seq 1 30); do
        [ "$mam" -ge "$rozmiar" ] && break
        "${SSH[@]}" "tail -c +$((mam + 1)) $zdalny" >> "$cel" 2>/dev/null || true
        mam=$(stat -c %s "$cel" 2>/dev/null || echo 0)
        if [ "$mam" -lt "$rozmiar" ]; then
            log "$nazwa: połączenie przerwane przy $((mam / 1048576)) z $((rozmiar / 1048576)) MB, wznawiam"
            sleep 10
        fi
    done
    if [ "$mam" -ne "$rozmiar" ]; then
        log "$nazwa: nie pobrano ($mam z $rozmiar B) - spróbuję w następnym obiegu"
        return 1
    fi
    mv "$cel" "$LOKALNIE/$nazwa.wektory.npy"
    ponow zdalnie "rm -f $ZDALNIE/$nazwa.jsonl.gz $zdalny"
    log "$nazwa: pobrana, usunięta z maszyny"
}

while true; do
    if [ ${#kolejka[@]} -eq 0 ] && [ "$podane" -eq 0 ]; then
        mapfile -t kolejka < <(lokalne)
    fi
    if [ ${#kolejka[@]} -eq 0 ] && [ ${#na_maszynie[@]} -eq 0 ] \
        && [ ${#do_importu[@]} -eq 0 ] && [ -z "$importowana" ]; then
        break
    fi
    zmiana=0

    while [ ${#na_maszynie[@]} -lt "$NAPRZOD" ] && [ ${#kolejka[@]} -gt 0 ]; do
        # Bezpiecznik dysku maszyny: liczona paczka rośnie do ~2 GB (części+wynik).
        wolne=$(zdalnie "df --output=avail -BG $ZDALNIE | tail -n 1 | tr -dc 0-9" || echo 0)
        if [ "${wolne:-0}" -lt "$WOLNE_GB" ]; then
            log "na maszynie wolne ${wolne:-?} GB (< $WOLNE_GB), wstrzymuję wysyłanie"
            break
        fi
        wyslij "${kolejka[0]}"
        na_maszynie+=("${kolejka[0]}")
        kolejka=("${kolejka[@]:1}")
        zmiana=1
        uruchom  # karty liczą już pierwszą, gdy reszta się wysyła
    done
    if [ ${#na_maszynie[@]} -gt 0 ]; then
        uruchom
        # Gotowe = linia "Gotowe: <plik>" w logu którejś karty (zapis pliku zakończony).
        gotowe=" $(zdalnie "cat /workspace/licz*.log 2>/dev/null | tr '\r' '\n' | sed -n 's|^Gotowe: $ZDALNIE/\([^ ]*\)\.wektory\.npy.*|\1|p' | sort -u | tr '\n' ' '" || true) "
        zostaja=()
        for nazwa in "${na_maszynie[@]}"; do
            if [[ $gotowe == *" $nazwa "* ]] && zdalnie "test -f $ZDALNIE/$nazwa.wektory.npy" \
                && pobierz "$nazwa"; then
                do_importu+=("$nazwa")
                zmiana=1
            else
                zostaja+=("$nazwa")
            fi
        done
        na_maszynie=("${zostaja[@]}")
    fi

    # Koniec importu w tle?
    if [ -n "$importowana" ] && ! kill -0 "$import_pid" 2>/dev/null; then
        kod=0
        wait "$import_pid" || kod=$?
        if [ "$kod" -eq 0 ]; then
            grep -E "kontrola|zapisano" "$LOKALNIE/$importowana.import.log" | tail -n 2
            log "$importowana: zaimportowana"
            zrobione+="$importowana "
        elif [ "$kod" -eq 2 ]; then
            log "$importowana: blokada paczek zajęta (eksport?), spróbuję za 5 min"
            do_importu=("$importowana" "${do_importu[@]}")
            import_po=$(($(date +%s) + 300))
        else
            log "$importowana: IMPORT NIEUDANY - zob. $LOKALNIE/$importowana.import.log"
            tail -n 5 "$LOKALNIE/$importowana.import.log"
            log "Zatrzymuję kolejkę: najpierw trzeba wyjaśnić nieudany import (liczenie na maszynie trwa)."
            exit 1
        fi
        importowana=""
        import_pid=""
        zmiana=1
    fi
    if [ -z "$importowana" ] && [ ${#do_importu[@]} -gt 0 ] && [ "$(date +%s)" -ge "$import_po" ]; then
        importowana=${do_importu[0]}
        do_importu=("${do_importu[@]:1}")
        log "$importowana: import (w tle)"
        importuj "$importowana" &
        import_pid=$!
        zmiana=1
    fi

    if [ "$zmiana" -eq 0 ]; then
        postep=$(zdalnie 'for f in /workspace/licz-gpu*.log; do tail -c 300 "$f" | tr "\r" "\n" | grep " / " | tail -n 1; done' | tr -s ' ' | paste -sd'|' || true)
        log "liczone: ${na_maszynie[*]:-} | import: ${importowana:--} | czeka na import: ${do_importu[*]:--} | ${postep}"
        sleep 60
    fi
done
log "Wszystkie paczki policzone i zaimportowane. Pamiętaj: Instances -> Destroy."
