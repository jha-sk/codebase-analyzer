"""Go source parser built on tree-sitter-go."""

from __future__ import annotations

import logging
from pathlib import Path

from . import ts_utils
from .base import EXTERNAL_MARKER, STDLIB_MARKER, BaseParser

logger = logging.getLogger(__name__)


class GoParser(BaseParser):
    """Parser for Go (``.go``) source files.

    Extracts ``import`` declarations, resolves project-internal package imports
    to the ``.go`` files of the target package directory (a Go package is a
    directory, so one import expands to multiple file dependencies), and
    classifies the rest as standard library or external. Module identity is
    determined from ``go.mod`` discovered by walking up from the source file.
    """

    language = "go"
    grammar = "go"

    def __init__(self, project_root: Path | None = None) -> None:
        super().__init__(project_root)
        # Cache of module-root directory -> module name, keyed by go.mod dir.
        self._module_cache: dict[Path, tuple[Path, str]] = {}

    @property
    def supported_extensions(self) -> list[str]:
        return [".go"]

    def parse_file(self, file_path: Path, content: str) -> dict:
        """Parse a Go file. See :meth:`BaseParser.parse_file`."""
        file_path = Path(file_path)
        try:
            root = ts_utils.parse_source(self.grammar, content)
        except Exception:  # pragma: no cover - defensive
            logger.exception("Failed to parse Go file %s", file_path)
            return self._empty_result(file_path)

        imports: list[str] = []
        dependencies: list[str] = []
        exports: list[str] = []

        # Imports live under import_declaration -> import_spec (optionally nested
        # in an import_spec_list for grouped imports). The path is an
        # interpreted_string_literal whose content we strip to a bare path.
        module_info = self._find_module(file_path)
        for spec in ts_utils.find_all(root, "import_spec"):
            path = self._import_path(spec)
            if not path:
                continue
            imports.append(path)
            for dep in self._resolve_import(path, module_info):
                dependencies.append(dep)

        # Exports: top-level identifiers beginning with an uppercase letter from
        # function/method declarations and type specs.
        for node in ts_utils.find_all(root, "function_declaration"):
            name = ts_utils.node_text(node.child_by_field_name("name"))
            if self._is_exported(name):
                exports.append(name)
        for node in ts_utils.find_all(root, "method_declaration"):
            name = ts_utils.node_text(node.child_by_field_name("name"))
            if self._is_exported(name):
                exports.append(name)
        for node in ts_utils.find_all(root, "type_spec"):
            name = ts_utils.node_text(node.child_by_field_name("name"))
            if self._is_exported(name):
                exports.append(name)

        ast_summary = {
            "function_count": (
                len(ts_utils.find_all(root, "function_declaration"))
                + len(ts_utils.find_all(root, "method_declaration"))
            ),
            "class_count": self._count_structs(root),
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

    # -- extraction helpers -----------------------------------------------

    def _import_path(self, spec) -> str:  # type: ignore[no-untyped-def]
        """Return the bare import path for an ``import_spec`` node.

        Strips the surrounding quotes of the ``interpreted_string_literal`` so
        ``"example.com/proj/pkg/util"`` becomes ``example.com/proj/pkg/util``.
        """
        literal = ts_utils.first_child_of_type(spec, "interpreted_string_literal")
        if literal is None:
            return ""
        content = ts_utils.first_child_of_type(literal, "interpreted_string_literal_content")
        if content is not None:
            return ts_utils.node_text(content)
        # Fall back to stripping the surrounding quotes.
        return ts_utils.node_text(literal).strip('"')

    def _count_structs(self, root) -> int:  # type: ignore[no-untyped-def]
        """Count ``type_spec`` nodes whose definition is a ``struct_type``."""
        count = 0
        for spec in ts_utils.find_all(root, "type_spec"):
            if ts_utils.first_child_of_type(spec, "struct_type") is not None:
                count += 1
        return count

    @staticmethod
    def _is_exported(name: str) -> bool:
        """Return whether ``name`` is a Go exported identifier (uppercase first)."""
        return bool(name) and name[0].isupper()

    # -- module discovery --------------------------------------------------

    def _find_module(self, file_path: Path) -> tuple[Path, str] | None:
        """Locate the governing ``go.mod`` and return ``(module_root, module_name)``.

        Searches upward from ``file_path`` and additionally at
        :attr:`project_root`. Results are cached per module-root directory.
        Returns ``None`` if no ``go.mod`` is found or there is no project root.
        """
        if self.project_root is None:
            return None

        search_dirs: list[Path] = []
        current = file_path.resolve().parent
        while True:
            search_dirs.append(current)
            if current == self.project_root or current == current.parent:
                break
            current = current.parent
        if self.project_root not in search_dirs:
            search_dirs.append(self.project_root)

        for directory in search_dirs:
            if directory in self._module_cache:
                return self._module_cache[directory]
            go_mod = directory / "go.mod"
            if go_mod.exists():
                module_name = self._read_module_name(go_mod)
                if module_name:
                    info = (directory, module_name)
                    self._module_cache[directory] = info
                    return info
        return None

    @staticmethod
    def _read_module_name(go_mod: Path) -> str | None:
        """Return the module name from the ``module <name>`` line in ``go.mod``."""
        try:
            text = go_mod.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover - defensive
            return None
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("module "):
                return stripped[len("module ") :].strip()
        return None

    # -- resolution helpers ------------------------------------------------

    def _resolve_import(self, path: str, module_info: tuple[Path, str] | None) -> list[str]:
        """Resolve a Go import path to dependency entries.

        Returns a list of resolved absolute ``.go`` file paths for local package
        imports, or a single-element list with a ``stdlib``/``external`` marker.
        """
        first_segment = path.split("/", 1)[0]
        # Standard library: first path segment has no dot.
        if "." not in first_segment:
            return [STDLIB_MARKER]

        if module_info is None:
            return [EXTERNAL_MARKER]

        module_root, module_name = module_info

        # Local module package: expand to all non-test .go files in the dir.
        if path == module_name or path.startswith(module_name + "/"):
            relative = path[len(module_name) :].lstrip("/")
            pkg_dir = module_root.joinpath(*relative.split("/")) if relative else module_root
            files = self._package_files(pkg_dir)
            if files:
                return files

        # Vendor fallback: <module_root>/vendor/<import_path>.
        vendor_dir = module_root.joinpath("vendor", *path.split("/"))
        vendor_files = self._package_files(vendor_dir)
        if vendor_files:
            return vendor_files

        return [EXTERNAL_MARKER]

    @staticmethod
    def _package_files(pkg_dir: Path) -> list[str]:
        """Return absolute paths of non-test ``.go`` files in ``pkg_dir``.

        Returns an empty list if the directory does not exist or holds no
        eligible files.
        """
        if not pkg_dir.is_dir():
            return []
        files: list[str] = []
        for go_file in sorted(pkg_dir.glob("*.go")):
            if go_file.name.endswith("_test.go"):
                continue
            files.append(str(go_file.resolve()))
        return files
