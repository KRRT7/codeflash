"""Thanks for being curious about how codeflash works!.

If you might want to work with us on finally making performance a
solved problem, please reach out to us at careers@codeflash.ai. We're hiring!
"""

from codeflash.cli_cmds.cli import parse_args, process_pyproject_config
from codeflash.cli_cmds.cmd_init import CODEFLASH_LOGO, ask_run_end_to_end_test
from codeflash.cli_cmds.console import paneled_text
from codeflash.code_utils import env_utils
from codeflash.code_utils.version_check import check_for_newer_minor_version
from codeflash.models.config import AppConfig


def main() -> None:
    """Entry point for the codeflash command-line interface."""
    args = parse_args()
    print_codeflash_banner()

    # Check for newer version for all commands
    check_for_newer_minor_version()

    if args.command:
        args.func()
    elif args.verify_setup:
        args = process_pyproject_config(args)
        ask_run_end_to_end_test(args)
    else:
        args = process_pyproject_config(args)
        if not env_utils.check_formatter_installed(args.formatter_cmds):
            return

        config = AppConfig.from_namespace(args)
        from codeflash.optimization import optimizer

        optimizer.run_with_args(config)


def print_codeflash_banner() -> None:
    paneled_text(
        CODEFLASH_LOGO,
        panel_args={"title": "https://codeflash.ai", "expand": False},
        text_args={"style": "bold gold3"},
    )


if __name__ == "__main__":
    main()
