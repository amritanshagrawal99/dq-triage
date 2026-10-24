"""Seeded incidents with known root causes, and a scorer for the agent's reports.

Each incident starts from the clean example project (all tests pass), lands one change as a git commit,
and runs `dbt build`. The expected answer is known, so a report can be graded on three things:
cause type, first bad model, and (for code and test-definition changes) the culprit commit.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

Edit = tuple[str, str, str]  # (path, old text, new text); old == "" appends new to the file


@dataclass(frozen=True)
class Incident:
    name: str
    description: str
    commit_message: str
    author: str
    edits: tuple[Edit, ...]
    failing_test: str
    cause_type: str
    first_bad_models: frozenset[str]  # any of these counts as correct
    has_culprit_commit: bool


INCIDENTS = (
    Incident(
        "fanout", "Aggregation removed from the payments CTE; an order paid in two instalments is duplicated.",
        "perf(orders): drop group by in payments CTE", "Vikram Iyer",
        (("models/marts/orders.sql",
          "    select order_id, sum(amount) as amount\n    from {{ ref('stg_payments') }}\n    group by order_id\n",
          "    -- perf: aggregation moved downstream, join payments directly\n    select order_id, amount\n"
          "    from {{ ref('stg_payments') }}\n"),),
        "unique_orders_order_id", "code_change", frozenset({"orders"}), True),
    Incident(
        "resent_payments", "The payment provider re-sent a batch: payment 101 now appears twice in the source.",
        "data: load payments batch 2026-10-24", "Payments Loader",
        (("seeds/raw_payments.csv", "", "101,11,card,49900\n"),),
        "unique_stg_payments_payment_id", "data_change", frozenset({"stg_payments", "raw_payments"}), False),
    Incident(
        "new_status", "The order system started emitting a new status value, 'shipped'.",
        "data: load orders batch 2026-10-24", "Orders Loader",
        (("seeds/raw_orders.csv", "", "14,2,2024-05-20,shipped\n"),),
        "accepted_values_stg_orders_status__placed__completed__returned", "data_change",
        frozenset({"stg_orders", "raw_orders"}), False),
    Incident(
        "filtered_customers", "A filter added to stg_customers drops customers that orders still reference.",
        "feat(stg_customers): only keep customers who signed up from Feb 2024", "Meera Nair",
        (("models/staging/stg_customers.sql", "from {{ ref('raw_customers') }}\n",
          "from {{ ref('raw_customers') }}\nwhere cast(signup_date as date) >= date '2024-02-01'\n"),),
        "relationships_orders_customer_id__customer_id__ref_stg_customers_", "code_change",
        frozenset({"stg_customers"}), True),
    Incident(
        "test_typo", "A test edit dropped 'returned' from the accepted status values; the data is fine.",
        "test(stg_orders): tidy accepted status values", "Asha Rao",
        (("models/schema.yml", "values: [placed, completed, returned]", "values: [placed, completed]"),),
        "accepted_values_stg_orders_status__placed__completed", "test_definition", frozenset({"stg_orders"}), True),
)

BY_NAME = {i.name: i for i in INCIDENTS}


def _force_remove(func, path, _exc) -> None:
    # git marks object files read-only; Windows refuses to delete them until the flag is cleared.
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _run(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, check=check, capture_output=True, text=True)


def _commit(cwd: Path, author: str, message: str) -> None:
    email = author.lower().replace(" ", ".") + "@example.com"
    _run(cwd, "git", "-c", f"user.name={author}", "-c", f"user.email={email}", "add", "-A")
    _run(cwd, "git", "-c", f"user.name={author}", "-c", f"user.email={email}", "commit", "-q", "-m", message)


def build(incident: Incident, base: Path, dest: Path, dbt: str = "dbt") -> subprocess.CompletedProcess:
    """Copy the clean project to `dest`, give it history, land the incident's change, run dbt build."""
    if dest.exists():
        shutil.rmtree(dest, onerror=_force_remove)
    shutil.copytree(base, dest, ignore=shutil.ignore_patterns("target", "logs", "*.duckdb*", ".user.yml"))
    (dest / ".gitignore").write_text("target/\nlogs/\n*.duckdb*\n.user.yml\n")
    _run(dest, "git", "init", "-q", "-b", "main")
    _commit(dest, "Asha Rao", "shop: staging and marts for customers, orders, payments")
    (dest / "README.md").write_text("Shop analytics project.\n")
    _commit(dest, "Asha Rao", "docs: add README")
    for path, old, new in incident.edits:
        f = dest / path
        text = f.read_text()
        if old:
            if old not in text:
                raise ValueError(f"{incident.name}: {path} does not contain the expected text")
            text = text.replace(old, new)
        else:
            text = text + new
        f.write_text(text)
    _commit(dest, incident.author, incident.commit_message)
    return _run(dest, dbt, "build", "--profiles-dir", ".", check=False)


def culprit_hash(project_dir: Path) -> str:
    return _run(project_dir, "git", "rev-parse", "--short", "HEAD").stdout.strip()


@dataclass
class Score:
    incident: str
    cause_type: bool
    first_bad_model: bool
    culprit_commit: bool | None  # None when the incident has no culprit commit
    submitted: bool = True
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.submitted and self.cause_type and self.first_bad_model and self.culprit_commit is not False


def score(incident: Incident, report: dict | None, culprit: str | None) -> Score:
    if not report:
        return Score(incident.name, False, False, False if incident.has_culprit_commit else None, submitted=False)
    model = (report.get("first_bad_model") or "").strip().strip("`").split(".")[-1].lower()
    commit = (report.get("culprit_commit") or "").strip().lower()
    s = Score(incident.name, report.get("cause_type") == incident.cause_type, model in incident.first_bad_models, None)
    if incident.has_culprit_commit:
        s.culprit_commit = bool(culprit and commit and (commit.startswith(culprit[:7]) or culprit.startswith(commit[:7])))
    elif commit:
        s.notes.append(f"named a commit ({commit}) for a data change")
    return s


def summarise(scores: list[Score]) -> dict:
    n = len(scores) or 1
    with_commit = [s for s in scores if s.culprit_commit is not None]
    return {
        "incidents": len(scores),
        "passed": sum(s.passed for s in scores),
        "cause_type_accuracy": round(sum(s.cause_type for s in scores) / n, 3),
        "first_bad_model_accuracy": round(sum(s.first_bad_model for s in scores) / n, 3),
        "culprit_commit_accuracy": round(sum(bool(s.culprit_commit) for s in with_commit) / len(with_commit), 3) if with_commit else None,
    }
