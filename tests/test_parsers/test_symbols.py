"""Tests for function/call symbol extraction across languages."""

from __future__ import annotations

from claude_codebase_analyzer.parsers import get_parser_for_file


def _symbols(project_root, rel):
    root = project_root.resolve()
    fp = root / rel
    parser = get_parser_for_file(fp, project_root=root)
    return parser.extract_symbols(fp, fp.read_text(encoding="utf-8"))


def test_python_symbols(python_project):
    s = _symbols(python_project, "myapp/main.py")
    names = {f["name"] for f in s["functions"]}
    assert {"run", "create_app"} <= names
    calls = {(c["caller"], c["callee"]) for c in s["calls"]}
    assert ("run", "format_name") in calls
    for fn in s["functions"]:
        assert fn["start_line"] >= 1
        assert fn["end_line"] >= fn["start_line"]


def test_go_symbols(go_project):
    s = _symbols(go_project, "pkg/util/util.go")
    assert any(f["name"] == "FormatName" for f in s["functions"])
    assert any(c["caller"] == "FormatName" for c in s["calls"])


def test_typescript_symbols(typescript_project):
    s = _symbols(typescript_project, "src/app.ts")
    names = {f["name"] for f in s["functions"]}
    assert {"render", "reboot"} <= names
    calls = {(c["caller"], c["callee"]) for c in s["calls"]}
    assert ("reboot", "bootstrap") in calls


def test_java_symbols(java_project):
    s = _symbols(java_project, "src/main/java/com/example/service/Greeter.java")
    names = {f["name"] for f in s["functions"]}
    assert "greet" in names


def test_symbols_on_empty(python_project):
    parser = get_parser_for_file(
        (python_project / "myapp" / "main.py"), project_root=python_project
    )
    s = parser.extract_symbols(python_project / "myapp" / "main.py", "")
    assert s["functions"] == []
    assert s["calls"] == []
