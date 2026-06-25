"""Abstract base class shared by all language parsers."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path

logger = logging.getLogger(__name__)

# Markers used in the ``dependencies`` list for imports that are intentionally
# not resolved to a local file path.
STDLIB_MARKER = "stdlib"
EXTERNAL_MARKER = "external"


class BaseParser(ABC):
    """Abstract base class for language-specific parsers.

    Subclasses parse a single source file into a normalized dictionary that the
    dependency-graph builder can consume. Parsers are stateless with respect to
    individual files but may hold project-wide context (e.g. the project root and
    cached build-tool metadata) used for import resolution.

    Args:
        project_root: Absolute path to the project root, used to resolve imports
            to local file paths. May be ``None`` for context-free parsing.
    """

    #: Canonical language name, e.g. ``"python"``. Set by each subclass.
    language: str = ""

    #: tree-sitter grammar name used by :mod:`ts_utils`. Set by each subclass.
    grammar: str = ""

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root: Path | None = (
            Path(project_root).resolve() if project_root is not None else None
        )

    @property
    @abstractmethod
    def supported_extensions(self) -> list[str]:
        """File extensions this parser handles (e.g. ``['.go']``)."""
        raise NotImplementedError

    @abstractmethod
    def parse_file(self, file_path: Path, content: str) -> dict:
        """Parse a single file into normalized dependency info.

        Args:
            file_path: Absolute path to the source file.
            content: Full text contents of the file.

        Returns:
            A dict with exactly these keys:

            ``file_path`` (str): Absolute path of the file.
            ``language`` (str): Canonical language name.
            ``imports`` (list[str]): Imported module/file path strings as written.
            ``exports`` (list[str]): Exported/public symbol names.
            ``dependencies`` (list[str]): Resolved local file paths this file
                depends on, plus the ``"stdlib"``/``"external"`` markers for
                unresolved imports.
            ``ast_summary`` (dict): High-level AST metadata such as function and
                class counts and a complexity estimate.
        """
        raise NotImplementedError

    def can_parse(self, file_path: Path) -> bool:
        """Return whether this parser handles ``file_path`` (by extension).

        Args:
            file_path: The candidate file.

        Returns:
            ``True`` if the file's suffix is in :attr:`supported_extensions`.
        """
        return file_path.suffix.lower() in self.supported_extensions

    # -- Shared helpers ---------------------------------------------------

    def _empty_result(self, file_path: Path) -> dict:
        """Return a well-formed empty result for ``file_path``.

        Used as a safe fallback when parsing fails, guaranteeing the output
        schema is always satisfied.

        Args:
            file_path: Absolute path to the source file.

        Returns:
            A result dict with empty collections and a zeroed summary.
        """
        return {
            "file_path": str(file_path),
            "language": self.language,
            "imports": [],
            "exports": [],
            "dependencies": [],
            "ast_summary": {
                "function_count": 0,
                "class_count": 0,
                "import_count": 0,
                "complexity": 1,
                "line_count": 0,
            },
        }

    def _grammar_for(self, file_path: Path) -> str:
        """Return the tree-sitter grammar name to use for ``file_path``.

        Defaults to :attr:`grammar`; subclasses (e.g. TypeScript) override this
        to choose a grammar per file extension.

        Args:
            file_path: The file being parsed.

        Returns:
            A canonical grammar name.
        """
        return self.grammar

    def extract_symbols(self, file_path: Path, content: str) -> dict:
        """Extract function definitions and call sites from a file.

        This powers the function-level (layer 3) call graph. It is implemented
        once here for all languages on top of :func:`ts_utils.extract_symbols`.

        Args:
            file_path: Absolute path to the source file.
            content: Full text contents of the file.

        Returns:
            A dict ``{"file_path", "language", "functions", "calls"}`` where
            ``functions`` is a list of ``{"name", "start_line", "end_line"}`` and
            ``calls`` is a list of ``{"caller", "callee", "line"}``. On any
            parse failure, empty ``functions``/``calls`` are returned.
        """
        from . import ts_utils

        file_path = Path(file_path)
        try:
            grammar = self._grammar_for(file_path)
            root = ts_utils.parse_source(grammar, content)
            symbols = ts_utils.extract_symbols(root, self.grammar)
        except Exception:  # pragma: no cover - defensive
            logger.exception("Symbol extraction failed for %s", file_path)
            symbols = {"functions": [], "calls": []}
        return {
            "file_path": str(file_path),
            "language": self.language,
            "functions": symbols["functions"],
            "calls": symbols["calls"],
        }

    @staticmethod
    def _dedupe_preserve_order(items: list[str]) -> list[str]:
        """Remove duplicates from ``items`` while preserving first-seen order.

        Args:
            items: A list of strings.

        Returns:
            A new list with duplicates removed.
        """
        seen: set[str] = set()
        result: list[str] = []
        for item in items:
            if item not in seen:
                seen.add(item)
                result.append(item)
        return result
