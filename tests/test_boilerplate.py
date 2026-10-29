"""Formulas: sentences repeated word for word in many judgments, dropped in the comparison."""

import time

from test_comparison import BRIEF, KeywordEmbedder, form

from judgments_core import boilerplate, comparison, folder
from judgments_core.settings import Settings
from judgments_core.web import Request

FORMULA = "Skarga nie zasługuje na uwzględnienie."


def test_sentences_do_not_end_after_abbreviations():
    line = (
        "Zgodnie z art. 145 § 1 pkt 1 lit. c p.p.s.a. sąd uchyla decyzję (Dz. U. z 2002r. poz. 1270)."
        " Skarga nie zasługuje na uwzględnienie. Dalej: 2) postanowienia."
    )
    assert boilerplate.sentences(line) == [
        "Zgodnie z art. 145 § 1 pkt 1 lit. c p.p.s.a. sąd uchyla decyzję (Dz. U. z 2002r. poz. 1270).",
        FORMULA,
        "Dalej:",
        "2) postanowienia.",
    ]


def test_normalise_masks_numbers_quotes_and_case():
    one = boilerplate.normalise("Zgodnie z art. 61 § 3 ustawy z dnia 30 sierpnia 2002 r. „Prawo”")
    two = boilerplate.normalise('zgodnie z art. 18 § 1 ustawy z dnia 1 sierpnia 2012 r. "Prawo"')
    assert one == two
    assert boilerplate.normalise("II SA/Rz 23/07") == ""  # sygnatura to nie zdanie
    assert boilerplate.normalise("Ad 1.") == ""


def test_counted_once_per_judgment_and_saved(tmp_path):
    texts = [
        f"{FORMULA} Sprawa {n} dotyczy czegoś innego niż pozostałe numer {chr(97 + n)}x. {FORMULA}"
        for n in range(4)
    ] + ["Skarga NIE zasługuje na uwzględnienie"]
    found, done = boilerplate.count_formulas(texts, min_docs=3)
    assert done == 5 and found == [(5, FORMULA)]  # raz na orzeczenie, wielkość liter bez znaczenia
    path = tmp_path / "formulki.txt"
    boilerplate.save(path, found, done, "test")
    formulas = boilerplate.Formulas.load(path)
    assert len(formulas) == 1 and formulas.sample == 5
    assert formulas.count("skarga nie zasługuje na uwzględnienie;") == 5
    assert not boilerplate.Formulas.load(tmp_path / "brak.txt")


def test_strip_keeps_lines_and_lists_what_was_dropped():
    formulas = boilerplate.Formulas({boilerplate.normalise(FORMULA): 900}, 1000)
    text = f"{FORMULA} Organ nie zbadał zarzutu przedawnienia.\n\n{FORMULA}\nDruga linia zostaje."
    stripped, dropped = formulas.strip(text)
    assert stripped == "Organ nie zbadał zarzutu przedawnienia.\n\nDruga linia zostaje."
    assert [(d.sentence, d.how, d.count) for d in dropped] == [(FORMULA, "lista", 900)] * 2


JUDGMENT = """Skarżąca zarzuciła naruszenie art. 74a p.p.s.a. oraz art. 45 Konstytucji.

Naczelny Sąd Administracyjny zważył, co następuje:

Skarga kasacyjna nie zasługuje na uwzględnienie. Zgodnie z art. 45 Konstytucji każdy ma prawo do sprawiedliwego i jawnego rozpatrzenia sprawy.

Doręczenie przez ePUAP było skuteczne, bo strona wniosła pismo elektronicznie w tej sprawie."""


def test_comparison_without_formulas(monkeypatch):
    monkeypatch.setattr(comparison, "choose_embedder", lambda settings, own: (own, "test"))
    pages = comparison.DocumentComparison(Settings(), KeywordEmbedder())
    sentences = ["Skarga kasacyjna nie zasługuje na uwzględnienie."]
    sentences.append(
        "Zgodnie z art. 45 Konstytucji każdy ma prawo do sprawiedliwego i jawnego rozpatrzenia sprawy."
    )
    pages.formulas = boilerplate.Formulas({boilerplate.normalise(s): 500 for s in sentences}, 1000)
    layout = lambda title, body: body

    def run(fields):
        path = dict(pages.handle(form(fields), layout).headers)["Location"]
        job = pages.get(path.rsplit("/", 1)[1])
        for _ in range(100):
            if job.status != "liczy":
                break
            time.sleep(0.05)
        assert job.status == "gotowe", job.error
        return job, pages.handle(Request({"PATH_INFO": path}), layout).body.decode()

    fields = {"tekst_pisma": BRIEF, "tekst_orzeczenia": JUDGMENT, "rozwazania": "1"}
    # bez filtra przytoczenie art. 45 Konstytucji "odpowiada" na zarzut konstytucyjny
    job, _ = run(fields)
    assert {m.number: m.status for m in job.matches} == {1: "odniesiono", 2: "odniesiono"}
    job, page = run({**fields, "formulki": "1"})
    assert {m.number: m.status for m in job.matches} == {1: "odniesiono", 2: "pominieto"}
    assert len(job.dropped["orzeczenie"]) == 2 and not job.dropped["pismo"]
    assert (
        "pominięte jako formułka (2: z listy 2)" in page
        and f"w 500 z {comparison.number(1000)} orzeczeń bazy" in page
    )
    assert "name=formulki" in pages.form()


