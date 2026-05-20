from __future__ import annotations

import os
import webbrowser

import git

from codeflash.api.cfapi import get_user_id, is_github_app_installed_on_repo
from codeflash.cli_cmds.cli_common import (
    apologize_and_exit,
    prompt_choice,
    prompt_text,
)
from codeflash.code_utils.compat import LF
from codeflash.code_utils.env_utils import get_codeflash_api_key
from codeflash.code_utils.git_utils import get_git_remotes, get_repo_owner_and_name
from codeflash.code_utils.oauth_handler import perform_oauth_signin
from codeflash.code_utils.shell_utils import get_shell_rc_path, save_api_key_to_rc


def install_github_app(git_remote: str) -> None:
    try:
        git_repo = git.Repo(search_parent_directories=True)
    except git.InvalidGitRepositoryError:
        print(
            "Skipping GitHub app installation because you're not in a git repository."
        )
        return

    if git_remote not in get_git_remotes(git_repo):
        print(
            f"Skipping GitHub app installation, remote ({git_remote}) does not exist in this repository."
        )
        return

    owner, repo = get_repo_owner_and_name(git_repo, git_remote)

    if is_github_app_installed_on_repo(owner, repo, suppress_errors=True):
        print(
            f"🐙 Looks like you've already installed the Codeflash GitHub app on this repository ({owner}/{repo})! Continuing…"
        )

    else:
        try:
            input(
                f"Finally, you'll need to install the Codeflash GitHub app by choosing the repository you want to install Codeflash on.{LF}"
                f"I will attempt to open the github app page - https://github.com/apps/codeflash-ai/installations/select_target {LF}"
                f"Please, press ENTER to open the app installation page{LF}"
                ">>> "
            )
            webbrowser.open(
                "https://github.com/apps/codeflash-ai/installations/select_target"
            )
            input(
                f"Please, press ENTER once you've finished installing the github app from https://github.com/apps/codeflash-ai/installations/select_target{LF}"
                ">>> "
            )

            count = 2
            while not is_github_app_installed_on_repo(
                owner, repo, suppress_errors=True
            ):
                if count == 0:
                    print(
                        f"❌ It looks like the Codeflash GitHub App is not installed on the repository {owner}/{repo}.{LF}"
                        f"You won't be able to create PRs with Codeflash until you install the app.{LF}"
                        f"In the meantime you can make local only optimizations by using the '--no-pr' flag with codeflash.{LF}"
                    )
                    break
                input(
                    f"❌ It looks like the Codeflash GitHub App is not installed on the repository {owner}/{repo}.{LF}"
                    f"Please install it from https://github.com/apps/codeflash-ai/installations/select_target {LF}"
                    f"Please, press ENTER to continue once you've finished installing the github app…{LF}"
                    ">>> "
                )
                count -= 1
        except (KeyboardInterrupt, EOFError):
            print()


def validate_cfapi_key(value: str) -> str | None:
    value = value.strip()
    if not value.startswith("cf-") and value:
        print(
            "That key seems to be invalid. It should start with a 'cf-' prefix. Please try again."
        )
        return None
    return value


# Returns True if the user entered a new API key, False if they used an existing one
def prompt_api_key() -> bool:
    """Prompt user for API key via OAuth or manual entry."""
    try:
        existing_api_key = get_codeflash_api_key()
    except OSError:
        existing_api_key = None

    if existing_api_key:
        display_key = f"{existing_api_key[:3]}****{existing_api_key[-4:]}"
        print(
            f"🔑 I found a CODEFLASH_API_KEY in your environment [{display_key}]!\n\n✅ You're all set with API authentication!"
        )
        return False

    auth_choices = ["🔐 Login in with Codeflash", "🔑 Use Codeflash API key"]

    method = prompt_choice(
        "How would you like to authenticate?",
        auth_choices,
        default=auth_choices[0],
    )

    if method == auth_choices[1]:
        enter_api_key_and_save_to_rc()
        return True

    api_key = perform_oauth_signin()

    if not api_key:
        apologize_and_exit()

    shell_rc_path = get_shell_rc_path()
    if not shell_rc_path.exists() and os.name == "nt":
        shell_rc_path.touch()
        print(f"✅ Created {shell_rc_path}")

    result = save_api_key_to_rc(api_key)
    if result.is_ok():
        print(result.unwrap())
        print("✅ Signed in successfully and API key saved!")
    else:
        print(result.failure())
        input("Press any key to continue...")

    os.environ["CODEFLASH_API_KEY"] = api_key
    return True


def enter_api_key_and_save_to_rc() -> None:
    browser_launched = False
    api_key = ""
    while api_key == "":
        api_key = prompt_text(
            f"Enter your Codeflash API key{' [or press Enter to open your API key page]' if not browser_launched else ''}",
            default="",
        )
        if not api_key:
            break
        if not browser_launched:
            print(
                f"Opening your Codeflash API key page. Grab a key from there!{LF}"
                "You can also open this link manually: https://app.codeflash.ai/app/apikeys"
            )
            webbrowser.open("https://app.codeflash.ai/app/apikeys")
            browser_launched = True
    shell_rc_path = get_shell_rc_path()
    if not shell_rc_path.exists() and os.name == "nt":
        shell_rc_path.parent.mkdir(parents=True, exist_ok=True)
        shell_rc_path.touch()
        print(f"✅ Created {shell_rc_path}")
    get_user_id(api_key=api_key)
    result = save_api_key_to_rc(api_key)
    if result.is_ok():
        print(result.unwrap())
    else:
        print(result.failure())
        input("Press any key to continue...")

    os.environ["CODEFLASH_API_KEY"] = api_key
