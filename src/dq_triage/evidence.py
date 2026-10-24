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
    related: list[FailedTest] = field(default_factory=list)  # other failures grouped into this incident


def group_failures(project: Project, tests: list[FailedTest]) -> list[list[FailedTest]]:
    """Group failures whose models sit on one lineage path (one is upstream of, or equal to, the other).

    A broken upstream model usually fails tests on itself and on several descendants; those are one incident.
    Each group is ordered most-upstream first, so its first test is where the investigation starts.
    """
    parent = list(range(len(tests)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    ancestors = {t.model: set(project.ancestors(t.model)) for t in tests if t.model}
    for i, a in enumerate(tests):
        for j in range(i + 1, len(tests)):
            b = tests[j]
            if a.model and b.model and (a.model == b.model or a.model in ancestors[b.model] or b.model in ancestors[a.model]):
                parent[find(i)] = find(j)
    groups: dict[int, list[FailedTest]] = {}
    for i, t in enumerate(tests):
        groups.setdefault(find(i), []).append(t)

    def depth(t: FailedTest) -> int:
        return len(ancestors.get(t.model, ())) if t.model else 0

    return sorted((sorted(g, key=lambda t: (depth(t), t.name)) for g in groups.values()), key=lambda g: g[0].name)


def gather(project: Project, db: Database | None, test: FailedTest, sample_rows: int = 20,
           related: list[FailedTest] | None = None) -> Evidence:
    model = test.model
    # Every model the test reads (a relationships test also reads its `to` model), each with its ancestors.
    roots = [model] if model else []
    roots += [d for d in test.depends_on if d != model and d in project.nodes and not d.startswith("test.")]
    lineage: list[str] = []
    for uid in roots:
        for u in (uid, *project.ancestors(uid)):
            if u not in lineage:
                lineage.append(u)
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
    # The test's own definition file too: a test edit is a cause in its own right.
    files = [p for p in dict.fromkeys(project.file_path(u) for u in [test.unique_id, *lineage]) if p]
    return Evidence(
        test=test,
        model_name=project.name(model) if model else None,
        failing_rows=failing,
        lineage=[project.name(u) for u in lineage],
        row_counts=counts,
        recent_commits=project.git_log(files, n=10) if files else "(no files)",
        exposures=project.exposures_downstream(model) if model else [],
        downstream=[project.name(d) for d in project.descendants(model)] if model else [],
        related=list(related or []),
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
    if ev.related:
        parts += ["", "Other failed tests on the same lineage (likely the same root cause; explain them too):"]
        parts += [f"- {r.name} ({r.test_type} on {r.model.split('.')[-1] if r.model else '?'}, failures={r.failures})"
                  for r in ev.related]
    return "\n".join(parts)
