VERBOSE_LOGGING_FORMAT = (
    "%(asctime)s [%(pathname)s:%(lineno)s in function %(funcName)s] %(message)s"
)
LOGGING_FORMAT = "[%(levelname)s] %(message)s"
BARE_LOGGING_FORMAT = "%(message)s"


def set_level(level: int, *, echo_setting: bool = True) -> None:
    import logging
    import time

    from codeflash.cli_cmds.console import console

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

    console.rule()
