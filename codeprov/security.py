"""Static scanners over disposable source snapshots, never imported or executed."""
import ast
from collections import defaultdict, deque
import importlib.metadata
import io
import json
import os
from pathlib import Path, PurePosixPath
import signal
import subprocess
import sys
import tempfile
import tokenize


SUCCESS_STATUSES = {"complete", "not_applicable"}


def safe_relative_path(value: str) -> PurePosixPath:
    """Reject traversal, platform-specific absolute paths, and ambiguous names."""
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("invalid snapshot path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") or ":" in part for part in value.split("/")):
        raise ValueError("unsafe snapshot path")
    return path


def normalize_code(source: str) -> str:
    """Ignore whitespace/comments, but retain token spelling including literals."""
    ignored = {tokenize.ENCODING, tokenize.ENDMARKER, tokenize.INDENT, tokenize.DEDENT,
               tokenize.NEWLINE, tokenize.NL, tokenize.COMMENT}
    try:
        return json.dumps([(token.type, token.string) for token in
                           tokenize.generate_tokens(io.StringIO(source).readline)
                           if token.type not in ignored], ensure_ascii=False)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return " ".join(source.split())


def finding_identity(finding: dict) -> tuple:
    return (finding["tool"], finding["rule"], finding["path"], normalize_code(finding["code"]))


def diff_findings(before: list[dict], after: list[dict]) -> dict:
    """One-to-one multiset matching preserves duplicate issues and ignores shifts."""
    remaining = defaultdict(deque)
    for index, finding in enumerate(before):
        remaining[finding_identity(finding)].append(index)
    matched = set()
    introduced = []
    for finding in after:
        candidates = remaining[finding_identity(finding)]
        if candidates:
            matched.add(candidates.popleft())
        else:
            introduced.append(finding)
    return {"introduced": introduced,
            "resolved": [finding for index, finding in enumerate(before) if index not in matched]}


def _relative_target(value: str, root: Path, files: dict[str, str]) -> str:
    path = Path(value)
    if path.is_absolute():
        path = path.relative_to(root)
    name = path.as_posix()
    if name.startswith("./"):
        name = name[2:]
    if name not in files:
        raise ValueError(f"scanner returned an unknown target: {value}")
    return name


def _source_range(source: str, start: dict, end: dict) -> str:
    if isinstance(start.get("offset"), int) and isinstance(end.get("offset"), int):
        return source.encode("utf-8")[start["offset"]:end["offset"]].decode("utf-8")
    lines = source.splitlines(keepends=True)
    first, last = int(start["line"]), int(end.get("line", start["line"]))
    if not 1 <= first <= last <= len(lines):
        raise ValueError("scanner returned an invalid source range")
    selected = lines[first - 1:last]
    if len(selected) == 1:
        return selected[0][int(start.get("col", 1)) - 1:int(end.get("col", len(selected[0]) + 1)) - 1]
    selected[0] = selected[0][int(start.get("col", 1)) - 1:]
    selected[-1] = selected[-1][:int(end.get("col", len(selected[-1]) + 1)) - 1]
    return "".join(selected)


def _bandit_code(source: str, result: dict, tree) -> str:
    line = int(result["line_number"])
    column = int(result.get("col_offset", result.get("issue_col_offset", 0)))
    if tree is not None:
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and (node.lineno, node.col_offset) <= (line, column)
                 and (node.end_lineno, node.end_col_offset) >= (line, column)]
        if calls:
            node = min(calls, key=lambda item: (item.end_lineno - item.lineno,
                                               item.end_col_offset - item.col_offset))
            segment = ast.get_source_segment(source, node)
            if segment is not None:
                return segment
    lines = source.splitlines()
    span = result.get("line_range") or [line]
    first, last = min(span), max(span)
    if not 1 <= first <= last <= len(lines):
        raise ValueError("Bandit returned an invalid source range")
    return "\n".join(lines[first - 1:last])


