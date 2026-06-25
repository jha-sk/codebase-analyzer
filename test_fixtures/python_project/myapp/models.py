"""Data models with no project-internal dependencies (a graph leaf)."""

from dataclasses import dataclass


@dataclass
class User:
    name: str

    def greeting(self) -> str:
        if self.name:
            return f"Hello, {self.name}"
        return "Hello"
