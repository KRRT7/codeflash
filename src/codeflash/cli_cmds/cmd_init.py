from __future__ import annotations
from codeflash.code_utils.path_utils import validate_relative_directory_path

import os
import re
import subprocess
import sys
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Union, cast

import tomlkit
from git import InvalidGitRepositoryError, Repo
from pydantic.dataclasses import dataclass

from codeflash.cli_cmds.cli_common import (
    apologize_and_exit,
    confirm,
    prompt_choice,
    prompt_text,
)
from codeflash.cli_cmds.github_setup import install_github_app, prompt_api_key
from codeflash.cli_cmds.logging_config import logger, rule
from codeflash.cli_cmds.extension import install_vscode_extension
from codeflash.cli_cmds.workflow_generator import install_github_actions
from codeflash.code_utils.compat import LF
from codeflash.code_utils.config_parser import parse_config_file
from codeflash.code_utils.env_utils import check_formatter_installed
from codeflash.code_utils.git_utils import get_git_remotes
from codeflash.version import __version__ as version

if TYPE_CHECKING:
    from argparse import Namespace

CODEFLASH_LOGO: str = (
    f"{LF}"
    r"                   _          ___  _               _     " + f"{LF}"
    r"                  | |        / __)| |             | |    " + f"{LF}"
    r"  ____   ___    _ | |  ____ | |__ | |  ____   ___ | | _  " + f"{LF}"
    r" / ___) / _ \  / || | / _  )|  __)| | / _  | /___)| || \ " + f"{LF}"
    r"( (___ | |_| |( (_| |( (/ / | |   | |( ( | ||___ || | | |" + f"{LF}"
    r" \____) \___/  \____| \____)|_|   |_| \_||_|(___/ |_| |_|" + f"{LF}"
    f"{('v' + version).rjust(66)}{LF}"
    f"{LF}"
)


@dataclass(frozen=True)
class CLISetupInfo:
    module_root: str
    tests_root: str
    benchmarks_root: Union[str, None]
    ignore_paths: list[str]
    formatter: Union[str, list[str]]
    git_remote: str


@dataclass(frozen=True)
class VsCodeSetupInfo:
    module_root: str
    tests_root: str
    formatter: Union[str, list[str]]


class CommonSections(Enum):
    module_root = "module_root"
    tests_root = "tests_root"
    formatter_cmds = "formatter_cmds"

    def get_toml_key(self) -> str:
        return self.value.replace("_", "-")


def init_codeflash() -> None:
    try:
        print("⚡️ Welcome to Codeflash!\n\nThis setup will take just a few minutes.")

        did_add_new_key = prompt_api_key()

        should_modify, config = should_modify_pyproject_toml()

        git_remote = config.get("git_remote", "origin") if config else "origin"

        if should_modify:
            setup_info: CLISetupInfo = collect_setup_info()
            git_remote = setup_info.git_remote
            configured = configure_pyproject_toml(setup_info)
            if not configured:
                apologize_and_exit()

        install_github_app(git_remote)

        install_github_actions(override_formatter_check=True)

        install_vscode_extension()

        module_string = ""
        if "setup_info" in locals():
            module_string = f" you selected ({setup_info.module_root})"

        completion_message = (
            "⚡️ Codeflash is now set up!\n\nYou can now run any of these commands:"
        )

        if did_add_new_key:
            completion_message += "\n\n🐚 Don't forget to restart your shell to load the CODEFLASH_API_KEY environment variable!"
            if os.name == "nt":
                from codeflash.code_utils.shell_utils import (
                    get_shell_rc_path,
                    is_powershell,
                )

                reload_cmd = (
                    f". {get_shell_rc_path()}"
                    if is_powershell()
                    else f"call {get_shell_rc_path()}"
                )
            else:
                from codeflash.code_utils.shell_utils import get_shell_rc_path

                reload_cmd = f"source {get_shell_rc_path()}"
            completion_message += f"\nOr run: {reload_cmd}"

        print(completion_message)
        print()
        print(
            "  codeflash --file <path-to-file> --function <function-name>  —  Optimize a specific function within a file"
        )
        print(
            "  codeflash optimize <myscript.py>  —  Trace and find the best optimizations for a script"
        )
        print("  codeflash --all  —  Optimize all functions in all files")
        print("  codeflash --help  —  See all available options")

        sys.exit(0)
    except KeyboardInterrupt:
        apologize_and_exit()


