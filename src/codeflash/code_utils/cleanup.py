from __future__ import annotations

import shutil
from pathlib import Path
from tempfile import TemporaryDirectory


def cleanup_paths(paths: list[Path]) -> None:
    for path in paths:
        if path and path.exists():
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)


def restore_conftest(path_to_content_map: dict[Path, str]) -> None:
    for path, file_content in path_to_content_map.items():
        path.write_text(file_content, encoding="utf8")


def get_run_tmp_file(file_path: Path | str) -> Path:
    if isinstance(file_path, str):
        file_path = Path(file_path)
    if not hasattr(get_run_tmp_file, "tmpdir"):
        get_run_tmp_file.tmpdir = TemporaryDirectory(prefix="codeflash_")  # type: ignore[attr-defined]
    return Path(get_run_tmp_file.tmpdir.name) / file_path  # type: ignore[attr-defined]
