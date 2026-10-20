"""All search pages under one address: python -m portal [--host 127.0.0.1] [--port 8080].

/etpc/ - ETPC search and statistics, /nsa/ - NSA/WSA, / - the list of projects;
old addresses without a prefix (/orzeczenie/1, /?q=...) go to ETPC, which used
to be alone on 8080. Lives outside judgments_core: the core must not import
projects. Each project reads its own settings (ETPC_*, NSA_*); the query model
(bge-m3 on the CPU) is loaded once for all of them.
"""

from __future__ import annotations

import argparse
import sys
from types import SimpleNamespace

from etpc.__main__ import PROJECT as ETPC
from judgments_core.cli import setup_logging
from judgments_core.settings import Settings
from judgments_core.web import Portal, serve
from nsa.__main__ import PROJECT as NSA

PROJECTS = (ETPC, NSA)


def build(projects=PROJECTS):
    apps = {p.name: (p.title, p.web(Settings.from_env(p.name))) for p in projects if p.web}
    return Portal(apps, default=projects[0].name)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m portal", description="Wszystkie wyszukiwarki")
    parser.add_argument("--host", default="127.0.0.1", help="adres (domyślnie 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="port (domyślnie 8080)")
    args = parser.parse_args(argv)
    setup_logging(SimpleNamespace(name="portal"), Settings.from_env("portal"))
    serve(build(), args.host, args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
