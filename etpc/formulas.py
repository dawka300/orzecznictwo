"""ETPC formulas: the list (python -m etpc formulki: sentences repeated word for word in many
HUDOC texts, a random sample of the language versions - child of FormulaCorpus) and the
patterns of whole sentences in the Court's languages (English, French) and in the Polish
translations: the heading, the composition, the procedure, the stock sentences of
admissibility and Article 41, the operative part, "Done in English ...". A brief to the
Court (in Polish or English): headings, the form's parts, the letter's end (the core's
LETTER_PATTERNS).

A pattern must match the whole sentence or line (fullmatch, case ignored), so "Holds that
there has been a violation of Article 6 § 1;" goes, "The Court holds that the length of
the proceedings ... was excessive." stays.
"""

from __future__ import annotations

from pathlib import Path

from judgments_core.boilerplate import LETTER_PATTERNS, FormulaCorpus

FILE = Path(__file__).parent / "formulki.txt"
BATCH = 200

_END = r"\s*[.:;,]?"
_VS = r"(?:v\.|c\.|przeciwko)"
_HEADINGS = (
    r"procedure|the facts|the law|the circumstances of the case|relevant (?:domestic|legal)"
    r" (?:law|framework)(?: and practice)?|relevant international (?:law|material)"
    r"(?: and practice)?|complaints?|alleged violations? of articles? .{1,80}|admissibility"
    r"|merits|application of article 41 of the convention|damage|pecuniary damage"
    r"|non-pecuniary damage|costs and expenses|default interest|the parties['’] submissions"
    r"|the court['’]s assessment|general principles|application (?:of those|of the general)"
    r" principles to the (?:present|facts of the present) case"
    r"|procédure|en fait|en droit|les circonstances de l['’]espèce|le droit (?:et la pratique)?"
    r" internes? pertinents?|griefs?|sur la (?:violation alléguée|recevabilité)|sur le fond"
    r"|application de l['’]article 41 de la convention|dommage|frais et dépens"
    r"|intérêts moratoires|thèses des parties|appréciation de la cour|principes généraux"
    r"|postępowanie|fakty|prawo|okoliczności sprawy|właściwe prawo krajowe(?: i praktyka)?"
    r"|zarzuty?|dopuszczalność|meritum|zastosowanie artykułu 41 konwencji|szkoda"
    r"|koszty i wydatki|odsetki za zwłokę|ocena trybunału|zasady ogólne"
)

