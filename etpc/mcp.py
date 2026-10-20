"""ETPC rulings as tools for Claude: python -m etpc mcp (child of McpServer).

szukaj - hybrid search (words + meaning) with the page's filters;
fragmenty - the best passages for a question, packed into a limit (RAG);
orzeczenie - metadata and the text of one language version (in parts);
podobne - rulings closest in meaning to one ruling.
"""

from __future__ import annotations

from etpc import labels, search
from etpc.labels import label
from etpc.rag import HUDOC_URL, EtpcRetriever
from etpc.semantic import EtpcSemantic
from etpc.weights import TIERS
from judgments_core.mcp import McpServer, cut, lines
from judgments_core.rag import render
from judgments_core.semantic import SemanticUnavailable

RELATIONS = {"violation": "naruszenie", "no_violation": "brak naruszenia", "concerned": "dotyczy"}

INSTRUCTIONS = """\
Orzeczenia Europejskiego Trybunału Praw Człowieka z HUDOC: wyroki i decyzje,
teksty po angielsku albo francusku (część też po polsku), artykuły Konwencji,
naruszenia, cytowania. Szukaj narzędziem szukaj (po słowach i po znaczeniu;
polskie zwroty prawnicze są tłumaczone) albo fragmenty (od razu najlepsze
fragmenty tekstów do pytania), potem czytaj orzeczenie(id) i cytuj
nazwę sprawy, numer skargi, datę oraz link do HUDOC. Nie zgaduj treści
orzeczeń, których nie przeczytałeś."""


