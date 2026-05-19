"""Zero-dependency SQLite schema constants.

Must not import anything from codeflash — imported by files injected
into user test code at runtime (codeflash_capture.py,
codeflash_wrap_decorator.py).
"""

TEST_RESULTS_TABLE_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS test_results ("
    "test_module_path TEXT, test_class_name TEXT, "
    "test_function_name TEXT, function_getting_tested TEXT, "
    "loop_index INTEGER, iteration_id TEXT, runtime INTEGER, "
    "return_value BLOB, verification_type TEXT)"
)
