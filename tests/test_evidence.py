from dq_triage.cli import main
from dq_triage.evidence import gather, to_prompt


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
