"""Evidence-limited SZZ, using historical mainline reachability and fixed windows.

PyDriller blames deleted lines against the single parent with ``git blame -w``;
its whitespace-ignoring diff also excludes whitespace-only fixes. This is not
semantic defect detection: addition-only fixes, binary files, comment-only lines,
refactorings, moved/copied code, and complex renames can escape attribution.
Root/merge fixes and unresolved squash/rebase mappings are not approximated.
No checkout is performed, including for bare mining clones.
"""
import json
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from git.exc import GitCommandError
from pydriller import Git

from .config import Study
from .mining import _safe_git_environment
from .storage import Store


class _BlameUnavailable(RuntimeError):
    pass


class _SZZGit(Git):
    def _get_blame(self, commit_hash, path, hashes_to_ignore_path=None):
        # PyDriller otherwise swallows GitCommandError and returns empty evidence.
        try:
            return super()._get_blame(commit_hash, path, hashes_to_ignore_path)
        except GitCommandError as exc:
            raise _BlameUnavailable(str(exc)) from exc


def _commit_time(commit) -> datetime:
    return commit.committer_date.astimezone(timezone.utc)


def _mapping_reason(sha, prs, git, cutoff):
    memberships = [pr for pr in prs if sha in pr.get("commits", [])]
    mappings = []
    for pr in memberships:
        merge_sha = pr.get("merge_sha")
        item = {"pr_number": pr["number"], "merge_sha": merge_sha}
        if not merge_sha:
            item["reason"] = "integration_sha_unavailable"
        else:
            try:
                integration = git.get_commit(merge_sha)
                if not git.repo.is_ancestor(sha, integration.hash):
                    item["reason"] = "squash_or_rebase_mapping_unresolved"
                elif _commit_time(integration) > cutoff:
                    item["reason"] = "integration_after_observed_cutoff"
                else:
                    item["reason"] = "integration_not_reachable_at_observed_cutoff"
            except Exception as exc:
                item.update(reason="integration_sha_unavailable", error=str(exc))
        mappings.append(item)
    unresolved = any(item["reason"] in ("integration_sha_unavailable", "squash_or_rebase_mapping_unresolved") for item in mappings)
    return ("unresolved_integration_mapping" if unresolved else "not_mainline_reachable_at_observed_cutoff"), mappings


