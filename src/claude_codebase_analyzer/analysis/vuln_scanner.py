"""Optional Semgrep-based vulnerability scanning.

This integration is best-effort: if Semgrep is not installed, the scanner
reports that gracefully instead of failing. It is intentionally not imported by
``analysis/__init__`` so the heavy dependency stays out of the default path.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class VulnScanner:
    """Run Semgrep over a project and normalize its findings.

    Args:
        project_root: Absolute path to the project root.
    """

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root).resolve()

    @staticmethod
    def is_available() -> bool:
        """Return whether the ``semgrep`` executable is on the PATH."""
        return shutil.which("semgrep") is not None

    def scan(self, config: str = "auto", timeout: int = 300) -> dict:
        """Scan the project with Semgrep and return normalized findings.

        Args:
            config: Semgrep ruleset (e.g. ``"auto"`` or a registry id).
            timeout: Maximum seconds to allow the scan to run.

        Returns:
            A dict with ``available`` (bool), ``findings`` (list) and a
            ``summary``. When Semgrep is unavailable, ``available`` is ``False``
            and ``findings`` is empty.
        """
        if not self.is_available():
            return {
                "available": False,
                "findings": [],
                "summary": {
                    "total": 0,
                    "message": "Semgrep is not installed; skipping vulnerability scan.",
                },
            }

        try:
            completed = subprocess.run(  # noqa: S603 - controlled args
                [
                    "semgrep",
                    "--config",
                    config,
                    "--json",
                    "--quiet",
                    str(self.project_root),
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("Semgrep invocation failed: %s", exc)
            return {
                "available": True,
                "findings": [],
                "summary": {"total": 0, "message": f"Semgrep failed: {exc}"},
            }

        return self._normalize(completed.stdout)

    def _normalize(self, stdout: str) -> dict:
        """Normalize raw Semgrep JSON output into the scanner result schema.

        Args:
            stdout: The captured stdout from a Semgrep ``--json`` run.

        Returns:
            The normalized result dict.
        """
        try:
            data = json.loads(stdout or "{}")
        except json.JSONDecodeError:
            return {
                "available": True,
                "findings": [],
                "summary": {"total": 0, "message": "Unparseable Semgrep output."},
            }

        findings: list[dict] = []
        for result in data.get("results", []):
            extra = result.get("extra", {})
            path = result.get("path", "")
            try:
                rel = str(Path(path).resolve().relative_to(self.project_root))
            except ValueError:
                rel = path
            findings.append(
                {
                    "check_id": result.get("check_id"),
                    "file_path": rel,
                    "line": result.get("start", {}).get("line"),
                    "severity": extra.get("severity", "INFO"),
                    "message": extra.get("message", ""),
                }
            )

        by_severity: dict[str, int] = {}
        for finding in findings:
            sev = str(finding["severity"])
            by_severity[sev] = by_severity.get(sev, 0) + 1

        return {
            "available": True,
            "findings": findings,
            "summary": {"total": len(findings), "by_severity": by_severity},
        }
