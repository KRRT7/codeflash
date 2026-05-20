from __future__ import annotations

import sys
import webbrowser
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING, Any

import git
import tomlkit
from git import Repo

from codeflash.api.aiservice import AiServiceClient
from codeflash.api.cfapi import setup_github_actions
from codeflash.cli_cmds.cli_common import (
    apologize_and_exit,
    confirm,
)
from codeflash.cli_cmds.logging_config import logger
from codeflash.code_utils.compat import LF
from codeflash.code_utils.config_parser import parse_config_file
from codeflash.code_utils.env_utils import (
    get_codeflash_api_key,
)
from codeflash.code_utils.git_utils import (
    get_current_branch,
    get_repo_owner_and_name,
)
from codeflash.code_utils.github_utils import get_github_secrets_page_url

if TYPE_CHECKING:
    pass


class DependencyManager(Enum):
    PIP = auto()
    POETRY = auto()
    UV = auto()
    UNKNOWN = auto()


def install_github_actions(override_formatter_check: bool = False) -> None:
    try:
        config, _config_file_path = parse_config_file(
            override_formatter_check=override_formatter_check
        )

        try:
            repo = Repo(config["module_root"], search_parent_directories=True)
        except git.InvalidGitRepositoryError:
            print(
                "Skipping GitHub action installation for continuous optimization because you're not in a git repository."
            )
            return

        git_root = Path(repo.git.rev_parse("--show-toplevel"))
        workflows_path = git_root / ".github" / "workflows"
        optimize_yaml_path = workflows_path / "codeflash.yaml"

        if optimize_yaml_path.exists():
            already_exists_message = (
                "✅ GitHub Actions workflow file already exists.\n\n"
            )
            already_exists_message += (
                "No changes needed - your repository is already configured!"
            )
            print(already_exists_message)
            logger.info(
                "[workflow_generator:install_github_actions] Workflow file already exists locally, skipping setup"
            )
            return

        git_remote = config.get("git_remote", "origin")
        try:
            base_branch = get_current_branch(repo)
        except Exception as e:
            logger.warning(
                f"[workflow_generator:install_github_actions] Could not determine current branch: {e}. Falling back to 'main'."
            )
            base_branch = "main"

        from importlib.resources import files

        benchmark_mode = False
        benchmarks_root = config.get("benchmarks_root", "").strip()
        if benchmarks_root and benchmarks_root != "":
            print(
                "📊 Benchmark Mode Available\n\nI noticed you've configured a benchmarks_root in your config. Benchmark mode will show the performance impact of Codeflash's optimizations on your benchmarks."
            )
            benchmark_mode = confirm(
                "Run GitHub Actions in benchmark mode?",
                default=True,
            )

        print(
            "🤖 GitHub Actions Setup\n\nGitHub Actions will automatically optimize your code in every pull request. This is the recommended way to use Codeflash for continuous optimization."
        )

        confirm_creation = confirm(
            "Set up GitHub Actions for continuous optimization? We'll open a pull request with the workflow file.",
            default=True,
        )
        if not confirm_creation:
            print("⏩️ Skipping GitHub Actions setup.")
            return

        logger.info(
            "[workflow_generator:install_github_actions] User confirmed, generating workflow content..."
        )
        optimize_yml_content = (
            files("codeflash")
            .joinpath("cli_cmds", "workflows", "codeflash-optimize.yaml")
            .read_text(encoding="utf-8")
        )
        materialized_optimize_yml_content = generate_dynamic_workflow_content(
            optimize_yml_content, config, git_root, benchmark_mode
        )

        workflows_path.mkdir(parents=True, exist_ok=True)

        pr_created_via_api = False
        pr_url = None

        try:
            owner, repo_name = get_repo_owner_and_name(repo, git_remote)
        except Exception as e:
            logger.error(
                f"[workflow_generator:install_github_actions] Failed to get repository owner and name: {e}"
            )
            workflows_path.mkdir(parents=True, exist_ok=True)
            with optimize_yaml_path.open("w", encoding="utf8") as optimize_yml_file:
                optimize_yml_file.write(materialized_optimize_yml_content)
            print(
                f"✅ Created GitHub action workflow at {optimize_yaml_path}\n\nYour repository is now configured for continuous optimization!"
            )
        else:
            try:
                print("Creating PR with GitHub Actions workflow...")
                logger.info(
                    f"[workflow_generator:install_github_actions] Calling setup_github_actions API for {owner}/{repo_name} on branch {base_branch}"
                )

                response = setup_github_actions(
                    owner=owner,
                    repo=repo_name,
                    base_branch=base_branch,
                    workflow_content=materialized_optimize_yml_content,
                )

                if response.status_code == 200:
                    response_data = response.json()
                    if response_data.get("success"):
                        pr_url = response_data.get("pr_url")
                        if pr_url:
                            pr_created_via_api = True
                            success_message = f"✅ PR created: {pr_url}\n\n"
                            success_message += "Your repository is now configured for continuous optimization!"
                            print(success_message)
                            logger.info(
                                f"[workflow_generator:install_github_actions] Successfully created PR #{response_data.get('pr_number')} for {owner}/{repo_name}"
                            )
                        else:
                            pr_created_via_api = True
                            already_exists_message = "✅ Workflow file already exists with the same content.\n\n"
                            already_exists_message += "No changes needed - your repository is already configured!"
                            print(already_exists_message)
                    else:
                        error_data = response_data
                        error_msg = error_data.get("error", "Unknown error")
                        error_message = error_data.get("message", error_msg)
                        error_help = error_data.get("help", "")
                        installation_url = error_data.get("installation_url")

                        if response.status_code == 403:
                            logger.error(
                                f"[workflow_generator:install_github_actions] Permission denied for {owner}/{repo_name}"
                            )
                            installation_url_403 = error_data.get(
                                "installation_url",
                                "https://github.com/apps/codeflash-ai/installations/select_target",
                            )
                            print(
                                "❌ Access Denied\n\nThe GitHub App may not be installed on "
                                + f"{owner}/{repo_name}, or it doesn't have the required permissions.\n\n💡 To fix this:\n1. Install the CodeFlash GitHub App on your repository\n2. Ensure the app has 'Contents: write', 'Workflows: write', and 'Pull requests: write' permissions\n3. Make sure you have write access to the repository\n\n🔗 Install GitHub App: {installation_url_403}"
                            )
                            print()
                            print(
                                f"Please install the CodeFlash GitHub App and ensure it has the required permissions.{LF}"
                                f"Visit: {installation_url_403}{LF}"
                            )
                            apologize_and_exit()

                        error_panel_text = f"❌ {error_msg}\n\n{error_message}\n"
                        if error_help:
                            error_panel_text += f"\n💡 {error_help}\n"
                        if installation_url:
                            error_panel_text += (
                                f"\n🔗 Install GitHub App: {installation_url}"
                            )
                        print(error_panel_text)

                        if response.status_code == 404 and installation_url:
                            logger.error(
                                f"[workflow_generator:install_github_actions] GitHub App not installed on {owner}/{repo_name}"
                            )
                            print(
                                f"Please install the CodeFlash GitHub App on your repository to continue.{LF}"
                                f"Visit: {installation_url}{LF}"
                            )
                            return

                        raise Exception(error_message)
                else:
                    try:
                        error_data = response.json()
                        error_msg = error_data.get("error", "API request failed")
                        error_message = error_data.get(
                            "message", f"API returned status {response.status_code}"
                        )
                        error_help = error_data.get("help", "")
                        installation_url = error_data.get("installation_url")

                        if response.status_code == 403:
                            logger.error(
                                f"[workflow_generator:install_github_actions] Permission denied for {owner}/{repo_name}"
                            )
                            installation_url_403 = error_data.get(
                                "installation_url",
                                "https://github.com/apps/codeflash-ai/installations/select_target",
                            )
                            print(
                                "❌ Access Denied\n\nThe GitHub App may not be installed on "
                                + f"{owner}/{repo_name}, or it doesn't have the required permissions.\n\n💡 To fix this:\n1. Install the CodeFlash GitHub App on your repository\n2. Ensure the app has 'Contents: write', 'Workflows: write', and 'Pull requests: write' permissions\n3. Make sure you have write access to the repository\n\n🔗 Install GitHub App: {installation_url_403}"
                            )
                            print()
                            print(
                                f"Please install the CodeFlash GitHub App and ensure it has the required permissions.{LF}"
                                f"Visit: {installation_url_403}{LF}"
                            )
                            apologize_and_exit()

                        error_panel_text = f"❌ {error_msg}\n\n{error_message}\n"
                        if error_help:
                            error_panel_text += f"\n💡 {error_help}\n"
                        if installation_url:
                            error_panel_text += (
                                f"\n🔗 Install GitHub App: {installation_url}"
                            )
                        print(error_panel_text)

                        if response.status_code == 404 and installation_url:
                            logger.error(
                                f"[workflow_generator:install_github_actions] GitHub App not installed on {owner}/{repo_name}"
                            )
                            print(
                                f"Please install the CodeFlash GitHub App on your repository to continue.{LF}"
                                f"Visit: {installation_url}{LF}"
                            )
                            return

                        if response.status_code == 401:
                            logger.error(
                                f"[workflow_generator:install_github_actions] Authentication failed for {owner}/{repo_name}"
                            )
                            print(
                                f"Authentication failed. Please check your API key and try again.{LF}"
                            )
                            return

                        raise Exception(error_message)
                    except (ValueError, KeyError) as parse_error:
                        status_msg = f"API returned status {response.status_code}"
                        raise Exception(status_msg) from parse_error

            except Exception as api_error:
                logger.warning(
                    f"[workflow_generator:install_github_actions] API call failed, falling back to local file creation: {api_error}"
                )
                workflows_path.mkdir(parents=True, exist_ok=True)
                with optimize_yaml_path.open("w", encoding="utf8") as optimize_yml_file:
                    optimize_yml_file.write(materialized_optimize_yml_content)
                print(
                    f"✅ Created GitHub action workflow at {optimize_yaml_path}\n\nYour repository is now configured for continuous optimization!"
                )

        if pr_created_via_api:
            if pr_url:
                print(
                    f"🚀 Codeflash is now configured to automatically optimize new Github PRs!{LF}"
                    f"Once you merge the PR, the workflow will be active.{LF}"
                )
            else:
                print(
                    f"🚀 Codeflash is now configured to automatically optimize new Github PRs!{LF}"
                    f"The workflow is ready to use.{LF}"
                )
        else:
            print(
                f"Please edit, commit and push this GitHub actions file to your repo, and you're all set!{LF}"
                f"🚀 Codeflash is now configured to automatically optimize new Github PRs!{LF}"
            )

        try:
            existing_api_key = get_codeflash_api_key()
        except OSError:
            existing_api_key = None

        secrets_message = (
            "🔐 Next Step: Add API Key as GitHub Secret\n\n"
            "You'll need to add your CODEFLASH_API_KEY as a secret to your GitHub repository.\n\n"
            "📋 Steps:\n"
            "1. Press Enter to open your repo's secrets page\n"
            "2. Click 'New repository secret'\n"
            "3. Add your API key with the variable name CODEFLASH_API_KEY"
        )
        if existing_api_key:
            secrets_message += f"\n\n🔑 Your API Key: {existing_api_key}"
        print(secrets_message)

        print(f"\n📍 Press Enter to open: {get_github_secrets_page_url(repo)}")
        input()
        webbrowser.open(get_github_secrets_page_url(repo))

        print(
            "🐙 I opened your GitHub secrets page!\n\nNote: If you see a 404, you probably don't have access to this repo's secrets. Ask a repo admin to add it for you, or (not recommended) you can temporarily hard-code your API key into the workflow file."
        )
        input("Press any key to continue...")
    except KeyboardInterrupt:
        apologize_and_exit()


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
            str(workflow_file.relative_to(git_root))
            for workflow_file in workflows_path.glob("*.yml")
        )
        important_files.extend(
            str(workflow_file.relative_to(git_root))
            for workflow_file in workflows_path.glob("*.yaml")
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
                        if subitem.is_dir():
                            dir_dict["contents"][subitem.name] = {"type": "directory"}
                        else:
                            dir_dict["contents"][subitem.name] = {"type": "file"}
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
            f"I couldn't find a pyproject.toml in the current directory.{LF}"
            f"Please create a new empty pyproject.toml file here, OR if you use poetry then run `poetry init`, OR run `codeflash init` again from a directory with an existing pyproject.toml file."
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
                            if current_indent < 8:
                                indented_steps.append(" " * 8 + line.lstrip())
                            else:
                                indented_steps.append(line)
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
            f"I couldn't find a pyproject.toml in the current directory.{LF}"
            f"Please create a new empty pyproject.toml file here, OR if you use poetry then run `poetry init`, OR run `codeflash init` again from a directory with an existing pyproject.toml file."
        )
        apologize_and_exit()

    working_dir = get_github_action_working_directory(toml_path, git_root)
    optimize_yml_content = optimize_yml_content.replace(
        "{{ working_directory }}", working_dir
    )
    dep_manager = determine_dependency_manager(pyproject_data)

    python_depmanager_installation = get_dependency_manager_installation_string(
        dep_manager
    )
    optimize_yml_content = optimize_yml_content.replace(
        "{{ setup_python_dependency_manager }}", python_depmanager_installation
    )
    install_deps_cmd = get_dependency_installation_commands(dep_manager)

    optimize_yml_content = optimize_yml_content.replace(
        "{{ install_dependencies_command }}", install_deps_cmd
    )

    codeflash_cmd = get_codeflash_github_action_command(dep_manager)
    if benchmark_mode:
        codeflash_cmd += " --benchmark"
    return optimize_yml_content.replace("{{ codeflash_command }}", codeflash_cmd)
