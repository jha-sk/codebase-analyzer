"""Tests for the Go parser."""

from __future__ import annotations

from pathlib import Path

from claude_codebase_analyzer.parsers import get_parser_for_file
from claude_codebase_analyzer.parsers.base import EXTERNAL_MARKER, STDLIB_MARKER
from claude_codebase_analyzer.parsers.go_parser import GoParser

REQUIRED_KEYS = {"file_path", "language", "imports", "exports", "dependencies", "ast_summary"}
SUMMARY_KEYS = {"function_count", "class_count", "import_count", "complexity", "line_count"}


def _parse(project_root, rel_path):
    file_path = (project_root / rel_path).resolve()
    parser = GoParser(project_root=project_root)
    return parser.parse_file(file_path, file_path.read_text(encoding="utf-8"))


def _dep_names(dependencies):
    return {Path(dep).name for dep in dependencies}


def test_supported_extensions_and_can_parse(go_project):
    parser = GoParser(project_root=go_project)
    assert parser.supported_extensions == [".go"]
    assert parser.can_parse(go_project / "main.go")
    assert not parser.can_parse(go_project / "main.py")
    assert not parser.can_parse(go_project / "README.md")


def test_get_parser_for_file_returns_go_parser(go_project):
    parser = get_parser_for_file(go_project / "main.go", project_root=go_project)
    assert isinstance(parser, GoParser)


def test_import_extraction(go_project):
    result = _parse(go_project, "main.go")
    assert "fmt" in result["imports"]
    assert "os" in result["imports"]
    assert "example.com/proj/pkg/model" in result["imports"]
    assert "example.com/proj/pkg/util" in result["imports"]


def test_resolution_stdlib_external_and_local(go_project):
    result = _parse(go_project, "pkg/util/util.go")
    deps = result["dependencies"]
    # encoding/json -> stdlib
    assert STDLIB_MARKER in deps
    # github.com/google/uuid -> external (no go.mod require resolution to file)
    assert EXTERNAL_MARKER in deps
    # local model package resolves to model.go
    assert "model.go" in _dep_names(deps)


def test_cycle_captured_at_file_level(go_project):
    util_result = _parse(go_project, "pkg/util/util.go")
    model_result = _parse(go_project, "pkg/model/model.go")

    model_go = (go_project / "pkg" / "model" / "model.go").resolve()
    util_go = (go_project / "pkg" / "util" / "util.go").resolve()

    util_resolved = {
        Path(dep).resolve() for dep in util_result["dependencies"] if Path(dep).exists()
    }
    model_resolved = {
        Path(dep).resolve() for dep in model_result["dependencies"] if Path(dep).exists()
    }

    assert model_go in util_resolved
    assert util_go in model_resolved


def test_exports_extraction(go_project):
    model_result = _parse(go_project, "pkg/model/model.go")
    assert "User" in model_result["exports"]
    assert "Display" in model_result["exports"]

    util_result = _parse(go_project, "pkg/util/util.go")
    assert "FormatName" in util_result["exports"]


def test_ast_summary_schema_and_values(go_project):
    result = _parse(go_project, "main.go")
    summary = result["ast_summary"]
    assert set(summary) == SUMMARY_KEYS
    assert summary["complexity"] >= 1
    assert summary["function_count"] >= 1
    assert summary["import_count"] >= 0
    assert summary["class_count"] >= 0
    assert summary["line_count"] >= 1


def test_malformed_source_does_not_raise(go_project):
    parser = GoParser(project_root=go_project)
    result = parser.parse_file(go_project / "broken.go", "this is not valid {{{ code")
    assert set(result) == REQUIRED_KEYS
    assert set(result["ast_summary"]) == SUMMARY_KEYS


def test_empty_string_returns_valid_result(go_project):
    parser = GoParser(project_root=go_project)
    result = parser.parse_file(go_project / "empty.go", "")
    assert set(result) == REQUIRED_KEYS
    assert set(result["ast_summary"]) == SUMMARY_KEYS
    assert result["language"] == "go"
