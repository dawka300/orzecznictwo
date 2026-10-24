"""Semantic axes: where a paragraph leans between two poles (agency, conflict, closure,
emotion), measured on the embedding vectors instead of asking a chat model.

An axis is the difference of the mean vectors of two sets of example sentences (pole
"+" minus pole "-"), normalised. A paragraph's score is the cosine between its vector,
centred on the mean of the analysed documents (so the shared topic of the case cancels
out), and the axis. The scores are small (about ±0.2) and only comparable within one
analysis: paragraph to paragraph, document to document. Signals to read with the text,
not a diagnosis; the poles are a first version to calibrate on real briefs.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass

# klucz -> (nazwa, biegun -, biegun +, zdania bieguna -, zdania bieguna +)
AXES = {
    "sprawczosc": (
        "Sprawczość",
        "bierność, przymus z zewnątrz",
        "decyzja, odpowiedzialność",
        (
            "Nie miałem żadnego wyboru, sytuacja mnie do tego zmusiła.",
            "To przez działania organu znalazłem się w tym położeniu.",
            "Byłem zdany na łaskę urzędników i nic nie mogłem zrobić.",
            "Okoliczności niezależne ode mnie uniemożliwiły dotrzymanie terminu.",
            "Zostałem postawiony przed faktem dokonanym.",
            "Nikt mnie nie poinformował, więc nie mogłem zareagować.",
            "Sprawy potoczyły się same, bez mojego udziału.",
            "Byłem bezradny wobec decyzji, które zapadały ponad moją głową.",
            "Musiałem się podporządkować, bo inaczej straciłbym wszystko.",
            "To nie moja wina, zawiódł system.",
        ),
        (
            "Podjąłem decyzję i biorę za nią pełną odpowiedzialność.",
            "Zaplanowałem kolejne kroki i konsekwentnie je realizuję.",
            "Złożyłem wniosek w terminie i dołączyłem wszystkie dokumenty.",
            "Sam zadbałem o zebranie dowodów w tej sprawie.",
            "Wybrałem tę drogę świadomie, znając jej skutki.",
            "Wdrożyłem rozwiązanie, które usunęło problem.",
            "Organ samodzielnie ustalił stan faktyczny i rozstrzygnął sprawę.",
            "Działam w swoim imieniu i na własny rachunek.",
            "Przejąłem inicjatywę i zaproponowałem konkretne rozwiązanie.",
            "Odpowiadam za swoje działania i ich skutki.",
        ),
    ),
    "konflikt": (
        "Kooperacja - eskalacja",
        "porozumienie, kompromis",
        "roszczenie, ultimatum, wina",
        (
            "Proponuję polubowne rozwiązanie sporu z korzyścią dla obu stron.",
            "Jesteśmy otwarci na rozmowy i szukanie kompromisu.",
            "Zależy nam na wspólnym wypracowaniu rozwiązania.",
            "Rozumiemy stanowisko drugiej strony i chcemy je uwzględnić.",
            "Możemy spotkać się w połowie drogi.",
            "Wnoszę o mediację i ugodowe zakończenie sprawy.",
            "Doceniam dotychczasową współpracę i chcę ją kontynuować.",
            "Wspólnym celem jest szybkie i sprawiedliwe załatwienie sprawy.",
            "Jestem gotów ustąpić w kwestii terminu płatności.",
            "Dziękuję za wyjaśnienia, przyjmuję je.",
        ),
        (
            "Żądam natychmiastowej zapłaty pod rygorem skierowania sprawy na drogę sądową.",
            "Jeżeli nie spełnicie żądania w ciągu siedmiu dni, poniesiecie konsekwencje.",
            "Cała wina leży wyłącznie po stronie przeciwnika.",
            "Działanie organu było rażąco bezprawne i świadczy o złej woli.",
            "Nie zgadzam się na żadne ustępstwa.",
            "Zawiadomię prokuraturę o popełnieniu przestępstwa.",
            "Strona przeciwna świadomie wprowadza sąd w błąd.",
            "Domagam się ukarania winnych i pełnego odszkodowania.",
            "To jest ostatnie wezwanie przed podjęciem dalszych kroków.",
            "Skarżący nadużywa prawa i działa wyłącznie na szkodę organu.",
        ),
    ),
    "domkniecie": (
        "Otwartość - domknięcie",
        "być może, zależnie od, rozważmy",
        "zawsze, nigdy, bezdyskusyjnie",
        (
            "Być może istnieje inne wytłumaczenie tej sytuacji.",
            "Zależy to od tego, jak ocenimy zebrane dowody.",
            "Warto rozważyć również odmienne stanowisko.",
            "Nie można wykluczyć, że organ popełnił błąd, ale wymaga to sprawdzenia.",
            "W orzecznictwie występują w tej kwestii rozbieżne poglądy.",
            "Wydaje się, że przepis można rozumieć na dwa sposoby.",
            "Należałoby jeszcze zbadać okoliczności towarzyszące.",
            "Przyjmuję, że mogę się mylić w tej ocenie.",
            "Odpowiedź nie jest jednoznaczna i zależy od szczegółów.",
            "Proszę o rozważenie wszystkich możliwych interpretacji.",
        ),
        (
            "Jest to oczywiste i nie wymaga żadnego dowodu.",
            "Organ zawsze postępuje w ten sam, wadliwy sposób.",
            "Nie ulega najmniejszej wątpliwości, że skarga jest zasadna.",
            "Nigdy nie było i nie będzie innej możliwej interpretacji.",
            "Bezsprzecznie i bezdyskusyjnie rację ma wyłącznie skarżący.",
            "Każdy rozsądny człowiek musi to przyznać.",
            "Stanowisko przeciwne jest całkowicie absurdalne.",
            "Jedyną możliwą konsekwencją jest uchylenie decyzji.",
            "Przepis jest jasny i nie podlega żadnej wykładni.",
            "Wszystkie zarzuty strony przeciwnej są w całości chybione.",
        ),
    ),
    "emocje": (
        "Rzeczowość - emocje",
        "fakty, daty, przepisy",
        "przeżycia, krzywda, oburzenie",
        (
            "Decyzja została doręczona w dniu 12 marca 2024 r.",
            "Zgodnie z art. 52 § 1 p.p.s.a. skargę wnosi się w terminie trzydziestu dni.",
            "Wartość przedmiotu sporu wynosi 15 000 zł.",
            "Do pisma dołączono kopię umowy oraz potwierdzenie przelewu.",
            "Organ wydał decyzję na podstawie art. 104 k.p.a.",
            "Wniosek wpłynął do urzędu w dniu jego nadania.",
            "Postępowanie wszczęto z urzędu i prowadzono je przez sześć miesięcy.",
            "W aktach sprawy znajduje się protokół oględzin nieruchomości.",
            "Zaskarżona decyzja dotyczy podatku od nieruchomości za 2022 r.",
            "Termin upłynął w poniedziałek, dzień roboczy.",
        ),
        (
            "Czuję się głęboko skrzywdzony i upokorzony przez urzędników.",
            "Jestem oburzony takim traktowaniem obywatela.",
            "Ta sprawa niszczy mi życie i zdrowie mojej rodziny.",
            "Nie mogę spać po nocach z powodu tej niesprawiedliwości.",
            "To skandaliczne i haniebne zachowanie.",
            "Boję się, że stracę dach nad głową.",
            "Przeżywam ogromny stres i poczucie bezsilności.",
            "Jestem zrozpaczony, nikt mnie nie chce wysłuchać.",
            "To, co mnie spotkało, jest po prostu nieludzkie.",
            "Mam dość lekceważenia i arogancji ze strony urzędu.",
        ),
    ),
}


@dataclass
class Axis:
    key: str
    title: str
    minus: str
    plus: str
    vector: list


def unit(vector):
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector] if norm else list(vector)


def mean(vectors):
    n = len(vectors)
    return [sum(column) / n for column in zip(*vectors)]


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def build(embedder, axes=AXES):
    """Axes from the example sentences, embedded by the same model as the documents."""
    out = []
    for key, (title, minus, plus, minus_texts, plus_texts) in axes.items():
        vectors = embedder.embed(list(minus_texts) + list(plus_texts))
        minus_mean = mean([unit(v) for v in vectors[: len(minus_texts)]])
        plus_mean = mean([unit(v) for v in vectors[len(minus_texts) :]])
        out.append(
            Axis(key, title, minus, plus, unit([p - m for p, m in zip(plus_mean, minus_mean)]))
        )
    return out


_cache = {}
_cache_lock = threading.Lock()


def cached(embedder, name):
    """build() once per model (the examples are ~80 sentences)."""
    with _cache_lock:
        if name not in _cache:
            _cache[name] = build(embedder)
        return _cache[name]


def scores(vectors, axes):
    """[{axis key: score}] per vector: cosine of (vector - mean of all) with each axis."""
    units = [unit(v) for v in vectors]
    centre = mean(units)
    out = []
    for u in units:
        centred = unit([x - c for x, c in zip(u, centre)])
        out.append({a.key: dot(centred, a.vector) for a in axes})
    return out
