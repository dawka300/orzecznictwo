"""Liczy wektory jednej paczki fragmentów orzeczeń (bge-m3) - u kolegi w dockerze albo z LM Studio.

Wejście:  <nazwa>.jsonl.gz      (od właściciela bazy: python -m <projekt> paczki)
Wyjście:  <nazwa>.wektory.npy   (float16, wiersz na fragment; odesłać właścicielowi)

Silniki:
  cuda / cpu - sentence-transformers z modelem BAAI/bge-m3 (w dockerze: cuda),
  http       - serwer zgodny z OpenAI (LM Studio: http://127.0.0.1:1234/v1/embeddings).

Wznawia się: co CZESC fragmentów zapisuje część do <nazwa>.czesci/; po przerwaniu
(Ctrl+C, restart komputera) to samo polecenie liczy dalej od brakującej części.
Kilka procesów na tej samej paczce (np. dwie karty: CUDA_VISIBLE_DEVICES=0 i =1) dzieli się
częściami: każda część jest rezerwowana plikiem <numer>.zajeta, skleja jeden z nich.
--limit N = tylko pierwsze N fragmentów (test szybkości), bez pliku wynikowego.

Samodzielny plik (Python 3.9+, numpy; sentence-transformers albo requests), bez repozytorium.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

CZESC = 4096  # fragmentów w jednym pliku części
ZNAKOW_NA_TOKEN = 3.3  # bge-m3, polski tekst (do podglądu tempa)
PORZUCONA_S = 3600  # blokada części bez działającego procesu (tylko Windows: po wieku)


def wczytaj(sciezka):
    with gzip.open(sciezka, "rt", encoding="utf-8") as f:
        naglowek = json.loads(next(f))
        teksty = [json.loads(wiersz)["t"] for wiersz in f]
    if len(teksty) != naglowek["count"]:
        sys.exit(f"Paczka uszkodzona: {len(teksty)} fragmentów zamiast {naglowek['count']}")
    return naglowek, teksty


class SentenceTransformers:
    def __init__(self, model, urzadzenie, batch):
        import torch
        from sentence_transformers import SentenceTransformer

        if urzadzenie == "cuda" and not torch.cuda.is_available():
            sys.exit("Brak CUDA: sprawdź sterownik NVIDIA i --gpus all (albo użyj --silnik cpu)")
        self.model = SentenceTransformer(model, device=urzadzenie)
        if urzadzenie == "cuda":
            self.model.half()  # fp16: szybciej, wynik zgodny (cosinus ~0.9999)
            print(f"Karta: {torch.cuda.get_device_name(0)}", flush=True)
        self.batch = batch

    def licz(self, teksty):
        # Tokenizacja w wątkach z wyprzedzeniem sprawdzona 2026-10-28 (5070, 5080, 5090):
        # zysk w szumie (-2..+4%), karty ogranicza moc, nie CPU - zostaje model.encode.
        return self.model.encode(
            teksty,
            batch_size=self.batch,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )


class Http:
    def __init__(self, url, model, batch):
        import requests

        self.session = requests.Session()
        self.url, self.nazwa, self.batch = url, model, batch

    def licz(self, teksty):
        wynik = []
        for i in range(0, len(teksty), self.batch):
            odp = self.session.post(
                self.url,
                json={"model": self.nazwa, "input": teksty[i : i + self.batch]},
                timeout=600,
            )
            odp.raise_for_status()
            dane = sorted(odp.json()["data"], key=lambda d: d["index"])
            wynik += [d["embedding"] for d in dane]
        v = np.asarray(wynik, dtype=np.float32)
        return v / np.linalg.norm(v, axis=1, keepdims=True)


def main(argv=None):
    p = argparse.ArgumentParser(description="Wektory jednej paczki fragmentów (bge-m3).")
    p.add_argument("paczka", help="plik <nazwa>.jsonl.gz")
    p.add_argument("--silnik", choices=("cuda", "cpu", "http"), default="cuda")
    p.add_argument("--model", default="", help="domyślnie BAAI/bge-m3 (http: model z paczki)")
    p.add_argument("--url", default="http://127.0.0.1:1234/v1/embeddings", help="dla --silnik http")
    p.add_argument("--batch", type=int, default=32)
    p.add_argument("--limit", type=int, help="tylko N pierwszych fragmentów (test szybkości)")
    args = p.parse_args(argv)

    paczka = Path(args.paczka)
    nazwa = paczka.name.replace(".jsonl.gz", "")
    naglowek, teksty = wczytaj(paczka)
    if args.limit:
        teksty = teksty[: args.limit]
    print(f"Paczka {nazwa}: {len(teksty)} fragmentów, {sum(map(len, teksty)):,} znaków", flush=True)

    if args.silnik == "http":
        silnik = Http(args.url, args.model or naglowek["model"], args.batch)
    else:
        silnik = SentenceTransformers(args.model or "BAAI/bge-m3", args.silnik, args.batch)

    czesci = paczka.parent / f"{nazwa}.czesci"
    cel = paczka.parent / f"{nazwa}.wektory.npy"
    pliki = [czesci / f"{i // CZESC:05d}.npy" for i in range(0, len(teksty), CZESC)]
    if not args.limit:
        czesci.mkdir(exist_ok=True)
    start = poprzednia = time.monotonic()
    znaki = 0
    while True:
        for numer, plik in enumerate(pliki):
            blokada = plik.with_suffix(".zajeta")
            if not args.limit:
                if plik.is_file():
                    continue  # policzone w poprzednim uruchomieniu albo przez inny proces
                if not zajmij(blokada):
                    continue  # tę część liczy inny proces (druga karta na tej samej paczce)
            kawalek = teksty[numer * CZESC : (numer + 1) * CZESC]
            wektory = np.asarray(silnik.licz(kawalek), dtype=np.float16)
            if not args.limit:
                zapisz(plik, wektory)
                blokada.unlink(missing_ok=True)
            znaki_czesci = sum(map(len, kawalek))
            znaki += znaki_czesci
            teraz = time.monotonic()
            s, s_czesci = teraz - start, teraz - poprzednia
            poprzednia = teraz
            # Średnia od startu (z rozgrzewką) i tempo ostatniej części (bez niej, do porównań kart).
            print(
                f"  {min((numer + 1) * CZESC, len(teksty)):>9,} / {len(teksty):,}"
                f"  ~{znaki / ZNAKOW_NA_TOKEN / s:,.0f} tokenów/s, {znaki / s:,.0f} znaków/s"
                f" (ostatnia część ~{znaki_czesci / ZNAKOW_NA_TOKEN / s_czesci:,.0f} tokenów/s)",
                flush=True,
            )
        if args.limit:
            print("Test zakończony (bez pliku wynikowego).")
            return 0
        if cel.is_file() or not czesci.is_dir() or all(plik.is_file() for plik in pliki):
            break
        if not paczka.is_file():
            sys.exit(f"Paczka {paczka} zniknęła w trakcie liczenia")
        time.sleep(5)  # inny proces kończy swoje części (albo jego porzucone przejmiemy)

    # Skleja jeden proces (blokada); inne czekają na gotowy plik.
    sklejanie = paczka.parent / f"{nazwa}.sklejanie"
    while not cel.is_file():
        if not czesci.is_dir():
            print(f"Paczkę {nazwa} skończył i odebrał inny proces.")
            return 0
        if zajmij(sklejanie):
            try:
                if not cel.is_file():
                    wynik = np.concatenate([np.load(plik) for plik in pliki])
                    # Plik tymczasowy, potem zmiana nazwy: kto czeka na .wektory.npy
                    # (vast_kolejka.sh), nie ściągnie pliku w połowie zapisu.
                    zapisz(cel, wynik)
            finally:
                sklejanie.unlink(missing_ok=True)
        elif not paczka.is_file():
            sys.exit(f"Paczka {paczka} zniknęła przed sklejeniem")
        else:
            time.sleep(5)
    print(f"Gotowe: {cel} ({len(teksty):,} fragmentów). Odeślij ten plik.")
    print(f"Części w {czesci} można usunąć.")
    return 0


def zapisz(plik, tablica):
    """np.save przez plik tymczasowy: inny proces nigdy nie czyta połowy zapisu."""
    tymczasowy = plik.with_name(plik.name + ".zapis")
    with open(tymczasowy, "wb") as f:
        np.save(f, tablica)
    os.replace(tymczasowy, plik)


def zajmij(blokada):
    """Atomowa rezerwacja (plik z PID). Blokadę procesu, który już nie działa, przejmuje."""
    for _ in range(2):
        try:
            fd = os.open(blokada, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileNotFoundError:
            return False  # katalog części usunięty: paczka skończona gdzie indziej
        except FileExistsError:
            if not porzucona(blokada):
                return False
            blokada.unlink(missing_ok=True)
            continue
        with os.fdopen(fd, "w") as f:
            f.write(str(os.getpid()))
        return True
    return False


def porzucona(blokada):
    try:
        pid = blokada.read_text().strip()
        wiek = time.time() - blokada.stat().st_mtime
    except FileNotFoundError:
        return True
    # Windows: os.kill(pid, 0) zamyka proces, więc tam tylko po wieku; pusty PID = świeżo zajęta.
    if os.name == "nt" or not pid.isdigit():
        return wiek > PORZUCONA_S
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


if __name__ == "__main__":
    sys.exit(main())
