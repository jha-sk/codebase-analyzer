"""Architectural risk scoring for files and modules.

The :class:`RiskAnalyzer` combines five weighted metrics into a single 0-100
risk score per file. The weights are fixed by specification:

================== ======
Metric             Weight
================== ======
cyclomatic_complexity  25%
dependency_depth       20%
circular_dependency    30%
change_frequency       15%
test_coverage          10%
================== ======

Each metric is normalized onto a 0-100 sub-score before weighting. Git history
is read with a short-timeout subprocess call and cached; the MCP server invokes
the analyzer from a worker thread so the event loop is never blocked.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from ..config import RiskThresholds
from ..graph.builder import DependencyGraph
from ..graph.cycles import CycleDetector

logger = logging.getLogger(__name__)

# Metric weights (must sum to 1.0).
WEIGHTS = {
    "cyclomatic_complexity": 0.25,
    "dependency_depth": 0.20,
    "circular_dependency": 0.30,
    "change_frequency": 0.15,
    "test_coverage": 0.10,
}

# Normalization constants: the metric value that maps to a 100 sub-score.
_COMPLEXITY_AT_MAX = 25  # complexity >= 25 -> 100
_DEPTH_AT_MAX = 5  # dependency depth >= 5 -> 100
_CHANGES_AT_MAX = 10  # >= 10 commits in 90d -> 100
_CIRCULAR_SUBSCORE = 50  # fixed sub-score when a file is in any cycle


class RiskAnalyzer:
    """Calculate architectural risk scores for files and modules.

    Args:
        graph: The populated dependency graph.
        project_root: Absolute path to the project root (for git + path math).
        thresholds: Score-to-level thresholds (defaults to :class:`RiskThresholds`).
    """

    def __init__(
        self,
        graph: DependencyGraph,
        project_root: Path,
        thresholds: RiskThresholds | None = None,
    ) -> None:
        self.graph = graph
        self.project_root = Path(project_root).resolve()
        self.thresholds = thresholds or RiskThresholds()
        # Caches.
        self._cycle_files: set[str] | None = None
        self._git_available: bool | None = None
        self._change_cache: dict[str, int] = {}

    # -- public API --------------------------------------------------------

    def calculate_file_risk(self, file_path: str) -> dict:
        """Calculate the 0-100 risk score for a single file.

        Args:
            file_path: The file to score (must be a node in the graph for full
                metrics; unknown files yield a minimal, low-risk result).

        Returns:
            A risk result dict (see module docstring for the metric table) with
            keys ``file_path``, ``risk_score``, ``risk_level``, ``metrics`` and
            ``recommendations``.
        """
        metadata = self.graph._file_metadata.get(file_path, {})
        ast_summary = metadata.get("ast_summary", {})

        complexity = int(ast_summary.get("complexity", 1))
        depth = self._dependency_depth(file_path)
        in_cycle = file_path in self._files_in_cycles()
        changes = self._change_frequency(file_path)
        coverage = self._test_coverage_estimate(file_path)

        sub_scores = {
            "cyclomatic_complexity": _clamp(complexity / _COMPLEXITY_AT_MAX * 100),
            "dependency_depth": _clamp(depth / _DEPTH_AT_MAX * 100),
            "circular_dependency": float(_CIRCULAR_SUBSCORE if in_cycle else 0),
            "change_frequency": _clamp(changes / _CHANGES_AT_MAX * 100),
            "test_coverage": float(100 - coverage),
        }
        score = round(sum(WEIGHTS[k] * sub_scores[k] for k in WEIGHTS))
        score = int(_clamp(score))

        metrics = {
            "cyclomatic_complexity": complexity,
            "dependency_depth": depth,
            "in_circular_dependency": in_cycle,
            "change_frequency_90d": changes,
            "test_coverage_estimate": coverage,
        }
        recommendations = self._recommendations(metrics, sub_scores)

        return {
            "file_path": self._relativize(file_path),
            "risk_score": score,
            "risk_level": self.thresholds.classify(score),
            "metrics": metrics,
            "recommendations": recommendations,
        }

    def calculate_module_risk(self, module_path: str) -> dict:
        """Aggregate risk for all files within a directory/module.

        Args:
            module_path: A directory path (absolute or relative to the project
                root). All graph files under it are aggregated.

        Returns:
            A dict with the module path, the number of files, the average risk
            score, the dominant risk level, and a list of the critical/high
            files within the module.
        """
        module_abs = self._absolutize(module_path)
        member_results = [
            self.calculate_file_risk(node)
            for node in self.graph.graph.nodes
            if _is_within(node, module_abs)
        ]

        if not member_results:
            return {
                "module_path": self._relativize(module_abs),
                "file_count": 0,
                "average_risk_score": 0,
                "risk_level": "low",
                "critical_files": [],
            }

        avg = round(sum(r["risk_score"] for r in member_results) / len(member_results))
        critical_files = [
            {
                "file_path": r["file_path"],
                "risk_score": r["risk_score"],
                "risk_level": r["risk_level"],
            }
            for r in member_results
            if r["risk_level"] in ("critical", "high")
        ]
        critical_files.sort(key=lambda r: r["risk_score"], reverse=True)

        return {
            "module_path": self._relativize(module_abs),
            "file_count": len(member_results),
            "average_risk_score": avg,
            "risk_level": self.thresholds.classify(avg),
            "critical_files": critical_files,
        }

    def get_top_risk_files(self, n: int = 10) -> list[dict]:
        """Return the ``n`` highest-risk files, descending by score.

        Args:
            n: Maximum number of files to return.

        Returns:
            A list of risk result dicts.
        """
        results = [self.calculate_file_risk(node) for node in self.graph.graph.nodes]
        results.sort(key=lambda r: r["risk_score"], reverse=True)
        return results[: max(0, n)]

    # -- metric helpers ----------------------------------------------------

    def _files_in_cycles(self) -> set[str]:
        """Return (caching) the set of files participating in any cycle."""
        if self._cycle_files is None:
            cycles = CycleDetector.find_cycles(self.graph.graph)
            files: set[str] = set()
            for cycle in cycles:
                files.update(cycle)
            self._cycle_files = files
        return self._cycle_files

    def _dependency_depth(self, file_path: str, _limit: int = 64) -> int:
        """Compute the longest downstream dependency chain length from a file.

        Cycles are handled by not revisiting a node already on the current path
        (a back-edge contributes no additional depth).

        Args:
            file_path: The file to measure from.
            _limit: Recursion safety bound.

        Returns:
            The maximum dependency depth (0 for a leaf or unknown file).
        """
        if not self.graph.graph.has_node(file_path):
            return 0

        def depth(node: str, on_path: frozenset[str], budget: int) -> int:
            if budget <= 0:
                return 0
            successors = [s for s in self.graph.graph.successors(node) if s not in on_path]
            if not successors:
                return 0
            return 1 + max(depth(s, on_path | {node}, budget - 1) for s in successors)

        return depth(file_path, frozenset(), _limit)

    def _change_frequency(self, file_path: str) -> int:
        """Return the number of git commits touching ``file_path`` in 90 days.

        Uses ``git log --since="90 days ago" --follow``. Returns 0 if git is
        unavailable, the project is not a repo, or the file is untracked.

        Args:
            file_path: Absolute path to the file.

        Returns:
            A commit count (>= 0).
        """
        if file_path in self._change_cache:
            return self._change_cache[file_path]
        if not self._git_repo_available():
            self._change_cache[file_path] = 0
            return 0

        try:
            rel = str(Path(file_path).resolve().relative_to(self.project_root))
        except ValueError:
            self._change_cache[file_path] = 0
            return 0

        count = 0
        try:
            completed = subprocess.run(  # noqa: S603 - args are controlled
                [
                    "git",
                    "-C",
                    str(self.project_root),
                    "log",
                    "--since=90 days ago",
                    "--follow",
                    "--format=%H",
                    "--",
                    rel,
                ],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if completed.returncode == 0:
                count = len([ln for ln in completed.stdout.splitlines() if ln.strip()])
        except (OSError, subprocess.SubprocessError):
            logger.debug("git log failed for %s", file_path, exc_info=True)
            count = 0

        self._change_cache[file_path] = count
        return count

    def _git_repo_available(self) -> bool:
        """Return (caching) whether ``project_root`` is inside a git work tree."""
        if self._git_available is not None:
            return self._git_available
        try:
            completed = subprocess.run(  # noqa: S603 - args are controlled
                ["git", "-C", str(self.project_root), "rev-parse", "--is-inside-work-tree"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self._git_available = completed.returncode == 0 and completed.stdout.strip() == "true"
        except (OSError, subprocess.SubprocessError):
            self._git_available = False
        return self._git_available

    def _test_coverage_estimate(self, file_path: str) -> int:
        """Estimate test coverage (0-100) from the presence of a test file.

        This is a heuristic: source files that appear to be tests themselves are
        treated as fully covered; non-test files are considered covered (70) when
        a plausibly-named test file exists for them anywhere in the project, and
        uncovered (0) otherwise.

        Args:
            file_path: Absolute path to the file.

        Returns:
            An estimated coverage percentage in 0-100.
        """
        name = Path(file_path).name
        if self._looks_like_test(name):
            return 100
        stem = Path(file_path).stem
        # Search the graph's known files for a matching test file.
        for node in self.graph.graph.nodes:
            node_name = Path(node).name
            if not self._looks_like_test(node_name):
                continue
            if stem and stem.lower() in node_name.lower():
                return 70
        return 0

    @staticmethod
    def _looks_like_test(name: str) -> bool:
        """Return whether a file name matches a common test convention."""
        lowered = name.lower()
        return (
            lowered.startswith("test_")
            or "_test." in lowered
            or ".test." in lowered
            or ".spec." in lowered
            or lowered.endswith("test.java")
        )

    # -- recommendations ---------------------------------------------------

    def _recommendations(self, metrics: dict, sub_scores: dict[str, float]) -> list[str]:
        """Build human-readable recommendations from a file's metrics.

        Args:
            metrics: The raw metric values for the file.
            sub_scores: The normalized 0-100 sub-scores.

        Returns:
            A list of recommendation strings (possibly empty).
        """
        recs: list[str] = []
        if metrics["in_circular_dependency"]:
            recs.append(
                "File participates in a circular dependency; consider breaking "
                "the cycle via dependency inversion or extracting shared code."
            )
        if sub_scores["cyclomatic_complexity"] >= 60:
            recs.append(
                f"High cyclomatic complexity (~{metrics['cyclomatic_complexity']}); "
                "consider splitting large functions and reducing branching."
            )
        if sub_scores["dependency_depth"] >= 60:
            recs.append(
                f"Deep dependency chain (depth {metrics['dependency_depth']}); "
                "a change here can ripple widely — add integration tests."
            )
        if metrics["change_frequency_90d"] >= _CHANGES_AT_MAX:
            recs.append(
                f"Frequently changed ({metrics['change_frequency_90d']} commits "
                "in 90 days); a churn hotspot that warrants extra review."
            )
        if metrics["test_coverage_estimate"] == 0:
            recs.append("No test file detected; add unit tests to make changes safer.")
        if not recs:
            recs.append("No significant risk factors detected.")
        return recs

    # -- path helpers ------------------------------------------------------

    def _relativize(self, file_path: str | Path) -> str:
        """Return ``file_path`` relative to the project root when possible."""
        try:
            return str(Path(file_path).resolve().relative_to(self.project_root))
        except ValueError:
            return str(file_path)

    def _absolutize(self, path: str) -> Path:
        """Resolve ``path`` to an absolute path under the project root."""
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.project_root / candidate
        return candidate.resolve()


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    """Clamp ``value`` into the inclusive range ``[low, high]``."""
    return max(low, min(high, value))


def _is_within(node: str, directory: Path) -> bool:
    """Return whether file path ``node`` lives under ``directory``."""
    try:
        Path(node).resolve().relative_to(directory)
        return True
    except ValueError:
        return False
