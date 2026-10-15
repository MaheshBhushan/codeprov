"""Validate study inputs before any network or repository work."""
import hashlib
import json
import re
import tomllib
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class Study:
    repositories: tuple[str, ...]
    since: str
    until: str
    observed_until: str
    followup_days: int
    max_prs: int
    ai_identities: tuple[str, ...]
    semgrep_rules: Path
    manual_labels: Path | None
    bug_fixes: Path | None
    review: dict

    def manifest(self) -> dict:
        values = asdict(self)
        for key in ("semgrep_rules", "manual_labels", "bug_fixes"):
            path = values[key]
            values[key] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} if path else None
        return values

    @property
    def run_id(self) -> str:
        return hashlib.sha256(json.dumps(self.manifest(), sort_keys=True).encode()).hexdigest()[:16]


def load(path: Path) -> Study:
    path = path.resolve()
    raw = tomllib.loads(path.read_text())
    data = raw.get("study", {})
    repositories = data.get("repositories", [])
    if not isinstance(repositories, list) or not repositories or any(
        not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)
        or any(part in (".", "..") for part in repo.split("/")) for repo in repositories
    ):
        raise ValueError("study.repositories must contain owner/repo names")
    if len(set(repositories)) != len(repositories):
        raise ValueError("duplicate repositories")
    dates = [date.fromisoformat(str(data[key])) for key in ("since", "until", "observed_until")]
    if not dates[0] <= dates[1] <= dates[2]:
        raise ValueError("require since <= until <= observed_until")
    for key in ("followup_days", "max_prs"):
        if type(data.get(key)) is not int or data[key] <= 0:
            raise ValueError(f"study.{key} must be a positive integer")
    identities = data.get("ai_identities", [])
    if not isinstance(identities, list) or not identities or any(not isinstance(x, str) or "@" not in x for x in identities):
        raise ValueError("ai_identities must contain exact assistant email identities")
    def evidence_file(key, required=False):
        value = data.get(key)
        if value is None and not required:
            return None
        if not isinstance(value, str):
            raise ValueError(f"study.{key} must be a file path")
        result = (path.parent / value).resolve()
        if not result.is_file():
            raise ValueError(f"study.{key} does not exist: {result}")
        if key != "semgrep_rules":
            records = json.loads(result.read_text())
            if not isinstance(records, list) or any(
                not isinstance(row, dict) or row.get("repo") not in repositories
                or not re.fullmatch(r"[0-9a-f]{40}", str(row.get("sha", "")))
                or not isinstance(row.get("evidence"), str) or not row["evidence"].strip()
                for row in records
            ):
                raise ValueError(f"{key} must be an array of repo, full sha, nonempty evidence records")
            if len({(row["repo"], row["sha"]) for row in records}) != len(records):
                raise ValueError(f"duplicate commit evidence in {key}")
        return result
    review = raw.get("review", {})
    endpoint = urlparse(str(review.get("endpoint", "")))
    if endpoint.scheme not in ("http", "https") or not endpoint.netloc or endpoint.username or endpoint.password:
        raise ValueError("review.endpoint must be an HTTP(S) URL without credentials")
    if endpoint.scheme != "https" and endpoint.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("non-local review endpoints require HTTPS")
    if not isinstance(review.get("model"), str) or not review["model"].strip():
        raise ValueError("review.model must be explicit")
    if review.get("effort", "low") != "low":
        raise ValueError("review.effort must be low for the historical review protocol")
    if type(review.get("max_chars")) is not int or review["max_chars"] <= 0:
        raise ValueError("review.max_chars must be positive")
    development = review.get("development_repositories", [])
    if not isinstance(development, list) or any(repo not in repositories for repo in development):
        raise ValueError("development_repositories must be selected repositories")
    return Study(tuple(repositories), *(d.isoformat() for d in dates), data["followup_days"],
                 data["max_prs"], tuple(x.lower() for x in identities), evidence_file("semgrep_rules", True),
                 evidence_file("manual_labels"), evidence_file("bug_fixes"), review)