def test_court_units_find_the_marker_before_cleaning():
    formulas = boilerplate.Formulas(
        {boilerplate.normalise("Naczelny Sąd Administracyjny zważył, co następuje:"): 9000}
    )
    parts, own = folder.court_units(JUDGMENT, lambda part: formulas.strip(part)[0])
    assert parts[own[0]].startswith("Skarga kasacyjna")  # rozważania znalezione mimo filtra
    assert not any("zważył" in p for p in parts)


COURT_FORMULAS = [
    "Skarga okazała się bezzasadna.",
    "W ocenie Sądu skarga kasacyjna nie zasługiwała na uwzględnienie.",
    "Skarga kasacyjna nie została oparta na usprawiedliwionych podstawach.",
    "Z tych względów skargę należało oddalić.",
    "Wojewódzki Sąd Administracyjny w Krakowie z w a ż y ł, co następuje:",
    (
        "Zgodnie z art. 1 § 1 i 2 ustawy z dnia 25 lipca 2002 r. - Prawo o ustroju sądów"
        " administracyjnych (Dz. U. z 2022 r. poz. 2492) sądy administracyjne sprawują wymiar"
        " sprawiedliwości przez kontrolę działalności administracji publicznej."
    ),
    (
        "Sąd rozstrzyga w granicach danej sprawy, nie będąc jednak związany zarzutami i wnioskami"
        " skargi oraz powołaną podstawą prawną (art. 134 § 1 p.p.s.a.)."
    ),
    (
        "W sprawie nie zachodzi żadna z przesłanek nieważności postępowania wymienionych w art. 183"
        " § 2 p.p.s.a."
    ),
    "O kosztach postępowania orzeczono na podstawie art. 200 i art. 205 § 2 p.p.s.a.",
    "Mając powyższe na uwadze, Sąd na podstawie art. 151 p.p.s.a. orzekł jak w sentencji.",
    "W odpowiedzi na skargę Komisja wniosła o jej oddalenie, podtrzymując stanowisko.",
]
COURT_ARGUMENTS = [
    "Skarga nie zasługuje na uwzględnienie, ponieważ komisja oceniła pracę zgodnie z kryteriami.",
    "Organ uznał, że wniosek nie zasługuje na uwzględnienie.",
    "Sąd nie podziela zarzutu naruszenia art. 7 k.p.a., bo organ zebrał cały materiał dowodowy.",
    "Doręczenie przez ePUAP było skuteczne, bo strona wniosła pismo elektronicznie.",
    "Sprawa została rozpoznana na posiedzeniu niejawnym, bo strona nie zażądała rozprawy.",
]
BRIEF_FORMULAS = [
    "Wojewódzki Sąd Administracyjny w Warszawie",
    "ul. Jasna 2/4, 00-013 Warszawa",
    "Skarżący: Jan Kowalski",
    "Sygn. akt: VI SA/Wa 123/25",
    "SKARGA",
    "Skarga na uchwałę Komisji Egzaminacyjnej II stopnia z dnia 3 lipca 2025 r. nr 12/2025",
    "Na podstawie art. 50 § 1 p.p.s.a. wnoszę skargę na uchwałę Komisji.",
    "Zaskarżonej uchwale zarzucam:",
    "Wnoszę o uchylenie zaskarżonej uchwały w całości oraz zasądzenie kosztów postępowania.",
    "Wnoszę o:",
    "1) uchylenie zaskarżonej uchwały w całości;",
    "2) zasądzenie od organu na rzecz skarżącego kosztów postępowania według norm przepisanych.",
    "Z poważaniem",
    "Warszawa, 12 sierpnia 2025 r.",
    "1. odpis skargi",
    "Od niniejszej uchwały przysługuje skarga do sądu administracyjnego w terminie 30 dni.",
]
BRIEF_ARGUMENTS = [
    "Zarzucam naruszenie art. 7 k.p.a. przez pominięcie notatki egzaminatora.",
    "Wnoszę o przeprowadzenie dowodu z notatki egzaminatora na okoliczność oceny pracy.",
    "Wojewódzki Sąd Administracyjny w Warszawie błędnie przyjął, że doręczenie było skuteczne.",
    "Uchylenie decyzji przez Komisję bez zbadania pracy narusza art. 138 § 2 k.p.a.",
    "Decyzja doręczona 5 maja 2023 r.",
]


