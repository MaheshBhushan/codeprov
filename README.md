# codeprov

A local Python research CLI for declared AI-assisted code provenance, maintainability, static security findings, candidate defect attribution, and historical LLM review evaluation.

**No trailer is not proof of human authorship.** The primary observable is declared assistance. Human-written comparisons require independently documented manual labels. Results are observational associations, not causal effects.

## Install and run

```sh
uv sync --python 3.12
uv run codeprov --study study.pilot.toml init
uv run codeprov --study study.pilot.toml run --export-only
uv run codeprov --study study.pilot.toml status
```

`study.pilot.toml` limits the public pilot to two merged PRs created on 2026-08-31 in `pallets/click`, selected to exercise historical inline-review extraction. This is a smoke cohort, not a representative research sample; its 180-day defect windows are incomplete at the configured observation cutoff. `study.toml` selects three Python repositories over an older window. Public GitHub requests can work without credentials but unauthenticated rate limits are small. Set `GITHUB_TOKEN` for larger samples. Do not put secrets in configuration, manifests, or adjudications.

The CLI creates `runs/<study-hash>/manifest.json` and `study.sqlite`, with raw evidence and reports alongside them. Selection, rule content, evidence-file content, review settings and dependency versions are frozen. Changed inputs create a different study hash; changed runtime versions require a different `--results` root. Exact installed dependencies are in `uv.lock`.

Commands can be run independently and resumed:

```sh
uv run codeprov mine
uv run codeprov measure
uv run codeprov szz
uv run codeprov review --export-only
uv run codeprov review
uv run codeprov evaluate --adjudications adjudications.json
uv run codeprov analyze
uv run codeprov report
```

Global `--study` and `--results` options precede the command. `run --export-only` exports genuine historical review requests without fabricating findings. `run --adjudications FILE` also evaluates human annotations. Missing adjudications remain pending. A stage failure is recorded and stops the command; inspect `status` and evidence before resuming. New completed reviews or changed adjudications invalidate derived scores and reports; rerun `analyze` and `report` after evaluation.

## Study configuration

The `[study]` section specifies owner/repo names, inclusive PR creation dates, an observation cutoff, fixed SZZ follow-up days, a per-repository PR cap, assistant email identities and a local Semgrep rule file. Sampling is restricted to merged PRs, not all project commits. See the mining evidence for actual coverage and exclusions. PR commit SHAs and integration/merge SHAs are separate records.

- `declared_ai`: qualifying trailing `Co-Authored-By` email matches the frozen identity list.
- `verified_manual`: independent evidence supports the full commit, with no conflicting AI declaration.
- `unknown`: absent qualifying trailer or conflicting evidence. Generic human co-authors do not change this.

Optional `manual_labels` and `bug_fixes` paths point to JSON arrays:

```json
[
  {
    "repo": "owner/repository",
    "sha": "0123456789abcdef0123456789abcdef01234567",
    "evidence": "URL or documented independent adjudication supporting this label"
  }
]
```

The SHA above illustrates the format, not a real label. Use actual full commit SHAs from the configured repositories. Bug-fix keywords alone are not validated evidence. Keep an auditable label protocol and document coverage; an incomplete list cannot prove defect freedom.

## Measurements and safety

- **radon:** maintainability index, source LOC and summed function/method cyclomatic complexity. Class aggregate complexity is not counted again. Parent/head deltas are undefined when a side is absent; root/merge exclusions are explicit.
- **Bandit and Semgrep:** changed Python sources are materialized in isolated temporary directories and scanned without executing target code or installing its dependencies. The included local Semgrep rules check dynamic evaluation and shell execution; they are a fixed, limited rule set, not exhaustive security coverage.
- **Finding deltas:** before/after findings are matched by rule, path and normalized offending code rather than line number alone. Duplicate findings retain multiplicity. Scan failure is missing data, never zero findings. Raw scanner outputs and errors are retained.
- **SZZ:** PyDriller identifies commits that last modified deleted bug-fix lines. Only validated fixes and sufficiently observed, reachable candidate histories contribute. Squash integration ambiguity, unavailable objects and censored windows remain explicit. Attribution is a candidate link, not proven causation. No bug-fix evidence produces pending/null outcomes.

A mined repository is untrusted input. The pipeline does not run repository scripts or hooks. Static scanner parsing still runs native tools under the current user's permissions; use OS/container isolation for adversarial repositories. LLM review requests send selected source code to the configured provider; only mine code you are allowed to transmit.

## Historical LLM review

Set `[review].endpoint` to an OpenAI-compatible chat-completions endpoint and `[review].model` to a model identifier supported by that provider. The example uses the requested `gpt-6.1-sol` identifier with `effort = "low"`; provider availability is not implied. Supply `OPENAI_API_KEY` to call the provider. HTTPS is required except for loopback endpoints.

Review snapshots use the original human-comment commit SHA and a merge-base diff. The request excludes human comments, later fixes, PR descriptions, and provenance labels. The system prompt treats repository code as untrusted data. Oversize or unavailable snapshots are reported rather than silently truncated. Saved requests and prompt hashes support auditing. Export-only requests do not count as completed reviews.

