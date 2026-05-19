from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AppConfig:
    project_root: Path
    module_root: Path
    tests_root: Path
    file: Path | None = None
    function: str | None = None
    all: Path | None = None
    replay_test: list[Path] | None = None
    no_pr: bool = False
    no_gen_tests: bool = False
    staging_review: bool = False
    benchmark: bool = False
    benchmarks_root: Path | None = None
    no_draft: bool = False
    worktree: bool = False
    effort: str = "medium"
    pytest_cmd: str = "pytest"
    formatter_cmds: list[str] | None = None
    disable_imports_sorting: bool = False
    git_remote: str | None = None
    override_fixtures: list[str] | None = None
    ignore_paths: list[Path] = field(default_factory=list)
    test_project_root: Path | None = None
    command: str | None = None
    verify_setup: bool = False

    @classmethod
    def from_namespace(cls, ns: object) -> AppConfig:
        args = ns  # keeping the name short in this conversion

        def to_path(v):
            return Path(v) if isinstance(v, str) else v

        return cls(
            project_root=to_path(getattr(args, "project_root", ".")),
            module_root=to_path(getattr(args, "module_root", ".")),
            tests_root=to_path(getattr(args, "tests_root", ".")),
            file=to_path(getattr(args, "file", None))
            if getattr(args, "file", None)
            else None,
            function=getattr(args, "function", None),
            all=to_path(getattr(args, "all", None))
            if getattr(args, "all", None)
            else None,
            replay_test=getattr(args, "replay_test", None),
            no_pr=getattr(args, "no_pr", False),
            no_gen_tests=getattr(args, "no_gen_tests", False),
            staging_review=getattr(args, "staging_review", False),
            benchmark=getattr(args, "benchmark", False),
            benchmarks_root=to_path(getattr(args, "benchmarks_root", None))
            if getattr(args, "benchmarks_root", None)
            else None,
            no_draft=getattr(args, "no_draft", False),
            worktree=getattr(args, "worktree", False),
            effort=getattr(args, "effort", "medium"),
            pytest_cmd=getattr(args, "pytest_cmd", "pytest"),
            formatter_cmds=getattr(args, "formatter_cmds", None),
            disable_imports_sorting=getattr(args, "disable_imports_sorting", False),
            git_remote=getattr(args, "git_remote", None),
            override_fixtures=getattr(args, "override_fixtures", None),
            ignore_paths=getattr(args, "ignore_paths", []),
            test_project_root=to_path(getattr(args, "test_project_root", None))
            if getattr(args, "test_project_root", None)
            else None,
            command=getattr(args, "command", None),
            verify_setup=getattr(args, "verify_setup", False),
        )
