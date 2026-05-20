from __future__ import annotations

import contextlib
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from codeflash.cli_cmds.logging_config import logger
from codeflash.code_utils.compat import LF
from codeflash.danom import Err, Ok

if TYPE_CHECKING:
    from codeflash.danom import Result


POWERSHELL_RC_EXPORT_PATTERN = re.compile(
    r'^\$env:CODEFLASH_API_KEY\s*=\s*(?:"|\')?(cf-[^\s"\']+)(?:"|\')?\s*$', re.MULTILINE
)
POWERSHELL_RC_EXPORT_PREFIX = "$env:CODEFLASH_API_KEY = "
CMD_RC_EXPORT_PATTERN = re.compile(r"^set CODEFLASH_API_KEY=(cf-.*)$", re.MULTILINE)
CMD_RC_EXPORT_PREFIX = "set CODEFLASH_API_KEY="
UNIX_RC_EXPORT_PATTERN = re.compile(
    r'^(?!#)export CODEFLASH_API_KEY=(?:"|\')?(cf-[^\s"\']+)(?:"|\')?$', re.MULTILINE
)
UNIX_RC_EXPORT_PREFIX = "export CODEFLASH_API_KEY="


def is_powershell() -> bool:
    if os.name != "nt":
        return False
    ps_module_path = os.environ.get("PSMODULEPATH")
    if ps_module_path:
        logger.debug(
            "api_key_storage.py:is_powershell - Detected PowerShell via PSModulePath"
        )
        return True
    comspec = os.environ.get("COMSPEC", "").lower()
    if "powershell" in comspec:
        logger.debug(
            f"api_key_storage.py:is_powershell - Detected PowerShell via COMSPEC: {comspec}"
        )
        return True
    term_program = os.environ.get("TERM_PROGRAM", "").lower()
    if (
        "windows" in term_program
        and "terminal" in term_program
        and "cmd.exe" not in comspec
    ):
        logger.debug(
            "api_key_storage.py:is_powershell - Detected PowerShell via Windows Terminal"
        )
        return True
    return False


def read_api_key_from_shell_config() -> str | None:
    shell_rc_path = get_shell_rc_path()
    if not isinstance(shell_rc_path, Path):
        shell_rc_path = Path(shell_rc_path)
    pattern = (
        POWERSHELL_RC_EXPORT_PATTERN
        if (os.name == "nt" and shell_rc_path.suffix == ".ps1")
        else (CMD_RC_EXPORT_PATTERN if os.name == "nt" else UNIX_RC_EXPORT_PATTERN)
    )
    try:
        with open(shell_rc_path.as_posix(), encoding="utf8") as shell_rc:
            matches = pattern.findall(shell_rc.read())
            return matches[-1] if matches else None
    except (FileNotFoundError, Exception) as e:
        logger.debug(f"api_key_storage.py:read_api_key_from_shell_config - Error: {e}")
        return None


def get_shell_rc_path() -> Path:
    if os.name == "nt":
        return Path.home() / (
            "codeflash_env.ps1" if is_powershell() else "codeflash_env.bat"
        )
    shell = os.environ.get("SHELL", "/bin/bash").split("/")[-1]
    rc_names = {
        "zsh": ".zshrc",
        "ksh": ".kshrc",
        "csh": ".cshrc",
        "tcsh": ".cshrc",
        "dash": ".profile",
    }
    return Path.home() / rc_names.get(shell, ".bashrc")


def get_api_key_export_line(api_key: str) -> str:
    if os.name == "nt":
        return (
            f'{POWERSHELL_RC_EXPORT_PREFIX}"{api_key}"'
            if is_powershell()
            else f'{CMD_RC_EXPORT_PREFIX}"{api_key}"'
        )
    return f'{UNIX_RC_EXPORT_PREFIX}"{api_key}"'


def save_api_key_to_rc(api_key: str) -> Result[str, str]:
    shell_rc_path = get_shell_rc_path()
    if not isinstance(shell_rc_path, Path):
        shell_rc_path = Path(shell_rc_path)
    api_key_line = get_api_key_export_line(api_key)
    if os.name == "nt":
        pattern = (
            POWERSHELL_RC_EXPORT_PATTERN if is_powershell() else CMD_RC_EXPORT_PATTERN
        )
    else:
        pattern = UNIX_RC_EXPORT_PATTERN
    try:
        with contextlib.suppress(OSError, PermissionError):
            shell_rc_path.parent.mkdir(parents=True, exist_ok=True)
        shell_rc_path_str = shell_rc_path.as_posix()
        try:
            with open(shell_rc_path_str, "r+", encoding="utf8") as shell_file:
                shell_contents = shell_file.read()
                if not shell_contents and os.name == "nt" and not is_powershell():
                    shell_contents = "@echo off"
                matches = pattern.findall(shell_contents)
                if matches:
                    updated_shell_contents = re.sub(
                        pattern, api_key_line, shell_contents
                    )
                    action = "Updated CODEFLASH_API_KEY in"
                else:
                    updated_shell_contents = (
                        (shell_contents.rstrip() + f"{LF}{api_key_line}{LF}")
                        if shell_contents
                        else (shell_contents + LF + api_key_line + LF)
                    )
                    action = "Added CODEFLASH_API_KEY to"
                shell_file.seek(0)
                shell_file.write(updated_shell_contents)
                shell_file.truncate()
        except FileNotFoundError:
            shell_contents = (
                "@echo off" if (os.name == "nt" and not is_powershell()) else ""
            )
            with open(shell_rc_path_str, "w", encoding="utf8") as shell_file:
                shell_file.write(shell_contents)
            with open(shell_rc_path_str, "r+", encoding="utf8") as shell_file:
                updated_shell_contents = (
                    shell_contents.rstrip() + f"{LF}{api_key_line}{LF}"
                )
                action = "Added CODEFLASH_API_KEY to"
                shell_file.seek(0)
                shell_file.write(updated_shell_contents)
                shell_file.truncate()
        logger.debug(
            f"api_key_storage.py:save_api_key_to_rc - Successfully wrote to {shell_rc_path}"
        )
        return Ok(f"✅ {action} {shell_rc_path}")
    except PermissionError:
        return Err(
            error=f"💡 I tried adding your Codeflash API key to {shell_rc_path} - but seems like I don't have permissions to do so.{LF}You'll need to open it yourself and add the following line:{LF}{LF}{api_key_line}{LF}"
        )
    except Exception as e:
        return Err(
            error=f"💡 I went to save your Codeflash API key to {shell_rc_path}, but encountered an error: {e}{LF}To ensure your Codeflash API key is automatically loaded into your environment at startup, you can create {shell_rc_path} and add the following line:{LF}{LF}{api_key_line}{LF}"
        )
