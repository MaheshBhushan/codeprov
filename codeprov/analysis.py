"""Pre-specified observational models; unavailable evidence is never zero."""
import json
import math
import warnings
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse
from statsmodels.genmod.bayes_mixed_glm import BinomialBayesMixedGLM
from statsmodels.regression.mixed_linear_model import MixedLM


CONTROLS = ["declared_ai", "log_churn", "prior_complexity", "log_experience"]


def _churn(commit):
    added, deleted = commit.get("added_lines"), commit.get("deleted_lines")
    return added + deleted if added is not None and deleted is not None else None


def _provenance(commit):
    evidence = commit.get("provenance_evidence") or {}
    return "excluded_conflict" if isinstance(evidence, dict) and evidence.get("conflict") else commit["provenance"]


def commit_frame(store) -> pd.DataFrame:
    measurements = defaultdict(list)
    for row in store.rows("measurement"):
        measurements[(row["repo"], row["sha"])].append(row)
    outcomes = {(row["repo"], row["sha"]): row for row in store.rows("szz_outcome")}
    data = []
    for commit in store.rows("commit"):
        files = measurements[(commit["repo"], commit["sha"])]
        valid = [row for row in files if row.get("status") == "complete"]
        before = [row["before"] for row in valid if row.get("before")]
        deltas = [row["mi_delta"] for row in valid if row.get("mi_delta") is not None]
        complexity = [row["complexity_delta"] for row in valid if row.get("complexity_delta") is not None]
        outcome = outcomes.get((commit["repo"], commit["sha"]), {})
        churn = _churn(commit)
        experience = commit.get("author_experience")
        row = {"repo": commit["repo"], "sha": commit["sha"], "provenance": _provenance(commit),
               "declared_ai": int(commit["provenance"] == "declared_ai"),
               "log_churn": math.log1p(churn) if churn is not None else None,
               "prior_complexity": sum(item["complexity"] for item in before) if before else None,
               "log_experience": math.log1p(experience) if experience is not None else None,
               "mi_delta": sum(deltas) / len(deltas) if deltas and len(valid) == len(files) else None,
               "complexity_delta": sum(complexity) if complexity and len(valid) == len(files) else None,
               "candidate_bug": outcome.get("candidate_bug") if outcome.get("eligible") else None,
               "szz_reason": outcome.get("reason"), "changed_python_files": len(files)}
        for tool in ("bandit", "semgrep"):
            scans = [item.get(tool, {}) for item in files]
            row[f"{tool}_introduced"] = int(any(scan.get("introduced") for scan in scans)) if scans and all(
                scan.get("status") == "complete" for scan in scans) else None
        data.append(row)
    return pd.DataFrame(data)


def fit_model(frame: pd.DataFrame, outcome: str, baseline: str, binary=False, controls=None) -> dict:
    controls = controls or CONTROLS
    label = f"{outcome}:declared_ai_vs_{baseline}"
    result = {"id": label, "outcome": outcome, "baseline": baseline,
              "model": "BinomialBayesMixedGLM" if binary else "MixedLM",
              "controls": controls, "random_effects": "repository intercept", "status": "not_estimable"}
    if frame.empty or outcome not in frame:
        return {**result, "reason": "no observations"}
    selected = frame[frame.provenance.isin(["declared_ai", baseline])]
    clean = selected.dropna(subset=[outcome, "repo", *controls]).copy()
    result.update(n=len(clean), excluded_missing=len(selected) - len(clean),
                  cohorts=dict(Counter(clean.provenance)), repositories=clean.repo.nunique())
    if len(clean) < 20 or clean.repo.nunique() < 2 or any(
        (clean.provenance == cohort).sum() < 5 for cohort in ("declared_ai", baseline)
    ):
        return {**result, "reason": "require >=20 complete rows, >=2 repositories and >=5 rows in each cohort"}
    # Standardize numeric controls for optimizer conditioning; provenance remains 0/1.
    transforms = {}
    active = ["declared_ai"]
    for column in controls:
        if column == "declared_ai":
            continue
        mean, scale = float(clean[column].mean()), float(clean[column].std(ddof=0))
        transforms[column] = {"mean": mean, "scale": scale, "included": scale > 0}
        if scale > 0:
            clean[column] = (clean[column] - mean) / scale
            active.append(column)
    result["standardization"] = transforms
    exog = np.column_stack([np.ones(len(clean)), clean[active].to_numpy(dtype=float)])
    names = ["Intercept", *active]
    if np.linalg.matrix_rank(exog) < exog.shape[1]:
        return {**result, "reason": "fixed-effect design is rank deficient"}
    y = clean[outcome].to_numpy(dtype=float)
    if not np.isfinite(exog).all() or not np.isfinite(y).all():
        return {**result, "reason": "nonfinite model inputs"}
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            if binary:
                if not set(y).issubset({0.0, 1.0}) or min(np.sum(y == 0), np.sum(y == 1)) < 5:
                    return {**result, "reason": "require binary outcome with >=5 events and >=5 non-events"}
                cross = pd.crosstab(clean.declared_ai, clean[outcome]).reindex(index=[0, 1], columns=[0, 1], fill_value=0)
                if (cross.to_numpy() == 0).any():
                    return {**result, "reason": "cohort/outcome separation; do not publish an unstable effect"}
                codes, groups = pd.factorize(clean.repo, sort=True)
                random = sparse.csr_matrix((np.ones(len(codes)), (np.arange(len(codes)), codes)),
                                           shape=(len(codes), len(groups)))
                fit = BinomialBayesMixedGLM(y, exog, random, np.zeros(len(groups), dtype=int)).fit_vb()
                if not fit.optim_retvals.get("success", False):
                    return {**result, "reason": "variational optimizer did not converge"}
                means, sds = fit.fe_mean, fit.fe_sd
                intervals = np.column_stack([means - 1.96 * sds, means + 1.96 * sds])
                result["interval_type"] = "approximate 95% variational posterior credible interval"
                result["repository_sd"] = float(np.exp(fit.vcp_mean[0]))
                result["method"] = "Bayesian variational approximation; independent random effects"
            else:
                if np.ptp(y) == 0:
                    return {**result, "reason": "outcome has no variation"}
                fit = MixedLM(y, exog, groups=clean.repo.to_numpy()).fit(reml=False, method="lbfgs", disp=False)
                if not fit.converged:
                    return {**result, "reason": "mixed linear optimizer did not converge"}
                means, sds = fit.fe_params, fit.bse_fe
                intervals = np.asarray(fit.conf_int())[:len(names)]
                result["interval_type"] = "95% Wald confidence interval"
                result["repository_sd"] = float(np.sqrt(fit.cov_re[0, 0]))
                result["residual_sd"] = float(np.sqrt(fit.scale))
                result["method"] = "Gaussian mixed linear model, maximum likelihood"
            result["warnings"] = list(dict.fromkeys(str(w.message) for w in caught))
        if not np.isfinite(np.concatenate([np.asarray(means), np.asarray(sds), intervals.ravel()])).all():
            return {**result, "reason": "nonfinite coefficient uncertainty"}
        result["coefficients"] = [{"term": name, "estimate": float(mean), "sd": float(sd),
                                   "lower": float(interval[0]), "upper": float(interval[1])}
                                  for name, mean, sd, interval in zip(names, means, sds, intervals)]
        result["status"] = "complete"
        return result
    except (ValueError, np.linalg.LinAlgError, FloatingPointError) as error:
        return {**result, "reason": f"model fitting failed: {error}"}


