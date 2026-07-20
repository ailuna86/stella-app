"""
progress_tracker_v5.py
======================
STANDALONE FILE — no imports from progress_tracker_v4.py.
All v4 logic is inlined. ProgressTrackerV2 is still imported from
progress_tracker_v2_scorer_feed (frozen engine, unchanged).

CHANGES vs progress_tracker_v4.py
-----------------------------------
B1 (F26)  build_cross_essay_trend_v5()
          Replaces build_cross_essay_trend_v4().

          WHY THE CHANGE IS NEEDED
          -------------------------
          v4 called ProgressTrackerV2.get_progress_snapshot() to get score_history.
          ProgressTrackerV2 assigns essay_id='?' to all entries internally because it
          was not designed to store essay_ids (it stores bands by session, not essay).
          As a result, essays_in_window was always ['?'] and cross_essay was always
          False, regardless of how many different essays the student had submitted.

          Fix: build_cross_essay_trend_v5() reads band_history.jsonl directly.
          The pipeline_runner writes real essay_id values to band_history.jsonl
          (one record per session, with the actual essay_id from the essay dict).
          band_history.jsonl schema (confirmed from live file):
              {
                  "session_id":    "...",
                  "submission_id": "...",
                  "essay_id":      "1",       ← real essay_id
                  "recorded_at":   "2026-...",
                  "session_n":     1,
                  "holistic":      4.5,
                  "bands":         {"TA": 3.0, "CC": 5.0, "LR": 5.0, "GRA": 5.0}
              }

          The band abbreviations (TA, CC, LR, GRA) are mapped back to full
          criterion names via _ABBR_TO_CRITERION.

          New parameter: band_history_path (str | None)
            1. If supplied, reads from this path.
            2. If tracker has a data_dir attribute, tries tracker.data_dir / band_history.jsonl.
            3. If neither resolves, returns status=insufficient_data.

build_v11_progress_snapshot()
          Renamed from build_v10_progress_snapshot().
          schema_version updated to PROGRESS_SNAPSHOT_V11.

USAGE
-----
    from progress_tracker_v5 import (
        append_metric_history_v3,
        compute_essay_metric_trends,
        build_cross_essay_trend_v5,
        build_skill_progress_narratives,
        build_v11_progress_snapshot,
    )
    from progress_tracker_v2_scorer_feed import ProgressTrackerV2
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import sys
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from progress_tracker_v2_scorer_feed import ProgressTrackerV2  # noqa: E402 (frozen)

# Optional LLM narrative enrichment (F31). Set True to enable when ready.
_ENABLE_LLM_NARRATIVES: bool = False


# =============================================================================
# CONSTANTS (from v4, unchanged)
# =============================================================================

_METRIC_DIRECTIONS: Dict[str, str] = {
    "affected_discourse_ratio": "lower_is_better",
    "gra_composite":            "higher_is_better",
    "lr_precision_index":       "higher_is_better",
    "semantic_recoverability":  "higher_is_better",
    "tr_composite":             "higher_is_better",
    "cc_composite":             "higher_is_better",
    "word_count":               "higher_is_better",
}

_METRIC_LABELS: Dict[str, str] = {
    "affected_discourse_ratio": "Sentence clarity",
    "gra_composite":            "Grammar accuracy",
    "lr_precision_index":       "Vocabulary precision",
    "semantic_recoverability":  "Overall text clarity",
    "tr_composite":             "Task response quality",
    "cc_composite":             "Coherence quality",
    "word_count":               "Word count",
}

_STABLE_DELTA = 0.01

_CRITERIA_ALL = [
    "task_achievement",
    "coherence_cohesion",
    "lexical_resource",
    "grammatical_range_accuracy",
    "holistic",
]

_CRIT_LABEL: Dict[str, str] = {
    "grammatical_range_accuracy": "Grammar accuracy",
    "lexical_resource":           "Vocabulary range",
    "task_achievement":           "Task achievement",
    "coherence_cohesion":         "Coherence and cohesion",
    "holistic":                   "Overall band",
}

# B1 fix: map band_history.jsonl abbreviations → full criterion names
_ABBR_TO_CRITERION: Dict[str, str] = {
    "TA":  "task_achievement",
    "CC":  "coherence_cohesion",
    "LR":  "lexical_resource",
    "GRA": "grammatical_range_accuracy",
}


# =============================================================================
# HELPERS (from v4, unchanged)
# =============================================================================

def _load_jsonl(path: Path) -> List[Dict]:
    entries: List[Dict] = []
    if not path.exists():
        return entries
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries


def _trend_from_delta(delta: float, direction: str) -> str:
    if abs(delta) < _STABLE_DELTA:
        return "stable"
    improving = (
        (delta > 0 and direction == "higher_is_better")
        or (delta < 0 and direction == "lower_is_better")
    )
    return "improving" if improving else "declining"


# =============================================================================
# F8: METRIC HISTORY APPEND (from v4, unchanged)
# =============================================================================

def append_metric_history_v3(
    history_path: Path,
    metrics: Dict[str, Any],
    essay_id: Optional[str] = None,
) -> None:
    """
    Append a metrics entry to metric_history.jsonl.
    Never deduplicates — every pipeline run produces a new entry.
    essay_id is stored on the entry for later filtering.
    """
    entry = dict(metrics)
    entry["appended_at"] = datetime.now(timezone.utc).isoformat()
    if essay_id is not None:
        entry["essay_id"] = str(essay_id)
    with open(history_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# =============================================================================
# F7: ESSAY-FILTERED METRIC TRENDS (from v4, unchanged)
# =============================================================================

def compute_essay_metric_trends(
    history_path: Path,
    essay_id: str,
    window: int = 3,
) -> Dict[str, Any]:
    """
    Compute metric trends using only metric_history.jsonl entries for the
    given essay_id.

    Metric trends (gra_composite, lr_precision_index, word_count, etc.) remain
    essay-filtered because essay composition affects these scores independently
    of student ability improvement.
    """
    all_entries   = _load_jsonl(history_path)
    essay_entries = [
        e for e in all_entries
        if str(e.get("essay_id", "")) == str(essay_id)
    ]

    base = {
        "essay_id":         essay_id,
        "same_essay_count": len(essay_entries),
        "window_used":      0,
    }

    if len(essay_entries) < 2:
        return {
            **base,
            "status": "insufficient_same_essay_data",
            "note": (
                f"Need ≥2 submissions of essay '{essay_id}' for trend comparison. "
                f"Found {len(essay_entries)}. "
                "Metric trends will appear after the next run of the same essay."
            ),
        }

    recent = essay_entries[-window:]
    result = {**base, "status": "available", "window_used": len(recent)}

    for key, direction in _METRIC_DIRECTIONS.items():
        values = [e[key] for e in recent if key in e and e[key] is not None]
        if len(values) < 2:
            continue
        delta = round(values[-1] - values[-2], 4)
        trend = _trend_from_delta(delta, direction)
        result[key] = {
            "current":       values[-1],
            "history":       values,
            "delta_last":    delta,
            "trend":         trend,
            "student_label": _METRIC_LABELS.get(key, key),
            "direction":     direction,
        }

    return result


# =============================================================================
# B1 (F26): CROSS-ESSAY BAND TREND — reads band_history.jsonl directly (new in v5)
# =============================================================================

def build_cross_essay_trend_v5(
    tracker: "ProgressTrackerV2",
    student_id: str = "",
    window: int = 5,
    band_history_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    B1 fix: Compute band trends from band_history.jsonl directly.

    WHY THIS IS CORRECT
    --------------------
    v4 used ProgressTrackerV2.get_progress_snapshot() which returns
    score_history with essay_id='?' for every entry (ProgressTrackerV2 was
    not designed to store essay_ids). As a result, cross_essay was always
    False and essays_in_window was always ['?'].

    v5 reads band_history.jsonl directly. The pipeline_runner writes a record
    per session to band_history.jsonl with the real essay_id. The bands in
    that file use abbreviated keys (TA, CC, LR, GRA) which are mapped back to
    full criterion names via _ABBR_TO_CRITERION.

    ProgressTrackerV2 (tracker) is still accepted as a parameter for API
    compatibility; it is only used to resolve band_history_path if not
    supplied explicitly.

    Parameters
    ----------
    tracker            : ProgressTrackerV2 instance (kept for API compat)
    student_id         : student identifier (not used for lookup here)
    window             : number of most-recent sessions to use (default 5)
    band_history_path  : explicit path to band_history.jsonl
                         If None, tries tracker.data_dir / band_history.jsonl

    Returns
    -------
    Dict:
        status            "available" | "insufficient_data"
        trends            {criterion: "improving"|"stable"|"declining"|"insufficient_data"}
        window            int — number of sessions used
        total_submissions int — total records in band_history.jsonl
        essays_in_window  list[str] — real essay_id values
        cross_essay       bool — True when window spans >1 unique essay_id
        note              str
    """
    # Resolve path
    path: Optional[Path] = None
    if band_history_path:
        path = Path(band_history_path)
    elif hasattr(tracker, "data_dir") and tracker.data_dir:
        path = Path(tracker.data_dir) / "band_history.jsonl"

    if path is None or not path.exists():
        return {
            "status":            "insufficient_data",
            "trends":            {},
            "window":            0,
            "total_submissions": 0,
            "essays_in_window":  [],
            "cross_essay":       False,
            "note": (
                "band_history.jsonl not found. "
                "Supply band_history_path argument or ensure tracker.data_dir is set."
            ),
        }

    entries     = _load_jsonl(path)
    total_count = len(entries)

    if total_count < 2:
        return {
            "status":            "insufficient_data",
            "trends":            {},
            "window":            0,
            "total_submissions": total_count,
            "essays_in_window":  [],
            "cross_essay":       False,
            "note": (
                f"Need at least 2 sessions to compute trends. "
                f"Found {total_count}. "
                "Trend will appear after your next session."
            ),
        }

    recent           = entries[-window:]
    essays_in_window = list(dict.fromkeys(str(e.get("essay_id", "?")) for e in recent))
    is_cross_essay   = len(essays_in_window) > 1

    trends: Dict[str, str] = {}

    # Holistic band trend
    holistic_vals = [e["holistic"] for e in recent if e.get("holistic") is not None]
    if len(holistic_vals) >= 2:
        if holistic_vals[-1] > holistic_vals[-2]:
            trends["holistic"] = "improving"
        elif holistic_vals[-1] < holistic_vals[-2]:
            trends["holistic"] = "declining"
        else:
            trends["holistic"] = "stable"
    else:
        trends["holistic"] = "insufficient_data"

    # Per-criterion trends (abbreviated keys in band_history → full criterion names)
    for abbr, crit in _ABBR_TO_CRITERION.items():
        vals = [
            e.get("bands", {}).get(abbr)
            for e in recent
            if e.get("bands", {}).get(abbr) is not None
        ]
        if len(vals) >= 2:
            if vals[-1] > vals[-2]:
                trends[crit] = "improving"
            elif vals[-1] < vals[-2]:
                trends[crit] = "declining"
            else:
                trends[crit] = "stable"
        else:
            trends[crit] = "insufficient_data"

    n_essays   = len(essays_in_window)
    essay_word = "essays" if n_essays > 1 else "essay"
    note = (
        f"Trend based on your last {len(recent)} sessions "
        f"({n_essays} different {essay_word}). "
        "This shows your overall progress as a writer, not just on one topic."
        if is_cross_essay
        else
        f"Trend based on your last {len(recent)} sessions of the same essay."
    )

    return {
        "status":            "available",
        "trends":            trends,
        "window":            len(recent),
        "total_submissions": total_count,
        "essays_in_window":  essays_in_window,
        "cross_essay":       is_cross_essay,
        "note":              note,
    }


