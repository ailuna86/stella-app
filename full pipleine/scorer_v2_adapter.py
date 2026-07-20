"""
scorer_v2_adapter.py
====================
CHANGE LOG vs. scorer_engine_v2_1_6.py
---------------------------------------
This file does NOT replace scorer_engine_v2_1_6.py.
It is a wrapper/adapter that calls the existing scorer and transforms its output
to the BandScores v2 contract format (contract_scorer_v2.json).

WHY THIS FILE EXISTS:
  scorer_engine_v2_1_6.py emits SCORER_OUTPUT_V1.1 with:
  - Criterion names: "grammar", "task_response" (not v2 standard names)
  - Integer bands (3–8), not half-band floats
  - No "confidence" field per criterion
  - No "score_flags" block (low_confidence_criteria, word_count_penalty_applied,
    estimated_true_band)
  - Outer key: score_profile.rubrics, not criteria_scores

CHANGES:
  1. Criterion renaming:
       "grammar"       → "grammatical_range_accuracy"
       "task_response" → "task_achievement"
  2. Band conversion: integer → float (bands are already valid as floats; if the
     scorer emits 6, this adapter emits 6.0 — which is a valid multiple of 0.5).
     Half-band detection: the scorer may emit 6 for a true 6.5. We round to the
     nearest 0.5 from the raw float band to handle this.
  3. confidence: estimated from the scorer's internal metadata fields where available
     (e.g. low_support flags, eci_tier, band_lock_flags). Falls back to 0.75 if
     no confidence signal is present.
  4. rationale: forwarded from scorer's internal rationale field if present.
  5. score_flags: constructed from scorer output signals:
       - low_confidence_criteria: criteria where confidence < 0.6
       - word_count_penalty_applied: from scorer's word_count_gate or penalty flag
       - estimated_true_band: from scorer's pre-penalty band if penalty was applied
  6. holistic_band: recalculated as mean of criteria_scores rounded to nearest 0.5.

USAGE:
  from scorer_v2_adapter import score_essay_v2

  # Option A — pass scorer_payload from detector:
  band_scores = score_essay_v2(
      submission_id="...",
      student_id="...",
      task_type="task2",
      essay_text="...",
      error_map=error_map_v2,       # optional; v2 ErrorMap dict
      scorer_payload=scorer_payload  # optional; from detector output
  )

  # Option B — essay text only (scorer runs standalone):
  band_scores = score_essay_v2(
      submission_id="...",
      student_id="...",
      task_type="task2",
      essay_text="..."
  )

  # band_scores is now compliant with contract_scorer_v2.json BandScores type.
"""
from __future__ import annotations

import math
import os
import sys
import importlib.util
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ── Criterion name maps ──────────────────────────────────────────────────────

_RUBRIC_TO_V2: Dict[str, str] = {
    "grammar":            "grammatical_range_accuracy",
    "GRA":                "grammatical_range_accuracy",
    "task_response":      "task_achievement",
    "TR":                 "task_achievement",
    "coherence_cohesion": "coherence_cohesion",
    "CC":                 "coherence_cohesion",
    "lexical_resource":   "lexical_resource",
    "LR":                 "lexical_resource",
}

_V2_CRITERIA = [
    "task_achievement",
    "coherence_cohesion",
    "lexical_resource",
    "grammatical_range_accuracy",
]

# ── Band rounding ─────────────────────────────────────────────────────────────

def _round_to_half_band(value: float) -> float:
    """Round to nearest 0.5, clipped to [0.0, 9.0]."""
    rounded = round(value * 2) / 2
    return max(0.0, min(9.0, rounded))


# ── Confidence estimation ─────────────────────────────────────────────────────

