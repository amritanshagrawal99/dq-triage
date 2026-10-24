"""Incident note in Markdown."""
from __future__ import annotations

from .agent import Investigation
from .evidence import Evidence


def render(ev: Evidence, inv: Investigation | None) -> str:
    t = ev.test
    lines = [f"# Data-quality incident: `{t.name}`", ""]
    lines += [f"- **Test:** `{t.test_type}` on `{ev.model_name}`" + (f".`{t.column}`" if t.column else "") +
              f" ({t.status}, {t.failures if t.failures is not None else '?'} failing rows)"]
    if ev.related:
        lines.append("- **Also failing (same lineage):** " + ", ".join(f"`{r.name}`" for r in ev.related))
    if ev.exposures:
        lines.append("- **Exposures downstream:** " + ", ".join(f"`{e['name']}` ({e['type']}, {e['owner']})" for e in ev.exposures))
    lines.append("")

    r = inv.report if inv else None
    if r:
        lines += [
            "## Summary", "", r["summary"], "",
            f"**Root cause** ({r['cause_type'].replace('_', ' ')}, {r['confidence']} confidence): {r['root_cause']}", "",
            f"- First bad model: `{r['first_bad_model']}`",
        ]
        if r.get("culprit_commit"):
            lines.append(f"- Culprit commit: `{r['culprit_commit']}`")
        lines += ["", "## Evidence", ""]
        lines += [f"- {e['claim']} _(source: {e['source']})_" for e in r["evidence"]]
        lines += ["", "## Blast radius", "", r["blast_radius"], "", "## Suggested fix", "", r["suggested_fix"], "",
                  "## Verify", "", "```sql", r["verification_sql"].strip(), "```", ""]
    elif inv is not None:
        lines += ["## Investigation did not finish", "",
                  f"The agent stopped ({inv.stop}) without submitting a report.", "", inv.final_text or "", ""]

    lines += ["## Evidence pack", "",
              "<details><summary>Failing rows (sample)</summary>", "", "```", ev.failing_rows or "(not available)", "```",
              "", "</details>", "",
              "**Lineage:** " + " ← ".join(f"`{m}`" for m in ev.lineage), "",
              "**Row counts:** " + ", ".join(f"`{k}` {v}" for k, v in ev.row_counts.items()), "",
              "**Recent commits on these models:**", "", "```", ev.recent_commits, "```", ""]
    if inv and inv.steps:
        lines += ["<details><summary>Investigation log</summary>", "", "| # | Tool | Input | OK |", "|---|---|---|---|"]
        for i, s in enumerate(inv.steps, 1):
            arg = s.input.get("purpose") or s.input.get("name") or s.input.get("commit") or s.input.get("model") or ""
            lines.append(f"| {i} | `{s.tool}` | {str(arg).replace('|', '/')[:120]} | {'yes' if s.ok else 'no'} |")
        lines += ["", f"Tokens: {inv.usage['input_tokens']:,} in / {inv.usage['output_tokens']:,} out", "", "</details>", ""]
    return "\n".join(lines).rstrip() + "\n"
