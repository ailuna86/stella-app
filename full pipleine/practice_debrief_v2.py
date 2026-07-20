"""
practice_debrief_v2.py
======================
STANDALONE FILE — no imports from practice_debrief_v1.py.
All v1 logic is inlined. Only one targeted change was made.

CHANGES vs practice_debrief_v1.py
-----------------------------------
F28-B9  student_message field when overall_accuracy == 0.0.

        WHY THE CHANGE IS NEEDED
        -------------------------
        When a student completes 0 exercises correctly (overall_accuracy == 0.0),
        the v1 debrief had no special handling. The output contained an empty
        or confusing performance block with no guidance.

        Two cases produce overall_accuracy == 0.0:
          (a) Student answered all exercises incorrectly.
          (b) Student skipped/abandoned all exercises (exercises_completed == 0).

        Both cases need a student_message field to prevent a blank or confusing
        debrief. The message is different for each case.

        Fix: build_practice_debrief() now adds a top-level student_message field
        whenever overall_accuracy == 0.0. The message is warm and non-punitive.

USAGE
-----
    from practice_debrief_v2 import build_practice_debrief

    debrief = build_practice_debrief(
        results      = practice_results,   # dict from PracticeEngineV3.get_session_results()
        exercise_log = exercise_log,       # list from 07e_exercise_log.json
    )
    # save as 07f_practice_debrief.json
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


# =============================================================================
# CONSTANTS (from v1, unchanged)
# =============================================================================

# F28-B9: student_message for 0.0 accuracy cases
_MSG_ZERO_ACCURACY_ALL_WRONG = (
    "You didn't get any exercises right this session — and that is completely OK. "
    "It just means these patterns are new for you, which is exactly why we practise. "
    "Read through the model answers and explanations below carefully. "
    "Even reading them once helps your brain start to recognise the correct patterns. "
    "Try the same family of exercises again next session and you will see improvement."
)

_MSG_ZERO_ACCURACY_NO_ATTEMPTS = (
    "You didn't answer any exercises this session — that's OK. "
    "Review the model answers above and try the exercises next time. "
    "Even reading through them helps you recognise the patterns."
)


# =============================================================================
# HELPERS (from v1, unchanged)
# =============================================================================

def _accuracy_status(acc: float) -> str:
    if acc >= 0.85:
        return "Excellent"
    if acc >= 0.70:
        return "Good"
    if acc >= 0.50:
        return "Getting there"
    return "Needs work"


def _next_session_tip(
    perf_by_crit: List[Dict],
    acc_overall: float,
    weak_families: List[Dict],
) -> str:
    if not perf_by_crit:
        return "Complete more exercises to get personalised feedback."

    worst = min(perf_by_crit, key=lambda x: x["accuracy"])

    if acc_overall >= 0.85:
        return (
            "Excellent session! You're ready to move to harder exercises. "
            "Try the stretch difficulty level next time."
        )
    if acc_overall >= 0.70:
        if weak_families:
            wf = weak_families[0]["family_label"]
            return (
                f"Good work overall. Your weakest area was {wf} — "
                "focus on this family in your next session."
            )
        return "Good session. Keep practising regularly to consolidate your progress."

    crit = worst["criterion_label"]
    tip_map = {
        "Grammar (GRA)": (
            "Review the grammar rules for this family carefully before your next session, "
            "then try foundational-level exercises."
        ),
        "Vocabulary (LR)": (
            "Look up the correct collocations and word forms in a dictionary, "
            "then practise again."
        ),
        "Task Achievement (TA)": (
            "Re-read the task instructions and practise structuring your arguments "
            "before the next session."
        ),
        "Coherence & Cohesion (CC)": (
            "Practise using linking words and reference chains to connect your ideas."
        ),
    }
    specific_tip = tip_map.get(crit, "Review the rules for this area and retry the exercises.")
    return (
        f"Focus on {crit} — accuracy was {worst['accuracy'] * 100:.0f}%. "
        f"{specific_tip}"
    )


# =============================================================================
# MAIN BUILDER
# =============================================================================

def build_practice_debrief(
    results: Dict[str, Any],
    exercise_log: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Build a PracticeDebrief v2 dict from get_session_results() output and
    the per-exercise log saved in step 07e.

    Parameters
    ----------
    results      : dict returned by PracticeEngineV3.get_session_results()
    exercise_log : list of exercise dicts from 07e_exercise_log.json

    Returns
    -------
    PracticeDebrief v2 dict ready to save as 07f_practice_debrief.json.

    Schema: PRACTICE_DEBRIEF_V2
        schema                    "PRACTICE_DEBRIEF_V2"
        session_id                str
        overall_accuracy          float  0.0–1.0
        exercises_completed       int
        student_message           str | None   (F28-B9: present when accuracy == 0.0)
        performance_by_criterion  list[CriterionPerformance]
        weak_families             list[WeakFamily]
        exercise_review           list[ExerciseReview]  (wrong answers only)
        next_session_tip          str
        ready_for_essay           bool  (accuracy >= 0.70)
    """
    acc_overall = results.get("overall_accuracy", 0.0)
    completed   = results.get("exercises_completed", 0)

    # ── F28-B9: student_message when accuracy == 0.0 ─────────────────────────
    student_message: Optional[str] = None
    if acc_overall == 0.0:
        if completed == 0:
            student_message = _MSG_ZERO_ACCURACY_NO_ATTEMPTS
        else:
            student_message = _MSG_ZERO_ACCURACY_ALL_WRONG

    # ── Per-criterion performance ─────────────────────────────────────────────
    perf_by_crit: List[Dict] = []
    for crit, data in (results.get("by_criterion") or {}).items():
        acc = data.get("accuracy", 0.0)
        perf_by_crit.append({
            "criterion_label": data.get("criterion_label", crit),
            "attempted":       data.get("attempted", 0),
            "correct":         data.get("correct", 0),
            "accuracy":        round(acc, 3),
            "status":          _accuracy_status(acc),
        })
    # Worst first so student sees priority at the top
    perf_by_crit.sort(key=lambda x: x["accuracy"])

    # ── Weak families with suggestions ───────────────────────────────────────
    weak_families: List[Dict] = []
    for sug in (results.get("suggestions") or []):
        weak_families.append({
            "family_label":    sug.get("family_label", sug.get("family", "")),
            "criterion_label": sug.get("criterion_label", ""),
            "error_count":     sug.get("error_count", 0),
            "suggestion":      sug.get("suggestion", ""),
        })

    # ── Per-exercise review (wrong answers only) ──────────────────────────────
    exercise_review: List[Dict] = []
    for ex in exercise_log:
        is_correct = ex.get("is_correct", True)
        model_ans  = ex.get("model_answer", "")
        if not is_correct or not model_ans:
            exercise_review.append({
                "exercise_id":    ex.get("exercise_id", ""),
                "family":         ex.get("family", ""),
                "full_prompt":    ex.get("full_prompt", ""),
                "student_answer": ex.get("student_answer", ""),
                "model_answer":   model_ans,
                "explanation":    ex.get("explanation", ""),
            })

    # ── Next-session tip ──────────────────────────────────────────────────────
    tip = _next_session_tip(perf_by_crit, acc_overall, weak_families)

    return {
        "schema":                   "PRACTICE_DEBRIEF_V2",
        "session_id":               results.get("session_id"),
        "overall_accuracy":         round(acc_overall, 3),
        "exercises_completed":      completed,
        "student_message":          student_message,        # F28-B9 (None when accuracy > 0.0)
        "performance_by_criterion": perf_by_crit,
        "weak_families":            weak_families,
        "exercise_review":          exercise_review,
        "next_session_tip":         tip,
        "ready_for_essay":          acc_overall >= 0.70,
    }
