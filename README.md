# dq-triage

[![CI](https://github.com/amritanshagrawal99/dq-triage/actions/workflows/ci.yml/badge.svg)](https://github.com/amritanshagrawal99/dq-triage/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**Root-cause failed dbt tests with a read-only Claude agent.** When `dbt build` reports a failing test, dq-triage collects
an evidence pack and gives it to Claude. Claude investigates through read-only tools: SQL against the warehouse, the dbt
models, and the project's git history. The result is an incident note covering what broke, the first model where it went
wrong, the commit or data change responsible, the downstream blast radius, a fix, and a query to verify the fix.

```bash
dbt build                       # a test fails
dq-triage --project . --db duckdb:///warehouse.duckdb -o incidents/
```

## Why the design looks like this

On-call triage for a failed data test is mostly the same loop every time: look at the failing rows, walk up the lineage,
check what changed recently, and test a hypothesis with a query. That's a good fit for an agent, but only if it can't make
things worse and if every claim it makes can be checked.

- **The evidence comes first, without an LLM.** The failing rows, the lineage, row counts per upstream model, recent commits
  on those models and the downstream exposures are gathered in code. `--no-llm` stops there. That alone is a useful incident
  note, and it's what the agent starts from.
- **It can't write, at two layers.** Every query the agent sends is parsed with sqlglot. Anything that isn't a single
  `SELECT`/`WITH`/`UNION` is rejected and returned to the model as a tool error. That covers `INSERT`, `DELETE`, `COPY`,
  `ATTACH`, `SELECT INTO`, and stacked statements. Separately, the DuckDB connection is opened `read_only` with external file
  access disabled, so a parser gap still can't write. Git access is a fixed argument list (`log`, `show`) with the commit hash
  validated.
- **Structured, evidence-cited output.** The agent finishes by calling a strict `submit_report` tool: root cause, cause type
  (code / data / test definition), confidence, first bad model, culprit commit, evidence claims with their sources, blast
  radius, fix, and verification SQL.
- **One incident per root cause.** Failures whose models sit on one lineage path (a broken staging model that fails
  tests on itself and on three marts) are grouped. The investigation starts at the most-upstream failure and is asked to
  explain the rest. `--no-group` turns this off.
- **Bounded and auditable.** It has a step limit, a log of every tool call, and token usage in the report.
- **API usage:** `claude-opus-5-5` at `effort: high` with prompt caching on the stable system prompt and tools, plus
  `fallbacks: "default"` so a safety-classifier decline is retried server-side instead of failing the run. Refusals end the
  run cleanly.

## Try it

```bash
pip install -e ".[dev]" dbt-duckdb
python examples/setup_incident.py /tmp/shop-incident    # dbt project + git history + a bad "perf" commit
export ANTHROPIC_API_KEY=...
dq-triage --project /tmp/shop-incident --db duckdb:////tmp/shop-incident/shop.duckdb
```

The example incident: a commit titled `perf(orders): drop group by in payments CTE` joins payments to orders without
aggregating them first. Order 12 was paid in two instalments, so it now appears twice. `unique(order_id)` fails, and
`customers.lifetime_value` is overstated for that customer, which feeds a revenue dashboard.

The evidence pack alone already lines up the clues: the failing key, `orders` at 5 rows against `stg_orders` at 4, and the
commit. See [docs/example-evidence.md](docs/example-evidence.md). The agent's job is to confirm the mechanism with queries
and the diff, then write the fix.

## In CI

```yaml
# after `dbt build` in your workflow
- if: failure()
  uses: amritanshagrawal99/dq-triage@main
  with:
    project-dir: .
    db: duckdb:///warehouse.duckdb                          # read-only connection for evidence + agent queries
    anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}     # omit to post the evidence pack only
    slack-webhook-url: ${{ secrets.DATA_ONCALL_SLACK_WEBHOOK }}  # optional
```

The action writes one incident note per root cause to the job summary, keeps a single PR comment updated, and optionally
posts a short Slack alert linking to the run. The repo's own CI runs it end to end on the example incident.

## Evals

[`dq_triage/evals.py`](src/dq_triage/evals.py) defines five seeded incidents with known answers. Each starts from the clean
example project, where all 18 checks pass, and lands one change as a commit:

| Incident | Change | Failing test | Expected cause | First bad model |
|---|---|---|---|---|
| `fanout` | `GROUP BY` removed from a payments CTE | `unique(orders.order_id)` | code change | `orders` |
| `resent_payments` | provider re-sent a payment row | `unique(stg_payments.payment_id)` | data change | `stg_payments` / `raw_payments` |
| `new_status` | source emits a new status `shipped` | `accepted_values(stg_orders.status)` | data change | `stg_orders` / `raw_orders` |
| `filtered_customers` | filter added to `stg_customers` | `relationships(orders.customer_id)` | code change | `stg_customers` |
| `test_typo` | a test edit dropped an accepted value | `accepted_values(...)` | test definition | `stg_orders` |

```bash
python evals/run.py --build-only        # reproduce all five; no API key needed (this also runs in CI)
python evals/run.py --effort medium     # run the agent and score: cause type, first bad model, culprit commit
```

The scorer counts a report as passing when the cause type and first bad model are right and, for code and test-definition
changes, it names the culprit commit. Results are written to `evals/results/` with token usage per incident, so a model or
effort setting can be compared on cost per correct diagnosis.

The eval has already paid for itself. The `filtered_customers` and `test_typo` incidents showed that the evidence pack missed
the culprit commit, because it only followed the tested model's own lineage. A `relationships` test also depends on its target
model, and a test-definition edit lives in the test's YAML file. Both are now included.

## Tools the agent has

| Tool | What it does |
|---|---|
| `run_sql` | One read-only query, auto-`LIMIT 200`, results as a compact table. A `purpose` field records the hypothesis being tested |
| `get_model` | Raw and compiled SQL, relation, materialization, parents, children and tests of a model |
| `git_log` | Recent commits touching a model's file, or the whole project |
| `git_show` | The diff of a commit, optionally limited to one model's file |
| `submit_report` | Final structured report (strict schema) |

## Tests

```bash
pytest
```

- **Read-only guard:** reads pass; 10 kinds of write or escape are rejected; the connection itself refuses writes.
- **Evidence pack:** built on a real dbt-duckdb project with a real git history, created fresh for the test session.
- **Agent loop:** runs against a scripted stand-in for the API, with no key or network. The tests cover:
  - tool dispatch and parallel tool results returned in one turn;
  - a write attempt coming back as `is_error`;
  - the nudge when the model stops without a report;
  - refusal handling;
  - the step limit;
  - that every tool schema is strict.

## Limitations and next steps

- DuckDB and SQLite connections today; Snowflake and Databricks connectors (read-only roles) are next.
- Triage advice is a starting point for a human, not an automatic fix. The tool never applies changes.

## License

[MIT](LICENSE)
