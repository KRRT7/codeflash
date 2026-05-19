from __future__ import annotations

import logging
import shutil
from collections.abc import Generator
from contextlib import contextmanager

VERBOSE_LOGGING_FORMAT = (
    "%(asctime)s [%(pathname)s:%(lineno)s in function %(funcName)s] %(message)s"
)
LOGGING_FORMAT = "[%(levelname)s] %(message)s"
BARE_LOGGING_FORMAT = "%(message)s"

logging.basicConfig(
    level=logging.INFO,
    handlers=[logging.StreamHandler()],
    format=BARE_LOGGING_FORMAT,
)

logger = logging.getLogger("codeflash")
logging.getLogger("parso").setLevel(logging.WARNING)
DEBUG_MODE = logging.getLogger().getEffectiveLevel() == logging.DEBUG


def set_level(level: int, *, echo_setting: bool = True) -> None:
    import time

    logging.basicConfig(
        level=level,
        handlers=[logging.StreamHandler()],
        format=BARE_LOGGING_FORMAT,
    )
    logging.getLogger().setLevel(level)
    if echo_setting and level == logging.DEBUG:
        logging.Formatter.converter = time.gmtime
        logging.basicConfig(
            format=VERBOSE_LOGGING_FORMAT,
            handlers=[logging.StreamHandler()],
            force=True,
        )
        logging.info("Verbose DEBUG logging enabled")
    rule()


def rule(title: str = "") -> None:
    width = shutil.get_terminal_size().columns
    if title:
        prefix = "─" * 3
        suffix_len = width - len(prefix) - len(title) - 1
        suffix = "─" * max(suffix_len, 0)
        print(f"{prefix} {title} {suffix}")
    else:
        print("─" * width)


def paneled_text(
    text: str,
    panel_args: dict[str, str | bool] | None = None,
    text_args: dict[str, str] | None = None,
) -> None:
    print(text)


def code_print(
    code_str: str,
    file_name: str | None = None,
    function_name: str | None = None,
    lsp_message_id: str | None = None,
) -> None:
    rule()
    print(code_str)
    rule()


@contextmanager
def progress_bar(
    message: str, *, transient: bool = False, revert_to_print: bool = False
) -> Generator[int, None, None]:
    logger.info(message)
    yield 0


@contextmanager
def test_files_progress_bar(
    total: int, description: str
) -> Generator[tuple[None, int], None, None]:
    logger.info(f"{description}: 0/{total}")
    yield None, 0
