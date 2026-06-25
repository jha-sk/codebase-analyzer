"""Tests for the Python parser."""

from __future__ import annotations

from pathlib import Path

from claude_codebase_analyzer.parsers import get_parser_for_file
from claude_codebase_analyzer.parsers.base import STDLIB_MARKER
from claude_codebase_analyzer.parsers.python_parser import PythonParser

REQUIRED_KEYS = {"file_path", "language", "imports", "exports", "dependencies", "ast_summary"}
SUMMARY_KEYS = {"function_count", "class_count", "import_count", "complexity", "line_count"}


def _parse(project_root, rel_path):
    file_path = (project_root / rel_path).resolve()
    parser = PythonParser(project_root=project_root)
    return parser.parse_file(file_path, file_path.read_text(encoding="utf-8"))


def _dep_names(dependencies):
    return {Path(dep).name for dep in dependencies}


def test_supported_extensions_and_can_parse(python_project):
    parser = PythonParser(project_root=python_project)
    assert parser.supported_extensions == [".py", ".pyi"]
    assert parser.can_parse(python_project / "myapp" / "main.py")
    assert not parser.can_parse(python_project / "myapp" / "main.go")
    assert not parser.can_parse(python_project / "pyproject.toml")


def test_get_parser_for_file_returns_python_parser(python_project):
    parser = get_parser_for_file(python_project / "myapp" / "main.py", project_root=python_project)
    assert isinstance(parser, PythonParser)


def test_import_extraction(python_project):
    result = _parse(python_project, "myapp/main.py")
    assert "os" in result["imports"]
    assert "sys" in result["imports"]
    # `from dataclasses import dataclass` is shown as the module name.
    assert "dataclasses" in result["imports"]
    assert ".models" in result["imports"]
    assert ".utils" in result["imports"]


def test_resolution_stdlib_and_local(python_project):
    result = _parse(python_project, "myapp/main.py")
    deps = result["dependencies"]
    # os/sys/dataclasses -> stdlib
    assert STDLIB_MARKER in deps
    # local .models and .utils resolve to files
    names = _dep_names(deps)
    assert "models.py" in names
    assert "utils.py" in names


def test_cycle_captured_at_file_level(python_project):
    main_result = _parse(python_project, "myapp/main.py")
    utils_result = _parse(python_project, "myapp/utils.py")

    main_py = (python_project / "myapp" / "main.py").resolve()
    utils_py = (python_project / "myapp" / "utils.py").resolve()

    main_resolved = {
        Path(dep).resolve() for dep in main_result["dependencies"] if Path(dep).exists()
    }
    utils_resolved = {
        Path(dep).resolve() for dep in utils_result["dependencies"] if Path(dep).exists()
    }

    assert utils_py in main_resolved
    assert main_py in utils_resolved


def test_exports_extraction(python_project):
    main_result = _parse(python_project, "myapp/main.py")
    assert "App" in main_result["exports"]
    assert "create_app" in main_result["exports"]

    models_result = _parse(python_project, "myapp/models.py")
    assert "User" in models_result["exports"]


def test_ast_summary_schema_and_values(python_project):
    result = _parse(python_project, "myapp/main.py")
    summary = result["ast_summary"]
    assert set(summary) == SUMMARY_KEYS
    assert summary["complexity"] >= 1
    assert summary["function_count"] >= 1
    assert summary["class_count"] >= 1
    assert summary["import_count"] >= 0
    assert summary["line_count"] >= 1


def test_malformed_source_does_not_raise(python_project):
    parser = PythonParser(project_root=python_project)
    result = parser.parse_file(python_project / "broken.py", "this is not valid {{{ code")
    assert set(result) == REQUIRED_KEYS
    assert set(result["ast_summary"]) == SUMMARY_KEYS


def test_empty_string_returns_valid_result(python_project):
    parser = PythonParser(project_root=python_project)
    result = parser.parse_file(python_project / "empty.py", "")
    assert set(result) == REQUIRED_KEYS
    assert set(result["ast_summary"]) == SUMMARY_KEYS
    assert result["language"] == "python"