def ask_run_end_to_end_test(args: Namespace) -> None:
    run_tests = confirm(
        "⚡️ Do you want to run a sample optimization to make sure everything's set up correctly? (takes about 3 minutes)",
        default=True,
    )

    rule()

    if run_tests:
        file_path = create_find_common_tags_file(args, "find_common_tags.py")
        run_end_to_end_test(args, file_path)


def config_found(pyproject_toml_path: Union[str, Path]) -> tuple[bool, str]:
    pyproject_toml_path = Path(pyproject_toml_path)

    if not pyproject_toml_path.exists():
        return False, f"Configuration file not found: {pyproject_toml_path}"

    if not pyproject_toml_path.is_file():
        return False, f"Configuration file is not a file: {pyproject_toml_path}"

    if pyproject_toml_path.suffix != ".toml":
        return False, f"Configuration file is not a .toml file: {pyproject_toml_path}"

    return True, ""


def is_valid_pyproject_toml(
    pyproject_toml_path: Union[str, Path],
) -> tuple[bool, dict[str, Any] | None, str]:
    pyproject_toml_path = Path(pyproject_toml_path)
    try:
        config, _ = parse_config_file(pyproject_toml_path)
    except Exception as e:
        return False, None, f"Failed to parse configuration: {e}"

    module_root = config.get("module_root")
    if not module_root:
        return False, config, "Missing required field: 'module_root'"

    if not Path(module_root).is_dir():
        return (
            False,
            config,
            f"Invalid 'module_root': directory does not exist at {module_root}",
        )

    tests_root = config.get("tests_root")
    if not tests_root:
        return False, config, "Missing required field: 'tests_root'"

    if not Path(tests_root).is_dir():
        return (
            False,
            config,
            f"Invalid 'tests_root': directory does not exist at {tests_root}",
        )

    return True, config, ""


def should_modify_pyproject_toml() -> tuple[bool, dict[str, Any] | None]:
    pyproject_toml_path = Path.cwd() / "pyproject.toml"

    found, _ = config_found(pyproject_toml_path)
    if not found:
        return True, None

    valid, config, _message = is_valid_pyproject_toml(pyproject_toml_path)
    if not valid:
        return True, None

    return confirm(
        "✅ A valid Codeflash config already exists in this project. Do you want to re-configure it?",
        default=False,
    ), config


@lru_cache(maxsize=1)
def get_valid_subdirs(current_dir: Path | None = None) -> list[str]:
    ignore_subdirs = [
        "venv",
        "node_modules",
        "dist",
        "build",
        "build_temp",
        "build_scripts",
        "env",
        "logs",
        "tmp",
        "__pycache__",
    ]
    path_str = str(current_dir) if current_dir else "."
    return [
        d
        for d in next(os.walk(path_str))[1]
        if not d.startswith(".") and not d.startswith("__") and d not in ignore_subdirs
    ]


def get_suggestions(section: str) -> tuple[list[str], str | None]:
    valid_subdirs = get_valid_subdirs()
    if section == CommonSections.module_root.value:
        return [d for d in valid_subdirs if d != "tests"], None
    if section == CommonSections.tests_root.value:
        default = "tests" if "tests" in valid_subdirs else None
        return valid_subdirs, default
    if section == CommonSections.formatter_cmds.value:
        return ["disabled", "ruff", "black"], "disabled"
    msg = f"Unknown section: {section}"
    raise ValueError(msg)


