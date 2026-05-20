from __future__ import annotations

import sys
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING, Any

import tomlkit

from codeflash.api.aiservice import AiServiceClient
from codeflash.cli_cmds.cli_common import apologize_and_exit
from codeflash.cli_cmds.logging_config import logger
from codeflash.code_utils.compat import LF

if TYPE_CHECKING:
    pass


class DependencyManager(Enum):
    PIP = auto()
    POETRY = auto()
    UV = auto()
    UNKNOWN = auto()


def determine_dependency_manager(pyproject_data: dict[str, Any]) -> DependencyManager:
    if (Path.cwd() / "poetry.lock").exists():
        return DependencyManager.POETRY
    if (Path.cwd() / "uv.lock").exists():
        return DependencyManager.UV
    if "tool" not in pyproject_data:
        return DependencyManager.PIP
    tool_section = pyproject_data["tool"]
    if "poetry" in tool_section:
        return DependencyManager.POETRY
    if any(key.startswith("uv") for key in tool_section):
        return DependencyManager.UV
    if "pip" in tool_section or "setuptools" in tool_section:
        return DependencyManager.PIP
    return DependencyManager.UNKNOWN


def get_codeflash_github_action_command(dep_manager: DependencyManager) -> str:
    if dep_manager == DependencyManager.POETRY:
        return """|
          poetry env use python
          poetry run codeflash"""
    if dep_manager == DependencyManager.UV:
        return "uv run codeflash"
    return "codeflash"


def get_dependency_installation_commands(
    dep_manager: DependencyManager,
) -> tuple[str, str]:
    if dep_manager == DependencyManager.POETRY:
        return """|
          python -m pip install --upgrade pip
          pip install poetry
          poetry install --all-extras"""
    if dep_manager == DependencyManager.UV:
        return """|
          uv sync --all-extras
          uv pip install --upgrade codeflash"""
    return """|
      python -m pip install --upgrade pip
      pip install -r requirements.txt
      pip install codeflash"""


def get_dependency_manager_installation_string(dep_manager: DependencyManager) -> str:
    py_version = sys.version_info
    python_version_string = f"'{py_version.major}.{py_version.minor}'"
    if dep_manager == DependencyManager.UV:
        return """name: 🐍 Setup UV
        uses: astral-sh/setup-uv@v6
        with:
          enable-cache: true"""
    return f"""name: 🐍 Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: {python_version_string}"""


def get_github_action_working_directory(toml_path: Path, git_root: Path) -> str:
    if toml_path.parent == git_root:
        return ""
    working_dir = str(toml_path.parent.relative_to(git_root))
    return f"""defaults:
      run:
        working-directory: ./{working_dir}"""


def collect_repo_files_for_workflow(git_root: Path) -> dict[str, Any]:
    important_files = [
        "pyproject.toml",
        "requirements.txt",
        "requirements-dev.txt",
        "requirements/requirements.txt",
        "requirements/dev.txt",
        "Pipfile",
        "Pipfile.lock",
        "poetry.lock",
        "uv.lock",
        "setup.py",
        "setup.cfg",
        "Dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
        "Makefile",
        "README.md",
        "README.rst",
    ]
    workflows_path = git_root / ".github" / "workflows"
    if workflows_path.exists():
        important_files.extend(
            str(wf.relative_to(git_root)) for wf in workflows_path.glob("*.yml")
        )
        important_files.extend(
            str(wf.relative_to(git_root)) for wf in workflows_path.glob("*.yaml")
        )
    files_dict: dict[str, str] = {}
    max_file_size = 8 * 1024
    for file_path_str in important_files:
        file_path = git_root / file_path_str
        if file_path.exists() and file_path.is_file():
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
                if len(content) > max_file_size:
                    content = content[:max_file_size] + "\n... (truncated)"
                files_dict[file_path_str] = content
            except Exception as e:
                logger.warning(
                    f"[workflow_generator:collect_repo_files_for_workflow] Failed to read {file_path_str}: {e}"
                )
    directory_structure: dict[str, Any] = {}
    try:
        for item in sorted(git_root.iterdir()):
            if item.name.startswith(".") and item.name not in [".github", ".git"]:
                continue
            if item.is_dir():
                dir_dict: dict[str, Any] = {"type": "directory", "contents": {}}
                try:
                    for subitem in sorted(item.iterdir()):
                        if subitem.name.startswith("."):
                            continue
                        dir_dict["contents"][subitem.name] = {
                            "type": "directory" if subitem.is_dir() else "file"
                        }
                except PermissionError:
                    pass
                directory_structure[item.name] = dir_dict
            elif item.is_file():
                directory_structure[item.name] = {"type": "file"}
    except Exception as e:
        logger.warning(
            f"[workflow_generator:collect_repo_files_for_workflow] Error collecting directory structure: {e}"
        )
    return {"files": files_dict, "directory_structure": directory_structure}


