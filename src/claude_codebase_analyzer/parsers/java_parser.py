"""Java source parser built on tree-sitter-java."""

from __future__ import annotations

import logging
from pathlib import Path

from . import ts_utils
from .base import EXTERNAL_MARKER, STDLIB_MARKER, BaseParser

logger = logging.getLogger(__name__)

# Directories never worth scanning when discovering Java source roots.
_PRUNE_DIRS: frozenset[str] = frozenset({"node_modules", ".git", "build", "target", ".venv"})

# Conventional Maven/Gradle source-root suffixes, as path part sequences.
_SOURCE_ROOT_SUFFIXES: tuple[tuple[str, ...], ...] = (
    ("src", "main", "java"),
    ("src", "test", "java"),
)


class JavaParser(BaseParser):
    """Parser for Java (``.java``) source files.

    Extracts ``import`` declarations and ``package`` info, resolves project
    internal imports to local file paths using discovered Maven/Gradle source
    roots (``src/main/java`` and ``src/test/java``), and classifies the rest as
    standard library (``java.*``/``javax.*``) or external.
    """

    language = "java"
    grammar = "java"

    def __init__(self, project_root: Path | None = None) -> None:
        super().__init__(project_root)
        # Cache of discovered absolute source-root paths, keyed by project_root.
        self._source_root_cache: dict[Path, list[Path]] = {}

    @property
    def supported_extensions(self) -> list[str]:
        return [".java"]

    def parse_file(self, file_path: Path, content: str) -> dict:
        """Parse a Java file. See :meth:`BaseParser.parse_file`."""
        file_path = Path(file_path)
        try:
            root = ts_utils.parse_source(self.grammar, content)
        except Exception:  # pragma: no cover - defensive
            logger.exception("Failed to parse Java file %s", file_path)
            return self._empty_result(file_path)

        imports: list[str] = []
        dependencies: list[str] = []
        exports: list[str] = []

        # `import com.example.Foo;` and wildcard `import com.example.*;`
        for node in ts_utils.find_all(root, "import_declaration"):
            fqn, is_wildcard = self._import_name(node)
            if not fqn:
                continue
            imports.append(f"{fqn}.*" if is_wildcard else fqn)
            dependencies.extend(self._resolve_import(fqn, is_wildcard))

        # Public exports: all top-level declared type names.
        for node in root.children:
            if node.type in (
                "class_declaration",
                "interface_declaration",
                "enum_declaration",
                "record_declaration",
            ):
                name = node.child_by_field_name("name")
                if name is not None:
                    exports.append(ts_utils.node_text(name))

        function_count = len(
            ts_utils.find_all(root, "method_declaration", "constructor_declaration")
        )
        class_count = len(
            ts_utils.find_all(
                root,
                "class_declaration",
                "interface_declaration",
                "enum_declaration",
                "record_declaration",
            )
        )

        ast_summary = {
            "function_count": function_count,
            "class_count": class_count,
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

    def _import_name(self, node) -> tuple[str, bool]:  # type: ignore[no-untyped-def]
        """Return ``(fully_qualified_name, is_wildcard)`` for an import node.

        For ``import com.foo.*;`` the name is the package (``com.foo``) and the
        wildcard flag is ``True``. For ``import com.foo.Bar;`` the name is the
        full type FQN and the flag is ``False``. Static imports are treated the
        same as ordinary imports (their FQN is returned as written).
        """
        scoped = ts_utils.first_child_of_type(node, "scoped_identifier")
        if scoped is None:
            # Single-segment import such as `import Foo;` (rare).
            identifier = ts_utils.first_child_of_type(node, "identifier")
            name = ts_utils.node_text(identifier) if identifier is not None else ""
        else:
            name = ts_utils.node_text(scoped)
        is_wildcard = ts_utils.first_child_of_type(node, "asterisk") is not None
        return name, is_wildcard

    # -- source-root discovery --------------------------------------------

    def _source_roots(self) -> list[Path]:
        """Return discovered source roots, falling back to the project root.

        Searches under ``project_root`` for directories ending in
        ``src/main/java`` or ``src/test/java`` (pruning ``node_modules``,
        ``.git``, ``build``, ``target`` and ``.venv``). Results are cached per
        project root. If none are found, the project root itself is used.
        """
        if self.project_root is None:
            return []
        if self.project_root in self._source_root_cache:
            return self._source_root_cache[self.project_root]

        roots: list[Path] = []
        suffixes = {parts[-1]: parts for parts in _SOURCE_ROOT_SUFFIXES}
        leaf_names = set(suffixes)
        for dirpath in self._walk_dirs(self.project_root):
            if dirpath.name not in leaf_names:
                continue
            for parts in _SOURCE_ROOT_SUFFIXES:
                if dirpath.parts[-len(parts) :] == parts:
                    roots.append(dirpath)
                    break

        if not roots:
            roots = [self.project_root]
        self._source_root_cache[self.project_root] = roots
        return roots

    def _walk_dirs(self, base: Path) -> list[Path]:
        """Yield ``base`` and all descendant directories, pruning noise dirs."""
        result: list[Path] = []
        stack = [base]
        while stack:
            current = stack.pop()
            result.append(current)
            try:
                children = [c for c in current.iterdir() if c.is_dir()]
            except OSError:  # pragma: no cover - defensive
                continue
            for child in children:
                if child.name in _PRUNE_DIRS:
                    continue
                stack.append(child)
        return result

    # -- resolution helpers ------------------------------------------------

    def _resolve_import(self, fqn: str, is_wildcard: bool) -> list[str]:
        """Resolve an import FQN to dependency markers and/or local file paths.

        Returns a list because a wildcard import may resolve to several files.
        """
        top = fqn.split(".")[0]
        if top in ("java", "javax"):
            return [STDLIB_MARKER]
        if self.project_root is None:
            return [EXTERNAL_MARKER]

        parts = fqn.split(".")
        for src_root in self._source_roots():
            if is_wildcard:
                package_dir = src_root.joinpath(*parts)
                if package_dir.is_dir():
                    files = sorted(str(p.resolve()) for p in package_dir.glob("*.java"))
                    if files:
                        return files
            else:
                candidate = src_root.joinpath(*parts).with_suffix(".java")
                if candidate.exists():
                    return [str(candidate.resolve())]

        return [EXTERNAL_MARKER]