def review_frame(store) -> pd.DataFrame:
    commits = {(c["repo"], c["sha"]): c for c in store.rows("commit")}
    prs = {(p["repo"], p["number"]): p for p in store.rows("pr")}
    rows = []
    for item in store.rows("evaluation"):
        pr = prs.get((item["repo"], item["pr_number"]), {})
        members = [commits[(item["repo"], sha)] for sha in pr.get("commits", [])
                   if (item["repo"], sha) in commits]
        labels = {_provenance(c) for c in members}
        churns = [_churn(c) for c in members]
        complete_churn = bool(members) and len(members) == len(pr.get("commits", [])) and all(c is not None for c in churns)
        # Mixed and unlabeled PRs remain excluded, rather than majority-vote provenance.
        provenance = next(iter(labels)) if len(labels) == 1 else "mixed"
        row = {"repo": item["repo"], "pr_number": item["pr_number"], "split": item["split"],
               "provenance": provenance, "declared_ai": int(provenance == "declared_ai"),
               "log_churn": math.log1p(sum(churns)) if complete_churn else None,
               "tp": item["tp"], "fp": item["fp"], "fn": item["fn"]}
        for metric, denominator in (("precision", "fp"), ("recall", "fn")):
            row[f"{metric}_logit"] = math.log((item["tp"] + 0.5) / (item[denominator] + 0.5)) if (
                item["tp"] + item[denominator]) > 0 else None
        rows.append(row)
    return pd.DataFrame(rows)


def _write_csv(path: Path, frame: pd.DataFrame):
    frame.to_csv(path, index=False)


def analyze(study, store, run_dir: Path) -> dict:
    store.clear("analysis")
    frame = commit_frame(store)
    _write_csv(run_dir / "commit-analysis.csv", frame)
    models = []
    for baseline in ("verified_manual", "unknown"):
        for outcome in ("mi_delta", "complexity_delta", "bandit_introduced", "semgrep_introduced", "candidate_bug"):
            models.append(fit_model(frame, outcome, baseline, binary=outcome not in ("mi_delta", "complexity_delta")))
    reviews = review_frame(store)
    _write_csv(run_dir / "review-analysis.csv", reviews)
    # Holdout results only; development labels cannot contribute to published evaluation effects.
    if not reviews.empty:
        heldout = reviews[reviews.split == "test"]
        for baseline in ("verified_manual", "unknown"):
            for outcome in ("precision_logit", "recall_logit"):
                model = fit_model(heldout, outcome, baseline, controls=["declared_ai", "log_churn"])
                model["interpretation"] = "unweighted per-PR empirical logit with 0.5 continuity correction; exploratory"
                models.append(model)
    for model in models:
        store.put("analysis", model["id"], model)
    result = {"status": "complete" if any(model["status"] == "complete" for model in models) else "not_estimable",
              "observations": len(frame), "models_estimated": sum(m["status"] == "complete" for m in models),
              "models_not_estimable": sum(m["status"] != "complete" for m in models),
              "provenance_counts": dict(Counter(frame.provenance)) if not frame.empty else {},
              "interpretation": "associations, not causal effects; unknown is no declaration, not human authorship"}
    (run_dir / "analysis.json").write_text(json.dumps({"summary": result, "models": models}, indent=2, allow_nan=False) + "\n")
    return result
