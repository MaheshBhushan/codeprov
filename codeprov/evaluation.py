"""Human-only, issue-level evaluation of actually reviewed historical snapshots.

Adjudication input is exactly {"version": 1, "snapshots": [
  {"snapshot_id": str, "split": "development"|"test",
   "comments": [{"comment_id": int, "disposition": "issue"|"ignore",
                 "canonical_comment_id": int|null, "rationale": str}],
   "finding_groups": [{"canonical_finding_id": str, "finding_ids": [str],
                       "rationale": str, "genuine": bool}],
   "matches": [{"finding_id": str, "comment_id": int, "rationale": str}]}
]}.
All keys are required; additional keys are rejected. Every actually reviewed
snapshot, every nonbot comment on its original_commit_id, and every model
finding must occur exactly once. Exported/skipped/failed requests are excluded.
Replies are not automatically gold: humans explicitly ignore or map them.
Ignored comments require null canonical IDs; issue comments map to a same-
snapshot canonical comment which maps to itself. Multiple comments may map to
one historical issue. Each finding group represents one semantic issue and
covers one or more findings; canonical_finding_id must be one of its members.
All rationales are nonempty. Groups require human genuine=true/false even when
unmatched. Matches use canonical IDs, are one-to-one, stay in their snapshot
and frozen split, and can only match genuine findings. No semantic labels are
inferred from text, location, replies, or duplicate wording.

review/adjudication-worksheet.json contains contract, blank adjudications, and
human-only evidence. Fill/extract the adjudications object into the input file;
null cells are pending, not false. Evidence includes excluded bot comments.

Recovery: TP=matched issue pairs, FP=unmatched finding groups (including real
issues absent from historical comments), FN=unmatched historical issue groups.
Precision=TP/(TP+FP), recall=TP/(TP+FN), null for a zero denominator.
Adjudicated precision=genuine finding groups/all finding groups, reported
separately; genuine_unmatched counts valid discoveries not historically noted.
reviewed_snapshots includes completed empty-finding API responses, never exports.
Records kind=evaluation are per-PR {repo,pr_number,split,tp,fp,fn,precision,
recall,adjudicated_precision,reviewed_snapshots,historical_issues,finding_issues,
genuine_findings,genuine_unmatched,invalid_findings,status}. Return includes
per_pr, per_split, total, worksheet_path, and adjudication_contract. No complete
scores are written if coverage/labels fail validation. Input bytes are frozen
with a SHA256 and copied to the run only after validation.
"""
import hashlib
import json
from pathlib import Path

from .config import Study
from .storage import Store


ADJUDICATION_CONTRACT = {
    "version": 1,
    "root_keys": ["version", "snapshots"],
    "snapshot_keys": ["snapshot_id", "split", "comments", "finding_groups", "matches"],
    "comment_keys": ["comment_id", "disposition", "canonical_comment_id", "rationale"],
    "finding_group_keys": ["canonical_finding_id", "finding_ids", "rationale", "genuine"],
    "match_keys": ["finding_id", "comment_id", "rationale"],
    "comment_dispositions": ["issue", "ignore"],
    "coverage": "Every reviewed snapshot, nonbot original-snapshot comment, and finding exactly once",
    "canonical_comments": "Issue mappings stay within snapshot; canonical maps to itself; ignore uses null",
    "finding_groups": "Nonempty member IDs; canonical is a member; rationale and human genuine boolean required",
    "matches": "One-to-one canonical issue pairs within snapshot and frozen split; finding must be genuine",
    "rationales": "Nonempty strings required for all comment mappings, finding groups and matches",
    "pending": "Null worksheet cells are not adjudicated labels; extract/fill adjudications object",
    "precision": "Historical recovery TP/(TP+FP); unmatched genuine findings still FP for recovery only",
    "adjudicated_precision": "Human genuine finding issues / all finding issues",
    "zero_denominators": "null",
}


def _data(store: Store) -> tuple[dict, dict, dict, list]:
    from .review import is_bot

    requests = {row["id"]: row for row in store.rows("review_request") if row.get("status") == "reviewed"}
    comments = {snapshot_id: [] for snapshot_id in requests}
    excluded = []
    for comment in store.rows("comment"):
        if is_bot(comment):
            excluded.append(dict(comment, exclusion_reason="bot comment"))
            continue
        if not str(comment.get("path", "")).endswith(".py"):
            excluded.append(dict(comment, exclusion_reason="non-Python review comment"))
            continue
        snapshot_id = f"{comment['repo']}:{comment['pr_number']}:{comment.get('original_commit_id')}"
        if snapshot_id in requests:
            comments[snapshot_id].append(comment)
    findings = {snapshot_id: [] for snapshot_id in requests}
    for finding in store.rows("review_finding"):
        if finding.get("snapshot_id") in requests:
            findings[finding["snapshot_id"]].append(finding)
    for rows in comments.values():
        rows.sort(key=lambda row: row["id"])
    for rows in findings.values():
        rows.sort(key=lambda row: row["id"])
    return requests, comments, findings, excluded


