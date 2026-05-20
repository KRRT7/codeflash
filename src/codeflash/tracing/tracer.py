from __future__ import annotations

import datetime
import os
import json
import sqlite3
import sys
import threading
import time
from collections import defaultdict
from importlib.util import find_spec
from pathlib import Path
from types import TracebackType

from codeflash.cli_cmds.logging_config import rule
from codeflash.tracing.tracing_utils import (
    FunctionModules,
)


class Tracer:
    """Use this class as a 'with' context manager to trace a function call.

    Traces function calls, input arguments, and profiling info.
    """

    def __init__(
        self,
        config: dict,
        result_pickle_file_path: Path,
        functions: list[str] | None = None,
        disable: bool = False,  # noqa: FBT001, FBT002
        project_root: Path | None = None,
        max_function_count: int = 256,
        timeout: int | None = None,  # seconds
        command: str = "",
    ) -> None:
        """Use this class to trace function calls.

        :param functions: List of functions to trace. If None, trace all functions
        :param disable: Disable the tracer if True
        :param max_function_count: Maximum number of times to trace one function
        :param timeout: Timeout in seconds for the tracer, if the traced code takes more than this time, then tracing
                    stops and normal execution continues. If this is None then no timeout applies
        :param command: The command that initiated the tracing (for metadata storage)
        """
        if functions is None:
            functions = []
        if os.environ.get("CODEFLASH_TRACER_DISABLE", "0") == "1":
            rule(
                "Codeflash: Tracer disabled by environment variable CODEFLASH_TRACER_DISABLE"
            )
            disable = True
        self.disable = disable
        self._db_lock: threading.Lock | None = None
        if self.disable:
            return
        if sys.getprofile() is not None or sys.gettrace() is not None:
            print(
                "WARNING - Codeflash: Another profiler, debugger or coverage tool is already running. "
                "Please disable it before starting the Codeflash Tracer, both can't run. Codeflash Tracer is DISABLED."
            )
            self.disable = True
            return

        self._db_lock = threading.Lock()

        self.con = None
        self.functions = functions
        self.function_modules: list[FunctionModules] = []
        self.function_count = defaultdict(int)  # type: ignore[var-annotated]
        self.current_file_path = Path(__file__).resolve()
        self.ignored_qualified_functions = {
            f"{self.current_file_path}:Tracer.__exit__",
            f"{self.current_file_path}:Tracer.__enter__",
        }
        self.max_function_count = max_function_count
        self.config = config
        self.project_root = project_root
        rule(f"Project Root: {self.project_root}")
        self.ignored_functions = {
            "<listcomp>",
            "<genexpr>",
            "<dictcomp>",
            "<setcomp>",
            "<lambda>",
            "<module>",
        }

        self.sanitized_filename = self.sanitize_to_filename(command)  # type: ignore[attr-defined]
        # Place trace file next to replay tests in the tests directory
        from codeflash.verification.verification_utils import get_test_file_path

        function_path = "_".join(functions) if functions else self.sanitized_filename
        test_file_path = get_test_file_path(
            test_dir=Path(config["tests_root"]),
            function_name=function_path,
            test_type="replay",
        )
        test_file_path.parent.mkdir(parents=True, exist_ok=True)
        trace_filename = test_file_path.stem + ".trace"
        self.output_file = test_file_path.parent / trace_filename
        self.result_pickle_file_path = result_pickle_file_path

        assert timeout is None or timeout > 0, "Timeout should be greater than 0"
        self.timeout = timeout
        self.next_insert = 1000
        self.trace_count = 0
        self.path_cache: dict[Path, str] = {}

        # Profiler variables
        self.bias = 0  # calibration constant
        self.timings = {}  # type: ignore[var-annotated]
        self.cur = None
        self.start_time = None
        self.timer = time.process_time_ns
        self.total_tt = 0
        self.simulate_call("profiler")  # type: ignore[attr-defined]
        self.t = self.timer()

        # Store command information for metadata table
        self.command = command

    def __enter__(self) -> None:
        if self.disable:
            return
        if getattr(Tracer, "used_once", False):
            print(
                "Codeflash: Tracer can only be used once per program run. "
                "Please only enable the Tracer once. Skipping tracing this section."
            )
            self.disable = True
            return
        Tracer.used_once = True  # type: ignore[attr-defined]

        if Path(self.output_file).exists():
            rule("Removing existing trace file")
            rule()
        Path(self.output_file).unlink(missing_ok=True)

        self.con = sqlite3.connect(self.output_file, check_same_thread=False)  # type: ignore[assignment]
        cur = self.con.cursor()  # type: ignore[attr-defined]
        cur.execute("""PRAGMA synchronous = OFF""")
        cur.execute("""PRAGMA journal_mode = WAL""")
        # TODO: Check out if we need to export the function test name as well
        cur.execute(
            "CREATE TABLE function_calls(type TEXT, function TEXT, classname TEXT, filename TEXT, "
            "line_number INTEGER, last_frame_address INTEGER, time_ns INTEGER, args BLOB)"
        )

        # Create metadata table to store command information
        cur.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT)")

        # Store command metadata
        cur.execute("INSERT INTO metadata VALUES (?, ?)", ("command", self.command))
        cur.execute(
            "INSERT INTO metadata VALUES (?, ?)",
            ("program_name", self.sanitized_filename),
        )
        cur.execute(
            "INSERT INTO metadata VALUES (?, ?)",
            (
                "functions_filter",
                json.dumps(self.functions) if self.functions else None,
            ),
        )
        cur.execute(
            "INSERT INTO metadata VALUES (?, ?)",
            ("timestamp", datetime.datetime.now(datetime.timezone.utc).isoformat()),
        )
        cur.execute(
            "INSERT INTO metadata VALUES (?, ?)",
            ("project_root", str(self.project_root)),
        )
        rule("Codeflash: Traced Program Output Begin")
        frame = sys._getframe(
            0
        )  # Get this frame and simulate a call to it  # noqa: SLF001
        self.dispatch["call"](self, frame, 0)  # type: ignore[attr-defined]
        self.start_time = time.time()  # type: ignore[assignment]
        sys.setprofile(self.trace_callback)  # type: ignore[attr-defined]
        threading.setprofile(self.trace_callback)  # type: ignore[attr-defined]

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self.disable or self._db_lock is None:
            return
        sys.setprofile(None)
        threading.setprofile(None)

        with self._db_lock:
            if self.con is None:
                return

            self.con.commit()  # Commit any pending from tracer_logic
            rule("Codeflash: Traced Program Output End")
            self.create_stats()  # This calls snapshot_stats which uses self.timings

            cur = self.con.cursor()
            cur.execute(
                "CREATE TABLE pstats (filename TEXT, line_number INTEGER, function TEXT, class_name TEXT, "
                "call_count_nonrecursive INTEGER, num_callers INTEGER, total_time_ns INTEGER, "
                "cumulative_time_ns INTEGER, callers BLOB)"
            )
            # self.stats is populated by snapshot_stats() called within create_stats()
            # Ensure self.stats is accessed after create_stats() and within the lock if it involves DB data
            # For now, assuming self.stats is primarily in-memory after create_stats()
            for func, (cc, nc, tt, ct, callers) in self.stats.items():
                remapped_callers = [{"key": k, "value": v} for k, v in callers.items()]
                cur.execute(
                    "INSERT INTO pstats VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(Path(func[0]).resolve()),
                        func[1],
                        func[2],
                        func[3],
                        cc,
                        nc,
                        tt,
                        ct,
                        json.dumps(remapped_callers),
                    ),
                )
            self.con.commit()

            self.make_pstats_compatible()  # Modifies self.stats and self.timings in-memory
            self.print_stats("tottime")  # Uses self.stats, prints to console

            cur = self.con.cursor()  # New cursor
            cur.execute("CREATE TABLE total_time (time_ns INTEGER)")
            cur.execute("INSERT INTO total_time VALUES (?)", (self.total_tt,))
            self.con.commit()
            self.con.close()
            self.con = None  # Mark connection as closed

        # filter any functions where we did not capture the return
        self.function_modules = [
            function
            for function in self.function_modules
            if self.function_count[
                str(function.file_name)
                + ":"
                + (function.class_name + "." if function.class_name else "")
                + function.function_name
            ]
            > 0
        ]

        # These modules have been imported here now the tracer is done. It is safe to import codeflash and external modules here


class FakeCode:
    def __init__(self, filename: str, line: int, name: str) -> None:
        self.co_filename = filename
        self.co_line = line
        self.co_name = name
        self.co_firstlineno = 0

    def __repr__(self) -> str:
        return repr((self.co_filename, self.co_line, self.co_name, None))


class FakeFrame:
    def __init__(self, code: FakeCode, prior: FakeFrame | None) -> None:
        self.f_code = code
        self.f_back = prior
        self.f_locals: dict = {}


def patch_ap_scheduler() -> None:
    if find_spec("apscheduler"):
        import apscheduler.schedulers.background as bg  # type: ignore[import-not-found]
        import apscheduler.schedulers.blocking as bb  # type: ignore[import-not-found]
        from apscheduler.schedulers import base

        bg.BackgroundScheduler.start = lambda _, *_a, **_k: None
        bb.BlockingScheduler.start = lambda _, *_a, **_k: None
        base.BaseScheduler.add_job = lambda _, *_a, **_k: None


# Debug this file by simply adding print statements. This file is not meant to be debugged by the debugger.
