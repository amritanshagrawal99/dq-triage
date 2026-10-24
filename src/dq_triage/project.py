"""dbt artifacts (manifest + run_results) and the git history of the project."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FailedTest:
    unique_id: str
    name: str
    status: str            # fail | error
    failures: int | None
    message: str | None
    test_type: str | None  # unique, not_null, relationships, accepted_values, custom...
    column: str | None
    model: str | None      # unique_id of the model under test
    compiled_sql: str | None
    depends_on: list[str] = field(default_factory=list)


class Project:
    def __init__(self, root: str | Path, target_dir: str = "target"):
        self.root = Path(root)
        target = self.root / target_dir
        self.manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        rr = target / "run_results.json"
        self.run_results = json.loads(rr.read_text(encoding="utf-8")) if rr.exists() else {"results": []}
        self.nodes = {**self.manifest.get("nodes", {}), **self.manifest.get("sources", {})}
        self.parent_map: dict[str, list[str]] = self.manifest.get("parent_map", {})
        self.child_map: dict[str, list[str]] = self.manifest.get("child_map", {})

    # ---------------------------------------------------------------- tests
    def failed_tests(self) -> list[FailedTest]:
        out = []
        for r in self.run_results.get("results", []):
            uid = r.get("unique_id", "")
            if not uid.startswith("test.") or r.get("status") not in ("fail", "error"):
                continue
            node = self.nodes.get(uid, {})
            meta = node.get("test_metadata") or {}
            kwargs = meta.get("kwargs") or {}
            model = node.get("attached_node") or next(
                (d for d in (node.get("depends_on") or {}).get("nodes", []) if d.startswith("model.")), None)
            out.append(FailedTest(
                unique_id=uid, name=node.get("name", uid), status=r["status"], failures=r.get("failures"),
                message=r.get("message"), test_type=meta.get("name") or "custom",
                column=kwargs.get("column_name") or node.get("column_name"),
                model=model, compiled_sql=node.get("compiled_code"),
                depends_on=(node.get("depends_on") or {}).get("nodes", []),
            ))
        return out

    # ---------------------------------------------------------------- lineage
    def find(self, name_or_id: str) -> str | None:
        if name_or_id in self.nodes:
            return name_or_id
        hits = [uid for uid, n in self.nodes.items() if n.get("name") == name_or_id and not uid.startswith("test.")]
        return hits[0] if hits else None

    def ancestors(self, uid: str) -> list[str]:
        seen: list[str] = []
        stack = list(self.parent_map.get(uid, []))
        while stack:
            p = stack.pop()
            if p in seen:
                continue
            seen.append(p)
            stack.extend(self.parent_map.get(p, []))
        return seen

    def descendants(self, uid: str, include_tests: bool = False) -> list[str]:
        seen: list[str] = []
        stack = list(self.child_map.get(uid, []))
        while stack:
            c = stack.pop()
            if c in seen:
                continue
            seen.append(c)
            stack.extend(self.child_map.get(c, []))
        return [c for c in seen if include_tests or not c.startswith("test.")]

    def relation(self, uid: str) -> str | None:
        return self.nodes.get(uid, {}).get("relation_name")

    def name(self, uid: str) -> str:
        return self.nodes.get(uid, {}).get("name", uid.split(".")[-1])

    def file_path(self, uid: str) -> str | None:
        path = self.nodes.get(uid, {}).get("original_file_path")
        return path.replace("\\", "/") if path else None

    def describe_model(self, uid: str) -> dict:
        n = self.nodes[uid]
        return {
            "unique_id": uid,
            "resource_type": n.get("resource_type"),
            "relation": n.get("relation_name"),
            "file": n.get("original_file_path"),
            "materialized": (n.get("config") or {}).get("materialized"),
            "raw_sql": n.get("raw_code"),
            "compiled_sql": n.get("compiled_code"),
            "parents": [self.name(p) for p in self.parent_map.get(uid, [])],
            "children": [self.name(c) for c in self.child_map.get(uid, []) if not c.startswith("test.")],
            "tests": [self.name(c) for c in self.child_map.get(uid, []) if c.startswith("test.")],
        }

    def exposures_downstream(self, uid: str) -> list[dict]:
        hits = []
        down = set(self.descendants(uid)) | {uid}
        for e in self.manifest.get("exposures", {}).values():
            if down & set((e.get("depends_on") or {}).get("nodes", [])):
                owner = e.get("owner") or {}
                hits.append({"name": e.get("name"), "type": e.get("type"), "owner": owner.get("name") or owner.get("email")})
        return hits

    # ---------------------------------------------------------------- git (read-only, fixed argv)
    def _git(self, *args: str) -> str:
        try:
            res = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            return f"(git unavailable: {e})"
        return res.stdout if res.returncode == 0 else f"(git error: {res.stderr.strip()[:300]})"

    def git_log(self, paths: list[str] | None = None, n: int = 15) -> str:
        args = ["log", f"-n{max(1, min(n, 50))}", "--date=iso-strict", "--format=%h | %ad | %an | %s", "--"]
        return self._git(*args, *(paths or [])).strip() or "(no commits)"

    def git_show(self, commit: str, paths: list[str] | None = None) -> str:
        if not commit.replace("~", "").replace("^", "").isalnum():
            return "(invalid commit reference)"
        out = self._git("show", "--stat", "--patch", "--format=%h | %ad | %an%n%s%n%b", commit, "--", *(paths or []))
        return out[:12000]