def export_worksheet(study: Study, store: Store, run_dir: Path) -> Path:
    requests, comments, findings, excluded = _data(store)
    skeleton = []
    evidence = []
    for snapshot_id, request in sorted(requests.items()):
        skeleton.append({
            "snapshot_id": snapshot_id, "split": request["split"],
            "comments": [{"comment_id": row["id"], "disposition": None,
                          "canonical_comment_id": None, "rationale": None} for row in comments[snapshot_id]],
            "finding_groups": [{"canonical_finding_id": row["id"], "finding_ids": [row["id"]],
                                "rationale": None, "genuine": None} for row in findings[snapshot_id]],
            "matches": [],
        })
        evidence.append({"snapshot_id": snapshot_id, "repo": request["repo"],
                         "pr_number": request["pr_number"], "sha": request["sha"], "split": request["split"],
                         "request_path": request["request_path"],
                         "comments": comments[snapshot_id], "findings": findings[snapshot_id]})
    path = run_dir / "review" / "adjudication-worksheet.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"contract": ADJUDICATION_CONTRACT,
                               "adjudications": {"version": 1, "snapshots": skeleton},
                               "evidence": evidence, "excluded_comments": excluded,
                               "not_reviewed": [row for row in store.rows("review_request")
                                                if row.get("status") != "reviewed"]},
                              indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    return path


def _object(value, keys: set[str], context: str):
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"{context} must contain exactly {sorted(keys)}")


def _list(value, context: str):
    if not isinstance(value, list):
        raise ValueError(f"{context} must be an array")


def _rationale(value, context: str):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} requires a nonempty human rationale")


def _empty() -> dict:
    return {key: 0 for key in ("tp", "fp", "fn", "reviewed_snapshots", "historical_issues",
                               "finding_issues", "genuine_findings", "genuine_unmatched", "invalid_findings")}


def _rates(counts: dict) -> dict:
    return dict(counts, precision=counts["tp"] / (counts["tp"] + counts["fp"])
                if counts["tp"] + counts["fp"] else None,
                recall=counts["tp"] / (counts["tp"] + counts["fn"])
                if counts["tp"] + counts["fn"] else None,
                adjudicated_precision=counts["genuine_findings"] / counts["finding_issues"]
                if counts["finding_issues"] else None)


def _add(target: dict, source: dict):
    for key in _empty():
        target[key] += source[key]


def _invalidate_results(store: Store, run_dir: Path):
    store.clear("evaluation")
    store.clear("analysis")
    store.stage("evaluate", "pending", {"reason": "review evidence changed; human adjudications required"})
    store.stage("analyze", "pending", {"reason": "review evidence changed; rerun analyze"})
    store.stage("report", "pending", {"reason": "review evidence changed; rerun report"})
    for name in ("analysis.json", "review-analysis.csv", "report.md", "evidence.json"):
        (run_dir / name).unlink(missing_ok=True)


