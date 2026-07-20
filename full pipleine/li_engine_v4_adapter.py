"""
li_engine_v4_adapter.py
=======================
NEW FILE — wraps li_engine_v3_adapter.py (frozen, preserved unchanged).

CHANGES vs v3
-------------
F16  Criterion-level accumulation
     v3 matches weaknesses by (criterion, skill_tag) pair. When the GRA skill_tag
     changes each session (COMPARATIVE_FORM → SUBJECT_VERB_AGREEMENT → VERB_FORM),
     every GRA entry appears as a new weakness and sessions_flagged resets to 1.

     After 10 sessions where GRA is Band 4.0 and the PE primary limiter in every
     single session, the LIE has never produced sessions_flagged >= 2 for any GRA
     entry — so the directive never escalates GRA to "recurring_error" priority.

     Fix: track weaknesses at TWO levels simultaneously:
       1. Specific: (criterion, skill_tag) — unchanged from v3
       2. Criterion: (criterion, CRITERION_LEVEL) — increments whenever any weakness
          for that criterion appears, regardless of skill_tag

     A synthetic criterion-level entry sits alongside specific entries in weakness_map:
       { criterion, skill_tag="CRITERION_LEVEL", sessions_flagged=N,
         trend, _criterion_level=True }

     Once GRA/CRITERION_LEVEL reaches sessions_flagged >= 3, the directive adapter
     will escalate GRA to "recurring_error" priority (existing threshold logic,
     no directive adapter changes needed).

USAGE
-----
    from li_engine_v4_adapter import update_learner_profile_v4

    updated_profile = update_learner_profile_v4(
        student_id               = STUDENT_ID,
        session_id               = session_id,
        priority_directive       = directive,
        practice_session_result  = psr,
        previous_learner_profile = prior_profile,
    )
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from li_engine_v3_adapter import update_learner_profile_v3  # noqa: E402  (frozen v3)

_RECURRING_THRESHOLD  = 3    # must match pe_to_priority_directive_v2_v5
_CRITERION_LEVEL_TAG  = "CRITERION_LEVEL"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_criterion_counts(
    profile: Optional[Dict[str, Any]],
) -> Dict[str, int]:
    """
    Return { criterion: sessions_flagged } for criterion-level synthetic entries
    from the prior profile.  Returns {} if no prior profile or no entries.
    """
    if not profile:
        return {}
    counts: Dict[str, int] = {}
    for w in profile.get("weakness_map", []):
        if w.get("_criterion_level") and w.get("skill_tag") == _CRITERION_LEVEL_TAG:
            crit = w.get("criterion")
            if crit:
                counts[crit] = w.get("sessions_flagged", 0)
    return counts


def _get_criteria_with_weaknesses(profile: Dict[str, Any]) -> Set[str]:
    """
    Return the set of criteria that have at least one non-criterion-level
    weakness entry in the updated profile's weakness_map.
    """
    criteria: Set[str] = set()
    for w in profile.get("weakness_map", []):
        if not w.get("_criterion_level"):
            crit = w.get("criterion")
            if crit:
                criteria.add(crit)
    return criteria


def _apply_criterion_trend(sessions_flagged: int) -> str:
    if sessions_flagged >= _RECURRING_THRESHOLD:
        return "recurring"
    elif sessions_flagged == 2:
        return "persistent"
    return "stable"


def _upsert_criterion_level_entry(
    profile: Dict[str, Any],
    criterion: str,
    sessions_flagged: int,
) -> None:
    """
    Insert or update the criterion-level synthetic entry in weakness_map.
    Does not modify existing specific entries.
    """
    wm: List[Dict] = profile.setdefault("weakness_map", [])
    # Find existing criterion-level entry for this criterion
    for w in wm:
        if w.get("_criterion_level") and w.get("criterion") == criterion:
            w["sessions_flagged"] = sessions_flagged
            w["trend"] = _apply_criterion_trend(sessions_flagged)
            w["note"] = (
                f"{criterion} weakness has appeared in {sessions_flagged} "
                f"session{'s' if sessions_flagged != 1 else ''} "
                f"across multiple skill families"
            )
            return

    # New criterion-level entry
    wm.append({
        "criterion":        criterion,
        "skill_tag":        _CRITERION_LEVEL_TAG,
        "sessions_flagged": sessions_flagged,
        "trend":            _apply_criterion_trend(sessions_flagged),
        "_criterion_level": True,
        "note": (
            f"{criterion} weakness has appeared in {sessions_flagged} "
            f"session{'s' if sessions_flagged != 1 else ''} "
            f"across multiple skill families"
        ),
    })


# ── Main function ─────────────────────────────────────────────────────────────

def update_learner_profile_v4(
    student_id: str,
    session_id: str,
    priority_directive: Dict[str, Any],
    practice_session_result: Dict[str, Any],
    previous_learner_profile: Optional[Dict[str, Any]] = None,
    task_type: str = "task2",
) -> Dict[str, Any]:
    """
    Drop-in replacement for update_learner_profile_v3() that adds criterion-level
    accumulation (F16).

    For each criterion that has at least one weakness in the current session,
    a synthetic (criterion, CRITERION_LEVEL) entry is maintained in weakness_map
    with a cross-session sessions_flagged count that ignores skill_tag changes.

    All v3 logic (specific-pair accumulation, trend assignment, metadata) is
    preserved exactly — v3 is called first and the criterion-level entries are
    added on top.

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
    Updated LearnerProfile dict with v3 accumulation PLUS criterion-level entries.
    """
    # Step 1: snapshot criterion-level counts from prior profile
    prior_criterion_counts = _extract_criterion_counts(previous_learner_profile)

    # Step 2: call frozen v3 (which already calls frozen v2 inside)
    new_profile = update_learner_profile_v3(
        student_id               = student_id,
        session_id               = session_id,
        priority_directive       = priority_directive,
        practice_session_result  = practice_session_result,
        previous_learner_profile = previous_learner_profile,
        task_type                = task_type,
    )

    # Step 3: determine which criteria have weaknesses this session
    active_criteria = _get_criteria_with_weaknesses(new_profile)

    # Step 4: upsert criterion-level entries
    for criterion in active_criteria:
        prior_n = prior_criterion_counts.get(criterion, 0)
        new_n   = prior_n + 1
        _upsert_criterion_level_entry(new_profile, criterion, new_n)

    # Step 5: update metadata
    criterion_level_entries = [
        w for w in new_profile.get("weakness_map", [])
        if w.get("_criterion_level")
    ]
    new_profile["_lie_adapter_version"]      = "v4"
    new_profile["_criterion_level_entries"]  = len(criterion_level_entries)

    return new_profile
