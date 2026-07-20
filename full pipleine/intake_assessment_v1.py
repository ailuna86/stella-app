"""
intake_assessment_v1.py
=======================
NEW FILE — no LLM dependency. Pure question → directive-adjustment mapping.

PURPOSE
-------
Collects student context before each session. Output feeds into:
  - goal_band in the directive (replaces hardcoded 7.0)
  - Initial weakness_map priors in LearnerProfile (session 1 only)
  - Directive priority adjustments (session 2+)
  - PE difficulty preset (via directive)

SESSION 1
---------
  Four fixed multiple-choice questions (target band, weeks to exam, experience,
  self-reported challenge). Answers build a seed LearnerProfile so the first
  directive is oriented before the LIE has any real data.

SESSION 2+
-----------
  1–2 LIE-linked questions generated from the prior weakness_map.
  Maximum 2 questions — should feel like a check-in, not a form.

HEADLESS MODE (pipeline runner)
---------------------------------
  load_session1_intake(goal_band) returns a preset intake with default answers.
  To supply real answers, pass intake_answers={} to load_session1_intake().
  In a web app, show the questions and pass the user's answers back.

USAGE
-----
    from intake_assessment_v1 import (
        load_session1_intake,
        generate_return_questions,
        build_seed_profile_from_intake,
        apply_intake_to_directive,
        SESSION_1_QUESTIONS,
    )

    # Session 1 (headless — preset answers):
    intake = load_session1_intake(goal_band=7.0)
    seed   = build_seed_profile_from_intake(intake)

    # Session 2+ (headless — generated questions):
    intake = generate_return_questions(prior_profile, prior_practice_results, session_n=2)

    # Adjust directive after session 2+ intake:
    directive = apply_intake_to_directive(directive, intake, prior_profile)
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ─────────────────────────────────────────────────────────────────────────────
# SESSION 1 QUESTION DEFINITIONS
# ─────────────────────────────────────────────────────────────────────────────

SESSION_1_QUESTIONS: List[Dict[str, Any]] = [
    {
        "id":      "target_band",
        "text":    "What IELTS band score do you need?",
        "options": ["5.0", "5.5", "6.0", "6.5", "7.0", "7.5", "8.0+"],
        "maps_to": "goal_band",
        "multi_select": False,
    },
    {
        "id":      "exam_weeks",
        "text":    "How many weeks until your exam?",
        "options": ["< 4 weeks", "4–8 weeks", "2–3 months", "3–6 months", "6+ months"],
        "maps_to": "urgency_flag",
        "multi_select": False,
    },
    {
        "id":      "experience",
        "text":    "How would you describe your English writing experience?",
        "options": [
            "Beginner — I find basic grammar difficult",
            "Intermediate — I make grammar mistakes but can express ideas",
            "Upper-intermediate — my ideas are clear but my language is not precise enough",
            "Advanced — I need fine-tuning for IELTS band requirements",
        ],
        "maps_to": "experience_level",
        "multi_select": False,
    },
    {
        "id":      "challenge",
        "text":    "What do you find hardest about IELTS writing?",
        "options": [
            "Grammar (verb forms, sentence structure)",
            "Vocabulary (word choice, collocations)",
            "Organising my ideas clearly",
            "Answering the question fully",
        ],
        "maps_to": "self_reported_challenge",
        "multi_select": True,
    },
]

# Default headless answers for session 1 (used when running pipeline without UI)
_DEFAULT_SESSION_1_ANSWERS: Dict[str, Any] = {
    "target_band": "7.0",
    "exam_weeks":  "2–3 months",
    "experience":  "Intermediate — I make grammar mistakes but can express ideas",
    "challenge":   ["Grammar (verb forms, sentence structure)"],
}

# ── Band option → float mapping ───────────────────────────────────────────────
_BAND_OPTION_TO_FLOAT: Dict[str, float] = {
    "5.0": 5.0, "5.5": 5.5, "6.0": 6.0, "6.5": 6.5,
    "7.0": 7.0, "7.5": 7.5, "8.0+": 8.0,
}

# ── Urgency mapping ───────────────────────────────────────────────────────────
_URGENCY_FLAGS: Dict[str, str] = {
    "< 4 weeks":    "high",
    "4–8 weeks":    "medium",
    "2–3 months":   "medium",
    "3–6 months":   "low",
    "6+ months":    "low",
}

# ── Challenge → criterion boost mapping ──────────────────────────────────────
_CHALLENGE_TO_CRITERION: Dict[str, str] = {
    "Grammar (verb forms, sentence structure)":  "grammatical_range_accuracy",
    "Vocabulary (word choice, collocations)":     "lexical_resource",
    "Organising my ideas clearly":               "coherence_cohesion",
    "Answering the question fully":              "task_achievement",
}

# ── Experience → seed family priors ──────────────────────────────────────────
_EXPERIENCE_SEED_FAMILIES: Dict[str, List[str]] = {
    "Beginner — I find basic grammar difficult": [
        "VERB_FORM", "CLAUSE_STRUCTURE", "SUBJECT_VERB_AGREEMENT"
    ],
    "Intermediate — I make grammar mistakes but can express ideas": [
        "VERB_FORM", "SUBJECT_VERB_AGREEMENT"
    ],
    "Upper-intermediate — my ideas are clear but my language is not precise enough": [
        "COLLOCATION", "LEXICAL_PRECISION"
    ],
    "Advanced — I need fine-tuning for IELTS band requirements": [],
}


# ─────────────────────────────────────────────────────────────────────────────
# SESSION 1 INTAKE LOADER
# ─────────────────────────────────────────────────────────────────────────────

def load_session1_intake(
    goal_band: float = 7.0,
    intake_answers: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build a session-1 intake dict.

    In headless mode (intake_answers=None), returns preset default answers.
    The pipeline uses this when there is no UI to collect real answers.

    In UI mode, pass intake_answers={question_id: answer} from the front end.

    Returns
    -------
    intake dict with:
      schema         "INTAKE_V1_SESSION1"
      session_type   "session_1"
      questions      the 4 SESSION_1_QUESTIONS
      responses      {question_id: answer}
      goal_band      float — from answers if provided, else from goal_band param
      generated_at   ISO timestamp
      headless       True if default answers used
    """
    answers  = intake_answers if intake_answers else _DEFAULT_SESSION_1_ANSWERS.copy()
    headless = intake_answers is None

    # Override goal_band from answers if available
    target_band_str = answers.get("target_band", "")
    resolved_band   = _BAND_OPTION_TO_FLOAT.get(target_band_str, goal_band)

    return {
        "schema":       "INTAKE_V1_SESSION1",
        "session_type": "session_1",
        "questions":    SESSION_1_QUESTIONS,
        "responses":    answers,
        "goal_band":    resolved_band,
        "headless":     headless,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# SEED PROFILE BUILDER (session 1 only)
# ─────────────────────────────────────────────────────────────────────────────

def build_seed_profile_from_intake(intake: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Convert session-1 intake answers into a lightweight seed LearnerProfile.
    This becomes prior_profile for the first session's directive.

    Returns None if intake is not session-1 type (safe to call unconditionally).

    The seed profile has sessions_flagged=0 for all seeded weaknesses — it
    exists only to orient the first directive, not to claim the student has
    exhibited these errors yet.
    """
    if intake.get("session_type") != "session_1":
        return None

    responses   = intake.get("responses", {})
    experience  = responses.get("experience", "")
    challenges  = responses.get("challenge", [])
    if isinstance(challenges, str):
        challenges = [challenges]
    goal_band   = intake.get("goal_band", 7.0)
    urgency     = _URGENCY_FLAGS.get(responses.get("exam_weeks", ""), "medium")

    # Build seed weakness_map from experience level
    seed_families = _EXPERIENCE_SEED_FAMILIES.get(experience, [])
    weakness_map: List[Dict[str, Any]] = []

    for family in seed_families:
        criterion = (
            "grammatical_range_accuracy"
            if family in ("VERB_FORM", "CLAUSE_STRUCTURE", "SUBJECT_VERB_AGREEMENT",
                          "COMPARATIVE_FORM", "ARTICLE_DETERMINER", "WORD_FORM")
            else "lexical_resource"
        )
        weakness_map.append({
            "criterion":        criterion,
            "skill_tag":        family,
            "sessions_flagged": 0,       # seeded, not yet observed
            "trend":            "seeded",
            "_seeded":          True,
            "note":             "Seed from session-1 intake — not yet observed in essays",
        })

    # Build challenge-derived priority boosts (stored for directive adapter)
    challenge_boosts: Dict[str, float] = {}
    for ch in challenges:
        crit = _CHALLENGE_TO_CRITERION.get(ch)
        if crit:
            challenge_boosts[crit] = challenge_boosts.get(crit, 0.0) + 0.5

    return {
        "schema_version":     "LEARNER_PROFILE_V1_SEED",
        "student_id":         None,         # will be set by LIE on first real run
        "sessions_analyzed":  0,
        "goal_band":          goal_band,
        "urgency_flag":       urgency,
        "experience_level":   experience,
        "self_reported_challenge": challenges,
        "challenge_boosts":   challenge_boosts,  # used by apply_intake_to_directive
        "weakness_map":       weakness_map,
        "_seeded":            True,
        "_seeded_at":         intake.get("generated_at", ""),
    }


# ─────────────────────────────────────────────────────────────────────────────
# RETURN SESSION QUESTIONS (session 2+)
# ─────────────────────────────────────────────────────────────────────────────

_CRITERION_LABELS: Dict[str, str] = {
    "grammatical_range_accuracy": "Grammar",
    "lexical_resource":           "Vocabulary",
    "coherence_cohesion":         "Coherence & Cohesion",
    "task_achievement":           "Task Achievement",
}

_SKILL_TAG_LABELS: Dict[str, str] = {
    "VERB_FORM":                "verb form errors (e.g. 'has to spent')",
    "SUBJECT_VERB_AGREEMENT":   "subject-verb agreement errors (e.g. 'it have')",
    "COMPARATIVE_FORM":         "comparative form errors (e.g. 'more stronger')",
    "CLAUSE_STRUCTURE":         "clause structure errors",
    "ARTICLE_DETERMINER":       "article/determiner errors",
    "COLLOCATION":              "word combination errors",
    "LEXICAL_PRECISION":        "vocabulary precision issues",
    "TASK_COMPLETENESS":        "task completion issues",
    "LEXICAL_CONTROL":          "vocabulary control issues",
}


def generate_return_questions(
    prior_profile: Dict[str, Any],
    prior_practice_results: Optional[Dict[str, Any]],
    session_n: int,
) -> Dict[str, Any]:
    """
    Generate 1–2 LIE-linked check-in questions for sessions 2+.

    Rules:
      R1 — Recurring weakness acknowledgement
           If any weakness has sessions_flagged >= 2 and trend in (recurring, persistent)
      R2 — Practice accuracy check
           If prior practice accuracy for a criterion was < 0.50
      R3 — Mode preference (every 3rd return session: session 4, 7, 10 ...)

    Maximum 2 questions returned.
    Headless mode returns questions with default answers pre-filled.

    Returns
    -------
    intake dict with schema "INTAKE_V1_RETURN"
    """
    questions: List[Dict[str, Any]] = []
    default_responses: Dict[str, Any] = {}

    weakness_map = prior_profile.get("weakness_map", [])

    # R1 — recurring weakness
    recurring = [
        w for w in weakness_map
        if not w.get("_criterion_level")
        and not w.get("_seeded")
        and w.get("sessions_flagged", 0) >= 2
        and w.get("trend") in ("recurring", "persistent")
    ]
    if recurring and len(questions) < 2:
        top = sorted(recurring, key=lambda w: -w.get("sessions_flagged", 0))[0]
        skill_label = _SKILL_TAG_LABELS.get(
            top.get("skill_tag", ""),
            top.get("skill_tag", "this type of error"),
        )
        q = {
            "id":      "r1_recurring",
            "rule":    "R1",
            "text":    (
                f"Last session we noticed repeated {skill_label} in your writing. "
                f"Does this feel like your biggest challenge right now?"
            ),
            "options": [
                "Yes, this is still my main problem",
                "No, I think I'm improving on that",
                "I'm not sure — I need more practice to tell",
            ],
            "maps_to": "recurring_acknowledgement",
            "context": {"criterion": top.get("criterion"), "skill_tag": top.get("skill_tag")},
            "multi_select": False,
        }
        questions.append(q)
        default_responses["r1_recurring"] = "I'm not sure — I need more practice to tell"

    # R2 — practice accuracy
    if prior_practice_results and len(questions) < 2:
        acc = prior_practice_results.get("overall_accuracy", 1.0)
        crit_acc: Dict[str, float] = {}
        # FIX-R2-001: by_criterion is a dict {criterion: {...accuracy...}},
        # not a list — iterate .items() instead of iterating keys.
        by_crit = prior_practice_results.get("by_criterion", {})
        if isinstance(by_crit, dict):
            for c, item in by_crit.items():
                a = float(item.get("accuracy", 1.0)) if isinstance(item, dict) else 1.0
                if c:
                    crit_acc[c] = a
        else:  # legacy list format: [{criterion, accuracy}, ...]
            for item in by_crit:
                c = item.get("criterion", "") if isinstance(item, dict) else ""
                a = float(item.get("accuracy", 1.0)) if isinstance(item, dict) else 1.0
                if c:
                    crit_acc[c] = a
        low_crit = [c for c, a in crit_acc.items() if a < 0.50]
        if not low_crit and acc < 0.50:
            low_crit = ["grammatical_range_accuracy"]  # fallback to GRA

        if low_crit:
            crit = low_crit[0]
            crit_label = _CRITERION_LABELS.get(crit, crit)
            crit_acc_pct = int((crit_acc.get(crit, acc)) * 100)
            q = {
                "id":      "r2_practice_confidence",
                "rule":    "R2",
                "text":    (
                    f"Your {crit_label} practice accuracy was {crit_acc_pct}% last session. "
                    f"How confident do you feel about it now?"
                ),
                "options": [
                    "Still struggling — I need more practice",
                    "Getting better — I understand some of it",
                    "I think I understand it — ready for harder exercises",
                ],
                "maps_to": "practice_confidence",
                "context": {"criterion": crit, "accuracy": crit_acc_pct},
                "multi_select": False,
            }
            questions.append(q)
            default_responses["r2_practice_confidence"] = "Getting better — I understand some of it"

    # R3 — mode preference (every 3rd return session: session 4, 7, 10 ...)
    if session_n >= 4 and (session_n - 1) % 3 == 0 and len(questions) < 2:
        q = {
            "id":      "r3_mode_preference",
            "rule":    "R3",
            "text":    "What would you like to focus on today?",
            "options": [
                "More grammar and vocabulary drills",
                "Review what I keep getting wrong",
                "Push harder — I want more challenging exercises",
            ],
            "maps_to": "mode_preference",
            "multi_select": False,
        }
        questions.append(q)
        default_responses["r3_mode_preference"] = "More grammar and vocabulary drills"

    return {
        "schema":       "INTAKE_V1_RETURN",
        "session_type": "return",
        "session_n":    session_n,
        "questions":    questions,
        "responses":    default_responses,  # headless defaults
        "headless":     True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# DIRECTIVE ADJUSTER
# ─────────────────────────────────────────────────────────────────────────────

def apply_intake_to_directive(
    directive: Dict[str, Any],
    intake: Dict[str, Any],
    prior_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Adjust directive focus_area priority weights based on intake answers.

    Mutations (does NOT change PE, scorer, or band targets):
    - challenge_boosts from session-1 seed profile → boost matching criterion rank
    - r1_recurring "Yes" → boost recurring criterion priority rank by 1
    - r2_practice_confidence "Still struggling" → lower PE recommended_difficulty for that criterion
    - r3_mode_preference "Review wrong" → boost criteria with highest sessions_flagged
    - goal_band from intake → update directive goal_band if different

    Mutates directive in place. Returns directive.
    """
    d = directive
    responses   = intake.get("responses", {})
    session_type = intake.get("session_type", "")

    # --- Update goal_band ---
    intake_band = intake.get("goal_band")
    if intake_band and intake_band != d.get("goal_band"):
        d["goal_band"]            = intake_band
        d["_intake_goal_band_set"] = True

    focus_areas: List[Dict] = d.get("focus_areas", [])
    if not focus_areas:
        return d

    def _boost_criterion(criterion: str, rank_delta: int = -1) -> None:
        """Move a criterion closer to rank 1 (lower rank number = higher priority)."""
        target = next((fa for fa in focus_areas if fa.get("criterion") == criterion), None)
        if not target:
            return
        current_rank = target.get("rank", 99)
        new_rank = max(1, current_rank + rank_delta)
        # Swap ranks with whatever's at new_rank
        for fa in focus_areas:
            if fa is not target and fa.get("rank") == new_rank:
                fa["rank"] = current_rank
                break
        target["rank"] = new_rank

    def _lower_difficulty(criterion: str) -> None:
        """Lower recommended_difficulty for a criterion by one step."""
        diff_order = ["beginner", "elementary", "intermediate", "advanced"]
        target = next((fa for fa in focus_areas if fa.get("criterion") == criterion), None)
        if not target:
            return
        current = target.get("recommended_difficulty", "intermediate").lower()
        try:
            idx = diff_order.index(current)
            if idx > 0:
                target["recommended_difficulty"] = diff_order[idx - 1]
                target["_intake_difficulty_lowered"] = True
        except ValueError:
            pass

    # --- Session 1: apply challenge boosts from seed profile ---
    if session_type == "session_1" and prior_profile and prior_profile.get("_seeded"):
        challenge_boosts: Dict[str, float] = prior_profile.get("challenge_boosts", {})
        for criterion, boost in challenge_boosts.items():
            if boost >= 0.5:
                _boost_criterion(criterion, rank_delta=-1)

    # --- Session 2+: apply return-question responses ---

    # R1: recurring acknowledgement
    r1 = responses.get("r1_recurring", "")
    r1_ctx = next(
        (q.get("context", {}) for q in intake.get("questions", []) if q.get("id") == "r1_recurring"),
        {}
    )
    if "yes" in r1.lower() and r1_ctx.get("criterion"):
        _boost_criterion(r1_ctx["criterion"], rank_delta=-1)
        d["_intake_recurring_boosted"] = r1_ctx["criterion"]

    # R2: practice confidence → lower difficulty
    r2 = responses.get("r2_practice_confidence", "")
    r2_ctx = next(
        (q.get("context", {}) for q in intake.get("questions", []) if q.get("id") == "r2_practice_confidence"),
        {}
    )
    if "still struggling" in r2.lower() and r2_ctx.get("criterion"):
        _lower_difficulty(r2_ctx["criterion"])

    # R3: mode preference
    r3 = responses.get("r3_mode_preference", "")
    if "review what i keep" in r3.lower() and prior_profile:
        # Boost the criterion with highest sessions_flagged (non-criterion-level)
        wm = [
            w for w in prior_profile.get("weakness_map", [])
            if not w.get("_criterion_level") and not w.get("_seeded")
        ]
        if wm:
            top = max(wm, key=lambda w: w.get("sessions_flagged", 0))
            _boost_criterion(top.get("criterion", ""), rank_delta=-1)
    elif "harder exercises" in r3.lower():
        for fa in focus_areas:
            diff = fa.get("recommended_difficulty", "intermediate").lower()
            diff_order = ["beginner", "elementary", "intermediate", "advanced"]
            try:
                idx = diff_order.index(diff)
                if idx < len(diff_order) - 1:
                    fa["recommended_difficulty"] = diff_order[idx + 1]
            except ValueError:
                pass

    d["_intake_applied"]  = True
    d["_intake_session_type"] = session_type
    return d