class EtpcMcp(McpServer):
    project = "etpc"
    title = "Orzecznictwo ETPC"
    instructions = INSTRUCTIONS
    semantic_class = EtpcSemantic
    retriever_class = EtpcRetriever

    def register(self, server):
        server.add_tool(self.szukaj, name="szukaj", description=self.szukaj.__doc__)
        server.add_tool(self.fragmenty, name="fragmenty", description=self.fragmenty.__doc__)
        server.add_tool(self.orzeczenie, name="orzeczenie", description=self.orzeczenie.__doc__)
        server.add_tool(self.podobne, name="podobne", description=self.podobne.__doc__)

    def szukaj(
        self,
        zapytanie: str = "",
        tryb: str = "",
        rodzaj: str = "",
        panstwo: str = "",
        artykul: str = "",
        wynik: str = "",
        waznosc: int = 0,
        waga: str = "",
        rok_od: int = 0,
        rok_do: int = 0,
        nr_skargi: str = "",
        jezyk: str = "",
        sortuj: str = "",
        strona: int = 1,
    ) -> str:
        """Szukaj orzeczeń ETPC; 20 wyników na stronę, z fragmentem tekstu.

        zapytanie: słowa albo opis problemu ("przewlekłość postępowania karnego");
          "dokładna fraza", -wykluczone; polskie zwroty tłumaczone na angielski/francuski.
        tryb: "" hybrydowe (domyślnie) / pelnotekstowe / znaczeniowe.
        rodzaj: judgment (wyrok) / decision (decyzja). panstwo: kod, np. POL, TUR, RUS.
        artykul: artykuł Konwencji, np. 6, 6-1, 3, P1-1; sam artykul = powołany w sprawie
          (też odrzucony lub niezbadany), orzeczenia o nim: z wynik. wynik: violation /
          no_violation / concerned (wobec artykul). waznosc: 1 kluczowe ... 4 (co najmniej taka).
        waga: A / B / C (co najmniej taka; A kluczowe, B ważne, C zwykłe, D rutynowe) - z ważności
          HUDOC, Wielkiej Izby i liczby cytowań; powód przy każdym wyniku.
        nr_skargi: np. 30210/96. jezyk: ENG / FRE / POL (wersja z fragmentem).
        sortuj: "" trafność / data / najstarsze / cytowane.
        """
        q = search.SearchQuery(
            text=zapytanie,
            mode=tryb,
            kind=rodzaj,
            respondent=panstwo.upper(),
            article=artykul,
            relation=wynik,
            importance=waznosc,
            weight=_weight(waga),
            year_from=rok_od,
            year_to=rok_do,
            appno=nr_skargi,
            language=jezyk.upper(),
            sort=sortuj,
            page=strona,
        )
        with self.connect() as conn, conn.cursor() as cur:
            try:
                result = search.search(cur, q, self.semantic)
            except search.QueryError as e:
                return f"Błąd zapytania: {e}"
        head = f"Znaleziono {result.total} (tryb {result.mode}), strona {max(strona, 1)}."
        if result.notice:
            head += f"\nUwaga: {result.notice}"
        return "\n\n".join([head] + [_hit(h) for h in result.hits])

    def fragmenty(
        self,
        pytanie: str,
        rodzaj: str = "",
        panstwo: str = "",
        artykul: str = "",
        wynik: str = "",
        waznosc: int = 0,
        waga: str = "",
        rok_od: int = 0,
        rok_do: int = 0,
        limit_znakow: int = 0,
        tryb: str = "",
    ) -> str:
        """Najlepsze fragmenty orzeczeń ETPC do pytania, gotowe do odpowiedzi z cytowaniem:
        [n] sprawa, dane, link HUDOC, a pod spodem fragmenty (nagłówek, znaki od-do).
        Po znaki można sięgnąć dalej: orzeczenie(id, od=...).

        pytanie: opis problemu po polsku albo angielsku. Filtry jak w szukaj (panstwo: POL ...,
        artykul: 6, wynik: violation, waznosc: 1-4, waga: A-C). limit_znakow: ile tekstu (domyślnie 16000).
        tryb: domyślnie znaczeniowe (szybko); hybrydowe = też po słowach (bywa bardzo wolne).
        """
        q = self.retriever.query(
            pytanie,
            mode=tryb,
            kind=rodzaj,
            respondent=panstwo.upper(),
            article=artykul,
            relation=wynik,
            importance=waznosc,
            weight=_weight(waga),
            year_from=rok_od,
            year_to=rok_do,
        )
        with self.connect() as conn, conn.cursor() as cur:
            try:
                result = self.retriever.retrieve(cur, q, limit_znakow or None)
            except search.QueryError as e:
                return f"Błąd zapytania: {e}"
        notice = f"Uwaga: {result.notice}\n\n" if result.notice else ""
        return notice + render(result)

    def orzeczenie(self, id: int, jezyk: str = "", od: int = 0) -> str:
        """Orzeczenie o tym id (z wyników szukaj): metadane i pełny tekst jednej wersji
        językowej (jezyk: ENG / FRE / POL ...; domyślnie najlepsza dostępna). Długi tekst
        przychodzi w częściach: kolejną daje od=<liczba z końca>.
        """
        with self.connect() as conn, conn.cursor() as cur:
            page = search.ruling_page(cur, id, jezyk.upper())
        if page is None:
            return f"Nie ma orzeczenia o id {id}."
        r, doc = page.ruling, page.document
        body = doc["body"] if doc else ""
        if od:
            return f"{r['title']}, tekst od znaku {od}:\n\n{cut(body, od)}"
        meta = lines(
            ("Sprawa", r["title"]),
            (
                "Rodzaj",
                f"{label(labels.KINDS, r['kind'])}, {label(labels.FORMATIONS, r['formation'])}",
            ),
            ("Data", r["ruling_date"]),
            ("ECLI", r["ecli"]),
            ("Skargi", ", ".join(page.applications)),
            ("Państwa", ", ".join(label(labels.STATES, s) for s in page.states)),
            (
                "Waga",
                f"{page.weight[0]} {TIERS[page.weight[0]]} ({page.weight[1]})"
                if page.weight
                else "",
            ),
            ("Ważność", label(labels.IMPORTANCE, r["importance"])),
            (
                "Artykuły",
                "; ".join(
                    f"{RELATIONS.get(rel, rel)}: {', '.join(arts)}"
                    for rel, arts in page.articles.items()
                ),
            ),
            ("Wnioski", r["conclusion"]),
            ("Sędziowie", r["judges"]),
            ("Cytowane przez", f"{page.cited_by_count} orzeczeń" if page.cited_by_count else ""),
            (
                "Wersje",
                ", ".join(f"{d['language']} ({d['itemid']})" for d in page.documents),
            ),
            ("Tekst", f"{doc['language']}, {HUDOC_URL.format(doc['itemid'])}" if doc else ""),
        )
        return f"{meta}\n\n{cut(body)}"

    def podobne(self, id: int, ile: int = 10) -> str:
        """Orzeczenia najbliższe znaczeniem orzeczeniu o tym id (z części prawnej uzasadnienia)."""
        if self.semantic is None:
            return "Wyszukiwanie znaczeniowe jest wyłączone."
        try:
            passages = self.semantic.similar(id, min(max(ile, 1), 50))
        except SemanticUnavailable as e:
            return str(e)
        if not passages:
            return "Brak wektorów tego orzeczenia (python -m etpc wektory)."
        by_id = {p.judgment_id: p for p in passages}
        with self.connect() as conn, conn.cursor() as cur:
            hits = search._hits(cur, list(by_id))
            search._add_passages(cur, hits, by_id)
        return "\n\n".join(_hit(h) for h in hits)


def _weight(value):
    value = value.strip().upper()
    return value if value in TIERS else ""


def _hit(h):
    head = f"[id {h.id}] {h.title} - {label(labels.KINDS, h.kind)} z {h.ruling_date}"
    details = lines(
        ("Skargi", ", ".join(h.appnos) + (" ..." if h.appno_count > len(h.appnos) else "")),
        ("Państwa", ", ".join(h.states)),
        ("Naruszenia", ", ".join(h.violations)),
        ("Waga", f"{h.tier} {TIERS[h.tier]} ({h.reasons})" if h.tier else ""),
        ("Cytowane", h.cited_by or ""),
        ("HUDOC", HUDOC_URL.format(h.itemid) if h.itemid else ""),
    )
    snippet = f"fragment ({h.snippet_language or '?'}): {h.snippet}" if h.snippet else ""
    return "\n".join(x for x in (head, details, snippet) if x)
