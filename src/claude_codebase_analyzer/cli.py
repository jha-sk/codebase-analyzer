"""Command-line interface for the codebase analyzer.

Provides a ``server`` command (used by Claude Desktop) plus standalone analysis
commands for use without Claude.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
from colorama import Fore, Style
from colorama import init as colorama_init

from . import __version__
from .config import AnalyzerConfig


def _force_utf8_streams() -> None:
    """Reconfigure stdout/stderr to UTF-8 so emoji/box-drawing never crash.

    On Windows the default console encoding (cp1252) cannot encode the emoji and
    box-drawing characters used in reports. ``errors="replace"`` guarantees the
    CLI never raises ``UnicodeEncodeError`` even on legacy terminals.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover - platform-specific
                pass


_force_utf8_streams()
colorama_init()


def _echo_header(text: str) -> None:
    """Print a bold cyan header line.

    Args:
        text: The header text.
    """
    click.echo(f"{Fore.CYAN}{Style.BRIGHT}{text}{Style.RESET_ALL}")


def _write_output(content: str, output: str | None) -> None:
    """Write ``content`` to ``output`` or stdout.

    Args:
        content: The report text.
        output: Output file path, or ``None`` for stdout.
    """
    if output:
        Path(output).write_text(content, encoding="utf-8")
        click.echo(f"{Fore.GREEN}Report written to {output}{Style.RESET_ALL}")
    else:
        click.echo(content)


def _build(project_root: str | Path):  # type: ignore[no-untyped-def]
    """Build a project context with a friendly spinner-style message.

    Args:
        project_root: The project root to analyze.

    Returns:
        A populated ``ProjectContext``.
    """
    from .server import build_context

    config = AnalyzerConfig.create(project_root=project_root)
    _echo_header(f"Analyzing {config.project_root} ...")
    ctx = build_context(config)
    click.echo(
        f"{Fore.GREEN}Parsed {len(ctx.parser_results)} files, "
        f"{ctx.graph.graph.number_of_edges()} edges.{Style.RESET_ALL}"  # type: ignore[attr-defined]
    )
    return ctx


def _pick_root_node(ctx) -> str | None:  # type: ignore[no-untyped-def]
    """Choose a sensible root node for the full-report dependency tree.

    Picks the node with the highest total degree (the most connected file).

    Args:
        ctx: The project context.

    Returns:
        A node key, or ``None`` if the graph is empty.
    """
    graph = ctx.graph.graph
    if graph.number_of_nodes() == 0:
        return None
    return max(graph.nodes, key=lambda n: graph.in_degree(n) + graph.out_degree(n))


@click.group()
@click.version_option(version=__version__)
def cli() -> None:
    """Claude Codebase Analyzer — analyze code architecture from the command line."""


@cli.command()
@click.option(
    "--project-root",
    type=click.Path(exists=True, file_okay=False),
    default=None,
    help="Project root to analyze (defaults to the PROJECT_ROOT env var or cwd).",
)
def server(project_root: str | None) -> None:
    """Run the MCP server over stdio (used by Claude Desktop)."""
    import os

    if project_root:
        os.environ["PROJECT_ROOT"] = str(Path(project_root).resolve())
    from .server import run

    run()


@cli.command()
@click.argument("project_root", type=click.Path(exists=True, file_okay=False))
@click.option("--format", "fmt", type=click.Choice(["markdown", "json"]), default="markdown")
@click.option("--output", "-o", type=click.Path(), default=None, help="Output file path")
def analyze(project_root: str, fmt: str, output: str | None) -> None:
    """Run a full analysis on a project and output a combined report."""
    import json

    from .graph.cycles import CycleDetector
    from .reporters.markdown_gen import MarkdownReporter

    ctx = _build(project_root)
    cycles = CycleDetector.find_cycles(ctx.graph.graph)
    risk_results = ctx.risk_analyzer.get_top_risk_files(50)
    root_node = _pick_root_node(ctx)
    tree = ctx.graph.get_dependency_tree(root_node, max_depth=3) if root_node else {}

    workflow_map = None
    workflow_files = ctx.workflow_mapper.find_workflow_files()
    if workflow_files:
        workflow_map = ctx.workflow_mapper.map_workflow_to_code(workflow_files[0])

    if fmt == "json":
        payload = {
            "cycles": cycles,
            "cycle_summary": CycleDetector.get_cycle_summary(cycles),
            "risk": risk_results,
            "workflow": workflow_map,
        }
        _write_output(json.dumps(payload, indent=2), output)
        return

    report = MarkdownReporter.full_analysis_report(tree, cycles, risk_results, workflow_map)
    _write_output(report, output)


