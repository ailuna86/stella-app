"""
pe_to_priority_directive_v2_v5.py
==================================
NEW FILE — wraps pe_to_priority_directive_v2_v4.py (preserved unchanged).

CHANGES vs v4
-------------
1. Accepts optional `learner_profile` parameter (LearnerProfile from prior session's LIE).
2. After v4 builds focus areas, applies profile-based adjustments:
   - Recurring skills (sessions_flagged >= 3 in weakness_map) →
     priority_reason set to "recurring_error" so FE and student see the pattern
   - Directive carries `profile_summary` block for downstream engines
3. Adjusts recommended_difficulty based on learning_velocity from prior profile:
   - velocity > 0.5  → stretch (student progressing well)
   - velocity < 0.0  → foundational (student struggling)
   - else            → keep v4's recommendation

WHY HERE (NOT IN PE)
--------------------
The Priority Engine (priority_engine_v4_4_selfcontained.py) is a frozen
subprocess. We cannot pass LearnerProfile to it via its current CLI interface
(--input, --scorer, --knowledge, --output). Profile-awareness is therefore
applied in the adapter layer immediately after PE output is received.

The effect is equivalent: focus areas that match recurring skills get their
priority_reason flagged, which (a) reaches the student in FE's feedback and
(b) allows Practice Engine to surface the right exercise type label.

USAGE
-----
    from pe_to_priority_directive_v2_v5 import pe_output_to_directive_v5

    directive = pe_output_to_directive_v5(
        pe_output      = pe_out,
        submission_id  = submission_id,
        student_id     = student_id,
        session_id     = session_id,
        goal_band      = 7.0,
        learner_profile = prior_profile,   # None on first session
    )
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── Import v4 adapter ─────────────────────────────────────────────────────────
# The v5 adapter is a thin wrapper — v4 does all the heavy lifting.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from pe_to_priority_directive_v2_v4 import pe_output_to_directive  # noqa: E402

# ── Constants ─────────────────────────────────────────────────────────────────

_RECURRING_THRESHOLD      = 3    # sessions_flagged >= this → "recurring_error"
_VELOCITY_STRETCH_MIN     = 0.5  # learning_velocity above this → upgrade to "stretch"
_VELOCITY_FOUNDATIONAL_MAX = 0.0 # learning_velocity below this → downgrade to "foundational"
_DIFFICULTY_ORDER = ["foundational", "consolidation", "stretch"]


# ── Profile helpers ───────────────────────────────────────────────────────────

def _get_weakness_map(profile: Optional[Dict]) -> List[Dict]:
    if not profile:
        return []
    return profile.get("weakness_map", [])


def _get_learning_velocity(profile: Optional[Dict]) -> Optional[float]:
    if not profile:
        return None
    lv = profile.get("learning_velocity")
    if lv is None:
        return None
    # learning_velocity may be a list of per-criterion velocities or a scalar
    if isinstance(lv, list):
        vals = [v for v in lv if isinstance(v, (int, float))]
        return sum(vals) / len(vals) if vals else None
    try:
        return float(lv)
    except (TypeError, ValueError):
        return None


def _get_recurring_skill_tags(weakness_map: List[Dict]) -> Dict[str, int]:
    """
    Return {skill_tag: sessions_flagged} for skills meeting the recurring threshold.
    Also matches on criterion label as a fallback.
    """
    recurring: Dict[str, int] = {}
    for entry in weakness_map:
        flagged = entry.get("sessions_flagged", 0)
        if flagged >= _RECURRING_THRESHOLD:
            tag = entry.get("skill_tag") or entry.get("criterion") or ""
            if tag:
                recurring[tag.upper()] = flagged
    return recurring


def _adjust_difficulty(current: str, velocity: Optional[float]) -> str:
    """Upgrade or downgrade difficulty based on learning velocity."""
    if velocity is None:
        return current
    if current not in _DIFFICULTY_ORDER:
        return current
    idx = _DIFFICULTY_ORDER.index(current)
    if velocity > _VELOCITY_STRETCH_MIN and idx < len(_DIFFICULTY_ORDER) - 1:
        return _DIFFICULTY_ORDER[idx + 1]
    if velocity < _VELOCITY_FOUNDATIONAL_MAX and idx > 0:
        return _DIFFICULTY_ORDER[idx - 1]
    return current


def _build_profile_summary(profile: Optional[Dict]) -> Dict:
    """Build a compact profile summary to embed in the directive."""
    if not profile:
        return {"available": False, "sessions_analyzed": 0}
    wm = profile.get("weakness_map", [])
    recurring = [
        w.get("skill_tag", w.get("criterion", "?"))
        for w in wm
        if w.get("sessions_flagged", 0) >= _RECURRING_THRESHOLD
    ]
    resolved = [
        w.get("skill_tag", w.get("criterion", "?"))
        for w in wm
        if w.get("trend") == "resolved"
    ]
    return {
        "available":             True,
        "sessions_analyzed":     profile.get("sessions_analyzed", 0),
        "recurring_skills":      recurring,
        "resolved_skills":       resolved,
        "learning_velocity":     _get_learning_velocity(profile),
        "recommended_intent":    profile.get("recommended_session_intent"),
    }


# ── Main function ─────────────────────────────────────────────────────────────

def pe_output_to_directive_v5(
    pe_output:       Dict[str, Any],
    submission_id:   str,
    student_id:      str,
    session_id:      str,
    goal_band:       Optional[float] = None,
    learner_profile: Optional[Dict]  = None,
) -> Dict[str, Any]:
    """
    Convert PRIORITY_ENGINE_OUTPUT_V4 to PriorityDirective v2 with
    optional profile-awareness from LearnerProfile[prev].

    Parameters
    ----------
    pe_output       : PE output dict (same format as v4 adapter expects)
    submission_id   : UUID string
    student_id      : student identifier
    session_id      : UUID string
    goal_band       : student's target IELTS band (e.g. 7.0)
    learner_profile : LearnerProfile from prior session's LIE output (None = first session)

    Returns
    -------
    PriorityDirective v2 dict with profile adjustments applied to focus areas.
    """

    # ── Step 1: Call v4 adapter ───────────────────────────────────────────────
    directive = pe_output_to_directive(
        pe_output     = pe_output,
        submission_id = submission_id,
        student_id    = student_id,
        session_id    = session_id,
        goal_band     = goal_band,
    )

    # ── Step 2: Skip adjustments if no profile ────────────────────────────────
    directive["profile_summary"] = _build_profile_summary(learner_profile)

    if not learner_profile:
        directive["_adapter_version"] = "v5_no_profile"
        return directive

    # ── Step 3: Build lookup structures from profile ──────────────────────────
    weakness_map    = _get_weakness_map(learner_profile)
    recurring_tags  = _get_recurring_skill_tags(weakness_map)
    velocity        = _get_learning_velocity(learner_profile)

    # Build a set of (criterion, skill_tag) pairs that are recurring
    # so we can match focus areas by either skill_tag or criterion label
    recurring_criteria: Dict[str, int] = {}
    for entry in weakness_map:
        if entry.get("sessions_flagged", 0) >= _RECURRING_THRESHOLD:
            crit = entry.get("criterion", "")
            if crit:
                recurring_criteria[crit] = entry.get("sessions_flagged", 0)

    # ── Step 4: Adjust each focus area ───────────────────────────────────────
    adjustments_made: List[str] = []

    for fa in directive.get("focus_areas", []):
        skill_tag_upper = (fa.get("skill_tag") or "").upper()
        criterion       = fa.get("criterion", "")

        # 4a — Mark recurring if skill_tag or criterion is a known persistent limiter
        is_recurring = (
            skill_tag_upper in recurring_tags
            or criterion in recurring_criteria
        )
        if is_recurring:
            sessions_flagged = (
                recurring_tags.get(skill_tag_upper)
                or recurring_criteria.get(criterion, _RECURRING_THRESHOLD)
            )
            fa["priority_reason"]       = "recurring_error"
            fa["sessions_flagged"]      = sessions_flagged
            fa["profile_note"]          = (
                f"This skill has appeared in {sessions_flagged} previous sessions."
            )
            adjustments_made.append(
                f"rank{fa['rank']} {criterion}: priority_reason→recurring_error"
                f" (sessions_flagged={sessions_flagged})"
            )

        # 4b — Adjust difficulty from learning velocity
        if velocity is not None:
            old_diff = fa.get("recommended_difficulty", "consolidation")
            new_diff = _adjust_difficulty(old_diff, velocity)
            if new_diff != old_diff:
                fa["recommended_difficulty"] = new_diff
                adjustments_made.append(
                    f"rank{fa['rank']} {criterion}: difficulty "
                    f"{old_diff}→{new_diff} (velocity={velocity:.2f})"
                )

    # ── Step 5: Embed adjustment log ─────────────────────────────────────────
    directive["_adapter_version"]   = "v5_with_profile"
    directive["_profile_adjustments"] = adjustments_made

    if adjustments_made:
        print(f"  [DirectiveV5] Profile adjustments applied ({len(adjustments_made)}):")
        for adj in adjustments_made:
            print(f"    {adj}")
    else:
        print("  [DirectiveV5] Profile loaded — no adjustments triggered "
              f"(recurring threshold={_RECURRING_THRESHOLD}, "
              f"velocity={velocity})")

    return directive
