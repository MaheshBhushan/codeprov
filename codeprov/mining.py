"""Mine bounded public GitHub PR cohorts and immutable Git commit evidence."""
import json
import os
import re
import subprocess
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydriller import Repository

from .github import GitHub, GitHubError
from .provenance import classify


_SHA = re.compile(r"[0-9a-f]{40}")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_REFERENCE = re.compile(r"(?<![\w/#])(?:(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+))?#(?P<number>[1-9][0-9]*)\b")


def _git_environment():
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("GIT_"):
            del env[key]
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT="0", GIT_CONFIG_COUNT="4",
               GIT_CONFIG_KEY_0="core.hooksPath", GIT_CONFIG_VALUE_0=os.devnull,
               GIT_CONFIG_KEY_1="protocol.allow", GIT_CONFIG_VALUE_1="never",
               GIT_CONFIG_KEY_2="protocol.https.allow", GIT_CONFIG_VALUE_2="always",
               GIT_CONFIG_KEY_3="http.followRedirects", GIT_CONFIG_VALUE_3="false")
    return env


@contextmanager
def _safe_git_environment():
    previous = {key: value for key, value in os.environ.items() if key.startswith("GIT_")}
    safe = {key: value for key, value in _git_environment().items() if key.startswith("GIT_")}
    for key in previous:
        del os.environ[key]
    os.environ.update(safe)
    try:
        yield
    finally:
        for key in list(os.environ):
            if key.startswith("GIT_"):
                del os.environ[key]
        os.environ.update(previous)


