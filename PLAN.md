# codeprov implementation and verification plan

## 1. Outcome and execution boundary

Deliver a reproducible Python research CLI that traces declared AI assistance in GitHub pull requests, measures maintainability and scanner findings, attributes candidate defect introductions with SZZ, evaluates historical review recovery, and fits defensible observational models. A fresh checkout must include executable checks and an offline demonstration. Every output must distinguish measured evidence, synthetic demonstrations, unavailable evidence, and human judgments.

The repository already contains the pipeline. Preserve it and complete its verification and usability. Do not create another application, database architecture, web interface, queue, service, or deployment. Do not replace working statistical or evidence-handling code to make the project appear new.

### Completion criteria

1. Existing stage commands remain usable with their documented argument order.
2. Committed automated checks reproduce the important methodological safeguards.
3. An offline command produces an auditable synthetic run without GitHub access or a paid model call.
4. The demo exercises real Git history, PyDriller, Radon, scanner adapters, review validation, adjudication, analysis guards, and reporting to the extent explicitly declared in its manifest.
5. Genuine Bandit/Semgrep integration checks run against small local source files. Mock scanner results remain confined to adapter/error tests.
6. The README distinguishes earlier pilot claims from checks reproduced by this implementation.
7. Missing evidence remains null/pending. Unsupported estimates remain `not_estimable`.
8. The implementation records its test commands, outcomes, and known limitations in a verification document.

## 2. Inspected baseline

Inspection date: 2026-10-15. Baseline commit: `6f3c6daabd066b898fb570d4ee59ae30cde6e07a`.

Run all project commands from the repository root.

| Existing file | Observed responsibility | Implementation direction |
| --- | --- | --- |
| `pyproject.toml`, `uv.lock` | Python 3.11–3.13 package, console entry point, locked dependencies | Retain dependency set; use stdlib unittest unless a demonstrated need appears |
| `codeprov/config.py` | TOML validation, evidence file hashes, immutable study ID | Cover invalid inputs and hash changes |
| `codeprov/cli.py` | init/status/mine/measure/szz/review/evaluate/analyze/report/run | Add a bounded offline demo entry point or script without destabilizing commands |
| `codeprov/storage.py` | SQLite JSON records and stage states | Test idempotent keys and persistence |
| `codeprov/github.py` | Same-origin pagination, HTTP evidence cache, rate-limit errors | Test using an in-process mock transport |
| `codeprov/mining.py` | GitHub PR selection, safe Git operations, PyDriller snapshots | Exercise a generated local repository and deterministic API fixtures |
| `codeprov/provenance.py` | Terminal Git trailers and independent manual evidence | Preserve three-way labels and conflict behavior |
| `codeprov/metrics.py` | Before/after Radon and scanner deltas | Verify source semantics and missing-data propagation |
| `codeprov/security.py` | Isolated scanner execution, normalized findings, multiset deltas | Exercise real scanners plus timeout/error fixtures |
| `codeprov/szz.py` | Validated-fix attribution, mainline reachability, follow-up windows | Exercise known local introduction/fix history |
| `codeprov/review.py` | Original-snapshot prompts, HTTP client, strict finding validation | Verify leakage controls and response failures |
| `codeprov/evaluation.py` | Human issue grouping, one-to-one matching, complete coverage | Test known counts and rejection paths |
| `codeprov/analysis.py` | Continuous and Bayesian binary mixed models, eligibility gates | Test controlled synthetic fits and refusal cases |
| `codeprov/report.py` | Markdown report and machine-readable evidence | Verify statuses and exclusions remain visible |
| `study.toml`, `study.pilot.toml` | Research and smoke configurations | Preserve public-study definitions |
| `rules/python-security.yml` | Local Semgrep rules | Reuse frozen rules in real scanner checks |
| `README.md` | Workflow and prior pilot narrative | Add reproducible local verification and accurate demo boundaries |

The inspected source contains roughly 2,471 Python lines. No committed `tests/` suite or offline demo was visible. The README describes earlier throwaway checks and a public pilot. Those statements are historical claims until their artifacts or reruns substantiate them. The implementation must not present them as newly verified results.

One concrete consistency issue deserves a regression test: configuration accepts reviewer efforts `low`, `medium`, and `high`, while `review()` requires `low` and constructs `reasoning_effort="low"`. Preserve the existing low-only research protocol and reject unsupported effort at configuration loading, unless an explicit project requirement supports a broader change. Update the error and documentation together.

## 3. Research contract

### Questions and units

