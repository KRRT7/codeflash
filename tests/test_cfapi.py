from __future__ import annotations

import json
import os
from unittest.mock import ANY, MagicMock, patch

import pytest
import requests

from codeflash.github.pr_comment import FileDiffContent, PrComment
from codeflash.models.test_results import TestResults

_empty_results = TestResults()


@pytest.fixture(autouse=True)
def clear_caches() -> None:
    from codeflash.api.cfapi import get_cfapi_base_urls, get_user_id

    get_cfapi_base_urls.cache_clear()
    get_user_id.cache_clear()


@pytest.fixture
def mock_api_key() -> MagicMock:
    with patch(
        "codeflash.api.cfapi.get_codeflash_api_key", return_value="cf-test-key-123"
    ):
        yield


@pytest.fixture
def mock_git_info() -> MagicMock:
    with (
        patch(
            "codeflash.api.cfapi.get_repo_owner_and_name",
            return_value=("test-owner", "test-repo"),
        ),
        patch("codeflash.api.cfapi.get_pr_number", return_value=42),
        patch("codeflash.api.cfapi.get_current_branch", return_value="main"),
    ):
        yield


class TestGetCfapiBaseUrls:
    def test_default_prod(self) -> None:
        from codeflash.api.cfapi import get_cfapi_base_urls

        urls = get_cfapi_base_urls()
        assert urls.cfapi_base_url == "https://app.codeflash.ai"
        assert urls.cfwebapp_base_url == "https://app.codeflash.ai"

    def test_local_env(self) -> None:
        with patch.dict(os.environ, {"CODEFLASH_CFAPI_SERVER": "local"}):
            from codeflash.api.cfapi import get_cfapi_base_urls

            get_cfapi_base_urls.cache_clear()
            urls = get_cfapi_base_urls()
            assert urls.cfapi_base_url == "http://localhost:3001"
            assert urls.cfwebapp_base_url == "http://localhost:3000"


