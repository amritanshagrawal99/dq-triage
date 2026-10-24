"""The agent loop against a scripted stand-in for the Claude API (no network, no key)."""
from types import SimpleNamespace as NS

from dq_triage.agent import TOOLS, investigate
from dq_triage.evidence import gather
from dq_triage.report import render

REPORT = {
    "summary": "orders.order_id is duplicated because payments are joined unaggregated.",
    "root_cause": "Commit removed GROUP BY from the payments CTE; order 12 has two payments and fans out.",
    "cause_type": "code_change", "confidence": "high", "first_bad_model": "orders", "culprit_commit": "abc1234",
    "evidence": [{"claim": "order 12 has 2 rows in orders", "source": "run_sql"}],
    "blast_radius": "customers.lifetime_value overstated for customer 2; revenue_dashboard.",
    "suggested_fix": "Restore sum(amount) ... group by order_id.",
    "verification_sql": "select order_id from shop.main.orders group by 1 having count(*) > 1",
}


def tool_use(i, tool_name, **inp):
    return NS(type="tool_use", id=f"tu_{i}", name=tool_name, input=inp)


def response(*blocks, stop="tool_use"):
    return NS(content=list(blocks), stop_reason=stop, usage=NS(input_tokens=100, output_tokens=20))


class FakeClient:
    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.script.pop(0)


def test_investigation_loop(project, db):
    ev = gather(project, db, project.failed_tests()[0])
    client = FakeClient([
        response(NS(type="text", text="Checking duplicates."),
                 tool_use(1, "run_sql", sql="select order_id, count(*) from shop.main.orders group by 1 having count(*) > 1",
                          purpose="find duplicated orders")),
        response(tool_use(2, "run_sql", sql="delete from shop.main.orders", purpose="try a write")),
        response(tool_use(3, "git_log", model="orders", limit=5), tool_use(4, "get_model", name="orders")),
        response(tool_use(5, "submit_report", **REPORT)),
    ])
    inv = investigate(project, db, ev, client=client)

    assert inv.report == REPORT
    assert [s.tool for s in inv.steps] == ["run_sql", "run_sql", "git_log", "get_model"]
    assert inv.steps[0].ok and "12 | 2" in inv.steps[0].output_preview
    assert not inv.steps[1].ok and inv.steps[1].output_preview.startswith("Rejected:")
    assert inv.usage == {"input_tokens": 400, "output_tokens": 80}

    first = client.calls[0]
    assert first["model"] == "claude-opus-5-5" and first["fallbacks"] == "default"
    assert first["betas"] == ["server-side-fallback-2026-07-01"] and first["tools"] is TOOLS
    # The rejected write came back to the model as a tool error, and parallel results share one user turn.
    third = client.calls[2]["messages"]
    assert third[-1]["content"][0]["is_error"] is True
    assert len(client.calls[3]["messages"][-1]["content"]) == 2

    md = render(ev, inv)
    assert "## Root cause" not in md and "**Root cause** (code change, high confidence)" in md
    assert "Investigation log" in md and "`abc1234`" in md


def test_nudges_when_model_stops_without_report(project, db):
    ev = gather(project, db, project.failed_tests()[0])
    client = FakeClient([response(NS(type="text", text="I think it's the join."), stop="end_turn"),
                         response(tool_use(1, "submit_report", **REPORT))])
    inv = investigate(project, db, ev, client=client)
    assert inv.report == REPORT
    assert client.calls[1]["messages"][-1] == {"role": "user", "content": "Please call submit_report with your findings now."}


def test_refusal_stops_cleanly(project, db):
    ev = gather(project, db, project.failed_tests()[0])
    inv = investigate(project, db, ev, client=FakeClient([response(stop="refusal")]))
    assert inv.report is None and inv.stop == "refusal"
    assert "did not finish" in render(ev, inv)


def test_step_limit(project, db):
    ev = gather(project, db, project.failed_tests()[0])
    script = [response(tool_use(i, "get_model", name="orders")) for i in range(3)]
    inv = investigate(project, db, ev, client=FakeClient(script), max_steps=3)
    assert inv.report is None and len(inv.steps) == 3


def test_tool_schemas_are_strict():
    for t in TOOLS:
        assert t["strict"] is True and t["input_schema"]["additionalProperties"] is False
        assert set(t["input_schema"]["required"]) == set(t["input_schema"]["properties"])
