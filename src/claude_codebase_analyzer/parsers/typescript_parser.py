"""TypeScript/JavaScript source parser built on tree-sitter-typescript."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from . import ts_utils
from .base import EXTERNAL_MARKER, BaseParser

logger = logging.getLogger(__name__)

# Extensions tried in order when resolving a module specifier that lacks an
# explicit (or existing) extension.
_RESOLVE_EXTENSIONS: tuple[str, ...] = (
    ".ts",
    ".tsx",
    ".d.ts",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
)

# Index files tried when a specifier resolves to a directory.
_INDEX_FILES: tuple[str, ...] = ("index.ts", "index.tsx", "index.js")

# Extensions that, when already present on a specifier, are honoured as-is.
_KNOWN_EXTENSIONS: frozenset[str] = frozenset(
    {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts"}
)


def _strip_jsonc_comments(text: str) -> str:
    """Strip ``//`` and ``/* */`` comments from JSON-with-comments text.

    Implemented as a small character scanner (rather than a regex) so that
    comment-like sequences inside string literals — e.g. the ``/*`` in an alias
    pattern like ``"@/*"`` — are left untouched. This is config, not source.
    """
    out: list[str] = []
    i = 0
    length = len(text)
    in_string = False
    while i < length:
        char = text[i]
        if in_string:
            out.append(char)
            if char == "\\" and i + 1 < length:
                # Preserve escaped character verbatim.
                out.append(text[i + 1])
                i += 2
                continue
            if char == '"':
                in_string = False
            i += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            i += 1
            continue
        if char == "/" and i + 1 < length and text[i + 1] == "/":
            while i < length and text[i] not in "\r\n":
                i += 1
            continue
        if char == "/" and i + 1 < length and text[i + 1] == "*":
            i += 2
            while i + 1 < length and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(char)
        i += 1
    return "".join(out)


class TypeScriptParser(BaseParser):
    """Parser for TypeScript and JavaScript source files.

    Extracts ES module imports/re-exports and CommonJS ``require``/dynamic
    ``import`` specifiers, resolves project-internal imports to local file paths
    (honouring ``tsconfig.json`` ``baseUrl``/``paths`` aliases and the usual
    extension/index resolution order), and classifies bare specifiers as
    external. TypeScript has no standard library to speak of, so unresolved
    specifiers are recorded as external rather than stdlib.
    """

    language = "typescript"
    grammar = "typescript"

    def __init__(self, project_root: Path | None = None) -> None:
        super().__init__(project_root)
        # Cache of (baseUrl, paths) tsconfig data keyed by project_root, loaded
        # on first use. ``None`` means "not yet loaded".
        self._tsconfig: tuple[Path, dict[str, list[str]]] | None = None
        self._tsconfig_loaded = False

    @property
    def supported_extensions(self) -> list[str]:
        return [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts"]

    def _grammar_for(self, file_path: Path) -> str:
        """Return the tree-sitter grammar to parse ``file_path`` with.

        ``.tsx``/``.jsx`` files require the ``tsx`` grammar; everything else
        parses fine with the ``typescript`` grammar.
        """
        return "tsx" if file_path.suffix.lower() in (".tsx", ".jsx") else "typescript"

    def parse_file(self, file_path: Path, content: str) -> dict:
        """Parse a TS/JS file. See :meth:`BaseParser.parse_file`."""
        file_path = Path(file_path)
        grammar = self._grammar_for(file_path)
        try:
            root = ts_utils.parse_source(grammar, content)
        except Exception:  # pragma: no cover - defensive
            logger.exception("Failed to parse TypeScript file %s", file_path)
            return self._empty_result(file_path)

        imports: list[str] = []
        dependencies: list[str] = []
        exports: list[str] = []

        # ES module `import ... from "..."` and bare `import "..."`.
        for node in ts_utils.find_all(root, "import_statement"):
            spec = self._statement_source(node)
            if spec is not None:
                imports.append(spec)
                dependencies.append(self._resolve(spec, file_path))

        # Re-exports `export { a } from "..."` and exported symbol names.
        for node in ts_utils.find_all(root, "export_statement"):
            spec = self._statement_source(node)
            if spec is not None:
                imports.append(spec)
                dependencies.append(self._resolve(spec, file_path))
            exports.extend(self._export_names(node))

        # CommonJS `require("...")` and dynamic `import("...")`.
        for node in ts_utils.find_all(root, "call_expression"):
            spec = self._call_source(node)
            if spec is not None:
                imports.append(spec)
                dependencies.append(self._resolve(spec, file_path))

        ast_summary = {
            "function_count": len(
                ts_utils.find_all(
                    root,
                    "function_declaration",
                    "arrow_function",
                    "method_definition",
                    "function_expression",
                )
            ),
            "class_count": len(ts_utils.find_all(root, "class_declaration")),
            "import_count": len(imports),
            "complexity": ts_utils.count_branch_points(root, "typescript"),
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

    # -- specifier extraction helpers -------------------------------------

    def _statement_source(self, node) -> str | None:  # type: ignore[no-untyped-def]
        """Return the module specifier of an import/export statement, if any.

        Looks for a direct ``string`` child (the ``from "..."`` source) and
        returns its unquoted text. Returns ``None`` for statements with no
        source (e.g. ``export const x = 1``).
        """
        source = ts_utils.first_child_of_type(node, "string")
        if source is None:
            return None
        return self._string_value(source)

    def _call_source(self, node) -> str | None:  # type: ignore[no-untyped-def]
        """Return the specifier from a ``require(...)``/``import(...)`` call.

        Matches ``call_expression`` nodes whose function is the identifier
        ``require`` or the ``import`` keyword (dynamic import), returning the
        first string argument's unquoted text. Returns ``None`` otherwise.
        """
        func = node.child_by_field_name("function")
        if func is None:
            return None
        is_require = func.type == "identifier" and ts_utils.node_text(func) == "require"
        is_import = func.type == "import"
        if not (is_require or is_import):
            return None
        args = node.child_by_field_name("arguments")
        if args is None:
            return None
        first_string = ts_utils.first_child_of_type(args, "string")
        if first_string is None:
            return None
        return self._string_value(first_string)

    def _export_names(self, node) -> list[str]:  # type: ignore[no-untyped-def]
        """Best-effort extraction of exported symbol names from a statement."""
        names: list[str] = []
        for child in node.children:
            if child.type in ("function_declaration", "class_declaration"):
                name = child.child_by_field_name("name")
                if name is not None:
                    names.append(ts_utils.node_text(name))
            elif child.type in ("lexical_declaration", "variable_declaration"):
                for declarator in ts_utils.find_all(child, "variable_declarator"):
                    name = declarator.child_by_field_name("name")
                    if name is not None and name.type == "identifier":
                        names.append(ts_utils.node_text(name))
            elif child.type == "export_clause":
                for specifier in ts_utils.find_all(child, "export_specifier"):
                    name = specifier.child_by_field_name("name")
                    if name is not None:
                        names.append(ts_utils.node_text(name))

        # `export default <expr>`: record the symbol name if present, else
        # "default".
        if ts_utils.first_child_of_type(node, "default") is not None:
            default_name = self._default_export_name(node)
            names.append(default_name if default_name else "default")
        return names

    def _default_export_name(self, node) -> str | None:  # type: ignore[no-untyped-def]
        """Return the named symbol of an ``export default`` statement, if any."""
        for child in node.children:
            if child.type in ("function_declaration", "class_declaration"):
                name = child.child_by_field_name("name")
                if name is not None:
                    return ts_utils.node_text(name)
            elif child.type in ("identifier", "type_identifier"):
                return ts_utils.node_text(child)
        return None

    @staticmethod
    def _string_value(string_node) -> str:  # type: ignore[no-untyped-def]
        """Return the unquoted contents of a tree-sitter ``string`` node."""
        for child in string_node.children:
            if child.type == "string_fragment":
                return ts_utils.node_text(child)
        # Fall back to stripping the surrounding quotes (e.g. empty string).
        return ts_utils.node_text(string_node).strip("\"'`")

    # -- import resolution -------------------------------------------------

    def _resolve(self, specifier: str, file_path: Path) -> str:
        """Resolve a module specifier to a local path or :data:`EXTERNAL_MARKER`.

        Relative specifiers resolve against the importing file's directory;
        ``tsconfig`` path-alias specifiers substitute into their target under
        ``baseUrl``; everything else (bare/node_modules specifiers) is external.
        """
        if specifier.startswith(".") or specifier.startswith("/"):
            if self.project_root is None:
                return EXTERNAL_MARKER
            resolved = self._resolve_extension(file_path.parent / specifier)
            return resolved if resolved is not None else EXTERNAL_MARKER

        alias_target = self._resolve_alias(specifier)
        if alias_target is not None:
            resolved = self._resolve_extension(alias_target)
            return resolved if resolved is not None else EXTERNAL_MARKER

        # Bare specifier with no matching alias -> external dependency.
        return EXTERNAL_MARKER

    def _resolve_alias(self, specifier: str) -> Path | None:
        """Map ``specifier`` through tsconfig ``paths`` to an absolute base path.

        Returns the substituted path (without extension resolution) under
        ``baseUrl``/``project_root``, or ``None`` if no alias matches.
        """
        tsconfig = self._load_tsconfig()
        if tsconfig is None:
            return None
        base_dir, paths = tsconfig
        for pattern, targets in paths.items():
            if not targets:
                continue
            target = targets[0]
            if pattern.endswith("/*"):
                prefix = pattern[:-1]  # keep trailing slash, drop the star
                if specifier.startswith(prefix):
                    captured = specifier[len(prefix) :]
                    substituted = target[:-1] + captured if target.endswith("/*") else target
                    return base_dir / substituted
            elif specifier == pattern:
                return base_dir / target
        return None

    def _resolve_extension(self, path: Path) -> str | None:
        """Apply TS extension/index resolution to ``path`` (no extension).

        Tries an existing explicit extension, then each candidate extension,
        then index files in a directory. Returns the first existing absolute
        path, or ``None`` if nothing matches.
        """
        # Honour an explicit, existing known extension.
        if path.suffix.lower() in _KNOWN_EXTENSIONS and path.exists():
            return str(path.resolve())

        for ext in _RESOLVE_EXTENSIONS:
            candidate = path.with_name(path.name + ext)
            if candidate.exists():
                return str(candidate.resolve())

        for index in _INDEX_FILES:
            candidate = path / index
            if candidate.exists():
                return str(candidate.resolve())

        return None

    # -- tsconfig loading --------------------------------------------------

    def _load_tsconfig(self) -> tuple[Path, dict[str, list[str]]] | None:
        """Load and cache (baseUrl_dir, paths) from the nearest tsconfig.json.

        Searches ``project_root`` first, then the importing project's root only
        (per-project cache). Returns ``None`` if no tsconfig is found or the
        project root is unknown.
        """
        if self._tsconfig_loaded:
            return self._tsconfig
        self._tsconfig_loaded = True

        if self.project_root is None:
            self._tsconfig = None
            return None

        # Prefer tsconfig.json; fall back to jsconfig.json (used by Next.js and
        # other JS-only projects, with the same compilerOptions schema).
        config_path = self.project_root / "tsconfig.json"
        if not config_path.exists():
            config_path = self.project_root / "jsconfig.json"
        if not config_path.exists():
            self._tsconfig = None
            return None

        try:
            raw = config_path.read_text(encoding="utf-8")
            stripped = _strip_jsonc_comments(raw)
            data = json.loads(stripped)
        except Exception:  # pragma: no cover - defensive
            logger.exception("Failed to read TS/JS config at %s", config_path)
            self._tsconfig = None
            return None

        compiler_options = data.get("compilerOptions", {}) or {}
        base_url = compiler_options.get("baseUrl", ".") or "."
        base_dir = (self.project_root / base_url).resolve()
        raw_paths = compiler_options.get("paths", {}) or {}
        paths: dict[str, list[str]] = {
            str(key): [str(v) for v in value]
            for key, value in raw_paths.items()
            if isinstance(value, list)
        }
        self._tsconfig = (base_dir, paths)
        return self._tsconfig