@_safe_git_environment()
def attribute(study: Study, store: Store, run_dir: Path) -> dict:
    """Persist all fix evidence and links; a zero means no supplied-label link."""
    store.clear("szz")
    store.clear("szz_outcome")
    commits = store.rows("commit")
    prs = store.rows("pr")
    repos = {row["repo"]: row for row in store.rows("repo")}
    labels = json.loads(study.bug_fixes.read_text()) if study.bug_fixes else []
    cutoff = datetime.combine(date.fromisoformat(study.observed_until), time.max, timezone.utc)
    window = timedelta(days=study.followup_days)
    sampled = {(row["repo"], row["sha"]): row for row in commits}
    states = {}
    histories = {}
    history_errors = {}
    mainlines = {}
    failed_repos = set()
    fix_records = []
    limits = [
        "Only supplied validated fix labels are observed; no candidate attribution is not verified absence of defects.",
        "Single-parent deleted-line SZZ uses PyDriller whitespace-ignoring diffs and git blame -w; whitespace-only fixes are not attributed.",
        "PyDriller filters blank/comment-prefixed deleted lines; addition-only and binary changes cannot establish an introduction.",
        "Rename/deletion blame uses the parent old path; complex renames and moved/copied code may fail or misattribute.",
        "Root and merge commits are excluded; PR SHA is never substituted for a squash/rebase integration SHA.",
        "Historical mainline is the first-parent tip at the committed-time observation cutoff, not all fetched PR refs.",
    ]
    try:
        for repo in study.repositories:
            row = repos.get(repo)
            if not row:
                history_errors[repo] = "repository_not_mined"
                continue
            try:
                git = _SZZGit(str(row.get("path") or run_dir / "repos" / repo))
                histories[repo] = git
                git._conf.set_value("skip_whitespaces", True)
                head = git.get_commit(row.get("mainline_sha") or "HEAD")
                tip = next((commit for commit in git.get_list_commits(head.hash, first_parent=True, reverse=False)
                            if _commit_time(commit) <= cutoff), None)
                mainlines[repo] = {"head_sha": head.hash, "observed_tip_sha": tip.hash if tip else None}
            except Exception as exc:
                history_errors[repo] = str(exc)

        for repo, sha in sampled:
            observed = {
                "repo": repo, "sha": sha, "eligible": False, "candidate_bug": None,
                "followup_days": study.followup_days, "observed_until": study.observed_until,
                "fix_links": [], "evidence_coverage": "supplied_validated_fixes_only" if labels else "no_validated_fixes",
            }
            states[(repo, sha)] = observed
            git = histories.get(repo)
            if repo in history_errors or not git:
                observed["reason"] = "repository_unavailable"
                observed["error"] = history_errors.get(repo)
                continue
            try:
                candidate = git.get_commit(sha)
                start = _commit_time(candidate)
                observed.update(committed_at=start.isoformat(), window_end=(start + window).isoformat())
                if len(candidate.parents) != 1:
                    observed["reason"] = "root_commit_excluded" if not candidate.parents else "merge_commit_excluded"
                elif start > cutoff:
                    observed["reason"] = "candidate_after_observed_cutoff"
                elif not mainlines[repo]["observed_tip_sha"] or not git.repo.is_ancestor(sha, mainlines[repo]["observed_tip_sha"]):
                    observed["reason"], observed["integration_mapping"] = _mapping_reason(sha, [pr for pr in prs if pr["repo"] == repo], git, cutoff)
                elif start + window > cutoff:
                    observed["reason"] = "right_censored"
                else:
                    observed.update(eligible=True, reason="awaiting_fix_attribution")
            except Exception as exc:
                observed.update(reason="candidate_commit_unavailable", error=str(exc))

        for label in labels:
            repo, sha = label["repo"], label["sha"]
            record = {"repo": repo, "fix_sha": sha, "status": "unavailable", "evidence": label["evidence"],
                      "candidates": [], "candidate_links": [], "files": [], "whitespace_ignored": True}
            fix_records.append(record)
            git = histories.get(repo)
            if repo in history_errors or not git:
                record.update(reason="repository_unavailable", error=history_errors.get(repo))
                failed_repos.add(repo)
                continue
            try:
                fix = git.get_commit(sha)
                fixed_at = _commit_time(fix)
                record["committed_at"] = fixed_at.isoformat()
                if fixed_at > cutoff:
                    record.update(status="excluded", reason="fix_after_observed_cutoff")
                    continue
                tip = mainlines[repo]["observed_tip_sha"]
                if not tip or not git.repo.is_ancestor(sha, tip):
                    reason, mapping = _mapping_reason(sha, [pr for pr in prs if pr["repo"] == repo], git, cutoff)
                    record.update(status="excluded", reason=reason, integration_mapping=mapping)
                    failed_repos.add(repo)
                    continue
                if len(fix.parents) != 1:
                    record.update(status="excluded", reason="root_fix_excluded" if not fix.parents else "merge_fix_excluded")
                    failed_repos.add(repo)
                    continue
                paths = {}
                incomplete = False
                for modification in fix.modified_files:
                    item = {"path": modification.new_path, "old_path": modification.old_path,
                            "change_type": modification.change_type.name, "candidates": []}
                    record["files"].append(item)
                    try:
                        deleted = modification.diff_parsed["deleted"]
                        item["deleted_lines"] = len(deleted)
                        if not deleted:
                            item.update(status="no_candidate_attribution", reason="no_deleted_lines_after_whitespace_ignoring_diff")
                            continue
                        if not any(not git._useless_line(line.strip()) for _, line in deleted):
                            item.update(status="no_candidate_attribution", reason="only_blank_or_comment_deleted_lines")
                            continue
                        found = git.get_commits_last_modified_lines(fix, modification=modification)
                        hashes = sorted({candidate for values in found.values() for candidate in values})
                        item.update(status="complete", candidates=hashes)
                        if not hashes:
                            item["reason"] = "no_candidate_attribution_unblamable_lines"
                        for candidate_sha in hashes:
                            paths.setdefault(candidate_sha, []).append(modification.new_path or modification.old_path)
                    except Exception as exc:
                        item.update(status="unavailable", reason="deleted_line_blame_unavailable", error=str(exc))
                        incomplete = True
                record["candidates"] = sorted(paths)
                for candidate_sha, candidate_paths in sorted(paths.items()):
                    link = {"sha": candidate_sha, "paths": sorted(set(candidate_paths)), "eligible": False,
                            "fix_evidence": label["evidence"], "exclusion_reasons": []}
                    record["candidate_links"].append(link)
                    key = (repo, candidate_sha)
                    try:
                        candidate = git.get_commit(candidate_sha)
                        started_at = _commit_time(candidate)
                        link.update(committed_at=started_at.isoformat(), window_end=(started_at + window).isoformat())
                        if key not in sampled:
                            link["exclusion_reasons"].append("outside_sampled_commits")
                        if len(candidate.parents) != 1:
                            link["exclusion_reasons"].append("root_commit_excluded" if not candidate.parents else "merge_commit_excluded")
                        if not git.repo.is_ancestor(candidate_sha, tip):
                            link["exclusion_reasons"].append("not_mainline_reachable_at_observed_cutoff")
                        if not started_at < fixed_at <= started_at + window:
                            link["exclusion_reasons"].append("fix_outside_candidate_followup_window")
                        if started_at + window > cutoff:
                            link["exclusion_reasons"].append("right_censored")
                        if key in states and not states[key]["eligible"]:
                            reason = states[key]["reason"]
                            if reason not in link["exclusion_reasons"]:
                                link["exclusion_reasons"].append(reason)
                        link["eligible"] = not link["exclusion_reasons"]
                    except Exception as exc:
                        link["exclusion_reasons"].append("candidate_commit_unavailable")
                        link["error"] = str(exc)
                        incomplete = True
                    if key in states:
                        states[key]["fix_links"].append({"fix_sha": sha, "fix_committed_at": fixed_at.isoformat(),
                                                         "evidence": label["evidence"], **link})
                record["status"] = "partial" if incomplete else "complete"
                if incomplete:
                    record["reason"] = "some_deleted_line_attribution_unavailable"
                    failed_repos.add(repo)
                elif not paths:
                    record["reason"] = "no_candidate_attribution_not_verified_absence"
            except Exception as exc:
                record.update(status="unavailable", reason="fix_commit_or_diff_unavailable", error=str(exc))
                failed_repos.add(repo)

        labeled_repos = {label["repo"] for label in labels}
        for (repo, sha), outcome in states.items():
            if not labels or repo not in labeled_repos:
                if outcome["eligible"]:
                    outcome["reason"] = "missing_validated_fix_evidence_pending"
                outcome["evidence_coverage"] = "no_validated_fixes"
            elif outcome["eligible"]:
                if any(link["eligible"] for link in outcome["fix_links"]):
                    outcome.update(candidate_bug=1, reason="supplied_fix_candidate_attribution")
                elif repo in failed_repos:
                    outcome["reason"] = "incomplete_supplied_fix_attribution_pending"
                else:
                    outcome.update(candidate_bug=0, reason="no_candidate_attribution_under_supplied_fixes_not_verified_absence")
            store.put("szz_outcome", f"{repo}:{sha}", outcome)
        for record in fix_records:
            store.put("szz", f"{record['repo']}:{record['fix_sha']}", record)
        counts = Counter(record["status"] for record in fix_records)
        return {
            "status": "pending" if not labels else "partial" if failed_repos or history_errors or set(study.repositories) - labeled_repos else "complete",
            "validated_fixes": len(labels), "fix_statuses": dict(counts), "sampled_commits": len(states),
            "eligible_commits": sum(outcome["eligible"] for outcome in states.values()),
            "attributed_commits": sum(outcome["candidate_bug"] == 1 for outcome in states.values()),
            "pending_outcomes": sum(outcome["candidate_bug"] is None for outcome in states.values()),
            "evidence_coverage": "limited_to_supplied_validated_fixes" if labels else "no_validated_fixes_pending",
            "mainlines": mainlines, "repository_errors": history_errors, "algorithm_limits": limits,
        }
    finally:
        for git in histories.values():
            git.clear()
