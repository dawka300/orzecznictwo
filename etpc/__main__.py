import sys
from pathlib import Path

from etpc.source import HudocSource
from etpc.tables import derive
from judgments_core.cli import Project, main

PROJECT = Project(
    name="etpc",
    title="Orzeczenia ETPC z HUDOC",
    make_source=HudocSource,
    migrations=Path(__file__).parent / "migrations",
    # HUDOC stoi za Cloudflare, który zatrzymuje TLS Pythona, a curl przepuszcza.
    transport="curl",
    derive=derive,
)

if __name__ == "__main__":
    sys.exit(main(PROJECT))
