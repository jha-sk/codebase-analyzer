"""Utility helpers. Imports back into main to create a circular dependency."""

import json

from .models import User
from . import main  # noqa: F401  (intentional cycle: utils -> main)


def format_name(user: User) -> str:
    return json.dumps({"name": user.name})


def make_default_app() -> "main.App":
    return main.create_app()
