import sys
from pathlib import Path

from judgments_core.cli import Project, main
from nsa.deriver import NsaDeriver
from nsa.embed import NsaEmbedJob
from nsa.importer import NsaImporter
from nsa.mcp import NsaMcp
from nsa.pipeline import NsaRag
from nsa.rag import NsaRetriever
from nsa.source import CbosaSource
from nsa.web import NsaWebApp

PROJECT = Project(
    name="nsa",
    title="Orzeczenia sądów administracyjnych (CBOSA)",
    # pobierz: strony orzeczeń CBOSA dla identyfikatorów z plików w
    # nsa/database/cbosa-id/ (wyszukiwarki CBOSA robots.txt zabrania, nsa/notepad.md).
    # Zbiór JuDDGES/pl-nsa (CBOSA do 2025-03) już wczytany; ponownie:
    # source=JuddgesSource (nsa.juddges). przetworz bierze oba źródła.
    source=CbosaSource,
    importer=NsaImporter,
    deriver=NsaDeriver,
    embed_job=NsaEmbedJob,
    web=NsaWebApp,
    mcp=NsaMcp,
    retriever=NsaRetriever,
    rag=NsaRag,
    migrations=Path(__file__).parent / "migrations",
)

if __name__ == "__main__":
    sys.exit(main(PROJECT))
