from argparse import Namespace
from codeflash.models.config import AppConfig
from pathlib import Path

import pytest
from codeflash.cli_cmds.cli import process_pyproject_config
from codeflash.optimization.optimizer import Optimizer


def test_mirror_paths_for_worktree_mode(monkeypatch: pytest.MonkeyPatch):
    repo_root = Path(__file__).resolve().parent.parent
    project_root = (
        repo_root
        / "tests"
        / "code_to_optimize"
        / "code_directories"
        / "nested_module_root"
    )

    monkeypatch.setattr(
        "codeflash.optimization.optimizer.git_root_dir", lambda: project_root
    )

    args = Namespace()
    args.benchmark = False
    args.benchmarks_root = None
    args.no_pr = True

    args.config_file = project_root / "pyproject.toml"
    args.file = project_root / "src" / "app" / "main.py"
    args.worktree = True

    new_args = process_pyproject_config(args)

    config = AppConfig.from_namespace(new_args)
    optimizer = Optimizer(config)

    worktree_dir = repo_root / "worktree"
    optimizer.mirror_paths_for_worktree_mode(worktree_dir)

    assert optimizer.config.project_root == worktree_dir / "src"
    assert optimizer.config.test_project_root == worktree_dir / "src"
    assert optimizer.config.module_root == worktree_dir / "src" / "app"
    assert optimizer.config.tests_root == worktree_dir / "src" / "tests"
    assert optimizer.config.file == worktree_dir / "src" / "app" / "main.py"

    assert optimizer.test_cfg.tests_root == worktree_dir / "src" / "tests"
    assert (
        optimizer.test_cfg.project_root_path == worktree_dir / "src"
    )  # same as project_root
    assert (
        optimizer.test_cfg.tests_project_rootdir == worktree_dir / "src"
    )  # same as test_project_root

    # test on our repo
    monkeypatch.setattr(
        "codeflash.optimization.optimizer.git_root_dir", lambda: repo_root
    )
    args = Namespace()
    args.benchmark = False
    args.benchmarks_root = None
    args.no_pr = True

    args.config_file = repo_root / "pyproject.toml"
    args.file = repo_root / "src/codeflash/optimization/optimizer.py"
    args.worktree = True

    new_args = process_pyproject_config(args)
    config = AppConfig.from_namespace(new_args)
    optimizer = Optimizer(config)

    worktree_dir = repo_root / "worktree"
    optimizer.mirror_paths_for_worktree_mode(worktree_dir)

    assert optimizer.config.project_root == worktree_dir / "src"
    assert optimizer.config.test_project_root == worktree_dir
    assert optimizer.config.module_root == worktree_dir / "src" / "codeflash"
    assert optimizer.config.tests_root == worktree_dir / "tests"
    assert (
        optimizer.config.file
        == worktree_dir / "src/codeflash/optimization/optimizer.py"
    )

    assert optimizer.test_cfg.tests_root == worktree_dir / "tests"
    assert optimizer.test_cfg.project_root_path == worktree_dir / "src"
    assert optimizer.test_cfg.tests_project_rootdir == worktree_dir
