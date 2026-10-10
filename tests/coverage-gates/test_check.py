"""Synthetic reports exercise gate failure modes; these are not product coverage evidence."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("coverage_check", Path(__file__).with_name("check.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CoverageGateTests(unittest.TestCase):
    """Prevent missing reports, rounding, missing branches and inflated counters going green."""
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps({"api": {"kind": "c8", "lines": 80, "branches": 60}}))
        self.report = self.root / "report.json"

    def tearDown(self):
        self.directory.cleanup()

    def write(self, lines=80, branches=60, total=100):
        self.report.write_text(json.dumps({"total": {
            "lines": {"covered": lines, "total": total},
            "branches": {"covered": branches, "total": total}}}))

    def test_valid_boundary_passes(self):
        self.write()
        self.assertEqual(module.check("api", self.report, self.config), (80, 60))

    def test_missing_report_rejected(self):
        with self.assertRaises(OSError):
            module.check("api", self.report, self.config)

    def test_below_either_floor_rejected(self):
        for lines, branches in [(79, 60), (80, 59)]:
            self.write(lines, branches)
            with self.assertRaises(ValueError):
                module.check("api", self.report, self.config)

    def test_empty_or_inflated_counters_rejected(self):
        for lines, branches, total in [(0, 0, 0), (101, 60, 100)]:
            self.write(lines, branches, total)
            with self.assertRaises(ValueError):
                module.check("api", self.report, self.config)

    def test_python_without_branch_measurement_rejected(self):
        with self.assertRaises(ValueError):
            module.percentages({"meta": {"branch_coverage": False}, "totals": {}}, "python")

    def test_no_rounded_pass(self):
        self.write(7999, 6000, 10000)
        with self.assertRaises(ValueError):
            module.check("api", self.report, self.config)

    def test_malformed_report_cli_nonzero(self):
        import subprocess
        import sys
        self.report.write_text("{}")
        result = subprocess.run([sys.executable, str(Path(__file__).with_name("check.py")),
                                 "api", str(self.report), "--config", str(self.config)], capture_output=True, check=False)
        self.assertEqual(result.returncode, 1)


    def test_failed_or_unexpected_skipped_junit_cli_rejected(self):
        import subprocess
        import sys
        self.write()
        junit = self.root / "junit.xml"
        for child in ['<failure message="broken"/>', '<skipped/>']:
            junit.write_text(f'<testsuite><testcase name="unexpected">{child}</testcase></testsuite>')
            result = subprocess.run([sys.executable, str(Path(__file__).with_name("check.py")),
                "api", str(self.report), "--config", str(self.config), "--junit", str(junit)], capture_output=True, check=False)
            self.assertEqual(result.returncode, 1)

    def test_explicit_allowed_skip_cli_passes(self):
        import subprocess
        import sys
        self.write()
        junit = self.root / "junit.xml"
        junit.write_text('<testsuite><testcase name="external"><skipped/></testcase></testsuite>')
        result = subprocess.run([sys.executable, str(Path(__file__).with_name("check.py")),
            "api", str(self.report), "--config", str(self.config), "--junit", str(junit),
            "--allow-skip", "external"], capture_output=True, check=False)
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