Findings must use a changed Python file and a valid new-side diff line. Structured output contains `findings`, each with `path`, `line`, `category` and `message`. The provider cannot execute tools or repository code.

### Human adjudication

After actual reviews, the reviewer exports `review/adjudication-worksheet.json`. Fill its `adjudications` object and save that object alone as the evaluation input; the surrounding worksheet contains human-only evidence. Do not ask another LLM to invent semantic ground truth. Freeze `development_repositories` before evaluation; other selected repositories are held out as `test`.

The JSON contract has `version: 1` and a `snapshots` array. Every reviewed snapshot requires:

- `snapshot_id` and its frozen `split`.
- `comments`: every eligible human comment, with `comment_id`, `disposition` (`issue` or `ignore`), `canonical_comment_id`, and a rationale. Group duplicate comments under one canonical issue; explicitly ignore non-actionable remarks.
- `finding_groups`: every actual finding assigned to one issue group, with `canonical_finding_id`, `finding_ids`, a rationale, and human-assessed `genuine` boolean.
- `matches`: one-to-one issue matches, each with canonical `finding_id`, canonical `comment_id`, and a rationale.

Bot and non-Python comments are excluded. Replies are not independently counted without human issue grouping. Unknown IDs, incomplete coverage, duplicate matches and cross-snapshot matches are rejected. A blank worksheet is pending annotation, not a negative label.

Recovery precision is `TP/(TP+FP)` against historical issues; recovery recall is `TP/(TP+FN)`. Adjudicated precision separately credits independently valid findings absent from historical human comments. Undefined denominators are null, not zero. Scores aggregate per PR and development/test split; duplicate snapshots/issues must be adjudicated within the documented snapshot unit.

## Analysis and artifacts

`analyze` exports commit- and PR-level inputs and fits repository-intercept mixed-effects models:

- Gaussian `MixedLM` for maintainability and complexity deltas.
- Bayesian variational `BinomialBayesMixedGLM` for presence of introduced scanner findings and SZZ candidate outcomes.
- Exploratory Gaussian models of per-PR empirical-logit recovery precision/recall on held-out PRs only; a 0.5 continuity correction handles observed boundary proportions.

Commit models control change size, prior changed-file complexity and author experience (the count of reachable ancestor commits with the exact author email). Continuous controls are standardized; transforms are recorded. Declared-AI versus verified-manual and declared-AI versus unknown are separate comparisons. Conflicting provenance evidence and mixed-provenance PRs are excluded from provenance models.

Each fit requires at least 20 complete observations, two repositories and five observations per cohort. Binary outcomes also require five events/non-events and nonseparated cohort cells. Missing data, rank deficiency and failed convergence produce `not_estimable` with a reason. Bayesian credible intervals and frequentist confidence intervals are labeled separately. Review empirical-logit models are exploratory, not binomial likelihood models.

Important outputs:

| Artifact | Purpose |
| --- | --- |
| `manifest.json` | Frozen inputs and runtime versions |
| `study.sqlite` | Idempotent JSON evidence records and stage status |
| Raw API/scanner/review files | Source evidence, errors and exact requests |
| `commit-analysis.csv` | Commit model inputs and missingness |
| `review-analysis.csv` | PR review model inputs |
| `analysis.json` | Coefficients, uncertainty and fit diagnostics |
| `report.md` | Readable results, coverage and limitations |
| `evidence.json` | Audit export of normalized records |

Historical comments are incomplete ground truth. Trailer disclosure is selective. SZZ is affected by refactors, squash histories and incomplete fix labels. Scanner findings are not confirmed vulnerabilities. Review comments and SZZ candidate links are not independent causal evidence. Do not select only significant outcomes from the reported comparisons.

## Verification performed

The frozen public smoke run `d3ddbd7a02a8fd33` mined two merged Click PRs, two commits and six inline comments; three Python file measurements completed both scanners, and one historical review request was exported. Repeat mining preserved exact records and PR memberships. Its report is `runs/d3ddbd7a02a8fd33/report.md`.

Isolated throwaway scenarios exercised:

- Actual Bandit/Semgrep introduced and resolved findings, line shifts, duplicate finding multiplicity, invalid rules, scanner deadlines, unsafe paths, and unavailable existing source.
- Radon conditional complexity and method-versus-class aggregation.
- PyDriller SZZ known introductions, renamed fixes, whitespace-only changes, incomplete observation windows, out-of-window fixes, unresolved squash mappings, and missing validated labels.
- Provenance terminal trailers, ordinary human co-authors, absent declarations, independently verified manual evidence, and conflicting labels.
- Review payload isolation, strict response locations/types, byte-identical export/API requests, cached completed reviews, human issue deduplication, one-to-one matching, zero denominators, and stale-result invalidation.
- Gaussian and binary mixed-effects fitting on controlled synthetic data; missing comparison cohorts remained not estimable.

The HTTP review client was exercised against a local deterministic provider, not a live LLM. Synthetic fixture data was never inserted into the public research run. Live model reviews need `OPENAI_API_KEY`, valid provider/model settings, and human adjudications; defect analysis needs validated fix evidence and completed observation windows. The smoke cohort reports no estimated research effects.
