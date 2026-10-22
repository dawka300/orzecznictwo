"""Language markers of a Polish text (the psychological layer of the document analysis).

Counted locally, no model: word lists after the psycholinguistic tradition (function
words, Pennebaker/LIWC) and pragmatics (epistemic certainty, necessity, absolutes,
hiding the agent). Each marker gives its rate per 1000 words and the sentences it
appears in - signals for a reader (or for Claude, when connected), not a diagnosis.
Polish inflection is handled by stems (prefix match) where a word inflects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# nazwa -> (opis, co może znaczyć, słowa; "*" na końcu = początek słowa)
MARKERS = {
    "pewnosc": (
        "Pewność deklarowana",
        "„oczywiste” bez dowodu - bywa w najsłabszych punktach argumentacji",
        (
            "oczywiście",
            "oczywist*",
            "niewątpliwie",
            "bezsprzecznie",
            "bezspornie",
            "niewątpliw*",
            "bezsporn*",
            "jak wiadomo",
            "powszechnie wiadomo",
            "nie ulega wątpliwości",
            "z całą pewnością",
            "bez wątpienia",
            "jasne jest",
            "jednoznacznie",
        ),
    ),
    "przymus": (
        "Przymus i powinność",
        "„musi / należy” - poczucie przymusu z zewnątrz albo moralizowanie",
        (
            "musi*",
            "muszą",
            "należy",
            "należało",
            "trzeba",
            "powinien",
            "powinn*",
            "obowiąz*",
            "zobowiąz*",
        ),
    ),
    "absolut": (
        "Kwantyfikatory absolutne",
        "„zawsze / nigdy / wszyscy” - generalizacje, myślenie czarno-białe",
        (
            "zawsze",
            "nigdy",
            "wszyscy",
            "wszystkie",
            "wszystko",
            "każdy",
            "każda",
            "każde",
            "nikt",
            "nic",
            "żaden",
            "żadna",
            "żadne",
            "całkowicie",
            "absolutnie",
            "zupełnie",
        ),
    ),
    "wyjatki": (
        "Wyjątki i zastrzeżenia",
        "„jednak / z wyjątkiem” - złożoność i precyzja myślenia (przeciwwaga absolutów)",
        (
            "jednak",
            "jednakże",
            "ale",
            "lecz",
            "wyjątk*",
            "chyba że",
            "o ile",
            "natomiast",
            "aczkolwiek",
        ),
    ),
    "ja": (
        "Pierwsza osoba (ja)",
        "„ja / mnie / mój” - skupienie na sobie, osobiste zaangażowanie",
        ("ja", "mnie", "mi", "mój", "moja", "moje", "moim", "mojej", "moich", "sam", "sama"),
    ),
    "my": (
        "Pierwsza osoba (my)",
        "„my / nasz” - budowanie wspólnoty albo rozmywanie odpowiedzialności",
        ("my", "nas", "nam", "nasz", "nasza", "nasze", "naszym", "naszej", "naszych"),
    ),
    "bezosobowe": (
        "Bezosobowość i strona bierna",
        "„zostało ustalone / dokonano” - ukryty sprawca, przeniesienie odpowiedzialności",
        (
            "zostało",
            "została",
            "został",
            "zostały",
            "zostali",
            "dokonano",
            "uznano",
            "ustalono",
            "stwierdzono",
            "przyjęto",
            "wskazano",
            "uczyniono",
        ),
    ),
    "emocje": (
        "Słownictwo nacechowane",
        "oceny i emocje - legitymizacja lub delegitymizacja stron (rama moralna)",
        (
            "rażąc*",
            "skandal*",
            "bezprawn*",
            "absurd*",
            "krzywd*",
            "niesprawiedliw*",
            "oburz*",
            "kuriozal*",
            "arbitraln*",
            "szykan*",
            "nadużyc*",
        ),
    ),
}
# Te same znaczniki dla tekstów angielskich (wyroki ETPC); opisy jak w MARKERS.
WORDS_EN = {
    "pewnosc": (
        "obviously",
        "clearly",
        "undoubtedly",
        "indisputabl*",
        "unquestionabl*",
        "of course",
        "it is well known",
        "it is evident",
        "beyond doubt",
        "self-evident",
        "manifestly",
    ),
    "przymus": ("must", "have to", "has to", "should", "shall", "obliged", "required", "necessar*"),
    "absolut": (
        "always",
        "never",
        "all",
        "everyone",
        "everything",
        "every",
        "nobody",
        "nothing",
        "none",
        "entirely",
        "absolutely",
        "completely",
        "totally",
    ),
    "wyjatki": (
        "however",
        "but",
        "except*",
        "unless",
        "although",
        "whereas",
        "nevertheless",
        "provided that",
    ),
    "ja": ("i", "me", "my", "mine", "myself"),
    "my": ("we", "us", "our", "ours", "ourselves"),
    "bezosobowe": (
        "was found",
        "it was held",
        "was established",
        "were found",
        "it has been",
        "was decided",
    ),
    "emocje": (
        "arbitrar*",
        "outrageous",
        "absurd*",
        "unjust*",
        "flagrant*",
        "egregious",
        "abusiv*",
        "humiliat*",
        "unlawful*",
    ),
}
_PL_LETTERS = re.compile(r"[ąćęłńóśźż]")
_PL_HINTS = {"i", "w", "z", "na", "się", "nie", "że", "do", "jest", "oraz", "przez", "sąd"}
_EN_HINTS = {"the", "of", "and", "to", "in", "is", "that", "court", "was", "which"}
_WORD = re.compile(r"[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]+")
_SENTENCE = re.compile(r"(?<=[.!?;])\s+")
EXAMPLES = 5  # zdań na znacznik w raporcie


@dataclass
class Marker:
    key: str
    title: str
    meaning: str
    count: int = 0
    per_1000: float = 0.0
    found: dict = field(default_factory=dict)  # słowo -> ile razy
    sentences: list = field(default_factory=list)


@dataclass
class Markers:
    words: int
    sentences: int
    markers: list
    lang: str = "pl"


def _compile(words):
    single, phrases = [], []
    for w in words:
        if " " in w:
            phrases.append(w)
        elif w.endswith("*"):
            single.append(re.escape(w[:-1]) + r"[a-ząćęłńóśźż]*")
        else:
            single.append(re.escape(w))
    parts = [rf"\b(?:{'|'.join(single)})\b"] if single else []
    parts += [rf"\b{re.escape(p)}\b" for p in phrases]
    return re.compile("|".join(parts), re.IGNORECASE)


_PATTERNS = {
    "pl": {key: _compile(words) for key, (_, _, words) in MARKERS.items()},
    "en": {key: _compile(words) for key, words in WORDS_EN.items()},
}


def language(text):
    """ "pl" or "en" by the most common function words (other languages: "pl" lists anyway)."""
    words = [w.lower() for w in _WORD.findall(text[:20000])]
    # Polskie znaki to najpewniejszy sygnał ("to", "i" są w obu językach).
    pl = sum(w in _PL_HINTS for w in words) + 2 * sum(bool(_PL_LETTERS.search(w)) for w in words)
    en = sum(w in _EN_HINTS for w in words)
    return "en" if en > pl else "pl"


def analyse(text, lang=None):
    """Markers of a text: per marker the count, the rate per 1000 words, the words
    found and up to EXAMPLES sentences (the ones with the most hits first). lang: "pl"
    or "en" (default: recognised from the text)."""
    lang = lang or language(text)
    patterns = _PATTERNS[lang]
    words = len(_WORD.findall(text))
    sentences = [s.strip() for s in _SENTENCE.split(" ".join(text.split())) if s.strip()]
    out = []
    for key, (title, meaning, _) in MARKERS.items():
        pattern = patterns[key]
        marker = Marker(key, title, meaning)
        scored = []
        for sentence in sentences:
            hits = [m.group(0).lower() for m in pattern.finditer(sentence)]
            if hits:
                marker.count += len(hits)
                for hit in hits:
                    marker.found[hit] = marker.found.get(hit, 0) + 1
                scored.append((len(hits), sentence))
        marker.per_1000 = round(1000 * marker.count / words, 1) if words else 0.0
        marker.sentences = [s for _, s in sorted(scored, key=lambda x: -x[0])[:EXAMPLES]]
        out.append(marker)
    return Markers(words, len(sentences), out, lang)
