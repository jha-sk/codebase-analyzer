"""Build a 3-layer dependency model: directories, files, and functions.

Layer 1 (most abstract): directory -> directory dependencies.
Layer 2: file -> file dependencies (imports plus aggregated calls).
Layer 3 (least abstract): function -> function call relationships.

The function call graph is *best-effort*. A call is resolved to a definition by,
in order: (1) a same-file function of that name, (2) a function of that name in
one of the file's resolved import dependencies, (3) a globally-unique function of
that name. Calls that cannot be resolved this way are counted as unresolved and
omitted from the graph. Dynamic dispatch and duck-typed calls are inherently not
resolvable statically.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ..parsers import get_parser_for_file
from ..parsers.ts_utils import MODULE_SCOPE

logger = logging.getLogger(__name__)

# Label used for the synthetic node representing a file's module-level scope.
_MODULE_LABEL = "(module)"


def _rel(path: str | Path, root: Path) -> str:
    """Return ``path`` relative to ``root`` in POSIX form (best-effort).

    Args:
        path: Absolute or relative path.
        root: The project root.

    Returns:
        A POSIX relative path string, or the absolute POSIX path if ``path`` is
        outside ``root``.
    """
    try:
        return Path(path).resolve().relative_to(root).as_posix()
    except ValueError:
        return Path(path).resolve().as_posix()


def _dir_of(rel_file: str) -> str:
    """Return the POSIX parent directory of a relative file path.

    Args:
        rel_file: A project-relative POSIX file path.

    Returns:
        The parent directory, or ``"."`` for files at the project root.
    """
    parent = PurePosixPath(rel_file).parent.as_posix()
    return parent if parent not in ("", ".") else "."


@dataclass
class LayeredGraph:
    """The serialized 3-layer dependency model.

    Attributes mirror the JSON consumed by the HTML renderer. Use
    :meth:`to_dict` for serialization.
    """

    project_root: str
    directories: list[dict] = field(default_factory=list)
    files: list[dict] = field(default_factory=list)
    functions: list[dict] = field(default_factory=list)
    dir_edges: list[dict] = field(default_factory=list)
    file_edges: list[dict] = field(default_factory=list)
    func_edges: list[dict] = field(default_factory=list)
    dir_files: dict[str, list[str]] = field(default_factory=dict)
    file_functions: dict[str, list[str]] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Return the model as a JSON-serializable dict."""
        return {
            "project_root": self.project_root,
            "stats": self.stats,
            "nodes": {
                "directories": self.directories,
                "files": self.files,
                "functions": self.functions,
            },
            "edges": {
                "directory": self.dir_edges,
                "file": self.file_edges,
                "function": self.func_edges,
            },
            "containment": {
                "dir_files": self.dir_files,
                "file_functions": self.file_functions,
            },
        }


