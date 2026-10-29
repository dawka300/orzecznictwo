"""NSA topics (python -m nsa tematy), the NSA child of TopicModel.

Level A = judgments: the mean of a judgment's chunks without the operative part and
the dissenting opinion (the operative part, "oddala skargę", would group by outcome,
not by subject). Level B = chunks of the reasoning, but only a slice: NSA orders
from 2019 on (~210k chunks) - all 21M reasoning chunks do not fit in RAM or UMAP.
2.25M judgment vectors are kept in float16 (~4.6 GB instead of 9.2 GB) in a file mapped
into memory (on_disk: Qdrant takes most of the RAM), the fit
runs on a sample, every judgment then goes to the nearest topic centre.

Judgment vectors are centred by court (center_by), as the ECHR ones by state: the
uncentred first run split the topics by court.
"""

from __future__ import annotations

from typing import ClassVar

from judgments_core.topics import TopicModel
from nsa.parser import judgment_body

# Polskie słowa bez treści i słowa każdego orzeczenia sądu administracyjnego.
POLISH_STOP_WORDS = frozenset(
    """
    aby albo ale bez bo być był była było były będzie będą by byłby co czy dla do gdy
    gdyż go ich im iż jak jako je jego jej jest jeszcze jednak jeśli jeżeli już ją kiedy
    kto która które którego której których który którym lub ma mają może można mu na nad
    nie nich nim niż o od oraz po pod podczas poza przed przez przy również się są ta tak
    także tam te tego tej ten to tu tych tylko tym tę u w we wraz wszystkie z za ze że
    zaś zgodnie jako której także jedynie natomiast więc wobec której wskazać należy
    """.split()  # noqa: SIM905
)
COURT_STOP_WORDS = frozenset(
    """
    sąd sądu sądowi sądem administracyjny administracyjnego administracyjnej
    administracyjnym naczelny naczelnego wojewódzki wojewódzkiego skarga skargi skargę
    skargą skarżący skarżącego skarżącej skarżącemu skarżąca organ organu organowi
    organem organy organów art ust pkt ustawy ustawa ustawie dnia roku sprawy sprawie
    sprawa postępowania postępowanie postępowaniu przepisu przepisów przepis przepisy
    decyzji decyzja decyzję wyrok wyroku postanowienie postanowienia uzasadnienie
    uzasadnieniu sentencja wskazał wskazała stwierdził uznał zważył następuje kasacyjnej
    kasacyjna kasacyjną zaskarżonego zaskarżonej zaskarżony zaskarżoną poz późn zm
    """.split()  # noqa: SIM905
)


class NsaTopics(TopicModel):
    judgment_skipped_parts = ("sentencja", "zdanie_odrebne")
    chunk_parts = ("uzasadnienie",)
    fact_fields: ClassVar[dict] = {
        "court": "sądy",
        "kind": "rodzaje orzeczeń",
        "symbols": "symbole spraw",
    }
    # Pilotaż poziomu B (nsa/notepad.md): postanowienia NSA od 2019 r.
    chunk_filter: ClassVar[dict] = {
        "is_nsa": True,
        "kind": "postanowienie",
        "year": (2019, None),
    }
    # Pierwszy przebieg bez tego: 205 z 432 tematów (49% orzeczeń) miało >= 90%
    # orzeczeń jednego sądu (każdy WSA ma swój szablon wpisu, prawa pomocy...).
    center_by = ("court",)
    vector_dtype = "float16"
    on_disk = True  # 2,25 mln wektorów orzeczeń w pliku mapowanym (RAM zajmuje Qdrant)
    corpus = (
        "orzeczenia polskich sądów administracyjnych (NSA i WSA, baza CBOSA): wyroki,"
        " postanowienia, uchwały"
    )
    assignment_tables: ClassVar[dict] = {
        "A": ("judgment_topics", ("judgment_id", "topic", "score")),
        "B": ("chunk_topics", ("judgment_id", "number", "topic", "score")),
    }
    min_cluster_size: ClassVar[dict] = {"A": 150, "B": 150}
    min_samples: ClassVar[dict] = {"A": 15, "B": 15}
    sample_size: ClassVar[dict] = {"A": 300000, "B": 150000}
    stop_words = POLISH_STOP_WORDS | COURT_STOP_WORDS

    def judgment_texts(self, cur, judgment_ids):
        cur.execute(
            "SELECT id, thesis, sentence, reasons, dissenting_opinion FROM judgments"
            " WHERE id IN %s",
            (list(judgment_ids),),
        )
        return {row[0]: judgment_body(*row[1:]) for row in cur.fetchall()}

    def judgment_titles(self, cur, judgment_ids):
        cur.execute(
            "SELECT id, docket_number, judgment_type, judgment_date FROM judgments WHERE id IN %s",
            (list(judgment_ids),),
        )
        return {
            judgment_id: f"{docket} - {kind} z {when}"
            for judgment_id, docket, kind, when in cur.fetchall()
        }