def _git(path, *arguments, check=True):
    result = subprocess.run(["git", "-C", str(path), *arguments], env=_git_environment(),
                            capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(f"git {' '.join(arguments)} failed in dedicated clone {path}: {result.stderr.strip()}")
    return result


def _clone(repo, run_dir, metadata):
    requested_root = Path(run_dir) / "repos"
    if requested_root.is_symlink():
        raise ValueError(f"Dedicated clone root must not be a symlink: {requested_root}")
    root = requested_root.resolve()
    owner, name = repo.split("/")
    root.mkdir(parents=True, exist_ok=True)
    parent = root / owner
    path = parent / name
    marker = parent / f"{name}.codeprov-clone.json"
    if parent.is_symlink() or path.is_symlink() or marker.is_symlink():
        raise ValueError(f"Dedicated clone paths must not be symlinks: {path}")
    parent.mkdir(exist_ok=True)
    url = f"https://github.com/{repo}.git"
    if path.exists():
        if not marker.is_file() or json.loads(marker.read_text()).get("url") != url:
            raise ValueError(f"Refusing to modify an unowned repository at {path}; use a fresh run directory")
        if _git(path, "rev-parse", "--is-bare-repository").stdout.strip() != "true":
            raise ValueError(f"Expected codeprov's dedicated bare clone: {path}")
        if _git(path, "remote", "get-url", "origin").stdout.strip() != url:
            raise ValueError(f"Dedicated clone origin changed: {path}")
    else:
        template = root / ".empty-template"
        if template.is_symlink():
            raise ValueError(f"Clone template must not be a symlink: {template}")
        template.mkdir(exist_ok=True)
        result = subprocess.run(["git", "clone", "--bare", f"--template={template}", url, str(path)],
                                env=_git_environment(), capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(f"Public HTTPS full-history clone failed for {repo}: {result.stderr.strip()}")
        marker.write_text(json.dumps({
            "url": url, "full_history": True,
            "mainline_sha": _git(path, "rev-parse", "HEAD").stdout.strip(),
            "default_branch": metadata["default_branch"],
        }, sort_keys=True) + "\n")
    if _git(path, "rev-parse", "--is-shallow-repository").stdout.strip() != "false":
        raise ValueError(f"Full history is required, but clone is shallow: {path}")
    snapshot = json.loads(marker.read_text())
    tip = snapshot["mainline_sha"]
    return {"repo": repo, "path": str(path), "mainline_sha": tip,
            "default_branch": snapshot["default_branch"], "clone_url": url,
            "full_history": True}


def _ensure_sha(path, sha, pr_number):
    if not isinstance(sha, str) or not _SHA.fullmatch(sha):
        raise ValueError(f"Expected exact full Git commit SHA, got {sha!r}")
    if _git(path, "cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode == 0:
        return None
    attempt = _git(path, "fetch", "--no-tags", "origin", sha, check=False)
    if attempt.returncode:
        attempt = _git(path, "fetch", "--no-tags", "origin", f"refs/pull/{pr_number}/head", check=False)
    if _git(path, "cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode:
        return f"Commit object unavailable from GitHub SHA/PR-head fetch: {attempt.stderr.strip()}"
    return None


def _stamp(value):
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sample(api, repo, since, until, limit):
    """Search only the creation window; subdivide GitHub's 1,000-result ceiling."""
    start = datetime.fromisoformat(since).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(until).replace(tzinfo=timezone.utc) + timedelta(days=1, seconds=-1)
    selected = []
    total = None

    def interval(left, right):
        nonlocal total
        query = f"repo:{repo} is:pr is:merged created:{_stamp(left)}..{_stamp(right)}"
        params = {"q": query, "sort": "created", "order": "asc", "per_page": 100}
        payload, next_url, _ = api.get("/search/issues", params)
        if payload.get("incomplete_results"):
            raise GitHubError(f"GitHub search returned incomplete results for {query}; narrow the creation window")
        count = payload["total_count"]
        if total is None:
            total = count
        if count > 1000:
            seconds = int((right - left).total_seconds())
            if seconds == 0:
                raise GitHubError(f"More than 1,000 merged PRs created within one second for {repo}; "
                                  "GitHub REST search cannot provide a complete deterministic sample")
            middle = left + timedelta(seconds=seconds // 2)
            interval(left, middle)
            if len(selected) < limit:
                interval(middle + timedelta(seconds=1), right)
            return
        items = list(payload["items"])
        if next_url:
            for page, _ in api.pages(next_url):
                if page.get("incomplete_results"):
                    raise GitHubError(f"Incomplete paginated GitHub search for {query}")
                items.extend(page["items"])
                if len(items) > 1000:
                    raise GitHubError(f"Search pagination exceeded the bounded result bucket for {repo}")
        if len(items) != count or len({item["number"] for item in items}) != count:
            raise GitHubError(f"GitHub search count/page disagreement for {query}; cached cohort is incomplete")
        items.sort(key=lambda item: (item["created_at"], item["number"]))
        seen = {item["number"] for item in selected}
        for item in items:
            if item["number"] not in seen and len(selected) < limit:
                selected.append(item)
                seen.add(item["number"])

    interval(start, end)
    return selected, {"creation_window": [since, until], "available_merged_prs": total,
                      "selected_prs": len(selected), "max_prs": limit,
                      "capped": total > len(selected), "order": "created_at,number ascending"}


def _experience(path, parents, email):
    if not parents:
        return 0
    with subprocess.Popen(["git", "-C", str(path), "log", "--format=%ae", *parents, "--"],
                          env=_git_environment(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True) as process:
        count = sum(line.rstrip("\n") == email for line in process.stdout)
        error = process.stderr.read()
        if process.wait():
            raise RuntimeError(f"Author experience history failed in {path}: {error}")
    return count


def _commit(path, repo, sha, identities, manual):
    with _safe_git_environment():
        commit = next(Repository(str(path), single=sha).traverse_commits())
        classified = classify(commit.msg, identities, manual)
        parents = commit.parents
        files = []
        if len(parents) == 1:
            for modification in commit.modified_files:
                before = modification.source_code_before
                after = modification.source_code
                if before is None and modification.content_before == b"":
                    before = ""
                if after is None and modification.content == b"":
                    after = ""
                files.append({"path": modification.new_path or modification.old_path,
                              "old_path": modification.old_path, "new_path": modification.new_path,
                              "before": before, "after": after,
                              "added": modification.added_lines, "deleted": modification.deleted_lines,
                              "old_mode": modification._c_diff.a_mode,
                              "new_mode": modification._c_diff.b_mode})
        return {"repo": repo, "sha": sha, "parents": parents,
                "author": {"name": commit.author.name, "email": commit.author.email},
                "authored_at": commit.author_date.isoformat(), "committed_at": commit.committer_date.isoformat(),
                "message": commit.msg, "provenance": classified["provenance"],
                "provenance_evidence": classified["evidence"],
                "added_lines": sum(file["added"] for file in files) if len(parents) == 1 else None,
                "deleted_lines": sum(file["deleted"] for file in files) if len(parents) == 1 else None,
                "author_experience": _experience(path, parents, commit.author.email),
                "author_experience_definition": "reachable ancestor commits with exact author email",
                "files": files, "delta_status": "complete" if len(parents) == 1 else
                ("excluded_root" if not parents else "excluded_merge"), "status": "complete"}


def _issues(api, store, repo, number, pr, timeline):
    references = {(match.group("repo") or repo, int(match.group("number")), "pr_body_reference")
                  for match in _REFERENCE.finditer(pr.get("body") or "")}
    for event in timeline:
        issue = (event.get("source") or {}).get("issue")
        if event.get("event") == "cross-referenced" and issue:
            repository_url = issue.get("repository_url", "")
            prefix = "https://api.github.com/repos/"
            if repository_url.startswith(prefix):
                references.add((repository_url[len(prefix):], issue["number"], "timeline_cross_reference"))
    for issue_repo, issue_number, kind in sorted(references):
        if not _REPO.fullmatch(issue_repo) or any(part in (".", "..") for part in issue_repo.split("/")):
            continue
        if issue_repo == repo and issue_number == number:
            continue
        try:
            issue, _, raw = api.get(f"/repos/{issue_repo}/issues/{issue_number}")
        except GitHubError as error:
            if "GitHub HTTP 404 " not in str(error):
                raise
            store.put("issue_link", f"{repo}:{number}:{issue_repo}:{issue_number}:{kind}",
                      {"repo": repo, "pr_number": number, "issue_repo": issue_repo,
                       "issue_number": issue_number, "evidence_kind": kind,
                       "status": "unavailable", "reason": str(error), "validated_fix": False})
            continue
        if issue.get("pull_request"):
            continue
        store.put("issue", f"{issue_repo}:{issue_number}", {**issue, "repo": issue_repo, "raw_path": raw})
        store.put("issue_link", f"{repo}:{number}:{issue_repo}:{issue_number}:{kind}",
                  {"repo": repo, "pr_number": number, "issue_repo": issue_repo,
                   "issue_number": issue_number, "evidence_kind": kind,
                   "labels": issue.get("labels", []), "validated_fix": False, "raw_path": raw})


def mine(study, store, run_dir):
    manual = {}
    if study.manual_labels:
        manual = {(row["repo"], row["sha"]): row for row in json.loads(Path(study.manual_labels).read_text())}
    summary = {"status": "complete", "repositories": {}, "prs": 0, "commits": 0, "comments": 0,
               "unavailable_commits": [], "provenance_conflicts": []}
    counted = set()
    with GitHub(Path(run_dir) / "raw" / "github") as api:
        for repo in study.repositories:
            if not _REPO.fullmatch(repo) or any(part in (".", "..") for part in repo.split("/")):
                raise ValueError(f"Invalid public repository name: {repo}")
            metadata, _, _ = api.get(f"/repos/{repo}")
            if metadata.get("private") or metadata.get("visibility", "public") != "public":
                raise ValueError(f"Only public repositories may be mined: {repo}")
            repository = _clone(repo, run_dir, metadata)
            store.put("repo", repo, repository)
            path = Path(repository["path"])
            sampled, sampling = _sample(api, repo, study.since, study.until, study.max_prs)
            summary["repositories"][repo] = sampling
            store.put("sample", repo, {"repo": repo, **sampling})
            for item in sampled:
                number = item["number"]
                pr, _, raw = api.get(f"/repos/{repo}/pulls/{number}")
                if not pr.get("merged_at"):
                    raise GitHubError(f"Search/detail merge-state disagreement for {repo}#{number}; frozen cache is inconsistent")
                commits = []
                for page, _ in api.pages(f"/repos/{repo}/pulls/{number}/commits", {"per_page": 100}):
                    commits.extend(row["sha"] for row in page)
                commits = list(dict.fromkeys(commits))
                membership_source = "github_pr_commits"
                if len(commits) != pr.get("commits", len(commits)) and commits:
                    # The PR endpoint caps at 250; paginated compare retains actual SHAs.
                    known = set(commits)
                    compared = [commits[0]]
                    for page, _ in api.pages(
                        f"/repos/{repo}/compare/{commits[0]}...{pr['head']['sha']}", {"per_page": 100}
                    ):
                        compared.extend(row["sha"] for row in page["commits"])
                    compared = list(dict.fromkeys(compared))
                    if known.issubset(compared) and len(compared) == pr["commits"]:
                        commits = compared
                        membership_source = "github_pr_commits_and_paginated_compare"
                if len(commits) != pr.get("commits", len(commits)):
                    raise GitHubError(f"GitHub PR/compare membership evidence is incomplete for {repo}#{number}: "
                                      f"received {len(commits)} of {pr.get('commits')}; no partial memberships inferred")
                record = {"repo": repo, "number": number, "base_sha": pr["base"]["sha"],
                          "head_sha": pr["head"]["sha"], "merge_sha": pr["merge_commit_sha"],
                          "created_at": pr["created_at"], "merged_at": pr["merged_at"],
                          "commits": commits, "membership_source": membership_source,
                          "url": pr["html_url"], "raw_path": raw}
                unavailable = {}
                for sha in dict.fromkeys([*commits, record["base_sha"], record["head_sha"], record["merge_sha"]]):
                    if sha:
                        reason = _ensure_sha(path, sha, number)
                        if reason:
                            unavailable[sha] = reason
                if unavailable:
                    summary["status"] = "partial"
                record["unavailable_shas"] = unavailable
                store.put("pr", f"{repo}:{number}", record)
                summary["prs"] += 1
                for sha in commits:
                    key = f"{repo}:{sha}"
                    previous = store.get("commit", key)
                    if sha in unavailable:
                        commit = {"repo": repo, "sha": sha, "status": "unavailable", "reason": unavailable[sha],
                                  "provenance": "unknown", "provenance_evidence": {"reason": "Git object unavailable"},
                                  "parents": [], "files": [], "added_lines": None, "deleted_lines": None,
                                  "author_experience": None, "delta_status": "unavailable"}
                        summary["unavailable_commits"].append(key)
                        summary["status"] = "partial"
                    elif previous and previous.get("status") == "complete":
                        commit = previous
                    else:
                        commit = _commit(path, repo, sha, study.ai_identities, manual.get((repo, sha)))
                    commit["pr_numbers"] = sorted(set((previous or {}).get("pr_numbers", [])) | {number})
                    store.put("commit", key, commit)
                    if key not in counted:
                        summary["commits"] += 1
                        counted.add(key)
                        if commit["provenance_evidence"].get("conflict"):
                            summary["provenance_conflicts"].append(key)
                for comments, raw_path in api.pages(f"/repos/{repo}/pulls/{number}/comments", {"per_page": 100}):
                    for comment in comments:
                        original = comment.get("original_commit_id") or comment.get("commit_id")
                        reason = _ensure_sha(path, original, number) if original else "Comment has no commit identity"
                        if reason:
                            summary["status"] = "partial"
                        store.put("comment", f"{repo}:{comment['id']}",
                                  {**comment, "repo": repo, "pr_number": number, "raw_path": raw_path,
                                   "snapshot_unavailable_reason": reason})
                        summary["comments"] += 1
                timeline = []
                for events, _ in api.pages(f"/repos/{repo}/issues/{number}/timeline", {"per_page": 100}):
                    timeline.extend(events)
                _issues(api, store, repo, number, pr, timeline)
    summary["provenance_conflicts"].sort()
    return summary
