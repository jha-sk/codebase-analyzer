"""Language-specific parsers built on tree-sitter.

The :func:`get_parser_for_file` factory returns the appropriate parser for a
given file based on its extension. All parsers conform to the
:class:`~claude_codebase_analyzer.parsers.base.BaseParser` interface.
"""

from __future__ import annotations

from pathlib import Path

from .base import BaseParser

__all__ = [
    "BaseParser",
    "get_all_parsers",
    "get_parser_for_file",
]


def get_all_parsers(project_root: Path | None = None) -> list[BaseParser]:
    """Instantiate one parser per supported language.

    Args:
        project_root: Project root used by parsers for import resolution.

    Returns:
        A list of parser instances (Go, Java, Python, TypeScript/JavaScript).
    """
    # Imported lazily to avoid importing every tree-sitter grammar at module load.
    from .go_parser import GoParser
    from .java_parser import JavaParser
    from .python_parser import PythonParser
    from .typescript_parser import TypeScriptParser

    return [
        GoParser(project_root=project_root),
        JavaParser(project_root=project_root),
        PythonParser(project_root=project_root),
        TypeScriptParser(project_root=project_root),
    ]


def get_parser_for_file(file_path: Path, project_root: Path | None = None) -> BaseParser | None:
    """Return a parser that can handle ``file_path``, or ``None`` if unsupported.

    Args:
        file_path: The file to find a parser for.
        project_root: Project root used by parsers for import resolution.

    Returns:
        A matching :class:`BaseParser`, or ``None`` when no parser applies.
    """
    for parser in get_all_parsers(project_root=project_root):
        if parser.can_parse(file_path):
            return parser
    return None
