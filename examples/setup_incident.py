"""Build a reproducible incident to triage (any of the seeded eval incidents; default: fanout).

    python examples/setup_incident.py /tmp/shop-incident [incident-name]
    dq-triage --project /tmp/shop-incident --db duckdb:////tmp/shop-incident/shop.duckdb

Incidents are defined in dq_triage/evals.py: fanout, resent_payments, new_status, filtered_customers, test_typo.
"""
import sys
from pathlib import Path

from dq_triage.evals import BY_NAME, build

dest = Path(sys.argv[1] if len(sys.argv) > 1 else "shop-incident").resolve()
incident = BY_NAME[sys.argv[2] if len(sys.argv) > 2 else "fanout"]
result = build(incident, Path(__file__).parent / "shop", dest, dbt=str(Path(sys.executable).with_name("dbt")))
print(f"{incident.name} ready in {dest}: {incident.description}")
print("\n".join(result.stdout.strip().splitlines()[-1:]))
