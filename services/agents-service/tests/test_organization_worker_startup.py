"""Offline process-start regression for the independently deployed organization worker."""

from pathlib import Path
import os
import subprocess
import sys


def test_organization_worker_imports_without_provider_credentials_or_database() -> None:
    """Import the complete worker dependency graph before any durable turn can be claimed.

    The old local runtime lacked pypdf and crashed before claim_turn, leaving an
    accepted message queued indefinitely. A separate process detects missing
    deployment dependencies without inheriting secrets or making external calls.
    """
    source = Path(__file__).resolve().parents[1] / "src"
    result = subprocess.run(
        [sys.executable, "-c", "from agents_service.organization_worker import OrganizationWorker; assert callable(OrganizationWorker.process_once)"],
        env={"PATH": os.defpath, "PYTHONPATH": str(source)},
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, "organization worker startup dependencies are incomplete"
