"""Build a reproducible incident to triage.

Copies examples/shop into DEST, gives it a git history, then lands a plausible-looking
"performance" commit that removes the aggregation from the payments CTE in `orders`.
Order 12 was paid in two instalments, so the join now fans it out and `unique(order_id)` fails.

    python examples/setup_incident.py /tmp/shop-incident
    dq-triage --project /tmp/shop-incident --db duckdb:////tmp/shop-incident/shop.duckdb
"""
import shutil
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).parent / "shop"
DEST = Path(sys.argv[1] if len(sys.argv) > 1 else "shop-incident").resolve()
DBT = Path(sys.executable).with_name("dbt")

BAD_ORDERS = """with payments as (
    -- perf: aggregation moved downstream, join payments directly
    select order_id, amount
    from {{ ref('stg_payments') }}
)
select o.*, p.amount
from {{ ref('stg_orders') }} o
left join payments p on p.order_id = o.order_id
"""


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=DEST, check=check, capture_output=True, text=True)


def git(*args: str) -> None:
    run("git", "-c", "user.name=Asha Rao", "-c", "user.email=asha@example.com", *args)


def main() -> None:
    if DEST.exists():
        shutil.rmtree(DEST)
    shutil.copytree(SRC, DEST, ignore=shutil.ignore_patterns("target", "logs", "*.duckdb*", ".user.yml"))
    (DEST / ".gitignore").write_text("target/\nlogs/\n*.duckdb*\n.user.yml\n")
    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-q", "-m", "shop: staging and marts for customers, orders, payments")
    readme = DEST / "README.md"
    readme.write_text("Shop analytics project.\n")
    git("add", "-A")
    git("commit", "-q", "-m", "docs: add README")
    (DEST / "models/marts/orders.sql").write_text(BAD_ORDERS)
    run("git", "-c", "user.name=Vikram Iyer", "-c", "user.email=vikram@example.com", "commit", "-q", "-am",
        "perf(orders): drop group by in payments CTE")
    build = run(str(DBT), "build", "--profiles-dir", ".", check=False)
    tail = "\n".join(build.stdout.strip().splitlines()[-3:])
    print(f"incident ready in {DEST}\n{tail}")


if __name__ == "__main__":
    main()
