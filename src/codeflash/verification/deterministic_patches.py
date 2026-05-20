from __future__ import annotations

import os
import platform
import time as _time_module


if platform.system() == "Linux":

    _memory_limit_set = False

    def _set_linux_memory_limit() -> None:
        global _memory_limit_set
        if not _memory_limit_set:
            import resource

            mem = resource.getrlimit(resource.RLIMIT_AS)
            memory_limit = int(mem[1] * 0.85) if mem[1] != -1 else mem[1]
            resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
            _memory_limit_set = True


_ORIGINAL_TIME_TIME = _time_module.time
_ORIGINAL_PERF_COUNTER = _time_module.perf_counter
_ORIGINAL_PERF_COUNTER_NS = _time_module.perf_counter_ns
_ORIGINAL_TIME_SLEEP = _time_module.sleep


__all__ = [
    "_ORIGINAL_TIME_TIME",
    "_ORIGINAL_PERF_COUNTER",
    "_ORIGINAL_PERF_COUNTER_NS",
    "_ORIGINAL_TIME_SLEEP",
    "_apply_deterministic_patches",
]


def _apply_deterministic_patches() -> None:
    import datetime
    import random
    import time
    import uuid

    _original_time = time.time
    _original_perf_counter = time.perf_counter
    _original_datetime_now = datetime.datetime.now
    _original_datetime_utcnow = datetime.datetime.utcnow
    _original_uuid4 = uuid.uuid4
    _original_uuid1 = uuid.uuid1
    _original_random = random.random

    fixed_timestamp = 1761717605.108106
    fixed_datetime = datetime.datetime(
        2021, 1, 1, 2, 5, 10, tzinfo=datetime.timezone.utc
    )
    fixed_uuid = uuid.UUID("12345678-1234-5678-9abc-123456789012")

    _perf_counter_start = fixed_timestamp
    _perf_counter_calls = 0

    def mock_time_time() -> float:
        _original_time()
        return fixed_timestamp

    def mock_perf_counter() -> float:
        nonlocal _perf_counter_calls
        _original_perf_counter()
        _perf_counter_calls += 1
        return _perf_counter_start + (_perf_counter_calls * 0.001)

    def mock_datetime_now(tz: datetime.timezone | None = None) -> datetime.datetime:
        _original_datetime_now(tz)
        if tz is None:
            return fixed_datetime
        return fixed_datetime.replace(tzinfo=tz)

    def mock_datetime_utcnow() -> datetime.datetime:
        _original_datetime_utcnow()
        return fixed_datetime

    def mock_uuid4() -> uuid.UUID:
        _original_uuid4()
        return fixed_uuid

    def mock_uuid1(node: int | None = None, clock_seq: int | None = None) -> uuid.UUID:
        _original_uuid1(node, clock_seq)
        return fixed_uuid

    def mock_random() -> float:
        _original_random()
        return 0.123456789

    time.time = mock_time_time
    time.perf_counter = mock_perf_counter
    uuid.uuid4 = mock_uuid4
    uuid.uuid1 = mock_uuid1

    random.seed(42)
    random.random = mock_random

    import builtins

    builtins._original_datetime_now = _original_datetime_now
    builtins._original_datetime_utcnow = _original_datetime_utcnow
    builtins._mock_datetime_now = mock_datetime_now
    builtins._mock_datetime_utcnow = mock_datetime_utcnow

    try:
        import numpy as np

        np.random.default_rng(42)
        np.random.seed(42)
    except ImportError:
        pass

    try:
        _original_urandom = os.urandom

        def mock_urandom(n: int) -> bytes:
            _original_urandom(n)
            return b"\x42" * n

        os.urandom = mock_urandom
    except (ImportError, AttributeError):
        pass
