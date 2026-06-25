"""Python source parser built on tree-sitter-python."""

from __future__ import annotations

import logging
from pathlib import Path

from . import ts_utils
from .base import EXTERNAL_MARKER, STDLIB_MARKER, BaseParser

logger = logging.getLogger(__name__)

# A representative subset of the Python standard library top-level modules.
# Anything in here is classified as "stdlib" rather than resolved or treated as
# an external dependency. (Using sys.stdlib_module_names keeps this current on
# 3.10+.)
try:  # pragma: no cover - trivial
    import sys

    _STDLIB_MODULES: frozenset[str] = frozenset(sys.stdlib_module_names)
except AttributeError:  # pragma: no cover - very old interpreters
    _STDLIB_MODULES = frozenset(
        {"os", "sys", "json", "re", "math", "typing", "dataclasses", "pathlib"}
    )


class PythonParser(BaseParser):
    """Parser for Python (``.py``) source files.

    Extracts ``import`` and ``from ... import`` statements, resolves project
    internal imports to file paths (including relative imports and packages with
    ``__init__.py``), and classifies the rest as standard library or external.
    """

    language = "python"
    grammar = "python"

    @property
    def supported_extensions(self) -> list[str]:
        return [".py", ".pyi"]

    def parse_file(self, file_path: Path, content: str) -> dict:
        """Parse a Python file. See :meth:`BaseParser.parse_file`."""
        file_path = Path(file_path)
        try:
            root = ts_utils.parse_source(self.grammar, content)
        except Exception:  # pragma: no cover - defensive
            logger.exception("Failed to parse Python file %s", file_path)
            return self._empty_result(file_path)

        imports: list[str] = []
        dependencies: list[str] = []
        exports: list[str] = []

        # Top-level `import a.b.c` / `import a as b`
        for node in ts_utils.find_all(root, "import_statement"):
            for name_node in self._iter_dotted_names(node):
                module = name_node
                imports.append(module)
                dependencies.append(self._resolve_absolute(module, file_path))

        # `from X import y` and `from . import y`
        for node in ts_utils.find_all(root, "import_from_statement"):
            module_text, level = self._from_module(node)
            names = self._imported_names(node)
            display = ("." * level) + module_text
            imports.append(display)
            if level > 0:
                dependencies.append(self._resolve_relative(module_text, level, file_path))
                # `from . import submodule` -> resolve each name as a submodule,
                # but only record it when it maps to a real file (a name like
                # `User` from `.models` is a symbol, not a module).
                for name in names:
                    sub = f"{module_text}.{name}" if module_text else name
                    resolved = self._resolve_relative(sub, level, file_path)
                    if resolved not in (STDLIB_MARKER, EXTERNAL_MARKER):
                        dependencies.append(resolved)
            elif module_text:
                dependencies.append(self._resolve_absolute(module_text, file_path))

        # Public exports: top-level function/class defs not prefixed with "_".
        for node in root.children:
            if node.type in ("function_definition", "class_definition"):
                name = node.child_by_field_name("name")
                if name is not None:
                    symbol = ts_utils.node_text(name)
                    if not symbol.startswith("_"):
                        exports.append(symbol)
            elif node.type == "decorated_definition":
                inner = node.child_by_field_name("definition")
                if inner is not None:
                    name = inner.child_by_field_name("name")
                    if name is not None and not ts_utils.node_text(name).startswith("_"):
                        exports.append(ts_utils.node_text(name))

        # Honour __all__ if present (overrides heuristic exports).
        explicit = self._extract_dunder_all(root)
        if explicit is not None:
            exports = explicit

        ast_summary = {
            "function_count": len(ts_utils.find_all(root, "function_definition")),
            "class_count": len(ts_utils.find_all(root, "class_definition")),
            "import_count": len(imports),
            "complexity": ts_utils.count_branch_points(root, self.grammar),
            "line_count": content.count("\n") + 1,
        }

        return {
            "file_path": str(file_path),
            "language": self.language,
            "imports": self._dedupe_preserve_order(imports),
            "exports": self._dedupe_preserve_order(exports),
            "dependencies": self._dedupe_preserve_order(dependencies),
            "ast_summary": ast_summary,
        }

    # -- import extraction helpers ----------------------------------------

    def _iter_dotted_names(self, import_node) -> list[str]:  # type: ignore[no-untyped-def]
        """Extract module names from an ``import_statement`` node."""
        names: list[str] = []
        for child in import_node.children:
            if child.type == "dotted_name":
                names.append(ts_utils.node_text(child))
            elif child.type == "aliased_import":
                target = ts_utils.first_child_of_type(child, "dotted_name")
                if target is not None:
                    names.append(ts_utils.node_text(target))
        return names

    def _from_module(self, node) -> tuple[str, int]:  # type: ignore[no-untyped-def]
        """Return (module_path, relative_level) for a ``from`` import.

        ``from . import x`` -> ("", 1); ``from ..pkg import y`` -> ("pkg", 2);
        ``from pkg.mod import z`` -> ("pkg.mod", 0).
        """
        module_node = node.child_by_field_name("module_name")
        if module_node is None:
            return "", 0
        if module_node.type == "relative_import":
            prefix = ts_utils.first_child_of_type(module_node, "import_prefix")
            level = ts_utils.node_text(prefix).count(".") if prefix is not None else 1
            dotted = ts_utils.first_child_of_type(module_node, "dotted_name")
            module = ts_utils.node_text(dotted) if dotted is not None else ""
            return module, level
        # Absolute import: module_name is a plain dotted_name.
        return ts_utils.node_text(module_node), 0

    def _imported_names(self, node) -> list[str]:  # type: ignore[no-untyped-def]
        """Return the names imported by a ``from ... import a, b`` statement.

        Excludes the module name itself and wildcard imports.
        """
        module_node = node.child_by_field_name("module_name")
        names: list[str] = []
        seen_import_kw = False
        for child in node.children:
            if child.type == "import":
                seen_import_kw = True
                continue
            if not seen_import_kw:
                continue
            if child is module_node:
                continue
            if child.type == "dotted_name":
                names.append(ts_utils.node_text(child))
            elif child.type == "aliased_import":
                target = ts_utils.first_child_of_type(child, "dotted_name")
                if target is not None:
                    names.append(ts_utils.node_text(target))
        return names

    def _extract_dunder_all(self, root) -> list[str] | None:  # type: ignore[no-untyped-def]
        """Return the contents of ``__all__`` if defined at module level."""
        for node in root.children:
            if node.type != "expression_statement":
                continue
            assign = ts_utils.first_child_of_type(node, "assignment")
            if assign is None:
                continue
            left = assign.child_by_field_name("left")
            if left is None or ts_utils.node_text(left) != "__all__":
                continue
            right = assign.child_by_field_name("right")
            if right is None or right.type not in ("list", "tuple"):
                continue
            values: list[str] = []
            for string_node in ts_utils.find_all(right, "string"):
                values.append(self._string_value(string_node))
            return [v for v in values if v]
        return None

    @staticmethod
    def _string_value(string_node) -> str:  # type: ignore[no-untyped-def]
        """Return the unquoted contents of a tree-sitter ``string`` node."""
        for child in string_node.children:
            if child.type == "string_content":
                return ts_utils.node_text(child)
        # Fall back to stripping the surrounding quotes.
        return ts_utils.node_text(string_node).strip("\"'")

    # -- resolution helpers ------------------------------------------------

    def _resolve_absolute(self, module: str, file_path: Path) -> str:
        """Resolve an absolute import to a local path, stdlib, or external."""
        top = module.split(".")[0]
        if top in _STDLIB_MODULES:
            return STDLIB_MARKER
        if self.project_root is None:
            return EXTERNAL_MARKER
        resolved = self._module_to_path(module.split("."), self.project_root)
        return resolved if resolved is not None else EXTERNAL_MARKER

    def _resolve_relative(self, module: str, level: int, file_path: Path) -> str:
        """Resolve a relative import (``from . / .. import``) to a local path."""
        base = file_path.parent
        # `from . import x` (level 1) -> current package dir; each extra dot goes up.
        for _ in range(level - 1):
            base = base.parent
        parts = module.split(".") if module else []
        resolved = self._module_to_path(parts, base)
        return resolved if resolved is not None else EXTERNAL_MARKER

    def _module_to_path(self, parts: list[str], base: Path) -> str | None:
        """Map module path components under ``base`` to a real ``.py`` file.

        Tries ``base/a/b.py`` then ``base/a/b/__init__.py``. An empty ``parts``
        list resolves to ``base/__init__.py`` (for ``from . import x``).
        """
        if not parts:
            init = base / "__init__.py"
            return str(init.resolve()) if init.exists() else None

        candidate_module = base.joinpath(*parts).with_suffix(".py")
        if candidate_module.exists():
            return str(candidate_module.resolve())

        candidate_pkg = base.joinpath(*parts) / "__init__.py"
        if candidate_pkg.exists():
            return str(candidate_pkg.resolve())

        return None