def test_patterns_drop_formulas_and_keep_arguments():
    from nsa import formulas as nsa_formulas

    formulas = nsa_formulas.NsaFormulas.load()
    for sentence in COURT_FORMULAS:
        assert formulas.pattern(sentence, "court"), sentence
    for sentence in COURT_ARGUMENTS:
        assert not formulas.pattern(sentence, "court"), sentence
    for sentence in BRIEF_FORMULAS:
        assert formulas.pattern(sentence, "brief"), sentence
    for sentence in BRIEF_ARGUMENTS + COURT_FORMULAS[:1]:
        assert not formulas.pattern(sentence, "brief"), sentence
    text, dropped = formulas.strip("\n".join(BRIEF_FORMULAS + BRIEF_ARGUMENTS), "brief")
    assert text == "\n".join(BRIEF_ARGUMENTS)
    assert {d.how for d in dropped} == {"wzorzec"}


class TopicEmbedder:
    """Fake model: [costs, the complaint's merits, ePUAP]."""

    def embed(self, texts):
        return [
            [float("koszt" in t.lower()), float("skarga" in t.lower()), float("ePUAP" in t)]
            for t in texts
        ]


def test_similar_sentences_go_only_from_a_court():
    formulas = boilerplate.Formulas({boilerplate.normalise(FORMULA): 900}, 1000)
    formulas.examples = [FORMULA]
    text = "Skarga zatem nie mogła odnieść skutku w tej sprawie.\nDoręczenie przez ePUAP było skuteczne."
    stripped, dropped = formulas.strip(text, "court", TopicEmbedder())
    assert stripped == "Doręczenie przez ePUAP było skuteczne."
    assert dropped[0].how == "podobne" and dropped[0].label == FORMULA and dropped[0].score > 0.99
    assert formulas.strip(text, "brief", TopicEmbedder())[0] == text  # pismo: bez "podobne"


def test_brief_heading_lines():
    from nsa import formulas as nsa_formulas

    formulas = nsa_formulas.NsaFormulas.load()
    for line in (
        "J., dnia 10 marca 2016 r.",
        "na decyzję Ministra Infrastruktury z dnia [...] lutego 2016 r. w przedmiocie opłaty",
    ):
        assert formulas.pattern(line, "brief"), line
    assert not formulas.pattern("Na decyzję organ czekał ponad rok.", "brief")


JUDGMENT_HEADER = [
    "WYROK",
    "W IMIENIU RZECZYPOSPOLITEJ POLSKIEJ",
    "Dnia 10 listopada 2016 r.",
    "Sygn. akt VI SA/Wa 883/16",
    "Wojewódzki Sąd Administracyjny w Warszawie w składzie następującym:",
    "Przewodniczący Sędzia WSA Jakub Linkowski Sędziowie Sędzia WSA Sławomir Kozik",
    "Protokolant sekr. sąd. Katarzyna Zielińska",
    (
        "po rozpoznaniu na rozprawie w dniu 10 listopada 2016 r. sprawy ze skargi H. sp. z o.o."
        " na decyzję Ministra w przedmiocie opłaty oddala skargę"
    ),
    "1. uchyla zaskarżoną decyzję;",
    "U Z A S A D N I E N I E",
]
JUDGMENT_CONTENT = [
    "Stwierdza się, że kontrolerzy nie sprawdzili żadnego odbiornika.",
    "Po rozpoznaniu sprawy Sąd uznał, że kontrola była wadliwa, bo nie zbadano odbiorników.",
    "Sędziowski obowiązek wyjaśnienia sprawy nie został tu naruszony.",
]


def test_judgment_header_and_sentence_are_court_formulas():
    from nsa import formulas as nsa_formulas

    formulas = nsa_formulas.NsaFormulas.load()
    for line in JUDGMENT_HEADER:
        assert formulas.pattern(line, "court"), line
    for line in JUDGMENT_CONTENT:
        assert not formulas.pattern(line, "court"), line


