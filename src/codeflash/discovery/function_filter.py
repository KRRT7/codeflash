from __future__ import annotations

import ast
import os
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

import git

from codeflash.api.cfapi import get_blocklisted_functions
from codeflash.cli_cmds.logging_config import logger, rule
from codeflash.code_utils.env_utils import get_pr_number
from codeflash.code_utils.git_utils import get_repo_owner_and_name
from codeflash.code_utils.path_utils import (
    module_name_from_file_path,
    path_belongs_to_site_packages,
)
from codeflash.models.config import AppConfig

if TYPE_CHECKING:
    from codeflash.discovery.functions_to_optimize import FunctionToOptimize
    from codeflash.models.domain import CodeOptimizationContext


def is_git_repo(file_path: str) -> bool:
    try:
        git.Repo(file_path, search_parent_directories=True)
        return True
    except git.InvalidGitRepositoryError:
        return False


@cache
def ignored_submodule_paths(module_root: str) -> list[Path]:
    if is_git_repo(module_root):
        git_repo = git.Repo(module_root, search_parent_directories=True)
        try:
            return [
                Path(git_repo.working_tree_dir, submodule.path).resolve()
                for submodule in git_repo.submodules
            ]
        except Exception as e:
            logger.warning(f"Error getting submodule paths: {e}")
    return []


def was_function_previously_optimized(
    function_to_optimize: FunctionToOptimize,
    code_context: CodeOptimizationContext,
    config: AppConfig,
) -> bool:
    try:
        owner, repo = get_repo_owner_and_name()
    except git.exc.InvalidGitRepositoryError:
        logger.warning("No git repository found")
        owner, repo = None, None
    pr_number = get_pr_number()

    if not owner or not repo or pr_number is None or config.no_pr:
        return False

    code_contexts = []
    func_hash = code_context.hashing_code_context_hash

    code_contexts.append(
        {
            "file_path": function_to_optimize.file_path,
            "function_name": function_to_optimize.qualified_name,
            "code_hash": func_hash,
        }
    )

    if not code_contexts:
        return False

    try:
        from codeflash.api.cfapi import is_function_being_optimized_again

        result = is_function_being_optimized_again(
            owner, repo, pr_number, code_contexts
        )
        already_optimized_paths: list[tuple[str, str]] = result.get(
            "already_optimized_tuples", []
        )
        return len(already_optimized_paths) > 0
    except Exception as e:
        logger.warning(f"Failed to check optimization status: {e}")
        return False


def filter_functions(
    modified_functions: dict[Path, list[FunctionToOptimize]],
    tests_root: Path,
    ignore_paths: list[Path],
    project_root: Path,
    module_root: Path,
    *,
    disable_logs: bool = False,
) -> tuple[dict[Path, list[FunctionToOptimize]], int]:
    filtered_modified_functions: dict[str, list[FunctionToOptimize]] = {}
    blocklist_funcs = get_blocklisted_functions()
    logger.debug(f"Blocklisted functions: {blocklist_funcs}")

    submodule_paths = ignored_submodule_paths(module_root)

    functions_count: int = 0
    test_functions_removed_count: int = 0
    non_modules_removed_count: int = 0
    site_packages_removed_count: int = 0
    ignore_paths_removed_count: int = 0
    malformed_paths_count: int = 0
    submodule_ignored_paths_count: int = 0
    blocklist_funcs_removed_count: int = 0
    tests_root_str = os.path.normcase(str(tests_root))
    module_root_str = os.path.normcase(str(module_root))

    for file_path_path, functions in modified_functions.items():
        _functions = functions
        file_path = str(file_path_path)
        file_path_normalized = os.path.normcase(file_path)
        if file_path_normalized.startswith(tests_root_str + os.sep):
            test_functions_removed_count += len(_functions)
            continue
        if file_path in ignore_paths or any(
            file_path_normalized.startswith(os.path.normcase(str(ignore_path)) + os.sep)
            for ignore_path in ignore_paths
        ):
            ignore_paths_removed_count += 1
            continue
        if file_path in submodule_paths or any(
            file_path_normalized.startswith(
                os.path.normcase(str(submodule_path)) + os.sep
            )
            for submodule_path in submodule_paths
        ):
            submodule_ignored_paths_count += 1
            continue
        if path_belongs_to_site_packages(Path(file_path)):
            site_packages_removed_count += len(_functions)
            continue
        if not file_path_normalized.startswith(module_root_str + os.sep):
            non_modules_removed_count += len(_functions)
            continue
        try:
            ast.parse(
                f"import {module_name_from_file_path(Path(file_path), project_root)}"
            )
        except SyntaxError:
            malformed_paths_count += 1
            continue

        if blocklist_funcs:
            functions_tmp = []
            for function in _functions:
                if (
                    function.file_path.name in blocklist_funcs
                    and function.qualified_name
                    in blocklist_funcs[function.file_path.name]
                ):
                    blocklist_funcs_removed_count += 1
                    continue
                functions_tmp.append(function)
            _functions = functions_tmp

        filtered_modified_functions[file_path] = _functions
        functions_count += len(_functions)

    if not disable_logs:
        log_info = {
            "Test functions removed": (test_functions_removed_count, "yellow"),
            "Site-package functions removed": (site_packages_removed_count, "magenta"),
            "Non-importable file paths": (malformed_paths_count, "red"),
            "Functions outside module-root": (non_modules_removed_count, "cyan"),
            "Files from ignored paths": (ignore_paths_removed_count, "blue"),
            "Files from ignored submodules": (
                submodule_ignored_paths_count,
                "bright_black",
            ),
            "Blocklisted functions removed": (
                blocklist_funcs_removed_count,
                "bright_red",
            ),
        }
        ignored_items = [
            (label, count) for label, (count, _) in log_info.items() if count > 0
        ]
        if ignored_items:
            print("Ignored functions and files:")
            for label, count in ignored_items:
                print(f"  {label}: {count}")
            rule()
    return {
        Path(k): v for k, v in filtered_modified_functions.items() if v
    }, functions_count


def filter_files_optimized(
    file_path: Path, tests_root: Path, ignore_paths: list[Path], module_root: Path
) -> bool:
    submodule_paths = None
    if file_path.is_relative_to(tests_root):
        return False
    if file_path in ignore_paths or any(
        file_path.is_relative_to(ignore_path) for ignore_path in ignore_paths
    ):
        return False
    if path_belongs_to_site_packages(file_path):
        return False
    if not file_path.is_relative_to(module_root):
        return False
    if submodule_paths is None:
        submodule_paths = ignored_submodule_paths(module_root)
    return not (
        file_path in submodule_paths
        or any(
            file_path.is_relative_to(submodule_path)
            for submodule_path in submodule_paths
        )
    )
