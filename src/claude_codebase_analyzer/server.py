"""MCP server entry point exposing four codebase-analysis tools.

The server scans the project named by the ``PROJECT_ROOT`` environment variable
on startup, builds a dependency graph, and exposes:

- ``analyze_dependencies`` — dependency tree for a file/module
- ``detect_circular_dependencies`` — circular dependency chains
- ``generate_risk_report`` — architectural risk report
- ``map_workflow`` — CI/CD workflow to code mapping

All handlers validate input and return Markdown (with Mermaid diagrams) so the
server never crashes on bad input — it returns a helpful message instead.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from .config import AnalyzerConfig

logger = logging.getLogger(__name__)

# Number of bytes inspected when sniffing for binary content.
_BINARY_SNIFF_BYTES = 1024


def _as_int(value: object, default: int) -> int:
    """Best-effort coercion of a loosely-typed argument to an int.

    Tolerates ``int``, ``float`` and numeric strings (e.g. ``"15"``); falls back
    to ``default`` for ``None`` or anything non-numeric. This lets the MCP tools
    accept stringified numbers that some clients send.

    Args:
        value: The raw argument value.
        default: Value to use when coercion is not possible.

    Returns:
        An integer.
    """
    if isinstance(value, bool):  # bool is a subclass of int; treat as invalid
        return default
    if isinstance(value, int):
        return value
    try:
        return int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


@dataclass
class ProjectContext:
    """Fully-initialized analysis state for one project.

    Attributes:
        config: The analyzer configuration.
        graph: The populated dependency graph.
        risk_analyzer: A risk analyzer bound to the graph.
        workflow_mapper: A workflow mapper bound to the project.
        parser_results: Raw per-file parser outputs.
    """

    config: AnalyzerConfig
    graph: object
    risk_analyzer: object
    workflow_mapper: object
    parser_results: list[dict]


def _matches_any(rel_posix: str, patterns: list[str]) -> bool:
    """Return whether a relative POSIX path matches any glob/exclude pattern.

    Supports both fnmatch-style patterns and the common ``dir/**`` directory
    form (which excludes everything beneath ``dir``).

    Args:
        rel_posix: Path relative to the project root, in POSIX form.
        patterns: Glob patterns to test against.

    Returns:
        ``True`` if any pattern matches.
    """
    parts = rel_posix.split("/")
    for pattern in patterns:
        if pattern.endswith("/**"):
            prefix = pattern[:-3]
            if rel_posix == prefix or rel_posix.startswith(prefix + "/"):
                return True
            # Also exclude when the directory appears anywhere in the path.
            if prefix in parts:
                return True
        if fnmatch.fnmatch(rel_posix, pattern):
            return True
        # Match the bare filename too (e.g. "*.py" against "a/b.py").
        if fnmatch.fnmatch(parts[-1], pattern):
            return True
    return False


def _looks_binary(path: Path) -> bool:
    """Heuristically detect binary files by sniffing for NUL bytes.

    Args:
        path: The file to inspect.

    Returns:
        ``True`` if the file appears to be binary.
    """
    try:
        with path.open("rb") as handle:
            chunk = handle.read(_BINARY_SNIFF_BYTES)
        return b"\x00" in chunk
    except OSError:
        return True


def discover_files(config: AnalyzerConfig) -> list[Path]:
    """Discover source files to analyze under the project root.

    Honors include/exclude patterns, the maximum file size, and skips binary
    files.

    Args:
        config: The analyzer configuration.

    Returns:
        A sorted list of absolute file paths.
    """
    root = config.project_root
    candidates: set[Path] = set()
    for pattern in config.include_patterns:
        for match in root.glob(pattern):
            if match.is_file():
                candidates.add(match)

    selected: list[Path] = []
    for path in candidates:
        rel = path.relative_to(root).as_posix()
        if _matches_any(rel, config.exclude_patterns):
            continue
        try:
            if path.stat().st_size > config.max_file_size_bytes:
                logger.debug("Skipping large file: %s", rel)
                continue
        except OSError:
            continue
        if _looks_binary(path):
            continue
        selected.append(path)

    return sorted(selected)


def build_context(config: AnalyzerConfig) -> ProjectContext:
    """Scan the project, parse files, and build the full analysis context.

    Args:
        config: The analyzer configuration.

    Returns:
        A populated :class:`ProjectContext`.
    """
    # Lazy imports keep startup light and avoid importing all grammars eagerly.
    from .analysis.risk_engine import RiskAnalyzer
    from .analysis.workflow_mapper import WorkflowMapper
    from .graph.builder import DependencyGraph
    from .parsers import get_parser_for_file

    root = config.project_root
    parser_results: list[dict] = []
    for path in discover_files(config):
        parser = get_parser_for_file(path, project_root=root)
        if parser is None:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            logger.warning("Could not read %s", path, exc_info=True)
            continue
        try:
            parser_results.append(parser.parse_file(path, content))
        except Exception:  # pragma: no cover - parser already guards internally
            logger.exception("Parser crashed on %s", path)

    graph = DependencyGraph()
    graph.build_from_parsers(parser_results)

    return ProjectContext(
        config=config,
        graph=graph,
        risk_analyzer=RiskAnalyzer(graph, root),
        workflow_mapper=WorkflowMapper(root),
        parser_results=parser_results,
    )


# --------------------------------------------------------------------------
# Path resolution for user-supplied targets
# --------------------------------------------------------------------------


def _resolve_target(ctx: ProjectContext, target: str) -> str | None:
    """Resolve a user-supplied target to a graph node key.

    Accepts absolute paths, paths relative to the project root, or bare file
    names. For directories, returns the directory's resolved path string.

    Args:
        ctx: The project context.
        target: The user-provided file or directory path.

    Returns:
        The matching graph node key (absolute path string), or ``None``.
    """
    root = ctx.config.project_root
    candidate = Path(target)
    if not candidate.is_absolute():
        candidate = root / target
    candidate = candidate.resolve()
    key = str(candidate)
    if ctx.graph.graph.has_node(key):  # type: ignore[attr-defined]
        return key
    # Fall back to matching by file name suffix across known nodes.
    target_name = Path(target).name
    for node in ctx.graph.graph.nodes:  # type: ignore[attr-defined]
        if Path(node).name == target_name:
            return node
    if candidate.exists():
        return key
    return None


# --------------------------------------------------------------------------
# Tool handlers (pure functions, reused by the CLI and the MCP layer)
# --------------------------------------------------------------------------


def handle_analyze_dependencies(ctx: ProjectContext, target: str, max_depth: int = 3) -> str:
    """Build a dependency tree report for ``target``.

    Args:
        ctx: The project context.
        target: A file or directory path.
        max_depth: Maximum tree depth (1-10).

    Returns:
        A Markdown report.
    """
    from .reporters.markdown_gen import MarkdownReporter

    max_depth = max(1, min(10, _as_int(max_depth, 3)))
    node = _resolve_target(ctx, target)
    if node is None:
        return (
            f"⚠️ Could not find `{target}` in the analyzed project. "
            "Provide a path relative to the project root, or check the file is "
            "a supported source type."
        )

    tree = ctx.graph.get_dependency_tree(node, max_depth=max_depth)  # type: ignore[attr-defined]
    reverse = ctx.graph.get_reverse_dependencies(node)  # type: ignore[attr-defined]
    report = MarkdownReporter.dependency_tree_report(tree, node)
    if reverse:
        rel = [str(Path(p).name) for p in reverse]
        report += "\n\n## Depended On By\n\n" + "\n".join(f"- `{n}`" for n in rel)
    return report


def handle_detect_cycles(ctx: ProjectContext, include_external: bool = False) -> str:
    """Detect and report circular dependencies.

    Args:
        ctx: The project context.
        include_external: Accepted for API compatibility; external/stdlib
            dependencies are never edges in the graph, so they cannot form
            cycles. The flag is noted in the report when set.

    Returns:
        A Markdown report.
    """
    from .graph.cycles import CycleDetector
    from .reporters.markdown_gen import MarkdownReporter

    cycles = CycleDetector.find_cycles(ctx.graph.graph)  # type: ignore[attr-defined]
    summary = CycleDetector.get_cycle_summary(cycles)
    breakpoints = CycleDetector.suggest_breakpoints(ctx.graph.graph, cycles)  # type: ignore[attr-defined]
    report = MarkdownReporter.cycles_report_with_breakpoints(cycles, summary, breakpoints)
    if include_external:
        report += (
            "\n\n> Note: external/stdlib dependencies are not graph edges and "
            "cannot participate in cycles, so `include_external` has no effect."
        )
    return report


def handle_generate_risk_report(
    ctx: ProjectContext, output_format: str = "markdown", top_n: int = 20
) -> str:
    """Generate the architectural risk report.

    Args:
        ctx: The project context.
        output_format: ``"markdown"`` or ``"json"``.
        top_n: Number of top-risk files (1-100).

    Returns:
        A Markdown or JSON string.
    """
    from .reporters.markdown_gen import MarkdownReporter

    top_n = max(1, min(100, _as_int(top_n, 20)))
    if output_format not in ("markdown", "json"):
        output_format = "markdown"
    results = ctx.risk_analyzer.get_top_risk_files(top_n)  # type: ignore[attr-defined]
    if output_format == "json":
        return json.dumps({"top_n": top_n, "files": results}, indent=2)
    return MarkdownReporter.risk_report(results, top_n=top_n)


def handle_map_workflow(ctx: ProjectContext, workflow_file: str | None = None) -> str:
    """Map a CI/CD workflow to code.

    Args:
        ctx: The project context.
        workflow_file: Optional path to a workflow file; auto-detected if absent.

    Returns:
        A Markdown report.
    """
    from .reporters.markdown_gen import MarkdownReporter

    mapper = ctx.workflow_mapper
    if workflow_file:
        wf_path = Path(workflow_file)
        if not wf_path.is_absolute():
            wf_path = ctx.config.project_root / workflow_file
        if not wf_path.is_file():
            return f"⚠️ Workflow file not found: `{workflow_file}`."
    else:
        candidates = mapper.find_workflow_files()  # type: ignore[attr-defined]
        if not candidates:
            return (
                "⚠️ No CI/CD workflow files detected. Supported: GitHub Actions "
                "(`.github/workflows/*.yml`), GitLab CI (`.gitlab-ci.yml`), "
                "Jenkins (`Jenkinsfile`)."
            )
        wf_path = candidates[0]

    workflow_map = mapper.map_workflow_to_code(wf_path)  # type: ignore[attr-defined]
    return MarkdownReporter.workflow_report(workflow_map)


def build_layered_graph_for(ctx: ProjectContext):  # type: ignore[no-untyped-def]
    """Build the 3-layer (directory/file/function) graph for a context.

    Re-parses the project's files for function/call symbols and combines them
    with the already-computed import dependencies.

    Args:
        ctx: The project context.

    Returns:
        A populated ``LayeredGraph``.
    """
    from .analysis.callgraph import build_layered_graph, extract_symbols_for_files

    files = discover_files(ctx.config)
    symbol_results = extract_symbols_for_files(ctx.config.project_root, files)
    return build_layered_graph(ctx.config.project_root, ctx.parser_results, symbol_results)


# --------------------------------------------------------------------------
# MCP wiring
# --------------------------------------------------------------------------

# Global context, initialized once at startup (per the single-init rule).
_context: ProjectContext | None = None


def _get_context() -> ProjectContext:
    """Return the initialized global context or raise if uninitialized."""
    if _context is None:  # pragma: no cover - defensive
        raise RuntimeError("Server context is not initialized.")
    return _context


def _build_server():  # type: ignore[no-untyped-def]
    """Construct and wire the MCP ``Server`` with its four tools.

    Returns:
        A configured ``mcp.server.Server`` instance.
    """
    from mcp.server import Server
    from mcp.types import TextContent, Tool

    server = Server("claude-codebase-analyzer")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="analyze_dependencies",
                description=(
                    "Build a dependency tree for a given file or module. Shows "
                    "what files it imports and what imports it. Use this to "
                    "understand code relationships."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "description": (
                                "Relative or absolute path to the file or directory to analyze"
                            ),
                        },
                        "max_depth": {
                            "type": "integer",
                            "description": (
                                "Maximum depth of dependency tree to traverse (default: 3)"
                            ),
                            "default": 3,
                            "minimum": 1,
                            "maximum": 10,
                        },
                    },
                    "required": ["target"],
                },
            ),
            Tool(
                name="detect_circular_dependencies",
                description=(
                    "Find all circular dependency chains in the codebase. "
                    "Circular dependencies indicate architectural debt and can "
                    "cause build failures or infinite loops."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "include_external": {
                            "type": "boolean",
                            "description": (
                                "Whether to include external dependencies in "
                                "cycle detection (default: false)"
                            ),
                            "default": False,
                        }
                    },
                },
            ),
            Tool(
                name="generate_risk_report",
                description=(
                    "Generate a comprehensive architectural risk report. Scores "
                    "each file on complexity, dependency depth, circular "
                    "dependencies, change frequency, and test coverage. "
                    "Identifies critical files that are dangerous to modify."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "format": {
                            "type": "string",
                            "enum": ["markdown", "json"],
                            "description": "Output format (default: markdown)",
                            "default": "markdown",
                        },
                        "top_n": {
                            "type": "integer",
                            "description": "Number of top-risk files to include (default: 20)",
                            "default": 20,
                            "minimum": 1,
                            "maximum": 100,
                        },
                    },
                },
            ),
            Tool(
                name="map_workflow",
                description=(
                    "Map CI/CD workflow execution to code paths. Shows which "
                    "files are executed in which pipeline stages and identifies "
                    "uncovered code. Supports GitHub Actions, GitLab CI, and "
                    "Jenkins."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "workflow_file": {
                            "type": "string",
                            "description": (
                                "Path to workflow file (e.g. "
                                ".github/workflows/ci.yml). If not provided, "
                                "auto-detects from project."
                            ),
                        }
                    },
                },
            ),
        ]

    # validate_input is disabled so the handlers can leniently coerce loosely
    # typed arguments (e.g. a stringified "15" for an integer) instead of the
    # SDK hard-rejecting the call before it reaches us.
    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        import asyncio

        arguments = arguments or {}
        ctx = _get_context()
        try:
            if name == "analyze_dependencies":
                target = arguments.get("target")
                if not target:
                    text = "⚠️ Missing required argument: `target`."
                else:
                    text = await asyncio.to_thread(
                        handle_analyze_dependencies,
                        ctx,
                        target,
                        arguments.get("max_depth", 3),
                    )
            elif name == "detect_circular_dependencies":
                text = await asyncio.to_thread(
                    handle_detect_cycles,
                    ctx,
                    bool(arguments.get("include_external", False)),
                )
            elif name == "generate_risk_report":
                text = await asyncio.to_thread(
                    handle_generate_risk_report,
                    ctx,
                    arguments.get("format", "markdown"),
                    arguments.get("top_n", 20),
                )
            elif name == "map_workflow":
                text = await asyncio.to_thread(
                    handle_map_workflow,
                    ctx,
                    arguments.get("workflow_file"),
                )
            else:
                text = f"⚠️ Unknown tool: `{name}`."
        except Exception as exc:  # never crash the server
            logger.exception("Tool %s failed", name)
            text = f"❌ Tool `{name}` failed: {exc}"

        return [TextContent(type="text", text=text)]

    return server


async def main() -> None:
    """Server entry point. Reads ``PROJECT_ROOT`` from the environment."""
    global _context

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    from mcp.server.stdio import stdio_server

    project_root = Path(os.environ.get("PROJECT_ROOT", ".")).resolve()
    logger.info("Initializing analyzer for project root: %s", project_root)
    config = AnalyzerConfig.create(project_root=project_root)
    _context = build_context(config)
    logger.info(
        "Analysis ready: %d files, %d dependency edges",
        len(_context.parser_results),
        _context.graph.graph.number_of_edges(),  # type: ignore[attr-defined]
    )

    server = _build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def run() -> None:
    """Synchronous wrapper so the CLI can launch the async server."""
    import asyncio

    asyncio.run(main())


if __name__ == "__main__":
    run()
