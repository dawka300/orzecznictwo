"""NSA formulas: the list (python -m nsa formulki: sentences repeated word for word in the
reasons of many judgments, a random sample - child of FormulaCorpus) and the patterns of
whole sentences: a court's formulas said in many words and the parts of a party's brief
or an authority's decision that are no argument (headings, petitum, signature,
instruction on appeal). load() gives both to the pages.

A pattern must match the whole sentence (fullmatch, case ignored), so "Skarga nie
zasługuje na uwzględnienie." goes, "Skarga nie zasługuje na uwzględnienie, bo organ
..." stays. Only the reasons are counted (what the comparison reads); the sample by
random ids of the primary key (no scan of the 45 GB of reasons).
"""

from __future__ import annotations

from pathlib import Path

from judgments_core.boilerplate import FormulaCorpus, Formulas

FILE = Path(__file__).parent / "formulki.txt"
BATCH = 500

_END = r"\s*[.:;]?"
_MEANS = r"(?:skarga(?: kasacyjna)?|zażalenie|wniosek|sprzeciw|odwołanie|środek zaskarżenia)"
# "Z tych względów", "W ocenie Sądu", "Wniesiona w sprawie" ...; bez "że": cudze stanowisko
# ("Organ uznał, że wniosek nie zasługuje na uwzględnienie.") zostaje.
_LEAD = r"(?:(?!\bże\b).){0,60}?\b"
_BRACKET = r"(?:\s*\(.{0,120}?\))?"
_PETITUM = (
    r"(?:uchylenie|stwierdzenie nieważności|zasądzenie|zwrot\w* kosztów|rozpoznanie|"
    r"przekazanie|zmian\w|uwzględnienie|wstrzymanie|przyznanie|zwolnienie|oddalenie|"
    r"odrzucenie|umorzenie|skierowanie)"
)

