"""Explicit, resumable pipeline commands over one immutable study selection."""
import argparse
import importlib.metadata
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .config import load
from .storage import Store


def initialize(study, root: Path) -> tuple[Store, Path]:
    run_dir = root.resolve() / study.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "manifest.json"
    versions = {"python": platform.python_version(), "codeprov": __version__}
    for package in ("pydriller", "radon", "bandit", "semgrep", "statsmodels", "pandas", "httpx"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not installed"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest["study"] != json.loads(json.dumps(study.manifest())):
            raise ValueError("run manifest does not match study; use a new results directory")
        if manifest["versions"] != versions:
            raise ValueError("runtime versions changed; use a new results directory to preserve reproducibility")
    else:
        manifest_path.write_text(json.dumps({"run_id": study.run_id, "created_at": datetime.now(timezone.utc).isoformat(),
                                           "study": study.manifest(), "versions": versions}, indent=2) + "\n")
        for key in ("semgrep_rules", "manual_labels", "bug_fixes"):
            path = getattr(study, key)
            if path:
                (run_dir / f"{key}{path.suffix}").write_bytes(path.read_bytes())
    return Store(run_dir / "study.sqlite"), run_dir


def main(argv=None):
    parser = argparse.ArgumentParser(prog="codeprov", description=__doc__)
    parser.add_argument("--study", type=Path, default=Path("study.toml"))
    parser.add_argument("--results", type=Path, default=Path("runs"))
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "status", "mine", "measure", "szz", "analyze", "report"):
        sub.add_parser(name)
    review_parser = sub.add_parser("review")
    review_parser.add_argument("--export-only", action="store_true", help="Export leakage-free requests without an API call")
    evaluate_parser = sub.add_parser("evaluate")
    evaluate_parser.add_argument("--adjudications", type=Path, required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--export-only", action="store_true")
    run_parser.add_argument("--adjudications", type=Path)
    args = parser.parse_args(argv)
    store = None
    try:
        study = load(args.study)
        store, run_dir = initialize(study, args.results)
        if args.command == "init":
            result = {"run_dir": str(run_dir), "run_id": study.run_id}
        elif args.command == "status":
            result = {"run_dir": str(run_dir), "stages": store.stages()}
        else:
            from .mining import mine
            from .metrics import measure
            from .szz import attribute
            from .review import review
            from .evaluation import evaluate
            from .analysis import analyze
            from .report import report
            functions = {"mine": mine, "measure": measure, "szz": attribute, "analyze": analyze, "report": report}
            def execute(command):
                store.stage(command, "running", {})
                try:
                    if command == "review":
                        output = review(study, store, run_dir, export_only=args.export_only)
                    elif command == "evaluate":
                        output = evaluate(study, store, run_dir, args.adjudications)
                    else:
                        output = functions[command](study, store, run_dir)
                    # Modules may report partial / pending / not_estimable outcomes explicitly.
                    store.stage(command, output.get("status", "complete"), output)
                    return output
                except Exception as error:
                    store.stage(command, "failed", {"error": str(error)})
                    raise
            if args.command == "run":
                result = {}
                for command in ("mine", "measure", "szz", "review"):
                    result[command] = execute(command)
                if args.adjudications:
                    result["evaluate"] = execute("evaluate")
                else:
                    store.stage("evaluate", "pending", {"reason": "human adjudications required"})
                for command in ("analyze", "report"):
                    result[command] = execute(command)
            else:
                result = execute(args.command)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except Exception as error:
        print(f"codeprov: {error}", file=sys.stderr)
        return 1
    finally:
        if store:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