def test_research_measures_the_text_without_formulas(monkeypatch):
    from test_folder import KeywordEmbedder as FolderEmbedder

    from nsa import formulas as nsa_formulas

    monkeypatch.setattr(folder, "choose_embedder", lambda settings, own: (own, "test"))
    from judgments_core import research

    pages = research.Research(Settings(), FolderEmbedder())
    pages.formulas = nsa_formulas.NsaFormulas.load()
    pages.formulas.examples = []  # bez "podobnych" (fałszywy model)
    text = (
        "\n".join(JUDGMENT_HEADER) + "\nSąd zważył, co następuje:\n" + "\n".join(JUDGMENT_CONTENT)
    )
    assert "name=formulki" in pages.form()
    layout = lambda title, body: body

    def run(fields):
        request = form_research({"sekcja_psychologia": "1", "dok1_tekst": text, **fields})
        path = dict(pages.handle(request, layout).headers)["Location"]
        job = pages.get(path.rsplit("/", 1)[1])
        for _ in range(200):
            if job.status != "liczy":
                break
            time.sleep(0.05)
        assert job.status == "gotowe", job.error
        return job, pages.handle(Request({"PATH_INFO": path}), layout).body.decode()

    job, page = run({"formulki": "1"})
    doc = job.documents[0]
    assert job.skip_formulas and len(doc.dropped) >= len(JUDGMENT_HEADER)
    assert "RZECZYPOSPOLITEJ" not in doc.clean_text and "odbiornika" in doc.clean_text
    assert "Pominięte formułki" in page
    rows = pages.handle(Request({"PATH_INFO": f"/badanie/{job.id}-dokumenty.csv"}), layout)
    head, row = rows.body.decode("utf-8-sig").splitlines()
    assert row.split(",")[head.split(",").index("pominiete_formulki")] == str(len(doc.dropped))
    job, page = run({})  # bez pola: tekst cały
    assert not job.skip_formulas and not job.documents[0].dropped
    assert "Pominięte formułki" not in page


def form_research(fields):
    from test_folder import form as folder_form

    return folder_form(fields, "/badanie")


def test_panel_and_sentence_patterns_leave_reasoning():
    from nsa import formulas as nsa_formulas

    formulas = nsa_formulas.NsaFormulas.load()
    for line in (
        "Referendarz sądowy zasadnie uznał, iż skarżący nie przedstawił wydatków.",
        "Zwraca uwagę wielowiekowa tradycja odrębności tej dziedziny prawa.",
    ):
        assert not formulas.pattern(line, "court"), line
    assert formulas.pattern("sędzia WSA (del.) sędzia NSA (przew.) sędzia NSA", "court")


ETPC_FORMULAS = [
    "FOURTH SECTION",
    "CASE OF LEHTINEN v. FINLAND (No. 2)",
    "(Application no. 41585/98)",
    "JUDGMENT",
    "STRASBOURG",
    "8 June 2006",
    (
        "This judgment will become final in the circumstances set out in Article 44 § 2 of the"
        " Convention."
    ),
    "It may be subject to editorial revision.",
    "LEHTINEN v. FINLAND (No. 2) JUDGMENT 11",
    "In the case of Lehtinen v. Finland (No. 2),",
    "The European Court of Human Rights (Fourth Section), sitting as a Chamber composed of:",
    "Sir Nicolas Bratza, President,",
    "Mr J. Casadevall,",
    "Mr J. Šikuta, judges,",
    "and Mr M. O’Boyle, Section Registrar,",
    "Having deliberated in private on 13 December 2005 and on 16 May 2006,",
    "Delivers the following judgment, which was adopted on the lastmentioned date:",
    "PROCEDURE",
    (
        "The Finnish Government (“the Government”) were represented by their Agent, Mr Arto"
        " Kosonen, Director in the Ministry for Foreign Affairs."
    ),
    "II. ALLEGED VIOLATION OF ARTICLE 6 § 1 OF THE CONVENTION",
    (
        "The Court notes that this complaint is not manifestly ill-founded within the meaning of"
        " Article 35 § 3 (a) of the Convention."
    ),
    "It must therefore be declared admissible.",
    "FOR THESE REASONS, THE COURT UNANIMOUSLY",
    "1. Holds that there has been a violation of Article 6 § 1 of the Convention;",
    "(i) EUR 1,500 (one thousand and five hundred euros) in respect of non-pecuniary damage;",
    (
        "Done in English, and notified in writing on 8 June 2006, pursuant to Rule 77 §§ 2 and 3"
        " of the Rules of Court."
    ),
    "AFFAIRE MORICE c. FRANCE",
    (
        "Cet arrêt deviendra définitif dans les conditions définies à l’article 44 § 2 de la"
        " Convention."
    ),
    "PAR CES MOTIFS, LA COUR, À L’UNANIMITÉ,",
    "Dit qu’il y a eu violation de l’article 10 de la Convention;",
    "SPRAWA KUDŁA przeciwko POLSCE",
    "Wyrok ten stanie się ostateczny zgodnie z warunkami określonymi w art. 44 § 2 Konwencji.",
]
ETPC_CONTENT = [
    "The Court holds that the length of the proceedings was excessive.",
    "Having regard to the foregoing, the Court considers that there has been a violation.",
    "The applicant complained that the proceedings had lasted too long.",
    "Sprawa Kudła przeciwko Polsce dotyczyła długości tymczasowego aresztowania.",
    "Stwierdza się, że skarżący nie wyczerpał środków krajowych.",
    "Mr Smith disagreed.",
]