* Q1: How do before/after maintainability changes differ between declared AI and independently verified manual commits?
* Q2: How do introduced scanner findings and supplied-fix SZZ candidate attribution differ between these groups?
* Q3: How much of the historical human review issue set does the reviewer recover on the same historical code snapshots?
* Q4: What associations remain after pre-specified controls and repository grouping?

Use commit-level observations for quality and candidate-defect outcomes. Keep file-level measurements as underlying evidence. Use historical review snapshots for finding/comment matching, then aggregate counts to PRs. A commit appearing in multiple PRs remains one quality observation. A mixed-provenance PR cannot receive a majority-vote authorship label.

### Provenance

The allowed cohorts are `declared_ai`, `verified_manual`, and `unknown`. Parse terminal `Co-Authored-By` trailers with Git's trailer parser. Match configured assistant email identities exactly after case normalization. A name, body mention, ordinary human coauthor, or missing AI trailer cannot establish manual authorship. Retain raw coauthor values, matched identities, independent evidence, and conflicts.

A conflicting AI declaration and manual label becomes explicit uncertain evidence and stays out of either primary cohort. Primary comparison uses verified manual evidence. The unknown comparator is a separately labeled sensitivity analysis. Neither comparison proves causation. Trailer practices, assistant identities, and voluntary disclosure can vary across repositories and time.

### Maintainability and security

Measure historical before/after Python source. Keep MI, complexity, LOC, parser failures, and file change type. Define MI change as after minus before. Preserve missing values for additions/deletions where a paired metric does not exist. Record exactly how file-level results become commit-level outcomes.

Count each function definition once when aggregating complexity. Avoid double-counting class totals and their methods. Scanner outputs represent static-analysis findings, not independently confirmed vulnerabilities. Track introduced/resolved findings using normalized semantic identities and multiplicities. A line-number shift alone must not create a new finding. A failed scanner must not become a clean scan.

### SZZ

Accept explicit validated bug-fix records with repository, full commit SHA, and a nonempty evidence description. Preserve each supplied label and attribution result. Restrict eligible candidate/fix histories to the recorded historical mainline and observation cutoff. Require the same configured follow-up length for primary outcomes. Record right-censored candidates separately.

Use PyDriller deleted-line blame against single-parent fixes. Retain fix-to-candidate edges and per-file reasons. Exclude root and merge ambiguity. Preserve unresolved squash/rebase mappings instead of silently assigning a PR commit to an integration SHA. Addition-only fixes, comments, whitespace changes, renames, copied code, and refactoring can limit or bias attribution.

`candidate_bug=0` means no candidate link under the supplied eligible fix set. It does not establish defect-free code. Without validated fix evidence, use null and pending. Synthetic demo labels describe synthetic commits only.

### Historical review evaluation

Review the code at each eligible comment's original commit ID. Use the documented historical base/merge-base reconstruction and store its SHAs. Do not include human comments, PR conclusions, later fixes, provenance labels, or gold issue IDs in the reviewer prompt. A current PR head is not a replacement for a missing historical snapshot.

Keep configured model, effort, system prompt, exact request hash, response hash, and snapshot ID. Oversized/unavailable snapshots are explicit skips. Validate returned paths and new-side line numbers against the exported snapshot. Treat empty valid findings as a completed review. An export is not a review response.

Historical comments provide incomplete ground truth. Human adjudication must group duplicate comments into issues, ignore non-issue remarks, group duplicate model findings, judge whether every finding group is genuine, and match canonical issues one-to-one. String overlap and line proximity cannot establish semantic equivalence.

Report separately:

* Historical recovery precision: matched issues / all finding issue groups.
* Historical recovery recall: matched issues / all historical issue groups.
* Adjudicated precision: genuine finding issue groups / all finding issue groups.
* Genuine unmatched findings: valid discoveries missing from historical review.

Use null for zero denominators. Keep development/test allocation frozen by repository. Report held-out estimates separately. Synthetic annotations may validate arithmetic but must never be described as empirical human adjudication.

### Statistical analysis

Retain the current Gaussian `MixedLM` for continuous changes and `BinomialBayesMixedGLM` for binary outcomes. Preserve repository random intercepts. Controls currently include declared-AI indicator, log1p churn, prior complexity, and log1p author experience. Record standardized control means/scales and excluded missing rows.

Existing computational gates require at least 20 complete rows, two repositories, and five observations in each cohort. Binary models also require events/non-events and no empty cohort-by-outcome cell. These thresholds are computational checks; they are not power calculations or proof of adequate cluster counts.