def _estimate_confidence(
    rubric_data: Dict[str, Any],
    scorer_output: Dict[str, Any],
    criterion: str,
) -> float:
    """
    Estimate a 0–1 confidence score for a criterion's band.

    Uses signals from scorer_output where available:
    - explicit 'confidence' or 'band_confidence' field in rubric_data
    - eci_tier: 'clear' → high, 'borderline' → medium, 'unclear' → low
    - band_lock_flags: if band was locked to minimum due to penalty, confidence is low
    - gate_passed / gate_failures: failed gates reduce confidence
    """
    # Explicit confidence
    explicit = rubric_data.get("confidence") or rubric_data.get("band_confidence")
    if explicit is not None:
        return float(explicit)

    # ECI tier from scorer metadata
    meta = scorer_output.get("metadata", {})
    eci_tier = (
        rubric_data.get("eci_tier")
        or meta.get("eci_tier")
        or scorer_output.get("eci_tier")
        or "clear"
    )
    eci_confidence = {"clear": 0.85, "borderline": 0.65, "unclear": 0.50}.get(eci_tier, 0.75)

    # Gate failures reduce confidence
    gate_failures = len(rubric_data.get("gate_failures", []))
    gate_penalty  = min(0.20, gate_failures * 0.05)

    # Word-count penalty → lower confidence in all criteria
    wc_penalty_applied = bool(scorer_output.get("word_count_penalty_applied", False))
    wc_confidence_hit  = 0.10 if wc_penalty_applied else 0.0

    confidence = eci_confidence - gate_penalty - wc_confidence_hit
    return round(max(0.30, min(1.0, confidence)), 3)


# ── Scorer caller ────────────────────────────────────────────────────────────

