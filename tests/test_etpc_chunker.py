from etpc.chunker import (
    MAX_CHARS,
    chunk_document,
    embedding_input,
    heading_level,
    section_of,
    split_long,
)

PARAGRAPH = "The applicant complained that the length of the proceedings had been excessive. " * 8

JUDGMENT = "\n".join(
    [
        "FIRST SECTION",
        "CASE OF KOWALSKI v. POLAND",
        "(Application no. 12345/20)",
        "JUDGMENT",
        "Andreas Zünd, President,",
        "INTRODUCTION",
        "1.  The case concerns the length of civil proceedings.",
        "THE FACTS",
        f"2.  {PARAGRAPH}",
        "KOWALSKI v. POLAND JUDGMENT",
        "3",
        f"3.  {PARAGRAPH}",
        "THE LAW",
        "I. ALLEGED VIOLATION OF ARTICLE 6 § 1 OF THE CONVENTION",
        "A. Admissibility",
        f"4.  {PARAGRAPH}",
        "B. Merits",
        "1. The parties’ submissions",
        f"5.  {PARAGRAPH}",
        "2. The Court’s assessment",
        "(a) General principles",
        f"6.  {PARAGRAPH}",
        f"7.  {PARAGRAPH}",
        "FOR THESE REASONS, THE COURT, UNANIMOUSLY,",
        "1. Declares the application admissible;",
        "2. Holds that there has been a violation of Article 6 § 1 of the Convention;",
        "CONCURRING OPINION OF JUDGE ZÜND",
        f"1.  {PARAGRAPH}",
        "APPENDIX",
        "List of applicants",
    ]
)


def chunks(body=JUDGMENT, title="CASE OF KOWALSKI v. POLAND"):
    return chunk_document(7, body, title, meta={"language": "ENG"})


def test_sections_and_skipped_parts():
    result = chunks()
    assert [c.part for c in result] == [
        "wprowadzenie",
        "fakty",
        "prawo",
        "prawo",
        "prawo",
        "sentencja",
        "opinia",
    ]
    assert [c.number for c in result] == list(range(len(result)))
    assert all(c.judgment_id == "7" and c.meta["language"] == "ENG" for c in result)
    text = "\n".join(c.text for c in result)
    assert "Andreas Zünd" not in text  # nagłówek pominięty
    assert "List of applicants" not in text  # załącznik pominięty
    assert "KOWALSKI v. POLAND JUDGMENT" not in text and "\n3\n" not in text  # główka, numer strony


def test_start_points_into_body():
    for c in chunks():
        assert JUDGMENT[c.start :].startswith(c.text.split("\n")[0])


def test_heading_path_in_context():
    by_text = {c.text.split("\n")[0]: c for c in chunks()}
    merits = by_text["B. Merits"]
    assert merits.meta["context"] == (
        "CASE OF KOWALSKI v. POLAND | THE LAW | I. ALLEGED VIOLATION OF ARTICLE 6 § 1 OF THE CONVENTION"
        " > B. Merits > 1. The parties’ submissions"
    )
    assessment = by_text["2. The Court’s assessment"]
    assert assessment.meta["context"].endswith(
        "> B. Merits > 2. The Court’s assessment > (a) General principles"
    )
    assert embedding_input(assessment).startswith(
        assessment.meta["context"] + "\n\n2. The Court’s assessment"
    )


def test_new_chunk_at_heading_only_when_long_enough():
    first = chunks()[2]
    # "THE LAW", "I. ..." i "A. ..." są krótkie: zostają w jednym fragmencie z akapitem 4.
    assert first.text.startswith("THE LAW\nI. ALLEGED VIOLATION")
    assert "4.  The applicant" in first.text


def test_sizes_are_bounded():
    body = "THE FACTS\n" + "\n".join(f"{i}.  {PARAGRAPH}" for i in range(1, 40))
    result = chunks(body)
    assert len(result) > 5
    assert all(len(c.text) <= MAX_CHARS for c in result)
    assert sum(len(c.text) for c in result) >= sum(len(line.strip()) for line in body.split("\n"))


def test_long_paragraph_split_at_sentences():
    long = "This sentence is about Article 8. " * 300
    pieces = split_long(long.strip())
    assert len(pieces) > 2
    assert all(len(p) <= MAX_CHARS and p.endswith("Article 8.") for p in pieces)
    assert all(len(p) <= MAX_CHARS for p in split_long("x" * 10000))


def test_french_sections():
    assert section_of("EN FAIT") == "fakty"
    assert section_of("EN DROIT") == "prawo"
    assert section_of("LE CADRE JURIDIQUE ET LA PRATIQUE INTERNES PERTINENTS") == "prawo_krajowe"
    assert section_of("PAR CES MOTIFS, LA COUR, À L’UNANIMITÉ,") == "sentencja"
    assert (
        section_of("OPINION CONCORDANTE COMMUNE AUX JUGES MOUROU-VIKSTRÖM ET GUYOMAR") == "opinia"
    )
    assert section_of("ANNEXE") == "zalacznik"
    assert section_of("For these reasons, the Court, unanimously,") == "sentencja"
    assert section_of("The law") is None  # sekcje tylko wielkimi literami
    assert heading_level("a) \xa0Principes généraux") == 4


def test_headings_versus_paragraphs():
    assert heading_level("1. The parties’ submissions", "5.  The Government") == 3
    assert heading_level("(ii) Compensatory remedy") == 5
    assert heading_level("(β) Burden of proof") == 6
    assert heading_level("4.  The facts of the case may be summarised as follows") is None
    assert heading_level("2. Non-pecuniary damage shall include:") is None
    assert heading_level("11. decided to resume examination") is None
    # stary wyrok: akapit z jedną spacją, łamany w środku zdania
    assert (
        heading_level("26. The Government objected that D. Van Oosterwijck", "- had no ...") is None
    )
    assert heading_level("1. Thèses des parties", "a) Les requérants") == 3


def test_document_without_sections():
    body = "Kennedy LINDSAY against the United Kingdom\n" + "\n".join([PARAGRAPH] * 10)
    result = chunks(body)
    assert {c.part for c in result} == {"tekst"}
    assert result[0].text.startswith("Kennedy LINDSAY")


def test_long_header_kept():
    body = "\n".join([PARAGRAPH] * 10) + "\nTHE LAW\n" + PARAGRAPH
    assert [c.part for c in chunks(body)][:1] == ["poczatek"]
