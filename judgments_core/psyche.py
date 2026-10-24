"""The psychological layer of the case folder: emotions (Plutchik's eight), cognitive
distortions (as in cognitive therapy) and the cognitive style of a text.

Emotions and distortions are recognised by meaning, without a trained classifier: each
category is the mean vector of Polish example sentences, a paragraph's score is its
cosine with the category minus its cosine with neutral, factual sentences. Scores are
compared within one folder only (the top tenth of all paragraphs counts as "marked"),
so they say where a text is most emotional or most distorted relative to the rest, not
how much in absolute terms. The style (sentence length, subordinate clauses, modal verbs,
first person against impersonal forms) is counted from the text.

What the layer does not do, on purpose: personality (Big Five) and mental health
(depression, stress) - briefs are mostly written by lawyers in a fixed genre, the
models are English and trained on essays and forums, and inferring a person's health
is special-category data (GDPR art. 9). Signals for a reader, not a diagnosis.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass

from judgments_core.axes import dot, mean, unit

NEUTRAL = (
    "Decyzja została doręczona w dniu 12 marca 2024 r.",
    "Zgodnie z art. 52 § 1 p.p.s.a. skargę wnosi się w terminie trzydziestu dni.",
    "Do pisma dołączono kopię umowy oraz potwierdzenie przelewu.",
    "Organ wydał decyzję na podstawie art. 104 k.p.a.",
    "Postępowanie wszczęto z urzędu i prowadzono je przez sześć miesięcy.",
    "W aktach sprawy znajduje się protokół oględzin nieruchomości.",
    "Zaskarżona decyzja dotyczy podatku od nieruchomości za 2022 r.",
    "Wartość przedmiotu sporu wynosi 15 000 zł.",
    "Sąd rozpoznał sprawę na posiedzeniu niejawnym.",
    "Pełnomocnik strony złożył wniosek o sporządzenie uzasadnienia.",
)

# klucz -> (nazwa, rodzaj, zdania wzorcowe)
CATEGORIES = {
    "gniew": (
        "Gniew",
        "emocja",
        (
            "Jestem wściekły na to, jak potraktował mnie urząd.",
            "To bezczelność i arogancja, która doprowadza mnie do furii.",
            "Nie pozwolę dłużej tak się traktować.",
            "Oburza mnie lekceważenie moich praw.",
            "Działanie organu jest skandaliczne i niedopuszczalne.",
            "Mam dość tego bezprawia.",
            "Urzędnicy kpią sobie z obywateli.",
            "To jawna złośliwość wobec mojej osoby.",
        ),
    ),
    "strach": (
        "Strach",
        "emocja",
        (
            "Boję się, że stracę dom i całe oszczędności.",
            "Obawiam się, co stanie się z moją rodziną.",
            "Żyję w ciągłym lęku przed kolejnym pismem z urzędu.",
            "Przeraża mnie wizja egzekucji.",
            "Nie wiem, co mnie jeszcze czeka, i to mnie paraliżuje.",
            "Grozi mi utrata źródła utrzymania.",
            "Drżę na myśl o kolejnej kontroli.",
            "Czuję się zagrożony i bezbronny.",
        ),
    ),
    "smutek": (
        "Smutek",
        "emocja",
        (
            "Jest mi bardzo przykro i czuję się przygnębiony.",
            "Straciłem nadzieję, że ktoś mi pomoże.",
            "Ta sprawa odebrała mi radość życia.",
            "Żałuję, że w ogóle złożyłem ten wniosek.",
            "Czuję się samotny i opuszczony w tej walce.",
            "Po śmierci ojca wszystko się rozpadło.",
            "Jestem zmęczony i zrezygnowany.",
            "Nic już nie będzie takie jak dawniej.",
        ),
    ),
    "wstret": (
        "Wstręt, pogarda",
        "emocja",
        (
            "Takie postępowanie budzi moje obrzydzenie.",
            "To żałosne i godne pogardy zachowanie.",
            "Brzydzę się metodami stosowanymi przez stronę przeciwną.",
            "Argumenty przeciwnika są po prostu niepoważne i kompromitujące.",
            "Nie da się traktować serio tak nędznych wymówek.",
            "Postawa urzędnika jest odrażająca.",
            "To poniżej wszelkiej krytyki.",
            "Szkoda słów na komentowanie tych bzdur.",
        ),
    ),
    "zaskoczenie": (
        "Zaskoczenie",
        "emocja",
        (
            "Ze zdumieniem przyjąłem treść decyzji.",
            "Nie spodziewałem się takiego rozstrzygnięcia.",
            "Zaskakujące jest, że organ zmienił zdanie bez uzasadnienia.",
            "Byłem zszokowany, gdy dowiedziałem się o wszczęciu postępowania.",
            "Nagle, bez żadnego uprzedzenia, nałożono na mnie karę.",
            "Trudno uwierzyć, że coś takiego mogło się wydarzyć.",
            "Wbrew wcześniejszym zapewnieniom wydano decyzję odmowną.",
            "Ku mojemu zdziwieniu wniosek odrzucono.",
        ),
    ),
    "zaufanie": (
        "Zaufanie",
        "emocja",
        (
            "Ufam, że Sąd rzetelnie rozpozna sprawę.",
            "Wierzę w bezstronność i sprawiedliwość tego postępowania.",
            "Liczę na rzetelną ocenę zebranych dowodów.",
            "Polegam na fachowej wiedzy biegłego.",
            "Jestem przekonany, że organ działał w dobrej wierze.",
            "Dziękuję za dotychczasową współpracę i zrozumienie.",
            "Mam pełne zaufanie do mojego pełnomocnika.",
            "Strony dotrzymywały wzajemnych zobowiązań.",
        ),
    ),
    "oczekiwanie": (
        "Oczekiwanie",
        "emocja",
        (
            "Oczekuję rozpatrzenia sprawy w ustawowym terminie.",
            "Czekam na odpowiedź i dalsze kroki organu.",
            "Spodziewam się, że decyzja zostanie uchylona.",
            "Liczę, że w najbliższym czasie sprawa się wyjaśni.",
            "Planuję złożyć kolejny wniosek po zakończeniu postępowania.",
            "Zakładam, że organ przeprowadzi dodatkowe dowody.",
            "Wkrótce przedstawię dalsze dokumenty.",
            "Mam nadzieję na pozytywne rozstrzygnięcie.",
        ),
    ),
    "radosc": (
        "Radość, ulga",
        "emocja",
        (
            "Z ulgą przyjąłem wyrok uwzględniający skargę.",
            "Cieszę się, że sprawa wreszcie znalazła szczęśliwy finał.",
            "Jestem bardzo zadowolony z takiego rozstrzygnięcia.",
            "To dla mnie wielka radość i satysfakcja.",
            "Wreszcie mogę odetchnąć.",
            "Jestem wdzięczny za życzliwość i pomoc.",
            "Dobrze, że udało się porozumieć.",
            "To najlepsza wiadomość od wielu miesięcy.",
        ),
    ),
    "czarno_biale": (
        "Myślenie czarno-białe",
        "znieksztalcenie",
        (
            "Albo organ uchyli decyzję w całości, albo całe postępowanie jest fikcją.",
            "Wszystko, co zrobił urząd, jest złe, a ja zrobiłem wszystko dobrze.",
            "Nie ma żadnych odcieni, racja jest wyłącznie po mojej stronie.",
            "Każdy, kto się ze mną nie zgadza, działa w złej wierze.",
            "Jeśli nie wygram, to znaczy, że sądy w ogóle nie działają.",
            "Strona przeciwna nie ma ani jednego słusznego argumentu.",
            "Albo sprawiedliwość, albo totalne bezprawie.",
            "Decyzja jest w całości błędna od pierwszego do ostatniego zdania.",
        ),
    ),
    "katastrofizacja": (
        "Katastrofizacja",
        "znieksztalcenie",
        (
            "Ta decyzja oznacza dla mnie całkowitą ruinę i koniec wszystkiego.",
            "Jeśli przegram, stracę wszystko i nigdy się nie podniosę.",
            "To doprowadzi do nieodwracalnej katastrofy dla całej rodziny.",
            "Skutki będą tragiczne i nikt już nic nie naprawi.",
            "Najgorsze dopiero przed nami, czeka mnie upadek.",
            "Każdy kolejny dzień zwłoki przybliża nieuchronne nieszczęście.",
            "Ta kara zniszczy mi życie na zawsze.",
            "Nie przetrwam tego, jeśli sąd mi nie pomoże.",
        ),
    ),
    "uogolnianie": (
        "Nadmierne uogólnianie",
        "znieksztalcenie",
        (
            "Urzędnicy zawsze tak postępują wobec zwykłych ludzi.",
            "Nigdy żaden organ nie potraktował mnie uczciwie.",
            "Wszyscy w tym urzędzie są przeciwko mnie.",
            "Za każdym razem dzieje się dokładnie to samo.",
            "Sądy nigdy nie stają po stronie obywatela.",
            "Każde moje pismo jest ignorowane.",
            "Tak jest zawsze, kiedy ktoś domaga się swoich praw.",
            "Nikt nigdy nie odpowiada na moje argumenty.",
        ),
    ),
    "czytanie_mysli": (
        "Czytanie w myślach",
        "znieksztalcenie",
        (
            "Organ celowo chciał mnie skrzywdzić i dobrze o tym wiedział.",
            "Urzędnik od początku zamierzał wydać decyzję odmowną.",
            "Wiem, że strona przeciwna tylko czeka na mój błąd.",
            "Oni na pewno uważają mnie za oszusta.",
            "Biegły z góry przyjął tezę korzystną dla organu.",
            "Przeciwnik świadomie dąży do mojej ruiny.",
            "Sąd zapewne już zdecydował, zanim mnie wysłuchał.",
            "Celem kontroli było wyłącznie znalezienie pretekstu do kary.",
        ),
    ),
    "personalizacja": (
        "Personalizacja",
        "znieksztalcenie",
        (
            "Przepisy zmieniono specjalnie po to, żeby uderzyć we mnie.",
            "To wszystko dzieje się z mojego powodu.",
            "Urząd uwziął się właśnie na mnie.",
            "Tylko ja jestem tak traktowany, inni mają spokój.",
            "Decyzja jest osobistą zemstą urzędnika wobec mnie.",
            "To moja wina, że cała rodzina cierpi.",
            "Gdybym był kimś innym, sprawa wyglądałaby inaczej.",
            "Kontrola była wymierzona personalnie we mnie.",
        ),
    ),
    "etykietowanie": (
        "Etykietowanie",
        "znieksztalcenie",
        (
            "Urzędnik prowadzący sprawę to zwykły nieuk.",
            "Pełnomocnik strony przeciwnej to kłamca i manipulator.",
            "Organ to banda niekompetentnych biurokratów.",
            "Skarżący jest pieniaczem, który zasypuje urząd pismami.",
            "Sąsiad to notoryczny oszust.",
            "Biegły to amator bez żadnej wiedzy.",
            "Ten człowiek jest po prostu nieuczciwy.",
            "Mamy do czynienia z zawodowym naciągaczem.",
        ),
    ),
}
KINDS = {"emocja": "Emocje (Plutchik)", "znieksztalcenie": "Zniekształcenia poznawcze"}
MARKED_SHARE = 0.10  # górna dziesiąta akapitów teczki = „wyraźne”


@dataclass
class Category:
    key: str
    title: str
    kind: str
    vector: list


_cache = {}
_cache_lock = threading.Lock()


def build(embedder, categories=CATEGORIES):
    """Category centroids and the neutral centroid, embedded by the documents' model."""
    neutral = mean([unit(v) for v in embedder.embed(list(NEUTRAL))])
    out = []
    for key, (title, kind, texts) in categories.items():
        out.append(Category(key, title, kind, mean([unit(v) for v in embedder.embed(list(texts))])))
    return neutral, out


