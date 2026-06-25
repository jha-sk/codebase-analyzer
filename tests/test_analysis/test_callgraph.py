"""Tests for the 3-layer call-graph builder."""

from __future__ import annotations

from claude_codebase_analyzer.analysis.callgraph import (
    build_layered_graph,
    extract_symbols_for_files,
)
from claude_codebase_analyzer.config import AnalyzerConfig
from claude_codebase_analyzer.server import build_context, discover_files


def _build_layered(project_root):
    cfg = AnalyzerConfig.create(project_root)
    ctx = build_context(cfg)
    files = discover_files(cfg)
    syms = extract_symbols_for_files(cfg.project_root, files)
    return build_layered_graph(cfg.project_root, ctx.parser_results, syms)


def test_layers_present(python_project):
    g = _build_layered(python_project)
    d = g.to_dict()
    assert set(d["nodes"]) == {"directories", "files", "functions"}
    assert set(d["edges"]) == {"directory", "file", "function"}
    assert set(d["containment"]) == {"dir_files", "file_functions"}
    assert g.stats["files"] >= 4
    assert g.stats["functions"] >= 4


def test_directory_layer(python_project):
    g = _build_layered(python_project)
    dir_ids = {d["id"] for d in g.directories}
    assert "myapp" in dir_ids
    # Directory edges are cross-directory only (no self-loops).
    for e in g.dir_edges:
        assert e["source"] != e["target"]


def test_file_layer_has_import_edges(python_project):
    g = _build_layered(python_project)
    file_ids = {f["id"] for f in g.files}
    assert "myapp/main.py" in file_ids
    # main.py imports models.py and utils.py.
    targets = {e["target"] for e in g.file_edges if e["source"] == "myapp/main.py"}
    assert "myapp/models.py" in targets
    assert "myapp/utils.py" in targets


def test_function_layer_cross_file_call(python_project):
    g = _build_layered(python_project)
    # main.run() calls format_name() defined in utils.py -> cross-file func edge.
    edges = {(e["source"], e["target"]) for e in g.func_edges}
    assert ("myapp/main.py::run", "myapp/utils.py::format_name") in edges


def test_containment_maps(python_project):
    g = _build_layered(python_project)
    assert "myapp/main.py" in g.dir_files.get("myapp", [])
    fns = g.file_functions.get("myapp/main.py", [])
    assert any(fid.endswith("::run") for fid in fns)


def test_stats_consistency(python_project):
    g = _build_layered(python_project)
    assert g.stats["directories"] == len(g.directories)
    assert g.stats["files"] == len(g.files)
    assert g.stats["function_edges"] == len(g.func_edges)
    assert g.stats["resolved_calls"] >= 0
    assert g.stats["unresolved_calls"] >= 0


def test_works_on_go_fixture(go_project):
    g = _build_layered(go_project)
    # util<->model cycle exists at file level.
    file_edges = {(e["source"], e["target"]) for e in g.file_edges}
    has_util_to_model = any(s.endswith("util.go") and t.endswith("model.go") for s, t in file_edges)
    assert has_util_to_model
