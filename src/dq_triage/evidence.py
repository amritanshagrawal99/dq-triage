"""Deterministic evidence pack for a failed test, gathered before (and independently of) any LLM."""
from __future__ import annotations

from dataclasses import dataclass, field

from .db import Database, format_rows
from .project import FailedTest, Project


@dataclass
class Evidence:
    test: FailedTest
    model_name: str | None
    failing_rows: str | None
    lineage: list[str]
    row_counts: dict[str, int | str]
    recent_commits: str
    exposures: list[dict]
    downstream: list[str] = field(default_factory=list)


def gather(project: Project, db: Database | None, test: FailedTest, sample_rows: int = 20) -> Evidence:
    model = test.model
    lineage = [model, *project.ancestors(model)] if model else []
    failing = None
    if db is not None and test.compiled_sql:
        try:
            failing = format_rows(*db.query(f"select * from ({test.compiled_sql}) as failures", limit=sample_rows))
        except Exception as e:  # noqa: BLE001 - evidence is best-effort; the report says what failed
            failing = f"(could not fetch failing rows: {e})"
    counts: dict[str, int | str] = {}
    if db is not None:
        for uid in lineage:
            rel = project.relation(uid)
            if not rel:
                continue
            try:
                counts[project.name(uid)] = db.query(f"select count(*) from {rel}")[1][0][0]
            except Exception as e:  # noqa: BLE001
                counts[project.name(uid)] = f"error: {str(e).splitlines()[0][:120]}"
    files = [p for p in (project.file_path(u) for u in lineage) if p]
    return Evidence(
        test=test,
        model_name=project.name(model) if model else None,
        failing_rows=failing,
        lineage=[project.name(u) for u in lineage],
        row_counts=counts,
        recent_commits=project.git_log(files, n=10) if files else "(no files)",
        exposures=project.exposures_downstream(model) if model else [],
        downstream=[project.name(d) for d in project.descendants(model)] if model else [],
    )


def to_prompt(ev: Evidence) -> str:
    t = ev.test
    parts = [
        f"Failed test: {t.name} ({t.test_type}) status={t.status} failures={t.failures}",
        f"Model under test: {ev.model_name}" + (f", column: {t.column}" if t.column else ""),
        f"dbt message: {t.message}",
        "", "Test SQL (returns the failing rows):", t.compiled_sql or "(none)",
        "", "Sample of failing rows:", ev.failing_rows or "(not available)",
        "", "Upstream lineage (model first, then ancestors): " + " <- ".join(ev.lineage),
        "Row counts: " + ", ".join(f"{k}={v}" for k, v in ev.row_counts.items()),
        "", "Recent commits touching these models (newest first):", ev.recent_commits,
        "", "Downstream models: " + (", ".join(ev.downstream) or "none"),
        "Downstream exposures: " + (", ".join(f"{e['name']} ({e['type']}, owner {e['owner']})" for e in ev.exposures) or "none"),
    ]
    return "\n".join(parts)
