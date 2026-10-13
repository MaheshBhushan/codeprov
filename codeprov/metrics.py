"""Before/after Radon and security measurements over mined commit content."""
import ast
import hashlib
import math
from pathlib import Path

from .security import SUCCESS_STATUSES, diff_findings, safe_relative_path, scan_snapshot


def source_metrics(source: str) -> dict:
    """Radon MI, physical LOC, and summed functions/methods (not class totals)."""
    from radon.metrics import mi_visit
    from radon.raw import analyze
    from radon.visitors import ComplexityVisitor

    tree = ast.parse(source)
    # Visit each definition exactly once as a scored block, including local classes.
    # Radon's function score excludes closure scores; never use a class aggregate.
    complexity = sum(ComplexityVisitor.from_ast(node).functions[0].complexity
                     for node in ast.walk(tree)
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))

    mi = mi_visit(source, multi=True)
    return {"mi": mi if math.isfinite(mi) else None,
            "complexity": complexity,
            "loc": analyze(source).loc}


def _delta(before, after, metric):
    if before is None or after is None or before[metric] is None or after[metric] is None:
        return None
    return after[metric] - before[metric]


def _symlink(mode):
    return mode in (0o120000, 120000, "120000", "0o120000")


def _skipped_scanner(reason):
    side = {"status": "skipped", "findings": None, "reason": reason, "artifacts": {}}
    return {"status": "skipped", "before": dict(side), "after": dict(side),
            "introduced": None, "resolved": None, "error": reason}


def _file_side(snapshot: dict, path: str, present: bool) -> dict:
    if not present:
        return {"status": "not_applicable", "findings": [], "reason": "file_absent", "artifacts": {}}
    return {**snapshot, "findings": [finding for finding in snapshot["findings"] if finding["path"] == path]}


def _scanner_delta(before: dict, after: dict) -> dict:
    result = {"status": "complete", "before": before, "after": after,
              "introduced": None, "resolved": None}
    failed = [side for side in (before, after) if side["status"] not in SUCCESS_STATUSES]
    if failed:
        result["status"] = failed[0]["status"]
        result["error"] = "; ".join(side.get("error", side["status"]) for side in failed)
    else:
        result.update(diff_findings(before["findings"], after["findings"]))
    return result


def measure(study, store, run_dir: Path) -> dict:
    """Replace idempotent measurements, preserving exclusions and scanner failures."""
    commits = store.rows("commit")
    store.clear("measurement")
    counts = {"commits": len(commits), "files": 0, "complete": 0, "partial": 0, "skipped": 0,
              "scanner_failures": {"bandit": 0, "semgrep": 0}}
    excluded_commits = []
    reasons = []
    # CLI freezes the configuration bytes alongside the run manifest.
    frozen_rules = Path(run_dir) / f"semgrep_rules{study.semgrep_rules.suffix}"
    rules = frozen_rules if frozen_rules.is_file() else study.semgrep_rules
    for commit in commits:
        parent_count = len(commit.get("parents", []))
        exclusion = "unavailable_commit" if commit.get("status") == "unavailable" else (
            "root_commit" if parent_count == 0 else "merge_commit" if parent_count > 1 else None
        )
        if exclusion == "unavailable_commit":
            reasons.append({"repo": commit["repo"], "sha": commit["sha"], "reason": exclusion})
        if exclusion:
            excluded_commits.append({"repo": commit["repo"], "sha": commit["sha"], "reason": exclusion})
        rows = []
        snapshots = {"before": {}, "after": {}}
        seen = set()
        for file in commit.get("files", []):
            path = file["path"]
            old_path = file.get("old_path") or path
            if not path.endswith(".py") and not old_path.endswith(".py"):
                continue
            # On a rename out of Python, measure the former Python path only.
            canonical = path if path.endswith(".py") else old_path
            row = {"repo": commit["repo"], "sha": commit["sha"], "path": canonical,
                   "status": "complete", "before": None, "after": None,
                   "mi_delta": None, "complexity_delta": None, "loc_delta": None,
                   "added_lines": file.get("added", 0), "deleted_lines": file.get("deleted", 0)}
            reason = exclusion
            try:
                safe_relative_path(path)
                safe_relative_path(old_path)
            except ValueError as error:
                reason = "unsafe_path"
                row["error"] = str(error)
            if _symlink(file.get("old_mode")) or _symlink(file.get("new_mode")):
                reason = "symlink_source"
            if canonical in seen:
                reason = "duplicate_path"
            seen.add(canonical)
            if reason:
                row.update(status="skipped", reason=reason)
                row["bandit"] = _skipped_scanner(reason)
                row["semgrep"] = _skipped_scanner(reason)
                if reason in ("unsafe_path", "duplicate_path"):
                    reasons.append({"repo": commit["repo"], "sha": commit["sha"],
                                    "path": canonical, "reason": reason})
            else:
                errors = {}
                for side, side_path in (("before", old_path), ("after", path)):
                    source = file.get(side) if side_path.endswith(".py") else None
                    if source is None:
                        presence_key = "old_path" if side == "before" else "new_path"
                        if presence_key in file and file[presence_key] is not None and side_path.endswith(".py"):
                            errors[side] = "existing Python file content unavailable"
                        continue
                    if not isinstance(source, str):
                        errors[side] = "source must be a string or null"
                        continue
                    snapshots[side][canonical] = source
                    try:
                        row[side] = source_metrics(source)
                    except (SyntaxError, ValueError, TypeError, ImportError, RecursionError) as error:
                        errors[side] = f"{type(error).__name__}: {error}"
                if errors:
                    row.update(status="partial", metric_errors=errors)
                if row["before"] is None and row["after"] is None and not errors:
                    row.update(status="partial", reason="no_source_content")
                for metric in ("mi", "complexity", "loc"):
                    row[f"{metric}_delta"] = _delta(row["before"], row["after"], metric)
            rows.append(row)
        active = [row for row in rows if row["status"] != "skipped"]
        if active:
            token = hashlib.sha256(f"{commit['repo']}:{commit['sha']}".encode()).hexdigest()
            artifacts = Path(run_dir) / "scans" / token
            scans = {side: scan_snapshot(snapshots[side], rules, artifacts / side)
                     for side in ("before", "after")}
            for row in active:
                for tool in ("bandit", "semgrep"):
                    sides = {side: _file_side(scans[side][tool], row["path"], row["path"] in snapshots[side])
                             for side in ("before", "after")}
                    # Missing/malformed source is unknown, not a known absent file.
                    for side in row.get("metric_errors", {}):
                        if row["path"] not in snapshots[side]:
                            sides[side] = {"status": "invalid_snapshot", "findings": None,
                                           "artifacts": {}, "error": row["metric_errors"][side]}
                    if row.get("reason") == "no_source_content":
                        sides["before"] = {"status": "invalid_snapshot", "findings": None,
                                           "artifacts": {}, "error": "no source content on either side"}
                    row[tool] = _scanner_delta(sides["before"], sides["after"])
        for row in rows:
            store.put("measurement", f"{row['repo']}:{row['sha']}:{row['path']}", row)
            counts["files"] += 1
            counts[row["status"]] += 1
            for tool in ("bandit", "semgrep"):
                if row[tool]["status"] not in ("complete", "skipped"):
                    counts["scanner_failures"][tool] += 1
    partial = bool(counts["partial"] or reasons or any(counts["scanner_failures"].values()) or not commits)
    return {"status": "partial" if partial else "complete", **counts,
            "excluded_commits": excluded_commits, "issues": reasons,
            **({"reason": "no_mined_commits"} if not commits else {})}