def cached(embedder, name):
    with _cache_lock:
        if name not in _cache:
            _cache[name] = build(embedder)
        return _cache[name]


def scores(vectors, neutral, categories):
    """[{category: cos(paragraph, category) - cos(paragraph, neutral)}] per vector."""
    out = []
    for v in vectors:
        u = unit(v)
        base = dot(u, neutral)
        out.append({c.key: dot(u, c.vector) - base for c in categories})
    return out


def thresholds(all_scores, categories, share=MARKED_SHARE):
    """Per category the score of the top `share` of the folder's paragraphs (at least
    above zero: closer to the category than to neutral sentences)."""
    out = {}
    for c in categories:
        values = sorted((s[c.key] for s in all_scores), reverse=True)
        cut = values[max(0, int(len(values) * share) - 1)] if values else 0.0
        out[c.key] = max(cut, 0.0)
    return out


# --- styl poznawczy (z tekstu) -----------------------------------------------------

_WORD = re.compile(r"[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]+")
_SENTENCE = re.compile(r"(?<=[.!?;])\s+")
_SUBORDINATE = re.compile(
    r"\b(?:który|która|które|którego|której|którym|których|że|żeby|aby|ponieważ|gdyż|"
    r"bo|jeżeli|jeśli|gdyby|chociaż|choć|mimo że|kiedy|gdy|skoro|zanim|dopóki)\b",
    re.IGNORECASE,
)
_NECESSITY = re.compile(
    r"\b(?:musi\w*|musz\w*|należ\w*|trzeba|powin\w*|zobowiązan\w*|obowiąz\w*|nie wolno)\b",
    re.IGNORECASE,
)
_POSSIBILITY = re.compile(
    r"\b(?:może|mogę|możemy|mogą|można|mógł\w*|mogł\w*|możliw\w*|ewentualn\w*|wolno)\b",
    re.IGNORECASE,
)
_FIRST_PERSON = re.compile(
    r"\b(?:ja|mnie|mi|mną|mój|moja|moje|mojego|mojej|moim|moich|my|nas|nam|nami|nasz\w*|"
    r"\w+(?:łem|łam|liśmy|łyśmy))\b",
    re.IGNORECASE,
)
_IMPERSONAL = re.compile(
    r"\b(?:\w+(?:no|to)|się|należy|można|trzeba|wskazuje się|uznaje się|zostało|zostały|został\w*)\b",
    re.IGNORECASE,
)


