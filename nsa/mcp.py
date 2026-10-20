"""NSA/WSA judgments as tools for Claude: python -m nsa mcp (child of McpServer).

szukaj - hybrid search (words + meaning) with the page's filters;
fragmenty - the best passages for a question, packed into a limit (RAG);
orzeczenie - metadata and the text (in parts for long ones);
podobne - judgments closest in meaning to one judgment.
"""

from __future__ import annotations

from judgments_core.mcp import McpServer, cut, lines
from judgments_core.rag import render
from judgments_core.semantic import SemanticUnavailable
from nsa import search
from nsa.rag import CBOSA_URL, FINAL, NsaRetriever
from nsa.semantic import NsaSemantic

INSTRUCTIONS = """\
Orzeczenia polskich sądów administracyjnych (NSA i wszystkie WSA) z bazy CBOSA,
do marca 2025: wyroki, postanowienia, uchwały; teza, sentencja, uzasadnienie.
Szukaj narzędziem szukaj (po słowach i po znaczeniu naraz) albo fragmenty
(od razu najlepsze fragmenty tekstów do pytania), potem czytaj
orzeczenie(id) i cytuj sygnaturę, datę, sąd oraz link do CBOSA. Nie zgaduj
treści orzeczeń, których nie przeczytałeś."""


