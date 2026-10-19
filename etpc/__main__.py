import sys
from pathlib import Path

from etpc.embed import embed
from etpc.importer import EtpcImporter
from etpc.ranks import refresh_ranks
from etpc.source import HudocSource
from etpc.stats import refresh_stats
from etpc.tables import derive
from etpc.verify import verify
from etpc.web import make_app
from judgments_core.cli import Project, main


def refresh_derived(cur):
    """After przetworz: statistics and the citation ranking (a few seconds)."""
    refresh_stats(cur)
    refresh_ranks(cur)


PROJECT = Project(
    name="etpc",
    title="Orzeczenia ETPC z HUDOC",
    source=HudocSource,
    importer=EtpcImporter,
    migrations=Path(__file__).parent / "migrations",
    # HUDOC stoi za Cloudflare: TLS Pythona zatrzymuje zawsze, curl zwykle
    # przepuszcza, a gdy i jego blokuje - przeglądarka (tylko lokalnie).
    transports=("requests", "curl", "playwright"),
    derive=derive,
    verify=verify,
    after_derive=refresh_derived,
    web=make_app,
    embed=embed,
)

if __name__ == "__main__":
    sys.exit(main(PROJECT))