@cli.command()
@click.argument("target", type=click.Path(exists=True))
@click.option("--max-depth", default=3, type=int)
@click.option(
    "--project-root",
    type=click.Path(exists=True, file_okay=False),
    default=None,
    help="Project root for import resolution (defaults to the target's parent).",
)
def deps(target: str, max_depth: int, project_root: str | None) -> None:
    """Show the dependency tree for a file or directory."""
    from .server import handle_analyze_dependencies

    root = project_root or str(Path(target).resolve().parent)
    ctx = _build(root)
    click.echo(handle_analyze_dependencies(ctx, str(Path(target).resolve()), max_depth))


@cli.command()
@click.argument("project_root", type=click.Path(exists=True, file_okay=False))
def cycles(project_root: str) -> None:
    """Detect and display circular dependencies."""
    from .server import handle_detect_cycles

    ctx = _build(project_root)
    click.echo(handle_detect_cycles(ctx))


@cli.command()
@click.argument("project_root", type=click.Path(exists=True, file_okay=False))
@click.option("--top-n", default=20, type=int)
def risk(project_root: str, top_n: int) -> None:
    """Generate a risk report for the project."""
    from .server import handle_generate_risk_report

    ctx = _build(project_root)
    click.echo(handle_generate_risk_report(ctx, "markdown", top_n))


@cli.command()
@click.argument("project_root", type=click.Path(exists=True, file_okay=False))
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default=None,
    help="HTML output path (default: <project_root>/dependency-graph.html).",
)
@click.option(
    "--no-open",
    "no_open",
    is_flag=True,
    default=False,
    help="Do not open the generated HTML in your browser.",
)
def graph(project_root: str, output: str | None, no_open: bool) -> None:
    """Build the 3-layer interactive dependency graph as a self-contained HTML.

    Produces a modern, interactive HTML file with drill-down: click a directory
    to expand into its files, click a file to expand into its functions. The
    file is opened in your default browser unless ``--no-open`` is passed.
    """
    import webbrowser

    from .reporters.html_viz import render_layered_html
    from .server import build_layered_graph_for

    ctx = _build(project_root)
    layered = build_layered_graph_for(ctx)

    out_path = Path(output) if output else ctx.config.project_root / "dependency-graph.html"
    html = render_layered_html(layered, title=f"Dependency Graph — {ctx.config.project_root.name}")
    out_path.write_text(html, encoding="utf-8")

    s = layered.stats
    click.echo(f"{Fore.GREEN}Interactive graph written to {out_path}{Style.RESET_ALL}")
    click.echo(
        f"{Fore.CYAN}{s.get('directories', 0)} directories · "
        f"{s.get('files', 0)} files · {s.get('functions', 0)} functions. "
        f"Open it in a browser to explore all 3 layers.{Style.RESET_ALL}"
    )
    if not no_open:
        webbrowser.open(out_path.resolve().as_uri())


@cli.command()
@click.argument("workflow_file", type=click.Path(exists=True))
@click.option(
    "--project-root",
    type=click.Path(exists=True, file_okay=False),
    default=None,
    help="Project root (defaults to two levels up from the workflow file).",
)
def workflow(workflow_file: str, project_root: str | None) -> None:
    """Map a CI/CD workflow to code execution paths."""
    from .server import handle_map_workflow

    wf = Path(workflow_file).resolve()
    root = project_root or _infer_root_from_workflow(wf)
    ctx = _build(root)
    click.echo(handle_map_workflow(ctx, str(wf)))


def _infer_root_from_workflow(workflow_file: Path) -> str:
    """Infer the project root from a workflow file's location.

    Args:
        workflow_file: Absolute path to the workflow file.

    Returns:
        The inferred project-root path string.
    """
    # .github/workflows/ci.yml -> project root is three levels up.
    parts = workflow_file.parts
    if ".github" in parts:
        idx = parts.index(".github")
        return str(Path(*parts[:idx])) if idx > 0 else str(workflow_file.parent)
    return str(workflow_file.parent)


if __name__ == "__main__":
    cli()
    sys.exit(0)
