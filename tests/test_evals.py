import sys
from pathlib import Path

import pytest

from dq_triage.evals import INCIDENTS, build, culprit_hash, score, summarise
from dq_triage.evidence import gather, group_failures
from dq_triage.project import Project

BASE = Path(__file__).resolve().parents[1] / "examples" / "shop"
DBT = str(Path(sys.executable).with_name("dbt"))


@pytest.mark.parametrize("incident", INCIDENTS, ids=lambda i: i.name)
def test_incident_reproduces_exactly(incident, tmp_path):
    """Each seeded change fails its expected test and nothing else, and the evidence pack points at the change."""
    dest = tmp_path / incident.name
    build(incident, BASE, dest, dbt=DBT)
    project = Project(dest)
    failed = project.failed_tests()
    assert [t.name for t in failed] == [incident.failing_test]
    ev = gather(project, None, group_failures(project, failed)[0][0])
    assert incident.commit_message in ev.recent_commits or not incident.has_culprit_commit
    assert culprit_hash(dest)


def test_score_correct_report():
    inc = next(i for i in INCIDENTS if i.name == "fanout")
    s = score(inc, {"cause_type": "code_change", "first_bad_model": "`orders`", "culprit_commit": "abc1234def"}, "abc1234")
    assert s.passed and s.culprit_commit is True


def test_score_wrong_model_and_commit():
    inc = next(i for i in INCIDENTS if i.name == "filtered_customers")
    s = score(inc, {"cause_type": "code_change", "first_bad_model": "orders", "culprit_commit": "ffff000"}, "abc1234")
    assert not s.passed and not s.first_bad_model and s.culprit_commit is False


def test_score_data_change_accepts_source_or_staging():
    inc = next(i for i in INCIDENTS if i.name == "resent_payments")
    for model in ("stg_payments", "shop.main.raw_payments"):
        assert score(inc, {"cause_type": "data_change", "first_bad_model": model, "culprit_commit": None}, "x").passed


def test_score_no_report_and_summary():
    scores = [score(i, None, None) for i in INCIDENTS]
    assert not any(s.passed or s.submitted for s in scores)
    summary = summarise(scores)
    assert summary["incidents"] == 5 and summary["passed"] == 0 and summary["culprit_commit_accuracy"] == 0