def generate_dynamic_workflow_content(
    optimize_yml_content: str,
    config: tuple[dict[str, Any], Path],
    git_root: Path,
    benchmark_mode: bool = False,
) -> str:
    module_path = str(Path(config["module_root"]).relative_to(git_root) / "**")
    optimize_yml_content = optimize_yml_content.replace(
        "{{ codeflash_module_path }}", module_path
    )
    toml_path = Path.cwd() / "pyproject.toml"
    try:
        with toml_path.open(encoding="utf8") as pyproject_file:
            pyproject_data = tomlkit.parse(pyproject_file.read())
    except FileNotFoundError:
        print(
            f"I couldn't find a pyproject.toml in the current directory.{LF}Please create a new empty pyproject.toml file here, OR if you use poetry then run `poetry init`, OR run `codeflash init` again from a directory with an existing pyproject.toml file."
        )
        apologize_and_exit()
    working_dir = get_github_action_working_directory(toml_path, git_root)
    optimize_yml_content = optimize_yml_content.replace(
        "{{ working_directory }}", working_dir
    )
    try:
        repo_data = collect_repo_files_for_workflow(git_root)
        codeflash_config = {
            "module_root": config["module_root"],
            "tests_root": config.get("tests_root", ""),
            "benchmark_mode": benchmark_mode,
        }
        aiservice_client = AiServiceClient()
        dynamic_steps = aiservice_client.generate_workflow_steps(
            repo_files=repo_data["files"],
            directory_structure=repo_data["directory_structure"],
            codeflash_config=codeflash_config,
        )
        if dynamic_steps:
            steps_start = optimize_yml_content.find("    steps:")
            if steps_start != -1:
                lines = optimize_yml_content.split("\n")
                steps_start_line = optimize_yml_content[:steps_start].count("\n")
                steps_end_line = len(lines)
                for i in range(steps_start_line + 1, len(lines)):
                    line = lines[i]
                    if line and not line.startswith(" ") and not line.startswith("\t"):
                        steps_end_line = i
                        break
                steps_content = dynamic_steps
                if steps_content.startswith("steps:"):
                    steps_content = steps_content[6:].lstrip("\n")
                indented_steps = []
                for line in steps_content.split("\n"):
                    if line.strip():
                        if not line.startswith(" "):
                            indented_steps.append("        " + line)
                        else:
                            current_indent = len(line) - len(line.lstrip())
                            indented_steps.append(
                                " " * 8 + line.lstrip() if current_indent < 8 else line
                            )
                    else:
                        indented_steps.append("")
                dep_manager = determine_dependency_manager(pyproject_data)
                codeflash_cmd = get_codeflash_github_action_command(dep_manager)
                if benchmark_mode:
                    codeflash_cmd += " --benchmark"
                if "|" in codeflash_cmd:
                    cmd_lines = codeflash_cmd.split("\n")
                    codeflash_step = f"      - name: ⚡️Codeflash Optimization\n        run: {cmd_lines[0].strip()}"
                    for cmd_line in cmd_lines[1:]:
                        codeflash_step += f"\n          {cmd_line.strip()}"
                else:
                    codeflash_step = f"      - name: ⚡️Codeflash Optimization\n        run: {codeflash_cmd}"
                indented_steps.append(codeflash_step)
                return "\n".join(
                    [
                        *lines[:steps_start_line],
                        "    steps:",
                        *indented_steps,
                        *lines[steps_end_line:],
                    ]
                )
            logger.warning(
                "[workflow_generator:generate_dynamic_workflow_content] Could not find steps section in template"
            )
        else:
            logger.debug(
                "[workflow_generator:generate_dynamic_workflow_content] AI service returned no steps, falling back to static"
            )
    except Exception as e:
        logger.warning(
            f"[workflow_generator:generate_dynamic_workflow_content] Error generating dynamic workflow, falling back to static: {e}"
        )
    return customize_codeflash_yaml_content(
        optimize_yml_content, config, git_root, benchmark_mode
    )


def customize_codeflash_yaml_content(
    optimize_yml_content: str,
    config: tuple[dict[str, Any], Path],
    git_root: Path,
    benchmark_mode: bool = False,
) -> str:
    module_path = str(Path(config["module_root"]).relative_to(git_root) / "**")
    optimize_yml_content = optimize_yml_content.replace(
        "{{ codeflash_module_path }}", module_path
    )
    toml_path = Path.cwd() / "pyproject.toml"
    try:
        with toml_path.open(encoding="utf8") as pyproject_file:
            pyproject_data = tomlkit.parse(pyproject_file.read())
    except FileNotFoundError:
        print(
            f"I couldn't find a pyproject.toml in the current directory.{LF}Please create a new empty pyproject.toml file here..."
        )
        apologize_and_exit()
    working_dir = get_github_action_working_directory(toml_path, git_root)
    optimize_yml_content = optimize_yml_content.replace(
        "{{ working_directory }}", working_dir
    )
    dep_manager = determine_dependency_manager(pyproject_data)
    optimize_yml_content = optimize_yml_content.replace(
        "{{ setup_python_dependency_manager }}",
        get_dependency_manager_installation_string(dep_manager),
    )
    optimize_yml_content = optimize_yml_content.replace(
        "{{ install_dependencies_command }}",
        get_dependency_installation_commands(dep_manager),
    )
    codeflash_cmd = get_codeflash_github_action_command(dep_manager)
    if benchmark_mode:
        codeflash_cmd += " --benchmark"
    return optimize_yml_content.replace("{{ codeflash_command }}", codeflash_cmd)
