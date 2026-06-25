"""Tests for the TypeScript/JavaScript parser."""

from __future__ import annotations

from pathlib import Path

from claude_codebase_analyzer.parsers import get_parser_for_file
from claude_codebase_analyzer.parsers.base import EXTERNAL_MARKER
from claude_codebase_analyzer.parsers.typescript_parser import TypeScriptParser

REQUIRED_KEYS = {"file_path", "language", "imports", "exports", "dependencies", "ast_summary"}
SUMMARY_KEYS = {"function_count", "class_count", "import_count", "complexity", "line_count"}


def _parse(project_root, rel_path):
    file_path = (project_root / rel_path).resolve()
    parser = TypeScriptParser(project_root=project_root)
    return parser.parse_file(file_path, file_path.read_text(encoding="utf-8"))


def _dep_names(dependencies):
    return {Path(dep).name for dep in dependencies}


def test_supported_extensions_and_can_parse(typescript_project):
    parser = TypeScriptParser(project_root=typescript_project)
    assert ".ts" in parser.supported_extensions
    assert ".js" in parser.supported_extensions
    assert parser.can_parse(typescript_project / "src" / "index.ts")
    assert parser.can_parse(typescript_project / "src" / "legacy.js")
    assert not parser.can_parse(typescript_project / "src" / "index.py")
    assert not parser.can_parse(typescript_project / "tsconfig.json")


def test_get_parser_for_file_returns_typescript_parser(typescript_project):
    parser = get_parser_for_file(
        typescript_project / "src" / "index.ts", project_root=typescript_project
    )
    assert isinstance(parser, TypeScriptParser)


def test_import_extraction(typescript_project):
    result = _parse(typescript_project, "src/index.ts")
    assert "lodash" in result["imports"]
    assert "@models/user" in result["imports"]
    assert "./app" in result["imports"]


def test_require_extraction_in_js(typescript_project):
    result = _parse(typescript_project, "src/legacy.js")
    assert "lodash" in result["imports"]
    assert "./utils" in result["imports"]


def test_resolution_external_and_local(typescript_project):
    result = _parse(typescript_project, "src/index.ts")
    deps = result["dependencies"]
    # lodash is a bare specifier -> external
    assert EXTERNAL_MARKER in deps
    names = _dep_names(deps)
    # ./app resolves to app.ts; @models/user alias resolves to user.ts
    assert "app.ts" in names
    assert "user.ts" in names


def test_cycle_captured_at_file_level(typescript_project):
    index_result = _parse(typescript_project, "src/index.ts")
    app_result = _parse(typescript_project, "src/app.ts")

    index_ts = (typescript_project / "src" / "index.ts").resolve()
    app_ts = (typescript_project / "src" / "app.ts").resolve()

    index_resolved = {
        Path(dep).resolve() for dep in index_result["dependencies"] if Path(dep).exists()
    }
    app_resolved = {Path(dep).resolve() for dep in app_result["dependencies"] if Path(dep).exists()}

    assert app_ts in index_resolved
    assert index_ts in app_resolved


def test_exports_extraction(typescript_project):
    index_result = _parse(typescript_project, "src/index.ts")
    assert "bootstrap" in index_result["exports"]
    assert "VERSION" in index_result["exports"]

    app_result = _parse(typescript_project, "src/app.ts")
    assert "App" in app_result["exports"]


def test_ast_summary_schema_and_values(typescript_project):
    result = _parse(typescript_project, "src/index.ts")
    summary = result["ast_summary"]
    assert set(summary) == SUMMARY_KEYS
    assert summary["complexity"] >= 1
    assert summary["function_count"] >= 1
    assert summary["import_count"] >= 0
    assert summary["class_count"] >= 0
    assert summary["line_count"] >= 1


def test_malformed_source_does_not_raise(typescript_project):
    parser = TypeScriptParser(project_root=typescript_project)
    result = parser.parse_file(typescript_project / "broken.ts", "this is not valid {{{ code")
    assert set(result) == REQUIRED_KEYS
    assert set(result["ast_summary"]) == SUMMARY_KEYS


def test_empty_string_returns_valid_result(typescript_project):
    parser = TypeScriptParser(project_root=typescript_project)
    result = parser.parse_file(typescript_project / "empty.ts", "")
    assert set(result) == REQUIRED_KEYS
    assert set(result["ast_summary"]) == SUMMARY_KEYS
    assert result["language"] == "typescript"