def collect_setup_info() -> CLISetupInfo:
    curdir = Path.cwd()
    if not os.access(curdir, os.W_OK):
        print(
            f"❌ The current directory isn't writable, please check your folder permissions and try again.{LF}"
        )
        print("It's likely you don't have write permissions for this folder.")
        sys.exit(1)

    project_name = check_for_toml_or_setup_file()
    valid_module_subdirs, _ = get_suggestions(CommonSections.module_root.value)

    curdir_option = f"current directory ({curdir})"
    custom_dir_option = "enter a custom directory…"
    module_subdir_options = [*valid_module_subdirs, curdir_option, custom_dir_option]

    print(
        "📁 Let's identify your Python module directory.\n\nThis is usually the top-level directory containing all your Python source code."
    )
    module_root_answer = prompt_choice(
        "Which Python module do you want me to optimize?",
        module_subdir_options,
        default=(
            project_name
            if project_name in module_subdir_options
            else module_subdir_options[0]
        ),
    )
    if module_root_answer == curdir_option:
        module_root = "."
    elif module_root_answer == custom_dir_option:
        print(
            "📂 Enter a custom module directory path.\n\nPlease provide the path to your Python module directory."
        )
        module_root = None
        while module_root is None:
            custom_path_str = prompt_text(
                "Enter the path to your module directory",
            )
            is_valid, error_msg = validate_relative_directory_path(custom_path_str)
            if not is_valid:
                print(f"❌ Invalid path: {error_msg}")
                print("Please enter a valid relative directory path.")
                print()
                continue
            module_root = Path(custom_path_str)
    else:
        module_root = module_root_answer

    create_for_me_option = f"🆕 Create a new tests{os.pathsep} directory for me!"
    tests_suggestions, default_tests_subdir = get_suggestions(
        CommonSections.tests_root.value
    )
    test_subdir_options = [
        sub_dir for sub_dir in tests_suggestions if sub_dir != module_root
    ]
    if "tests" not in tests_suggestions:
        test_subdir_options.append(create_for_me_option)
    custom_dir_option = "📁 Enter a custom directory…"
    test_subdir_options.append(custom_dir_option)

    print(
        "🧪 Now let's locate your test directory.\n\nThis is where all your test files are stored. If you don't have tests yet, I can create a directory for you!"
    )

    tests_root_answer = prompt_choice(
        "Where are your tests located?",
        test_subdir_options,
        default=(default_tests_subdir or test_subdir_options[0]),
    )

    if tests_root_answer == create_for_me_option:
        tests_root = Path(curdir) / (default_tests_subdir or "tests")
        tests_root.mkdir()
        print(f"✅ Created directory {tests_root}{os.path.sep}{LF}")
    elif tests_root_answer == custom_dir_option:
        print(
            "🧪 Enter a custom test directory path.\n\nPlease provide the path to your test directory, relative to the current directory."
        )
        tests_root = None
        while tests_root is None:
            custom_tests_path_str = prompt_text(
                "Enter the path to your tests directory",
            )
            is_valid, error_msg = validate_relative_directory_path(
                custom_tests_path_str
            )
            if not is_valid:
                print(f"❌ Invalid path: {error_msg}")
                print("Please enter a valid relative directory path.")
                print()
                continue
            tests_root = Path(curdir) / Path(custom_tests_path_str)
    else:
        tests_root = Path(curdir) / Path(cast("str", tests_root_answer))

    tests_root = tests_root.relative_to(curdir)

    resolved_module_root = (Path(curdir) / Path(module_root)).resolve()
    resolved_tests_root = (Path(curdir) / Path(tests_root)).resolve()
    if resolved_module_root == resolved_tests_root:
        logger.warning(
            "It looks like your tests root is the same as your module root. This is not recommended and can lead to unexpected behavior."
        )

    benchmarks_root = None

    print(
        "🎨 Let's configure your code formatter.\n\nCode formatters help maintain consistent code style. Codeflash will use this to format optimized code."
    )

    formatter = prompt_choice(
        "Which code formatter do you use?",
        ["black", "ruff", "other", "don't use a formatter"],
        default="black",
    )

    git_remote = ""
    try:
        repo = Repo(str(module_root), search_parent_directories=True)
        git_remotes = get_git_remotes(repo)
        if git_remotes:
            if len(git_remotes) > 1:
                print(
                    "🔗 Configure Git Remote for Pull Requests.\n\nCodeflash will use this remote to create pull requests with optimized code."
                )
                git_remote = prompt_choice(
                    "Which git remote should Codeflash use for Pull Requests?",
                    git_remotes,
                    default="origin",
                )
            else:
                git_remote = git_remotes[0]
        else:
            print(
                "No git remotes found. You can still use Codeflash locally, but you'll need to set up a remote "
                "repository to use GitHub features."
            )
    except InvalidGitRepositoryError:
        git_remote = ""

    ignore_paths: list[str] = []
    return CLISetupInfo(
        module_root=str(module_root),
        tests_root=str(tests_root),
        benchmarks_root=str(benchmarks_root) if benchmarks_root else None,
        ignore_paths=ignore_paths,
        formatter=cast("str", formatter),
        git_remote=str(git_remote),
    )


