"""Tests for :mod:`claude_codebase_analyzer.analysis.risk_engine`."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_codebase_analyzer.analysis.risk_engine import RiskAnalyzer
from claude_codebase_analyzer.config import RiskThresholds

_VALID_LEVELS = {"critical", "high", "medium", "low"}
_METRIC_KEYS = {
    "cyclomatic_complexity",
    "dependency_depth",
    "in_circular_dependency",
    "change_frequency_90d",
    "test_coverage_estimate",
}


def _node_by_basename(analyzer: RiskAnalyzer, basename: str) -> str:
    """Return the graph node whose path basename matches ``basename``."""
    for node in analyzer.graph.graph.nodes:
        if Path(node).name == basename:
            return node
    raise AssertionError(f"no graph node with basename {basename!r}")


def test_calculate_file_risk_shape(python_graph, python_project):
    analyzer = RiskAnalyzer(python_graph, python_project)
    node = _node_by_basename(analyzer, "models.py")
    result = analyzer.calculate_file_risk(node)

    assert isinstance(result["risk_score"], int)
    assert 0 <= result["risk_score"] <= 100
    assert result["risk_level"] in _VALID_LEVELS
    assert set(result["metrics"].keys()) == _METRIC_KEYS
    assert isinstance(result["recommendations"], list)
    assert len(result["recommendations"]) >= 1


def test_calculate_file_risk_cycle_member(python_graph, python_project):
    analyzer = RiskAnalyzer(python_graph, python_project)
    node = _node_by_basename(analyzer, "main.py")
    result = analyzer.calculate_file_risk(node)
    assert result["metrics"]["in_circular_dependency"] is True


def test_all_files_scores_within_range(python_graph, python_project):
    analyzer = RiskAnalyzer(python_graph, python_project)
    results = analyzer.get_top_risk_files(100)
    assert results  # there should be files to score
    for result in results:
        assert 0 <= result["risk_score"] <= 100
        assert result["risk_level"] in _VALID_LEVELS


def test_get_top_risk_files_limit_and_sorted(python_graph, python_project):
    analyzer = RiskAnalyzer(python_graph, python_project)
    top = analyzer.get_top_risk_files(2)
    assert len(top) <= 2
    scores = [r["risk_score"] for r in top]
    assert scores == sorted(scores, reverse=True)


def test_get_top_risk_files_zero(python_graph, python_project):
    analyzer = RiskAnalyzer(python_graph, python_project)
    assert analyzer.get_top_risk_files(0) == []


def test_calculate_module_risk_existing(python_graph, python_project):
    analyzer = RiskAnalyzer(python_graph, python_project)
    module = analyzer.calculate_module_risk("myapp")

    assert module["module_path"]
    assert module["file_count"] > 0
    assert 0 <= module["average_risk_score"] <= 100
    assert module["risk_level"] in _VALID_LEVELS
    assert isinstance(module["critical_files"], list)


def test_calculate_module_risk_nonexistent(python_graph, python_project):
    analyzer = RiskAnalyzer(python_graph, python_project)
    module = analyzer.calculate_module_risk("does_not_exist_module")
    assert module["file_count"] == 0
    assert module["risk_level"] == "low"


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (80, "critical"),
        (100, "critical"),
        (79, "high"),
        (60, "high"),
        (40, "medium"),
        (0, "low"),
    ],
)
def test_risk_thresholds_classify_boundaries(score, expected):
    assert RiskThresholds().classify(score) == expected


def test_determinism(python_graph, python_project):
    analyzer = RiskAnalyzer(python_graph, python_project)
    node = _node_by_basename(analyzer, "models.py")
    first = analyzer.calculate_file_risk(node)
    second = analyzer.calculate_file_risk(node)
    assert first["risk_score"] == second["risk_score"]


def test_change_frequency_raises_score(python_graph, python_project):
    node_basename = "models.py"

    baseline = RiskAnalyzer(python_graph, python_project)
    node = _node_by_basename(baseline, node_basename)
    # Force zero change frequency for the baseline analyzer.
    baseline._change_cache[node] = 0
    baseline_score = baseline.calculate_file_risk(node)["risk_score"]

    churned = RiskAnalyzer(python_graph, python_project)
    churn_node = _node_by_basename(churned, node_basename)
    # Pre-seed the change cache so scoring sees a hot churn file.
    churned._change_cache[churn_node] = 20
    churned_score = churned.calculate_file_risk(churn_node)["risk_score"]

    assert churned_score >= baseline_score
