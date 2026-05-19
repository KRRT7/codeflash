from __future__ import annotations

import re


def shorten_pytest_error(pytest_error_string: str) -> str:
    return "\n".join(re.findall(r"^[E>] +(.*)$", pytest_error_string, re.MULTILINE))


def extract_unique_errors(pytest_output: str) -> set[str]:
    unique_errors = set()
    pattern = r"^E\s+(.*)$"
    for error_message in re.findall(pattern, pytest_output, re.MULTILINE):
        error_message = error_message.strip()
        if error_message:
            unique_errors.add(error_message)
    return unique_errors
