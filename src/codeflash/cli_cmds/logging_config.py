from __future__ import annotations

import logging
import shutil

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
