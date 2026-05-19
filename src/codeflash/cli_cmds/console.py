from __future__ import annotations

import logging
import shutil
from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from typing import Any

from codeflash.cli_cmds.logging_config import BARE_LOGGING_FORMAT

DEBUG_MODE = logging.getLogger().getEffectiveLevel() == logging.DEBUG


class _Console:
    def print(self, *args: Any, **kwargs: Any) -> None:
        print(*args)

    @staticmethod
    def rule(title: str = "") -> None:
        width = shutil.get_terminal_size().columns
        if title:
            prefix = "─" * 3
            suffix_len = width - len(prefix) - len(title) - 1
            suffix = "─" * max(suffix_len, 0)
            print(f"{prefix} {title} {suffix}")
        else:
            print("─" * width)


console = _Console()

logging.basicConfig(
    level=logging.INFO,
    handlers=[logging.StreamHandler()],
    format=BARE_LOGGING_FORMAT,
)

logger = logging.getLogger("codeflash")
logging.getLogger("parso").setLevel(logging.WARNING)


class DummyTask:
    def __init__(self) -> None:
        self.id = 0


class DummyProgress:
    def __init__(self) -> None:
        pass

    @staticmethod
    def advance(task_id: int, advance: int = 1) -> None:
        pass


def paneled_text(
    text: str,
    panel_args: dict[str, str | bool] | None = None,
    text_args: dict[str, str] | None = None,
) -> None:
    console.print(text)


def code_print(
    code_str: str,
    file_name: Optional[str] = None,
    function_name: Optional[str] = None,
    lsp_message_id: Optional[str] = None,
) -> None:
    console.rule()
    print(code_str)
    console.rule()


@contextmanager
def progress_bar(
    message: str, *, transient: bool = False, revert_to_print: bool = False
) -> Generator[int, None, None]:
    logger.info(message)
    yield DummyTask().id


@contextmanager
def test_files_progress_bar(
    total: int, description: str
) -> Generator[tuple[DummyProgress, int], None, None]:
    logger.info(f"{description}: 0/{total}")
    progress = DummyProgress()
    yield progress, DummyTask().id
