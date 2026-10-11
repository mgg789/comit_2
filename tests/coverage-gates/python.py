"""Measure one backend package at a time: legacy test helper imports cannot share a process."""
import argparse
import hashlib
import os
import secrets
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = {"matching-engine": "matching_engine", "testing-engine": "testing_engine",
            "integration-engine": "integration_engine", "agents-service": "agents_service",
            "verification-engine": "verification_engine", "code-judge": "code_judge"}
# These acceptance tests require separately seeded operational cards/consent fixtures.
ALLOWED_SKIPS = {"test_current_consent_revalidated_without_matching_projection",
                 "test_operational_export_holds_authoritative_consent_until_commit",
                 "test_consent_cannot_commit_during_fit_and_result_write"}



def prepare_api_schema(database_url: str) -> None:
    """Apply real API migrations to disposable matching fixtures that read canonical consent.

    This is schema preparation for tests only. Checksums and the migration lock match
    the API migration contract; no source rows or substitute schemas are introduced.
    """
    import psycopg
    with psycopg.connect(database_url) as connection:
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (2188463147,))
        connection.execute("""CREATE TABLE IF NOT EXISTS auth_migrations (
            name text PRIMARY KEY, checksum text NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT now())""")
        for path in sorted((ROOT / "apps/api/migrations").glob("[0-9][0-9][0-9]_*.sql")):
            source = path.read_text()
            checksum = hashlib.sha256(source.encode()).hexdigest()
            previous = connection.execute("SELECT checksum FROM auth_migrations WHERE name=%s", (path.name,)).fetchone()
            if previous and previous[0] != checksum:
                raise ValueError(f"applied API test migration changed: {path.name}")
            if not previous:
                connection.execute(source)
                connection.execute("INSERT INTO auth_migrations(name,checksum) VALUES(%s,%s)", (path.name, checksum))

def main() -> None:
    """Full mode requires an explicitly disposable DB; unit mode measures without a coverage gate."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", choices=PACKAGES)
    parser.add_argument("--unit", action="store_true")
    args = parser.parse_args()
    env = os.environ.copy()
    env.pop("UPDATE_GOLDEN", None)
    for key in list(env):
        if (key.endswith("TEST_DATABASE_URL") and key != "COVERAGE_TEST_DATABASE_URL") or key == "LEARNING_ACCEPTANCE_DATABASE_URL":
            env.pop(key)
    if not args.unit:
        url = env.get("COVERAGE_TEST_DATABASE_URL", "")
        parsed = urlparse(url)
        if parsed.scheme not in {"postgres", "postgresql"} or not parsed.path.endswith("_test"):
            parser.error("COVERAGE_TEST_DATABASE_URL must identify an explicitly disposable *_test database")
        env.update(PIPELINE_TEST_DATABASE_URL=url, AGENTS_TEST_DATABASE_URL=url)
    out = ROOT / "coverage" / ("unit" if args.unit else "backend")
    out.mkdir(parents=True, exist_ok=True)
    report = out / f"{args.package}.json"
    junit = out / f"{args.package}.xml"
    report.unlink(missing_ok=True)
    junit.unlink(missing_ok=True)
    env["COVERAGE_FILE"] = str(out / f".{args.package}.coverage")
    command = [sys.executable, "-m", "pytest", f"services/{args.package}/tests", "-q", "-ra",
               f"--cov={PACKAGES[args.package]}", "--cov-branch", "--cov-report=term",
               f"--cov-report=json:{report}", f"--cov-report=xml:{out / (args.package + '-coverage.xml')}",
               f"--junitxml={junit}"]
    # Docker isolation is a separate explicit acceptance contour, never substituted
    # by unit tests or silently counted as passing inside generic Python containers.
    if args.package == "code-judge":
        command.append("--ignore=services/code-judge/tests/test_docker_acceptance.py")
    # This one cross-service fixture requires the canonical API source schema. Legacy
    # matching-only fixtures intentionally provide their own consent schemas; sharing
    # the canonical schema globally changes their boundary semantics.
    recommendations = "services/matching-engine/tests/test_recommendations_integration.py"
    split = not args.unit and args.package == "matching-engine"
    if split:
        command.append(f"--ignore={recommendations}")
    result = subprocess.run(command, cwd=ROOT, env=env, check=False)
    if result.returncode:
        sys.exit(result.returncode)
    junit_reports = [junit]
    if split:
        from urllib.parse import urlunparse

        import psycopg
        parsed = urlparse(env["COVERAGE_TEST_DATABASE_URL"])
        name = f"matching_recommendations_{secrets.token_hex(8)}_test"
        isolated_url = urlunparse(parsed._replace(path=f"/{name}"))
        with psycopg.connect(env["COVERAGE_TEST_DATABASE_URL"], autocommit=True) as admin:
            admin.execute(f'CREATE DATABASE "{name}"')
            try:
                prepare_api_schema(isolated_url)
                extra_junit = out / "matching-recommendations.xml"
                extra_env = {**env, "PIPELINE_TEST_DATABASE_URL": isolated_url}
                extra_command = [sys.executable, "-m", "pytest", recommendations, "-q", "-ra",
                    f"--cov={PACKAGES[args.package]}", "--cov-branch", "--cov-append", "--cov-report=term",
                    f"--cov-report=json:{report}", f"--cov-report=xml:{out / (args.package + '-coverage.xml')}",
                    f"--junitxml={extra_junit}"]
                extra = subprocess.run(extra_command, cwd=ROOT, env=extra_env, check=False)
                if extra.returncode:
                    sys.exit(extra.returncode)
                junit_reports.append(extra_junit)
            finally:
                admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')

    if not args.unit:
        skips = [case.get("name") for path in junit_reports for case in ET.parse(path).iter("testcase")
                 if case.find("skipped") is not None]
        unexpected = set(skips) - ALLOWED_SKIPS
        if unexpected:
            sys.exit(f"Unexpected skipped backend tests: {sorted(unexpected)}")
        print(f"Explicit acceptance contour exclusions: {skips}", flush=True)
        subprocess.run([sys.executable, str(Path(__file__).with_name("check.py")), args.package, str(report)], check=True)


if __name__ == "__main__":
    main()