@dataclass
class Style:
    words: int
    sentences: int
    words_per_sentence: float
    subordinate_per_sentence: float  # spójniki zdań podrzędnych na zdanie
    necessity: float  # na 1000 słów
    possibility: float  # na 1000 słów
    first_person: float  # na 1000 słów
    impersonal: float  # na 1000 słów
    self_focus: float  # 1. osoba / (1. osoba + bezosobowe), 0-1


def style(text):
    """Cognitive style of a Polish text from its grammar (no lemmatiser: word forms)."""
    flat = " ".join(text.split())
    words = len(_WORD.findall(flat))
    sentences = [s for s in _SENTENCE.split(flat) if _WORD.search(s)]
    n = max(len(sentences), 1)

    def per_1000(pattern):
        return round(1000 * len(pattern.findall(flat)) / words, 1) if words else 0.0

    first, impersonal = len(_FIRST_PERSON.findall(flat)), len(_IMPERSONAL.findall(flat))
    return Style(
        words=words,
        sentences=len(sentences),
        words_per_sentence=round(words / n, 1),
        subordinate_per_sentence=round(len(_SUBORDINATE.findall(flat)) / n, 2),
        necessity=per_1000(_NECESSITY),
        possibility=per_1000(_POSSIBILITY),
        first_person=per_1000(_FIRST_PERSON),
        impersonal=per_1000(_IMPERSONAL),
        self_focus=round(first / (first + impersonal), 2) if first + impersonal else 0.0,
    )
