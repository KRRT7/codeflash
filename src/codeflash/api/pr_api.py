from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from requests import Response

from codeflash.api.cfapi import make_cfapi_request
from codeflash.code_utils.git_utils import get_current_branch
from codeflash.github.pr_comment import FileDiffContent, PrComment

if TYPE_CHECKING:
    from codeflash.result.explanation import Explanation


def suggest_changes(
    owner: str,
    repo: str,
    pr_number: int,
    file_changes: dict[str, FileDiffContent],
    pr_comment: PrComment,
    existing_tests: str,
    generated_tests: str,
    trace_id: str,
    coverage_message: str,
    replay_tests: str = "",
    concolic_tests: str = "",
    optimization_review: str = "",
) -> Response:
    payload = {
        "owner": owner,
        "repo": repo,
        "pullNumber": pr_number,
        "diffContents": file_changes,
        "prCommentFields": pr_comment.to_json(),
        "existingTests": existing_tests,
        "generatedTests": generated_tests,
        "traceId": trace_id,
        "coverage_message": coverage_message,
        "replayTests": replay_tests,
        "concolicTests": concolic_tests,
        "optimizationReview": optimization_review,
    }
    return make_cfapi_request(
        endpoint="/suggest-pr-changes", method="POST", payload=payload
    )


def create_pr(
    owner: str,
    repo: str,
    base_branch: str,
    file_changes: dict[str, FileDiffContent],
    pr_comment: PrComment,
    existing_tests: str,
    generated_tests: str,
    trace_id: str,
    coverage_message: str,
    replay_tests: str = "",
    concolic_tests: str = "",
    optimization_review: str = "",
) -> Response:
    payload = {
        "owner": owner,
        "repo": repo,
        "baseBranch": base_branch,
        "diffContents": file_changes,
        "prCommentFields": pr_comment.to_json(),
        "existingTests": existing_tests,
        "generatedTests": generated_tests,
        "traceId": trace_id,
        "coverage_message": coverage_message,
        "replayTests": replay_tests,
        "concolicTests": concolic_tests,
        "optimizationReview": optimization_review,
    }
    return make_cfapi_request(endpoint="/create-pr", method="POST", payload=payload)


def setup_github_actions(
    owner: str, repo: str, base_branch: str, workflow_content: str
) -> Response:
    payload = {
        "owner": owner,
        "repo": repo,
        "baseBranch": base_branch,
        "workflowContent": workflow_content,
    }
    return make_cfapi_request(
        endpoint="/setup-github-actions", method="POST", payload=payload
    )


def create_staging(
    original_code: dict[Path, str],
    new_code: dict[Path, str],
    explanation: Explanation,
    existing_tests_source: str,
    generated_original_test_source: str,
    function_trace_id: str,
    coverage_message: str,
    replay_tests: str,
    concolic_tests: str,
    root_dir: Path,
    optimization_review: str = "",
) -> Response:
    relative_path = explanation.file_path.relative_to(root_dir).as_posix()
    build_file_changes = {
        Path(p).relative_to(root_dir).as_posix(): FileDiffContent(
            oldContent=original_code[p], newContent=new_code[p]
        )
        for p in original_code
    }
    payload = {
        "baseBranch": get_current_branch(),
        "diffContents": build_file_changes,
        "prCommentFields": PrComment(
            optimization_explanation=explanation.explanation_message(),
            best_runtime=explanation.best_runtime_ns,
            original_runtime=explanation.original_runtime_ns,
            function_name=explanation.function_name,
            relative_file_path=relative_path,
            speedup_x=explanation.speedup_x,
            speedup_pct=explanation.speedup_pct,
            winning_behavior_test_results=explanation.winning_behavior_test_results,
            winning_benchmarking_test_results=explanation.winning_benchmarking_test_results,
            benchmark_details=explanation.benchmark_details,
        ).to_json(),
        "existingTests": existing_tests_source,
        "generatedTests": generated_original_test_source,
        "traceId": function_trace_id,
        "coverage_message": coverage_message,
        "replayTests": replay_tests,
        "concolicTests": concolic_tests,
        "optimizationReview": optimization_review,
    }
    return make_cfapi_request(
        endpoint="/create-staging", method="POST", payload=payload
    )