class TestMakeCfapiRequest:
    def test_get_request(self, mock_api_key: MagicMock) -> None:
        with patch("requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_get.return_value = mock_response

            from codeflash.api.cfapi import make_cfapi_request

            response = make_cfapi_request("/cli-get-user", "GET", params={"foo": "bar"})

            mock_get.assert_called_once_with(
                "https://app.codeflash.ai/cfapi/cli-get-user",
                headers={"Authorization": "Bearer cf-test-key-123"},
                params={"foo": "bar"},
                timeout=60,
            )
            assert response.status_code == 200

    def test_post_request(self, mock_api_key: MagicMock) -> None:
        with patch("requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            from codeflash.api.cfapi import make_cfapi_request

            response = make_cfapi_request(
                "/create-pr", "POST", payload={"key": "value"}
            )

            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert call_args[0][0] == "https://app.codeflash.ai/cfapi/create-pr"
            assert json.loads(call_args[1]["data"]) == {"key": "value"}
            assert call_args[1]["headers"]["Content-Type"] == "application/json"
            assert call_args[1]["headers"]["Authorization"] == "Bearer cf-test-key-123"
            assert response.status_code == 200

    def test_http_error_not_suppressed(self, mock_api_key: MagicMock) -> None:
        with patch("requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 400
            mock_response.text = '{"error": "bad request"}'
            mock_response.json.return_value = {"error": "bad request"}
            mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
                response=mock_response
            )
            mock_post.return_value = mock_response

            from codeflash.api.cfapi import make_cfapi_request

            with patch("codeflash.api.cfapi.logger") as mock_logger:
                response = make_cfapi_request("/test", "POST", payload={"x": 1})
                mock_logger.error.assert_called_once()
                assert response.status_code == 400

    def test_http_error_suppressed(self, mock_api_key: MagicMock) -> None:
        with patch("requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 400
            mock_response.text = "error"
            mock_response.json.side_effect = ValueError
            mock_post.return_value = mock_response

            from codeflash.api.cfapi import make_cfapi_request

            with patch("codeflash.api.cfapi.logger") as mock_logger:
                response = make_cfapi_request(
                    "/test", "POST", payload={"x": 1}, suppress_errors=True
                )
                mock_logger.error.assert_not_called()
                assert response.status_code == 400


class TestGetUserId:
    def test_no_api_key_returns_none(self) -> None:
        with patch("codeflash.api.cfapi.ensure_codeflash_api_key", return_value=False):
            from codeflash.api.cfapi import get_user_id

            result = get_user_id()
            assert result is None

    def test_successful_response(self, mock_api_key: MagicMock) -> None:
        with patch("codeflash.api.cfapi.make_cfapi_request") as mock_request:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = '{"userId": "user-abc", "min_version": "0.0.1"}'
            mock_response.json.return_value = {
                "userId": "user-abc",
                "min_version": "0.0.1",
            }
            mock_request.return_value = mock_response

            from codeflash.api.cfapi import get_user_id

            result = get_user_id("cf-custom-key")
            assert result == "user-abc"

    def test_403_exits(self, mock_api_key: MagicMock) -> None:
        with patch("codeflash.api.cfapi.make_cfapi_request") as mock_request:
            mock_response = MagicMock()
            mock_response.status_code = 403
            mock_response.ok = False
            mock_request.return_value = mock_response

            from codeflash.api.cfapi import get_user_id

            with patch("codeflash.api.cfapi.exit_with_message") as mock_exit:
                get_user_id()
                mock_exit.assert_called_once_with(ANY, error_on_exit=True)


class TestSuggestChanges:
    def test_basic(self, mock_api_key: MagicMock, mock_git_info: MagicMock) -> None:
        with patch("codeflash.api.cfapi.make_cfapi_request") as mock_request:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_request.return_value = mock_response

            from codeflash.api.cfapi import suggest_changes

from codeflash.models.test_results import TestResults

            empty_results = TestResults()
            pr_comment = PrComment(
                optimization_explanation="test",
                best_runtime=100,
                original_runtime=200,
                function_name="foo",
                relative_file_path="src/foo.py",
                speedup_x="2x",
                speedup_pct="50%",
                winning_behavior_test_results=empty_results,
                winning_benchmarking_test_results=empty_results,
            )

            response = suggest_changes(
                owner="test-owner",
                repo="test-repo",
                pr_number=1,
                file_changes={
                    "src/foo.py": FileDiffContent(oldContent="old", newContent="new")
                },
                pr_comment=pr_comment,
                existing_tests="existing",
                generated_tests="generated",
                trace_id="trace-123",
                coverage_message="90%",
            )
            assert response.status_code == 200


class TestCreatePr:
    def test_basic(self, mock_api_key: MagicMock) -> None:
        with patch("codeflash.api.cfapi.make_cfapi_request") as mock_request:
            from codeflash.api.cfapi import create_pr

            mock_request.return_value.status_code = 200
            mock_request.return_value.ok = True

            pr_comment = PrComment(
                optimization_explanation="test",
                best_runtime=100,
                original_runtime=200,
                function_name="foo",
                relative_file_path="src/foo.py",
                speedup_x="2x",
                speedup_pct="50%",
                winning_behavior_test_results=_empty_results,
                winning_benchmarking_test_results=_empty_results,
            )

            response = create_pr(
                owner="o",
                repo="r",
                base_branch="main",
                file_changes={"f.py": FileDiffContent(oldContent="a", newContent="b")},
                pr_comment=pr_comment,
                existing_tests="e",
                generated_tests="g",
                trace_id="t",
                coverage_message="c",
            )
            assert response.status_code == 200


class TestMarkOptimizationSuccess:
    def test_success(self, mock_api_key: MagicMock) -> None:
        with patch("codeflash.api.cfapi.make_cfapi_request") as mock_request:
            from codeflash.api.cfapi import mark_optimization_success

            mock_request.return_value.status_code = 200

            response = mark_optimization_success("trace-1", is_optimization_found=True)
            assert response.status_code == 200
            call_payload = mock_request.call_args[1]["payload"]
            assert call_payload["trace_id"] == "trace-1"
            assert call_payload["is_optimization_found"] is True


class TestIsGithubAppInstalled:
    def test_installed(self, mock_api_key: MagicMock) -> None:
        with patch("codeflash.api.cfapi.make_cfapi_request") as mock_request:
            from codeflash.api.cfapi import is_github_app_installed_on_repo

            mock_request.return_value.ok = True
            mock_request.return_value.text = "true"

            result = is_github_app_installed_on_repo("owner", "repo")
            assert result is True

    def test_not_installed(self, mock_api_key: MagicMock) -> None:
        with patch("codeflash.api.cfapi.make_cfapi_request") as mock_request:
            from codeflash.api.cfapi import is_github_app_installed_on_repo

            mock_request.return_value.ok = True
            mock_request.return_value.text = "false"

            result = is_github_app_installed_on_repo("owner", "repo")
            assert result is False


class TestGetBlocklistedFunctions:
    def test_no_pr_number(self, mock_api_key: MagicMock) -> None:
        with patch("codeflash.api.cfapi.get_pr_number", return_value=None):
            from codeflash.api.cfapi import get_blocklisted_functions

            result = get_blocklisted_functions()
            assert result == {}

    def test_with_data(self, mock_api_key: MagicMock, mock_git_info: MagicMock) -> None:
        with patch("codeflash.api.cfapi.make_cfapi_request") as mock_request:
            from codeflash.api.cfapi import get_blocklisted_functions

            mock_request.return_value.status_code = 200
            mock_request.return_value.json.return_value = {
                "src/foo.py": ["bar()", "baz()"]
            }
            mock_request.return_value.raise_for_status = lambda: None

            result = get_blocklisted_functions()
            assert result == {"foo.py": {"bar", "baz"}}


class TestSendCompletionEmail:
    def test_success(self, mock_api_key: MagicMock, mock_git_info: MagicMock) -> None:
        with patch("codeflash.api.cfapi.make_cfapi_request") as mock_request:
            from codeflash.api.cfapi import send_completion_email

            mock_request.return_value.ok = True
            mock_request.return_value.status_code = 200

            response = send_completion_email()
            assert response.ok is True

    def test_missing_git_info_returns_500(self, mock_api_key: MagicMock) -> None:
        with patch(
            "codeflash.api.cfapi.get_repo_owner_and_name",
            side_effect=Exception("no git"),
        ):
            from codeflash.api.cfapi import send_completion_email

            response = send_completion_email()
            assert response.status_code == 500