# =============================================================================
# F27: SKILL PROGRESS NARRATIVES (from v4, unchanged)
# =============================================================================

_NARRATIVE_TEMPLATES: Dict[tuple, tuple] = {
    ("improving", "recurring_error"): (
        "Your {label} has improved recently. This is a recurring area, "
        "so the improvement shows that your practice is paying off. "
        "Keep the momentum — consistent work here will make this a strength.",
        "Continue practising {skill} exercises at {level} level to consolidate the gain.",
    ),
    ("improving", "high_impact_gap"): (
        "Your {label} has improved and is moving closer to your target band. "
        "This is your biggest lever for score improvement — keep pushing.",
        "Aim for {level} difficulty exercises. "
        "You are ready to handle more complex {skill} patterns.",
    ),
    ("improving", "new_weakness"): (
        "Your {label} improved this session after being flagged for the first time. "
        "Good recovery.",
        "Check that the {skill} pattern is fully understood — practise one more session before moving on.",
    ),
    ("stable", "recurring_error"): (
        "Your {label} has stayed at the same level for several sessions. "
        "The same error patterns keep appearing. This is the area that most "
        "needs your focused attention before your exam.",
        "Set aside 15 minutes specifically for {skill} rules this week. "
        "Then practise {level} exercises until accuracy reaches 70%+.",
    ),
    ("stable", "high_impact_gap"): (
        "Your {label} has been stable. "
        "To reach Band {target}, you need to push this criterion forward — "
        "stability here means you are not closing the gap to your target.",
        "Focus on {skill} at {level} level. "
        "Try to answer at least 5 exercises correctly in a row before moving on.",
    ),
    ("stable", "new_weakness"): (
        "Your {label} was flagged and has stayed at the same level. "
        "Addressing this now prevents it from becoming a recurring pattern.",
        "Review {skill} rules, then complete one focused practice session.",
    ),
    ("declining", "recurring_error"): (
        "Your {label} dipped this session. This sometimes happens when you try "
        "more ambitious sentence structures — which is actually a sign of growth. "
        "But the recurring pattern in {skill} needs attention to stabilise your score.",
        "Go back to {skill} rules and practise at {level} level "
        "to rebuild accuracy before adding complexity.",
    ),
    ("declining", "high_impact_gap"): (
        "Your {label} dropped this session. "
        "This is your most important area for score improvement, "
        "so a dip here has a direct effect on your overall band.",
        "Review the {skill} errors from this session carefully, "
        "then practise consolidation-level exercises until accuracy recovers.",
    ),
    ("declining", "new_weakness"): (
        "Your {label} was flagged for the first time and dropped this session. "
        "Address this early — a new weakness that goes unaddressed can become "
        "a recurring pattern.",
        "Study {skill} rules immediately and complete a short focused practice session.",
    ),
}

