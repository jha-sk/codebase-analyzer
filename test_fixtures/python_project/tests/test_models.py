"""A test file so the analyzer's test-coverage heuristic detects coverage."""

from myapp.models import User


def test_greeting() -> None:
    assert User(name="ada").greeting() == "Hello, ada"
