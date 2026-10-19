import sys
from pathlib import Path

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
    make_source=HudocSource,
    migrations=Path(__file__).parent / "migrations",
    # HUDOC stoi za Cloudflare, który zatrzymuje TLS Pythona, a curl przepuszcza.
    transport="curl",
    derive=derive,
    verify=verify,
    after_derive=refresh_derived,
    web=make_app,
)

if __name__ == "__main__":
    sys.exit(main(PROJECT))
