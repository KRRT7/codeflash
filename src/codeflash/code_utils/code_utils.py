from __future__ import annotations

import sys


def exit_with_message(message: str, *, error_on_exit: bool = False) -> None:
    print(message)
    sys.exit(1 if error_on_exit else 0)
