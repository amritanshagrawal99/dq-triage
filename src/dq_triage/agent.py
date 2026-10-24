"""Investigation agent: Claude with read-only tools over the warehouse, the dbt project and its git history."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import anthropic

from .db import Database, ReadOnlyViolation, format_rows
from .evidence import Evidence, to_prompt
from .project import Project

MODEL = "claude-opus-5-5"
MAX_STEPS = 20

SYSTEM = """You are a senior analytics engineer on call for a dbt project. A data-quality test has failed.
Find the root cause, not just the symptom, and write it up so the owning engineer can fix it quickly.

How to work:
- Start from the evidence pack in the first message. Form hypotheses, then confirm or reject each one with tools.
- Separate the two usual families of cause: a code change (look at recent commits and diffs of the models in the
  lineage) or a data change (look at the upstream rows: duplicates, nulls, late or re-sent records).
- Prefer small, targeted queries: aggregate and count rather than dumping tables.
- Walk the lineage upstream until you find the first model where the problem appears; that is where the fix belongs.
- Your tools are read-only. Never suggest running a fix directly against production; propose a code change or a
  data correction for a human to review.

When you are confident, or after about 12 tool calls, call submit_report exactly once. Every claim in the report
must be backed by something you actually saw in a tool result or in the evidence pack; say how confident you are."""

TOOLS = [
    {
        "name": "run_sql",
        "description": "Run one read-only SELECT query against the warehouse and return up to 200 rows. "
                       "Use fully qualified relation names as shown in the lineage. Writes are rejected.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "A single SELECT / WITH query."},
                "purpose": {"type": "string", "description": "One line: which hypothesis this query tests."},
            },
            "required": ["sql", "purpose"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "get_model",
        "description": "Get a dbt model's raw and compiled SQL, relation name, materialization, parents, children and tests.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Model name, e.g. stg_orders."}},
            "required": ["name"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "git_log",
        "description": "Recent commits (hash, date, author, subject) touching a model's file, or the whole project if no model is given.",
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {"type": ["string", "null"], "description": "Model name, or null for the whole project."},
                "limit": {"type": "integer", "description": "Number of commits, 1-50."},
            },
            "required": ["model", "limit"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "git_show",
        "description": "The diff of one commit, optionally limited to a model's file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "commit": {"type": "string", "description": "Commit hash from git_log."},
                "model": {"type": ["string", "null"], "description": "Limit the diff to this model's file, or null."},
            },
            "required": ["commit", "model"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "submit_report",
        "description": "Submit the final incident report. Call once, at the end.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Two or three sentences a stakeholder can read."},
                "root_cause": {"type": "string", "description": "What changed, where, and why it produces the failure."},
                "cause_type": {"type": "string", "enum": ["code_change", "data_change", "test_definition", "unknown"]},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "first_bad_model": {"type": "string", "description": "The most upstream model where the problem appears."},
                "culprit_commit": {"type": ["string", "null"], "description": "Commit hash if a code change caused it."},
                "evidence": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"claim": {"type": "string"}, "source": {"type": "string"}},
                        "required": ["claim", "source"],
                        "additionalProperties": False,
                    },
                },
                "blast_radius": {"type": "string", "description": "Downstream models, exposures and numbers affected."},
                "suggested_fix": {"type": "string", "description": "Concrete code or data change, with SQL if useful."},
                "verification_sql": {"type": "string", "description": "A query that should return zero rows once fixed."},
            },
            "required": ["summary", "root_cause", "cause_type", "confidence", "first_bad_model", "culprit_commit",
                         "evidence", "blast_radius", "suggested_fix", "verification_sql"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


@dataclass
class Step:
    tool: str
    input: dict
    ok: bool
    output_preview: str


@dataclass
class Investigation:
    report: dict | None
    steps: list[Step] = field(default_factory=list)
    stop: str = ""
    final_text: str = ""
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})


class Tools:
    def __init__(self, project: Project, db: Database | None):
        self.project, self.db = project, db

    def run(self, name: str, args: dict) -> tuple[str, bool]:
        try:
            return getattr(self, f"_{name}")(**args), True
        except ReadOnlyViolation as e:
            return f"Rejected: {e}", False
        except Exception as e:  # noqa: BLE001 - errors go back to the model as tool errors
            return f"Error: {type(e).__name__}: {str(e).splitlines()[0][:500]}", False

    def _run_sql(self, sql: str, purpose: str) -> str:
        if self.db is None:
            raise RuntimeError("no database connection was provided")
        return format_rows(*self.db.query(sql))

    def _get_model(self, name: str) -> str:
        uid = self.project.find(name)
        if uid is None:
            raise KeyError(f"no model named {name!r}")
        return json.dumps(self.project.describe_model(uid), indent=1)

    def _file_of(self, model: str | None) -> list[str] | None:
        if not model:
            return None
        uid = self.project.find(model)
        path = self.project.file_path(uid) if uid else None
        if path is None:
            raise KeyError(f"no model named {model!r}")
        return [path]

    def _git_log(self, model: str | None, limit: int) -> str:
        return self.project.git_log(self._file_of(model), n=limit)

    def _git_show(self, commit: str, model: str | None) -> str:
        return self.project.git_show(commit, self._file_of(model))


def investigate(project: Project, db: Database | None, ev: Evidence, client: anthropic.Anthropic | None = None,
                model: str = MODEL, max_steps: int = MAX_STEPS, effort: str = "high") -> Investigation:
    client = client or anthropic.Anthropic()
    tools = Tools(project, db)
    messages: list[dict] = [{"role": "user", "content": "Evidence pack:\n\n" + to_prompt(ev)}]
    inv = Investigation(report=None)

    for _ in range(max_steps):
        response = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            system=SYSTEM,
            tools=TOOLS,
            messages=list(messages),  # snapshot: the list keeps growing after this call
            output_config={"effort": effort},
            cache_control={"type": "ephemeral"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        usage = getattr(response, "usage", None)
        for k in inv.usage:
            inv.usage[k] += getattr(usage, k, 0) or 0
        inv.stop = response.stop_reason
        if response.stop_reason == "refusal":
            break
        messages.append({"role": "assistant", "content": response.content})
        uses = [b for b in response.content if b.type == "tool_use"]
        texts = [b.text for b in response.content if b.type == "text"]
        if texts:
            inv.final_text = "\n".join(texts)
        if not uses:
            if response.stop_reason == "pause_turn":
                continue
            if inv.report is None and response.stop_reason in ("end_turn", "max_tokens"):
                messages.append({"role": "user", "content": "Please call submit_report with your findings now."})
                continue
            break
        results = []
        for block in uses:
            if block.name == "submit_report":
                inv.report = dict(block.input)
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": "Report received."})
                continue
            out, ok = tools.run(block.name, dict(block.input))
            inv.steps.append(Step(block.name, dict(block.input), ok, out[:300]))
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": out, **({} if ok else {"is_error": True})})
        messages.append({"role": "user", "content": results})
        if inv.report is not None:
            break
    return inv
