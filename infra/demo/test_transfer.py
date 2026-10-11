"""Regression checks for fail-closed demo transfer, optionally on PostgreSQL."""
import importlib.util
import os
import subprocess
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("transfer", Path(__file__).with_name("transfer.py"))
transfer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transfer)


@unittest.skipUnless(os.environ.get("DEMO_TRANSFER_TEST_CONTAINER"), "explicit PostgreSQL container required")
class PostgreSQLTransferTests(unittest.TestCase):
    """Exercise collision branches in real PostgreSQL; every run rolls back."""

    @classmethod
    def setUpClass(cls):
        cls.command = transfer.command(os.environ["DEMO_TRANSFER_TEST_CONTAINER"])
        cls.columns, cls.data = transfer.export(cls.command)

    def test_identical_retry_is_insert_free(self):
        output = transfer.run(self.command, transfer.transfer_sql(self.columns, self.data, False))
        rows = [line.split("|") for line in output.strip().splitlines()]
        self.assertEqual(len(rows), len(transfer.TABLES))
        self.assertTrue(all(row[-1] == "0" for row in rows))

    def test_changed_id_aborts_without_row_disclosure(self):
        sql = transfer.transfer_sql(self.columns, self.data, False)
        sql = sql.replace("DO $$ BEGIN IF EXISTS", "UPDATE demo_0 SET role=CASE WHEN role='candidate' THEN 'employer' ELSE 'candidate' END; DO $$ BEGIN IF EXISTS", 1)
        with self.assertRaisesRegex(RuntimeError, "demo_transfer differing ID collision: public.accounts"):
            transfer.run(self.command, sql)

    def test_email_collision_aborts(self):
        sql = transfer.transfer_sql(self.columns, self.data, False)
        sql = sql.replace("DO $$ BEGIN IF EXISTS", "UPDATE demo_0 SET id=gen_random_uuid(); DO $$ BEGIN IF EXISTS", 1)
        with self.assertRaisesRegex(RuntimeError, "demo_transfer email collision"):
            transfer.run(self.command, sql)

    def test_tombstone_insert_lock_is_blocked(self):
        sql = transfer.transfer_sql(self.columns, self.data, False)
        locks = sql.split("CREATE TEMP TABLE", 1)[0]
        process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            process.stdin.write(locks + "SELECT 'locks_acquired'; SELECT pg_sleep(3); ROLLBACK;\n")
            process.stdin.close()
            self.assertEqual(process.stdout.readline().strip(), "locks_acquired")
            # INSERT requires ROW EXCLUSIVE; it must fail while transfer holds its lock.
            with self.assertRaisesRegex(RuntimeError, "database command failed"):
                transfer.run(self.command, "BEGIN; SET LOCAL lock_timeout='100ms'; LOCK TABLE candidate_pipeline.tombstones IN ROW EXCLUSIVE MODE; ROLLBACK;")
            self.assertEqual(process.wait(timeout=10), 0)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
            process.stdout.close()
            process.stderr.close()

    def test_inactive_projection_schema_aborts(self):
        sql = transfer.transfer_sql(self.columns, self.data, False)
        needle = "DO $$ BEGIN IF EXISTS (SELECT 1 FROM demo_5 p WHERE NOT EXISTS"
        self.assertIn(needle, sql)
        sql = sql.replace(needle, "UPDATE demo_5 SET schema_hash='incompatible-demo-test'; " + needle, 1)
        with self.assertRaisesRegex(RuntimeError, "demo_transfer inactive schema projection"):
            transfer.run(self.command, sql)


if __name__ == "__main__":
    unittest.main()
