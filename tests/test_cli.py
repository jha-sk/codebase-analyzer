"""Tests for the click CLI, exercised via click's CliRunner."""

from __future__ import annotations

import json

from click.testing import CliRunner

from claude_codebase_analyzer.cli import cli


def test_version() -> None:
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "1.0.0" in result.output


def test_cycles_command(go_project) -> None:
    result = CliRunner().invoke(cli, ["cycles", str(go_project)])
    assert result.exit_code == 0, result.output
    assert "Circular Dependencies" in result.output
    assert "util.go" in result.output


def test_risk_command(python_project) -> None:
    result = CliRunner().invoke(cli, ["risk", str(python_project), "--top-n", "3"])
    assert result.exit_code == 0, result.output
    assert "Architectural Risk Report" in result.output


def test_deps_command(typescript_project) -> None:
    target = typescript_project / "src" / "index.ts"
    result = CliRunner().invoke(
        cli,
        ["deps", str(target), "--project-root", str(typescript_project)],
    )
    assert result.exit_code == 0, result.output
    assert "Dependency Tree" in result.output


def test_workflow_command(python_project) -> None:
    wf = python_project / ".github" / "workflows" / "ci.yml"
    result = CliRunner().invoke(cli, ["workflow", str(wf)])
    assert result.exit_code == 0, result.output
    assert "Workflow Map" in result.output


def test_analyze_json(python_project) -> None:
    result = CliRunner().invoke(cli, ["analyze", str(python_project), "--format", "json"])
    assert result.exit_code == 0, result.output
    # The JSON payload is the last block; find the first '{'.
    start = result.output.index("{")
    payload = json.loads(result.output[start:])
    assert "risk" in payload
    assert "cycles" in payload


def test_graph_command(python_project, tmp_path) -> None:
    out = tmp_path / "graph.html"
    result = CliRunner().invoke(
        cli, ["graph", str(python_project), "--output", str(out), "--no-open"]
    )
    assert result.exit_code == 0, result.output
    assert "Interactive graph written to" in result.output
    assert out.is_file()
    html = out.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in html
    assert "const DATA =" in html


def test_analyze_markdown_to_file(python_project, tmp_path) -> None:
    out = tmp_path / "report.md"
    result = CliRunner().invoke(cli, ["analyze", str(python_project), "--output", str(out)])
    assert result.exit_code == 0, result.output
    assert out.is_file()
    content = out.read_text(encoding="utf-8")
    assert "Codebase Analysis Report" in content
