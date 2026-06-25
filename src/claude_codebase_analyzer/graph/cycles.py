"""Circular dependency detection via strongly connected components."""

from __future__ import annotations

import logging

import networkx as nx

logger = logging.getLogger(__name__)


class CycleDetector:
    """Detect and summarize circular dependencies in a dependency graph.

    Cycles are found via strongly connected components (Tarjan's algorithm, as
    implemented by :func:`networkx.strongly_connected_components`). Any SCC with
    more than one node — or a single node with a self-loop — represents a
    circular dependency.
    """

    @staticmethod
    def find_cycles(graph: nx.DiGraph) -> list[list[str]]:
        """Return circular dependency chains in ``graph``.

        Each returned cycle is a list of file paths ordered so the chain reads
        ``A -> B -> C -> A`` (the first node is repeated at the end to make the
        cycle explicit).

        Args:
            graph: The directed dependency graph.

        Returns:
            A list of cycles, each a list of file paths. Cycles are sorted by
            descending size for stable, useful output.
        """
        cycles: list[list[str]] = []
        for component in nx.strongly_connected_components(graph):
            if len(component) > 1:
                ordered = CycleDetector._order_cycle(graph, component)
                if ordered:
                    cycles.append(ordered)
            else:
                # Single-node SCC is only a cycle if it has a self-loop.
                (node,) = tuple(component)
                if graph.has_edge(node, node):
                    cycles.append([node, node])

        cycles.sort(key=len, reverse=True)
        return cycles

    @staticmethod
    def _order_cycle(graph: nx.DiGraph, component: set[str]) -> list[str]:
        """Order the nodes of an SCC into a closed walk for display.

        Args:
            graph: The directed graph.
            component: The set of nodes forming a strongly connected component.

        Returns:
            A list of nodes forming a cycle with the start node repeated at the
            end, or an empty list if no cycle could be reconstructed.
        """
        subgraph = graph.subgraph(component)
        try:
            # networkx.find_cycle returns a list of edges forming one cycle.
            edges = nx.find_cycle(subgraph)
        except nx.NetworkXNoCycle:  # pragma: no cover - SCC guarantees a cycle
            return []
        path = [u for u, _ in edges]
        path.append(edges[0][0])  # close the loop
        return path

    @staticmethod
    def get_cycle_summary(cycles: list[list[str]]) -> dict:
        """Summarize a list of cycles.

        Args:
            cycles: Cycles as returned by :meth:`find_cycles`.

        Returns:
            A dict with ``cycle_count``, ``files_in_cycles`` (unique, sorted),
            ``largest_cycle_size`` and ``cycles_by_language`` (counts keyed by
            the file extension acting as a language proxy).
        """
        files_in_cycles: set[str] = set()
        cycles_by_language: dict[str, int] = {}
        largest = 0

        for cycle in cycles:
            # Distinct files in this cycle (drop the repeated closing node).
            unique_nodes = set(cycle)
            files_in_cycles.update(unique_nodes)
            # The cycle "size" is the number of distinct files involved.
            largest = max(largest, len(unique_nodes))

            language = CycleDetector._cycle_language(cycle)
            cycles_by_language[language] = cycles_by_language.get(language, 0) + 1

        return {
            "cycle_count": len(cycles),
            "files_in_cycles": sorted(files_in_cycles),
            "largest_cycle_size": largest,
            "cycles_by_language": cycles_by_language,
        }

    @staticmethod
    def _cycle_language(cycle: list[str]) -> str:
        """Infer a language label for a cycle from its files' extensions.

        Args:
            cycle: A cycle's list of file paths.

        Returns:
            A language name (e.g. ``"go"``) or ``"mixed"``/``"unknown"``.
        """
        ext_to_lang = {
            ".go": "go",
            ".java": "java",
            ".py": "python",
            ".pyi": "python",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".js": "typescript",
            ".jsx": "typescript",
        }
        langs = set()
        for path in cycle:
            for ext, lang in ext_to_lang.items():
                if path.endswith(ext):
                    langs.add(lang)
                    break
        if not langs:
            return "unknown"
        if len(langs) == 1:
            return next(iter(langs))
        return "mixed"

    @staticmethod
    def suggest_breakpoints(graph: nx.DiGraph, cycles: list[list[str]]) -> list[dict]:
        """Suggest edges to remove to break cycles.

        Uses edge betweenness centrality as a heuristic: the edge within a cycle
        with the *lowest* betweenness is the least disruptive to remove (it lies
        on the fewest shortest paths), so it is suggested first.

        Args:
            graph: The directed dependency graph.
            cycles: Cycles as returned by :meth:`find_cycles`.

        Returns:
            A list of suggestions, each a dict with ``edge`` (``(from, to)``),
            ``impact_score`` (int; lower = less disruptive) and ``suggestion``
            (human-readable text). One suggestion per cycle.
        """
        if not cycles:
            return []

        try:
            edge_betweenness = nx.edge_betweenness_centrality(graph)
        except Exception:  # pragma: no cover - defensive
            edge_betweenness = {}

        suggestions: list[dict] = []
        for cycle in cycles:
            cycle_edges = list(zip(cycle, cycle[1:], strict=False))
            if not cycle_edges:
                continue
            # Pick the edge with the smallest betweenness (least disruptive).
            best_edge = min(
                cycle_edges,
                key=lambda e: edge_betweenness.get(e, 0.0),
            )
            score = int(round(edge_betweenness.get(best_edge, 0.0) * 100))
            from_name = best_edge[0].rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            to_name = best_edge[1].rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            suggestions.append(
                {
                    "edge": best_edge,
                    "impact_score": score,
                    "suggestion": (
                        f"Break the dependency from '{from_name}' to '{to_name}' "
                        f"(e.g. via dependency inversion, an interface, or moving "
                        f"shared code to a third module)."
                    ),
                }
            )
        return suggestions
