"""Directed dependency graph over source files."""

from __future__ import annotations

import logging
from typing import Any

import networkx as nx

logger = logging.getLogger(__name__)

# Marker values produced by parsers for imports that are not local files.
NON_FILE_DEPENDENCIES = frozenset({"stdlib", "external"})

# Valid edge categories (kept permissive but documented).
EDGE_TYPES = frozenset({"import", "call", "inherit", "execute"})


class DependencyGraph:
    """Directed graph representing file/module dependencies.

    Nodes are file path strings (normally absolute paths produced by the
    parsers). Each directed edge ``A -> B`` means "file A depends on file B"
    (e.g. A imports B). Node metadata (language, size, AST summary, etc.) is
    stored both as networkx node attributes and in :attr:`_file_metadata`.
    """

    def __init__(self) -> None:
        self.graph: nx.DiGraph = nx.DiGraph()
        self._file_metadata: dict[str, dict] = {}

    # -- construction ------------------------------------------------------

    def add_node(self, file_path: str, metadata: dict | None = None) -> None:
        """Add a file node with optional metadata.

        Args:
            file_path: Identifier for the file (typically an absolute path).
            metadata: Optional attributes (language, size, ast_summary, ...).
                Merged into any existing metadata for the node.
        """
        meta = dict(metadata) if metadata else {}
        if self.graph.has_node(file_path):
            existing = self._file_metadata.setdefault(file_path, {})
            existing.update(meta)
            self.graph.nodes[file_path].update(meta)
        else:
            self.graph.add_node(file_path, **meta)
            self._file_metadata[file_path] = meta

    def add_edge(self, from_file: str, to_file: str, edge_type: str = "import") -> None:
        """Add a dependency edge ``from_file -> to_file``.

        Args:
            from_file: The dependent file.
            to_file: The depended-upon file.
            edge_type: One of ``import``, ``call``, ``inherit`` or ``execute``.
                Unknown types are stored as-is but logged.
        """
        if edge_type not in EDGE_TYPES:
            logger.debug("Unknown edge_type %r between %s and %s", edge_type, from_file, to_file)
        # Ensure both endpoints exist as nodes (with at least empty metadata).
        if not self.graph.has_node(from_file):
            self.add_node(from_file)
        if not self.graph.has_node(to_file):
            self.add_node(to_file)
        self.graph.add_edge(from_file, to_file, edge_type=edge_type)

    def build_from_parsers(self, parser_results: list[dict]) -> None:
        """Populate the graph from a list of parser output dicts.

        Each dict must contain at least ``file_path`` and ``dependencies``;
        ``imports`` and ``ast_summary`` are used as metadata when present. Only
        dependencies that look like file paths (i.e. not the ``stdlib``/
        ``external`` markers) create edges.

        Args:
            parser_results: Output dicts from :meth:`BaseParser.parse_file`.
        """
        # First pass: register every node with its metadata.
        for result in parser_results:
            file_path = result.get("file_path")
            if not file_path:
                continue
            self.add_node(
                file_path,
                {
                    "language": result.get("language", "unknown"),
                    "imports": result.get("imports", []),
                    "exports": result.get("exports", []),
                    "ast_summary": result.get("ast_summary", {}),
                },
            )

        # Second pass: add edges for resolved file dependencies.
        for result in parser_results:
            file_path = result.get("file_path")
            if not file_path:
                continue
            for dep in result.get("dependencies", []):
                if dep in NON_FILE_DEPENDENCIES:
                    continue
                if dep == file_path:
                    continue  # ignore self-loops
                self.add_edge(file_path, dep, edge_type="import")

    # -- queries -----------------------------------------------------------

    def get_dependency_tree(self, root: str, max_depth: int = 3) -> dict:
        """Return a nested dependency tree rooted at ``root``.

        The format is::

            {root: {"metadata": {...}, "children": {child: {...}, ...}}}

        Traversal stops at ``max_depth`` levels and guards against cycles by not
        re-expanding a node already present on the current path.

        Args:
            root: The file path to root the tree at.
            max_depth: Maximum depth to traverse (root is depth 0).

        Returns:
            A nested dict. If ``root`` is not in the graph, the tree still
            contains the root node with empty metadata and no children.
        """

        def build(node: str, depth: int, ancestors: frozenset[str]) -> dict:
            metadata = dict(self._file_metadata.get(node, {}))
            children: dict[str, dict] = {}
            if depth < max_depth and self.graph.has_node(node):
                for child in self.graph.successors(node):
                    if child in ancestors:
                        # Cycle back-edge: record the node but do not recurse.
                        children[child] = {
                            "metadata": dict(self._file_metadata.get(child, {})),
                            "children": {},
                            "cyclic": True,
                        }
                    else:
                        children[child] = build(child, depth + 1, ancestors | {node})
            return {"metadata": metadata, "children": children}

        return {root: build(root, 0, frozenset())}

    def get_reverse_dependencies(self, file_path: str) -> list[str]:
        """Return files that depend on ``file_path`` (i.e. its predecessors).

        Args:
            file_path: The depended-upon file.

        Returns:
            A sorted list of dependent file paths (empty if none/unknown).
        """
        if not self.graph.has_node(file_path):
            return []
        return sorted(self.graph.predecessors(file_path))

    def get_critical_paths(self, limit: int = 5) -> list[list[str]]:
        """Return the longest dependency chains in the graph.

        For a DAG this is the single longest path (via
        :func:`networkx.dag_longest_path`). For a cyclic graph the strongly
        connected components are condensed into a DAG first, the longest path
        through the condensation is found, and each condensed node is expanded
        back into its member files.

        Args:
            limit: Maximum number of paths to return.

        Returns:
            A list of paths, each a list of file paths, longest first.
        """
        if self.graph.number_of_nodes() == 0:
            return []

        if nx.is_directed_acyclic_graph(self.graph):
            longest = nx.dag_longest_path(self.graph)
            return [longest] if longest else []

        # Cyclic: condense SCCs, find the longest path in the condensation,
        # then expand each super-node back into its members.
        condensation = nx.condensation(self.graph)
        try:
            super_path = nx.dag_longest_path(condensation)
        except Exception:  # pragma: no cover - defensive
            return []

        expanded: list[str] = []
        for super_node in super_path:
            members = sorted(condensation.nodes[super_node]["members"])
            expanded.extend(members)

        paths = [expanded] if expanded else []
        return paths[:limit]

    def get_centrality(self, metric: str = "betweenness") -> dict[str, float]:
        """Return centrality scores per node.

        High centrality indicates a bottleneck/hub file. Supported metrics:
        ``"betweenness"``, ``"closeness"`` and ``"degree"``.

        Args:
            metric: The centrality metric to compute.

        Returns:
            A mapping of file path to centrality score (empty for an empty
            graph).

        Raises:
            ValueError: If ``metric`` is not supported.
        """
        if self.graph.number_of_nodes() == 0:
            return {}
        if metric == "betweenness":
            return nx.betweenness_centrality(self.graph)
        if metric == "closeness":
            return nx.closeness_centrality(self.graph)
        if metric == "degree":
            return nx.degree_centrality(self.graph)
        raise ValueError(f"Unsupported centrality metric: {metric!r}")

    # -- serialization -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize the graph to a JSON-friendly dict.

        Returns:
            A dict with ``nodes`` (each with its metadata) and ``edges`` (with
            ``from``, ``to`` and ``edge_type``), plus summary counts.
        """
        nodes = [
            {"id": node, "metadata": dict(self._file_metadata.get(node, {}))}
            for node in self.graph.nodes
        ]
        edges = [
            {
                "from": u,
                "to": v,
                "edge_type": data.get("edge_type", "import"),
            }
            for u, v, data in self.graph.edges(data=True)
        ]
        return {
            "node_count": self.graph.number_of_nodes(),
            "edge_count": self.graph.number_of_edges(),
            "nodes": nodes,
            "edges": edges,
        }

    def __len__(self) -> int:
        return self.graph.number_of_nodes()
