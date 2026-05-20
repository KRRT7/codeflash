from __future__ import annotations
from codeflash.code_utils.code_utils import exit_with_message

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import git
import requests
from pydantic.json import pydantic_encoder

from codeflash.cli_cmds.logging_config import logger, rule
from codeflash.code_utils.env_utils import (
    ensure_codeflash_api_key,
    get_codeflash_api_key,
    get_pr_number,
)
from codeflash.code_utils.git_utils import get_repo_owner_and_name
from codeflash.version import __version__

if TYPE_CHECKING:
    from requests import Response


from packaging import version


@dataclass
class BaseUrls:
    cfapi_base_url: str | None = None
    cfwebapp_base_url: str | None = None


@lru_cache(maxsize=1)
def get_cfapi_base_urls() -> BaseUrls:
    if os.environ.get("CODEFLASH_CFAPI_SERVER", "prod").lower() == "local":
        cfapi_base_url = "http://localhost:3001"
        cfwebapp_base_url = "http://localhost:3000"
        logger.info(f"Using local CF API at {cfapi_base_url}.")
        rule()
    else:
        cfapi_base_url = "https://app.codeflash.ai"
        cfwebapp_base_url = "https://app.codeflash.ai"
    return BaseUrls(cfapi_base_url=cfapi_base_url, cfwebapp_base_url=cfwebapp_base_url)


def make_cfapi_request(
    endpoint: str,
    method: str,
    payload: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
    *,
    api_key: str | None = None,
    suppress_errors: bool = False,
    params: dict[str, Any] | None = None,
) -> Response:
    """Make an HTTP request using the specified method, URL, headers, and JSON payload.

    :param endpoint: The endpoint URL to send the request to.
    :param method: The HTTP method to use ('GET', 'POST', etc.).
    :param payload: Optional JSON payload to include in the POST request body.
    :param extra_headers: Optional extra headers to include in the request.
    :param api_key: Optional API key to use for authentication.
    :param suppress_errors: If True, suppress error logging for HTTP errors.
    :param params: Optional query parameters for GET requests.
    :return: The response object from the API.
    """
    url = f"{get_cfapi_base_urls().cfapi_base_url}/cfapi{endpoint}"
    final_api_key = api_key or get_codeflash_api_key()
    cfapi_headers = {"Authorization": f"Bearer {final_api_key}"}
    if extra_headers:
        cfapi_headers.update(extra_headers)
    try:
        if method.upper() == "POST":
            json_payload = json.dumps(payload, indent=None, default=pydantic_encoder)
            cfapi_headers["Content-Type"] = "application/json"
            response = requests.post(
                url, data=json_payload, headers=cfapi_headers, timeout=60
            )
        else:
            response = requests.get(
                url, headers=cfapi_headers, params=params, timeout=60
            )
        response.raise_for_status()
        return response  # noqa: TRY300
    except requests.exceptions.HTTPError:
        # response may be either a string or JSON, so we handle both cases
        error_message = ""
        try:
            json_response = response.json()
            if "error" in json_response:
                error_message = json_response["error"]
            elif "message" in json_response:
                error_message = json_response["message"]
        except (ValueError, TypeError):
            error_message = response.text

        if not suppress_errors:
            logger.error(
                f"CF_API_Error:: making request to Codeflash API (url: {url}, method: {method}, status {response.status_code}): {error_message}"
            )
        return response


@lru_cache(maxsize=1)
def get_user_id(api_key: str | None = None) -> str | None:  # noqa: PLR0911
    """Retrieve the user's userid by making a request to the /cfapi/cli-get-user endpoint.

    :param api_key: The API key to use. If None, uses get_codeflash_api_key().
    :return: The userid or None if the request fails.
    """
    if not api_key and not ensure_codeflash_api_key():
        return None

    response = make_cfapi_request(
        endpoint="/cli-get-user",
        method="GET",
        extra_headers={"cli_version": __version__},
        api_key=api_key,
        suppress_errors=True,
    )
    if response.status_code == 200:
        if "min_version" not in response.text:
            return response.text
        resp_json = response.json()
        userid: str | None = resp_json.get("userId")
        min_version: str | None = resp_json.get("min_version")
        if userid:
            if min_version and version.parse(min_version) > version.parse(__version__):
                msg = "Your Codeflash CLI version is outdated. Please update to the latest version using `pip install --upgrade codeflash`."
                print(msg)
                exit_with_message(msg, error_on_exit=True)
            return userid

        logger.error("Failed to retrieve userid from the response.")
        return None

    if response.status_code == 403:
        error_title = (
            "Invalid Codeflash API key. The API key you provided is not valid."
        )
        msg = (
            f"{error_title}\n"
            "Please generate a new one at https://app.codeflash.ai/app/apikeys ,\n"
            "then set it as a CODEFLASH_API_KEY environment variable.\n"
            "For more information, refer to the documentation at \n"
            "https://docs.codeflash.ai/optimizing-with-codeflash/codeflash-github-actions#manual-setup\n"
            "or\n"
            "https://docs.codeflash.ai/optimizing-with-codeflash/codeflash-github-actions#automated-setup-recommended"
        )
        exit_with_message(msg, error_on_exit=True)

    # For other errors, log and return None (backward compatibility)
    logger.error(
        f"Failed to look up your userid; is your CF API key valid? ({response.reason})"
    )
    return None


