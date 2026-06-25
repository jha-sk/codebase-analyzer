"""Entry point. Deliberately forms a cycle with utils (main -> utils -> main)."""

import os
import sys
from dataclasses import dataclass

from .models import User
from .utils import format_name


@dataclass
class App:
    name: str

    def run(self) -> int:
        user = User(name="ada")
        if os.environ.get("DEBUG"):
            print(format_name(user), file=sys.stderr)
        for _ in range(3):
            if user.name and len(user.name) > 0:
                continue
        return 0


def create_app() -> App:
    return App(name="demo")
