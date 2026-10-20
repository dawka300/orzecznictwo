"""etpc/weights.py: tiers A-D with their reasons."""

from etpc.weights import weigh


def test_hudoc_key_cases_and_grand_chamber_judgments_are_key():
    assert weigh("judgment", "CHAMBER", 1, 0) == ("A", "kluczowe w HUDOC")
    assert weigh("decision", "ADMISSIBILITY", 1, 3) == ("A", "kluczowe w HUDOC; cytowania: 3")
    assert weigh("judgment", "GRANDCHAMBER", 2, 418) == (
        "A",
        "wysoka ważność w HUDOC; Wielka Izba; cytowania: 418",
    )


def test_citations_make_a_judgment_key_but_a_decision_only_important():
    assert weigh("judgment", "CHAMBER", 4, 100)[0] == "A"
    assert weigh("judgment", "CHAMBER", 4, 99)[0] == "B"
    # Josipović: rutynowa decyzja cytowana w formułach tysiące razy
    assert weigh("decision", "ADMISSIBILITY", 4, 2449)[0] == "B"
    assert weigh("decision", "DECGRANDCHAMBER", 4, 0) == ("B", "Wielka Izba")


def test_ordinary_and_routine():
    assert weigh("judgment", "CHAMBER", 3, 0) == ("C", "średnia ważność w HUDOC")
    assert weigh("judgment", "COMMITTEE", 4, 5)[0] == "C"
    assert weigh("judgment", "COMMITTEE", 4, 4) == ("D", "cytowania: 4")
    assert weigh("decision", "ADMISSIBILITY", None, 0) == ("D", "bez wyróżnień")
