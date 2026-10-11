"""The skip gate must not accidentally authorize missing backend coverage."""
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("python_gate", Path(__file__).with_name("python.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SkipGateTests(unittest.TestCase):
    """Paid-provider opt-in exclusions stay exact and package scoped."""

    def test_opt_in_skips_only_without_live_acceptance(self):
        names = [f"test_live_employer_agent[{name}]" for name in
                 ("interface_help", "markdown_report", "background_clarification")]
        self.assertEqual(module.unexpected_skips("agents-service", names, False), set())
        self.assertEqual(module.unexpected_skips("agents-service", names, True), set(names))
        self.assertEqual(module.unexpected_skips("matching-engine", names, False), set(names))

    def test_unknown_skips_still_fail(self):
        self.assertEqual(module.unexpected_skips("agents-service", ["test_live_employer_agent[new_case]", "test_regression"], False),
                         {"test_live_employer_agent[new_case]", "test_regression"})


if __name__ == "__main__":
    unittest.main()