Check finite inputs, fixed-effect rank, variation, convergence, and finite uncertainty. Report Bayesian approximate credible intervals distinctly from Gaussian Wald confidence intervals. Preserve optimizer warnings and repository variance diagnostics. Inspect cohort overlap by repository so a reader can see whether provenance aligns with repository membership. An additional overlap diagnostic is a follow-up research enhancement, not an acceptance requirement for this implementation. Do not silently imply within-repository identification when all exposure variation is between repositories.

Treat empirical-logit PR recovery models as exploratory. Report their continuity correction and unweighted aggregation. Avoid causal language, automatic significance claims, and unsupported generalization across languages. Plan a larger, independently sampled study before publishing empirical conclusions.

## 4. Data and artifact contracts

Preserve the current SQLite schema:

```sql
CREATE TABLE records (
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (kind, key)
);
CREATE TABLE stages (
    name TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    details TEXT NOT NULL
);
```

Do not add relational migrations solely for documentation. Use existing JSON record kinds. Stable identities must include repository scope.

| Record/artifact | Required identity and evidence |
| --- | --- |
| `repo` | owner/name, local history path, mainline SHA |
| `sample` | repository, sampling interval, cap and completeness diagnostics |
| `pr` | repository + number, exact commit membership, historical base/integration evidence |
| `commit` | repository + SHA, parents, dates, provenance evidence, churn, experience, source snapshots |
| `comment` | repository + comment ID, PR, original commit, path/line, user/bot evidence, body |
| `measurement` | repository + commit + path, before/after values, scanner statuses and artifacts |
| `szz` | repository + fix SHA, label evidence, per-file candidates and exclusions |
| `szz_outcome` | repository + candidate SHA, eligibility, follow-up, nullable outcome, reasons |
| `review_request` | snapshot ID, SHA/base, split, payload path/hash, status |
| `review_response` | snapshot ID, actual response body/hash and HTTP status |
| `review_finding` | snapshot ID + finding index, validated location/category/message |
| `evaluation` | repository + PR + split, counts, rates, status |
| `analysis` | outcome + comparator, controls, eligibility, coefficients or refusal reason |

Freeze study configuration, scanner rules, and independent evidence file bytes. Retain the current run manifest and runtime versions. Keep JSON strict: no NaN, infinity, or manufactured sentinel values. The demo must add explicit `synthetic: true` metadata in its own summary/manifest and prominently mark its report. Avoid changing the canonical study manifest contract unless necessary.

## 5. Minimal file changes

Expected additions:

* `tests/test_pipeline.py`: compact stdlib unittest suite grouped by scientific boundary; split into two files only if readability requires it.
* `tests/helpers.py` or a helper in the test module: deterministic local Git history and reusable fixture records.
* `codeprov/demo.py` or `scripts/demo.py`: one offline demonstration entry point. Prefer the module if `codeprov demo` can be added with minimal CLI branching.
* `VERIFICATION.md`: commands, environment, reproduced outcomes, demo limitations, and unresolved issues.

Expected modifications:

* `codeprov/cli.py` only if exposing a `demo` command.
* `codeprov/config.py` for the confirmed effort validation inconsistency.
* Existing modules only for test-proven defects or compact evidence diagnostics.
* `README.md` for offline/demo/test instructions and accurate verification claims.
* `.gitignore` only if the demo needs a new local output path beyond ignored `runs/`.

Do not commit virtual environments, cloned histories, raw provider responses, tokens, generated databases, or scanner output. Keep committed fixtures small and synthetic. Do not add CI merely to add another deliverable; a local reproducible check is sufficient for this completion scope.

## 6. Implementation sequence and gates

### Phase A — Reproduce the baseline

1. Confirm project Git status and baseline revision.
2. Inspect Python/tool availability and the locked environment.
3. Run `uv run --locked codeprov --help` and import smoke checks.
4. Read relevant source before constructing fixtures; match existing record contracts.
5. Add the effort-validation regression test and document its initial failure.

Gate: existing CLI imports and help work; one concrete inconsistency is reproducibly captured. If dependencies need installation, launch `uv sync --locked --python 3.12` as a tracked background job. Keep independent documentation/test design work moving while it runs.

### Phase B — Commit meaningful invariant checks

Use `unittest`, `tempfile`, `unittest.mock`, `http.server` or `httpx.MockTransport`, and ordinary Git subprocesses. Avoid a new test framework. Give subprocesses explicit roots and timeouts. Use fixed author/committer identities and dates for generated commits. Disable global Git config/hooks in fixture operations where appropriate.

