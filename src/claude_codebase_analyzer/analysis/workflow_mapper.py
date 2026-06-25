"""Map CI/CD workflow files to the code they execute."""

from __future__ import annotations

import logging
import shlex
from pathlib import Path
from typing import Any

import networkx as nx

logger = logging.getLogger(__name__)

# File extensions treated as "scripts" that can be referenced from a CI step.
_SCRIPT_EXTENSIONS = frozenset(
    {".py", ".sh", ".bash", ".js", ".ts", ".rb", ".go", ".ps1", ".bat", ".mjs"}
)

# Source extensions used when computing CI coverage of the codebase.
_SOURCE_EXTENSIONS = frozenset({".py", ".go", ".java", ".ts", ".tsx", ".js", ".jsx"})

_PRUNE_DIRS = frozenset(
    {"node_modules", "vendor", ".git", "dist", "build", ".venv", "venv", "__pycache__"}
)


class WorkflowMapper:
    """Map CI/CD workflow files to code execution paths.

    Args:
        project_root: Absolute path to the project root.
    """

    SUPPORTED_PLATFORMS = ["github_actions", "gitlab_ci", "jenkins"]

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root).resolve()

    # -- detection ---------------------------------------------------------

    def detect_platform(self) -> str | None:
        """Detect the CI/CD platform from well-known config files.

        Returns:
            ``"github_actions"``, ``"gitlab_ci"``, ``"jenkins"`` or ``None``.
        """
        workflows_dir = self.project_root / ".github" / "workflows"
        if workflows_dir.is_dir() and any(
            p.suffix in (".yml", ".yaml") for p in workflows_dir.iterdir()
        ):
            return "github_actions"
        if (self.project_root / ".gitlab-ci.yml").is_file():
            return "gitlab_ci"
        if (self.project_root / "Jenkinsfile").is_file():
            return "jenkins"
        return None

    def find_workflow_files(self) -> list[Path]:
        """Return all detectable workflow files in the project.

        Returns:
            A list of workflow file paths (may be empty).
        """
        files: list[Path] = []
        workflows_dir = self.project_root / ".github" / "workflows"
        if workflows_dir.is_dir():
            files.extend(
                sorted(p for p in workflows_dir.iterdir() if p.suffix in (".yml", ".yaml"))
            )
        gitlab = self.project_root / ".gitlab-ci.yml"
        if gitlab.is_file():
            files.append(gitlab)
        jenkins = self.project_root / "Jenkinsfile"
        if jenkins.is_file():
            files.append(jenkins)
        return files

    # -- parsing -----------------------------------------------------------

    def parse_github_actions(self, workflow_file: Path) -> dict:
        """Parse a GitHub Actions workflow YAML file.

        Args:
            workflow_file: Path to a ``.github/workflows/*.yml`` file.

        Returns:
            A structured dict::

                {
                  "name": str,
                  "jobs": {
                    job_name: {
                      "steps": [{"name", "run", "uses"}],
                      "scripts_referenced": [str],
                    }
                  }
                }
        """
        import yaml  # local import keeps PyYAML out of the import-time path

        try:
            data = yaml.safe_load(workflow_file.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            logger.exception("Failed to parse workflow %s", workflow_file)
            return {"name": workflow_file.name, "jobs": {}}

        name = str(data.get("name", workflow_file.name))
        jobs_out: dict[str, dict] = {}
        jobs = data.get("jobs", {}) or {}
        if isinstance(jobs, dict):
            for job_name, job in jobs.items():
                jobs_out[str(job_name)] = self._parse_job(job)

        return {"name": name, "jobs": jobs_out}

    def _parse_job(self, job: Any) -> dict:
        """Parse a single GitHub Actions job mapping.

        Args:
            job: The job mapping from the YAML document.

        Returns:
            A dict with ``steps`` and ``scripts_referenced``.
        """
        steps_out: list[dict] = []
        scripts: list[str] = []
        if not isinstance(job, dict):
            return {"steps": [], "scripts_referenced": []}

        for step in job.get("steps", []) or []:
            if not isinstance(step, dict):
                continue
            run = step.get("run")
            uses = step.get("uses")
            steps_out.append(
                {
                    "name": step.get("name"),
                    "run": run,
                    "uses": uses,
                }
            )
            if isinstance(run, str):
                scripts.extend(self._extract_scripts(run))

        # De-duplicate scripts, preserving order.
        seen: set[str] = set()
        unique_scripts: list[str] = []
        for script in scripts:
            if script not in seen:
                seen.add(script)
                unique_scripts.append(script)
        return {"steps": steps_out, "scripts_referenced": unique_scripts}

    def _extract_scripts(self, run_command: str) -> list[str]:
        """Extract local script paths referenced in a ``run:`` command.

        A token is considered a local script if it resolves to an existing file
        under the project root and has a script-like extension.

        Args:
            run_command: The shell command(s) from a ``run`` step.

        Returns:
            Project-relative paths (POSIX style) of referenced scripts.
        """
        found: list[str] = []
        for line in run_command.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                tokens = shlex.split(line, posix=True)
            except ValueError:
                tokens = line.split()
            for raw in tokens:
                token = raw.strip("'\"")
                if "/" not in token and Path(token).suffix not in _SCRIPT_EXTENSIONS:
                    continue
                if Path(token).suffix not in _SCRIPT_EXTENSIONS:
                    continue
                candidate = (self.project_root / token).resolve()
                if candidate.is_file() and _is_within(candidate, self.project_root):
                    found.append(candidate.relative_to(self.project_root).as_posix())
        return found

    # -- graph + mapping ---------------------------------------------------

    def build_execution_graph(self, workflow_data: dict) -> nx.DiGraph:
        """Build a job -> script -> file execution graph from parsed data.

        Args:
            workflow_data: Output of :meth:`parse_github_actions`.

        Returns:
            A directed graph whose nodes carry ``kind`` (``job``/``script``/
            ``file``) and ``label`` attributes (suitable for the visualizer).
        """
        graph: nx.DiGraph = nx.DiGraph()
        for job_name, job in workflow_data.get("jobs", {}).items():
            job_node = f"job:{job_name}"
            graph.add_node(job_node, kind="job", label=job_name)
            for script in job.get("scripts_referenced", []):
                script_node = f"script:{script}"
                graph.add_node(script_node, kind="script", label=Path(script).name)
                graph.add_edge(job_node, script_node)
        return graph

    def map_workflow_to_code(self, workflow_file: Path) -> dict:
        """Run the full pipeline: parse, build graph, trace code paths.

        Args:
            workflow_file: Path to the workflow file to map.

        Returns:
            A dict with ``workflow_file`` (relative), ``platform``, ``jobs``,
            ``execution_paths`` (chains of job/script identifiers) and
            ``uncovered_files`` (source files never referenced by CI).
        """
        platform = self.detect_platform() or "github_actions"
        workflow_data = self.parse_github_actions(workflow_file)
        exec_graph = self.build_execution_graph(workflow_data)

        # Execution paths: each script reachable from each job.
        execution_paths: list[list[str]] = []
        referenced_files: set[str] = set()
        for job_name, job in workflow_data.get("jobs", {}).items():
            for script in job.get("scripts_referenced", []):
                execution_paths.append([f"job:{job_name}", f"script:{script}"])
                referenced_files.add(script)

        uncovered = self._uncovered_files(referenced_files)

        jobs_list = [
            {
                "name": job_name,
                "step_count": len(job.get("steps", [])),
                "scripts_referenced": job.get("scripts_referenced", []),
            }
            for job_name, job in workflow_data.get("jobs", {}).items()
        ]

        return {
            "workflow_file": self._relativize(workflow_file),
            "workflow_name": workflow_data.get("name"),
            "platform": platform,
            "jobs": jobs_list,
            "execution_graph_nodes": exec_graph.number_of_nodes(),
            "execution_paths": execution_paths,
            "uncovered_files": uncovered,
        }

    def _uncovered_files(self, referenced: set[str]) -> list[str]:
        """Return project source files not referenced by any CI script.

        Args:
            referenced: Project-relative POSIX paths referenced in CI.

        Returns:
            A sorted list of project-relative source paths not referenced.
        """
        uncovered: list[str] = []
        for path in self._iter_source_files():
            rel = path.relative_to(self.project_root).as_posix()
            if rel not in referenced:
                uncovered.append(rel)
        return sorted(uncovered)

    def _iter_source_files(self) -> list[Path]:
        """Walk the project for source files, pruning vendored/build dirs."""
        results: list[Path] = []
        for path in self.project_root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in _SOURCE_EXTENSIONS:
                continue
            if any(part in _PRUNE_DIRS for part in path.relative_to(self.project_root).parts):
                continue
            results.append(path)
        return results

    def _relativize(self, path: Path) -> str:
        """Return ``path`` relative to the project root when possible."""
        try:
            return Path(path).resolve().relative_to(self.project_root).as_posix()
        except ValueError:
            return str(path)


def _is_within(path: Path, directory: Path) -> bool:
    """Return whether ``path`` lives under ``directory``."""
    try:
        path.resolve().relative_to(directory)
        return True
    except ValueError:
        return False