class CallGraphBuilder:
    """Construct a :class:`LayeredGraph` from parser output and symbol data.

    Args:
        project_root: Absolute path to the project root.
    """

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root).resolve()

    def build(self, parser_results: list[dict], symbol_results: list[dict]) -> LayeredGraph:
        """Build the 3-layer model.

        Args:
            parser_results: Per-file parser output (provides import deps,
                ``file_path``, ``language``).
            symbol_results: Per-file symbol output from
                :meth:`BaseParser.extract_symbols`.

        Returns:
            A populated :class:`LayeredGraph`.
        """
        root = self.project_root
        symbols_by_file = {_rel(s["file_path"], root): s for s in symbol_results}
        languages = {
            _rel(r["file_path"], root): r.get("language", "unknown") for r in parser_results
        }
        # Resolved import dependencies (real files only), keyed by relative file.
        deps_by_file: dict[str, list[str]] = {}
        all_files = set(languages)
        for r in parser_results:
            rel_file = _rel(r["file_path"], root)
            deps: list[str] = []
            for dep in r.get("dependencies", []):
                if dep in ("stdlib", "external"):
                    continue
                rel_dep = _rel(dep, root)
                if rel_dep in all_files and rel_dep != rel_file:
                    deps.append(rel_dep)
            deps_by_file[rel_file] = deps

        # Index function names per file and globally.
        funcs_by_file = self._index_functions(symbols_by_file)
        global_name_index: dict[str, set[str]] = {}
        for rel_file, names in funcs_by_file.items():
            for name in names:
                global_name_index.setdefault(name, set()).add(rel_file)

        # Build function-level edges by resolving call sites.
        func_edge_weights: dict[tuple[str, str], int] = {}
        module_nodes_used: set[str] = set()
        resolved = 0
        unresolved = 0

        for rel_file, sym in symbols_by_file.items():
            local_names = funcs_by_file.get(rel_file, set())
            for call in sym.get("calls", []):
                callee = call["callee"]
                target_file = self._resolve_callee(
                    callee,
                    rel_file,
                    local_names,
                    deps_by_file.get(rel_file, []),
                    funcs_by_file,
                    global_name_index,
                )
                if target_file is None:
                    unresolved += 1
                    continue
                resolved += 1
                source_id = self._caller_id(rel_file, call["caller"])
                if call["caller"] == MODULE_SCOPE:
                    module_nodes_used.add(rel_file)
                target_id = f"{target_file}::{callee}"
                if source_id == target_id:
                    continue
                key = (source_id, target_id)
                func_edge_weights[key] = func_edge_weights.get(key, 0) + 1

        # Materialize layers.
        self._materialize(
            funcs_by_file,
            symbols_by_file,
            languages,
            deps_by_file,
            func_edge_weights,
            module_nodes_used,
        )
        graph = self._graph
        graph.stats = {
            "directories": len(graph.directories),
            "files": len(graph.files),
            "functions": len(graph.functions),
            "directory_edges": len(graph.dir_edges),
            "file_edges": len(graph.file_edges),
            "function_edges": len(graph.func_edges),
            "resolved_calls": resolved,
            "unresolved_calls": unresolved,
        }
        return graph

    # -- internals ---------------------------------------------------------

    def _index_functions(self, symbols_by_file: dict[str, dict]) -> dict[str, set[str]]:
        """Map each file to the set of function names it defines."""
        out: dict[str, set[str]] = {}
        for rel_file, sym in symbols_by_file.items():
            out[rel_file] = {f["name"] for f in sym.get("functions", [])}
        return out

    def _caller_id(self, rel_file: str, caller: str) -> str:
        """Return the function-node id for a call's caller."""
        return f"{rel_file}::{caller}"

    def _resolve_callee(
        self,
        callee: str,
        rel_file: str,
        local_names: set[str],
        dep_files: list[str],
        funcs_by_file: dict[str, set[str]],
        global_index: dict[str, set[str]],
    ) -> str | None:
        """Resolve a callee name to the file that defines it (best-effort).

        Args:
            callee: The called function's simple name.
            rel_file: The calling file.
            local_names: Function names defined in ``rel_file``.
            dep_files: ``rel_file``'s resolved import dependency files.
            funcs_by_file: Map of file -> function names.
            global_index: Map of function name -> files defining it.

        Returns:
            The defining file's relative path, or ``None`` if unresolved.
        """
        if callee in local_names:
            return rel_file
        for dep in sorted(dep_files):
            if callee in funcs_by_file.get(dep, set()):
                return dep
        defining = global_index.get(callee, set())
        if len(defining) == 1:
            return next(iter(defining))
        return None

    def _materialize(
        self,
        funcs_by_file: dict[str, set[str]],
        symbols_by_file: dict[str, dict],
        languages: dict[str, str],
        deps_by_file: dict[str, list[str]],
        func_edge_weights: dict[tuple[str, str], int],
        module_nodes_used: set[str],
    ) -> None:
        """Assemble all node lists, edge lists, and containment maps."""
        graph = LayeredGraph(project_root=str(self.project_root))

        # --- Function nodes -------------------------------------------------
        func_start: dict[str, int] = {}
        for rel_file, sym in symbols_by_file.items():
            for f in sym.get("functions", []):
                func_start[f"{rel_file}::{f['name']}"] = f["start_line"]

        function_ids: set[str] = set()
        for rel_file, names in funcs_by_file.items():
            for name in names:
                function_ids.add(f"{rel_file}::{name}")
        for rel_file in module_nodes_used:
            function_ids.add(f"{rel_file}::{MODULE_SCOPE}")

        for fid in sorted(function_ids):
            rel_file, _, name = fid.partition("::")
            graph.functions.append(
                {
                    "id": fid,
                    "label": _MODULE_LABEL if name == MODULE_SCOPE else name,
                    "file": rel_file,
                    "dir": _dir_of(rel_file),
                    "start_line": func_start.get(fid, 0),
                }
            )

        # --- Function edges -------------------------------------------------
        for (src, dst), weight in sorted(func_edge_weights.items()):
            graph.func_edges.append({"source": src, "target": dst, "weight": weight})

        # --- File nodes -----------------------------------------------------
        for rel_file in sorted(languages):
            graph.files.append(
                {
                    "id": rel_file,
                    "label": PurePosixPath(rel_file).name,
                    "dir": _dir_of(rel_file),
                    "language": languages[rel_file],
                    "func_count": len(funcs_by_file.get(rel_file, set())),
                }
            )

        # --- File edges (imports unioned with aggregated calls) -------------
        file_edge: dict[tuple[str, str], dict] = {}
        for rel_file, deps in deps_by_file.items():
            for dep in deps:
                key = (rel_file, dep)
                file_edge.setdefault(key, {"weight": 0, "import": False, "call": False})
                file_edge[key]["weight"] += 1
                file_edge[key]["import"] = True
        for (src, dst), weight in func_edge_weights.items():
            src_file = src.split("::", 1)[0]
            dst_file = dst.split("::", 1)[0]
            if src_file == dst_file:
                continue
            key = (src_file, dst_file)
            file_edge.setdefault(key, {"weight": 0, "import": False, "call": False})
            file_edge[key]["weight"] += weight
            file_edge[key]["call"] = True
        for (src, dst), info in sorted(file_edge.items()):
            kind = (
                "both"
                if info["import"] and info["call"]
                else ("import" if info["import"] else "call")
            )
            graph.file_edges.append(
                {"source": src, "target": dst, "weight": info["weight"], "kind": kind}
            )

        # --- Directory nodes ------------------------------------------------
        dir_files: dict[str, list[str]] = {}
        for rel_file in sorted(languages):
            dir_files.setdefault(_dir_of(rel_file), []).append(rel_file)
        for directory, members in dir_files.items():
            func_total = sum(len(funcs_by_file.get(m, set())) for m in members)
            graph.directories.append(
                {
                    "id": directory,
                    "label": PurePosixPath(directory).name or directory,
                    "file_count": len(members),
                    "func_count": func_total,
                }
            )
        graph.dir_files = dir_files

        # --- Directory edges (cross-directory only) ------------------------
        dir_edge: dict[tuple[str, str], int] = {}
        for edge in graph.file_edges:
            src_dir = _dir_of(edge["source"])
            dst_dir = _dir_of(edge["target"])
            if src_dir == dst_dir:
                continue
            key = (src_dir, dst_dir)
            dir_edge[key] = dir_edge.get(key, 0) + edge["weight"]
        for (src, dst), weight in sorted(dir_edge.items()):
            graph.dir_edges.append({"source": src, "target": dst, "weight": weight})

        # --- Containment: file -> functions --------------------------------
        file_functions: dict[str, list[str]] = {}
        for fid in sorted(function_ids):
            rel_file = fid.split("::", 1)[0]
            file_functions.setdefault(rel_file, []).append(fid)
        graph.file_functions = file_functions

        self._graph = graph


def build_layered_graph(
    project_root: Path, parser_results: list[dict], symbol_results: list[dict]
) -> LayeredGraph:
    """Convenience wrapper around :class:`CallGraphBuilder`.

    Args:
        project_root: Absolute path to the project root.
        parser_results: Per-file parser output.
        symbol_results: Per-file symbol output.

    Returns:
        The populated :class:`LayeredGraph`.
    """
    return CallGraphBuilder(project_root).build(parser_results, symbol_results)


def extract_symbols_for_files(project_root: Path, file_paths: list[Path]) -> list[dict]:
    """Run symbol extraction over the given files.

    Args:
        project_root: The project root (for parser import resolution).
        file_paths: Absolute paths to source files.

    Returns:
        A list of symbol-result dicts (one per parseable file).
    """
    project_root = Path(project_root).resolve()
    results: list[dict] = []
    for path in file_paths:
        parser = get_parser_for_file(path, project_root=project_root)
        if parser is None:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        results.append(parser.extract_symbols(path, content))
    return results
