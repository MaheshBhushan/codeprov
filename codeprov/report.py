"""Human-readable audit report backed entirely by persisted evidence."""
import json
from collections import Counter
from pathlib import Path


def _cell(value):
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _table(headers, rows):
    return "\n".join(["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |",
                       *("| " + " | ".join(_cell(cell) for cell in row) + " |" for row in rows)])


def report(study, store, run_dir: Path) -> dict:
    manifest = json.loads((run_dir / "manifest.json").read_text())
    commits = store.rows("commit")
    prs = store.rows("pr")
    measurements = store.rows("measurement")
    outcomes = store.rows("szz_outcome")
    requests = store.rows("review_request")
    evaluations = store.rows("evaluation")
    models = store.rows("analysis")
    sections = ["# codeprov research report", f"Run `{manifest['run_id']}`; created {manifest['created_at']}.",
                "## Study selection", _table(["Setting", "Value"], [
                    ("Repositories", ", ".join(study.repositories)), ("PR creation window", f"{study.since} to {study.until} inclusive"),
                    ("History observation cutoff", study.observed_until), ("Equal SZZ follow-up", f"{study.followup_days} days"),
                    ("Maximum merged PRs per repository", study.max_prs),
                    ("Development repositories", ", ".join(study.review.get('development_repositories', []))),
                    ("Review model / effort", f"{study.review['model']} / {study.review.get('effort', 'low')}"),
                ]), "## Evidence and execution", _table(["Stage", "Status", "Details"], [
                    (s["name"], s["status"], json.dumps({key: value for key, value in s["details"].items()
                        if type(value) in (int, float) or key in ("reason", "error")}, sort_keys=True))
                    for s in store.stages() if s["name"] != "report"
                ]), _table(["Artifact", "Count"], [("PRs", len(prs)), ("PR commits (unique per repository)", len(commits)),
                    ("Python file measurements", len(measurements)), ("SZZ outcomes", len(outcomes)),
                    ("Historical review requests", len(requests)), ("Adjudicated PR evaluations", len(evaluations))]),
                "## Provenance", _table(["Label", "Commits"], sorted(Counter(c['provenance'] for c in commits).items())),
                "`declared_ai` means a matched assistant co-author trailer. `verified_manual` requires independent evidence. "
                "`unknown` means no qualifying declaration; it does not establish human authorship. Conflicts are excluded as unknown.",
                "## Scanner coverage", _table(["Tool", "Status", "Files"], [
                    (tool, status, count) for tool in ("bandit", "semgrep")
                    for status, count in sorted(Counter(m.get(tool, {}).get('status', 'unavailable') for m in measurements).items())
                ]), "Findings are scanner warnings, not confirmed vulnerabilities. Introduced/resolved findings are matched across parent/head snapshots. "
                "A failed scan is missing data, never a clean scan.",
                "## SZZ evidence", _table(["Eligibility", "Outcome", "Commits"], [
                    (eligible, value, count) for (eligible, value), count in sorted(
                        Counter((str(o.get('eligible')), str(o.get('candidate_bug'))) for o in outcomes).items())
                ]), "SZZ links are candidate attributions, not verified causation. Outcomes are restricted to equal completed observation windows. "
                "No validated fix list means unknown outcomes. A zero means no SZZ candidate in the supplied fix evidence, not proven defect freedom.",
                "## Historical review evaluation", _table(["Request status", "Snapshots"], sorted(Counter(r['status'] for r in requests).items())),
                _table(["Repository", "PR", "Split", "TP", "FP", "FN", "Recovery precision", "Recovery recall", "Adjudicated precision"], [
                    (e['repo'], e['pr_number'], e['split'], e['tp'], e['fp'], e['fn'], e.get('precision'), e.get('recall'), e.get('adjudicated_precision'))
                    for e in evaluations]),
                "Recovery metrics use deduplicated historical human issues and one-to-one adjudicated matches. "
                "Adjudicated precision additionally credits independently valid findings absent from historical comments. "
                "Human comments are incomplete ground truth; export-only requests are not completed reviews. Empty denominators remain undefined.",
                "## Mixed-effects models", _table(["Outcome", "Comparison baseline", "Status", "N", "Reason"], [
                    (m['outcome'], m['baseline'], m['status'], m.get('n', 0), m.get('reason', '')) for m in models
                ])]
    for model in models:
        if model["status"] != "complete":
            continue
        sections.extend([f"### {model['id']}", f"{model['model']}; {model['method']}. {model['interval_type']}.",
                         _table(["Term", "Estimate", "SD", "Lower", "Upper"], [
                             (c['term'], c['estimate'], c['sd'], c['lower'], c['upper']) for c in model['coefficients']]),
                         f"Repository SD: {_cell(model['repository_sd'])}. Numeric controls standardized; transforms in `analysis.json`."])
        if model.get("warnings"):
            sections.append("Diagnostics: " + "; ".join(model["warnings"]))
        if model.get("interpretation"):
            sections.append(model["interpretation"])
    sections.extend(["## Reproduction", "Use the frozen input copies, version manifest and `uv.lock`. "
                     "Raw API responses, scanner outputs and review payloads remain alongside `study.sqlite`. "
                     "`commit-analysis.csv`, `review-analysis.csv` and `analysis.json` preserve model inputs and diagnostics.",
                     _table(["Dependency", "Version"], sorted(manifest['versions'].items())),
                     "## Interpretation limits", "These are observational associations, not causal effects. "
                     "Declared assistance is a noisy, selectively disclosed proxy. PR sampling, squash histories, missing review-time snapshots, "
                     "incomplete bug-fix labels and unavailable annotations constrain generalization. Scanner rules are a fixed subset of security checks. "
                     "SZZ cannot attribute fixes that only add lines and may misattribute refactors. "
                     "Per-PR empirical-logit review models are exploratory, not binomial likelihood models. "
                     "Multiple outcomes/comparisons are reported together; do not select only significant results."])
    output = run_dir / "report.md"
    output.write_text("\n\n".join(sections) + "\n")
    # Export audit rows without inventing columns or dropping nested source evidence.
    evidence = run_dir / "evidence.json"
    evidence.write_text(json.dumps({kind: store.rows(kind) for kind in (
        "repo", "sample", "pr", "commit", "comment", "issue", "issue_link", "measurement", "szz", "szz_outcome",
        "review_request", "review_response", "review_finding", "evaluation", "analysis"
    )}, indent=2, allow_nan=False) + "\n")
    return {"status": "complete", "report": str(output), "evidence": str(evidence)}
