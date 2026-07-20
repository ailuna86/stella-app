#!/usr/bin/env python3
"""
Det VIP CLI Bridge v1.0
=========================

Standalone Gold-contract CLI wrapper around det_vip_v18d_2.py's
PremiumDetectorV9 -- the real LLM-based detector (gpt-4o-mini by default,
6 parallel LLM passes plus rule/spaCy/LanguageTool passes), which ships only
as a FastAPI service (no CLI, no argparse, no main()). This bridge imports
PremiumDetectorV9 directly and calls .analyze() in-process, skipping
FastAPI/uvicorn entirely, so it fits the orchestrator's normal
subprocess-per-stage JSON-in/JSON-out contract exactly like every other Gold
stage.

Confirmed compatible with the *existing* downstream detector consumers
(scorer_input_evidence_guard_standalone_v1_4_7.py,
detector_to_errormap_v3_standalone.py,
detector_for_evaluator_adapter_standalone.py) by reading their actual field
lookups rather than assuming -- they already prefer det_vip's native field
names first (rubric, issue_code, score_charge_weight, root_or_secondary) and
already contain a CANONICAL_RUBRIC_BY_FAMILY table keyed on det_vip's real
family codes (SPELLING, REGISTER, VERB_TENSE, TRANSITION, ...), not just
detector_cli_v1_4_4.py's smaller prefixed family set. That strongly suggests
these bridges were originally written with a det_vip-shaped detector in
mind. This bridge therefore does MINIMAL field renaming: it wraps det_vip's
native analyze() output in the same top-level container shape
detector_cli_v1_4_4.py produces ({"results": [...]}), and adds a small set
of alias fields per row (chargeable, criterion, student_message,
suggested_revision, excerpt, surface_quote, error_type, category) purely so
every consumer's fallback-key chain has something to find, without
discarding det_vip's own native fields.

capacity_domain is deliberately NOT synthesized here. Per the Gold
architecture (Evaluator measures capacity/skill; Detector only detects
errors), capacity_domain is left absent on rows -- detector_to_errormap_v3
already falls back to its own CAPACITY_BY_CRITERION table keyed on
criterion/rubric when a row has no explicit capacity_domain, so nothing
extra is needed here.

Scope: LEXICAL-ERROR-ONLY vs full detection is a downstream routing
decision, not something this bridge encodes. det_vip's own
lret_fix_payload.validated_fix_candidates is already a lexical_resource-only
subset of the same chargeable rows this bridge passes through -- it does not
need separate plumbing to reach LRET, since those rows already flow through
detector -> errormap -> LRET's existing --detector-output input like every
other detector row.

Boundary:
- Does not implement detection logic -- that is entirely PremiumDetectorV9's.
- Does not decide word-limit rejection policy beyond exposing the flag.
- Does not call any LLM by default (--require-llm must be passed explicitly).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ENGINE_ID = "VA_STELLA_DET_VIP_CLI_BRIDGE"
ENGINE_VERSION = "1.0.0-wraps-det_vip_v18d_2-PremiumDetectorV9"
SCHEMA_VERSION = "DETECTOR_OUTPUT_STANDALONE_V1_4_4"  # container-compatible with the CLI it replaces


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, data: Any, pretty: bool = False) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2 if pretty else None)
        f.write("\n")


def normalize_submission(raw: Any, essay_index: int = 0) -> Dict[str, Any]:
    """Accept either Gold's normalized submission shape or a raw/batch essay JSON."""
    if isinstance(raw, dict) and isinstance(raw.get("essays"), list):
        essays = raw.get("essays") or []
        if not essays:
            raise ValueError("essays[] is empty")
        rec = dict(essays[essay_index] or {})
    elif isinstance(raw, dict):
        rec = dict(raw)
    else:
        raise ValueError("input must be a JSON object or {essays:[...]}")
    essay_text = str(rec.get("essay_text") or rec.get("text") or "").strip()
    prompt_text = str(rec.get("prompt_text") or rec.get("prompt") or "").strip()
    if not essay_text:
        raise ValueError("missing essay_text")
    return {
        "essay_id": str(rec.get("essay_id") or "essay_001"),
        "student_id": str(rec.get("student_id") or "student_unknown"),
        "task_type": str(rec.get("task_type") or "WT2"),
        "prompt_text": prompt_text,
        "essay_text": essay_text,
        "topic_keywords": rec.get("topic_keywords", []),
        "source_metadata": rec.get("source_metadata") or {},
    }