Test matrix:

| Area | Minimum cases | Success condition |
| --- | --- | --- |
| Configuration | valid config, bad dates, duplicate repo, empty evidence, changed evidence hash, unsupported effort | Invalid studies fail before network work; changed inputs change run identity |
| Provenance | exact assistant trailer, name-only/body mention, human trailer, no trailer, manual evidence, conflict | Only supported evidence selects a known cohort |
| Storage | repeat put, reopen, clear kind | Stable keys prevent duplicates and unrelated kinds survive |
| GitHub | multi-page list, repeated pagination, external next URL, 429, cached successful page | No credential forwarding or silent incomplete success |
| Metrics | known small Python function, nested definition, parse error, addition/deletion | Correct relative changes and explicit missingness |
| Scanner normalization | shifted lines, duplicates, introduced/resolved issue, invalid path, failed process | Findings remain distinct from tool failures |
| Real scanners | small unsafe and clean local source snapshots | Bandit and Semgrep actually execute; recorded output parses |
| SZZ | known introduction/fix, no labels, censored candidate, merge/root exclusions | Expected edge exists and unknown outcomes stay null |
| Review payload | original snapshot, later fix, malicious code comment, oversized payload | Payload remains historical and gold-free; skip state is explicit |
| Review validation | empty findings, valid finding, invalid path/line, duplicate JSON keys | Only valid structured responses become reviewed |
| Adjudication | one TP/FP/FN example, genuine unmatched, duplicates, incomplete coverage | Exact expected counts; invalid labels never leave complete results |
| Models | no rows, one cohort/repo, rank failure, separated binary data, seeded positive fit | Refusal cases explain themselves; valid synthetic data yields finite diagnostics |
| Reporting | partial/pending evidence, zero denominator, synthetic marker | Reader can distinguish unavailable research outputs from zero measurements |

Prefer behavioral assertions over snapshots of incidental formatting. A scanner dependency missing in a required integration check is a reported failure, not a silently skipped success. Keep a small fast subset available if full scanner checks take longer.

Gate: committed tests reproduce the central claims. Fix discovered root causes minimally, then rerun their scoped checks before the full suite.

### Phase C — Offline end-to-end demonstration

Build a deterministic disposable local repository with a base commit, a declared-AI introduction, a verified-manual change, an unknown-provenance change, and a later validated fix. Choose source that makes the expected Radon change and scanner finding understandable. Include one historical inline review comment tied to the pre-fix SHA. Fix timestamps and expose the selected SHAs in the demo summary.

Use the existing source extraction and measurement functions. Prefer a small deterministic GitHub transport fixture that feeds the real mining orchestration. If that requires invasive changes, use the existing local `_commit` extraction and seed the minimal PR/comment metadata directly. Explicitly state that this alternative bypasses GitHub discovery; cover discovery separately in mocked API tests.

Run real Radon/Bandit/Semgrep and real local SZZ. Export the historical review request. Exercise the real review HTTP client with a local deterministic endpoint or scoped mock transport that returns a disclosed fixture response. Mark this response synthetic and avoid describing it as model inference. Never read or require a real API key for the demo. Isolate the environment so a user's existing token is not sent anywhere.

Provide synthetic adjudications that cover all fixture findings/comments. Evaluate them through the production validation path. Run analysis and report. The small demonstration should naturally produce `not_estimable`; a separate seeded statistical test validates successful fitting. Do not inflate demo data to suggest a research sample.

Write `demo-summary.json` with `synthetic: true`, exercised components, bypassed components, expected/actual counts, stage states, and report/evidence paths. Mark the generated Markdown report as synthetic at its beginning. Use a fresh output directory or explicit idempotent rerun behavior. Never overwrite a real study run.

Gate: one documented offline command finishes without external HTTP or credentials, generates inspectable artifacts, and checks its expected evidence. Rerunning it succeeds without duplicate records.

### Phase D — Documentation and verification

1. Add the exact offline command and test command to the README near quickstart.
2. Explain the demo's real components, mocked provider, and synthetic labels.
3. Keep public mining as an optional network workflow.
4. Correct low-only reviewer effort validation/documentation consistently.
5. Write `VERIFICATION.md` with executed commands and results. Distinguish old pilot evidence from current checks.
6. When the optional code-health CLI is installed, run `code-health check --fast --changed` after meaningful code changes and `code-health check --deep --changed` before completion. Fix relevant high-confidence findings and rerun affected checks. Explain remaining design findings without speculative rewrites.
7. Run the full committed suite and the offline demo after final source changes.
8. Inspect `git diff --check`, status, and artifact paths. Confirm no secret/generated run data enters the diff.