COURT_PATTERNS = (
    # nagłówek
    (
        "nagłówek orzeczenia",
        (
            r"(?:(?:first|second|third|fourth|fifth|former \w+) section|grand chamber|"
            r"(?:première|deuxième|troisième|quatrième|cinquième|ancienne \w+) section|"
            r"grande chambre|sekcja \w+|wielka izba|committee|comité|komitet)" + _END
        ),
    ),
    (
        "nagłówek orzeczenia",  # "Sprawa X przeciwko Polsce dotyczyła ..." (zdanie z kropką) zostaje
        (
            r"(?:case of|affaire|sprawa) [^.]{1,150}? "
            + _VS
            + r" [^.,;:]{1,80}?(?: \((?:no|n°|nr)\.? ?\d+\))?"
        ),
    ),
    (
        "nagłówek orzeczenia",
        (r"\((?:applications?|requêtes?|skargi?) (?:no|nos|n[o°]s?|nr)\.? .{1,300}\)" + _END),
    ),
    (
        "nagłówek orzeczenia",
        (
            r"(?:judgment|decision|arrêt|décision|wyrok|decyzja)(?: \(.{0,60}\))?|strasbourg"
            r"|strasburg|final|définitif|ostateczny|\d{1,2}(?:er)? \w+ \d{4}(?: r\.)?|"
            r"\d{2}/\d{2}/\d{4}" + _END
        ),
    ),
    (
        "nagłówek orzeczenia",  # żywa pagina: "LEHTINEN v. FINLAND (No. 2) JUDGMENT 11"
        (
            r".{1,150} " + _VS + r" .{1,150} (?:judgment|decision|arrêt|décision|wyrok|"
            r"decyzja)(?: \(.{0,40}\))?(?: [-–] .{0,60})?\s*\d*"
        ),
    ),
    (
        "nagłówek orzeczenia",
        (
            r"this (?:judgment|decision) (?:will become|has become|is) final.{0,200}"
            r"|it may be subject to editorial revision"
            + _END
            + r"|cet arrêt (?:deviendra|est devenu|est) définitif.{0,200}"
            r"|il peut subir des retouches de forme"
            + _END
            + r"|wyrok ten (?:stanie się|stał się|staje się|jest) ostateczny.{0,200}"
            r"|(?:może on )?podlega(?:ć)? (?:korekcie|zmianom) (?:wydawniczej|redakcyjnej"
            r"|redakcyjnym)" + _END
        ),
    ),
    # skład
    (
        "skład trybunału",
        r"(?:in the case of|en l['’]affaire|w sprawie) .{1,200}?" + _VS + r" .{1,150},",
    ),
    (
        "skład trybunału",
        (
            r"the european court of human rights(?: \(.{1,60}\))?,? sitting as (?:a|the) .{1,80}"
            r"composed of\s*:?|la cour européenne des droits de l['’]homme(?: \(.{1,60}\))?,? "
            r"siégeant en .{1,80}composée? de\s*:?|europejski trybunał praw człowieka"
            r"(?: \(.{1,60}\))?,? (?:zasiadając|obradując) jako .{1,80}w składzie\s*:?"
        ),
    ),
    (
        "skład trybunału",
        (
            r"(?:and |et |oraz )?(?:sir |mr |mrs |ms |m\. |mme |lord |dame |pan |pani )?"
            r".{1,60}?, (?:president|vice-president|judges?|ad hoc judge|section registrar|"
            r"deputy section registrar|deputy registrar|registrar|présidente?|juges?|"
            r"juge ad hoc|greffi(?:er|ère)(?: adjointe?)?(?: de section)?|prezes|"
            r"wiceprezes|sędziowie|sędzia(?: ad hoc)?|kanclerz(?: sekcji)?|"
            r"zastępca kanclerza(?: sekcji)?)" + _END
        ),
    ),
    ("skład trybunału", r"(?:sir|mr|mrs|ms|m\.|mme|lord|dame|pan|pani) [^,]{1,50},"),
    (
        "skład trybunału",
        (
            r"having deliberated in private on .{1,200}|having deliberated,? decides as "
            r"follows\s*:?|deliver(?:s|ed) the following (?:judgment|decision).{0,120}"
            r"|après en avoir délibéré.{1,200}|rend (?:l['’]arrêt|la décision) que voici.{0,120}"
            r"|po naradzie.{1,200}|wydaje (?:następujący wyrok|następującą decyzję).{0,120}"
        ),
    ),
    # procedura
    (
        "procedura",
        (
            r"(?:the case originated in an? applications?|à l['’]origine de l['’]affaire se "
            r"trouvent? une? (?:requêtes?)|sprawa (?:została zainicjowana|wywodzi się ze|"
            r"rozpoczęła się od)).{1,600}"
        ),
    ),
    (
        "procedura",
        (
            r"(?:the applicants? (?:was|were|is|are) represented by|the .{1,80}government "
            r"(?:\(.{1,40}\) )?(?:was|were) represented by|(?:le|la|les) requérant\w* "
            r"(?:a|ont) été représenté\w* par|le gouvernement .{1,80}a été représenté par|"
            r"skarżąc\w+ (?:był\w*|została?|zostali|była) reprezentowan\w* przez|"
            r"rząd .{1,80}(?:był reprezentowany|reprezentował)).{1,300}"
        ),
    ),
    (
        "procedura",
        (
            # wstęp decyzji; "Having regard to the foregoing, the Court ..." zostaje
            r"having regard to the (?:above |said )?(?:applications?|observations|reply|"
            r"declarations?|formal declarations?|comments?|decision|partial decision) .{1,300}"
            r"|vu (?:la|les) (?:requêtes?|observations) .{1,300}"
            r"|(?:mając na uwadze|uwzględniając) (?:powyższą )?skargę .{1,300}"
        ),
    ),
    # nagłówki części
    (
        "nagłówek części",
        r"(?:[IVX]{1,5}\.|[A-H]\.|\d{1,2}\.|\([a-z]\))?\s*(?:" + _HEADINGS + r")" + _END,
    ),
    # dopuszczalność, art. 41
    (
        "dopuszczalność",
        (
            r".{0,120}?(?:not manifestly ill-founded within the meaning of article 35|"
            r"pas manifestement mal fondée? au sens de l['’]article 35|nie jest w sposób "
            r"oczywisty nieuzasadnion\w+ w rozumieniu art(?:ykułu|\.) 35).{0,160}"
        ),
    ),
    (
        "dopuszczalność",
        (
            r".{0,80}?(?:(?:is|it is) not inadmissible on any other grounds|ne se heurte à "
            r"aucun autre motif d['’]irrecevabilité|nie jest niedopuszczaln\w+ z jakichkolwiek "
            r"innych (?:powodów|przyczyn)|(?:it|they|the complaints?) (?:must|should) "
            r"therefore be declared admissible|il convient donc de (?:le|la|les) déclarer "
            r"recevables?|musi (?:zatem|więc) zostać uznan\w+ za dopuszczaln\w+).{0,80}"
        ),
    ),
    (
        "art. 41",
        (
            r"article 41 of the convention provides\s*:?|l['’]article 41 de la convention "
            r"(?:dispose|est ainsi libellé)\s*:?|artykuł 41 konwencji stanowi\s*:?"
        ),
    ),
    # sentencja
    (
        "sentencja",
        (
            r"(?:for these reasons|par ces motifs|z tych przyczyn),? (?:the court|la cour|"
            r"trybunał).{0,80}"
        ),
    ),
    (
        "sentencja",
        (
            r"(?:\d{1,2}\.\s*)?(?:holds|declares|dismisses|decides|rejects|joins|strikes|"
            r"dit|déclare|rejette|décide|joint|raye|stwierdza|uznaje|oddala|odrzuca|"
            r"postanawia|orzeka|skreśla)\b(?! (?:się|on|ona)\b).{0,500}"
        ),
    ),
    (
        "sentencja",
        (
            r"\(?[a-z]\)\s*(?:that the respondent state|que l['’]état défendeur|że pozwane "
            r"państwo).{0,500}|\(?[ivx]{1,4}\)\s*(?:eur|euro|€|gbp|chf|pln|usd|any tax|"
            r"tout montant|wszelkie podatki|wszelkich podatków).{0,300}"
        ),
    ),
    (
        "sentencja",
        (
            r"(?:done|fait|sporządzono) (?:in|en|w języku) (?:english|french|anglais|"
            r"français|angielskim|francuskim).{0,300}"
        ),
    ),
    (
        "sentencja",
        (
            r"(?:in accordance with article 45 § 2 of the convention|conformément aux articles?"
            r" 45 § 2 de la convention|zgodnie z art(?:ykułem|\.) 45 § 2 konwencji).{0,300}"
        ),
    ),
    (
        "sentencja",
        (
            r"(?:\S+ ){0,6}(?:deputy |section )*(?:registrar|greffi(?:er|ère)(?: adjointe?)?|"
            r"kanclerz)\s+(?:president|présidente?|prezes|przewodnicząc\w*)"
        ),
    ),
)