_NARRATIVE_FALLBACK: tuple = (
    "Your {label} needs attention. The specific error types are shown in your "
    "detailed feedback above.",
    "Review {skill} rules and practise {level} exercises before your next essay.",
)

_LEVEL_LABELS: Dict[str, str] = {
    "foundational":  "foundational (A2–B1)",
    "consolidation": "consolidation (B1)",
    "stretch":       "extension (B2)",
    "extension":     "extension (B2)",
    "advanced":      "advanced (C1)",
    "challenge":     "challenge (C1+)",
}

_INSUFFICIENT_DATA_NARRATIVE = (
    "You need at least 2 sessions to track progress for this area. "
    "Your trend will appear after your next essay."
)


def _build_single_narrative(
    criterion: str,
    trend: str,
    priority_reason: str,
    current_band: Optional[float],
    target_band: Optional[float],
    skill_tag: str,
    difficulty: str,
    sessions_flagged: int,
) -> Dict[str, Any]:
    label      = _CRIT_LABEL.get(criterion, criterion)
    skill_disp = (skill_tag or criterion).replace("_", " ").title()
    level_disp = _LEVEL_LABELS.get(difficulty, difficulty or "consolidation")

    if trend == "insufficient_data":
        return {
            "criterion":     criterion,
            "label":         label,
            "current_band":  current_band,
            "target_band":   target_band,
            "trend":         trend,
            "narrative":     _INSUFFICIENT_DATA_NARRATIVE,
            "next_step":     "Complete another essay to unlock progress tracking for this area.",
        }

    template_key = (trend, priority_reason)
    narrative_tmpl, step_tmpl = _NARRATIVE_TEMPLATES.get(
        template_key, _NARRATIVE_FALLBACK
    )

    fmt = {
        "label":   label,
        "skill":   skill_disp,
        "level":   level_disp,
        "current": f"{current_band:.1f}" if current_band else "?",
        "target":  f"{target_band:.1f}"  if target_band  else "?",
        "n":       sessions_flagged,
    }

    return {
        "criterion":        criterion,
        "label":            label,
        "current_band":     current_band,
        "target_band":      target_band,
        "trend":            trend,
        "sessions_flagged": sessions_flagged,
        "narrative":        narrative_tmpl.format(**fmt),
        "next_step":        step_tmpl.format(**fmt),
    }


