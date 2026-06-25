"""Generate Markdown reports from analysis results."""

from __future__ import annotations

from pathlib import PurePath

from ..graph.cycles import CycleDetector
from ..graph.visualizer import MermaidVisualizer

# Emoji markers per risk level for at-a-glance scanning.
_LEVEL_BADGE = {
    "critical": "🔴 critical",
    "high": "🟠 high",
    "medium": "🟡 medium",
    "low": "🟢 low",
}


def _name(path: str) -> str:
    """Return the final path component of ``path``."""
    return PurePath(path).name or path


def _fence(language: str, body: str) -> str:
    """Wrap ``body`` in a fenced code block tagged with ``language``."""
    return f"```{language}\n{body}\n```"


class MarkdownReporter:
    """Generate human-readable Markdown reports from analysis results."""

    @staticmethod
    def dependency_tree_report(tree: dict, root: str) -> str:
        """Render a dependency tree as Markdown (ASCII tree + stats + diagram).

        Args:
            tree: A nested tree dict from ``DependencyGraph.get_dependency_tree``.
            root: The root file path (key into ``tree``).

        Returns:
            A Markdown document string.
        """
        lines: list[str] = [f"# Dependency Tree: `{_name(root)}`", ""]

        ascii_lines: list[str] = []
        stats = {"nodes": 0, "max_depth": 0}

        def walk(node_key: str, subtree: dict, prefix: str, is_last: bool, depth: int) -> None:
            stats["nodes"] += 1
            stats["max_depth"] = max(stats["max_depth"], depth)
            if depth == 0:
                ascii_lines.append(_name(node_key))
            else:
                connector = "└── " if is_last else "├── "
                marker = "  ↻ (cycle)" if subtree.get("cyclic") else ""
                ascii_lines.append(f"{prefix}{connector}{_name(node_key)}{marker}")
            children = subtree.get("children", {})
            child_items = list(children.items())
            for index, (child_key, child_subtree) in enumerate(child_items):
                last = index == len(child_items) - 1
                child_prefix = "" if depth == 0 else prefix + ("    " if is_last else "│   ")
                walk(child_key, child_subtree, child_prefix, last, depth + 1)

        root_subtree = tree.get(root, {"metadata": {}, "children": {}})
        walk(root, root_subtree, "", True, 0)

        lines.append(_fence("text", "\n".join(ascii_lines)))
        lines.append("")
        lines.append("## Statistics")
        lines.append("")
        lines.append(f"- **Files in tree:** {stats['nodes']}")
        lines.append(f"- **Maximum depth:** {stats['max_depth']}")
        lines.append("")
        lines.append("## Diagram")
        lines.append("")
        lines.append(_fence("mermaid", MermaidVisualizer.dependency_tree_to_mermaid(tree, root)))
        return "\n".join(lines)

    @staticmethod
    def cycles_report(cycles: list[list[str]], summary: dict) -> str:
        """Render circular dependencies as Markdown.

        Args:
            cycles: Cycles from ``CycleDetector.find_cycles``.
            summary: Summary from ``CycleDetector.get_cycle_summary``.

        Returns:
            A Markdown document string.
        """
        lines: list[str] = ["# Circular Dependencies", ""]

        count = summary.get("cycle_count", len(cycles))
        if count == 0:
            lines.append("✅ **No circular dependencies detected.**")
            return "\n".join(lines)

        severity = "🔴 High" if count >= 3 else "🟠 Moderate"
        lines.append(f"**Status:** {severity} — found **{count}** circular dependency chain(s).")
        lines.append("")
        lines.append(f"- **Largest cycle size:** {summary.get('largest_cycle_size', 0)} files")
        lines.append(f"- **Files involved:** {len(summary.get('files_in_cycles', []))}")
        by_lang = summary.get("cycles_by_language", {})
        if by_lang:
            breakdown = ", ".join(f"{lang}: {n}" for lang, n in sorted(by_lang.items()))
            lines.append(f"- **By language:** {breakdown}")
        lines.append("")

        lines.append("## Diagram")
        lines.append("")
        lines.append(_fence("mermaid", MermaidVisualizer.cycles_to_mermaid(cycles)))
        lines.append("")

        lines.append("## Cycles")
        lines.append("")
        for index, cycle in enumerate(cycles, start=1):
            chain = " → ".join(_name(node) for node in cycle)
            lines.append(f"{index}. {chain}")
        lines.append("")

        return "\n".join(lines)

    @staticmethod
    def cycles_report_with_breakpoints(
        cycles: list[list[str]], summary: dict, breakpoints: list[dict]
    ) -> str:
        """Like :meth:`cycles_report` but appends breakpoint suggestions.

        Args:
            cycles: Cycles from ``CycleDetector.find_cycles``.
            summary: Summary from ``CycleDetector.get_cycle_summary``.
            breakpoints: Suggestions from ``CycleDetector.suggest_breakpoints``.

        Returns:
            A Markdown document string.
        """
        report = MarkdownReporter.cycles_report(cycles, summary)
        if not breakpoints:
            return report
        lines = [report, "", "## Suggested Breakpoints", ""]
        lines.append("| Edge to remove | Impact (lower = safer) | Suggestion |")
        lines.append("| --- | --- | --- |")
        for bp in breakpoints:
            edge = bp.get("edge", ("", ""))
            edge_text = f"`{_name(str(edge[0]))}` → `{_name(str(edge[1]))}`"
            lines.append(
                f"| {edge_text} | {bp.get('impact_score', 0)} | {bp.get('suggestion', '')} |"
            )
        return "\n".join(lines)

    @staticmethod
    def risk_report(risk_results: list[dict], top_n: int = 20) -> str:
        """Render a risk report as Markdown (summary table + detail sections).

        Args:
            risk_results: File risk dicts from ``RiskAnalyzer``.
            top_n: Maximum number of files to include.

        Returns:
            A Markdown document string.
        """
        lines: list[str] = ["# Architectural Risk Report", ""]
        if not risk_results:
            lines.append("No files were analyzed.")
            return "\n".join(lines)

        ordered = sorted(risk_results, key=lambda r: r["risk_score"], reverse=True)[:top_n]

        # Executive summary.
        level_counts: dict[str, int] = {}
        for r in risk_results:
            level_counts[r["risk_level"]] = level_counts.get(r["risk_level"], 0) + 1
        lines.append("## Summary")
        lines.append("")
        for level in ("critical", "high", "medium", "low"):
            lines.append(f"- **{_LEVEL_BADGE[level]}:** {level_counts.get(level, 0)} file(s)")
        lines.append("")

        # Summary table.
        lines.append(f"## Top {len(ordered)} Files by Risk")
        lines.append("")
        lines.append("| File | Score | Level | Primary reason |")
        lines.append("| --- | --- | --- | --- |")
        for r in ordered:
            reason = r["recommendations"][0] if r.get("recommendations") else "—"
            lines.append(
                f"| `{r['file_path']}` | {r['risk_score']} | "
                f"{_LEVEL_BADGE.get(r['risk_level'], r['risk_level'])} | {reason} |"
            )
        lines.append("")

        # Detailed breakdown for critical/high files.
        detailed = [r for r in ordered if r["risk_level"] in ("critical", "high")]
        if detailed:
            lines.append("## Detailed Breakdown (critical & high)")
            lines.append("")
            for r in detailed:
                lines.append(f"### `{r['file_path']}` — {r['risk_score']}/100 ({r['risk_level']})")
                lines.append("")
                m = r["metrics"]
                lines.append(f"- Cyclomatic complexity: {m['cyclomatic_complexity']}")
                lines.append(f"- Dependency depth: {m['dependency_depth']}")
                lines.append(f"- In circular dependency: {m['in_circular_dependency']}")
                lines.append(f"- Commits (90d): {m['change_frequency_90d']}")
                lines.append(f"- Test coverage estimate: {m['test_coverage_estimate']}%")
                lines.append("")
                lines.append("**Recommendations:**")
                lines.append("")
                for rec in r.get("recommendations", []):
                    lines.append(f"- {rec}")
                lines.append("")

        return "\n".join(lines)

    @staticmethod
    def workflow_report(workflow_map: dict) -> str:
        """Render a CI/CD workflow mapping as Markdown.

        Args:
            workflow_map: Output of ``WorkflowMapper.map_workflow_to_code``.

        Returns:
            A Markdown document string.
        """
        lines: list[str] = ["# CI/CD Workflow Map", ""]
        lines.append(f"- **Workflow file:** `{workflow_map.get('workflow_file', '')}`")
        lines.append(f"- **Platform:** {workflow_map.get('platform', 'unknown')}")
        lines.append(f"- **Jobs:** {len(workflow_map.get('jobs', []))}")
        lines.append("")

        jobs = workflow_map.get("jobs", [])
        if jobs:
            lines.append("## Jobs")
            lines.append("")
            lines.append("| Job | Steps | Scripts referenced |")
            lines.append("| --- | --- | --- |")
            for job in jobs:
                scripts = ", ".join(f"`{s}`" for s in job.get("scripts_referenced", [])) or "—"
                lines.append(f"| {job.get('name', '')} | {job.get('step_count', 0)} | {scripts} |")
            lines.append("")

        uncovered = workflow_map.get("uncovered_files", [])
        lines.append(f"## Files Not Referenced by CI ({len(uncovered)})")
        lines.append("")
        if uncovered:
            for path in uncovered[:50]:
                lines.append(f"- `{path}`")
            if len(uncovered) > 50:
                lines.append(f"- … and {len(uncovered) - 50} more")
        else:
            lines.append("All source files are referenced by CI scripts.")
        return "\n".join(lines)

    @staticmethod
    def full_analysis_report(
        dependency_tree: dict,
        cycles: list[list[str]],
        risk_results: list[dict],
        workflow_map: dict | None = None,
    ) -> str:
        """Combine all sections into a single comprehensive Markdown document.

        Args:
            dependency_tree: A tree dict (``{root: {...}}``); the first key is
                used as the root.
            cycles: Cycles from ``CycleDetector.find_cycles``.
            risk_results: File risk dicts from ``RiskAnalyzer``.
            workflow_map: Optional workflow mapping to append.

        Returns:
            A Markdown document string with a table of contents and an executive
            summary.
        """
        summary = CycleDetector.get_cycle_summary(cycles)
        high_risk = [r for r in risk_results if r["risk_level"] in ("critical", "high")]

        lines: list[str] = ["# Codebase Analysis Report", ""]

        # Executive summary.
        lines.append("## Executive Summary")
        lines.append("")
        lines.append(f"- **Files analyzed:** {len(risk_results)}")
        lines.append(f"- **Circular dependencies:** {summary['cycle_count']}")
        lines.append(f"- **High/critical-risk files:** {len(high_risk)}")
        lines.append("")

        # Table of contents.
        lines.append("## Table of Contents")
        lines.append("")
        lines.append("1. [Dependency Tree](#dependency-tree)")
        lines.append("2. [Circular Dependencies](#circular-dependencies)")
        lines.append("3. [Architectural Risk Report](#architectural-risk-report)")
        if workflow_map:
            lines.append("4. [CI/CD Workflow Map](#cicd-workflow-map)")
        lines.append("")
        lines.append("---")
        lines.append("")

        # Sections.
        if dependency_tree:
            root = next(iter(dependency_tree))
            lines.append(MarkdownReporter.dependency_tree_report(dependency_tree, root))
            lines.append("")
            lines.append("---")
            lines.append("")

        lines.append(MarkdownReporter.cycles_report(cycles, summary))
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append(MarkdownReporter.risk_report(risk_results))

        if workflow_map:
            lines.append("")
            lines.append("---")
            lines.append("")
            lines.append(MarkdownReporter.workflow_report(workflow_map))

        return "\n".join(lines)