def check_for_toml_or_setup_file() -> str | None:
    print()
    print("Checking for pyproject.toml or setup.py…\r", end="", flush=True)
    curdir = Path.cwd()
    pyproject_toml_path = curdir / "pyproject.toml"
    setup_py_path = curdir / "setup.py"
    project_name = None
    if pyproject_toml_path.exists():
        try:
            pyproject_toml_content = pyproject_toml_path.read_text(encoding="utf8")
            project_name = tomlkit.parse(pyproject_toml_content)["tool"]["poetry"][
                "name"
            ]
            print(f"✅ I found a pyproject.toml for your project {project_name}.")
        except Exception:
            print("✅ I found a pyproject.toml for your project.")
    else:
        if setup_py_path.exists():
            setup_py_content = setup_py_path.read_text(encoding="utf8")
            project_name_match = re.search(
                r"setup\s*\([^)]*?name\s*=\s*['\"](.*?)['\"]",
                setup_py_content,
                re.DOTALL,
            )
            if project_name_match:
                project_name = project_name_match.group(1)
                print(f"✅ Found setup.py for your project {project_name}")
            else:
                print("✅ Found setup.py.")
        print(
            f"💡 No pyproject.toml found in {curdir}.\n\nThis file is essential for Codeflash to store its configuration.\nPlease ensure you are running `codeflash init` from your project's root directory."
        )
        create_toml = confirm(
            "Create pyproject.toml in the current directory?",
            default=True,
        )
        if create_toml:
            create_empty_pyproject_toml(pyproject_toml_path)
    print()
    return cast("str", project_name)


def create_empty_pyproject_toml(pyproject_toml_path: Path) -> None:
    new_pyproject_toml = tomlkit.document()
    new_pyproject_toml["tool"] = {"codeflash": {}}
    try:
        pyproject_toml_path.write_text(
            tomlkit.dumps(new_pyproject_toml), encoding="utf8"
        )
        if pyproject_toml_path.exists():
            print(
                f"✅ Created a pyproject.toml file at {pyproject_toml_path}\n\nYour project is now ready for Codeflash configuration!"
            )
            print("\n📍 Press Enter to continue...")
            input()
    except OSError:
        print(
            "❌ Failed to create pyproject.toml. Please check your disk permissions and available space."
        )
        apologize_and_exit()


def get_formatter_cmds(formatter: str) -> list[str]:
    if formatter == "black":
        return ["black $file"]
    if formatter == "ruff":
        return ["ruff check --exit-zero --fix $file", "ruff format $file"]
    if formatter == "other":
        print(
            "🔧 In pyproject.toml, please replace 'your-formatter' with the command you use to format your code."
        )
        return ["your-formatter $file"]
    if formatter in {"don't use a formatter", "disabled"}:
        return ["disabled"]
    if " && " in formatter:
        return formatter.split(" && ")
    return [formatter]


def configure_pyproject_toml(
    setup_info: Union[VsCodeSetupInfo, CLISetupInfo], config_file: Path | None = None
) -> bool:
    for_vscode = isinstance(setup_info, VsCodeSetupInfo)
    toml_path = config_file or Path.cwd() / "pyproject.toml"
    try:
        with toml_path.open(encoding="utf8") as pyproject_file:
            pyproject_data = tomlkit.parse(pyproject_file.read())
    except FileNotFoundError:
        print(
            f"I couldn't find a pyproject.toml in the current directory.{LF}"
            f"Please create a new empty pyproject.toml file here, OR if you use poetry then run `poetry init`, OR run `codeflash init` again from a directory with an existing pyproject.toml file."
        )
        return False

    codeflash_section = tomlkit.table()
    codeflash_section.add(
        tomlkit.comment("All paths are relative to this pyproject.toml's directory.")
    )

    if for_vscode:
        for section in CommonSections:
            if hasattr(setup_info, section.value):
                codeflash_section[section.get_toml_key()] = getattr(
                    setup_info, section.value
                )
    else:
        codeflash_section["module-root"] = setup_info.module_root
        codeflash_section["tests-root"] = setup_info.tests_root
        codeflash_section["ignore-paths"] = setup_info.ignore_paths
        if setup_info.git_remote not in ["", "origin"]:
            codeflash_section["git-remote"] = setup_info.git_remote
        codeflash_section.add(tomlkit.nl())

    formatter = setup_info.formatter

    formatter_cmds = (
        formatter if isinstance(formatter, list) else get_formatter_cmds(formatter)
    )

    check_formatter_installed(formatter_cmds, exit_on_failure=False)
    codeflash_section["formatter-cmds"] = formatter_cmds
    tool_section = pyproject_data.get("tool", tomlkit.table())

    if for_vscode:
        existing_codeflash = tool_section.get("codeflash", tomlkit.table())
        for key, value in codeflash_section.items():
            existing_codeflash[key] = value
        tool_section["codeflash"] = existing_codeflash
    else:
        tool_section["codeflash"] = codeflash_section

    pyproject_data["tool"] = tool_section

    with toml_path.open("w", encoding="utf8") as pyproject_file:
        pyproject_file.write(tomlkit.dumps(pyproject_data))
    print(f"Added Codeflash configuration to {toml_path}")
    print()
    return True