def alias_row_fields(row: Dict[str, Any]) -> Dict[str, Any]:
    """Add Gold-CLI-shaped alias keys on top of det_vip's native DiagnosticRow
    fields, without removing anything. Every downstream consumer's fallback
    chain (rubric > criterion, chargeable_for_scoring > chargeable, etc.)
    already tries det_vip's native names first, so this is a safety net,
    not the primary compatibility mechanism."""
    if not isinstance(row, dict):
        return row
    row.setdefault("criterion", row.get("rubric"))
    row.setdefault("category", row.get("rubric"))
    row.setdefault("chargeable", row.get("chargeable_for_scoring"))
    row.setdefault("error_type", row.get("family") or row.get("issue_code"))
    row.setdefault("excerpt", row.get("quote"))
    row.setdefault("surface_quote", row.get("quote"))
    row.setdefault("student_message", row.get("problem_statement") or row.get("explanation"))
    row.setdefault("suggested_revision", row.get("repair_hypothesis"))
    row.setdefault("source_row_id", row.get("row_id"))
    row.setdefault("error_id", row.get("row_id"))
    return row


def alias_all_known_row_lists(det_result: Dict[str, Any]) -> None:
    """Apply alias_row_fields() to every row collection det_vip produces, not
    just student_rows.

    Bug found via a real sandbox test: student_rows only holds
    chargeable_for_scoring=True rows. evaluator_payload.all_detector_evidence
    is a SEPARATE, broader list that also includes review_only rows (rows
    det_vip's own arbitration explicitly marked NOT chargeable). Those rows
    are not the same dict objects as anything in student_rows, so aliasing
    only student_rows left them without a "chargeable" key entirely --
    detector_to_errormap_v3_standalone.py defaults missing "chargeable" to
    True (`bool(row.get("chargeable", True))`), so review-only candidates
    were silently being treated as real chargeable scoring evidence whenever
    errormap fell back to all_detector_evidence (which it does whenever
    scorer_payload.chargeable_detector_rows is empty). Aliasing every row
    list closes this: chargeable is always set to the correct
    chargeable_for_scoring value, so non-chargeable rows are correctly
    excluded downstream instead of defaulting to True."""
    seen_ids = set()

    def alias_list(rows: Any) -> None:
        if not isinstance(rows, list):
            return
        for r in rows:
            if isinstance(r, dict):
                alias_row_fields(r)
                rid = id(r)
                seen_ids.add(rid)

    alias_list(det_result.get("student_rows"))
    ep = det_result.get("evaluator_payload") or {}
    alias_list(ep.get("all_detector_evidence"))
    lret_payload = det_result.get("lret_fix_payload") or det_result.get("lret") or {}
    alias_list(lret_payload.get("validated_fix_candidates"))
    cl = det_result.get("candidate_lists") or {}
    for key in ("chargeable_rows", "review_only_rows", "survived_candidates",
                "all_stage_rows", "advisory_candidates", "uncertain_candidates"):
        alias_list(cl.get(key))