COURT_PATTERNS = (
    # nagłówek i sentencja (całe orzeczenie, np. w Badaniu; Porównanie bierze same rozważania)
    (
        "nagłówek orzeczenia",
        r"(?:wyrok|postanowienie|uchwała|u\s?z\s?a\s?s\s?a\s?d\s?n\s?i\s?e\s?n\s?i\s?e|sentencja|"
        r"teza)(?: w imieniu rzeczypospolitej polskiej)?" + _END,
    ),
    ("nagłówek orzeczenia", r"w imieniu rzeczypospolitej polskiej" + _END),
    ("nagłówek orzeczenia", r"(?:dnia )?\d{1,2} \w+ \d{4} r\.?(?: r\.)?"),
    (
        "nagłówek orzeczenia",
        r"(?:sygn\.(?: akt)?|sygnatura(?: akt)?)\s*:?.{0,40}|[IVX]+ [A-Za-z]{2,4}/[A-Za-z]{1,3} "
        r"\d+/\d+" + _END,
    ),
    ("skład sądu", r".{0,200}?\bw składzie(?: następującym)?\s*:?"),
    (
        "skład sądu",
        (
            # sam skład (nazwiska, tytuły); "Referendarz sądowy zasadnie uznał, że ..." zostaje
            r"(?!.*\b(?:że|iż|uznał\w*|stwierdził\w*|wskazał\w*|przyjął\w*|zasadnie|słusznie|"
            r"prawidłowo|błędnie|trafnie|nie)\b)"
            r"(?:przewodnicząc[ya]|sędzia|sędziowie|protokolant(?:ka)?|asesor|referendarz)"
            r"(?: (?:sądowy|wsa|nsa|sn|del\.|sekr\.|st\.))*\b.{0,200}"
        ),
    ),
    (
        "sentencja",
        (
            r".{0,60}?\bpo rozpoznaniu\b.{0,200}?\bspraw\w* (?:ze skargi|z wniosku|ze sprzeciwu|"
            r"z zażalenia|ze skargi kasacyjnej).{0,700}"
        ),
    ),
    (
        "sentencja",
        (
            r"(?:\d{1,2}[.)]\s*|[IVX]+[.)]\s*)?(?:oddala|odrzuca|uchyla|stwierdza|zasądza|umarza|"
            r"przyznaje|zwraca|odstępuje|zobowiązuje|przekazuje|postanawia|orzeka|wymierza|"
            r"określa|ustala)\b(?! (?:się|on|ona|ono|to|także|również|jednak|uwagę)\b).{0,400}"
        ),
    ),
    (
        "ocena środka zaskarżenia",
        _LEAD + _MEANS + r"\b.{0,40}?\b(?:nie )?(?:zasługuje|zasługiwał\w*|zasługiwać|mogła|może)"
        r"(?: \w+)? na uwzględnienie" + _END,
    ),
    (
        "ocena środka zaskarżenia",
        _LEAD + _MEANS + r" (?:jest|była|był|było|okazała się|okazał się|okazało się|nie jest|"
        r"nie była|nie okazała się)(?: \w+)? (?:nie)?(?:zasadn\w*|uzasadnion\w*|bezzasadn\w*|"
        r"usprawiedliwion\w*|trafn\w*|chybion\w*)" + _END,
    ),
    (
        "ocena środka zaskarżenia",
        _LEAD + _MEANS + r" (?:nie )?(?:ma|miała|zawiera|zawierała|posiada|została oparta na|"
        r"jest oparta na|opiera się na|oparta została na)(?: \w+)? usprawiedliwion\w* podstaw\w*"
        + _END,
    ),
    (
        "rozstrzygnięcie",
        _LEAD + _MEANS + r" (?:podlega|podlegała|podlegał|podlegało|należało) "
        r"(?:oddaleniu|odrzuceniu|uwzględnieniu|oddalić|odrzucić|uwzględnić)" + _END,
    ),
    (
        "rozstrzygnięcie",
        _LEAD + r"(?:skargę|skargę kasacyjną|zażalenie|wniosek|sprzeciw) należało "
        r"(?:oddalić|odrzucić|uwzględnić)" + _END,
    ),
    (
        "początek rozważań",
        r".{0,120}?(?:z\s?w\s?a\s?ż\s?y\s?ł\w*|zważono|zważyć należało|należało zważyć|"
        r"stwierdzono|rozważono),?\s+co następuje" + _END,
    ),
    (
        "zakres kontroli sądu (art. 1 p.u.s.a., art. 3 p.p.s.a.)",
        (
            r".{0,250}?sprawują (?:wymiar sprawiedliwości (?:przez|poprzez) )?kontrolę działalności "
            r"administracji publicznej.{0,160}"
        ),
    ),
    (
        "zakres kontroli sądu (art. 1 p.u.s.a., art. 3 p.p.s.a.)",
        r".{0,250}?pod względem (?:jej |ich )?zgodności z prawem(?:,? jeżeli ustawy nie stanowią "
        r"inaczej)?" + _BRACKET + _END,
    ),
    (
        "granice sprawy (art. 134 p.p.s.a.)",
        r".{0,200}?nie (?:będąc|jest|był) (?:jednak |przy tym )?związany zarzutami(?:,| i) "
        r"wnioskami (?:skargi )?(?:oraz|ani|i) powołaną (?:w niej )?podstawą prawną"
        + _BRACKET
        + _END,
    ),
    (
        "granice skargi kasacyjnej (art. 183 p.p.s.a.)",
        r".{0,200}?rozpoznaje sprawę w granicach skargi kasacyjnej,?" + _BRACKET + r"\s*"
        r"(?:bierze|biorąc) (?:jednak |pod uwagę )?z urzędu pod rozwagę nieważność postępowania"
        r".{0,60}",
    ),
    (
        "nieważność postępowania (art. 183 § 2 p.p.s.a.)",
        (
            r".{0,150}?(?:nie (?:zachodzi|zachodzą|występują|występuje|stwierdzono|dopatrzono się|"
            r"wystąpiła|wystąpiły)|brak) .{0,80}?(?:przesłan\w*|okoliczności|przyczyn\w*) "
            r"nieważności postępowania.{0,150}"
        ),
    ),
    (
        "podstawy kasacyjne (art. 174 p.p.s.a.)",
        (
            r".{0,120}?skarg\w* kasacyjn\w* (?:można|może) (?:oprzeć|być oparta|zostać oparta) na "
            r"(?:następujących )?podstawach.{0,250}"
        ),
    ),
    (
        "koszty",
        (
            r"(?:o kosztach|o zwrocie kosztów|o zasądzeniu|o należnych kosztach|rozstrzygnięcie o "
            r"kosztach|orzeczenie o kosztach|o wynagrodzeniu|o przyznaniu wynagrodzenia).{0,400}"
        ),
    ),
    (
        "koszty",
        (
            r"(?:na podstawie|stosownie do|w oparciu o|zgodnie z) art\..{0,200}?\b(?:zasądz\w*|"
            r"kosztów|koszty)\b.{0,150}"
        ),
    ),
    (
        "zakończenie",
        (
            r"(?:mając (?:na (?:względzie|uwadze) )?(?:powyższe|to|powyższe okoliczności)"
            r"(?: na (?:względzie|uwadze))?|z (?:tych|powyższych|przedstawionych|wskazanych|"
            r"wyżej wskazanych) (?:względów|przyczyn|powodów)|biorąc (?:pod uwagę|powyższe pod uwagę)"
            r"|w tym stanie rzeczy|wobec powyższego|w świetle powyższego|reasumując|kierując się "
            r"powyższym|uwzględniając powyższe),?.{0,250}?(?:orzek\w*|orzeczono|postanowi\w*|"
            r"należało orzec|jak w sentencji|na podstawie art\.).{0,200}"
        ),
    ),
    ("zakończenie", r".{0,200}?jak w sentencji.{0,100}"),
    (
        "przebieg postępowania",
        (
            r"w odpowiedzi na (?:skargę|skargę kasacyjną|zażalenie|sprzeciw).{0,120}?wni\w* o "
            r"(?:jej |jego )?(?:oddalenie|odrzucenie|nieuwzględnienie).{0,200}"
        ),
    ),
    (
        "przebieg postępowania",
        r"(?:odpowiedzi na (?:skargę|skargę kasacyjną|zażalenie) nie (?:wniesiono|złożono)|nie "
        r"(?:wniesiono|złożono) odpowiedzi na (?:skargę|skargę kasacyjną|zażalenie))" + _END,
    ),
    (
        "wstęp do stanu sprawy",
        r".{0,100}?(?:w następującym stanie|w następujących okolicznościach|stan (?:faktyczny |"
        r"prawny |i prawny )*sprawy przedstawia się|ustalono następujący stan|za podstawę "
        r"rozstrzygnięcia przyjął następujące).{0,80}" + _END,
    ),
)

