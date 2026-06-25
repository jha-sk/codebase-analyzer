"""Tests for the optional Semgrep-based vulnerability scanner."""

from __future__ import annotations

import json

from claude_codebase_analyzer.analysis.vuln_scanner import VulnScanner


def test_is_available_returns_bool(python_project) -> None:
    assert isinstance(VulnScanner.is_available(), bool)


def test_scan_when_unavailable(python_project, monkeypatch) -> None:
    monkeypatch.setattr(VulnScanner, "is_available", staticmethod(lambda: False))
    result = VulnScanner(python_project).scan()
    assert result["available"] is False
    assert result["findings"] == []
    assert result["summary"]["total"] == 0


def test_normalize_parses_semgrep_json(python_project) -> None:
    scanner = VulnScanner(python_project)
    sample = json.dumps(
        {
            "results": [
                {
                    "check_id": "rule.x",
                    "path": str(python_project / "myapp" / "main.py"),
                    "start": {"line": 3},
                    "extra": {"severity": "ERROR", "message": "boom"},
                }
            ]
        }
    )
    result = scanner._normalize(sample)
    assert result["available"] is True
    assert result["summary"]["total"] == 1
    finding = result["findings"][0]
    assert finding["check_id"] == "rule.x"
    assert finding["severity"] == "ERROR"
    assert finding["line"] == 3


def test_normalize_handles_bad_json(python_project) -> None:
    result = VulnScanner(python_project)._normalize("not json {{{")
    assert result["available"] is True
    assert result["findings"] == []
