"""Run the seeded-incident eval.

    python evals/run.py --build-only           # build every incident and check the expected test fails (no API key)
    python evals/run.py                        # also run the agent and score it (needs ANTHROPIC_API_KEY)
    python evals/run.py --effort medium --only fanout,test_typo

Each incident costs one agent investigation (a handful of Claude calls); results go to evals/results/<run>.json.
"""
import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from dq_triage.agent import MODEL, investigate
from dq_triage.db import Database
from dq_triage.evals import BY_NAME, INCIDENTS, build, culprit_hash, score, summarise
from dq_triage.evidence import gather, group_failures
from dq_triage.project import Project

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "examples" / "shop"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--work-dir", default=str(ROOT / "evals" / "work"))
    p.add_argument("--only", help="comma-separated incident names")
    p.add_argument("--build-only", action="store_true")
    p.add_argument("--model", default=MODEL)
    p.add_argument("--effort", default="high")
    p.add_argument("--dbt", default=str(Path(sys.executable).with_name("dbt")))
    a = p.parse_args()

    incidents = [BY_NAME[n] for n in a.only.split(",")] if a.only else list(INCIDENTS)
    work = Path(a.work_dir)
    scores, problems = [], []
    for inc in incidents:
        dest = work / inc.name
        build(inc, BASE, dest, dbt=a.dbt)
        project = Project(dest)
        failed = [t.name for t in project.failed_tests()]
        ok = inc.failing_test in failed
        print(f"{inc.name:<20} expected failure {'found' if ok else 'MISSING'}: {inc.failing_test}  (all failures: {failed})")
        if not ok:
            problems.append(inc.name)
            continue
        if a.build_only:
            continue
        db = Database.from_url(f"duckdb:///{dest / 'shop.duckdb'}")
        group = next(g for g in group_failures(project, project.failed_tests()) if any(t.name == inc.failing_test for t in g))
        ev = gather(project, db, group[0], related=group[1:])
        t0 = time.time()
        inv = investigate(project, db, ev, model=a.model, effort=a.effort)
        s = score(inc, inv.report, culprit_hash(dest))
        scores.append({**asdict(s), "passed": s.passed, "seconds": round(time.time() - t0, 1), "usage": inv.usage,
                       "steps": len(inv.steps), "report": inv.report})
        print(f"  -> passed={s.passed} cause={s.cause_type} model={s.first_bad_model} commit={s.culprit_commit} "
              f"steps={len(inv.steps)} tokens={inv.usage}")
        db.conn.close()

    if problems:
        print(f"Incidents that did not reproduce: {problems}")
        return 1
    if scores:
        from dq_triage.evals import Score
        summary = summarise([Score(**{k: v for k, v in r.items() if k in Score.__dataclass_fields__}) for r in scores])
        out = ROOT / "evals" / "results" / f"{time.strftime('%Y%m%d-%H%M%S')}-{a.model}-{a.effort}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"model": a.model, "effort": a.effort, "summary": summary, "incidents": scores}, indent=2))
        print(json.dumps(summary, indent=2))
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
