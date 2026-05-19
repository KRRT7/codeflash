from __future__ import annotations

import os
import site
from functools import lru_cache
from pathlib import Path


def module_name_from_file_path(
    file_path: Path, project_root_path: Path, *, traverse_up: bool = False
) -> str:
    try:
        relative_path = file_path.resolve().relative_to(project_root_path.resolve())
        return relative_path.with_suffix("").as_posix().replace("/", ".")
    except ValueError:
        if traverse_up:
            parent = file_path.parent
            while parent not in (project_root_path, parent.parent):
                try:
                    relative_path = file_path.resolve().relative_to(parent.resolve())
                    return relative_path.with_suffix("").as_posix().replace("/", ".")
                except ValueError:
                    parent = parent.parent
        msg = f"File {file_path} is not within the project root {project_root_path}."
        raise ValueError(msg)


def file_path_from_module_name(module_name: str, project_root_path: Path) -> Path:
    return project_root_path / (module_name.replace(".", os.sep) + ".py")


@lru_cache(maxsize=100)
def file_name_from_test_module_name(
    test_module_name: str, base_dir: Path
) -> Path | None:
    partial_test_class = test_module_name
    while partial_test_class:
        test_path = file_path_from_module_name(partial_test_class, base_dir)
        if (base_dir / test_path).exists():
            return base_dir / test_path
        partial_test_class = ".".join(partial_test_class.split(".")[:-1])
    return None


def path_belongs_to_site_packages(file_path: Path) -> bool:
    file_path_resolved = file_path.resolve()
    site_packages = [Path(p).resolve() for p in site.getsitepackages()]
    return any(
        file_path_resolved.is_relative_to(site_package_path)
        for site_package_path in site_packages
    )


_INVALID_CHARS_NT = {"<", ">", ":", '"', "|", "?", "*"}
_INVALID_CHARS_UNIX = {"\0"}


def validate_relative_directory_path(path: str) -> tuple[bool, str]:
    if not path or not path.strip():
        return False, "Path cannot be empty"
    path = path.strip()
    normalized = path.replace("\\", "/")
    if ".." in normalized:
        return (
            False,
            "Path cannot contain '..'. Use a relative path like 'tests' or 'src/app' instead",
        )
    error_msg = ""
    if Path(path).is_absolute():
        error_msg = "Path must be relative, not absolute"
    elif os.name == "nt":
        if any(char in _INVALID_CHARS_NT for char in path):
            error_msg = "Path contains invalid characters for this operating system"
    elif "\0" in path:
        error_msg = "Path contains invalid characters for this operating system"
    else:
        try:
            Path(path)
        except (ValueError, OSError) as e:
            error_msg = f"Invalid path format: {e!s}"
    if error_msg:
        return False, error_msg
    return True, ""