BRIEF_PATTERNS = (
    (
        "nagłówek: sąd",
        r"(?:do )?(?:naczelny|wojewódzki) sąd administracyjny(?: w \w+(?: \w+)?)?"
        r"(?:,? (?:wydział \w+|ul\. .{0,60}|\d{2}-\d{3} .{0,40}))*" + _END,
    ),
    ("nagłówek: adres", r"(?:za pośrednictwem|ul\.|al\.|\d{2}-\d{3})\s*\S.{0,150}"),
    (
        "nagłówek: strony",
        (
            r"(?:skarżąc\w*|wnoszący skargę(?: kasacyjną)?|organ(?: administracji| odwoławczy)?|"
            r"pełnomocnik\w*(?: skarżącego| strony)?|strona przeciwna|uczestni\w+|wnioskodawc\w*|"
            r"odwołując\w* się|reprezentowan\w* przez)\s*:.{0,200}"
        ),
    ),
    (
        "nagłówek: sygnatura, wartość",
        (
            r"(?:sygn\.(?: akt)?|sygnatura(?: akt)?|znak sprawy|nr sprawy|wartość przedmiotu "
            r"(?:zaskarżenia|sporu))\s*:?.{0,80}"
        ),
    ),
    (
        "nagłówek: rodzaj pisma",
        r"(?:skarga(?: kasacyjna)?|odwołanie|zażalenie|wniosek|sprzeciw|pismo procesowe|replika|"
        r"odpowiedź na skargę|zarzuty|uzasadnienie|petitum|pouczenie)" + _END,
    ),
    (
        "nagłówek: rodzaj pisma",
        (
            r"(?:skarga(?: kasacyjna)?|odwołanie|zażalenie|wniosek|sprzeciw) (?:na|od|o) "
            r"(?:decyzj\w|uchwał\w|postanowieni\w|wyrok\w*|bezczynność|przewlekłe|zarządzeni\w)"
            r".{0,250}"
        ),
    ),
    (
        "nagłówek: rodzaj pisma",  # drugi wiersz: "na decyzję ... z dnia ... w przedmiocie ..."
        (
            r"(?:na|od) (?:decyzj\w|uchwał\w|postanowieni\w|wyrok\w*|bezczynność|przewlekłe|"
            r"zarządzeni\w)\b(?=.*\b(?:z dnia|w przedmiocie|nr|znak|sygn)\b).{0,250}"
        ),
    ),
    (
        "wstęp pisma",
        (
            r"(?:działając (?:w imieniu|jako pełnomocnik)|w imieniu (?:własnym|skarżącego|mocodawcy))"
            r".{0,400}"
        ),
    ),
    (
        "wstęp pisma",
        (
            r"(?:na podstawie|w oparciu o|stosownie do) art\..{0,200}?\b(?:wnoszę|wnosimy|składam|"
            r"składamy|zaskarżam|zaskarżamy|wnosi)\b.{0,250}"
        ),
    ),
    (
        "wstęp do zarzutów",
        r"(?:zaskarżon\w+ \w+(?: \w+)? )?(?:zarzucam|zarzucamy|zarzuca|zarzuciła?|zarzucono)"
        r"(?: \w+){0,4}" + _END,
    ),
    (
        "wnioski (petitum)",
        r".{0,80}?\b(?:wnoszę|wnosimy|wnosi|domagam się|domagamy się)(?: \w+)? o\s*:?\s*"
        r"(?:\d+[.)]\s*)?" + _PETITUM + r"\b.{0,400}",
    ),
    ("wnioski (petitum)", r".{0,80}?\b(?:wnoszę|wnosimy|wnosi) o" + _END),
    (
        "wnioski (petitum)",
        r"(?:\d{1,2}[.)]|[a-z][.)]|-|•)?\s*" + _PETITUM + r"\b"
        r"(?=.*\b(?:zaskarżon\w*|w całości|ponownego rozpoznania|kosztów|według norm|na rzecz)\b)"
        r".{0,250}",
    ),
    (
        "zakończenie, podpis, załączniki",
        (
            r"(?:z poważaniem|z wyrazami szacunku|łączę wyrazy szacunku|\(?podpis\w*\)?|w załączeniu|"
            r"załącznik\w*)\b.{0,200}"
        ),
    ),
    (
        "zakończenie, podpis, załączniki",
        (
            r"(?:\d{1,2}[.)]|-|•)\s*(?:\d+\s*x\s*)?(?:odpis|pełnomocnictw|dowód uiszczenia|"
            r"potwierdzeni|kopi|oryginał)\w*\b[^.]{0,80}\.?"
        ),
    ),
    (
        "pouczenie",
        (
            r"(?:od (?:niniejszej|tej) (?:decyzji|uchwały|postanowienia)|na (?:niniejszą|tę) "
            r"(?:decyzję|uchwałę)|na (?:niniejsze|to) postanowienie).{0,400}"
        ),
    ),
    (
        "miejscowość i data",
        (
            r"[\wąćęłńóśźż-]+\.?, (?:dnia |dn\. )?\d{1,2}(?:[./-]\d{1,2}[./-]"
            r"\d{2,4}| \w+ \d{4}(?: r\.?)?)"
        ),
    ),
)


def load():
    """The list and the patterns for the pages."""
    return Formulas.load(FILE, COURT_PATTERNS, BRIEF_PATTERNS)


class NsaFormulas(FormulaCorpus):
    file = FILE
    source = "uzasadnienia orzeczeń NSA i WSA (CBOSA), losowa próbka"

    def sample_texts(self, size, rng):
        with self.conn.cursor() as cur:
            cur.execute("SELECT MIN(id), MAX(id) FROM judgments")
            low, high = cur.fetchone()
            if low is None:
                return
            seen, given = set(), 0
            while given < size and len(seen) < high - low + 1:
                ids = []
                while len(ids) < BATCH and len(seen) < high - low + 1:
                    n = rng.randint(low, high)
                    if n not in seen:
                        seen.add(n)
                        ids.append(n)
                cur.execute(
                    "SELECT reasons FROM judgments WHERE id IN %s AND reasons IS NOT NULL", (ids,)
                )
                for (reasons,) in cur.fetchall():
                    if given >= size:
                        return
                    given += 1
                    yield reasons
