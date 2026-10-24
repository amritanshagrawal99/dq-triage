import pytest

from dq_triage.db import ReadOnlyViolation, check_read_only


@pytest.mark.parametrize("sql", [
    "select 1",
    "with x as (select 1 as a) select a from x",
    "select a from t union all select a from u",
    "select count(*) from shop.main.orders where status = 'delete'",
])
def test_reads_allowed(sql):
    check_read_only(sql, "duckdb")


@pytest.mark.parametrize("sql", [
    "delete from orders",
    "insert into orders select * from orders",
    "update orders set amount = 0",
    "drop table orders",
    "create table x as select 1",
    "copy orders to 'out.csv'",
    "attach 'other.db'",
    "select 1; drop table orders",
    "select * into new_table from orders",
    "merge into t using s on t.id = s.id when matched then delete",
])
def test_writes_rejected(sql):
    with pytest.raises(ReadOnlyViolation):
        check_read_only(sql, "duckdb")


def test_query_adds_limit_and_connection_is_read_only(db):
    cols, rows = db.query("select * from range(1000)", limit=5)
    assert len(rows) == 5
    # Even if the guard were bypassed, the connection itself refuses writes.
    with pytest.raises(Exception):  # noqa: B017
        db.conn.execute("create table hack as select 1")
