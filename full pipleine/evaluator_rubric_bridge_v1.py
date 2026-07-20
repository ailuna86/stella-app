#!/usr/bin/env python3
"""
Evaluator -> Scorer Rubric Bridge v1 — standalone
==================================================

Gold v1.4.13 fix for stress-test Problem 2: premium_unified_scorer_v1_4_1_fixed.py's
task_response / coherence_cohesion content-quality sub-metrics (TR1-TR8, CC1-CC7)
were never populated by anything in the Gold pipeline, so they sat at their
hardcoded 0.55 default on every run regardless of actual essay content. A
3-essay stress test (weak/medium/strong, same prompt) showed these values
bit-for-bit identical across all three essays.

This bridge closes that gap using evidence the pipeline already computes: the
Evaluator's skill_observation_profile (121 skill entries with real per-essay
competence_vector dimensions in [0,1]).

IMPORTANT -- where this data actually needs to go (found by testing, not just
reading the code): premium_unified_scorer_v1_4_1_fixed.py's
_select_metric_source() tries FOUR sources in strict priority order and
returns the FIRST one that exists as a dict, full stop -- it never merges:
  1. record["scorer_payload"]["premium_metric_profile_mapped_metrics"]
  2. record["detector_metric_profile"]
  3. record["canonical_metric_profile"] / record["ielts_metric_profile"]
  4. record["premium_metric_profile"] (legacy names, translated via
     LEGACY_PREMIUM_MAP / _flatten_legacy_metric_profile())
Source #1 already exists on every record (scorer_input_evidence_guard_standalone_v1_4_7.py
always writes it), but only with word_count/semantic fields -- never TR/CC
content. Its mere existence short-circuits the chain before source #2 is ever
checked, even though source #2 (record["detector_metric_profile"]) often
already carries REAL, differentiated det_vip-computed values for TR5/TR6/TR7
(from det_vip's own layer0_idea_map component) that are currently being
silently discarded. Writing to source #4 (as an earlier version of this
script did) has zero effect, because source #1 always wins first.

This bridge therefore writes CANONICAL TRx/CCx-named fields directly into
source #1 (record["scorer_payload"]["premium_metric_profile_mapped_metrics"]),
merged with whatever real signal already exists in source #2
(record["detector_metric_profile"]) -- det_vip's own values win on overlapping
fields (they're a more direct signal), the Evaluator fills in every field
det_vip's layer0_idea_map doesn't cover. No scorer code changes needed; this
is a pure upstream-data fix.

Architecture note (why this runs where it does): the scorer needs the
Evaluator's signal, but the Evaluator's CLI historically took --scorer as a
required input for its own scorer_available context flag. Gold v1.4.13 moves
the evaluator stage to run BEFORE the scorer stage and makes evaluator's
--scorer optional (see evaluator_cli_bridge_standalone_v1_4_3.py v1.4.13
patch); the evaluator engine's own normalize_scorer_output(None) ->
{"available": False} path already tolerates this gracefully. This bridge
script is the new stage that sits between "evaluator" and "scorer" in
STAGE_ORDER.

Boundary:
- Does not evaluate writing itself (that's the Evaluator's job).
- Does not score (that's the Scorer's job).
- Only translates already-computed Evaluator evidence into a format the
  Scorer's existing (previously dead) backfill path already knows how to read.
- Values are only written when the source skill has real evidence
  (status != not_applicable_to_task_type, competence_vector dim is numeric,
  not None). Genuinely inapplicable/no-evidence dimensions are left unset so
  the scorer's own 0.55 default still applies for those specific fields --
  this bridge adds real signal, it does not force one where none exists.

Post-release-verification addition -- lexical_resource: a live 3-essay
re-run of the Problem 2 fix (this file, TR/CC only) confirmed
raw_criterion_quality.lexical_resource was still pinned at the scorer's
hardcoded 0.5645 default on every essay -- the exact same bug class,
just an uncovered rubric group -- and that this was very likely why the
medium and strong essays collapsed to an identical final overall_band.
build_lexical_resource_rubric() closes that gap the same way, sourced
from the Evaluator's "Lexical Control" / "Advanced Lexical Competence"
domain skills (see that function's own docstring for the domain-level
sharing behavior found there).
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "EVALUATOR_RUBRIC_BRIDGE_V1"
ENGINE_ID = "VA_STELLA_EVALUATOR_RUBRIC_BRIDGE"
ENGINE_VERSION = "1.0.0-standalone-no-imports"


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


def _index_skills(evaluator_output: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    sop = evaluator_output.get("skill_observation_profile") or []
    out: Dict[str, Dict[str, Any]] = {}
    for s in sop:
        if isinstance(s, dict) and s.get("skill_id"):
            out[s["skill_id"]] = s
    return out


def _cv(sop_by_id: Dict[str, Dict[str, Any]], skill_id: str, dim: str) -> Optional[float]:
    skill = sop_by_id.get(skill_id)
    if not skill:
        return None
    if skill.get("status") == "not_applicable_to_task_type":
        return None
    v = (skill.get("competence_vector") or {}).get(dim)
    return float(v) if isinstance(v, (int, float)) else None


def _avg(*vals: Optional[float]) -> Optional[float]:
    nums = [v for v in vals if isinstance(v, (int, float))]
    if not nums:
        return None
    return sum(nums) / len(nums)


# Maps scorer CANONICAL rubric field names (TRx/CCx, as read directly by
# CanonicalMetricProfile.build_profile_from_dict()) -> a function computing
# the value from the Evaluator's skill_observation_profile. Every source
# skill_id here is a real skill in VA_microskill_clustering_v3.json's ontology
# (121 skills). Left unset (returns None) when the underlying evidence is
# genuinely absent -- the scorer's own 0.55 default then still applies for
# that one field.
def build_task_response_rubric(sop: Dict[str, Dict[str, Any]]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    vals = {
        "TR1_prompt_part_coverage": _avg(
            _cv(sop, "identify_required_components", "component_coverage"),
            _cv(sop, "maintain_task_focus", "task_alignment"),
        ),
        "TR2_position_clarity": _avg(
            _cv(sop, "ws_proposition_clarity", "precision"),
            _cv(sop, "thesis_construction", "precision"),
        ),
        "TR3_position_consistency": _cv(sop, "arg_position_consistency", "relevance"),
        "TR4_relevance_ratio": _avg(
            _cv(sop, "arg_claim_relevance", "relevance"),
            _cv(sop, "maintain_task_focus", "focus"),
        ),
        "TR5_idea_extension_depth": _avg(
            _cv(sop, "reasoning_depth", "depth"),
            _cv(sop, "generate_relevant_ideas", "coverage"),
        ),
        "TR6_support_quality": _cv(sop, "support_quality", "specificity"),
        "TR7_conclusion_alignment": _avg(
            _cv(sop, "arg_conclusion_alignment", "closure"),
            _cv(sop, "ws_conclusion_alignment_struct", "closure"),
        ),
    }
    for k, v in vals.items():
        if v is not None:
            out[k] = round(v, 4)
    return out


def build_coherence_cohesion_rubric(sop: Dict[str, Dict[str, Any]]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    vals = {
        "CC1_global_logical_progression": _avg(
            _cv(sop, "progression_management", "progression"),
            _cv(sop, "information_flow", "progression"),
        ),
        "CC2_paragraph_topic_unity": _cv(sop, "topic_sentence_control", "focus"),
        "CC3_paragraphing_appropriacy": _cv(sop, "paragraph_balance", "balance"),
        "CC4_intra_paragraph_sequencing": _cv(sop, "logical_sequencing", "progression"),
        "CC5_inter_paragraph_transition_quality": _cv(sop, "transition_control", "transition_variety"),
        "CC6_reference_substitution_clarity": _cv(sop, "reference_management", "reference_clarity_proxy"),
        "CC7_cohesive_device_appropriacy": _cv(sop, "cohesion_without_overlinking", "connector_presence"),
    }
    for k, v in vals.items():
        if v is not None:
            out[k] = round(v, 4)
    return out


def build_lexical_resource_rubric(sop: Dict[str, Dict[str, Any]]) -> Dict[str, float]:
    """v1.4.13 addition (post-release-verification fix): lexical_resource was
    the one rubric group evaluator_rubric_bridge_v1.py's original version
    (Problem 2) didn't build a rubric for. Confirmed via a live 3-essay
    re-run that this left raw_criterion_quality.lexical_resource pinned at
    the scorer's hardcoded 0.5645 default on every essay -- same bug class
    as Problem 2, just an uncovered field -- and that this was very likely
    why the medium and strong essays collapsed to an identical final score
    (identical criteria_bands, identical overall_band).

    Source: the Evaluator's "Lexical Control" and "Advanced Lexical
    Competence" domains share ONE real, per-essay competence_vector across
    all of their skill_ids (confirmed directly: lexical_precision,
    collocation_control, topic_vocabulary_control, word_formation_control,
    lx_lexical_variation, lx_phrase_naturalness etc. all carry the identical
    {unit_presence, variety, specificity, naturalness_proxy,
    register_fit_proxy} vector within a given essay) -- it is a single
    domain-level lexical evidence cluster, not independently observed per
    skill. That's still genuine, differentiated per-essay signal (verified:
    variety/specificity/naturalness_proxy/register_fit_proxy all move
    between the weak/medium/strong essays), which is exactly what's missing
    for this rubric group.

    LR6_repetition_simplification_rate, LR8_spelling_impact and
    LR11_dynamic_multiword_density are deliberately left unset here --
    those are direct-count signals det_vip's own REPETITION/SPELLING
    families and lr_positive_signals block are architecturally the more
    direct source for (see det_vip_v18d_2.py's v18d.1 changelog); wiring
    those from det_vip's actual chargeable rows is a separate, future
    improvement, not part of this fix.
    """
    out: Dict[str, float] = {}
    vals = {
        "LR1_lexical_range": _cv(sop, "lexical_variety", "variety"),
        "LR2_topic_vocabulary_adequacy": _avg(
            _cv(sop, "topic_vocabulary_control", "variety"),
            _cv(sop, "topic_vocabulary_control", "register_fit_proxy"),
        ),
        "LR3_word_choice_precision": _cv(sop, "lexical_precision", "specificity"),
        "LR4_collocation_control": _avg(
            _cv(sop, "collocation_control", "naturalness_proxy"),
            _cv(sop, "collocation_control", "specificity"),
        ),
        "LR5_lexical_appropriacy_register": _cv(sop, "register_control", "register_fit_proxy"),
        "LR7_word_formation_accuracy": _cv(sop, "word_formation_control", "naturalness_proxy"),
        "LR9_semantic_phrase_naturalness": _avg(
            _cv(sop, "lx_phrase_naturalness", "naturalness_proxy"),
            _cv(sop, "semantic_compatibility", "naturalness_proxy"),
        ),
        "LR10_lexical_sophistication_index": _cv(sop, "lx_lexical_variation", "variety"),
    }
    for k, v in vals.items():
        if v is not None:
            out[k] = round(v, 4)
    return out


def merge_with_detector_metric_profile(evaluator_rubric: Dict[str, float], dmp_group: Dict[str, Any]) -> Dict[str, float]:
    """det_vip's own detector_metric_profile[group] values win on overlapping
    canonical fields (a more direct per-essay signal); the Evaluator fills in
    every field det_vip's layer0_idea_map doesn't cover."""
    merged = dict(evaluator_rubric)
    if isinstance(dmp_group, dict):
        for k, v in dmp_group.items():
            if k.startswith(("TR", "CC", "LR")) and isinstance(v, (int, float)):
                merged[k] = v
    return merged


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Bridge Evaluator skill_observation_profile into scorer-readable rubric metrics.")
    ap.add_argument("--evaluator", required=True, help="07_evaluator_output.json")
    ap.add_argument("--detector-for-scorer", required=True, help="01d_detector_for_scorer.json")
    ap.add_argument("--output", "-o", required=True)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)

    evaluator_output = read_json(args.evaluator)
    detector_for_scorer = read_json(args.detector_for_scorer)

    sop_by_id = _index_skills(evaluator_output)
    task_response_rubric = build_task_response_rubric(sop_by_id)
    coherence_cohesion_rubric = build_coherence_cohesion_rubric(sop_by_id)
    lexical_resource_rubric = build_lexical_resource_rubric(sop_by_id)

    eval_meta = evaluator_output.get("metadata") or {}
    essay_id = eval_meta.get("essay_id")
    student_id = eval_meta.get("student_id")

    def apply_to_record(r: Dict[str, Any]) -> None:
        dmp = r.get("detector_metric_profile") or {}
        merged_tr = merge_with_detector_metric_profile(task_response_rubric, dmp.get("task_response") or {})
        merged_cc = merge_with_detector_metric_profile(coherence_cohesion_rubric, dmp.get("coherence_cohesion") or {})
        merged_lr = merge_with_detector_metric_profile(lexical_resource_rubric, dmp.get("lexical_resource") or {})
        sp = r.get("scorer_payload")
        if not isinstance(sp, dict):
            sp = {}
            r["scorer_payload"] = sp
        mapped = sp.get("premium_metric_profile_mapped_metrics")
        if not isinstance(mapped, dict):
            mapped = {}
        existing_tr = mapped.get("task_response")
        if isinstance(existing_tr, dict):
            merged_tr = {**merged_tr, **{k: v for k, v in existing_tr.items() if isinstance(v, (int, float))}}
        existing_cc = mapped.get("coherence_cohesion")
        if isinstance(existing_cc, dict):
            merged_cc = {**merged_cc, **{k: v for k, v in existing_cc.items() if isinstance(v, (int, float))}}
        existing_lr = mapped.get("lexical_resource")
        if isinstance(existing_lr, dict):
            merged_lr = {**merged_lr, **{k: v for k, v in existing_lr.items() if isinstance(v, (int, float))}}
        mapped["task_response"] = merged_tr
        mapped["coherence_cohesion"] = merged_cc
        mapped["lexical_resource"] = merged_lr

    results = detector_for_scorer.get("results")
    matched_count = 0
    unmatched_essay_ids: List[str] = []
    if isinstance(results, list):
        for r in results:
            identity = r.get("identity") or {}
            if len(results) > 1 and essay_id and identity.get("essay_id") not in (None, essay_id):
                unmatched_essay_ids.append(identity.get("essay_id"))
                continue
            apply_to_record(r)
            matched_count += 1
    else:
        apply_to_record(detector_for_scorer)
        matched_count = 1

    detector_for_scorer["evaluator_rubric_bridge_audit"] = {
        "schema_version": SCHEMA_VERSION,
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "essay_id": essay_id,
        "student_id": student_id,
        "task_response_fields_populated": sorted(task_response_rubric.keys()),
        "coherence_cohesion_fields_populated": sorted(coherence_cohesion_rubric.keys()),
        "lexical_resource_fields_populated": sorted(lexical_resource_rubric.keys()),
        "task_response_fields_missing_evidence": sorted(
            set(["TR1_prompt_part_coverage", "TR2_position_clarity", "TR3_position_consistency",
                 "TR4_relevance_ratio", "TR5_idea_extension_depth", "TR6_support_quality",
                 "TR7_conclusion_alignment"])
            - set(task_response_rubric.keys())
        ),
        "coherence_cohesion_fields_missing_evidence": sorted(
            set(["CC1_global_logical_progression", "CC2_paragraph_topic_unity", "CC3_paragraphing_appropriacy",
                 "CC4_intra_paragraph_sequencing", "CC5_inter_paragraph_transition_quality",
                 "CC6_reference_substitution_clarity", "CC7_cohesive_device_appropriacy"])
            - set(coherence_cohesion_rubric.keys())
        ),
        "lexical_resource_fields_missing_evidence": sorted(
            set(["LR1_lexical_range", "LR2_topic_vocabulary_adequacy", "LR3_word_choice_precision",
                 "LR4_collocation_control", "LR5_lexical_appropriacy_register", "LR7_word_formation_accuracy",
                 "LR9_semantic_phrase_naturalness", "LR10_lexical_sophistication_index"])
            - set(lexical_resource_rubric.keys())
        ),
        "lexical_resource_fields_intentionally_not_sourced_from_evaluator": [
            "LR6_repetition_simplification_rate", "LR8_spelling_impact", "LR11_dynamic_multiword_density",
        ],
        "matched_results": matched_count,
        "unmatched_essay_ids": unmatched_essay_ids,
    }

    out_path = Path(args.output).resolve()
    write_json(out_path, detector_for_scorer, pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
