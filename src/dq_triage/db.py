"""Read-only query execution. Every statement the agent sends is checked before it reaches the database."""
from __future__ import annotations

import sqlglot
from sqlglot import exp

FORBIDDEN = (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter, exp.Command,
             exp.Into, exp.Copy, exp.Set, exp.Use, exp.Transaction, exp.Commit, exp.Rollback)


class ReadOnlyViolation(ValueError):
    pass


def check_read_only(sql: str, dialect: str) -> exp.Query:
    """Accept exactly one SELECT/WITH/UNION query; anything else raises ReadOnlyViolation."""
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except sqlglot.errors.ParseError as e:
        raise ReadOnlyViolation(f"could not parse query: {str(e).splitlines()[0]}") from e
    if len(statements) != 1:
        raise ReadOnlyViolation("send exactly one statement")
    stmt = statements[0]
    if not isinstance(stmt, exp.Query):
        raise ReadOnlyViolation(f"only SELECT queries are allowed, got {type(stmt).__name__.upper()}")
    bad = next(stmt.find_all(*FORBIDDEN), None)
    if bad is not None:
        raise ReadOnlyViolation(f"{type(bad).__name__.upper()} is not allowed inside a query")
    return stmt


class Database:
    """Thin DB-API wrapper. DuckDB files are opened read-only with external file access disabled."""

    def __init__(self, conn, dialect: str):
        self.conn = conn
        self.dialect = dialect

    @classmethod
    def from_url(cls, url: str) -> Database:
        scheme, _, path = url.partition(":///")
        if scheme == "duckdb":
            import duckdb
            return cls(duckdb.connect(path, read_only=True, config={"enable_external_access": False}), "duckdb")
        if scheme == "sqlite":
            import sqlite3
            return cls(sqlite3.connect(f"file:{path}?mode=ro", uri=True), "sqlite")
        raise ValueError(f"unsupported database URL {url!r} (use duckdb:///path or sqlite:///path)")

    def query(self, sql: str, limit: int = 200) -> tuple[list[str], list[tuple]]:
        stmt = check_read_only(sql, self.dialect)
        if stmt.args.get("limit") is None:
            stmt = stmt.limit(limit)
        cur = self.conn.cursor()
        try:
            cur.execute(stmt.sql(dialect=self.dialect))
            cols = [d[0] for d in cur.description or []]
            rows = cur.fetchmany(limit)
        finally:
            cur.close()
        return cols, rows


def format_rows(cols: list[str], rows: list[tuple], max_chars: int = 6000) -> str:
    if not cols:
        return "(no result set)"
    lines = [" | ".join(cols), " | ".join("---" for _ in cols)]
    lines += [" | ".join("NULL" if v is None else str(v) for v in r) for r in rows]
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n... (truncated; {len(rows)} rows total)"
    return text + f"\n({len(rows)} row{'s' if len(rows) != 1 else ''})"
