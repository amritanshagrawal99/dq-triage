"""`dq-triage --project . --db duckdb:///warehouse.duckdb`"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .agent import MODEL, investigate
from .db import Database
from .evidence import gather, group_failures
from .project import Project
from .report import render


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="dq-triage", description="Root-cause failed dbt tests with a read-only Claude agent.")
    p.add_argument("--project", default=".", help="dbt project directory (with target/manifest.json and run_results.json)")
    p.add_argument("--target-dir", default="target")
    p.add_argument("--db", help="read-only connection: duckdb:///path.duckdb or sqlite:///path.db")
    p.add_argument("--test", help="only triage tests whose name contains this")
    p.add_argument("--no-llm", action="store_true", help="evidence pack only, no API calls")
    p.add_argument("--model", default=MODEL)
    p.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--max-steps", type=int, default=20)
    p.add_argument("--no-group", action="store_true", help="one incident per failed test instead of per shared lineage")
    p.add_argument("-o", "--output-dir", help="write one incident_<test>.md per failure here (default: stdout)")
    p.add_argument("--version", action="version", version=f"dq-triage {__version__}")
    a = p.parse_args(argv)

    project = Project(a.project, a.target_dir)
    db = Database.from_url(a.db) if a.db else None
    failures = [t for t in project.failed_tests() if not a.test or a.test in t.name]
    if not failures:
        print("No failed tests in run_results.json.")
        return 0

    groups = [[t] for t in failures] if a.no_group else group_failures(project, failures)
    for test, *related in groups:
        ev = gather(project, db, test, related=related)
        inv = None if a.no_llm else investigate(project, db, ev, model=a.model, max_steps=a.max_steps, effort=a.effort)
        text = render(ev, inv)
        if a.output_dir:
            out = Path(a.output_dir)
            out.mkdir(parents=True, exist_ok=True)
            path = out / f"incident_{test.name}.md"
            path.write_text(text, encoding="utf-8")
            print(f"{test.name}: {path}")
        else:
            sys.stdout.buffer.write(text.encode("utf-8"))
    return 1


if __name__ == "__main__":
    sys.exit(main())