class NsaMcp(McpServer):
    project = "nsa"
    title = "Orzecznictwo sądów administracyjnych"
    instructions = INSTRUCTIONS
    semantic_class = NsaSemantic
    retriever_class = NsaRetriever

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
        instancja: str = "",
        sad: str = "",
        rok_od: int = 0,
        rok_do: int = 0,
        symbol: str = "",
        haslo: str = "",
        sedzia: str = "",
        organ: str = "",
        prawomocne: str = "",
        tylko_z_teza: bool = False,
        sortuj: str = "",
        strona: int = 1,
    ) -> str:
        """Szukaj orzeczeń NSA/WSA; 20 wyników na stronę, z fragmentem tekstu.

        zapytanie: słowa albo opis problemu prawnego ("zwrot VAT przy imporcie usług");
          "dokładna fraza", -wykluczone, słowa odmieniane same (rdzeń).
        tryb: "" hybrydowe (domyślnie) / pelnotekstowe / znaczeniowe.
        rodzaj: wyrok / postanowienie / uchwala / inne. instancja: nsa / wsa.
        sad: pełna nazwa, np. "WSA w Warszawie". symbol: kod sprawy, np. 6110 (VAT).
        haslo: hasło tematyczne CBOSA, np. "Podatek od towarów i usług".
        organ: początek nazwy skarżonego organu. prawomocne: "1" / "0".
        sortuj: "" trafność / data / najstarsze.
        """
        q = search.SearchQuery(
            text=zapytanie,
            mode=tryb,
            kind=rodzaj,
            instance=instancja,
            court=sad,
            year_from=rok_od,
            year_to=rok_do,
            symbol=symbol,
            keyword=haslo,
            judge=sedzia,
            authority=organ,
            final=prawomocne,
            with_thesis=tylko_z_teza,
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
        instancja: str = "",
        sad: str = "",
        rok_od: int = 0,
        rok_do: int = 0,
        symbol: str = "",
        haslo: str = "",
        tylko_z_teza: bool = False,
        limit_znakow: int = 0,
        tryb: str = "",
    ) -> str:
        """Najlepsze fragmenty orzeczeń NSA/WSA do pytania, gotowe do odpowiedzi z cytowaniem:
        [n] sygnatura, sąd, data, link CBOSA, a pod spodem fragmenty (część, znaki od-do).
        Po znaki można sięgnąć dalej: orzeczenie(id, od=...).

        pytanie: opis problemu prawnego. Filtry jak w szukaj (instancja: nsa / wsa, symbol: 6110,
        haslo, tylko_z_teza). limit_znakow: ile tekstu (domyślnie 16000).
        tryb: "" hybrydowe (domyślnie) / znaczeniowe / pelnotekstowe.
        """
        q = self.retriever.query(
            pytanie,
            mode=tryb,
            kind=rodzaj,
            instance=instancja,
            court=sad,
            year_from=rok_od,
            year_to=rok_do,
            symbol=symbol,
            keyword=haslo,
            with_thesis=tylko_z_teza,
        )
        with self.connect() as conn, conn.cursor() as cur:
            try:
                result = self.retriever.retrieve(cur, q, limit_znakow or None)
            except search.QueryError as e:
                return f"Błąd zapytania: {e}"
        notice = f"Uwaga: {result.notice}\n\n" if result.notice else ""
        return notice + render(result)

    def orzeczenie(self, id: int, od: int = 0) -> str:
        """Orzeczenie o tym id (z wyników szukaj): metadane i pełny tekst (teza, sentencja,
        uzasadnienie). Długi tekst przychodzi w częściach: kolejną daje od=<liczba z końca>.
        """
        with self.connect() as conn, conn.cursor() as cur:
            page = search.judgment_page(cur, id)
        if page is None:
            return f"Nie ma orzeczenia o id {id}."
        j = page.judgment
        meta = lines(
            ("Sygnatura", j["docket_number"]),
            ("Orzeczenie", f"{j['judgment_type']} z {j['judgment_date']}"),
            ("Sąd", j["court"]),
            ("Prawomocne", FINAL.get(j["is_final"])),
            ("Data wpływu", j["submission_date"]),
            ("Sędziowie", ", ".join(_judge(*p) for p in page.judges)),
            ("Symbole", "; ".join(f"{c} {d}".strip() for c, d in page.symbols)),
            ("Hasła", "; ".join(page.keywords)),
            ("Skarżony organ", j["challenged_authority"]),
            ("Wynik", "; ".join(page.results)),
            (
                "Przepisy",
                "; ".join(
                    " ".join(x for x in (b["journal"], b["article"], b["law"]) if x)
                    for b in page.legal_bases
                ),
            ),
            (
                "Powiązane",
                "; ".join(
                    f"{r['docket_number']} ({r['judgment_date']}, id {r['id']})"
                    if r["id"]
                    else f"{r['docket_number']} ({r['judgment_date']})"
                    for r in page.related
                ),
            ),
            ("Publikacje", "; ".join(t for _, t in page.publications)),
            ("CBOSA", CBOSA_URL.format(j["cbosa_id"])),
        )
        if od:
            return f"{j['docket_number']}, tekst od znaku {od}:\n\n{cut(page.body, od)}"
        return f"{meta}\n\n{cut(page.body)}"

    def podobne(self, id: int, ile: int = 10) -> str:
        """Orzeczenia najbliższe znaczeniem orzeczeniu o tym id (z uzasadnienia/tezy)."""
        if self.semantic is None:
            return "Wyszukiwanie znaczeniowe jest wyłączone."
        try:
            passages = self.semantic.similar(id, min(max(ile, 1), 50))
        except SemanticUnavailable as e:
            return str(e)
        if not passages:
            return "Brak wektorów tego orzeczenia (python -m nsa wektory)."
        by_id = {p.judgment_id: p for p in passages}
        with self.connect() as conn, conn.cursor() as cur:
            hits = search._hits(cur, list(by_id))
            search._add_passages(cur, hits, by_id)
        return "\n\n".join(_hit(h) for h in hits)


def _hit(h):
    head = f"[id {h.id}] {h.docket_number} - {h.judgment_type} z {h.judgment_date}"
    details = lines(
        ("Symbole", ", ".join(h.symbols)),
        ("Organ", h.challenged_authority),
        ("Wynik", h.result),
        ("CBOSA", CBOSA_URL.format(h.cbosa_id)),
    )
    snippet = f"{h.snippet_part}: {h.snippet}" if h.snippet else ""
    return "\n".join(x for x in (head, details, snippet) if x)


def _judge(name, presiding, rapporteur):
    roles = [r for r, on in (("przewodniczący", presiding), ("sprawozdawca", rapporteur)) if on]
    return f"{name} ({', '.join(roles)})" if roles else name