Gate: a reviewer can reproduce all newly claimed outcomes using committed code. No real empirical precision, recall, or model effects are claimed without a real study and independent annotation.

## 7. Concrete command contract

Run these commands from the project root. The existing commands are baseline contracts; the demo command is a planned addition.

```sh
uv sync --locked --python 3.12
uv run --locked codeprov --help
uv run --locked python -m unittest discover -s tests -v
uv run --locked codeprov --results runs/offline-demo demo
```

If a standalone module creates a smaller safe change, use this documented equivalent instead of adding CLI architecture:

```sh
uv run --locked python -m codeprov.demo --output runs/offline-demo
```

Choose exactly one public demo command and document it consistently. The demo must not require `study.toml` loading before it can create its own synthetic config.

Existing real-study workflow:

```sh
uv run --locked codeprov --study study.pilot.toml init
uv run --locked codeprov --study study.pilot.toml run --export-only
uv run --locked codeprov --study study.pilot.toml status
```

Live review requires explicit provider configuration and a credential in the environment. Human evaluation requires a completed worksheet. These are separate research operations, not prerequisites for offline verification.

```sh
uv run --locked codeprov --study study.pilot.toml review
uv run --locked codeprov --study study.pilot.toml evaluate --adjudications adjudications.json
uv run --locked codeprov --study study.pilot.toml analyze
uv run --locked codeprov --study study.pilot.toml report
```

Run commands that may exceed 30 seconds through the available tracked background facility. Capture progress and inspect native job status. Do not use polling shell loops. Keep working-tree changes away from a running test's inputs.

## 8. Error handling and trust boundaries

* GitHub pagination must remain on the API origin and detect loops.
* GitHub sampling caps and API limits must remain visible in the sample evidence.
* Mined repositories must never execute hooks, install packages, or run their tests.
* Scanner inputs must stay under a fresh temporary root; reject traversal and symlinks.
* Tool timeouts, nonzero error exits, malformed JSON, and partial scanner output must remain distinguishable.
* Repository code and model output are untrusted data. Prompt text cannot guarantee model behavior, so location/schema validation remains mandatory.
* Downstream results must not survive changed upstream review evidence or invalid adjudication.
* SQLite and JSON outputs must preserve null outcomes and stage statuses.
* Failed stages must expose useful errors without printing authorization headers or environment secrets.
* Tests must use disposable directories. Never mutate the public pilot cache to make a demo pass.

## 9. Review checklist and reporting limits

Before implementation handoff, review the diff against the baseline and answer:

1. Can a missing AI trailer still become verified manual without independent evidence? It must not.
2. Can a scanner failure become zero findings? It must not.
3. Can a missing fix label become a defect-free outcome? It must not.
4. Can a current head or later fix leak into a historical reviewer prompt? It must not.
5. Can exported requests or fixture responses be reported as real model review evidence? They must not.
6. Can incomplete adjudications retain old complete scores? They must not.
7. Can unsupported model data yield a fabricated coefficient? It must not.
8. Can a synthetic demo appear among public study results without a synthetic marker? It must not.
9. Does each added abstraction solve a demonstrated need? Remove it otherwise.

Report finished functionality, exact validation, and material remaining limits. A functioning pipeline is a completed software outcome. A representative research dataset, independently adjudicated holdout set, and publishable empirical findings require a separate study campaign. Do not manufacture those inputs to make every report section show a number.

## 10. Primary documentation checked

The implementation should consult the installed dependency interfaces before changing API calls. These primary references informed the plan:

* [PyDriller API reference](https://pydriller.readthedocs.io/en/latest/reference.html): `get_commits_last_modified_lines` returns per-path candidate commit sets. Preserve underlying uncertainty and inspect pinned behavior before changing the private blame adapter.
* [GitHub pull request review comments](https://docs.github.com/en/rest/pulls/comments): inline comments carry snapshot and location metadata. Preserve raw fields for reconstruction.
* [GitHub pull request reviews](https://docs.github.com/en/rest/pulls/reviews): review-level records and inline review comments represent distinct evidence.
* [statsmodels MixedLM API](https://www.statsmodels.org/stable/generated/statsmodels.regression.mixed_linear_model.MixedLM.from_formula.html): grouping and variance-component specification determine the dependence structure. Keep repository grouping explicit.

Dependency documentation describes APIs. It does not validate this study's causal identification, provenance reliability, annotation quality, or sample representativeness.
