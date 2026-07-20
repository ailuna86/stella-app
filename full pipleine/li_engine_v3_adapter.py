"""
li_engine_v3_adapter.py
=======================
NEW FILE — wraps li_engine_v2_adapter.py (frozen, preserved unchanged).

CHANGES vs v2
-------------
1. Extracts sessions_flagged counts from prior_profile.weakness_map before
   calling the frozen v2 adapter.
2. After v2 returns the updated profile, increments sessions_flagged for any
   weakness that also appeared in the prior session (matched by criterion +
   skill_tag pair).
3. Updates 'trend' field based on accumulated count:
     sessions_flagged >= 3  → "recurring"
     sessions_flagged == 2  → "persistent"
     else                   → preserve v2 trend
4. Exposes update_learner_profile_v3() as a drop-in replacement for
   update_learner_profile() from li_engine_v2_adapter.

WHY THIS MATTERS
----------------
pe_to_priority_directive_v2_v5 fires "recurring_error" priority only when
sessions_flagged >= _RECURRING_THRESHOLD (=3). Without accumulation this
threshold is never reached, so:
  - Practice Engine never adjusts difficulty for recurring weaknesses
  - Feedback Engine never surfaces "you have seen this error across sessions"
  - SkillsProgressReport.recurring_patterns is always empty
  - Students with persistent weaknesses appear as if each session is fresh

USAGE
-----
    from li_engine_v3_adapter import update_learner_profile_v3

    updated_profile = update_learner_profile_v3(
        session_data  = lie_input_dict,
        prior_profile = previous_session_profile,  # None on first session
    )
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from li_engine_v2_adapter import update_learner_profile  # noqa: E402  (frozen v2)

# Must match pe_to_priority_directive_v2_v5._RECURRING_THRESHOLD
_RECURRING_THRESHOLD = 3


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_prior_counts(prior_profile: Optional[Dict]) -> Dict[tuple, int]:
    """
    Return {(criterion, skill_tag): sessions_flagged} from the prior profile's
    weakness_map.  Returns empty dict if no prior profile.
    """
    if not prior_profile:
        return {}
    counts: Dict[tuple, int] = {}
    for w in prior_profile.get("weakness_map", []):
        key = (w.get("criterion"), w.get("skill_tag"))
        counts[key] = w.get("sessions_flagged", 0)
    return counts


def _apply_trend(w: Dict[str, Any], flagged: int) -> None:
    """Update 'trend' on a weakness entry based on accumulated sessions_flagged."""
    if flagged >= _RECURRING_THRESHOLD:
        w["trend"] = "recurring"
    elif flagged == 2:
        # Override only if v2 left it as 'stable' — don't downgrade 'resolved'
        if w.get("trend") not in ("resolved",):
            w["trend"] = "persistent"
    # else: keep whatever trend v2 set


# ── Main function ─────────────────────────────────────────────────────────────

def update_learner_profile_v3(
    student_id: str,
    session_id: str,
    priority_directive: Dict[str, Any],
    practice_session_result: Dict[str, Any],
    previous_learner_profile: Optional[Dict[str, Any]] = None,
    task_type: str = "task2",
) -> Dict[str, Any]:
    """
    Drop-in replacement for update_learner_profile() that accumulates
    sessions_flagged across sessions.

    Signature is identical to update_learner_profile() in li_engine_v2_adapter.py
    so it can be used as a direct swap in pipeline_runner_v6.py.

    Parameters
    ----------
    student_id                : student UUID
    session_id                : session UUID
    priority_directive        : PriorityDirective v2 dict
    practice_session_result   : PracticeSessionResult v2 dict
    previous_learner_profile  : LearnerProfile from previous session, or None
    task_type                 : "task2" (default)

    Returns
    -------
    Updated LearnerProfile dict with accumulated sessions_flagged and
    trend values reflecting persistence history.
    """
    # prior_profile alias for clarity
    prior_profile = previous_learner_profile

    # Step 1 — snapshot prior counts before v2 overwrites anything
    prior_counts = _extract_prior_counts(prior_profile)

    # Step 2 — call frozen v2 adapter with its exact keyword signature
    new_profile = update_learner_profile(
        student_id               = student_id,
        session_id               = session_id,
        priority_directive       = priority_directive,
        practice_session_result  = practice_session_result,
        previous_learner_profile = prior_profile,
        task_type                = task_type,
    )

    # Step 3 — accumulate sessions_flagged for persistent weaknesses
    for w in new_profile.get("weakness_map", []):
        key = (w.get("criterion"), w.get("skill_tag"))
        prior_n = prior_counts.get(key, 0)

        if prior_n > 0:
            # This weakness existed before — increment from prior count
            accumulated = prior_n + 1
            w["sessions_flagged"] = accumulated
        else:
            # New weakness this session — v2's value of 1 is correct
            accumulated = w.get("sessions_flagged", 1)

        _apply_trend(w, accumulated)

    # Step 4 — embed adapter metadata
    new_profile["_lie_adapter_version"] = "v3"
    new_profile["_prior_weakness_count"] = len(prior_counts)

    return new_profile
