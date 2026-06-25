"""Mermaid (and optional Graphviz) rendering of dependency data."""

from __future__ import annotations

import logging
from pathlib import PurePath

import networkx as nx

logger = logging.getLogger(__name__)


def _short_label(path: str) -> str:
    """Return a concise, human-readable label for a file path.

    Args:
        path: A file path (absolute or relative).

    Returns:
        The final path component, or the original string if it has none.
    """
    name = PurePath(path).name
    return name or path


class _IdAllocator:
    """Allocate stable, Mermaid-safe identifiers for arbitrary string keys."""

    def __init__(self, prefix: str = "n") -> None:
        self._prefix = prefix
        self._ids: dict[str, str] = {}

    def get(self, key: str) -> str:
        """Return (creating if needed) the Mermaid id for ``key``.

        Args:
            key: The underlying string (e.g. a file path).

        Returns:
            A stable identifier such as ``n0``, ``n1``, ...
        """
        if key not in self._ids:
            self._ids[key] = f"{self._prefix}{len(self._ids)}"
        return self._ids[key]


class MermaidVisualizer:
    """Generate Mermaid flowchart syntax from dependency structures."""

    CRITICAL_STYLE = "fill:#ff6b6b,stroke:#c92a2a,color:#fff"

    @staticmethod
    def dependency_tree_to_mermaid(tree: dict, root: str) -> str:
        """Render a dependency tree (see ``get_dependency_tree``) as Mermaid.

        Args:
            tree: The nested tree dict, ``{root: {"metadata", "children"}}``.
            root: The root file path (key into ``tree``).

        Returns:
            A Mermaid ``flowchart TD`` definition as a string.
        """
        allocator = _IdAllocator()
        lines: list[str] = ["flowchart TD"]
        edges: list[tuple[str, str]] = []
        cyclic_nodes: set[str] = set()
        declared: set[str] = set()

        def declare(node_key: str) -> str:
            node_id = allocator.get(node_key)
            if node_id not in declared:
                label = _short_label(node_key).replace('"', "'")
                lines.append(f'    {node_id}["{label}"]')
                declared.add(node_id)
            return node_id

        def visit(node_key: str, subtree: dict) -> None:
            declare(node_key)
            for child_key, child_subtree in subtree.get("children", {}).items():
                declare(child_key)
                edges.append((node_key, child_key))
                if child_subtree.get("cyclic"):
                    cyclic_nodes.add(child_key)
                else:
                    visit(child_key, child_subtree)

        root_subtree = tree.get(root, {"metadata": {}, "children": {}})
        visit(root, root_subtree)

        for parent, child in edges:
            lines.append(f"    {allocator.get(parent)} --> {allocator.get(child)}")

        # The root is the entry point and any cyclic back-edge nodes are
        # noteworthy -> highlight them (each node styled at most once).
        styled: set[str] = set()
        for node_key in [root, *cyclic_nodes]:
            node_id = allocator.get(node_key)
            if node_id not in styled:
                lines.append(f"    style {node_id} {MermaidVisualizer.CRITICAL_STYLE}")
                styled.add(node_id)

        return "\n".join(lines)

    @staticmethod
    def cycles_to_mermaid(cycles: list[list[str]]) -> str:
        """Render circular dependencies as a Mermaid diagram.

        Args:
            cycles: Cycles as returned by ``CycleDetector.find_cycles``.

        Returns:
            A Mermaid ``flowchart LR`` definition highlighting cycle nodes in
            red. Returns a minimal valid diagram when there are no cycles.
        """
        allocator = _IdAllocator()
        lines: list[str] = ["flowchart LR"]
        if not cycles:
            lines.append('    empty["No circular dependencies"]')
            return "\n".join(lines)

        declared: set[str] = set()
        styled: set[str] = set()

        for index, cycle in enumerate(cycles):
            # Group each cycle in its own subgraph for readability.
            lines.append(f"    subgraph cycle{index}[Cycle {index + 1}]")
            for node_key in cycle:
                node_id = allocator.get(node_key)
                if node_id not in declared:
                    label = _short_label(node_key).replace('"', "'")
                    lines.append(f'        {node_id}["{label}"]')
                    declared.add(node_id)
            lines.append("    end")

            for from_key, to_key in zip(cycle, cycle[1:], strict=False):
                lines.append(f"    {allocator.get(from_key)} --> {allocator.get(to_key)}")

        for cycle in cycles:
            for node_key in cycle:
                node_id = allocator.get(node_key)
                if node_id not in styled:
                    lines.append(f"    style {node_id} {MermaidVisualizer.CRITICAL_STYLE}")
                    styled.add(node_id)

        return "\n".join(lines)

    @staticmethod
    def workflow_to_mermaid(workflow_graph: nx.DiGraph) -> str:
        """Render a CI/CD workflow graph as Mermaid.

        Node shapes are chosen by each node's ``kind`` attribute:
        ``job`` -> rectangle, ``script`` -> rounded, ``file`` -> circle.

        Args:
            workflow_graph: A directed graph whose nodes carry a ``kind`` and
                optional ``label`` attribute.

        Returns:
            A Mermaid ``flowchart TD`` definition.
        """
        allocator = _IdAllocator(prefix="w")
        lines: list[str] = ["flowchart TD"]

        for node, data in workflow_graph.nodes(data=True):
            node_id = allocator.get(str(node))
            kind = data.get("kind", "file")
            label = str(data.get("label", _short_label(str(node)))).replace('"', "'")
            if kind == "job":
                lines.append(f'    {node_id}["{label}"]')
            elif kind == "script":
                lines.append(f'    {node_id}("{label}")')
            else:  # file
                lines.append(f'    {node_id}(("{label}"))')

        for u, v in workflow_graph.edges():
            lines.append(f"    {allocator.get(str(u))} --> {allocator.get(str(v))}")

        return "\n".join(lines)


class GraphvizVisualizer:
    """Generate Graphviz DOT output for optional PNG/SVG export."""

    @staticmethod
    def to_dot(graph: nx.DiGraph, highlight_nodes: list[str] | None = None) -> str:
        """Render ``graph`` as Graphviz DOT.

        Args:
            graph: The directed graph to render.
            highlight_nodes: Optional node keys to fill red.

        Returns:
            A DOT digraph definition as a string.
        """
        highlight = set(highlight_nodes or [])
        allocator = _IdAllocator()
        lines: list[str] = ["digraph dependencies {", "    rankdir=TB;"]

        for node in graph.nodes:
            node_id = allocator.get(str(node))
            label = _short_label(str(node)).replace('"', "'")
            if node in highlight:
                lines.append(f'    {node_id} [label="{label}", style=filled, fillcolor="#ff6b6b"];')
            else:
                lines.append(f'    {node_id} [label="{label}"];')

        for u, v in graph.edges():
            lines.append(f"    {allocator.get(str(u))} -> {allocator.get(str(v))};")

        lines.append("}")
        return "\n".join(lines)
