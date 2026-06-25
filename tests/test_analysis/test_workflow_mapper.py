"""Tests for :mod:`claude_codebase_analyzer.analysis.workflow_mapper`."""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from claude_codebase_analyzer.analysis.workflow_mapper import WorkflowMapper


def _ci_file(project_root: Path) -> Path:
    return project_root / ".github" / "workflows" / "ci.yml"


def test_detect_platform_github_actions(python_project):
    mapper = WorkflowMapper(python_project)
    assert mapper.detect_platform() == "github_actions"


def test_detect_platform_none(tmp_path):
    mapper = WorkflowMapper(tmp_path)
    assert mapper.detect_platform() is None


def test_find_workflow_files_includes_ci(python_project):
    mapper = WorkflowMapper(python_project)
    names = [p.name for p in mapper.find_workflow_files()]
    assert "ci.yml" in names


def test_parse_github_actions(python_project):
    mapper = WorkflowMapper(python_project)
    parsed = mapper.parse_github_actions(_ci_file(python_project))

    assert set(parsed.keys()) == {"name", "jobs"}
    assert "test" in parsed["jobs"]
    assert "lint" in parsed["jobs"]
    assert "scripts/check.py" in parsed["jobs"]["lint"]["scripts_referenced"]

    for step in parsed["jobs"]["lint"]["steps"]:
        assert set(step.keys()) == {"name", "run", "uses"}


def test_build_execution_graph(python_project):
    mapper = WorkflowMapper(python_project)
    parsed = mapper.parse_github_actions(_ci_file(python_project))
    graph = mapper.build_execution_graph(parsed)

    assert isinstance(graph, nx.DiGraph)
    assert graph.has_node("job:lint")
    assert graph.has_node("script:scripts/check.py")
    assert graph.has_edge("job:lint", "script:scripts/check.py")


def test_map_workflow_to_code(python_project):
    mapper = WorkflowMapper(python_project)
    result = mapper.map_workflow_to_code(_ci_file(python_project))

    assert result["platform"] == "github_actions"
    assert isinstance(result["jobs"], list)
    assert ["job:lint", "script:scripts/check.py"] in result["execution_paths"]

    assert isinstance(result["uncovered_files"], list)
    assert "myapp/main.py" in result["uncovered_files"]


def test_parse_and_map_custom_workflow(tmp_path):
    # A referenced script must exist on disk so it resolves.
    (tmp_path / "build.py").write_text("print('build')\n", encoding="utf-8")

    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    workflow_file = workflows_dir / "custom.yml"
    workflow_file.write_text(
        "name: Custom\n"
        "on: [push]\n"
        "jobs:\n"
        "  build:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: python build.py\n",
        encoding="utf-8",
    )

    mapper = WorkflowMapper(tmp_path)

    parsed = mapper.parse_github_actions(workflow_file)
    assert parsed["name"] == "Custom"
    assert "build" in parsed["jobs"]
    assert "build.py" in parsed["jobs"]["build"]["scripts_referenced"]

    result = mapper.map_workflow_to_code(workflow_file)
    assert ["job:build", "script:build.py"] in result["execution_paths"]


def test_parse_malformed_yaml(tmp_path):
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    bad = workflows_dir / "bad.yml"
    bad.write_text("name: [unclosed\n  : : :\njobs: {{{\n", encoding="utf-8")

    mapper = WorkflowMapper(tmp_path)
    parsed = mapper.parse_github_actions(bad)
    assert parsed["jobs"] == {}
    assert "name" in parsed
