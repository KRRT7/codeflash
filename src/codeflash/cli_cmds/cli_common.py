from __future__ import annotations

import sys

from codeflash.cli_cmds.console import console, logger


def apologize_and_exit() -> None:
    console.rule()
    logger.info(
        "💡 If you're having trouble, see https://docs.codeflash.ai/getting-started/local-installation for further help getting started with Codeflash!"
    )
    console.rule()
    logger.info("👋 Exiting...")
    sys.exit(1)


def prompt_choice(message: str, choices: list[str], default: str | None = None) -> str:
    choices_str = ", ".join(choices)
    prompt_text = f"{message} ({choices_str})"
    if default is not None:
        prompt_text += f" [{default}]"
    prompt_text += ": "
    while True:
        try:
            value = input(prompt_text).strip()
        except (KeyboardInterrupt, EOFError):
            apologize_and_exit()
        if not value and default is not None:
            return default
        if value in choices:
            return value
        print(f"Please choose from: {choices_str}")


def prompt_text(
    message: str, default: str | None = None, prompt_suffix: str = ": "
) -> str:
    prompt_text = f"{message}{prompt_suffix}"
    try:
        value = input(prompt_text).strip()
    except (KeyboardInterrupt, EOFError):
        apologize_and_exit()
    if not value and default is not None:
        return default
    return value


def confirm(message: str, default: bool = True) -> bool:
    indicator = "Y/n" if default else "y/N"
    while True:
        try:
            value = input(f"{message} [{indicator}]: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            apologize_and_exit()
        if not value:
            return default
        if value in ("y", "yes"):
            return True
        if value in ("n", "no"):
            return False
