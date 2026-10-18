import sys
from pathlib import Path

from judgments_core.cli import Project, main
from nsa.source import CbosaSource

PROJECT = Project(
    name="nsa",
    title="Orzeczenia sądów administracyjnych z CBOSA",
    make_source=CbosaSource,
    migrations=Path(__file__).parent / "migrations",
)

if __name__ == "__main__":
    sys.exit(main(PROJECT))