def normalize_findings(tool: str, payload: dict, files: dict[str, str], root: Path) -> list[dict]:
    """Convert scanner JSON into stable source-based findings for changed files."""
    if not isinstance(payload.get("results"), list):
        raise ValueError("scanner JSON lacks a results array")
    trees = {}
    if tool == "bandit":
        for path, source in files.items():
            try:
                trees[path] = ast.parse(source)
            except (SyntaxError, ValueError):
                trees[path] = None
    findings = []
    for result in payload["results"]:
        if tool == "bandit":
            path = _relative_target(result["filename"], root, files)
            line = int(result["line_number"])
            span = result.get("line_range") or [line]
            findings.append({"tool": tool, "rule": str(result["test_id"]), "path": path,
                             "line": line, "end_line": max(span),
                             "severity": str(result["issue_severity"]).upper(),
                             "code": _bandit_code(files[path], result, trees[path]),
                             "message": str(result["issue_text"])})
        elif tool == "semgrep":
            path = _relative_target(result["path"], root, files)
            extra = result["extra"]
            findings.append({"tool": tool, "rule": str(result["check_id"]), "path": path,
                             "line": int(result["start"]["line"]),
                             "end_line": int(result["end"]["line"]),
                             "severity": str(extra["severity"]).upper(),
                             "code": _source_range(files[path], result["start"], result["end"]),
                             "message": str(extra["message"])})
        else:
            raise ValueError(f"unknown scanner: {tool}")
    return findings


def _isolated_env(workspace: Path) -> dict:
    home = workspace / "home"
    home.mkdir()
    temporary = workspace / "tmp"
    temporary.mkdir()
    return {"PATH": str(Path(sys.executable).parent) + os.pathsep + os.defpath,
            "HOME": str(home), "XDG_CONFIG_HOME": str(home / "config"),
            "XDG_CACHE_HOME": str(home / "cache"), "TMPDIR": str(temporary),
            "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONNOUSERSITE": "1",
            "SEMGREP_SEND_METRICS": "off", "SEMGREP_ENABLE_VERSION_CHECK": "0",
            "SEMGREP_SETTINGS_FILE": str(home / "semgrep-settings.yml")}


def _run_scanner(tool: str, command: list[str], root: Path, files: dict[str, str],
                 artifact_dir: Path, env: dict, timeout: int) -> dict:
    raw_path = artifact_dir / f"{tool}.json"
    stderr_path = artifact_dir / f"{tool}.stderr.txt"
    metadata_path = artifact_dir / f"{tool}.meta.json"
    result = {"status": "failed", "findings": [], "returncode": None,
              "artifacts": {"raw": str(raw_path), "stderr": str(stderr_path),
                            "metadata": str(metadata_path)}}
    stdout = ""
    stderr = ""
    try:
        version = importlib.metadata.version(tool)
    except importlib.metadata.PackageNotFoundError:
        version = None
        result.update(status="unavailable", error=f"{tool} is not installed")
    if version is not None:
        try:
            process = subprocess.Popen(command, cwd=root, env=env, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                       errors="replace", start_new_session=True)
            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                stdout, stderr = process.communicate()
                result.update(status="timeout", error=f"scanner exceeded {timeout} seconds")
            result["returncode"] = process.returncode
            if result["status"] != "timeout":
                try:
                    payload = json.loads(stdout)
                    if not isinstance(payload, dict):
                        raise ValueError("scanner JSON must be an object")
                    result["findings"] = normalize_findings(tool, payload, files, root)
                    errors = payload.get("errors", [])
                    if not isinstance(errors, list):
                        raise ValueError("scanner errors must be an array")
                    # Bandit 1 means findings. Semgrep without --error succeeds with 0 only.
                    success_codes = {0, 1} if tool == "bandit" else {0}
                    if errors:
                        source_errors = all(
                            "syntax error" in str(error.get("reason", "")).lower()
                            if tool == "bandit" else error.get("type") in
                            ("ParseError", "PartialParsing", "LexicalError")
                            for error in errors if isinstance(error, dict)
                        ) and all(isinstance(error, dict) for error in errors)
                        result.update(status="parse_error" if source_errors else "failed",
                                      error="scanner reported source parse errors" if source_errors
                                      else "scanner reported configuration/runtime errors", errors=errors)
                    elif process.returncode not in success_codes:
                        result.update(status="failed", error=f"scanner exited {process.returncode}")
                    else:
                        if tool == "semgrep":
                            scanned = payload.get("paths", {}).get("scanned", [])
                        else:
                            scanned = [name for name in payload.get("metrics", {}) if name != "_totals"]
                        covered = {_relative_target(name, root, files) for name in scanned}
                        missing = sorted(set(files) - covered)
                        if missing:
                            result.update(status="incomplete", error="scanner did not scan every target",
                                          missing_paths=missing)
                        else:
                            result["status"] = "complete"
                except (ValueError, KeyError, TypeError, IndexError, AttributeError) as error:
                    result.update(status="invalid_output", error=str(error))
        except OSError as error:
            result.update(status="failed", error=str(error))
    raw_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    metadata_path.write_text(json.dumps({"tool": tool, "version": version, "command": command,
                                         "timeout_seconds": timeout, **result}, indent=2) + "\n")
    return result