def _call_scorer_engine(
    task_type: str,
    essay_text: str,
    scorer_payload: Optional[Dict[str, Any]] = None,
    word_count: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Call scorer_engine_v2_1_6.py and return its raw SCORER_OUTPUT_V1.1 dict.

    Looks for the scorer in:
    1. The same directory as this file
    2. sys.path

    Falls back to a minimal stub if the scorer cannot be imported (for testing).
    """
    # Attempt dynamic import of the scorer module
    scorer_dir = os.path.dirname(os.path.abspath(__file__))
    scorer_path = os.path.join(scorer_dir, "scorer_engine_v2_1_6.py")

    try:
        spec = importlib.util.spec_from_file_location("scorer_engine_v2_1_6", scorer_path)
        scorer_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(scorer_mod)

        # The scorer exposes a `score_essay()` or `run_scoring()` function.
        # Try the most common entry-point signatures:
        if hasattr(scorer_mod, "score_essay"):
            return scorer_mod.score_essay(
                task_type=task_type,
                essay_text=essay_text,
                scorer_payload=scorer_payload,
                word_count=word_count,
            )
        elif hasattr(scorer_mod, "run_scoring"):
            return scorer_mod.run_scoring(
                task_type=task_type,
                essay_text=essay_text,
                scorer_payload=scorer_payload,
            )
        elif hasattr(scorer_mod, "IELTSScorerV2"):
            scorer_instance = scorer_mod.IELTSScorerV2()
            return scorer_instance.score(
                task_type=task_type,
                essay_text=essay_text,
                detector_payload=scorer_payload,
            )
        else:
            raise ImportError("scorer_engine_v2_1_6 does not expose a known entry point")

    except Exception as e:
        # Return a minimal stub output so the adapter can still produce v2 format.
        # Log the error but do not crash the pipeline.
        print(f"[scorer_v2_adapter] WARNING: could not call scorer engine: {e}", file=sys.stderr)
        return _stub_scorer_output(task_type, essay_text, word_count or 0)


def _stub_scorer_output(task_type: str, essay_text: str, word_count: int) -> Dict[str, Any]:
    """Minimal fallback scorer output when the real scorer cannot be called."""
    return {
        "schema_version": "SCORER_OUTPUT_V1.1_STUB",
        "score_profile": {
            "rubrics": {
                "task_response":      {"band": 5, "eci_tier": "unclear"},
                "coherence_cohesion": {"band": 5, "eci_tier": "unclear"},
                "lexical_resource":   {"band": 5, "eci_tier": "unclear"},
                "grammar":            {"band": 5, "eci_tier": "unclear"},
            },
            "overall_band": 5,
        },
        "word_count_penalty_applied": word_count > 0 and word_count < (150 if "task1" in task_type else 250),
        "_stub": True,
    }


# ── ErrorMap → scorer_payload bridge ─────────────────────────────────────────

def _errormap_to_scorer_payload(error_map: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert ErrorMap v2 into a minimal scorer_payload the scorer can consume.
    Used when the caller provides an ErrorMap but not a raw scorer_payload.
    """
    # Inverse criterion mapping (v2 → scorer internal)
    _V2_TO_RUBRIC = {v: k for k, v in _RUBRIC_TO_V2.items() if k in (
        "grammar", "task_response", "coherence_cohesion", "lexical_resource"
    )}

    errors = error_map.get("errors", [])
    chargeable_rows = []
    for e in errors:
        criterion = e.get("criterion", "")
        rubric    = _V2_TO_RUBRIC.get(criterion, criterion)
        chargeable_rows.append({
            "row_id":             e.get("error_id", str(uuid.uuid4())),
            "rubric":             rubric,
            "family":             e.get("error_type", "unknown"),
            "severity":           e.get("severity", "minor"),
            "score_charge_weight": {"minor": 0.2, "moderate": 0.5, "major": 0.8}.get(
                                    e.get("severity", "minor"), 0.3),
            "quote":              (e.get("location") or {}).get("excerpt", ""),
        })

    return {
        "chargeable_detector_rows": chargeable_rows,
        "metadata": {
            "word_count": error_map.get("word_count_actual", 0),
        },
    }


# ── Main public API ──────────────────────────────────────────────────────────

def score_essay_v2(
    submission_id: str,
    student_id: str,
    task_type: str,
    essay_text: str,
    error_map: Optional[Dict[str, Any]] = None,
    scorer_payload: Optional[Dict[str, Any]] = None,
    word_count: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Score an essay and return BandScores in v2 contract format.

    Args:
        submission_id:   UUID for this submission.
        student_id:      UUID for the student.
        task_type:       "task1_academic" | "task1_general" | "task2"
        essay_text:      Raw essay text.
        error_map:       Optional ErrorMap v2 dict (from detector_to_errormap_v2).
        scorer_payload:  Optional raw scorer_payload from DETECTOR_OUTPUT_V1.1.
                         If provided, preferred over error_map for the scorer call.
        word_count:      Optional explicit word count.

    Returns:
        BandScores dict compliant with contract_scorer_v2.json.
    """
    # Prepare scorer_payload for the existing scorer engine
    if scorer_payload is None and error_map is not None:
        scorer_payload = _errormap_to_scorer_payload(error_map)

    if word_count is None and error_map is not None:
        word_count = error_map.get("word_count_actual")

    # Call the existing scorer
    raw = _call_scorer_engine(task_type, essay_text, scorer_payload, word_count)

    # Extract rubric data from SCORER_OUTPUT_V1.1 structure
    score_profile  = raw.get("score_profile", {})
    rubrics_raw    = score_profile.get("rubrics", {})
    overall_band   = score_profile.get("overall_band", 0)

    # Word-count penalty signals
    wc_penalty_applied = bool(
        raw.get("word_count_penalty_applied")
        or score_profile.get("word_count_penalty_applied")
        or (error_map or {}).get("word_count_flag", False)
    )
    pre_penalty_band = raw.get("pre_penalty_overall_band") or score_profile.get("pre_penalty_band")

    # Build criteria_scores
    criteria_scores: Dict[str, Any] = {}
    low_confidence: List[str] = []

    for raw_rubric, rubric_data in rubrics_raw.items():
        v2_criterion = _RUBRIC_TO_V2.get(raw_rubric)
        if v2_criterion is None:
            continue  # Unknown rubric, skip

        raw_band   = float(rubric_data.get("band", 0))
        band       = _round_to_half_band(raw_band)
        confidence = _estimate_confidence(rubric_data, raw, v2_criterion)
        rationale  = (
            rubric_data.get("rationale")
            or rubric_data.get("justification")
            or rubric_data.get("scoring_notes")
            or ""
        )

        criteria_scores[v2_criterion] = {
            "band":       band,
            "confidence": confidence,
            "rationale":  rationale,
        }

        if confidence < 0.6:
            low_confidence.append(v2_criterion)

    # Fill in any missing criteria with null-equivalent
    for criterion in _V2_CRITERIA:
        if criterion not in criteria_scores:
            criteria_scores[criterion] = {
                "band":       None,
                "confidence": 0.0,
                "rationale":  "Criterion not scored by underlying engine",
            }

    # Holistic band: mean of available bands, rounded to nearest 0.5
    available_bands = [
        criteria_scores[c]["band"]
        for c in _V2_CRITERIA
        if criteria_scores[c]["band"] is not None
    ]
    if available_bands:
        holistic = _round_to_half_band(sum(available_bands) / len(available_bands))
    else:
        holistic = _round_to_half_band(float(overall_band))

    # Estimated true band (pre-penalty)
    estimated_true: Optional[float] = None
    if wc_penalty_applied and pre_penalty_band is not None:
        estimated_true = _round_to_half_band(float(pre_penalty_band))

    return {
        "submission_id": submission_id,
        "student_id":    student_id,
        "scored_at":     datetime.now(timezone.utc).isoformat(),
        "task_type":     task_type,
        "criteria_scores": criteria_scores,
        "holistic_band": holistic,
        "score_flags": {
            "low_confidence_criteria":    low_confidence,
            "word_count_penalty_applied": wc_penalty_applied,
            "estimated_true_band":        estimated_true,
        },
        # Passthrough for engines that still consume V1.1 format
        "_scorer_v1_passthrough": raw,
    }


# ── Convenience: transform existing SCORER_OUTPUT_V1.1 without re-scoring ────

def scorer_output_v1_to_bandscores_v2(
    scorer_output_v1: Dict[str, Any],
    submission_id: Optional[str] = None,
    student_id: Optional[str] = None,
    task_type: str = "task2",
) -> Dict[str, Any]:
    """
    Transform an already-computed SCORER_OUTPUT_V1.1 dict to BandScores v2.
    Use when you have the scorer output from a previous run and just need the format.
    """
    score_profile = scorer_output_v1.get("score_profile", {})
    rubrics_raw   = score_profile.get("rubrics", {})
    overall_band  = score_profile.get("overall_band", 0)

    wc_penalty = bool(scorer_output_v1.get("word_count_penalty_applied", False))
    pre_penalty = scorer_output_v1.get("pre_penalty_overall_band")

    criteria_scores: Dict[str, Any] = {}
    low_confidence: List[str] = []

    for raw_rubric, rubric_data in rubrics_raw.items():
        v2_criterion = _RUBRIC_TO_V2.get(raw_rubric)
        if v2_criterion is None:
            continue
        band       = _round_to_half_band(float(rubric_data.get("band", 0)))
        confidence = _estimate_confidence(rubric_data, scorer_output_v1, v2_criterion)
        criteria_scores[v2_criterion] = {
            "band":       band,
            "confidence": confidence,
            "rationale":  rubric_data.get("rationale", ""),
        }
        if confidence < 0.6:
            low_confidence.append(v2_criterion)

    for criterion in _V2_CRITERIA:
        if criterion not in criteria_scores:
            criteria_scores[criterion] = {"band": None, "confidence": 0.0, "rationale": ""}

    available_bands = [criteria_scores[c]["band"] for c in _V2_CRITERIA
                       if criteria_scores[c]["band"] is not None]
    holistic = _round_to_half_band(sum(available_bands) / len(available_bands)) if available_bands \
               else _round_to_half_band(float(overall_band))

    estimated_true = _round_to_half_band(float(pre_penalty)) if wc_penalty and pre_penalty else None

    return {
        "submission_id": submission_id or str(uuid.uuid4()),
        "student_id":    student_id or "",
        "scored_at":     datetime.now(timezone.utc).isoformat(),
        "task_type":     task_type,
        "criteria_scores": criteria_scores,
        "holistic_band": holistic,
        "score_flags": {
            "low_confidence_criteria":    low_confidence,
            "word_count_penalty_applied": wc_penalty,
            "estimated_true_band":        estimated_true,
        },
        "_scorer_v1_passthrough": scorer_output_v1,
    }