def is_github_app_installed_on_repo(
    owner: str, repo: str, *, suppress_errors: bool = False
) -> bool:
    """Check if the Codeflash GitHub App is installed on the specified repository.

    :param owner: The owner of the repository.
    :param repo: The name of the repository.
    :param suppress_errors: If True, suppress error logging when the app is not installed.
    :return: True if the app is installed, False otherwise.
    """
    response = make_cfapi_request(
        endpoint=f"/is-github-app-installed?repo={repo}&owner={owner}",
        method="GET",
        suppress_errors=suppress_errors,
    )
    return response.ok and response.text == "true"


def get_blocklisted_functions() -> dict[str, set[str]] | dict[str, Any]:
    """Retrieve blocklisted functions for the current pull request.

    Returns A dictionary mapping filenames to sets of blocklisted function names.
    """
    pr_number = get_pr_number()
    if pr_number is None:
        return {}

    try:
        owner, repo = get_repo_owner_and_name()
        information = {"pr_number": pr_number, "repo_owner": owner, "repo_name": repo}

        req = make_cfapi_request(
            endpoint="/verify-existing-optimizations",
            method="POST",
            payload=information,
        )
        req.raise_for_status()
        content: dict[str, list[str]] = req.json()
    except Exception as e:
        logger.error(f"Error getting blocklisted functions: {e}")
        return {}

    return {
        Path(k).name: {v.replace("()", "") for v in values}
        for k, values in content.items()
    }


def is_function_being_optimized_again(
    owner: str, repo: str, pr_number: int, code_contexts: list[dict[str, str]]
) -> Any:  # noqa: ANN401
    """Check if the function being optimized is being optimized again."""
    response = make_cfapi_request(
        "/is-already-optimized",
        "POST",
        {
            "owner": owner,
            "repo": repo,
            "pr_number": pr_number,
            "code_contexts": code_contexts,
        },
    )
    response.raise_for_status()
    return response.json()


def add_code_context_hash(code_context_hash: str) -> None:
    """Add code context to the DB cache."""
    pr_number = get_pr_number()
    if pr_number is None:
        return
    try:
        owner, repo = get_repo_owner_and_name()
        pr_number = get_pr_number()
    except git.exc.InvalidGitRepositoryError:
        return

    if owner and repo and pr_number is not None:
        make_cfapi_request(
            "/add-code-hash",
            "POST",
            {
                "owner": owner,
                "repo": repo,
                "pr_number": pr_number,
                "code_hash": code_context_hash,
            },
        )


def mark_optimization_success(
    trace_id: str, *, is_optimization_found: bool
) -> Response:
    """Mark an optimization event as success or not.

    :param trace_id: The unique identifier for the optimization event.
    :param is_optimization_found: Boolean indicating whether the optimization was found.
    :return: The response object from the API.
    """
    payload = {"trace_id": trace_id, "is_optimization_found": is_optimization_found}
    return make_cfapi_request(
        endpoint="/mark-as-success", method="POST", payload=payload
    )


def send_completion_email() -> Response:
    """Send an email notification when codeflash --all completes."""
    try:
        owner, repo = get_repo_owner_and_name()
    except Exception as e:
        logger.error(f"Error determining repository owner and repo: {e}")
        response = requests.Response()
        response.status_code = 500
        return response
    payload = {"owner": owner, "repo": repo}
    return make_cfapi_request(
        endpoint="/send-completion-email", method="POST", payload=payload
    )
