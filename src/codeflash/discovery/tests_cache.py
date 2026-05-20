from __future__ import annotations

import hashlib
import sqlite3
from collections import defaultdict
from pathlib import Path

from codeflash.cli_cmds.logging_config import logger
from codeflash.code_utils.compat import codeflash_cache_db
from codeflash.models.domain import (
    CodePosition,
    FunctionCalledInTest,
    TestsInFile,
    TestType,
)


class TestsCache:
    SCHEMA_VERSION = 1

    def __init__(self, project_root_path: str | Path) -> None:
        self.project_root_path = Path(project_root_path).resolve().as_posix()
        self.connection = sqlite3.connect(codeflash_cache_db)
        self.cur = self.connection.cursor()

        self.cur.execute(
            "CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY)"
        )

        self.cur.execute("SELECT version FROM schema_version")
        result = self.cur.fetchone()
        current_version = result[0] if result else None

        if current_version != self.SCHEMA_VERSION:
            logger.debug(
                f"Schema version mismatch (current: {current_version}, expected: {self.SCHEMA_VERSION}). Recreating tables."
            )
            self.cur.execute("DROP TABLE IF EXISTS discovered_tests")
            self.cur.execute(
                "DROP INDEX IF EXISTS idx_discovered_tests_project_file_path_hash"
            )
            self.cur.execute("DELETE FROM schema_version")
            self.cur.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (self.SCHEMA_VERSION,),
            )
            self.connection.commit()

        self.cur.execute(
            "CREATE TABLE IF NOT EXISTS discovered_tests("
            "project_root_path TEXT,"
            "file_path TEXT,"
            "file_hash TEXT,"
            "qualified_name_with_modules_from_root TEXT,"
            "function_name TEXT,"
            "test_class TEXT,"
            "test_function TEXT,"
            "test_type TEXT,"
            "line_number INTEGER,"
            "col_number INTEGER)"
        )
        self.cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_discovered_tests_project_file_path_hash "
            "ON discovered_tests (project_root_path, file_path, file_hash)"
        )

        self.memory_cache: dict[
            tuple[str, str, str], dict[str, set[FunctionCalledInTest]] | None
        ] = {}

    def insert_test(
        self,
        file_path: str,
        file_hash: str,
        qualified_name_with_modules_from_root: str,
        function_name: str,
        test_class: str,
        test_function: str,
        test_type: TestType,
        line_number: int,
        col_number: int,
    ) -> None:
        test_type_value = test_type.value if hasattr(test_type, "value") else test_type
        self.cur.execute(
            "INSERT INTO discovered_tests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self.project_root_path,
                file_path,
                file_hash,
                qualified_name_with_modules_from_root,
                function_name,
                test_class,
                test_function,
                test_type_value,
                line_number,
                col_number,
            ),
        )
        self.connection.commit()

    def get_function_to_test_map_for_file(
        self, file_path: str, file_hash: str
    ) -> dict[str, set[FunctionCalledInTest]] | None:
        cache_key = (self.project_root_path, file_path, file_hash)
        if cache_key in self.memory_cache:
            return self.memory_cache[cache_key]

        self.cur.execute(
            "SELECT * FROM discovered_tests WHERE project_root_path = ? AND file_path = ? AND file_hash = ?",
            (self.project_root_path, file_path, file_hash),
        )
        rows = self.cur.fetchall()
        if not rows:
            return None

        function_to_test_map = defaultdict(set)

        for row in rows:
            qualified_name_with_modules_from_root = row[3]
            function_called_in_test = FunctionCalledInTest(
                tests_in_file=TestsInFile(
                    test_file=Path(row[1]),
                    test_class=row[5],
                    test_function=row[6],
                    test_type=TestType(int(row[7])),
                ),
                position=CodePosition(line_no=row[8], col_no=row[9]),
            )
            function_to_test_map[qualified_name_with_modules_from_root].add(
                function_called_in_test
            )

        result = dict(function_to_test_map)
        self.memory_cache[cache_key] = result
        return result

    @staticmethod
    def compute_file_hash(path: Path) -> str:
        h = hashlib.sha256(usedforsecurity=False)
        with path.open("rb", buffering=0) as f:
            buf = bytearray(8192)
            mv = memoryview(buf)
            while True:
                n = f.readinto(mv)
                if n == 0:
                    break
                h.update(mv[:n])
        return h.hexdigest()

    def close(self) -> None:
        self.cur.close()
        self.connection.close()