def test_etpc_court_patterns():
    from etpc.formulas import EtpcFormulas

    formulas = EtpcFormulas.load()
    for line in ETPC_FORMULAS:
        assert formulas.pattern(line, "court"), line
    for line in ETPC_CONTENT:
        assert not formulas.pattern(line, "court"), line
    for line in ("European Court of Human Rights", "Skarżący: Jan Kowalski", "Z poważaniem"):
        assert formulas.pattern(line, "brief"), line
    assert not formulas.pattern("Skarga do Trybunału jest dopuszczalna.", "brief")


def test_kind_of_a_text():
    k = boilerplate.Formulas.kind_of
    assert k("Fakty...\nTHE LAW\nThe Court ...") == "court"
    assert k("Sąd zważył, co następuje: ...") == "court"
    assert k("Fakty.\nPRAWO\nTrybunał ...") == "court"
    assert k("Zarzucam naruszenie art. 7 k.p.a.") == "brief"
    assert k("Sąd zważył, co następuje", role="skarzacy") == "brief"


def test_document_analysis_searches_without_formulas(monkeypatch):
    from test_analysis import FakeConnection, FakeRetriever, multipart, request

    from judgments_core import analysis
    from nsa.chunker import NsaChunker
    from nsa.formulas import NsaFormulas

    monkeypatch.setattr(analysis, "lm_studio_has_model", lambda settings: False)
    retriever = FakeRetriever()
    pages = analysis.DocumentAnalysis(Settings(), retriever, NsaChunker(), FakeConnection, "model")
    pages.formulas = NsaFormulas.load()
    pages.formulas.examples = []  # bez "podobnych" (model to napis)
    assert "name=formulki" in pages.form()
    text = "\n".join(BRIEF_FORMULAS + BRIEF_ARGUMENTS)
    layout = lambda title, body: body

    def run(fields):
        body, content_type = multipart({"strona": "skarzacy", "tekst": text, **fields}, {})
        path = dict(pages.handle(request(body, content_type), layout).headers)["Location"]
        job = pages.get(path.rsplit("/", 1)[1])
        for _ in range(100):
            if job.status != "liczy":
                break
            time.sleep(0.05)
        assert job.status == "gotowe", job.error
        return job, pages.handle(Request({"PATH_INFO": path}), layout).body.decode()

    job, page = run({"formulki": "1"})
    assert retriever.calls[-1][0] == "\n".join(BRIEF_ARGUMENTS)  # szukane bez formułek
    assert job.formula_kind == "brief" and len(job.dropped) == len(BRIEF_FORMULAS)
    assert "Pominięte formułki" in page and "pominięte jako formułka" in page
    job, page = run({})
    assert retriever.calls[-1][0] == text and "Formułki nie były pomijane" in page


def test_document_pages_share_the_formulas_and_route():
    from judgments_core.documents import DocumentPages

    formulas = boilerplate.Formulas({boilerplate.normalise(FORMULA): 1}, 10)
    pages = DocumentPages(Settings(), None, None, None, None, formulas)
    assert all(tab.formulas is formulas for tab in pages.tabs())
    assert pages.library.case_folder is pages.folder
    layout = lambda title, body: body
    assert (
        "Porównanie dokumentów"
        in pages.route(Request({"PATH_INFO": "/porownanie"}), layout).body.decode()
    )
    assert "name=formulki" in pages.route(Request({"PATH_INFO": "/badanie"}), layout).body.decode()
    assert pages.route(Request({"PATH_INFO": "/orzeczenie/1"}), layout) is None
