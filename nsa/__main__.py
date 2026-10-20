import sys
from pathlib import Path

from judgments_core.cli import Project, main
from nsa.deriver import NsaDeriver
from nsa.embed import NsaEmbedJob
from nsa.importer import NsaImporter
from nsa.juddges import JuddgesSource
from nsa.mcp import NsaMcp
from nsa.pipeline import NsaRag
from nsa.rag import NsaRetriever
from nsa.web import NsaWebApp

PROJECT = Project(
    name="nsa",
    title="Orzeczenia sądów administracyjnych (CBOSA)",
    # Na razie zbiór JuDDGES/pl-nsa z Hugging Face (CBOSA do 2025-03);
    # wyszukiwarki CBOSA robots.txt zabrania (nsa/notepad.md).
    source=JuddgesSource,
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
