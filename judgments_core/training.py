"""Training documents for our models ("Trening modeli", /trening): complaints, judgments,
authorities' answers ... uploaded by hand and kept in their own database `trening`
(settings.training_dsn), apart from the case folder and from the projects' bases. They
teach the patterns of each kind of document (how an SKO answer or a complaint to a WSA
begins and ends), so that the comparison of documents knows what to filter out.

Only on the developer's machine (APP_ENV=dev): elsewhere the tab is hidden and its
addresses answer 404. A child of library.Library - the same import (Word, PDF, OCR of a
scan), catalogs, tables and fragment vectors saved in the database on import - with
document kinds instead of the writer's role and no case analysis, plus the export of the
texts as JSON Lines.
"""

from __future__ import annotations

import json

from judgments_core.library import Library, ancestry, catalog_paths
from judgments_core.web import Response, e

KINDS = (
    ("inny", "inny / nie wiem"),
    ("skarga", "skarga"),
    ("orzeczenie", "orzeczenie (wyrok, postanowienie)"),
    ("odpowiedz", "odpowiedź na skargę"),
    ("pismo_organu", "pismo organu, decyzja"),
    ("pismo_strony", "pismo strony"),
)
EXPORT = "/eksport.jsonl"


class TrainingLibrary(Library):
    """The pages under /trening; a 404 unless settings.dev."""

    base = "/trening"
    title = "Trening modeli"
    short = "Trening"
    roles = KINDS
    role_label = "rodzaj"
    analyse_button = False
    remarks = True
    remarks_hint = (
        "np. skarga do SKO; przy porównaniu i badaniu pominąć pierwszą część (nagłówek,"
        " petitum) - uwagi dotyczą wszystkich dodawanych teraz plików"
    )

    def dsn(self):
        return self.settings.training_dsn

    def handle(self, request, layout):
        if not self.settings.dev:
            return Response(layout(self.title, "<p>Nie ma takiej strony.</p>"), "404 Not Found")
        if request.path == self.base + EXPORT:
            return self.export(request.get_int("k") or None)
        return super().handle(request, layout)

    def export(self, catalog_id=None):
        """JSON Lines, one document per line (text included): all of them, or one catalog
        with its subcatalogs."""
        with self.store() as store:
            catalogs = store.catalogs()
            documents = store.documents(everywhere=True)
            if catalog_id:
                inside = {c[0] for c in catalogs if catalog_id in ancestry(catalogs, c[0])}
                documents = [d for d in documents if d.catalog_id in inside]
            paths = catalog_paths(catalogs)
            kinds = dict(KINDS)
            lines = [
                json.dumps(
                    {
                        "id": d.id,
                        "katalog": paths.get(d.catalog_id, ""),
                        "tytul": d.title,
                        "rodzaj": d.role,
                        "rodzaj_opis": kinds.get(d.role, d.role),
                        "data": d.doc_date.isoformat() if d.doc_date else None,
                        "plik": d.filename,
                        "uwaga": d.note,  # adnotacja z odczytu pliku (OCR ...)
                        "uwagi": d.remarks,  # uwagi użytkownika
                        "tekst": store.text(d.id),
                    },
                    ensure_ascii=False,
                )
                for d in documents
            ]
        name = f"trening-{catalog_id}.jsonl" if catalog_id else "trening.jsonl"
        return Response(
            "".join(line + "\n" for line in lines),
            content_type="application/x-ndjson; charset=utf-8",
            headers=[("Content-Disposition", f'attachment; filename="{name}"')],
        )

    def intro(self):
        return f"""<p class=hint>Dokumenty do nauki wzorców (jak zaczyna się i kończy skarga do WSA,
odpowiedź SKO, wyrok ...), żeby porównanie wiedziało, co odsiać. Osobna baza MariaDB
<code>trening</code>, tylko na komputerze dewelopera (<code>APP_ENV=dev</code>). Przy imporcie
plik jest czytany raz (także OCR skanu), a tekst i wektory fragmentów zapisują się w bazie;
rodzaj i uwagi (np. czego nie brać pod uwagę przy porównaniu i badaniu) wpiszesz przy dodawaniu albo potem
na stronie dokumentu; uwagi idą do eksportu. <a href="{self.base}{EXPORT}">Eksport JSONL (wszystko)</a>.</p>"""

    def catalog_page(self, catalog_id, note=""):
        page = super().catalog_page(catalog_id, note)
        if catalog_id:
            page += (
                f'<p><a class=button href="{self.base}{EXPORT}?k={catalog_id}">'
                f"Eksport JSONL tego katalogu</a></p>"
            )
        return page

    def tools(self, stored):
        return f"<span class=hint>rodzaj: {e(dict(KINDS).get(stored.role, stored.role))}</span>"