BRIEF_PATTERNS = (
    (
        "nagłówek: adresat",
        (
            r"(?:to the )?(?:european court of human rights|cour européenne des droits de "
            r"l['’]homme|europejski trybunał praw człowieka)(?:,? .{0,120})?|council of "
            r"europe.{0,120}|conseil de l['’]europe.{0,120}|rada europy.{0,120}|"
            r"(?:f-)?67075 strasbourg.{0,80}"
        ),
    ),
    (
        "nagłówek: rodzaj pisma",
        (
            r"(?:application form|formulaire de requête|formularz skargi|skarga|application|"
            r"requête|observations?|uwagi|stanowisko|odpowiedź)(?: (?:against|contre|"
            r"przeciwko|nr|no\.?|n°) .{0,120})?" + _END
        ),
    ),
    (
        "nagłówek: strony",
        (
            r"(?:applicants?|requérant\w*|skarżąc\w*|representatives?|représentant\w*|"
            r"pełnomocnik\w*|respondent states?|état défendeur|państwo pozwane|"
            r"(?:application|requête|skarga) (?:no|n°|nr)\.?)\s*:.{0,200}"
        ),
    ),
    (
        "zakończenie, podpis",
        (
            r"(?:yours (?:faithfully|sincerely)|respectfully|veuillez agréer.{0,200}|"
            r"signature\w*|declaration and signature|list of (?:accompanying )?documents|"
            r"liste des documents|lista (?:załączonych )?dokumentów)" + _END
        ),
    ),
    *LETTER_PATTERNS,
)


class EtpcFormulas(FormulaCorpus):
    file = FILE
    source = "teksty orzeczeń ETPC z HUDOC (wszystkie wersje językowe), losowa próbka"
    court_patterns = COURT_PATTERNS
    brief_patterns = BRIEF_PATTERNS

    def sample_texts(self, size, rng):
        with self.conn.cursor() as cur:
            cur.execute("SELECT MIN(id), MAX(id) FROM ruling_documents")
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
                cur.execute("SELECT body FROM ruling_documents WHERE id IN %s", (ids,))
                for (body,) in cur.fetchall():
                    if given >= size:
                        return
                    given += 1
                    yield body