def evaluate(study: Study, store: Store, run_dir: Path, adjudications: Path) -> dict:
    worksheet = export_worksheet(study, store, run_dir)
    requests, comments, findings, _ = _data(store)
    # Old complete scores must not survive a new incomplete or invalid annotation.
    _invalidate_results(store, run_dir)
    raw = adjudications.read_bytes()
    labels = json.loads(raw)
    _object(labels, {"version", "snapshots"}, "adjudications")
    if type(labels["version"]) is not int or labels["version"] != 1:
        raise ValueError("adjudication version must be integer 1")
    _list(labels["snapshots"], "snapshots")
    seen_snapshots = set()
    per_pr = {}
    for label in labels["snapshots"]:
        _object(label, {"snapshot_id", "split", "comments", "finding_groups", "matches"}, "snapshot")
        snapshot_id = label["snapshot_id"]
        if not isinstance(snapshot_id, str) or snapshot_id not in requests or snapshot_id in seen_snapshots:
            raise ValueError("unknown, unreviewed, or duplicate snapshot_id")
        seen_snapshots.add(snapshot_id)
        request = requests[snapshot_id]
        expected_split = "development" if request["repo"] in study.review.get("development_repositories", []) else "test"
        if label["split"] != expected_split or request["split"] != expected_split:
            raise ValueError("development/test split mixing or changed frozen split")
        for key in ("comments", "finding_groups", "matches"):
            _list(label[key], key)
        available_comments = {row["id"] for row in comments[snapshot_id]}
        comment_map = {}
        for comment in label["comments"]:
            _object(comment, {"comment_id", "disposition", "canonical_comment_id", "rationale"}, "comment mapping")
            comment_id = comment["comment_id"]
            if type(comment_id) is not int or comment_id not in available_comments or comment_id in comment_map:
                raise ValueError("invented, bot, cross-snapshot, or duplicate comment_id")
            _rationale(comment["rationale"], "comment mapping")
            if comment["disposition"] == "ignore":
                if comment["canonical_comment_id"] is not None:
                    raise ValueError("ignored comments must have null canonical_comment_id")
            elif comment["disposition"] == "issue":
                if type(comment["canonical_comment_id"]) is not int or comment["canonical_comment_id"] not in available_comments:
                    raise ValueError("canonical comment must belong to the same historical snapshot")
            else:
                raise ValueError("comment disposition must be issue or ignore; pending labels are incomplete")
            comment_map[comment_id] = comment
        if set(comment_map) != available_comments:
            raise ValueError("human adjudication must cover every eligible comment")
        human_issues = set()
        for comment in comment_map.values():
            if comment["disposition"] == "issue":
                canonical = comment["canonical_comment_id"]
                target = comment_map[canonical]
                if target["disposition"] != "issue" or target["canonical_comment_id"] != canonical:
                    raise ValueError("canonical issue comment must map to itself, not a reply chain or ignored comment")
                human_issues.add(canonical)
        available_findings = {row["id"] for row in findings[snapshot_id]}
        covered_findings = set()
        groups = {}
        for group in label["finding_groups"]:
            _object(group, {"canonical_finding_id", "finding_ids", "rationale", "genuine"}, "finding group")
            _list(group["finding_ids"], "finding_ids")
            _rationale(group["rationale"], "finding grouping")
            if type(group["genuine"]) is not bool:
                raise ValueError("every finding group requires a human genuine boolean")
            canonical = group["canonical_finding_id"]
            if not isinstance(canonical, str) or canonical in groups or canonical not in group["finding_ids"]:
                raise ValueError("finding group canonical ID must be a unique group member")
            if not group["finding_ids"]:
                raise ValueError("finding groups cannot be empty")
            for finding_id in group["finding_ids"]:
                if not isinstance(finding_id, str) or finding_id not in available_findings or finding_id in covered_findings:
                    raise ValueError("invented, cross-snapshot, or duplicate finding_id")
                covered_findings.add(finding_id)
            groups[canonical] = group
        if covered_findings != available_findings:
            raise ValueError("human grouping must cover every finding exactly once before matching")
        matched_findings = set()
        matched_comments = set()
        for match in label["matches"]:
            _object(match, {"finding_id", "comment_id", "rationale"}, "match")
            _rationale(match["rationale"], "semantic match")
            finding_id, comment_id = match["finding_id"], match["comment_id"]
            if not isinstance(finding_id, str) or type(comment_id) is not int:
                raise ValueError("match IDs have invalid types")
            if finding_id not in groups or comment_id not in human_issues:
                raise ValueError("matches must use same-snapshot canonical issue IDs")
            if finding_id in matched_findings or comment_id in matched_comments:
                raise ValueError("semantic issue matches must be one-to-one")
            if not groups[finding_id]["genuine"]:
                raise ValueError("historical issue match cannot be labeled an invalid finding")
            matched_findings.add(finding_id)
            matched_comments.add(comment_id)
        tp = len(matched_findings)
        genuine = sum(group["genuine"] for group in groups.values())
        counts = {"tp": tp, "fp": len(groups) - tp, "fn": len(human_issues) - tp,
                  "reviewed_snapshots": 1, "historical_issues": len(human_issues),
                  "finding_issues": len(groups), "genuine_findings": genuine,
                  "genuine_unmatched": genuine - tp, "invalid_findings": len(groups) - genuine}
        pr_key = (request["repo"], request["pr_number"])
        if pr_key not in per_pr:
            per_pr[pr_key] = dict(_empty(), repo=request["repo"], pr_number=request["pr_number"], split=expected_split)
        _add(per_pr[pr_key], counts)
    if seen_snapshots != set(requests):
        raise ValueError("adjudication must cover every actually reviewed snapshot; exports do not count")
    if not requests:
        return {"status": "pending", "reason": "no actually reviewed snapshots",
                "worksheet_path": str(worksheet), "per_pr": [], "per_split": {}, "total": None,
                "adjudication_contract": ADJUDICATION_CONTRACT}
    digest = hashlib.sha256(raw).hexdigest()
    copied = run_dir / "review" / ("adjudications-" + digest + ".json")
    copied.write_bytes(raw)
    splits = {name: _empty() for name in ("development", "test")}
    total = _empty()
    rows = []
    for key, counts in sorted(per_pr.items()):
        _add(splits[counts["split"]], counts)
        _add(total, counts)
        record = dict(_rates(counts), status="complete", adjudication_sha256=digest)
        store.put("evaluation", f"{key[0]}:{key[1]}", record)
        rows.append(record)
    return {"status": "complete", "per_pr": rows,
            "per_split": {name: _rates(counts) for name, counts in splits.items()}, "total": _rates(total),
            "worksheet_path": str(worksheet), "adjudication_path": str(copied),
            "adjudication_sha256": digest, "adjudication_contract": ADJUDICATION_CONTRACT}
