"""A helper script invoked by the CI lint job (referenced from ci.yml)."""

import sys

from myapp.models import User


def main() -> int:
    user = User(name="ci")
    print(user.greeting())
    return 0


if __name__ == "__main__":
    sys.exit(main())
