from __future__ import annotations

import webbrowser
from pathlib import Path

import git
from git import Repo

from codeflash.api.pr_api import setup_github_actions
from codeflash.cli_cmds.cli_common import apologize_and_exit, confirm
from codeflash.cli_cmds.logging_config import logger
from codeflash.cli_cmds.workflow_generator import generate_dynamic_workflow_content
from codeflash.code_utils.compat import LF
from codeflash.code_utils.config_parser import parse_config_file
from codeflash.code_utils.env_utils import get_codeflash_api_key
from codeflash.code_utils.git_utils import get_current_branch, get_repo_owner_and_name
from codeflash.code_utils.github_utils import get_github_secrets_page_url


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
            print(
                "✅ GitHub Actions workflow file already exists.\n\nNo changes needed - your repository is already configured!"
            )
            logger.info(
                "[install_github_actions] Workflow file already exists locally, skipping setup"
            )
            return

        git_remote = config.get("git_remote", "origin")
        try:
            base_branch = get_current_branch(repo)
        except Exception as e:
            logger.warning(
                f"[install_github_actions] Could not determine current branch: {e}. Falling back to 'main'."
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
                "Run GitHub Actions in benchmark mode?", default=True
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
            "[install_github_actions] User confirmed, generating workflow content..."
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
                f"[install_github_actions] Failed to get repository owner and name: {e}"
            )
            workflows_path.mkdir(parents=True, exist_ok=True)
            optimize_yaml_path.write_text(
                materialized_optimize_yml_content, encoding="utf-8"
            )
            print(
                f"✅ Created GitHub action workflow at {optimize_yaml_path}\n\nYour repository is now configured for continuous optimization!"
            )
        else:
            try:
                print("Creating PR with GitHub Actions workflow...")
                logger.info(
                    f"[install_github_actions] Calling setup_github_actions API for {owner}/{repo_name} on branch {base_branch}"
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
                            print(
                                f"✅ PR created: {pr_url}\n\nYour repository is now configured for continuous optimization!"
                            )
                            logger.info(
                                f"[install_github_actions] Successfully created PR #{response_data.get('pr_number')} for {owner}/{repo_name}"
                            )
                        else:
                            pr_created_via_api = True
                            print(
                                "✅ Workflow file already exists with the same content.\n\nNo changes needed - your repository is already configured!"
                            )
                    else:
                        error_data = response_data
                        error_msg = error_data.get("error", "Unknown error")
                        error_message = error_data.get("message", error_msg)
                        error_help = error_data.get("help", "")
                        installation_url = error_data.get("installation_url")

                        if response.status_code == 403:
                            logger.error(
                                f"[install_github_actions] Permission denied for {owner}/{repo_name}"
                            )
                            url_403 = error_data.get(
                                "installation_url",
                                "https://github.com/apps/codeflash-ai/installations/select_target",
                            )
                            print(
                                f"❌ Access Denied\n\nThe GitHub App may not be installed on {owner}/{repo_name}...\n💡 To fix this:\n1. Install the CodeFlash GitHub App on your repository\n2. Ensure the app has 'Contents: write', 'Workflows: write', and 'Pull requests: write' permissions\n3. Make sure you have write access to the repository\n\n🔗 Install GitHub App: {url_403}"
                            )
                            print(
                                f"\nPlease install the CodeFlash GitHub App and ensure it has the required permissions.{LF}Visit: {url_403}{LF}"
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
                                f"[install_github_actions] GitHub App not installed on {owner}/{repo_name}"
                            )
                            print(
                                f"Please install the CodeFlash GitHub App on your repository to continue.{LF}Visit: {installation_url}{LF}"
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
                                f"[install_github_actions] Permission denied for {owner}/{repo_name}"
                            )
                            url_403 = error_data.get(
                                "installation_url",
                                "https://github.com/apps/codeflash-ai/installations/select_target",
                            )
                            print(
                                f"❌ Access Denied\n\nThe GitHub App may not be installed on {owner}/{repo_name}...\n💡 To fix this:\n1. Install the CodeFlash GitHub App on your repository\n2. Ensure the app has 'Contents: write', 'Workflows: write', and 'Pull requests: write' permissions\n3. Make sure you have write access to the repository\n\n🔗 Install GitHub App: {url_403}"
                            )
                            print(
                                f"\nPlease install the CodeFlash GitHub App and ensure it has the required permissions.{LF}Visit: {url_403}{LF}"
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
                                f"[install_github_actions] GitHub App not installed on {owner}/{repo_name}"
                            )
                            print(
                                f"Please install the CodeFlash GitHub App on your repository to continue.{LF}Visit: {installation_url}{LF}"
                            )
                            return
                        if response.status_code == 401:
                            logger.error(
                                f"[install_github_actions] Authentication failed for {owner}/{repo_name}"
                            )
                            print(
                                f"Authentication failed. Please check your API key and try again.{LF}"
                            )
                            return
                        raise Exception(error_message)
                    except (ValueError, KeyError) as parse_error:
                        raise Exception(
                            f"API returned status {response.status_code}"
                        ) from parse_error

            except Exception as api_error:
                logger.warning(
                    f"[install_github_actions] API call failed, falling back to local file creation: {api_error}"
                )
                workflows_path.mkdir(parents=True, exist_ok=True)
                optimize_yaml_path.write_text(
                    materialized_optimize_yml_content, encoding="utf-8"
                )
                print(
                    f"✅ Created GitHub action workflow at {optimize_yaml_path}\n\nYour repository is now configured for continuous optimization!"
                )

        if pr_created_via_api:
            if pr_url:
                print(
                    f"🚀 Codeflash is now configured to automatically optimize new Github PRs!{LF}Once you merge the PR, the workflow will be active.{LF}"
                )
            else:
                print(
                    f"🚀 Codeflash is now configured to automatically optimize new Github PRs!{LF}The workflow is ready to use.{LF}"
                )
        else:
            print(
                f"Please edit, commit and push this GitHub actions file to your repo, and you're all set!{LF}🚀 Codeflash is now configured to automatically optimize new Github PRs!{LF}"
            )

        try:
            existing_api_key = get_codeflash_api_key()
        except OSError:
            existing_api_key = None

        secrets_message = "🔐 Next Step: Add API Key as GitHub Secret\n\nYou'll need to add your CODEFLASH_API_KEY as a secret to your GitHub repository.\n\n📋 Steps:\n1. Press Enter to open your repo's secrets page\n2. Click 'New repository secret'\n3. Add your API key with the variable name CODEFLASH_API_KEY"
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
