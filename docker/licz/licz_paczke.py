"""Liczy wektory jednej paczki fragmentów orzeczeń (bge-m3) - u kolegi w dockerze albo z LM Studio.

Wejście:  <nazwa>.jsonl.gz      (od właściciela bazy: python -m <projekt> paczki)
Wyjście:  <nazwa>.wektory.npy   (float16, wiersz na fragment; odesłać właścicielowi)

Silniki:
  cuda / cpu - sentence-transformers z modelem BAAI/bge-m3 (w dockerze: cuda),
  http       - serwer zgodny z OpenAI (LM Studio: http://127.0.0.1:1234/v1/embeddings).

Wznawia się: co CZESC fragmentów zapisuje część do <nazwa>.czesci/; po przerwaniu
(Ctrl+C, restart komputera) to samo polecenie liczy dalej od brakującej części.
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
    if not args.limit:
        czesci.mkdir(exist_ok=True)
    start = time.monotonic()
    znaki = 0
    for i in range(0, len(teksty), CZESC):
        plik = czesci / f"{i // CZESC:05d}.npy"
        if not args.limit and plik.is_file():
            continue  # policzone w poprzednim uruchomieniu
        kawalek = teksty[i : i + CZESC]
        wektory = np.asarray(silnik.licz(kawalek), dtype=np.float16)
        if not args.limit:
            np.save(plik, wektory)
        znaki += sum(map(len, kawalek))
        s = time.monotonic() - start
        print(
            f"  {min(i + CZESC, len(teksty)):>9,} / {len(teksty):,}"
            f"  ~{znaki / ZNAKOW_NA_TOKEN / s:,.0f} tokenów/s, {znaki / s:,.0f} znaków/s",
            flush=True,
        )
    if args.limit:
        print("Test zakończony (bez pliku wynikowego).")
        return 0
    wynik = np.concatenate(
        [np.load(czesci / f"{i // CZESC:05d}.npy") for i in range(0, len(teksty), CZESC)]
    )
    cel = paczka.parent / f"{nazwa}.wektory.npy"
    # Najpierw plik tymczasowy, potem zmiana nazwy: kto czeka na .wektory.npy
    # (vast_kolejka.sh), nie ściągnie pliku w połowie zapisu.
    tymczasowy = paczka.parent / f"{nazwa}.wektory.npy.zapis"
    with open(tymczasowy, "wb") as f:
        np.save(f, wynik)
    os.replace(tymczasowy, cel)
    print(f"Gotowe: {cel} ({wynik.shape[0]:,} x {wynik.shape[1]}). Odeślij ten plik.")
    print(f"Części w {czesci} można usunąć.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
