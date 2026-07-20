#!/usr/bin/env python3
"""
Evaluator CLI Bridge v1.4.3 — standalone
========================================

Builds an Evaluator/WKE request JSON and delegates execution to the existing
Evaluator/WKE script. Imports no previous versions.

Boundary:
- Does not evaluate writing.
- Does not detect grammar errors.
- Does not score.
- Does not classify LRET candidates.
- Only assembles request data and runs the evaluator subprocess.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, List

SCHEMA_VERSION = "EVALUATOR_BRIDGE_REQUEST_V1_4_3"
ENGINE_ID = "VA_STELLA_EVALUATOR_CLI_BRIDGE"
ENGINE_VERSION = "1.4.3-standalone-no-imports"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: str) -> Any:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any, pretty: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2 if pretty else None) + "\n", encoding="utf-8")


def diagnostic_row_count(detector_obj: Any) -> int:
    if not isinstance(detector_obj, dict):
        return 0
    roots = [detector_obj]
    if isinstance(detector_obj.get("results"), list):
        roots += [r for r in detector_obj["results"] if isinstance(r, dict)]
    total = 0
    for root in roots:
        for key in ("diagnostic_rows", "student_rows", "rows", "errors"):
            value = root.get(key)
            if isinstance(value, list):
                total += len(value)
    return total


def build_request(submission: Dict[str, Any], detector_path: str, scorer_path: Optional[str], ontology_dir: Optional[str], use_llm: bool, detector_rows: int) -> Dict[str, Any]:
    # v1.4.13: --scorer is now optional. Gold v1.4.13 moved the evaluator stage
    # to run before the scorer stage (to feed the scorer real content-quality
    # signals via evaluator_rubric_bridge_v1.py), so at evaluator-run time no
    # scorer artifact exists yet on disk. The underlying evaluator engine
    # already tolerates this: normalize_scorer_output(None) -> {"available": False},
    # and load_json_path(None) -> None (no crash). A resolved-but-nonexistent
    # path would raise FileNotFoundError inside the engine, so we must pass
    # None rather than a dangling path when --scorer is omitted.
    scorer_output_path = str(Path(scorer_path).resolve()) if scorer_path else None
    return {
        "schema_version": SCHEMA_VERSION,
        "bridge_engine_id": ENGINE_ID,
        "bridge_engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "student_id": submission.get("student_id") or "student_unknown",
        "essay_id": submission.get("essay_id") or "essay_unknown",
        "submission_id": (submission.get("source_metadata") or {}).get("submission_id"),
        "prompt_text": submission.get("prompt_text") or "",
        "essay_text": submission.get("essay_text") or submission.get("text") or "",
        "detector_output_path": str(Path(detector_path).resolve()),
        "scorer_output_path": scorer_output_path,
        "ontology_dir": str(Path(ontology_dir).resolve()) if ontology_dir else None,
        "use_llm": bool(use_llm),
        "max_llm_skills": 30,
        "bridge_quality_context": {
            "detector_rows_supplied_to_evaluator": detector_rows,
            "detector_schema_expected_by_evaluator": "diagnostic_rows under root/results[0]",
        },
    }


def resolve_script(script: str, base_dir: Path) -> Path:
    p = Path(script)
    if p.is_absolute():
        return p
    for candidate in (base_dir / p, Path.cwd() / p):
        if candidate.exists():
            return candidate.resolve()
    return (base_dir / p).resolve()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build evaluator request and run Evaluator/WKE as subprocess.")
    ap.add_argument("--submission", required=True)
    ap.add_argument("--detector", required=True, help="Detector artifact already normalized for Evaluator/WKE.")
    ap.add_argument("--scorer", required=False, default=None,
                     help="Optional. Omit when the evaluator stage runs before the scorer stage "
                          "(Gold v1.4.13+ default order); the evaluator engine falls back to "
                          "scorer_available=False context gracefully.")
    ap.add_argument("--evaluator-script", required=True)
    ap.add_argument("--ontology-dir")
    ap.add_argument("--output", "-o", required=True)
    ap.add_argument("--request-output")
    ap.add_argument("--pretty", action="store_true")
    ap.add_argument("--no-llm", action="store_true")
    args = ap.parse_args(argv)

    submission = read_json(args.submission)
    detector_obj = read_json(args.detector)
    detector_rows = diagnostic_row_count(detector_obj)
    out_path = Path(args.output).resolve()
    request_path = Path(args.request_output).resolve() if args.request_output else out_path.with_suffix(".request.json")
    request = build_request(submission, args.detector, args.scorer, args.ontology_dir, use_llm=not args.no_llm, detector_rows=detector_rows)
    write_json(request_path, request, pretty=True)

    script = resolve_script(args.evaluator_script, Path(__file__).resolve().parent)
    cmd = [sys.executable, str(script), "--input", str(request_path), "--output", str(out_path)]
    if args.pretty:
        cmd.append("--pretty")
    if args.no_llm:
        cmd.append("--no-llm")
    result = subprocess.run(cmd, cwd=str(script.parent))
    return int(result.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
