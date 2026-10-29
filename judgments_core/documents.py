"""The document tabs of a project's pages, the same in every project: Analiza dokumentu
(/analiza), Porównanie dokumentów (/porownanie), Analiza sprawy (/sprawa), Teczka sprawy
(/teczka) and Badanie (/badanie).

A project gives what is its own - the retriever and the chunker of its base, its
formulas (a child of boilerplate.FormulaCorpus: list and patterns in its languages) -
and routes its requests through route(); the tabs, the case folder shared by all of them
and the formulas set on each are built here once.
"""

from __future__ import annotations

from judgments_core.analysis import DocumentAnalysis
from judgments_core.comparison import DocumentComparison
from judgments_core.folder import CaseFolder
from judgments_core.library import Library
from judgments_core.research import Research


class DocumentPages:
    """The document tabs of one project. Children may swap a tab's class (analysis_class,
    comparison_class, folder_class, research_class, library_class)."""

    analysis_class = DocumentAnalysis
    comparison_class = DocumentComparison
    folder_class = CaseFolder
    research_class = Research
    library_class = Library

    def __init__(self, settings, retriever, chunker, connect, embedder, formulas=None):
        """embedder: the query model already in the process (None = only LM Studio);
        formulas: boilerplate.Formulas of the project (None = no filter)."""
        self.analysis = self.analysis_class(settings, retriever, chunker, connect, embedder)
        self.comparison = self.comparison_class(settings, embedder)
        self.folder = self.folder_class(settings, embedder)
        self.research = self.research_class(settings, embedder)
        # Teczka sprawy: dokumenty zapisane raz (OCR), do wyboru w pozostałych zakładkach
        self.library = self.library_class(settings, self.folder, chunker, embedder)
        self.formulas = formulas
        for pages in self.tabs():
            pages.library = self.library
            pages.formulas = formulas
        if formulas and embedder is not None:  # wektory listy formułek w tle (~1 min CPU)
            formulas.prepare_in_background(embedder)

    def tabs(self):
        return (self.analysis, self.comparison, self.folder, self.research)

    def route(self, request, layout):
        """The Response of a document tab, or None when the address is not one of them."""
        pages = {
            "/analiza": self.analysis,
            "/porownanie": self.comparison,
            "/sprawa": self.folder,
            "/teczka": self.library,
            "/badanie": self.research,
        }
        for address, tab in pages.items():
            if request.path == address or request.path.startswith(address + "/"):
                return tab.handle(request, layout)
        return None
