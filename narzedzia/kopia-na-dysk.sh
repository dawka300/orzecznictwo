#!/usr/bin/env bash
# Kopia projektu na inny dysk (Windows, Git Bash; Docker Desktop działa):
#   narzedzia/kopia-na-dysk.sh "/g/projekty AI/orzecznictwo-szkielet" [nsa] [etpc]
#
# <kopia>/            lustro katalogu projektu (z .git, .env, etpc/database,
#                     nsa/database) bez .venv i cache; robocopy kopiuje tylko
#                     zmienione pliki, usuwa z lustra pliki usunięte w projekcie
# <kopia>/_archiwa/<data>/
#                     repo.bundle (wszystkie gałęzie i historia w jednym pliku:
#                     git clone repo.bundle), galezie.txt, poza-gitem.tar.gz
#                     (pliki spoza gita bez dużych danych, .git/config)
# <kopia>/_bazy/qdrant/   snapshoty kolekcji Qdranta (dwa ostatnie na kolekcję);
#                     QDRANT_COLLECTIONS="a b" = tylko te, "-" = żadna; snapshot powstaje
#                     w QDRANT_SNAPSHOTS_DIR z .env (bez niego w Dockerze na C:, gdzie
#                     NSA ~140 GB się nie mieści) i jest tylko przenoszony na miejsce
# <kopia>/_bazy/<projekt>.sql.gz   zrzut bazy projektów podanych po katalogu
#                     (python -m <projekt> kopia w kontenerze, prosto na ten dysk)
# Zrzuty z python -m <projekt> kopia (etpc/database, nsa/database) i ręczne
# (kopie/: teczki, trening) przechodzą lustrem.
set -euo pipefail

target="${1:?podaj katalog kopii, np. \"/g/projekty AI/orzecznictwo-szkielet\"}"
shift
repo="$(cd "$(dirname "$0")/.." && pwd)"
stamp="$(date +%Y-%m-%d_%H%M)"
archive="$target/_archiwa/$stamp"
qdrant="${QDRANT_URL:-http://127.0.0.1:6333}"
mkdir -p "$archive" "$target/_bazy/qdrant"

echo "== lustro katalogu -> $target"
set +e
# MSYS_NO_PATHCONV: Git Bash nie zamienia /MIR, /XD ... na ścieżki
MSYS_NO_PATHCONV=1 robocopy "$(cygpath -w "$repo")" "$(cygpath -w "$target")" /MIR \
    /XD .venv __pycache__ .pytest_cache .ruff_cache _archiwa _bazy \
    /R:1 /W:1 /NFL /NDL /NP /NJH
code=$?
set -e
if [ "$code" -ge 8 ]; then
    echo "robocopy: błąd (kod $code)" >&2
    exit 1
fi

echo "== git: wszystkie gałęzie i historia"
git -C "$repo" bundle create "$archive/repo.bundle" --all
git -C "$repo" bundle verify "$archive/repo.bundle" > /dev/null
git -C "$repo" bundle list-heads "$archive/repo.bundle" > "$archive/galezie.txt"
cat "$archive/galezie.txt"

echo "== pliki spoza gita (bez dużych danych)"
(
    cd "$repo"
    { git ls-files --others; echo .git/config; } \
        | grep -Ev '(^|/)(__pycache__|\.venv|\.pytest_cache|\.ruff_cache)/' \
        | grep -Ev '^(etpc/database|nsa/database|logs)/' \
        > "$archive/poza-gitem.txt"
    tar -czf "$archive/poza-gitem.tar.gz" -T "$archive/poza-gitem.txt"
)
echo "$(wc -l < "$archive/poza-gitem.txt") plików"

echo "== Qdrant: snapshoty kolekcji"
# QDRANT_SNAPSHOTS_DIR (z .env, jak w docker-compose.yml): gdzie Qdrant zapisuje
# snapshoty na komputerze
snapshots="${QDRANT_SNAPSHOTS_DIR:-$(sed -n 's/^QDRANT_SNAPSHOTS_DIR=//p' "$repo/.env" 2>/dev/null | tail -n 1)}"
if [ -n "$snapshots" ]; then snapshots="$(cygpath -u "$snapshots")"; fi
collections="${QDRANT_COLLECTIONS:-$(curl -sf "$qdrant/collections" | grep -o '"name":"[^"]*"' | cut -d'"' -f4)}"
if [ "$collections" = "-" ]; then collections=""; fi
for collection in $collections; do
    name="$(curl -sf -X POST "$qdrant/collections/$collection/snapshots?wait=true" \
        | grep -o '"name":"[^"]*"' | cut -d'"' -f4)"
    if [ -n "$snapshots" ]; then
        # snapshot już na dysku komputera: przeniesienie zamiast pobierania przez HTTP
        mv "$snapshots/$collection/$name" "$target/_bazy/qdrant/"
        rm -f "$snapshots/$collection/$name.checksum"
    else
        curl -sf -o "$target/_bazy/qdrant/$name" "$qdrant/collections/$collection/snapshots/$name"
        curl -sf -X DELETE "$qdrant/collections/$collection/snapshots/$name" > /dev/null
    fi
    # na dysku kopii dwa ostatnie snapshoty kolekcji
    ls -t "$target/_bazy/qdrant/$collection-"*.snapshot | tail -n +3 | xargs -r rm --
    echo "$collection: $name ($(du -h "$target/_bazy/qdrant/$name" | cut -f1))"
done

for project in "$@"; do
    echo "== zrzut bazy $project -> $target/_bazy/$project.sql.gz"
    upper="$(echo "$project" | tr '[:lower:]' '[:upper:]')"
    MSYS_NO_PATHCONV=1 docker compose -f "$(cygpath -w "$repo/docker-compose.yml")" run --rm --no-deps \
        -v "$(cygpath -w "$target/_bazy"):/kopia" -e "${upper}_BACKUP_DIR=/kopia" \
        www python -m "$project" kopia
done

echo "== gotowe: $target"
