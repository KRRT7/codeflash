from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping


def get_cross_platform_subprocess_run_args(
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    check: bool = False,  # noqa: FBT001, FBT002
    text: bool = True,  # noqa: FBT001, FBT002
    capture_output: bool = True,  # noqa: FBT001, FBT002 (only for non-Windows)
) -> dict[str, Any]:
    run_args = {
        "cwd": cwd,
        "env": env,
        "text": text,
        "timeout": timeout,
        "check": check,
    }
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        run_args["creationflags"] = creationflags
        run_args["stdout"] = subprocess.PIPE
        run_args["stderr"] = subprocess.PIPE
        run_args["stdin"] = subprocess.DEVNULL
    else:
        run_args["capture_output"] = capture_output

    return run_args