def build_skill_progress_narratives(
    band_trend: Dict[str, Any],
    learner_profile: Optional[Dict[str, Any]],
    directive: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    F27: Generate per-criterion student-friendly progress narratives.
    Unchanged from v4 — works with both band_trend_v4 and band_trend_v5 output.
    """
    trends     = band_trend.get("trends", {})
    is_cross   = band_trend.get("cross_essay", False)
    trend_note = band_trend.get("note", "")

    dir_fas: Dict[str, Dict] = {}
    if directive:
        for fa in directive.get("focus_areas", []) or []:
            c = fa.get("criterion", "")
            if c:
                dir_fas[c] = fa

    wm_by_criterion: Dict[str, int] = {}
    if learner_profile:
        for w in learner_profile.get("weakness_map", []) or []:
            if w.get("_criterion_level") or w.get("_seeded"):
                continue
            c  = w.get("criterion", "")
            sf = w.get("sessions_flagged", 0)
            if c and sf > wm_by_criterion.get(c, 0):
                wm_by_criterion[c] = sf

    ielts_criteria = [
        "grammatical_range_accuracy",
        "lexical_resource",
        "task_achievement",
        "coherence_cohesion",
    ]

    narratives: List[Dict] = []
    for criterion in ielts_criteria:
        trend            = trends.get(criterion, "insufficient_data")
        dfa              = dir_fas.get(criterion, {})
        current_band     = dfa.get("current_band")
        target_band      = dfa.get("target_band")
        priority_reason  = dfa.get("priority_reason", "high_impact_gap")
        skill_tag        = dfa.get("skill_tag", "")
        difficulty       = dfa.get("recommended_difficulty", "consolidation")
        sessions_flagged = wm_by_criterion.get(criterion, 0)

        narr = _build_single_narrative(
            criterion, trend, priority_reason,
            current_band, target_band,
            skill_tag, difficulty, sessions_flagged,
        )

        if _ENABLE_LLM_NARRATIVES and trend not in ("insufficient_data",):
            narr = _llm_enrich_narrative(narr)

        narratives.append(narr)

    return {
        "narratives":    narratives,
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "cross_essay":   is_cross,
        "trend_note":    trend_note,
    }


# =============================================================================
# F31: LLM NARRATIVE ENRICHMENT HOOK (from v4, unchanged)
# =============================================================================

def _llm_enrich_narrative(narr: Dict[str, Any]) -> Dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return narr
    try:
        import openai
        client = openai.OpenAI(api_key=api_key)
        prompt = (
            "You are writing student feedback for an IELTS preparation product. "
            "Rewrite the following progress note in exactly 2 clear, encouraging sentences. "
            "Keep the meaning identical. Address the student directly as 'you'. "
            "Do not add information not present in the original.\n\n"
            f"Criterion: {narr['label']}\n"
            f"Trend: {narr['trend']}\n"
            f"Original: {narr['narrative']}\n\n"
            "Rewrite:"
        )
        response = client.chat.completions.create(
            model       = os.environ.get("VIP_CHEAP_MODEL", "gpt-4o-mini"),
            messages    = [{"role": "user", "content": prompt}],
            max_tokens  = 120,
            temperature = 0.3,
        )
        text = (response.choices[0].message.content or "").strip()
        if text and len(text) > 30:
            narr = dict(narr)
            narr["narrative"]     = text
            narr["_llm_enriched"] = True
    except Exception:
        pass
    return narr


# =============================================================================
# PROGRESS SNAPSHOT BUILDER v11 (renamed from build_v10_progress_snapshot)
# =============================================================================

def build_v11_progress_snapshot(
    band_trend_v5: Dict[str, Any],
    metric_trends: Dict[str, Any],
    skill_narratives: Dict[str, Any],
    band_snapshot: Dict[str, Any],
    band_scores: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build the v11 progress snapshot dict saved as 09_progress_snapshot_v11.json.

    Changes vs build_v10_progress_snapshot:
      - Input is band_trend_v5 (from build_cross_essay_trend_v5)
      - cross_essay now reflects real essay_ids (B1 fix)
      - schema_version updated to PROGRESS_SNAPSHOT_V11
    """
    return {
        "schema_version": "PROGRESS_SNAPSHOT_V11",
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "scores": {
            "current_bands":     band_snapshot.get("current_bands", {}),
            "score_history":     band_snapshot.get("score_history", []),
            "trends":            band_trend_v5.get("trends", {}),
            "band_deltas":       band_snapshot.get("band_deltas", {}),
            "total_submissions": band_snapshot.get("total_submissions", 0),
            "cross_essay":       band_trend_v5.get("cross_essay", False),
            "essays_in_window":  band_trend_v5.get("essays_in_window", []),
            "trend_note":        band_trend_v5.get("note"),
            "trend_status":      band_trend_v5.get("status", "unavailable"),
        },
        "metrics":        metric_trends,
        "skill_progress": skill_narratives,
    }
