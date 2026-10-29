import sys
from pathlib import Path

from etpc.deriver import EtpcDeriver
from etpc.embed import EtpcEmbedJob
from etpc.formulas import EtpcFormulas
from etpc.importer import EtpcImporter
from etpc.mcp import EtpcMcp
from etpc.pipeline import EtpcRag
from etpc.rag import EtpcRetriever
from etpc.source import HudocSource
from etpc.topics import EtpcTopics
from etpc.verify import verify
from etpc.web import EtpcWebApp
from judgments_core.cli import Project, main

PROJECT = Project(
    name="etpc",
    title="Orzeczenia ETPC z HUDOC",
    source=HudocSource,
    importer=EtpcImporter,
    deriver=EtpcDeriver,
    migrations=Path(__file__).parent / "migrations",
    # HUDOC stoi za Cloudflare: TLS Pythona zatrzymuje zawsze, curl zwykle
    # przepuszcza, a gdy i jego blokuje - przeglądarka (tylko lokalnie).
    transports=("requests", "curl", "playwright"),
    verify=verify,
    web=EtpcWebApp,
    embed_job=EtpcEmbedJob,
    mcp=EtpcMcp,
    retriever=EtpcRetriever,
    rag=EtpcRag,
    formulas=EtpcFormulas,
    topics=EtpcTopics,
)

if __name__ == "__main__":
    sys.exit(main(PROJECT))
