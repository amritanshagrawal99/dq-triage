from dq_triage.cli import main
from dq_triage.evidence import gather, group_failures, to_prompt


def test_failed_test_parsed(project):
    [t] = project.failed_tests()
    assert t.name == "unique_orders_order_id"
    assert (t.test_type, t.column, t.model) == ("unique", "order_id", "model.shop.orders")
    assert t.failures == 1 and t.compiled_sql


def test_evidence_pack(project, db):
    ev = gather(project, db, project.failed_tests()[0])
    assert "12 | 2" in ev.failing_rows                  # order 12 appears twice
    assert ev.lineage[0] == "orders" and {"stg_payments", "stg_orders"} <= set(ev.lineage)
    assert ev.row_counts["orders"] == 5 and ev.row_counts["stg_orders"] == 4
    assert "perf(orders): drop group by in payments CTE" in ev.recent_commits
    assert ev.exposures == [{"name": "revenue_dashboard", "type": "dashboard", "owner": "Analytics"}]
    assert "customers" in ev.downstream
    prompt = to_prompt(ev)
    assert "unique_orders_order_id" in prompt and "Vikram Iyer" in prompt


def test_git_show_limited_to_model(project):
    log = project.git_log(["models/marts/orders.sql"])
    head = log.splitlines()[0].split(" | ")[0]
    diff = project.git_show(head, ["models/marts/orders.sql"])
    assert "-    group by order_id" in diff
    assert project.git_show("HEAD; rm -rf /") == "(invalid commit reference)"


def test_cli_no_llm(incident, tmp_path, capsys):
    rc = main(["--project", str(incident), "--db", f"duckdb:///{incident / 'shop.duckdb'}", "--no-llm", "-o", str(tmp_path)])
    assert rc == 1  # failures exist, like a test runner
    text = (tmp_path / "incident_unique_orders_order_id.md").read_text(encoding="utf-8")
    assert text.startswith("# Data-quality incident: `unique_orders_order_id`")
    assert "revenue_dashboard" in text and "Investigation log" not in text


def _fake(project, name, model):
    from dq_triage.project import FailedTest
    return FailedTest(f"test.shop.{name}", name, "fail", 1, None, "unique", "id", project.find(model), None)


def test_group_failures_by_lineage(project):
    tests = [_fake(project, "t_customers", "customers"), _fake(project, "t_contacts", "customer_contacts"),
             _fake(project, "t_orders", "orders"), _fake(project, "t_stg_orders", "stg_orders")]
    groups = [[t.name for t in g] for g in group_failures(project, tests)]
    # customers and customer_contacts both read stg_customers, but neither is upstream of the other: separate incidents.
    assert ["t_stg_orders", "t_orders", "t_customers"] in groups
    assert ["t_contacts"] in groups and len(groups) == 2


def test_grouped_evidence_mentions_related(project, db):
    primary, other = _fake(project, "t_stg", "stg_orders"), _fake(project, "t_orders", "orders")
    prompt = to_prompt(gather(project, db, primary, related=[other]))
    assert "Other failed tests on the same lineage" in prompt and "t_orders" in prompt
