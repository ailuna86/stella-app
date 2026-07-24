#!/usr/bin/env python3
"""
Priority Output Normalizer v1.4.4 — standalone
==============================================

New in v1.4.4 (this version):
Fixes a confirmed bug in v1.4.3's build(): focus_areas — what Directive,
Writing Coach's mission selection, and LIE's next_best_action all actually
read to decide what to recommend — was built ONLY from ErrorMap
(build_focus_from_errormap), even though two other real signal sources are
computed upstream and were never wired in:

  1. Evaluator's own holistic competence judgment. Evaluator is the only
     engine that judges skills like argumentation/organization rather than
     counting concrete errors. Its strengths already reach Priority Engine's
     "strengths" list via evaluator_payload.strengths_profile (see
     priority_engine_v4_4_selfcontained.py extract_strengths_profile() /
     strengths()) — but there was no equivalent path for Evaluator-derived
     WEAKNESSES to reach focus_areas. Confirmed by reading a real
     07_evaluator_output.json: the real weakness-equivalent field is
     consumer_payloads.writing_coach_payload.development_target_signals
     (skill_signal == "development_target", each carrying a priority_index)
     plus consumer_payloads.writing_coach_payload.gap_signals (gap_type:
     absence / quality / incomplete_execution). These are graded per-skill
     items in evaluator/domain vocabulary (e.g. "Organization",
     "Argumentation"), NOT ErrorMap's family/capacity_domain vocabulary — a
     real competence gap here (e.g. weak argumentation) can exist with ZERO
     matching ErrorMap rows.

  2. The LRET / Vocabulary Coach aggregated lexical signal added in
     priority_input_builder_standalone_v1_4_9.py (--lret/--vocab-ledger).
     IMPORTANT, confirmed by reading both engines end-to-end (not assumed):
     this signal's WEAKNESS/need side (family-level needs_work_score/status
     from priority_input's lexical_coach_signal.families) is NOT copied
     anywhere into Priority Engine's raw output. Only the STRENGTH side
     (via _strengths_from_lexical_signal -> evaluator_payload.strengths_profile
     -> Priority Engine's "strengths" list) reaches `priority`. So the
     `priority` argument this file already receives cannot supply the
     weakness signal at all — a NEW --priority-input argument (the file
     priority_input_builder_standalone_v1_4_9.py writes, already produced
     one pipeline stage earlier and still on disk) is required to read
     lexical_coach_signal.families directly. This is documented here because
     it is a real, verified discovery, not an assumption from the spec.

Merge/rank/dedup strategy (see merge_and_rank_focus()):
ErrorMap (countable errors), Evaluator (holistic per-skill competence
judgment), and the LRET/Vocab-Coach lexical aggregation are three genuinely
different taxonomies. They are NEVER silently conflated into one entry —
each keeps its own capacity_domain namespace ("<capacity>" for ErrorMap,
"evaluator_<domain>" for Evaluator, "lexical_family_<family>" for the
lexical signal) and its own evidence_source tag. All three are ranked
together in ONE focus_areas list using the SAME priority_level vocabulary
already established by build_focus_from_errormap (very_high/high/medium/
monitor): tier first, then — as a tie-break only, not a preference that
ever removes a candidate — ErrorMap before Evaluator before the lexical
signal (ErrorMap is concrete/countable evidence, so ties default to it).
Items that already reflect the same criterion are cross-referenced via a
new `related_focus_ranks` field for transparency; nothing is suppressed or
merged away, because ErrorMap and Evaluator/lexical evidence are
complementary, not redundant, even when they share a criterion.

Why it exists (unchanged from v1.4.3):
Some Priority Engine versions can output UNKNOWN_SKILL when detector-family
names do not match its registry. This bridge does not invent essay-specific
rules; it uses universal ErrorMap capacity_domain/family evidence (plus, as
of v1.4.4, Evaluator competence evidence and the LRET/Vocab-Coach lexical
signal) to build a safe focus_areas list for downstream routing.

Boundary:
- Does not detect errors.
- Does not score essays.
- Does not generate feedback, exercises, LRET labels, or coaching tasks.
- Only normalizes priority/evaluator/lexical-signal evidence into a stable
  downstream contract.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = "PRIORITY_NORMALIZED_V1_4_4"
ENGINE_ID = "VA_STELLA_PRIORITY_OUTPUT_NORMALIZER"
ENGINE_VERSION = "1.4.4-adds-evaluator-weakness-and-lexical-signal-focus-areas"

CAPACITY_TO_SKILL = {
    "sentence_control": ("sentence_control", "Sentence Control", "grammar"),
    "lexical_precision": ("lexical_precision", "Lexical Precision", "lexical_resource"),
    "academic_style": ("academic_style", "Academic Style", "lexical_resource"),
    "argument_development": ("argument_development", "Argument Development", "task_response"),
    "cohesion_control": ("cohesion_control", "Cohesion Control", "coherence_cohesion"),
    "task_response_control": ("task_response_control", "Task Response Control", "task_response"),
}
CRITERION_BY_FAMILY_PREFIX = {
    "G_": "grammar",
    "L_": "lexical_resource",
    "S_": "lexical_resource",
    "A_": "task_response",
    "C_": "coherence_cohesion",
}
SEVERITY_WEIGHT = {"critical": 1.4, "high": 1.25, "medium": 1.0, "low": 0.55}
SERVICE_BY_CAPACITY = {
    "sentence_control": "writing_coach",
    "argument_development": "writing_coach",
    "cohesion_control": "practice",
    "lexical_precision": "lret",
    "academic_style": "lret",
    "task_response_control": "writing_coach",
}

# ── v1.4.4: Evaluator competence-judgment vocabulary ─────────────────────────
# Domain names as produced by va_premium_evaluator_v8_x_wke_standalone.py's
# DOMAIN_CODE_MAP (confirmed by reading that file directly). Mapped to the
# same 4-value criterion vocabulary build_focus_from_errormap() already uses,
# so evaluator-derived and errormap-derived focus entries are comparable at
# the criterion level even though their capacity_domain namespaces differ.
EVALUATOR_DOMAIN_TO_CRITERION = {
    "Task Understanding": "task_response",
    "Content Development": "task_response",
    "Reasoning Competence": "task_response",
    "Information Processing": "task_response",
    "Thinking Competence": "task_response",
    "Argumentation": "task_response",
    "Organization": "coherence_cohesion",
    "Cohesion": "coherence_cohesion",
    "Lexical Control": "lexical_resource",
    "Advanced Lexical Competence": "lexical_resource",
    "Style & Reader Impact": "lexical_resource",
    "Grammar Production": "grammar",
    "Revision & Self-Editing": "unknown",
}
CRITERION_TO_SERVICE = {
    "grammar": "writing_coach",
    "lexical_resource": "lret",
    "task_response": "writing_coach",
    "coherence_cohesion": "practice",
    "unknown": "writing_coach",
}
# Provisional thresholds on Evaluator's own priority_index (skill_observation_
# profile / development_target_signals). NOT tuned from a labeled dataset —
# same caveat priority_input_builder_standalone_v1_4_9.py documents for its
# own LRET_STRENGTH_SCORE_THRESHOLD/VOCAB_STRENGTH_MIN_ATTEMPTS. Real values
# observed while verifying this file ranged ~0.04-0.25; revisit once more
# essays accumulate.
EVALUATOR_PRIORITY_HIGH = 0.20
EVALUATOR_PRIORITY_MEDIUM = 0.10

# ── v1.4.4: LRET / Vocabulary Coach lexical-signal vocabulary ───────────────
# Mirrors priority_input_builder_standalone_v1_4_9.py's FAMILY_SKILL_TOKEN
# exactly (copied, not imported, per this project's standalone-no-imports
# convention) so a family bucket maps to the same skill token everywhere.
LEXICAL_FAMILY_SKILL_TOKEN = {
    "single_word": "LEXICAL_FORM_CONTROL",
    "collocation_phrase": "COLLOCATION_CONTROL",
    "overall_lexical_control": "LEXICAL_CONTROL",
    "meaning_clarity": "SEMANTIC_PHRASE_CONTROL",
    "other_lexical": "LEXICAL_CONTROL",
}

TIER_RANK = {"very_high": 0, "high": 1, "medium": 2, "monitor": 3}
EVIDENCE_SOURCE_ORDER = {
    "errormap_detector_evidence": 0,
    "evaluator_competence_judgment": 1,
    "lret_vocab_coach_lexical_signal": 2,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: str) -> Any:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(path: str, data: Any, pretty: bool = False) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2 if pretty else None) + "\n", encoding="utf-8")


def is_unknown(value: Any) -> bool:
    return str(value or "").upper().startswith("UNKNOWN")


def _safe_num(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _domain_slug(domain: str) -> str:
    text = str(domain or "unknown").strip().lower().replace("&", "and")
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or "unknown"


def raw_priority_usable(priority: Dict[str, Any]) -> bool:
    candidates: List[Dict[str, Any]] = []
    if isinstance(priority.get("primary_limiter"), dict):
        candidates.append(priority["primary_limiter"])
    if isinstance(priority.get("results"), list) and priority["results"]:
        first = priority["results"][0]
        if isinstance(first, dict) and isinstance(first.get("primary_limiter"), dict):
            candidates.append(first["primary_limiter"])
        for item in (first.get("skill_profiles") if isinstance(first, dict) else []) or []:
            if isinstance(item, dict):
                candidates.append(item)
    for item in candidates:
        skill_values = [item.get("skill"), item.get("skill_tag"), item.get("student_label"), item.get("rubric")]
        if any(is_unknown(v) for v in skill_values):
            continue
        if item.get("skill") or item.get("skill_tag") or item.get("capacity_domain"):
            return True
    return False


def criterion_for_family(family: str) -> str:
    fam = str(family or "")
    for prefix, criterion in CRITERION_BY_FAMILY_PREFIX.items():
        if fam.startswith(prefix):
            return criterion
    return "unknown"


def collect_errors(errormap: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = errormap.get("errors") if isinstance(errormap, dict) else []
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def capacity_from_error(row: Dict[str, Any]) -> str:
    cap = str(row.get("capacity_domain") or "").strip()
    if cap:
        return cap
    criterion = str(row.get("criterion") or "").strip()
    fam = str(row.get("family") or "")
    if criterion == "grammar" or fam.startswith("G_"):
        return "sentence_control"
    if criterion == "lexical_resource" or fam.startswith("L_"):
        return "lexical_precision"
    if criterion == "academic_style" or fam.startswith("S_"):
        return "academic_style"
    if criterion == "argumentation" or fam.startswith("A_"):
        return "argument_development"
    if criterion == "cohesion_coherence" or fam.startswith("C_"):
        return "cohesion_control"
    return "task_response_control"


def severity_weight(row: Dict[str, Any]) -> float:
    return SEVERITY_WEIGHT.get(str(row.get("severity") or "").lower(), 1.0)


def build_focus_from_errormap(errormap: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = collect_errors(errormap)
    by_capacity: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("chargeable") is False:
            continue
        by_capacity[capacity_from_error(row)].append(row)

    focus: List[Dict[str, Any]] = []
    for cap, cap_rows in by_capacity.items():
        skill_id, label, default_criterion = CAPACITY_TO_SKILL.get(cap, (cap, cap.replace("_", " ").title(), "unknown"))
        fam_counts = Counter(str(r.get("family") or "UNKNOWN_FAMILY") for r in cap_rows)
        weighted_pressure = round(sum(severity_weight(r) for r in cap_rows), 3)
        evidence_samples = []
        for r in cap_rows[:5]:
            evidence_samples.append({
                "error_id": r.get("error_id"),
                "source_row_id": r.get("source_row_id"),
                "family": r.get("family"),
                "surface_quote": r.get("surface_quote"),
                "sentence_index": r.get("sentence_index"),
                "severity": r.get("severity"),
                "confidence": r.get("confidence"),
            })
        top_families = [fam for fam, _ in fam_counts.most_common(5)]
        criterion = default_criterion
        if top_families:
            criterion = criterion_for_family(top_families[0]) if criterion == "unknown" else criterion
        focus.append({
            "rank": 0,
            "capacity_domain": cap,
            "skill_tag": skill_id,
            "skill_id": skill_id,
            "student_label": label,
            "criterion": criterion,
            "evidence_count": len(cap_rows),
            "weighted_pressure": weighted_pressure,
            "priority_level": "very_high" if weighted_pressure >= 10 else "high" if weighted_pressure >= 6 else "medium" if weighted_pressure >= 3 else "monitor",
            "top_families": top_families,
            "family_counts": dict(fam_counts.most_common()),
            "recommended_service": SERVICE_BY_CAPACITY.get(cap, "writing_coach"),
            "recommended_difficulty": "controlled" if weighted_pressure >= 6 else "guided",
            "priority_reason": "Selected from chargeable ErrorMap capacity evidence because downstream routing requires a stable skill focus.",
            "evidence_samples": evidence_samples,
            "evidence_source": "errormap_detector_evidence",
        })
    focus.sort(key=lambda x: (-float(x.get("weighted_pressure") or 0), -int(x.get("evidence_count") or 0), str(x.get("capacity_domain"))))
    for i, item in enumerate(focus, start=1):
        item["rank"] = i
    return focus


# ── v1.4.4: Evaluator competence-judgment focus (real weakness signal) ─────

def _writing_coach_payload(evaluator_payload: Dict[str, Any]) -> Dict[str, Any]:
    cp = evaluator_payload.get("consumer_payloads") if isinstance(evaluator_payload, dict) else None
    if isinstance(cp, dict) and isinstance(cp.get("writing_coach_payload"), dict):
        return cp["writing_coach_payload"]
    return {}


def build_focus_from_evaluator(evaluator_payload: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Derives focus-area candidates from Evaluator's own holistic competence
    judgment (consumer_payloads.writing_coach_payload.development_target_signals
    + gap_signals — real field names/paths confirmed against a real
    07_evaluator_output.json, not assumed). These are genuine skill/criterion
    gaps Evaluator judged directly; they are NOT filtered through ErrorMap and
    can therefore surface a weakness (e.g. weak argumentation, weak
    organization) that no concrete Detector-catchable error would ever
    produce. Grouped per Evaluator "domain" (its own vocabulary — Argumentation,
    Organization, etc.) rather than per individual skill_id, mirroring how
    build_focus_from_errormap groups per capacity_domain rather than per row.
    """
    if not isinstance(evaluator_payload, dict) or not evaluator_payload:
        return []
    wcp = _writing_coach_payload(evaluator_payload)
    targets = wcp.get("development_target_signals") if isinstance(wcp.get("development_target_signals"), list) else []
    gaps = wcp.get("gap_signals") if isinstance(wcp.get("gap_signals"), list) else []
    gaps_by_skill: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for g in gaps:
        if isinstance(g, dict) and g.get("skill_id"):
            gaps_by_skill[str(g["skill_id"])].append(g)

    by_domain: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for t in targets:
        if not isinstance(t, dict):
            continue
        domain = str(t.get("domain") or "").strip() or "Unknown"
        by_domain[domain].append(t)

    focus: List[Dict[str, Any]] = []
    for domain, items in by_domain.items():
        items_sorted = sorted(items, key=lambda x: -(_safe_num(x.get("priority_index")) or 0))
        max_pi = max((_safe_num(x.get("priority_index")) or 0.0) for x in items_sorted)
        criterion = EVALUATOR_DOMAIN_TO_CRITERION.get(domain, "unknown")
        if max_pi >= EVALUATOR_PRIORITY_HIGH:
            priority_level = "high"
        elif max_pi >= EVALUATOR_PRIORITY_MEDIUM:
            priority_level = "medium"
        else:
            priority_level = "monitor"
        top = items_sorted[:5]
        evidence_samples = []
        for it in top:
            sid = str(it.get("skill_id") or "")
            gap = (gaps_by_skill.get(sid) or [{}])[0]
            evidence_samples.append({
                "skill_id": it.get("skill_id"),
                "skill_name": it.get("skill_name"),
                "status": it.get("status"),
                "capacity_signal": it.get("capacity_signal"),
                "priority_index": it.get("priority_index"),
                "diagnostic_confidence": it.get("diagnostic_confidence"),
                "evidence_strength": it.get("evidence_strength"),
                "gap_type": gap.get("gap_type"),
                "gap_note": gap.get("gap_note"),
            })
        cap = "evaluator_" + _domain_slug(domain)
        skill_ref = top[0].get("skill_id") if top and isinstance(top[0].get("skill_id"), str) else cap
        focus.append({
            "rank": 0,
            "capacity_domain": cap,
            "skill_tag": skill_ref,
            "skill_id": skill_ref,
            "student_label": domain,
            "criterion": criterion,
            "evidence_count": len(items_sorted),
            "weighted_pressure": round(max_pi * 10, 3),
            "raw_evaluator_priority_index": round(max_pi, 4),
            "priority_level": priority_level,
            "top_families": [str(it.get("skill_id")) for it in top if it.get("skill_id")],
            "family_counts": {},
            "recommended_service": CRITERION_TO_SERVICE.get(criterion, "writing_coach"),
            "recommended_difficulty": "controlled" if priority_level == "high" else "guided",
            "priority_reason": "Selected from Evaluator's own skill_observation_profile development_target_signals (holistic competence judgment) because ErrorMap alone would not surface this gap.",
            "evidence_samples": evidence_samples,
            "evidence_source": "evaluator_competence_judgment",
        })
    return focus


