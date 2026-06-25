"""Tests for cycle detection and cycle visualization."""

from __future__ import annotations

from pathlib import Path

from claude_codebase_analyzer.graph.builder import DependencyGraph
from claude_codebase_analyzer.graph.cycles import CycleDetector
from claude_codebase_analyzer.graph.visualizer import (
    GraphvizVisualizer,
    MermaidVisualizer,
)


def _names(paths):
    """Return the set of basenames for a sequence of path strings."""
    return {Path(p).name for p in paths}


def test_find_cycles_acyclic(acyclic_graph):
    assert CycleDetector.find_cycles(acyclic_graph.graph) == []


def test_find_cycles_cyclic(cyclic_graph):
    cycles = CycleDetector.find_cycles(cyclic_graph.graph)
    assert len(cycles) == 1
    # The returned list repeats the start node; drop duplicates for the set.
    assert _names(set(cycles[0])) == {"x.py", "y.py", "z.py"}


def test_find_cycles_self_loop():
    graph = DependencyGraph()
    graph.add_node("a.py")
    graph.add_edge("a.py", "a.py")
    cycles = CycleDetector.find_cycles(graph.graph)
    assert len(cycles) == 1
    assert _names(set(cycles[0])) == {"a.py"}


def test_get_cycle_summary(python_graph):
    cycles = CycleDetector.find_cycles(python_graph.graph)
    summary = CycleDetector.get_cycle_summary(cycles)

    assert summary["cycle_count"] >= 1
    # files_in_cycles is unique and sorted.
    assert summary["files_in_cycles"] == sorted(set(summary["files_in_cycles"]))
    assert summary["largest_cycle_size"] >= 2
    assert summary["cycles_by_language"].get("python", 0) >= 1


def test_suggest_breakpoints(cyclic_graph):
    cycles = CycleDetector.find_cycles(cyclic_graph.graph)
    suggestions = CycleDetector.suggest_breakpoints(cyclic_graph.graph, cycles)

    assert len(suggestions) == len(cycles)
    for suggestion in suggestions:
        edge = suggestion["edge"]
        assert isinstance(edge, tuple)
        assert len(edge) == 2
        assert isinstance(suggestion["impact_score"], int)
        assert isinstance(suggestion["suggestion"], str)


def test_suggest_breakpoints_empty(cyclic_graph):
    assert CycleDetector.suggest_breakpoints(cyclic_graph.graph, []) == []


def test_python_fixture_cycle(python_graph):
    cycles = CycleDetector.find_cycles(python_graph.graph)
    summary = CycleDetector.get_cycle_summary(cycles)
    assert summary["cycle_count"] >= 1
    involved = _names(summary["files_in_cycles"])
    assert {"main.py", "utils.py"} <= involved


def test_go_fixture_cycle(go_graph):
    cycles = CycleDetector.find_cycles(go_graph.graph)
    summary = CycleDetector.get_cycle_summary(cycles)
    assert summary["cycle_count"] >= 1
    involved = _names(summary["files_in_cycles"])
    assert {"util.go", "model.go"} <= involved


def test_cycles_to_mermaid_real_cycle(cyclic_graph):
    cycles = CycleDetector.find_cycles(cyclic_graph.graph)
    mermaid = MermaidVisualizer.cycles_to_mermaid(cycles)
    assert mermaid.startswith("flowchart")
    assert "-->" in mermaid


def test_cycles_to_mermaid_empty():
    mermaid = MermaidVisualizer.cycles_to_mermaid([])
    assert mermaid.startswith("flowchart")
    assert "No circular dependencies" in mermaid


def test_dependency_tree_to_mermaid(acyclic_graph):
    tree = acyclic_graph.get_dependency_tree("a.py")
    mermaid = MermaidVisualizer.dependency_tree_to_mermaid(tree, "a.py")
    assert mermaid.startswith("flowchart TD")


def test_graphviz_to_dot(cyclic_graph):
    dot = GraphvizVisualizer.to_dot(cyclic_graph.graph)
    assert dot.startswith("digraph")
