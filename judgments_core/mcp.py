"""Parent of the project MCP servers: the database as tools for Claude (python -m <projekt> mcp).

Claude (Claude Code, Claude Desktop) starts the server and talks to it over
stdin/stdout, so nothing else may print to stdout (the log goes to stderr
and the log file). A project's child registers its tools (search, a
judgment's text, similar judgments) in register(); Claude does the RAG
itself: it searches, reads what it found and answers with citations.

The common part: the database connection, the semantic search (query
embedders from the settings, Qdrant) and the trimming of long texts. Runs
locally (MCP SDK needs Python 3.10+, requirements-dev.txt), not on the hosting.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from judgments_core import db
from judgments_core.embeddings import make_embedder
from judgments_core.qdrant import QdrantStore, collection_name

log = logging.getLogger(__name__)

# Ile znaków tekstu orzeczenia oddać naraz: dłuższe Claude czyta kawałkami (od=...).
TEXT_LIMIT = 20_000
SEMANTIC_TIMEOUT_S = 60


class McpServer(ABC):
    """MCP server of a project; children set project, semantic_class, instructions and register()."""

    project = ""  # nazwa projektu: kolekcja Qdranta <projekt>_<model>
    title = ""
    instructions = ""  # dla Claude'a: co jest w bazie i jak szukać
    semantic_class = None  # klasa potomna SemanticSearch; None = bez wyszukiwania znaczeniowego

    def __init__(self, settings, semantic=None):
        self.settings = settings
        if semantic is None and self.semantic_class is not None:
            semantic = self.semantic_class(
                make_embedder(settings, timeout_s=SEMANTIC_TIMEOUT_S, retries=0),
                QdrantStore(
                    settings.qdrant_url,
                    settings.qdrant_collection
                    or collection_name(self.project, settings.embed_model),
                    settings.embed_model,
                    timeout_s=SEMANTIC_TIMEOUT_S,
                ),
            )
            semantic.prepare_in_background()
        self.semantic = semantic

    @abstractmethod
    def register(self, server):
        """Add the project's tools: server.add_tool(fn, name=..., description=...)."""

    def connect(self):
        """A database connection for one tool call (tools only read)."""
        return db.connect(self.settings.db_dsn, autocommit=True)

    def build(self):
        from mcp.server.mcpserver import MCPServer  # opcjonalna zależność (Python 3.10+)

        server = MCPServer(name=self.project, title=self.title, instructions=self.instructions)
        self.register(server)
        return server

    def run(self):
        log.info("serwer MCP %s: stdio", self.project)
        self.build().run("stdio")


def cut(text, start=0, limit=TEXT_LIMIT):
    """text[start:start+limit] with a note where the rest begins (for reading in parts)."""
    text = text or ""
    piece = text[start : start + limit]
    end = start + len(piece)
    if end < len(text):
        piece += f"\n\n[... ucięto: znaki {end}-{len(text)}; dalej: od={end}]"
    return piece


def lines(*pairs):
    """'Label: value' lines, skipping empty values."""
    return "\n".join(f"{label}: {value}" for label, value in pairs if value not in (None, "", []))
