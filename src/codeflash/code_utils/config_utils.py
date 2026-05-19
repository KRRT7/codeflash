from __future__ import annotations

import configparser
import re
from contextlib import contextmanager
from pathlib import Path

import tomlkit

from codeflash.cli_cmds.logging_config import logger
from codeflash.code_utils.config_parser import (
    find_pyproject_toml,
    get_all_closest_config_files,
)

ImportErrorPattern = re.compile(r"ModuleNotFoundError.*$", re.MULTILINE)

BLACKLIST_ADDOPTS = (
    "--benchmark",
    "--sugar",
    "--codespeed",
    "--cov",
    "--profile",
    "--junitxml",
    "-n",
)


def filter_args(addopts_args: list[str]) -> list[str]:
    blacklist = BLACKLIST_ADDOPTS
    n = len(addopts_args)
    filtered_args = []
    i = 0
    while i < n:
        current_arg = addopts_args[i]
        if current_arg.startswith(blacklist):
            i += 1
            if i < n and not addopts_args[i].startswith("-"):
                i += 1
        else:
            filtered_args.append(current_arg)
            i += 1
    return filtered_args


def modify_addopts(config_file: Path) -> tuple[str, bool]:
    file_type = config_file.suffix.lower()
    filename = config_file.name
    config = None
    if file_type not in {".toml", ".ini", ".cfg"} or not config_file.exists():
        return "", False
    with Path.open(config_file, encoding="utf-8") as f:
        content = f.read()
    try:
        was_modified = False
        if filename == "pyproject.toml":
            data = tomlkit.parse(content)
            original_addopts = (
                data.get("tool", {})
                .get("pytest", {})
                .get("ini_options", {})
                .get("addopts", "")
            )
            if isinstance(original_addopts, list):
                original_addopts = " ".join(original_addopts)
            original_addopts = original_addopts.replace("=", " ")
            addopts_args = original_addopts.split()
            new_addopts_args = filter_args(addopts_args)
            if new_addopts_args != addopts_args:
                data["tool"]["pytest"]["ini_options"]["addopts"] = " ".join(
                    new_addopts_args
                )
                was_modified = True
            norecursedirs = (
                data.get("tool", {})
                .get("pytest", {})
                .get("ini_options", {})
                .get("norecursedirs")
            )
            if norecursedirs:
                del data["tool"]["pytest"]["ini_options"]["norecursedirs"]
                was_modified = True
            if not was_modified:
                return content, False
            with Path.open(config_file, "w", encoding="utf-8") as f:
                f.write(tomlkit.dumps(data))
                return content, True
        else:
            config = configparser.ConfigParser()
            config.read_string(content)
            data = {section: dict(config[section]) for section in config.sections()}
            if config_file.name in {"pytest.ini", ".pytest.ini", "tox.ini"}:
                original_addopts = data.get("pytest", {}).get("addopts", "")
            else:
                original_addopts = data.get("tool:pytest", {}).get("addopts", "")
            original_addopts = original_addopts.replace("=", " ")
            addopts_args = original_addopts.split()
            new_addopts_args = filter_args(addopts_args)
            if new_addopts_args != addopts_args:
                if config_file.name in {"pytest.ini", ".pytest.ini", "tox.ini"}:
                    config.set("pytest", "addopts", " ".join(new_addopts_args))
                else:
                    config.set("tool:pytest", "addopts", " ".join(new_addopts_args))
                was_modified = True
            section = (
                "pytest"
                if config_file.name in {"pytest.ini", ".pytest.ini", "tox.ini"}
                else "tool:pytest"
            )
            if config.has_option(section, "norecursedirs"):
                config.remove_option(section, "norecursedirs")
                was_modified = True
            if not was_modified:
                return content, False
            with Path.open(config_file, "w", encoding="utf-8") as f:
                config.write(f)
                return content, True
    except Exception:
        logger.debug("Trouble parsing")
        return content, False


@contextmanager
def custom_addopts() -> None:
    closest_config_files = get_all_closest_config_files()
    original_content = {}
    try:
        for config_file in closest_config_files:
            original_content[config_file] = modify_addopts(config_file)
        yield
    finally:
        for file, (content, was_modified) in original_content.items():
            if was_modified:
                with Path.open(file, "w", encoding="utf-8") as f:
                    f.write(content)


@contextmanager
def add_addopts_to_pyproject() -> None:
    pyproject_file = find_pyproject_toml()
    original_content = None
    try:
        if pyproject_file.exists():
            with Path.open(pyproject_file, encoding="utf-8") as f:
                original_content = f.read()
                data = tomlkit.parse(original_content)
            data["tool"]["pytest"] = {}
            data["tool"]["pytest"]["ini_options"] = {}
            data["tool"]["pytest"]["ini_options"]["addopts"] = [
                "-n=auto",
                "-n",
                "1",
                "-n 1",
                "-n      1",
                "-n      auto",
            ]
            with Path.open(pyproject_file, "w", encoding="utf-8") as f:
                f.write(tomlkit.dumps(data))
        yield
    finally:
        with Path.open(pyproject_file, "w", encoding="utf-8") as f:
            f.write(original_content)


def get_qualified_name(module_name: str, full_qualified_name: str) -> str:
    if not full_qualified_name:
        msg = "full_qualified_name cannot be empty"
        raise ValueError(msg)
    if not full_qualified_name.startswith(module_name):
        msg = f"{full_qualified_name} does not start with {module_name}"
        raise ValueError(msg)
    if module_name == full_qualified_name:
        msg = f"{full_qualified_name} is the same as {module_name}"
        raise ValueError(msg)
    return full_qualified_name[len(module_name) + 1 :]
