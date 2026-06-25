"""Tests for the MCP server wiring and the analysis tool handlers."""

from __future__ import annotations

import json
from pathlib import Path

import mcp.types
import pytest

import claude_codebase_analyzer.server as srv
from claude_codebase_analyzer.config import AnalyzerConfig
from claude_codebase_analyzer.server import (
    _looks_binary,
    _matches_any,
    build_context,
    discover_files,
    handle_analyze_dependencies,
    handle_detect_cycles,
    handle_generate_risk_report,
)
from tests.conftest import make_context

EXPECTED_TOOLS = {
    "analyze_dependencies",
    "detect_circular_dependencies",
    "generate_risk_report",
    "map_workflow",
}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _list_tools_handler(server):
    return server.request_handlers[mcp.types.ListToolsRequest]


def _call_tool_handler(server):
    return server.request_handlers[mcp.types.CallToolRequest]


async def _call(server, name: str, arguments: dict) -> str:
    handler = _call_tool_handler(server)
    request = mcp.types.CallToolRequest(
        method="tools/call",
        params=mcp.types.CallToolRequestParams(name=name, arguments=arguments),
    )
    result = await handler(request)
    return result.root.content[0].text


@pytest.fixture
def server(python_project: Path, monkeypatch: pytest.MonkeyPatch):
    """A built server with the module-global context pointed at the python fixture."""
    monkeypatch.setattr(srv, "_context", make_context(python_project))
    return srv._build_server()


# --------------------------------------------------------------------------
# Tool schema validation
# --------------------------------------------------------------------------


async def test_list_tools_returns_exactly_four_tools(server):
    handler = _list_tools_handler(server)
    result = await handler(mcp.types.ListToolsRequest(method="tools/list"))
    tools = result.root.tools
    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    assert len(tools) == 4


async def test_each_tool_input_schema_is_object(server):
    handler = _list_tools_handler(server)
    result = await handler(mcp.types.ListToolsRequest(method="tools/list"))
    for tool in result.root.tools:
        assert tool.inputSchema["type"] == "object"


async def test_analyze_dependencies_requires_target(server):
    handler = _list_tools_handler(server)
    result = await handler(mcp.types.ListToolsRequest(method="tools/list"))
    tool = next(t for t in result.root.tools if t.name == "analyze_dependencies")
    assert tool.inputSchema["required"] == ["target"]


# --------------------------------------------------------------------------
# call_tool execution
# --------------------------------------------------------------------------


async def test_call_detect_circular_dependencies(server):
    text = await _call(server, "detect_circular_dependencies", {})
    assert "Circular Dependencies" in text


async def test_call_generate_risk_report_markdown(server):
    text = await _call(server, "generate_risk_report", {"format": "markdown"})
    assert "Risk" in text


async def test_call_generate_risk_report_json(server):
    text = await _call(server, "generate_risk_report", {"format": "json"})
    data = json.loads(text)
    assert "files" in data


async def test_call_analyze_dependencies(server):
    text = await _call(server, "analyze_dependencies", {"target": "myapp/main.py"})
    assert "Dependency Tree" in text


async def test_call_analyze_dependencies_missing_target(server):
    text = await _call(server, "analyze_dependencies", {})
    assert "Missing" in text or "target" in text


async def test_call_map_workflow_auto_detect(server):
    text = await _call(server, "map_workflow", {})
    assert "Workflow" in text


async def test_call_unknown_tool_does_not_raise(server):
    text = await _call(server, "does_not_exist", {})
    assert "Unknown tool" in text


