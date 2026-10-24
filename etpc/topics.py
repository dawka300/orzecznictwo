"""ETPC topics (python -m etpc tematy), the ETPC child of TopicModel.

Vectors are centred by respondent state and language first (see
TopicModel.center_by), so topics are about the issue, not the country.

Level A = rulings: the mean of a ruling's chunks without separate opinions
and the operative part (boilerplate "holds that there has been a violation"
groups by outcome, not by subject). Level B = chunks of THE LAW. Texts are
English or French (the version that has vectors, ruling_vectors.itemid), so
the stop words are both, plus words every ruling repeats.
"""

from __future__ import annotations

from typing import ClassVar

from judgments_core.topics import TopicModel

FRENCH_STOP_WORDS = frozenset(
    """
    au aux avec ce ces cet cette dans de des du elle elles en et eux il ils je la le les
    leur leurs lui ma mais me même mes moi mon ne nos notre nous on ou où par pas pour qu
    que qui sa se ses son sur ta te tes toi ton tu un une vos votre vous été étée étées étés
    étant suis es est sommes êtes sont serai sera seront serait seraient étais était étaient
    fus fut furent sois soit soient fusse fût ai as avons avez ont aurai aura auront aurait
    auraient avais avait avaient eu eus eut eurent ayant ceci cela celà celui celle ceux
    celles comme donc dont lequel laquelle lesquels lesquelles lors ainsi alors aussi autre
    autres avant après bien car chaque contre depuis déjà deux doit doivent encore entre
    fait faire leurs moins non outre parce peu peut peuvent plus plusieurs pendant puis
    quand quant quelle quelles quel quels sans selon si sous tant tel telle tels telles tous
    tout toute toutes très vers via également notamment toutefois cependant néanmoins
    """.split()  # noqa: SIM905
)

# Słowa każdego orzeczenia (po usunięciu angielskich/francuskich stop words).
ECHR_STOP_WORDS = frozenset(
    """
    court courts applicant applicants application applications article articles convention
    government governments case cases paragraph paragraphs para paras judgment judgments
    decision decisions protocol also would could may must shall mr mrs ms see cited above
    therefore however accordingly regard respect considers consider considered concerned
    particular present relevant thus whether although within without since upon even cour
    requérant requérante requérants requérantes requête requêtes gouvernement affaire
    affaires paragraphe paragraphes arrêt arrêts décision décisions protocole article
    articles
    """.split()  # noqa: SIM905
)


class EtpcTopics(TopicModel):
    judgment_skipped_parts = ("opinia", "sentencja")
    chunk_parts = ("prawo",)
    fact_fields: ClassVar[dict] = {"articles": "artykuły Konwencji", "respondents": "państwa"}
    # Bez tego 94% orzeczeń trafiało do tematów jednego państwa (i języka):
    # średni wektor niesie głównie nazwy, sądy i procedurę krajową.
    center_by = ("respondents", "language")
    corpus = (
        "orzeczenia Europejskiego Trybunału Praw Człowieka (wyroki i decyzje,"
        " teksty angielskie i francuskie)"
    )
    assignment_tables: ClassVar[dict] = {
        "A": ("ruling_topics", ("ruling_id", "topic", "score")),
        "B": ("chunk_topics", ("ruling_id", "number", "topic", "score")),
    }

    @property
    def stop_words(self):
        from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

        return ENGLISH_STOP_WORDS | FRENCH_STOP_WORDS | ECHR_STOP_WORDS

    def judgment_texts(self, cur, judgment_ids):
        cur.execute(
            "SELECT v.ruling_id, d.body FROM ruling_vectors v"
            " JOIN ruling_documents d ON d.itemid = v.itemid WHERE v.ruling_id IN %s",
            (list(judgment_ids),),
        )
        return dict(cur.fetchall())

    def judgment_titles(self, cur, judgment_ids):
        cur.execute(
            "SELECT r.id, d.title, r.ruling_date, r.kind FROM rulings r"
            " JOIN ruling_vectors v ON v.ruling_id = r.id"
            " JOIN ruling_documents d ON d.itemid = v.itemid WHERE r.id IN %s",
            (list(judgment_ids),),
        )
        return {
            ruling_id: f"{title} ({ruling_date}, {'wyrok' if kind == 'judgment' else 'decyzja'})"
            for ruling_id, title, ruling_date, kind in cur.fetchall()
        }