def build_supporting_strengths_from_evaluator(evaluator_payload: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """v1.4.4: surfaces Evaluator's current_strength_signals in this file's
    own output too, for visibility/consistency — but deliberately NOT as
    focus_areas entries. focus_areas is downstream-consumed as "what needs
    work" (Directive/Writing Coach mission selection/LIE next_best_action);
    injecting a strength there would corrupt that semantic. Evaluator
    strengths already have a real path to Priority Engine's own "strengths"
    list via evaluator_payload.strengths_profile (confirmed in
    priority_engine_v4_4_selfcontained.py); this list is additive evidence,
    not a routing input.
    """
    if not isinstance(evaluator_payload, dict) or not evaluator_payload:
        return []
    wcp = _writing_coach_payload(evaluator_payload)
    strengths = wcp.get("current_strength_signals") if isinstance(wcp.get("current_strength_signals"), list) else []
    out = []
    for s in strengths[:8]:
        if not isinstance(s, dict):
            continue
        out.append({
            "skill_id": s.get("skill_id"),
            "skill_name": s.get("skill_name"),
            "domain": s.get("domain"),
            "status": s.get("status"),
            "capacity_signal": s.get("capacity_signal"),
            "diagnostic_confidence": s.get("diagnostic_confidence"),
            "evidence_strength": s.get("evidence_strength"),
            "source": "evaluator_writing_coach_payload.current_strength_signals",
        })
    return out


# ── v1.4.4: LRET / Vocabulary Coach lexical-signal focus (real weakness) ───

def _first_lexical_signal_record(priority_input: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(priority_input, dict):
        return {}
    results = priority_input.get("results")
    if isinstance(results, list) and results:
        for r in results:
            if isinstance(r, dict) and isinstance(r.get("lexical_coach_signal"), dict):
                return r
        first = results[0]
        return first if isinstance(first, dict) else {}
    return priority_input


def build_focus_from_lexical_signal(priority_input: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Derives focus-area candidates from the LRET/Vocabulary Coach
    aggregated family-level lexical signal built by
    priority_input_builder_standalone_v1_4_9.py's _build_lexical_coach_signal
    (single_word / collocation_phrase / overall_lexical_control /
    meaning_clarity families with needs_work_score/status).

    IMPORTANT (verified, not assumed): this signal is read from the
    priority_input artifact, NOT the `priority` (Priority Engine raw output)
    argument this file already receives — Priority Engine's Essay.raw carries
    the full record but its analyze_essay() never re-serializes
    lexical_coach_signal into its own JSON output, so `priority` cannot
    supply this data at all. Only the STRENGTH side of this same signal
    reaches `priority` (via evaluator_payload.strengths_profile ->
    strengths()). Hence the new --priority-input CLI argument.

    Thresholds mirror the aggregator's OWN pre-computed combined_priority_flag
    / status fields exactly (no new magic numbers, no re-deriving verdict
    weights that priority_input_builder_standalone_v1_4_9.py already applied,
    including its documented needs_review exclusion and its
    attempted_incorrectly(2.0)/used_but_awkward(1.0) weighting).
    """
    rec = _first_lexical_signal_record(priority_input) if priority_input else {}
    lcs = rec.get("lexical_coach_signal") if isinstance(rec, dict) else None
    if not isinstance(lcs, dict):
        return []
    families = lcs.get("families") if isinstance(lcs.get("families"), dict) else {}

    focus: List[Dict[str, Any]] = []
    for fam, data in families.items():
        if not isinstance(data, dict):
            continue
        lret_fam = data.get("lret") if isinstance(data.get("lret"), dict) else {}
        vocab_fam = data.get("vocab_coach") if isinstance(data.get("vocab_coach"), dict) else {}
        combined_flag = bool(data.get("combined_priority_flag"))
        vocab_status = vocab_fam.get("status")
        need_score = _safe_num(lret_fam.get("mean_need_score"))

        if combined_flag:
            priority_level = "high"
        elif vocab_status == "monitor":
            priority_level = "medium"
        elif need_score is not None and need_score > 0:
            priority_level = "monitor"
        else:
            continue  # no actionable weakness for this family (functional/insufficient_data/no need signal)

        evidence_samples = []
        for s in (lret_fam.get("need_signals") or []):
            if isinstance(s, dict):
                evidence_samples.append({"source": "lret_skill_signal", **s})
        if vocab_fam:
            evidence_samples.append({
                "source": "vocab_coach_ledger",
                "status": vocab_fam.get("status"),
                "needs_work_score": vocab_fam.get("needs_work_score"),
                "needs_work_ratio": vocab_fam.get("needs_work_ratio"),
                "attempts_considered": vocab_fam.get("attempts_considered"),
                "attempted_incorrectly_count": vocab_fam.get("attempted_incorrectly_count"),
                "used_but_awkward_count": vocab_fam.get("used_but_awkward_count"),
            })
        needs_work_ratio = _safe_num(vocab_fam.get("needs_work_ratio")) or 0.0
        weighted_pressure = round(((need_score or 0.0) + needs_work_ratio) * 5, 3)
        skill_token = LEXICAL_FAMILY_SKILL_TOKEN.get(fam, "LEXICAL_CONTROL")
        focus.append({
            "rank": 0,
            "capacity_domain": "lexical_family_" + fam,
            "skill_tag": skill_token,
            "skill_id": skill_token,
            "student_label": fam.replace("_", " ").title() + " (Lexical)",
            "criterion": "lexical_resource",
            "evidence_count": max(len(evidence_samples), 1),
            "weighted_pressure": weighted_pressure,
            "priority_level": priority_level,
            "top_families": [fam],
            "family_counts": {fam: max(len(evidence_samples), 1)},
            "recommended_service": "lret",
            "recommended_difficulty": "controlled" if priority_level == "high" else "guided",
            "priority_reason": "Selected from the LRET/Vocabulary Coach aggregated family-level lexical signal (priority_input_builder_standalone_v1_4_9.py lexical_coach_signal) because ErrorMap does not aggregate cross-session lexical need/verdict history.",
            "evidence_samples": evidence_samples,
            "evidence_source": "lret_vocab_coach_lexical_signal",
        })
    return focus


def merge_and_rank_focus(
    errormap_focus: List[Dict[str, Any]],
    evaluator_focus: List[Dict[str, Any]],
    lexical_focus: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Merges the three focus-area sources into one ranked list.

    Design (see module docstring for the full rationale):
    - Never conflate: each source keeps its own capacity_domain namespace and
      an explicit evidence_source tag. No item from one source is merged
      into or replaces an item from another.
    - Rank uniformly by the SAME priority_level tier vocabulary
      (very_high > high > medium > monitor) across all three sources.
    - Within a tier, ties break errormap-first, then evaluator, then lexical
      signal (ErrorMap is concrete/countable evidence; this is a tie-break
      only, never a filter — nothing is dropped because of it).
    - related_focus_ranks cross-references other entries sharing the same
      criterion, purely for transparency; it never suppresses an entry.
    """
    tagged: List[Dict[str, Any]] = []
    for item in errormap_focus:
        it = dict(item)
        it.setdefault("evidence_source", "errormap_detector_evidence")
        tagged.append(it)
    tagged.extend(dict(x) for x in evaluator_focus)
    tagged.extend(dict(x) for x in lexical_focus)

    tagged.sort(key=lambda x: (
        TIER_RANK.get(x.get("priority_level"), 4),
        EVIDENCE_SOURCE_ORDER.get(x.get("evidence_source"), 9),
        -float(x.get("weighted_pressure") or 0),
        -int(x.get("evidence_count") or 0),
        str(x.get("capacity_domain")),
    ))
    for i, item in enumerate(tagged, start=1):
        item["rank"] = i

    by_criterion: Dict[str, List[int]] = defaultdict(list)
    for item in tagged:
        crit = item.get("criterion")
        if crit and crit != "unknown":
            by_criterion[crit].append(item["rank"])
    for item in tagged:
        crit = item.get("criterion")
        item["related_focus_ranks"] = [r for r in by_criterion.get(crit, []) if r != item["rank"]] if crit and crit != "unknown" else []
    return tagged


def score_context(score_contract: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(score_contract, dict):
        return {}
    return {
        "released_score": score_contract.get("released_score"),
        "score_status": score_contract.get("score_status"),
        "score_confidence": score_contract.get("score_confidence"),
        "progress_tracking_allowed": score_contract.get("progress_tracking_allowed"),
        "lie_update_allowed": score_contract.get("lie_update_allowed"),
    }


def build(
    priority: Dict[str, Any],
    errormap: Dict[str, Any],
    score_contract: Optional[Dict[str, Any]],
    evaluator: Optional[Dict[str, Any]] = None,
    priority_input: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    errormap_focus = build_focus_from_errormap(errormap)
    evaluator_focus = build_focus_from_evaluator(evaluator) if evaluator else []
    lexical_focus = build_focus_from_lexical_signal(priority_input) if priority_input else []
    focus = merge_and_rank_focus(errormap_focus, evaluator_focus, lexical_focus)
    supporting_strengths = build_supporting_strengths_from_evaluator(evaluator) if evaluator else []
    primary = focus[0] if focus else None
    usable = raw_priority_usable(priority)
    return {
        "schema_version": SCHEMA_VERSION,
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "boundary": "Priority/Evaluator/lexical-signal contract normalization only; no new scoring, detection, teaching, or exercise generation.",
        "source_priority_schema": priority.get("schema_version") if isinstance(priority, dict) else None,
        "source_priority_usable_without_repair": usable,
        "normalization_reason": "raw_priority_usable" if usable else "raw_priority_unknown_skill_or_missing_focus_repaired_from_errormap_capacity_evidence",
        "score_context": score_context(score_contract),
        "focus_areas": focus,
        "primary_focus": primary,
        "supporting_strengths": supporting_strengths,
        "focus_area_source_counts": {
            "errormap_detector_evidence": len(errormap_focus),
            "evaluator_competence_judgment": len(evaluator_focus),
            "lret_vocab_coach_lexical_signal": len(lexical_focus),
        },
        "evaluator_input_supplied": bool(evaluator),
        "priority_input_supplied": bool(priority_input),
        "gold_learning_directive_seed": {
            "next_best_skill": primary.get("skill_tag") if primary else None,
            "next_best_capacity_domain": primary.get("capacity_domain") if primary else None,
            "recommended_service": primary.get("recommended_service") if primary else None,
            "priority_level": primary.get("priority_level") if primary else None,
        },
        "quality_flags": {
            "has_focus_areas": bool(focus),
            "unknown_skill_remaining": any(is_unknown(x.get("skill_tag")) or is_unknown(x.get("student_label")) for x in focus),
            "evaluator_weakness_surfaced": bool(evaluator_focus),
            "lexical_signal_weakness_surfaced": bool(lexical_focus),
        },
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Normalize Priority Engine + Evaluator + lexical-signal evidence into directive-ready focus areas.")
    ap.add_argument("--priority", required=True)
    ap.add_argument("--errormap", required=True)
    ap.add_argument("--score-contract")
    ap.add_argument("--evaluator", required=False, help="Optional raw Evaluator/WKE output JSON (v8.3+; 07_evaluator_output.json shape) for holistic competence-gap focus areas.")
    ap.add_argument("--priority-input", required=False, help="Optional priority_input_builder_standalone_v1_4_9.py output JSON, read for lexical_coach_signal (LRET/Vocab Coach aggregated lexical weakness).")
    ap.add_argument("--output", "-o", required=True)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)
    priority = read_json(args.priority)
    errormap = read_json(args.errormap)
    score_contract = read_json(args.score_contract) if args.score_contract else None
    evaluator = read_json(args.evaluator) if args.evaluator else None
    priority_input = read_json(args.priority_input) if args.priority_input else None
    out = build(priority, errormap, score_contract, evaluator, priority_input)
    write_json(args.output, out, pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