async def test_call_tool_internal_error_is_caught(server, monkeypatch: pytest.MonkeyPatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(srv, "handle_detect_cycles", _boom)
    text = await _call(server, "detect_circular_dependencies", {})
    assert "failed" in text


# --------------------------------------------------------------------------
# Direct handler tests (sync, faster)
# --------------------------------------------------------------------------


def test_handle_analyze_dependencies_found(python_context):
    text = handle_analyze_dependencies(python_context, "myapp/utils.py")
    assert "utils.py" in text


def test_handle_analyze_dependencies_unknown_target(python_context):
    text = handle_analyze_dependencies(python_context, "does_not_exist.py")
    assert "Could not find" in text


def test_handle_generate_risk_report_json_respects_top_n(python_context):
    text = handle_generate_risk_report(python_context, "json", 5)
    data = json.loads(text)
    assert len(data["files"]) <= 5


def test_handle_detect_cycles_include_external_note(python_context):
    text = handle_detect_cycles(python_context, include_external=True)
    assert "include_external" in text


# --------------------------------------------------------------------------
# discover_files / config behavior
# --------------------------------------------------------------------------


def test_discover_files_respects_max_file_size(tmp_path: Path):
    small = tmp_path / "small.py"
    small.write_text("x = 1\n", encoding="utf-8")
    large = tmp_path / "large.py"
    large.write_text("# pad\n" * 1000, encoding="utf-8")  # several KB

    config = AnalyzerConfig.create(tmp_path, max_file_size_bytes=50)
    discovered = {p.as_posix() for p in discover_files(config)}

    assert small.resolve().as_posix() in discovered
    assert large.resolve().as_posix() not in discovered


def test_discover_files_respects_exclude_patterns(tmp_path: Path):
    node_modules = tmp_path / "node_modules"
    node_modules.mkdir()
    excluded = node_modules / "foo.js"
    excluded.write_text("console.log(1);\n", encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    included = src / "app.py"
    included.write_text("y = 2\n", encoding="utf-8")

    config = AnalyzerConfig.create(tmp_path)
    discovered = {p.as_posix() for p in discover_files(config)}

    assert excluded.resolve().as_posix() not in discovered
    assert included.resolve().as_posix() in discovered


def test_looks_binary(tmp_path: Path):
    binary = tmp_path / "binary.bin"
    binary.write_bytes(b"abc\x00def")
    text_file = tmp_path / "text.txt"
    text_file.write_text("hello world\n", encoding="utf-8")

    assert _looks_binary(binary) is True
    assert _looks_binary(text_file) is False


def test_matches_any():
    assert _matches_any("node_modules/x.js", ["node_modules/**"]) is True
    assert _matches_any("a/b.py", ["*.py"]) is True
    assert _matches_any("a/b.py", ["*.go"]) is False


def test_build_context_on_go_project(go_project: Path):
    config = AnalyzerConfig.create(go_project)
    ctx = build_context(config)

    assert ctx.graph.graph.number_of_nodes() > 0
    assert ctx.graph.graph.number_of_edges() >= 1
    assert ctx.parser_results


# --------------------------------------------------------------------------
# Loosely-typed argument coercion (regression: stringified numbers must not
# hard-fail via the SDK's input validation).
# --------------------------------------------------------------------------


async def test_risk_report_accepts_stringified_top_n(server):
    text = await _call(server, "generate_risk_report", {"top_n": "15"})
    assert "Architectural Risk Report" in text
    assert "validation error" not in text.lower()


async def test_risk_report_accepts_float_and_bad_format(server):
    text = await _call(server, "generate_risk_report", {"top_n": 15.0, "format": "weird"})
    # Unknown format falls back to markdown, not an error.
    assert "Architectural Risk Report" in text


async def test_analyze_deps_accepts_stringified_max_depth(server):
    text = await _call(
        server, "analyze_dependencies", {"target": "myapp/main.py", "max_depth": "2"}
    )
    assert "Dependency Tree" in text


def test_as_int_coercion():
    assert srv._as_int("15", 20) == 15
    assert srv._as_int(15.0, 20) == 15
    assert srv._as_int(None, 20) == 20
    assert srv._as_int("garbage", 7) == 7
    assert srv._as_int(True, 9) == 9  # bool rejected
