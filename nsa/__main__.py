import sys
from pathlib import Path

from judgments_core.cli import Project, main
from nsa.juddges import JuddgesSource
from nsa.tables import derive

PROJECT = Project(
    name="nsa",
    title="Orzeczenia sądów administracyjnych (CBOSA)",
    # Na razie zbiór JuDDGES/pl-nsa z Hugging Face (CBOSA do 2025-03);
    # wyszukiwarki CBOSA robots.txt zabrania (nsa/notepad.md).
    make_source=JuddgesSource,
    migrations=Path(__file__).parent / "migrations",
    derive=derive,
)

if __name__ == "__main__":
    sys.exit(main(PROJECT))