def create_find_common_tags_file(args: Namespace, file_name: str) -> Path:
    find_common_tags_content = """from __future__ import annotations


def find_common_tags(articles: list[dict[str, list[str]]]) -> set[str]:
    if not articles:
        return set()

    common_tags = articles[0].get("tags", [])
    for article in articles[1:]:
        common_tags = [tag for tag in common_tags if tag in article.get("tags", [])]
    return set(common_tags)
"""

    file_path = Path(args.module_root) / file_name
    if file_path.exists():
        overwrite = confirm(
            f"🤔 {file_path} already exists. Do you want to overwrite it?",
            default=True,
        )
        if not overwrite:
            apologize_and_exit()
        rule()

    file_path.write_text(find_common_tags_content, encoding="utf8")
    logger.info(f"Created demo optimization file: {file_path}")

    return file_path


def create_bubble_sort_file_and_test(args: Namespace) -> tuple[str, str]:
    bubble_sort_content = """from typing import Union, List
def sorter(arr: Union[List[int],List[float]]) -> Union[List[int],List[float]]:
    for i in range(len(arr)):
        for j in range(len(arr) - 1):
            if arr[j] > arr[j + 1]:
                temp = arr[j]
                arr[j] = arr[j + 1]
                arr[j + 1] = temp
    return arr
"""
    bubble_sort_test_content = f"""from {Path(args.module_root).name}.bubble_sort import sorter

def test_sort():
    input = [5, 4, 3, 2, 1, 0]
    output = sorter(input)
    assert output == [0, 1, 2, 3, 4, 5]

    input = [5.0, 4.0, 3.0, 2.0, 1.0, 0.0]
    output = sorter(input)
    assert output == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]

    input = list(reversed(range(500)))
    output = sorter(input)
    assert output == list(range(500))
"""

    bubble_sort_path = Path(args.module_root) / "bubble_sort.py"
    if bubble_sort_path.exists():
        overwrite = confirm(
            f"🤔 {bubble_sort_path} already exists. Do you want to overwrite it?",
            default=True,
        )
        if not overwrite:
            apologize_and_exit()
        rule()

    bubble_sort_path.write_text(bubble_sort_content, encoding="utf8")

    bubble_sort_test_path = Path(args.tests_root) / "test_bubble_sort.py"
    bubble_sort_test_path.write_text(bubble_sort_test_content, encoding="utf8")

    for path in [bubble_sort_path, bubble_sort_test_path]:
        logger.info(f"✅ Created {path}")
        rule()

    return str(bubble_sort_path), str(bubble_sort_test_path)


def run_end_to_end_test(args: Namespace, find_common_tags_path: Path) -> None:
    try:
        check_formatter_installed(args.formatter_cmds)
    except Exception:
        logger.error(
            "Formatter not found. Review the formatter_cmds in your pyproject.toml file and make sure the formatter is installed."
        )
        return

    command = [
        "codeflash",
        "--file",
        "find_common_tags.py",
        "--function",
        "find_common_tags",
    ]
    if args.no_pr:
        command.append("--no-pr")
    if args.verbose:
        command.append("--verbose")

    logger.info("Running sample optimization…")
    rule()

    try:
        output = []
        with subprocess.Popen(
            command,
            text=True,
            cwd=args.module_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ) as process:
            if process.stdout:
                for line in process.stdout:
                    stripped = line.strip()
                    print(stripped)
                    output.append(stripped)
            process.wait()
        rule()
        if process.returncode == 0:
            logger.info("End-to-end test passed. Codeflash has been correctly set up!")
        else:
            logger.error(
                "End-to-end test failed. Please check the logs above, and take a look at https://docs.codeflash.ai/getting-started/local-installation for help and troubleshooting."
            )
    finally:
        rule()
        logger.info("🧹 Cleaning up…")
        find_common_tags_path.unlink(missing_ok=True)
        logger.info(f"🗑️  Deleted {find_common_tags_path}")
