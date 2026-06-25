"""Tests for the Markdown report generator."""

from __future__ import annotations

from claude_codebase_analyzer.analysis.risk_engine import RiskAnalyzer
from claude_codebase_analyzer.graph.cycles import CycleDetector
from claude_codebase_analyzer.reporters.markdown_gen import MarkdownReporter


def _root_node(graph, name):
    for node in graph.graph.nodes:
        if node.endswith(name):
            return node
    raise AssertionError(f"node {name} not found")


def test_dependency_tree_report(python_graph):
    root = _root_node(python_graph, "main.py")
    tree = python_graph.get_dependency_tree(root, max_depth=3)
    report = MarkdownReporter.dependency_tree_report(tree, root)
    assert "# Dependency Tree" in report
    assert "```text" in report
    assert "```mermaid" in report
    assert "Maximum depth" in report


def test_cycles_report_empty():
    report = MarkdownReporter.cycles_report([], {"cycle_count": 0})
    assert "No circular dependencies" in report


def test_cycles_report_with_cycles(python_graph):
    cycles = CycleDetector.find_cycles(python_graph.graph)
    summary = CycleDetector.get_cycle_summary(cycles)
    report = MarkdownReporter.cycles_report(cycles, summary)
    assert "# Circular Dependencies" in report
    assert "```mermaid" in report
    assert "→" in report


def test_cycles_report_with_breakpoints(python_graph):
    cycles = CycleDetector.find_cycles(python_graph.graph)
    summary = CycleDetector.get_cycle_summary(cycles)
    bps = CycleDetector.suggest_breakpoints(python_graph.graph, cycles)
    report = MarkdownReporter.cycles_report_with_breakpoints(cycles, summary, bps)
    assert "Suggested Breakpoints" in report
    # With no breakpoints, the report equals the plain cycles report.
    plain = MarkdownReporter.cycles_report_with_breakpoints(cycles, summary, [])
    assert "Suggested Breakpoints" not in plain


def test_risk_report_empty():
    report = MarkdownReporter.risk_report([])
    assert "No files were analyzed" in report


def test_risk_report_with_results(python_graph, python_project):
    analyzer = RiskAnalyzer(python_graph, python_project)
    results = analyzer.get_top_risk_files(20)
    report = MarkdownReporter.risk_report(results, top_n=20)
    assert "# Architectural Risk Report" in report
    assert "| File | Score | Level | Primary reason |" in report


def test_workflow_report():
    workflow_map = {
        "workflow_file": ".github/workflows/ci.yml",
        "platform": "github_actions",
        "jobs": [{"name": "lint", "step_count": 2, "scripts_referenced": ["a.py"]}],
        "uncovered_files": ["src/x.py"],
    }
    report = MarkdownReporter.workflow_report(workflow_map)
    assert "# CI/CD Workflow Map" in report
    assert "lint" in report
    assert "src/x.py" in report


def test_full_analysis_report(python_graph, python_project):
    root = _root_node(python_graph, "main.py")
    tree = python_graph.get_dependency_tree(root, max_depth=2)
    cycles = CycleDetector.find_cycles(python_graph.graph)
    results = RiskAnalyzer(python_graph, python_project).get_top_risk_files(10)
    report = MarkdownReporter.full_analysis_report(tree, cycles, results)
    assert "# Codebase Analysis Report" in report
    assert "Executive Summary" in report
    assert "Table of Contents" in report