def build_bridge_output(
    det_result: Dict[str, Any],
    submission: Dict[str, Any],
    llm_enabled: bool,
) -> Dict[str, Any]:
    alias_all_known_row_lists(det_result)
    student_rows = det_result.get("student_rows") or []

    gm = det_result.get("generated_metadata") or {}
    shared_metrics = {
        "word_count": int(gm.get("word_count") or 0),
        "sentence_count": int(gm.get("sentence_count") or 0),
        "paragraph_count": int(gm.get("paragraph_count") or 0),
        "task_schema_status": "complete" if det_result.get("task_profile") else "unknown",
        "task_schema_confidence": float(
            (det_result.get("task_profile") or {}).get("task_completeness_confidence") or 0.72
        ),
    }

    metric_profile = det_result.get("detector_metric_profile") or {}
    shared_block = dict(metric_profile.get("shared") or {})
    shared_block.setdefault("word_count", shared_metrics["word_count"])
    shared_block.setdefault("sentence_count", shared_metrics["sentence_count"])
    shared_block.setdefault("paragraph_count", shared_metrics["paragraph_count"])
    shared_block.setdefault("task_schema_status", shared_metrics["task_schema_status"])
    shared_block.setdefault("task_schema_confidence", shared_metrics["task_schema_confidence"])
    metric_profile["shared"] = shared_block
    metric_profile.setdefault("source", "det_vip_v18d_2_via_bridge")
    metric_profile.setdefault("confidence", shared_metrics["task_schema_confidence"])

    family_counts: Dict[str, int] = {}
    capacity_counts: Dict[str, int] = {}
    criterion_counts: Dict[str, int] = {}
    for r in student_rows:
        fam = str(r.get("family") or "UNKNOWN")
        family_counts[fam] = family_counts.get(fam, 0) + 1
        crit = str(r.get("criterion") or r.get("rubric") or "unknown")
        criterion_counts[crit] = criterion_counts.get(crit, 0) + 1
        cap = r.get("capacity_domain")
        if cap:
            capacity_counts[str(cap)] = capacity_counts.get(str(cap), 0) + 1

    result = dict(det_result)  # keep every native det_vip key (lret_fix_payload, layer0_idea_map, etc.)
    result.setdefault("task_type", submission["task_type"])
    result.setdefault("prompt_text", submission["prompt_text"])
    result.setdefault("essay_text", submission["essay_text"])
    result["word_count"] = shared_metrics["word_count"]
    result["sentence_count"] = shared_metrics["sentence_count"]
    result["paragraph_count"] = shared_metrics["paragraph_count"]
    result["metadata"] = dict(shared_metrics)
    result.setdefault("generated_metadata", dict(shared_metrics))
    result["all_rows"] = student_rows
    result["detector_rows"] = student_rows
    result["detector_metric_profile"] = metric_profile
    result["scorer_payload"] = {
        "metadata": dict(shared_metrics),
        "chargeable_detector_rows": student_rows,
        "family_counts": family_counts,
        "capacity_counts": capacity_counts,
        "criterion_counts": criterion_counts,
        "sentence_count": shared_metrics["sentence_count"],
        "word_count": shared_metrics["word_count"],
        "paragraph_count": shared_metrics["paragraph_count"],
        "premium_metric_profile_mapped_metrics": {
            "shared": shared_block,
            "word_count": shared_metrics["word_count"],
            "sentence_count": shared_metrics["sentence_count"],
            "paragraph_count": shared_metrics["paragraph_count"],
            "task_schema_status": shared_metrics["task_schema_status"],
            "task_schema_confidence": shared_metrics["task_schema_confidence"],
        },
    }
    ep = result.get("evaluator_payload") or {}
    ep.setdefault("all_detector_evidence", student_rows)
    result["evaluator_payload"] = ep
    result["metadata_quality"] = {
        "word_count_positive": shared_metrics["word_count"] > 0,
        "sentence_count_positive": shared_metrics["sentence_count"] > 0,
        "paragraph_count_positive": shared_metrics["paragraph_count"] > 0,
        "length_metadata_complete": all([
            shared_metrics["word_count"] > 0,
            shared_metrics["sentence_count"] > 0,
            shared_metrics["paragraph_count"] > 0,
        ]),
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "wrapped_engine": "det_vip_v18d_2.py::PremiumDetectorV9",
        "wrapped_engine_version": det_result.get("run", {}).get("engine_version"),
        "created_at": now_iso(),
        "detector_mode": f"det_vip_llm_{'enabled_gpt4o_mini' if llm_enabled else 'disabled'}_via_bridge",
        "metadata_contract": {
            "detector_provides_scorer_length_metadata": True,
            "required_fields": ["word_count", "sentence_count", "paragraph_count"],
            "metadata_locations": [
                "results[].word_count",
                "results[].sentence_count",
                "results[].paragraph_count",
                "results[].metadata",
                "results[].generated_metadata",
                "results[].detector_metric_profile.shared",
                "results[].scorer_payload.metadata",
                "results[].scorer_payload.premium_metric_profile_mapped_metrics.shared",
            ],
        },
        "batch_id": (det_result.get("identity") or {}).get("batch_id") or (det_result.get("run") or {}).get("run_id"),
        "student_id": submission["student_id"],
        "result_count": 1,
        "failure_count": 0,
        "summary_metadata": dict(shared_metrics),
        "results": [result],
        "failures": [],
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="CLI bridge for det_vip_v18d_2.py's PremiumDetectorV9 (LLM-based detector, gpt-4o-mini default).")
    ap.add_argument("--input", "-i", required=True)
    ap.add_argument("--output", "-o", required=True)
    ap.add_argument("--essay-index", type=int, default=0)
    ap.add_argument("--pretty", action="store_true")
    ap.add_argument("--require-llm", action="store_true", help="Enable det_vip's LLM passes (gpt-4o-mini by default via VIP_CHEAP_MODEL). Off by default -- rule/spaCy/LT passes only.")
    ap.add_argument("--benchmark-mode", action="store_true", help="Passthrough to PremiumDetectorV9.analyze(benchmark_mode=...).")
    ap.add_argument("--allow-over-word-limit", action="store_true", default=True, help="Do not let det_vip's own 300-word ceiling reject the essay (Gold has its own length gating elsewhere). On by default.")
    ap.add_argument("--enforce-word-limit", dest="allow_over_word_limit", action="store_false", help="Let det_vip's built-in 300-word ceiling reject essays over the limit.")
    ap.add_argument("--resource-dirs", nargs="*", default=None, help="Passthrough to PremiumDetectorV9(resource_dirs=...).")
    ap.add_argument("--registry-dirs", nargs="*", default=None, help="Passthrough to PremiumDetectorV9(registry_dirs=...).")
    ap.add_argument("--engine-module-dir", required=False, help="Directory containing det_vip_v18d_2.py, if not already importable.")
    args = ap.parse_args(argv)

    if args.engine_module_dir:
        sys.path.insert(0, args.engine_module_dir)
    try:
        from det_vip_v18d_2 import PremiumDetectorV9
    except ImportError as exc:
        raise SystemExit(
            f"Could not import PremiumDetectorV9 from det_vip_v18d_2.py "
            f"(pass --engine-module-dir if it isn't next to this bridge): {exc}"
        )

    raw = read_json(args.input)
    submission = normalize_submission(raw, essay_index=args.essay_index)

    det = PremiumDetectorV9(args.resource_dirs, args.registry_dirs)
    det_result = det.analyze(
        essay_id=submission["essay_id"],
        essay_text=submission["essay_text"],
        prompt_text=submission["prompt_text"],
        metadata={"student_id": submission["student_id"], "source": "gold_pipeline"},
        require_llm=bool(args.require_llm),
        benchmark_mode=bool(args.benchmark_mode),
        allow_over_word_limit=bool(args.allow_over_word_limit),
    )

    out = build_bridge_output(det_result, submission, llm_enabled=bool(args.require_llm))
    write_json(args.output, out, pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
