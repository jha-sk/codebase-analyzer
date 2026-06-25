"""Configuration dataclasses for the codebase analyzer.

This module defines the immutable configuration objects used throughout the
analyzer. ``AnalyzerConfig`` describes *what* to analyze (project root, include
and exclude patterns, size limits, cache location) and ``RiskThresholds``
describes how numeric risk scores map onto human-readable risk levels.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Default glob patterns applied relative to the project root.
DEFAULT_INCLUDE_PATTERNS: list[str] = [
    "**/*.go",
    "**/*.java",
    "**/*.py",
    "**/*.ts",
    "**/*.tsx",
    "**/*.js",
    "**/*.jsx",
]

DEFAULT_EXCLUDE_PATTERNS: list[str] = [
    "node_modules/**",
    "vendor/**",
    ".git/**",
    "dist/**",
    "build/**",
    ".next/**",
    "out/**",
    ".turbo/**",
    "coverage/**",
    ".venv/**",
    "venv/**",
    "__pycache__/**",
    ".cache/**",
]

# Files larger than this (in bytes) are skipped entirely.
DEFAULT_MAX_FILE_SIZE_BYTES: int = 1_000_000  # 1 MB


@dataclass(frozen=True)
class AnalyzerConfig:
    """Configuration for the codebase analyzer.

    Attributes:
        project_root: Absolute path to the project root. Validated to exist and
            be a directory; relative paths are resolved to absolute on init.
        include_patterns: Glob patterns (relative to ``project_root``) selecting
            files to analyze.
        exclude_patterns: Glob patterns (relative to ``project_root``) selecting
            files/directories to skip.
        max_file_size_bytes: Files larger than this are skipped.
        cache_dir: Directory for the parsed-AST cache. Defaults to
            ``<project_root>/.cache/claude-analyzer`` when not supplied.

    Raises:
        FileNotFoundError: If ``project_root`` does not exist.
        NotADirectoryError: If ``project_root`` exists but is not a directory.
    """

    project_root: Path
    include_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_INCLUDE_PATTERNS))
    exclude_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE_PATTERNS))
    max_file_size_bytes: int = DEFAULT_MAX_FILE_SIZE_BYTES
    cache_dir: Path | None = None

    def __post_init__(self) -> None:
        # The dataclass is frozen, so attributes must be set via object.__setattr__.
        resolved_root = Path(self.project_root).expanduser().resolve()
        if not resolved_root.exists():
            raise FileNotFoundError(f"project_root does not exist: {resolved_root}")
        if not resolved_root.is_dir():
            raise NotADirectoryError(f"project_root is not a directory: {resolved_root}")
        object.__setattr__(self, "project_root", resolved_root)

        cache_dir = self.cache_dir
        if cache_dir is None:
            cache_dir = resolved_root / ".cache" / "claude-analyzer"
        object.__setattr__(self, "cache_dir", Path(cache_dir).expanduser().resolve())

    @classmethod
    def create(
        cls,
        project_root: Path | str,
        include_patterns: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
        max_file_size_bytes: int = DEFAULT_MAX_FILE_SIZE_BYTES,
        cache_dir: Path | str | None = None,
    ) -> AnalyzerConfig:
        """Convenience constructor that applies defaults for ``None`` arguments.

        Args:
            project_root: Path to the project root (relative or absolute).
            include_patterns: Override include globs, or ``None`` for defaults.
            exclude_patterns: Override exclude globs, or ``None`` for defaults.
            max_file_size_bytes: Maximum file size to parse.
            cache_dir: Override cache directory, or ``None`` for the default.

        Returns:
            A fully validated :class:`AnalyzerConfig`.
        """
        return cls(
            project_root=Path(project_root),
            include_patterns=(
                list(include_patterns)
                if include_patterns is not None
                else list(DEFAULT_INCLUDE_PATTERNS)
            ),
            exclude_patterns=(
                list(exclude_patterns)
                if exclude_patterns is not None
                else list(DEFAULT_EXCLUDE_PATTERNS)
            ),
            max_file_size_bytes=max_file_size_bytes,
            cache_dir=Path(cache_dir) if cache_dir is not None else None,
        )


@dataclass(frozen=True)
class RiskThresholds:
    """Score thresholds for mapping a 0-100 risk score onto a risk level.

    A score >= ``critical`` is "critical", >= ``high`` is "high", and so on.
    """

    critical: int = 80
    high: int = 60
    medium: int = 40
    low: int = 0

    def classify(self, score: float) -> str:
        """Classify a numeric risk score into a level string.

        Args:
            score: A risk score, expected in the range 0-100.

        Returns:
            One of ``"critical"``, ``"high"``, ``"medium"`` or ``"low"``.
        """
        if score >= self.critical:
            return "critical"
        if score >= self.high:
            return "high"
        if score >= self.medium:
            return "medium"
        return "low"
