"""Tests for the DependencyGraph builder."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_codebase_analyzer.graph.builder import DependencyGraph


def _names(paths):
    """Return the basenames of a sequence of path strings."""
    return [Path(p).name for p in paths]


def test_add_node_stores_metadata():
    graph = DependencyGraph()
    graph.add_node("a.py", {"language": "python", "size": 10})
    assert graph.graph.has_node("a.py")
    assert graph._file_metadata["a.py"]["language"] == "python"
    assert graph.graph.nodes["a.py"]["size"] == 10


def test_add_node_twice_merges_metadata():
    graph = DependencyGraph()
    graph.add_node("a.py", {"language": "python"})
    graph.add_node("a.py", {"size": 42})
    meta = graph._file_metadata["a.py"]
    assert meta["language"] == "python"
    assert meta["size"] == 42
    assert graph.graph.nodes["a.py"]["size"] == 42
    assert len(graph) == 1


def test_add_edge_creates_missing_endpoints():
    graph = DependencyGraph()
    graph.add_edge("a.py", "b.py")
    assert graph.graph.has_node("a.py")
    assert graph.graph.has_node("b.py")
    assert graph.graph.has_edge("a.py", "b.py")


def test_build_from_parsers_skips_non_file_markers():
    parser_results = [
        {
            "file_path": "/proj/a.py",
            "language": "python",
            "dependencies": ["stdlib", "external", "/proj/b.py"],
        },
        {
            "file_path": "/proj/b.py",
            "language": "python",
            "dependencies": [],
        },
    ]
    graph = DependencyGraph()
    graph.build_from_parsers(parser_results)

    assert not graph.graph.has_node("stdlib")
    assert not graph.graph.has_node("external")
    assert graph.graph.has_edge("/proj/a.py", "/proj/b.py")
    assert graph.graph.number_of_edges() == 1
    assert len(graph) == 2


def test_build_from_parsers_skips_self_loops():
    parser_results = [
        {
            "file_path": "/proj/a.py",
            "language": "python",
            "dependencies": ["/proj/a.py"],
        },
    ]
    graph = DependencyGraph()
    graph.build_from_parsers(parser_results)

    assert graph.graph.has_node("/proj/a.py")
    assert not graph.graph.has_edge("/proj/a.py", "/proj/a.py")
    assert graph.graph.number_of_edges() == 0


def test_get_dependency_tree_shape(acyclic_graph):
    tree = acyclic_graph.get_dependency_tree("a.py")
    assert set(tree) == {"a.py"}
    root = tree["a.py"]
    assert "metadata" in root
    assert "children" in root
    assert root["metadata"]["language"] == "python"
    assert set(root["children"]) == {"b.py"}
    b_node = root["children"]["b.py"]
    assert set(b_node["children"]) == {"c.py"}


def test_get_dependency_tree_respects_max_depth(acyclic_graph):
    tree = acyclic_graph.get_dependency_tree("a.py", max_depth=1)
    root = tree["a.py"]
    b_node = root["children"]["b.py"]
    # max_depth=1 stops expansion below b.py.
    assert b_node["children"] == {}


def test_get_dependency_tree_marks_cyclic_back_edges(cyclic_graph):
    tree = cyclic_graph.get_dependency_tree("x.py", max_depth=5)

    def find_cyclic(subtree):
        found = False
        for child in subtree.get("children", {}).values():
            if child.get("cyclic"):
                found = True
            found = found or find_cyclic(child)
        return found

    assert find_cyclic(tree["x.py"])


def test_get_reverse_dependencies(acyclic_graph):
    assert acyclic_graph.get_reverse_dependencies("c.py") == ["b.py"]


def test_get_reverse_dependencies_unknown_node(acyclic_graph):
    assert acyclic_graph.get_reverse_dependencies("missing.py") == []


def test_get_critical_paths_acyclic(acyclic_graph):
    paths = acyclic_graph.get_critical_paths()
    assert paths
    assert _names(paths[0]) == ["a.py", "b.py", "c.py"]


def test_get_critical_paths_cyclic_no_raise(cyclic_graph):
    paths = cyclic_graph.get_critical_paths()
    assert isinstance(paths, list)
    assert paths


@pytest.mark.parametrize("metric", ["betweenness", "closeness", "degree"])
def test_get_centrality_metrics(acyclic_graph, metric):
    scores = acyclic_graph.get_centrality(metric)
    assert set(scores) == {"a.py", "b.py", "c.py"}


def test_get_centrality_unsupported_metric(acyclic_graph):
    with pytest.raises(ValueError):
        acyclic_graph.get_centrality("nonsense")


def test_get_centrality_empty_graph():
    graph = DependencyGraph()
    assert graph.get_centrality("betweenness") == {}


def test_to_dict_counts(acyclic_graph):
    data = acyclic_graph.to_dict()
    assert data["node_count"] == 3
    assert data["edge_count"] == 2
    assert len(data["nodes"]) == 3
    assert len(data["edges"]) == 2


def test_len_matches_node_count(acyclic_graph):
    data = acyclic_graph.to_dict()
    assert len(acyclic_graph) == data["node_count"]
