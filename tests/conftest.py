import subprocess
import sys
from pathlib import Path

import pytest

from dq_triage.db import Database
from dq_triage.project import Project

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def incident(tmp_path_factory):
    """A real dbt-duckdb project with git history and a failing unique test (built by examples/setup_incident.py)."""
    dest = tmp_path_factory.mktemp("incident") / "shop"
    subprocess.run([sys.executable, str(ROOT / "examples" / "setup_incident.py"), str(dest)], check=True, capture_output=True)
    return dest


@pytest.fixture
def project(incident):
    return Project(incident)


@pytest.fixture
def db(incident):
    database = Database.from_url(f"duckdb:///{incident / 'shop.duckdb'}")
    yield database
    database.conn.close()
