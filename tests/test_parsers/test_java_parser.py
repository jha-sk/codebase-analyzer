"""Tests for the Java parser."""

from __future__ import annotations

from pathlib import Path

from claude_codebase_analyzer.parsers import get_parser_for_file
from claude_codebase_analyzer.parsers.base import EXTERNAL_MARKER, STDLIB_MARKER
from claude_codebase_analyzer.parsers.java_parser import JavaParser

REQUIRED_KEYS = {"file_path", "language", "imports", "exports", "dependencies", "ast_summary"}
SUMMARY_KEYS = {"function_count", "class_count", "import_count", "complexity", "line_count"}

SRC = Path("src") / "main" / "java" / "com" / "example"


def _parse(project_root, rel_path):
    file_path = (project_root / rel_path).resolve()
    parser = JavaParser(project_root=project_root)
    return parser.parse_file(file_path, file_path.read_text(encoding="utf-8"))


def _dep_names(dependencies):
    return {Path(dep).name for dep in dependencies}


def test_supported_extensions_and_can_parse(java_project):
    parser = JavaParser(project_root=java_project)
    assert parser.supported_extensions == [".java"]
    assert parser.can_parse(java_project / SRC / "App.java")
    assert not parser.can_parse(java_project / "App.py")
    assert not parser.can_parse(java_project / "pom.xml")


def test_get_parser_for_file_returns_java_parser(java_project):
    parser = get_parser_for_file(java_project / SRC / "App.java", project_root=java_project)
    assert isinstance(parser, JavaParser)


def test_import_extraction(java_project):
    result = _parse(java_project, SRC / "App.java")
    assert "java.util.List" in result["imports"]
    assert "java.util.ArrayList" in result["imports"]
    assert "com.example.service.Greeter" in result["imports"]


def test_resolution_stdlib_external_and_local(java_project):
    app_result = _parse(java_project, SRC / "App.java")
    # java.util.* -> stdlib
    assert STDLIB_MARKER in app_result["dependencies"]
    # local com.example.service.Greeter -> Greeter.java
    assert "Greeter.java" in _dep_names(app_result["dependencies"])

    greeter_result = _parse(java_project, SRC / "service" / "Greeter.java")
    # org.apache... -> external
    assert EXTERNAL_MARKER in greeter_result["dependencies"]


def test_cycle_captured_at_file_level(java_project):
    greeter_result = _parse(java_project, SRC / "service" / "Greeter.java")
    helper_result = _parse(java_project, SRC / "util" / "StringHelper.java")

    greeter_java = (java_project / SRC / "service" / "Greeter.java").resolve()
    helper_java = (java_project / SRC / "util" / "StringHelper.java").resolve()

    greeter_resolved = {
        Path(dep).resolve() for dep in greeter_result["dependencies"] if Path(dep).exists()
    }
    helper_resolved = {
        Path(dep).resolve() for dep in helper_result["dependencies"] if Path(dep).exists()
    }

    assert helper_java in greeter_resolved
    assert greeter_java in helper_resolved


def test_exports_extraction(java_project):
    app_result = _parse(java_project, SRC / "App.java")
    assert "App" in app_result["exports"]

    helper_result = _parse(java_project, SRC / "util" / "StringHelper.java")
    assert "StringHelper" in helper_result["exports"]


def test_ast_summary_schema_and_values(java_project):
    result = _parse(java_project, SRC / "App.java")
    summary = result["ast_summary"]
    assert set(summary) == SUMMARY_KEYS
    assert summary["complexity"] >= 1
    assert summary["function_count"] >= 1
    assert summary["class_count"] >= 1
    assert summary["import_count"] >= 0
    assert summary["line_count"] >= 1


def test_malformed_source_does_not_raise(java_project):
    parser = JavaParser(project_root=java_project)
    result = parser.parse_file(java_project / "Broken.java", "this is not valid {{{ code")
    assert set(result) == REQUIRED_KEYS
    assert set(result["ast_summary"]) == SUMMARY_KEYS


def test_empty_string_returns_valid_result(java_project):
    parser = JavaParser(project_root=java_project)
    result = parser.parse_file(java_project / "Empty.java", "")
    assert set(result) == REQUIRED_KEYS
    assert set(result["ast_summary"]) == SUMMARY_KEYS
    assert result["language"] == "java"
