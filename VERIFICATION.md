# Verification

Checks ran on 2026-10-15 from the `codeprov` repository. The baseline was `6f3c6daabd066b898fb570d4ee59ae30cde6e07a`. The implementation preserves the existing pipeline and adds an offline demonstration and stdlib unittest regressions.

## Environment

Linux; Python 3.12.13; Git 2.54.0; uv 0.11.24. The existing locked environment supplied PyDriller 2.12, Radon 6.0.1, Bandit 1.9.4, Semgrep 1.180.0, statsmodels 0.15.0, NumPy 2.5.4, pandas 2.3.3, and httpx 0.28.1. No dependencies were added. A fresh checkout can provision the environment with `uv sync --locked --python 3.12`.

## Executed checks

Run these commands from the repository root:

```sh
uv run --locked codeprov --help
uv run --locked python -m unittest discover -s tests -v
uv run --locked python -m codeprov.demo --output runs/offline-demo-final
code-health check --fast --changed --format json
code-health check --deep --changed --format json
git diff --check
```

The CLI help command passed. All 27 tests passed in the final full suite in 25.290 seconds. An earlier full run also passed in 34.600 seconds. It exercises the following boundaries:

| Area | Reproduced behavior |
| --- | --- |
| Configuration | Date order, duplicate repositories, positive limits, nonempty independent evidence, input hash changes, low-only reviewer effort |
| Provenance | Exact terminal assistant email, body mentions, human coauthors, missing declarations, independent manual evidence, explicit conflicts |
| Storage | Persistent upsert keys, independent record kinds, strict JSON |
| GitHub transport | Multiple pages, successful-page caching, loop rejection, external-origin rejection, rate-limit errors |
| Metrics | Function/method/closure counting, relative deltas, parse errors, additions/deletions, unavailable source |
| Scanners | Genuine unsafe/clean Bandit and Semgrep runs; shifted/multiple findings; introduced/resolved matching; traversal, process error and timeout paths; failed deltas remain null |
| SZZ | Real local deleted-line attribution, missing-label pending outcomes, censoring, root and merge exclusions |
| Historical review | Original commit/base selection, malicious source comment isolation, gold exclusion, oversized skip, empty completed response, HTTP failure, strict locations/types/duplicate JSON keys |
| Evaluation | Duplicate grouping, exact TP/FP/FN, genuine unmatched findings, complete coverage, one-to-one matches, null labels, null denominators, stale-result invalidation |
| Models | Seeded successful Gaussian and Bayesian fits with finite uncertainty; sample/cohort/repository, rank, separation, variation and finite-input guards |
| Reporting/demo | Pending evidence/null outcomes, synthetic report marker, exact demo evidence counts, repeated runs without duplicate records |

The required scanner integration test fails if a scanner is missing or fails. It does not skip unavailable tools. Mock process results appear only in adapter/error tests and the focused measurement missingness test. The demo measures real source with both scanners.

## Regressions fixed

The initial effort regression failed for both `medium` and `high`. Configuration accepted these values while the reviewer required `low`. Configuration now rejects them before network work.

A new nonfinite-control test initially returned `complete` when `log_churn` contained infinity. Standardization produced a nonfinite scale and silently removed that control. The model now checks finite outcomes and controls before standardization and returns `not_estimable` with `nonfinite model inputs`.

History tests exposed unclosed stdout/stderr pipes in author-experience extraction. A subprocess context manager now closes both streams. The focused history suite passed without those resource warnings after the change.

## Offline evidence

The public command is:

```sh
uv run --locked python -m codeprov.demo --output runs/offline-demo
```

The final standalone check used a fresh `runs/offline-demo-final` directory. The output root contains `synthetic-demo.json` with `synthetic: true`, deterministic source history, synthetic independent-evidence files, configuration, and `demo-summary.json`. The summary names the hashed run directory and its report/evidence files. The final run ID is `bc3cd55e960c80e4`; its artifacts are under `runs/offline-demo-final/runs/bc3cd55e960c80e4/`. All generated artifacts remain ignored by Git. `git diff --check` passed; no run files, virtual environment, or scanner cache files are tracked.

| Checked output | Expected and actual |
| --- | ---: |
| Sampled commits | 3 |
| Python file measurements | 3 |
| Introduced Bandit findings | 1 |
| Introduced Semgrep findings | 1 |
| Supplied-fix candidate introductions | 1 |
| Validated fixture review snapshots | 1 |
| Synthetic matched issue pairs | 1 |
| Estimated models | 0 |

The history contains a base, a declared-AI introduction of `eval(value)`, an independently labeled synthetic manual change, an unknown change, and a later synthetic fix. Fixed identities and dates produce stable SHAs. The review uses the introduction snapshot and excludes the later fix and historical comment text. Its untrusted code comment remains visible as source data.

The reviewer makes an actual HTTP request through its production client to a loopback fixture server. A scoped proxy directs the stable localhost endpoint to an ephemeral local port. The demo replaces the credential with a dummy value and disables environment proxy settings for that client. The fixture returns a disclosed synthetic response; no inference occurs. A rerun reuses its validated immutable response. The unittest demo check verifies identical SHAs, counts, and run directory on repetition.

The small dataset produces `not_estimable` for every model. Separate seeded tests validate successful model fitting. Synthetic annotations validate arithmetic and coverage. They do not measure a model's real review quality.

## Static analysis and limits

Fast and deep code-health checks each analyzed 10 changed/untracked files with zero errors and eight P3 warnings. The checks use the installed Ruff analyzer. New import-order and loop-binding findings were corrected. Remaining P3 findings concern existing import ordering, `datetime.UTC` aliases, explicit `check` arguments in helpers that inspect return codes, and the configuration API's existing `ValueError` contract. These findings do not require architectural changes. Code-health's deep mode did not run an independent type checker or a separate security audit.

GitHub discovery is bypassed by the demo. Mocked HTTP tests verify pagination and cache boundaries; they do not validate live GitHub availability or a representative sample. This verification did not rerun the historical public Click pilot. Earlier README pilot counts remain historical claims.

No paid reviewer, real human annotation, representative empirical cohort, or publishable research effects were produced. SZZ remains conditional on supplied labels and its deleted-line attribution assumptions. The tests cover selected boundaries; they do not establish correctness for every rename, squash, refactor, provider, or adversarial input. Computational model gates do not establish statistical power or causal identification.
