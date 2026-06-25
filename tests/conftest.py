"""Shared pytest fixtures for the analyzer test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_codebase_analyzer.config import AnalyzerConfig
from claude_codebase_analyzer.graph.builder import DependencyGraph
from claude_codebase_analyzer.parsers import get_parser_for_file
from claude_codebase_analyzer.server import ProjectContext, build_context

# Repo root = two levels up from this file (tests/conftest.py -> repo root).
REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_ROOT = REPO_ROOT / "test_fixtures"


@pytest.fixture(scope="session")
def fixtures_root() -> Path:
    """Absolute path to the ``test_fixtures`` directory."""
    return FIXTURES_ROOT


@pytest.fixture(scope="session")
def go_project() -> Path:
    """Absolute path to the Go fixture project."""
    return FIXTURES_ROOT / "go_project"


@pytest.fixture(scope="session")
def java_project() -> Path:
    """Absolute path to the Java fixture project."""
    return FIXTURES_ROOT / "java_project"


@pytest.fixture(scope="session")
def python_project() -> Path:
    """Absolute path to the Python fixture project."""
    return FIXTURES_ROOT / "python_project"


@pytest.fixture(scope="session")
def typescript_project() -> Path:
    """Absolute path to the TypeScript fixture project."""
    return FIXTURES_ROOT / "typescript_project"


def parse_project(project_root: Path) -> list[dict]:
    """Parse every supported source file under ``project_root``.

    Args:
        project_root: The fixture project root.

    Returns:
        A list of parser result dicts.
    """
    project_root = project_root.resolve()
    results: list[dict] = []
    for path in sorted(project_root.rglob("*")):
        if not path.is_file():
            continue
        parser = get_parser_for_file(path, project_root=project_root)
        if parser is None:
            continue
        results.append(parser.parse_file(path, path.read_text(encoding="utf-8")))
    return results


def build_graph(project_root: Path) -> DependencyGraph:
    """Build a dependency graph for a fixture project.

    Args:
        project_root: The fixture project root.

    Returns:
        A populated :class:`DependencyGraph`.
    """
    graph = DependencyGraph()
    graph.build_from_parsers(parse_project(project_root))
    return graph


def make_context(project_root: Path) -> ProjectContext:
    """Build a full :class:`ProjectContext` for a fixture project.

    Args:
        project_root: The fixture project root.

    Returns:
        A populated context.
    """
    config = AnalyzerConfig.create(project_root=project_root)
    return build_context(config)


@pytest.fixture
def python_graph(python_project: Path) -> DependencyGraph:
    """A dependency graph for the Python fixture (has a main<->utils cycle)."""
    return build_graph(python_project)


@pytest.fixture
def go_graph(go_project: Path) -> DependencyGraph:
    """A dependency graph for the Go fixture (has a util<->model cycle)."""
    return build_graph(go_project)


@pytest.fixture
def java_graph(java_project: Path) -> DependencyGraph:
    """A dependency graph for the Java fixture (Greeter<->StringHelper cycle)."""
    return build_graph(java_project)


@pytest.fixture
def typescript_graph(typescript_project: Path) -> DependencyGraph:
    """A dependency graph for the TypeScript fixture (index<->app cycle)."""
    return build_graph(typescript_project)


@pytest.fixture
def python_context(python_project: Path) -> ProjectContext:
    """A full analysis context for the Python fixture."""
    return make_context(python_project)


@pytest.fixture
def go_context(go_project: Path) -> ProjectContext:
    """A full analysis context for the Go fixture."""
    return make_context(go_project)


@pytest.fixture
def acyclic_graph() -> DependencyGraph:
    """A small hand-built acyclic graph: a.py -> b.py -> c.py."""
    graph = DependencyGraph()
    graph.add_node("a.py", {"language": "python", "ast_summary": {"complexity": 3}})
    graph.add_node("b.py", {"language": "python", "ast_summary": {"complexity": 1}})
    graph.add_node("c.py", {"language": "python", "ast_summary": {"complexity": 1}})
    graph.add_edge("a.py", "b.py")
    graph.add_edge("b.py", "c.py")
    return graph


@pytest.fixture
def cyclic_graph() -> DependencyGraph:
    """A small hand-built cyclic graph: x.py -> y.py -> z.py -> x.py."""
    graph = DependencyGraph()
    for name in ("x.py", "y.py", "z.py"):
        graph.add_node(name, {"language": "python", "ast_summary": {"complexity": 2}})
    graph.add_edge("x.py", "y.py")
    graph.add_edge("y.py", "z.py")
    graph.add_edge("z.py", "x.py")
    return graph