def scan_snapshot(files: dict[str, str], rules: Path, artifact_dir: Path, timeout: int = 120) -> dict:
    """Scan regular .py source strings together; keep JSON/errors, delete snapshots.

    Each tool returns status, findings, returncode, artifacts and optional error(s).
    Non-complete status never represents a clean scan. No target code is executed.
    """
    artifact_dir = Path(artifact_dir).resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    if not files:
        return {tool: {"status": "complete", "findings": [], "returncode": None,
                       "artifacts": {}, "reason": "empty_snapshot"} for tool in ("bandit", "semgrep")}
    with tempfile.TemporaryDirectory(prefix="codeprov-scan-") as directory:
        workspace = Path(directory)
        root = workspace / "source"
        root.mkdir()
        try:
            for name, source in files.items():
                path = safe_relative_path(name)
                if path.suffix != ".py" or not isinstance(source, str):
                    raise ValueError("snapshots require .py paths and source strings")
                target = root.joinpath(*path.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("x", encoding="utf-8") as output:
                    output.write(source)
        except (OSError, ValueError) as error:
            result = {tool: {"status": "invalid_snapshot", "findings": [], "returncode": None,
                             "artifacts": {}, "error": str(error)} for tool in ("bandit", "semgrep")}
            (artifact_dir / "snapshot-error.json").write_text(json.dumps(result, indent=2) + "\n")
            return result
        # Empty local ignore rules override Semgrep's built-in test/vendor exclusions.
        (root / ".semgrepignore").write_text("", encoding="utf-8")
        env = _isolated_env(workspace)
        bandit = [sys.executable, "-I", "-m", "bandit", "-q", "-f", "json", "-r", "."]
        results = {"bandit": _run_scanner("bandit", bandit, root, files, artifact_dir, env, timeout)}
        try:
            pinned = workspace / "pinned-rules.yml"
            pinned.write_bytes(Path(rules).read_bytes())
        except OSError as error:
            results["semgrep"] = {"status": "failed", "findings": [], "returncode": None,
                                  "artifacts": {}, "error": f"cannot read pinned rules: {error}"}
            (artifact_dir / "semgrep.meta.json").write_text(json.dumps(results["semgrep"], indent=2) + "\n")
            return results
        semgrep = [str(Path(sys.executable).parent / "semgrep"), "scan", "--json", "--config", str(pinned),
                   "--metrics", "off", "--disable-version-check", "--no-git-ignore", "--no-rewrite-rule-ids",
                   "--strict", "--jobs", "1", "--max-target-bytes", "0",
                   *["./" + name for name in files]]
        results["semgrep"] = _run_scanner("semgrep", semgrep, root, files, artifact_dir, env, timeout)
        return results
