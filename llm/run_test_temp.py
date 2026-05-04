from llm.rag.tests.test_run_mode_2_mate_sanitization import test_run_mode_2_quick_mate_sanitization

try:
    test_run_mode_2_quick_mate_sanitization()
    print("TEST PASSED")
except AssertionError as e:
    print("TEST FAILED", e)
