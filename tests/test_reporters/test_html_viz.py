"""Tests for the interactive HTML renderer and the layered text renderer."""

from __future__ import annotations

from claude_codebase_analyzer.analysis.callgraph import (
    build_layered_graph,
    extract_symbols_for_files,
)
from claude_codebase_analyzer.config import AnalyzerConfig
from claude_codebase_analyzer.reporters.html_viz import render_layered_html
from claude_codebase_analyzer.server import build_context, discover_files


def _layered(project_root):
    cfg = AnalyzerConfig.create(project_root)
    ctx = build_context(cfg)
    files = discover_files(cfg)
    syms = extract_symbols_for_files(cfg.project_root, files)
    return build_layered_graph(cfg.project_root, ctx.parser_results, syms)


def test_html_is_self_contained(python_project):
    g = _layered(python_project)
    html = render_layered_html(g, title="Test Graph")
    # No external scripts/styles -> self-contained.
    assert "<!DOCTYPE html>" in html
    assert "http://" not in html.replace("http://www.w3.org/2000/svg", "")
    assert "src=" not in html  # no external <script src> / <img src>
    assert "Test Graph" in html


def test_html_embeds_layer_data(python_project):
    g = _layered(python_project)
    html = render_layered_html(g)
    # The embedded DATA must carry all three layers + containment.
    assert "const DATA =" in html
    assert '"directories"' in html
    assert '"function"' in html
    assert '"containment"' in html
    assert "myapp/main.py" in html


def test_html_title_is_escaped():
    from claude_codebase_analyzer.analysis.callgraph import LayeredGraph

    g = LayeredGraph(project_root="x")
    html = render_layered_html(g, title="<script>bad</script>")
    assert "<script>bad" not in html
    assert "&lt;script&gt;bad" in html


def test_html_has_interactive_controls(python_project):
    g = _layered(python_project)
    html = render_layered_html(g)
    # The renderer ships drill-down controls and the SVG stage.
    assert 'id="svg"' in html
    assert "setView" in html
    assert "buildDirView" in html and "buildFileView" in html and "buildFuncView" in html
