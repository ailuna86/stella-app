"""
practice_engine_v4.py
======================
STANDALONE FILE — no imports from practice_engine_v3.py.
All logic is inlined from v3. Only the targeted B2 change below was made.

CHANGES vs practice_engine_v3.py
----------------------------------
B2 (F24)  CEFR floor emergency fallback redesign.

          WHY THE CHANGE IS NEEDED
          -------------------------
          v3 had two layers of CEFR fallback in filter_bank():
            1. If filtered_fallback was empty → relax floor by one level (never fired
               with bank v8 because B1/B2 levels always exist in the fallback order).
            2. Last-resort "return criterion_pool" at the end of the loop — this pool
               is ALL exercises for the criterion with NO CEFR filter. When a specific
               family (e.g. EXAMPLE_QUALITY for TA) had no exercises at B1/B2, the
               loop exhausted all valid CEFR levels without returning, then fell through
               to "return criterion_pool" which included C1 exercises. This caused
               a C1 EXAMPLE_QUALITY exercise to be served to a B1 student in session 019.

          Fix (two changes):

          1. filter_bank() — last-resort return:
             When min_cefr or max_cefr is active, do NOT return criterion_pool
             (which ignores CEFR). Instead return an empty list so the caller
             knows no valid exercises were found.

          2. build_exercise_queue() — family fallback:
             When filter_bank() returns [] for a slot, try other families
             within the same criterion at the same CEFR range (from
             _CRITERION_FAMILIES map). NEVER relax the CEFR floor.
             If all families for the criterion return [] at the correct CEFR:
             skip the slot entirely (log it in slots_skipped_no_stock).

          _CRITERION_FAMILIES — new constant:
             Maps each IELTS criterion to the list of exercise bank families
             that serve it. Used for family-level fallback in build_exercise_queue.

          set_session_length() — QA additions:
             family_fallbacks_used : list of {"requested_family", "used_family", ...}
             slots_skipped_no_stock: list of skipped slots when no family had stock

F24 (retained from v3):
          filter_bank gains min_cefr / max_cefr parameters.
          start_session reads _cefr_floor / _cefr_ceiling from directive.

F25 (retained from v3):
          get_next_exercise() includes "explanation" and "answer" in return dict.

USAGE
-----
    from practice_engine_v4 import PracticeEngineV4

    pe = PracticeEngineV4(
        bank_path="<path to va_exercise_bank_v10f_approved.jsonl>",
        session_dir="./practice_sessions"
    )
    welcome = pe.start_session(student_id, directive, learner_profile)
    plan    = pe.set_session_length(welcome["session_id"], minutes=10)
    while True:
        ex = pe.get_next_exercise(plan["session_id"])
        if ex is None:
            break
        feedback = pe.submit_answer(plan["session_id"], ex["exercise_id"], answer)
    results = pe.get_session_results(plan["session_id"])
    psr     = pe.submit_survey(plan["session_id"], exercise_ratings=[])
"""
from __future__ import annotations

import json
import random
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# =============================================================================
# CATEGORY / CEFR MAPPINGS  (from v3, unchanged)
# =============================================================================

_CATEGORY_TO_CRITERION: Dict[str, str] = {
    "grammar":            "grammatical_range_accuracy",
    "lexical_resource":   "lexical_resource",
    "coherence_cohesion": "coherence_cohesion",
    "argumentation":      "task_achievement",
    "task_response":      "task_achievement",
}

_CRITERION_TO_CATEGORIES: Dict[str, Set[str]] = {}
for _cat, _crit in _CATEGORY_TO_CRITERION.items():
    _CRITERION_TO_CATEGORIES.setdefault(_crit, set()).add(_cat)

_CEFR_TO_DIFFICULTY: Dict[str, str] = {
    "A2": "foundational",
    "B1": "consolidation",
    "B2": "stretch",
    "C1": "advanced",
}
_DIFFICULTY_TO_CEFR: Dict[str, str] = {v: k for k, v in _CEFR_TO_DIFFICULTY.items()}

_CEFR_ORDER_RANK: Dict[str, int] = {"A2": 0, "B1": 1, "B2": 2, "C1": 3}
_CEFR_BY_RANK:   Dict[int, str]  = {v: k for k, v in _CEFR_ORDER_RANK.items()}

_CEFR_FALLBACK_ORDER: Dict[str, List[str]] = {
    "A2": ["A2", "B1"],
    "B1": ["B1", "A2", "B2"],
    "B2": ["B2", "B1", "C1"],
    "C1": ["C1", "B2"],
}

# B2 fix: maps each IELTS criterion to all exercise bank family codes that serve it.
# Used for family-level fallback when a requested family has no B1/B2 stock.
_CRITERION_FAMILIES: Dict[str, List[str]] = {
    "grammatical_range_accuracy": [
        "ARTICLE_DETERMINER", "CLAUSE_STRUCTURE", "CONDITIONALS",
        "COUNTABLE_UNCOUNTABLE", "FRAGMENTS_RUNONS", "MODALS",
        "NOUN_NUMBER_COUNTABILITY", "PASSIVE_VOICE", "PREPOSITIONS",
        "PREPOSITION_PATTERN", "PRONOUN_REFERENCE", "PUNCTUATION",
        "RELATIVE_CLAUSES", "SUBJECT_VERB_AGREEMENT", "VERB_FORM",
        "VERB_TENSE", "WORD_FORM", "WORD_ORDER", "COMPARATIVES",
        "SENTENCE_VARIETY",
    ],
    "lexical_resource": [
        "ACADEMIC_VOCABULARY", "COLLOCATION", "FORMALITY",
        "IDIOMATIC_CONTROL", "LEXICAL_PRECISION", "PRECISION",
        "REGISTER_CONTROL", "REPETITION", "SEMANTIC_COMBINATION",
        "SLAVIC_TRANSFER_AWKWARD_PHRASE", "VERB_NOUN_COMBINATION",
        "WORD_CHOICE", "WORD_FORMATION",
    ],
    "task_achievement": [
        "ARGUMENT_STRUCTURE", "BALANCED_DISCUSSION", "CLAIM_SUPPORT",
        "CONCLUSION_LOGIC", "COUNTERARGUMENT", "DATA_DESCRIPTION",
        "EXAMPLE_QUALITY", "HEDGING", "INTRODUCTION_CONCLUSION",
        "OVERGENERALIZATION", "PARAPHRASE", "POSITION_CLARITY",
        "SENTENCE_VARIETY", "SUPPORTING_SENTENCE", "TASK_RESPONSE",
        "TASK_RESPONSE_COVERAGE",
    ],
    "coherence_cohesion": [
        "CAUSAL_REASONING", "CAUSE_EFFECT_REASONING", "COMPARATIVES",
        "DISCOURSE_LINKING", "PARAGRAPH_PROGRESS", "REFERENCE_COHESION",
        "TOPIC_SENTENCE", "TRANSITIONS",
    ],
}

# Module-level variable to pass family-fallback metadata from build_exercise_queue
# to set_session_length without changing the function return type.
# Single-threaded use only (standard for this pipeline).
_last_queue_metadata: Dict[str, List] = {
    "family_fallbacks_used":  [],
    "slots_skipped_no_stock": [],
}

# =============================================================================
# PRIORITY WEIGHTS / DISPLAY LABELS  (from v3, unchanged)
# =============================================================================

_PRIORITY_WEIGHTS: Dict[str, float] = {
    "high_impact_gap":  0.50,
    "recurring_error":  0.35,
    "exam_urgency":     0.55,
    "plateau_break":    0.25,
    "new_weakness":     0.15,
}

_PRIORITY_EXPLANATIONS: Dict[str, str] = {
    "high_impact_gap":  "is your biggest gap from your target band right now",
    "recurring_error":  "has appeared repeatedly in your recent essays",
    "exam_urgency":     "needs urgent attention ahead of your exam",
    "plateau_break":    "you have been stable here — time to push through to the next level",
    "new_weakness":     "was flagged for the first time in your latest essay",
}

_TIME_BUDGETS_SECONDS: Dict[int, int] = {5: 300, 10: 600, 15: 900}
_AVG_EXERCISE_SECONDS: int = 90

_CRITERION_LABELS: Dict[str, str] = {
    "grammatical_range_accuracy": "Grammar (GRA)",
    "task_achievement":           "Task Achievement (TA)",
    "coherence_cohesion":         "Coherence & Cohesion (CC)",
    "lexical_resource":           "Vocabulary (LR)",
}

_EXERCISE_TYPE_LABELS: Dict[str, str] = {
    "mcq":                 "Multiple Choice",
    "error_correction":    "Error Correction",
    "short_rewrite":       "Short Rewrite",
    "classification":      "Classification",
    "gap_bridge_sentence": "Gap Fill",
}

SURVEY_REASON_TAGS: List[str] = [
    "forgot_the_rule",
    "understood_after_explanation",
    "tricky_question",
    "topic_unfamiliar",
    "time_pressure",
    "careless_mistake",
]

# =============================================================================
# BANK LOADING  (from v3, unchanged)
# =============================================================================

def load_bank(bank_path: str) -> List[Dict[str, Any]]:
    """Load exercises from a JSONL file. Only active=True exercises included."""
    exercises: List[Dict[str, Any]] = []
    path = Path(bank_path)
    if not path.exists():
        raise FileNotFoundError(f"Exercise bank not found: {bank_path}")
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ex = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ex.get("active", True):
                exercises.append(ex)
    return exercises


# =============================================================================
# EXERCISE FILTERING & SELECTION  (from v3 + B2 fix)
# =============================================================================

def _skill_matches(exercise: Dict[str, Any], skill_tag: str) -> bool:
    if not skill_tag:
        return True
    hint   = re.sub(r"[_\-\s]+", "", skill_tag).lower()
    family = re.sub(r"[_\-\s]+", "", exercise.get("family", "")).lower()
    micro  = re.sub(r"[_\-\s]+", "", exercise.get("micro_skill", "")).lower()
    return hint in family or family in hint or hint in micro or micro in hint


def filter_bank(
    bank: List[Dict[str, Any]],
    criterion: str,
    difficulty: str = "consolidation",
    skill_tag: str = "",
    exclude_ids: Optional[Set[str]] = None,
    min_cefr: Optional[str] = None,
    max_cefr: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Return exercises matching criterion and difficulty, preferring skill_tag.

    F24: When min_cefr/max_cefr are supplied, the CEFR fallback order is
    filtered to exclude levels outside [min_cefr, max_cefr].

    B2 fix vs v3: When CEFR floor/ceiling is active and no exercises exist
    within the valid range, return [] instead of falling through to
    criterion_pool (which ignores CEFR). The caller (build_exercise_queue)
    handles the empty result by trying a different family — never relaxing
    the CEFR floor.
    """
    exclude_ids = exclude_ids or set()
    target_cats = _CRITERION_TO_CATEGORIES.get(criterion, set())
    target_cefr = _DIFFICULTY_TO_CEFR.get(difficulty, "B1")

    criterion_pool = [
        ex for ex in bank
        if ex.get("category", "") in target_cats
        and ex.get("exercise_id", "") not in exclude_ids
    ]

    def _by_cefr(pool: List[Dict], cefr: str) -> List[Dict]:
        return [ex for ex in pool if ex.get("cefr_level", "") == cefr]

    raw_fallback = _CEFR_FALLBACK_ORDER.get(target_cefr, [target_cefr])

    if min_cefr or max_cefr:
        min_rank = _CEFR_ORDER_RANK.get(min_cefr, 0) if min_cefr else 0
        max_rank = _CEFR_ORDER_RANK.get(max_cefr, 3) if max_cefr else 3
        filtered_fallback = [
            c for c in raw_fallback
            if min_rank <= _CEFR_ORDER_RANK.get(c, 0) <= max_rank
        ]
        if not filtered_fallback:
            # No CEFR levels in range — return empty so caller can try different family
            return []
        fallback_order = filtered_fallback
    else:
        fallback_order = raw_fallback

    for cefr in fallback_order:
        level_pool = _by_cefr(criterion_pool, cefr)
        if not level_pool:
            continue
        if skill_tag:
            tagged = [ex for ex in level_pool if _skill_matches(ex, skill_tag)]
            if tagged:
                return tagged
        return level_pool

    # B2 fix: when CEFR floor is active, do NOT return criterion_pool here
    # (which would ignore the floor). Return [] so build_exercise_queue can
    # try a different family at the correct CEFR range.
    if min_cefr or max_cefr:
        return []

    # No CEFR constraint — original v3 last resort: all criterion exercises
    return criterion_pool


# =============================================================================
# ALLOCATION  (from v3, unchanged)
# =============================================================================

def compute_allocation(
    focus_areas: List[Dict[str, Any]],
    minutes: int,
) -> List[Dict[str, Any]]:
    available_seconds = _TIME_BUDGETS_SECONDS.get(minutes, 600)
    total_ex = max(len(focus_areas), available_seconds // _AVG_EXERCISE_SECONDS)
    if not focus_areas:
        return []
    raw_weights = [
        _PRIORITY_WEIGHTS.get(fa.get("priority_reason", ""), 0.25)
        for fa in focus_areas
    ]
    total_w = sum(raw_weights) or 1.0
    norm_w  = [w / total_w for w in raw_weights]
    allocation: List[Dict[str, Any]] = []
    assigned = 0
    for i, (fa, weight) in enumerate(zip(focus_areas, norm_w)):
        if i == len(focus_areas) - 1:
            count = max(1, total_ex - assigned)
        else:
            count = max(1, round(weight * total_ex))
        assigned += count
        allocation.append({
            "rank":            fa.get("rank", i + 1),
            "criterion":       fa.get("criterion", ""),
            "skill_tag":       fa.get("skill_tag", ""),
            "difficulty":      fa.get("recommended_difficulty", "consolidation"),
            "count":           count,
            "priority_reason": fa.get("priority_reason", ""),
            "current_band":    fa.get("current_band"),
            "target_band":     fa.get("target_band"),
        })
    return allocation


def build_exercise_queue(
    bank: List[Dict[str, Any]],
    allocation: List[Dict[str, Any]],
    exclude_ids: Optional[Set[str]] = None,
    min_cefr: Optional[str] = None,
    max_cefr: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Select exercises per allocation slots and interleave round-robin.

    B2 fix: When filter_bank returns [] for a requested family:
      1. Try other families within the same criterion at the SAME CEFR range.
      2. If all families exhausted at correct CEFR: skip slot entirely.
      NEVER relax min_cefr — no CEFR violation allowed.

    Sets module-level _last_queue_metadata with:
      family_fallbacks_used  : list of fallbacks that fired
      slots_skipped_no_stock : list of slots with zero valid exercises
    """
    global _last_queue_metadata
    exclude_ids           = set(exclude_ids or set())
    family_fallbacks_used: List[Dict]  = []
    slots_skipped:        List[Dict]  = []
    groups: List[List[Dict[str, Any]]] = []

    for slot in allocation:
        criterion      = slot["criterion"]
        requested_tag  = slot["skill_tag"]

        # First attempt: requested family at correct CEFR
        pool = filter_bank(
            bank,
            criterion   = criterion,
            difficulty  = slot["difficulty"],
            skill_tag   = requested_tag,
            exclude_ids = exclude_ids,
            min_cefr    = min_cefr,
            max_cefr    = max_cefr,
        )

        # B2 fix: if empty, try alternative families for the same criterion
        if not pool and (min_cefr or max_cefr):
            alt_families = _CRITERION_FAMILIES.get(criterion, [])
            for alt_family in alt_families:
                # Skip the family we already tried
                if alt_family.lower() == (requested_tag or "").lower():
                    continue
                # Also skip if the requested_tag softly matches this alt (avoid re-trying)
                if requested_tag and _skill_matches(
                    {"family": alt_family, "micro_skill": ""}, requested_tag
                ):
                    continue

                pool = filter_bank(
                    bank,
                    criterion   = criterion,
                    difficulty  = slot["difficulty"],
                    skill_tag   = alt_family,
                    exclude_ids = exclude_ids,
                    min_cefr    = min_cefr,
                    max_cefr    = max_cefr,
                )
                if pool:
                    family_fallbacks_used.append({
                        "requested_family": requested_tag,
                        "used_family":      alt_family,
                        "criterion":        criterion,
                        "reason": (
                            f"No exercises at CEFR {min_cefr}–{max_cefr} "
                            f"for family '{requested_tag}'. "
                            f"Serving '{alt_family}' at correct CEFR instead."
                        ),
                    })
                    break

        if not pool:
            # No exercises at all for this criterion at the correct CEFR
            slots_skipped.append({
                "criterion":        criterion,
                "requested_family": requested_tag,
                "min_cefr":         min_cefr,
                "max_cefr":         max_cefr,
                "note": (
                    f"Slot skipped — no exercises at CEFR {min_cefr}–{max_cefr} "
                    f"for criterion '{criterion}' (any family). "
                    "CEFR floor NOT relaxed."
                ),
            })
            continue  # skip slot entirely — no CEFR violation

        random.shuffle(pool)
        selected = pool[: slot["count"]]
        exclude_ids.update(ex["exercise_id"] for ex in selected)
        if selected:
            groups.append(selected)

    # Store metadata for set_session_length to read
    _last_queue_metadata = {
        "family_fallbacks_used":  family_fallbacks_used,
        "slots_skipped_no_stock": slots_skipped,
    }

    # Round-robin interleave
    queue: List[Dict[str, Any]] = []
    max_len = max((len(g) for g in groups), default=0)
    for i in range(max_len):
        for group in groups:
            if i < len(group):
                queue.append(group[i])
    return queue


# =============================================================================
# GRADING  (from v3, unchanged)
# =============================================================================

def _normalize_answer(text: str) -> str:
    return re.sub(r"[\s]+", " ", text.lower().strip()).rstrip(".,;:!?\"'")


def grade_answer(
    exercise: Dict[str, Any],
    student_answer: str,
) -> Dict[str, Any]:
    correct      = exercise.get("answer", "")
    correct_norm = _normalize_answer(correct)
    student_norm = _normalize_answer(student_answer)
    choices      = exercise.get("choices") or []
    if choices and re.fullmatch(r"[1-4]", student_answer.strip()):
        idx = int(student_answer.strip()) - 1
        if 0 <= idx < len(choices):
            student_norm = _normalize_answer(choices[idx])
    is_correct = student_norm == correct_norm
    if not is_correct:
        s_clean = re.sub(r"[^\w\s]", "", student_norm)
        c_clean = re.sub(r"[^\w\s]", "", correct_norm)
        if s_clean and c_clean and s_clean == c_clean:
            is_correct = True
    return {
        "is_correct":     is_correct,
        "student_answer": student_answer,
        "model_answer":   correct,
        "explanation":    exercise.get("explanation", ""),
        "family_label":   exercise.get("family_label", ""),
        "micro_skill":    exercise.get("micro_skill", ""),
    }


# =============================================================================
# SESSION STATE PERSISTENCE  (from v3, unchanged)
# =============================================================================

class _SessionStore:
    def __init__(self, session_dir: str) -> None:
        self.dir = Path(session_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        safe = re.sub(r"[^\w\-]", "_", session_id)
        return self.dir / f"{safe}.json"

    def load(self, session_id: str) -> Optional[Dict[str, Any]]:
        p = self._path(session_id)
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def save(self, session_id: str, data: Dict[str, Any]) -> None:
        self._path(session_id).write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


# =============================================================================
# HELPERS  (from v3, unchanged)
# =============================================================================

def _recap_from_profile(
    profile: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not profile:
        return None
    engagement = profile.get("engagement_profile", {})
    velocity   = profile.get("learning_velocity", [])
    return {
        "sessions_analyzed":   profile.get("sessions_analyzed", 0),
        "avg_completion_rate": engagement.get("avg_completion_rate"),
        "avg_session_minutes": engagement.get("avg_session_duration_minutes"),
        "improving_criteria": [
            v["criterion"] for v in velocity if v.get("velocity") == "fast"
        ],
        "slow_criteria": [
            v["criterion"] for v in velocity if v.get("velocity") == "slow"
        ],
        "recommended_intent": profile.get("recommended_session_intent", "consolidation"),
    }


def _targets_from_directive(
    focus_areas: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    targets = []
    for fa in focus_areas:
        criterion = fa.get("criterion", "")
        reason    = fa.get("priority_reason", "")
        targets.append({
            "rank":            fa.get("rank", 1),
            "criterion":       criterion,
            "criterion_label": _CRITERION_LABELS.get(criterion, criterion),
            "skill_tag":       fa.get("skill_tag", ""),
            "current_band":    fa.get("current_band"),
            "target_band":     fa.get("target_band"),
            "priority_reason": reason,
            "reason_text":     _PRIORITY_EXPLANATIONS.get(reason, ""),
            "difficulty":      fa.get("recommended_difficulty", "consolidation"),
        })
    return targets


def _build_welcome_text(
    student_id: str,
    recap: Optional[Dict[str, Any]],
    targets: List[Dict[str, Any]],
    band_gap: Dict[str, Any],
) -> str:
    lines: List[str] = [f"Welcome back, {student_id}!", ""]
    if recap and recap.get("sessions_analyzed", 0) > 0:
        lines.append(f"Since we started ({recap['sessions_analyzed']} session(s) analysed):")
        if recap.get("improving_criteria"):
            improving = [_CRITERION_LABELS.get(c, c) for c in recap["improving_criteria"]]
            lines.append(f"  Improving:       {', '.join(improving)}")
        if recap.get("slow_criteria"):
            slow = [_CRITERION_LABELS.get(c, c) for c in recap["slow_criteria"]]
            lines.append(f"  Needs more work: {', '.join(slow)}")
        if recap.get("avg_completion_rate") is not None:
            pct = int(recap["avg_completion_rate"] * 100)
            lines.append(f"  Avg completion:  {pct}%")
        lines.append("")
    else:
        lines.append("This is your first session — let's build your profile!")
        lines.append("")
    current = band_gap.get("current_holistic")
    goal    = band_gap.get("goal_band")
    if current and goal:
        gap = band_gap.get("gap", round(float(goal) - float(current), 1))
        lines.append(f"Current holistic band: {current}   Target: {goal}   Gap: {gap}")
        lines.append("")
    if targets:
        lines.append("Today's targets — chosen specifically for you:")
        for t in targets:
            label     = t["criterion_label"]
            skill     = t["skill_tag"].replace("_", " ").title() if t.get("skill_tag") else ""
            band_info = (
                f"  (Band {t['current_band']} → {t['target_band']})"
                if t.get("current_band") and t.get("target_band")
                else ""
            )
            header = f"  {t['rank']}. {label}"
            if skill:
                header += f" — {skill}"
            header += band_info
            lines.append(header)
            if t.get("reason_text"):
                lines.append(f"     Because this area {t['reason_text']}.")
        lines.append("")
    return "\n".join(lines)


def _build_suggestions(
    wrong_attempts: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    family_errors: Dict[str, List[Dict[str, Any]]] = {}
    for att in wrong_attempts:
        fam = att.get("family", "unknown")
        family_errors.setdefault(fam, []).append(att)
    suggestions: List[Dict[str, Any]] = []
    for family, attempts in family_errors.items():
        criterion    = attempts[0].get("criterion", "")
        cefr         = attempts[0].get("cefr_level", "B1")
        family_label = attempts[0].get("family_label", family.lower().replace("_", " ").title())
        suggestions.append({
            "family":          family,
            "family_label":    family_label,
            "criterion":       criterion,
            "criterion_label": _CRITERION_LABELS.get(criterion, criterion),
            "error_count":     len(attempts),
            "cefr_level":      cefr,
            "suggestion": (
                f"Review {family_label.lower()} rules and practise "
                f"more {cefr}-level exercises before your next session."
            ),
        })
    suggestions.sort(key=lambda s: -s["error_count"])
    return suggestions[:5]


def _summarize_reason_tags(ratings: List[Dict[str, Any]]) -> Dict[str, int]:
    summary: Dict[str, int] = {}
    for r in ratings:
        tag = r.get("reason_tag", "")
        if tag:
            summary[tag] = summary.get(tag, 0) + 1
    return summary


# =============================================================================
# MAIN ENGINE CLASS
# =============================================================================

class PracticeEngineV4:
    """
    Standalone interactive practice engine for IELTS / VA English.

    Changes from PracticeEngineV3:
      - B2 fix: filter_bank() no longer falls through to CEFR-unfiltered pool.
        build_exercise_queue() tries alternative families at the correct CEFR
        before skipping a slot. CEFR floor is never relaxed.
      - F24 + F25 retained from v3.

    Session lifecycle:
        start_session()        → WelcomePayload
        set_session_length()   → SessionPlan (now includes family_fallbacks_used)
        get_next_exercise()    → Exercise dict | None
        submit_answer()        → ExerciseFeedback
        get_session_results()  → SessionResults
        submit_survey()        → PracticeSessionResult v2
    """

    def __init__(
        self,
        bank_path: str,
        session_dir: str = "./practice_sessions",
    ) -> None:
        self.bank: List[Dict[str, Any]] = load_bank(bank_path)
        self._store = _SessionStore(session_dir)

    # ── Step 1: Start session ─────────────────────────────────────────────────

    def start_session(
        self,
        student_id: str,
        directive: Dict[str, Any],
        learner_profile: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        sid         = session_id or str(uuid.uuid4())
        focus_areas = directive.get("focus_areas", [])
        band_gap    = directive.get("band_gap_summary", {})
        recap       = _recap_from_profile(learner_profile)
        targets     = _targets_from_directive(focus_areas)

        cefr_floor   = directive.get("_cefr_floor")
        cefr_ceiling = directive.get("_cefr_ceiling")

        session_data: Dict[str, Any] = {
            "session_id":          sid,
            "student_id":          student_id,
            "directive_id":        directive.get("directive_id", ""),
            "focus_areas":         focus_areas,
            "band_gap_summary":    band_gap,
            "state":               "awaiting_length",
            "started_at":          datetime.now(timezone.utc).isoformat(),
            "exercise_queue":      [],
            "exercise_details":    {},
            "exercise_index":      0,
            "completed_exercises": [],
            "correct_count":       0,
            "results_by_criterion": {},
            "seen_exercise_ids":   [],
            "_cefr_floor":         cefr_floor,
            "_cefr_ceiling":       cefr_ceiling,
            # B2: metadata populated by set_session_length after queue build
            "family_fallbacks_used":  [],
            "slots_skipped_no_stock": [],
        }
        self._store.save(sid, session_data)

        return {
            "session_id":    sid,
            "student_id":    student_id,
            "welcome_message": _build_welcome_text(student_id, recap, targets, band_gap),
            "last_session_recap": recap,
            "targets":       targets,
            "cefr_floor":    cefr_floor,
            "cefr_ceiling":  cefr_ceiling,
            "session_length_options": [
                {
                    "minutes":            m,
                    "estimated_exercises": max(2, _TIME_BUDGETS_SECONDS[m] // _AVG_EXERCISE_SECONDS),
                }
                for m in sorted(_TIME_BUDGETS_SECONDS)
            ],
        }

    # ── Step 2: Set session length ────────────────────────────────────────────

    def set_session_length(self, session_id: str, minutes: int) -> Dict[str, Any]:
        """
        Set session duration and build the exercise queue.
        B2 fix: family fallbacks and skipped slots are captured from
        _last_queue_metadata and stored in session_data for QA.
        """
        valid   = sorted(_TIME_BUDGETS_SECONDS.keys())
        minutes = min(valid, key=lambda m: abs(m - minutes))

        data = self._store.load(session_id)
        if data is None:
            raise ValueError(f"Session '{session_id}' not found.")

        allocation = compute_allocation(data["focus_areas"], minutes)
        exclude    = set(data.get("seen_exercise_ids", []))

        queue = build_exercise_queue(
            self.bank,
            allocation,
            exclude,
            min_cefr = data.get("_cefr_floor"),
            max_cefr = data.get("_cefr_ceiling"),
        )

        # B2: capture metadata written by build_exercise_queue
        meta = dict(_last_queue_metadata)

        data["chosen_minutes"]          = minutes
        data["allocation"]              = allocation
        data["exercise_queue"]          = [ex["exercise_id"] for ex in queue]
        data["exercise_details"]        = {ex["exercise_id"]: ex for ex in queue}
        data["exercise_index"]          = 0
        data["state"]                   = "in_progress"
        data["family_fallbacks_used"]   = meta.get("family_fallbacks_used", [])
        data["slots_skipped_no_stock"]  = meta.get("slots_skipped_no_stock", [])
        self._store.save(session_id, data)

        total_ex     = len(queue)
        cefr_selected = list({ex.get("cefr_level", "") for ex in queue if ex.get("cefr_level")})

        return {
            "session_id":               session_id,
            "chosen_duration_minutes":  minutes,
            "total_exercises":          total_ex,
            "cefr_floor":               data.get("_cefr_floor"),
            "cefr_ceiling":             data.get("_cefr_ceiling"),
            "cefr_levels_selected":     sorted(cefr_selected),
            "family_fallbacks_used":    meta.get("family_fallbacks_used", []),   # B2 QA
            "slots_skipped_no_stock":   meta.get("slots_skipped_no_stock", []),  # B2 QA
            "allocation": [
                {
                    "criterion":       slot["criterion"],
                    "criterion_label": _CRITERION_LABELS.get(slot["criterion"], slot["criterion"]),
                    "skill_tag":       slot["skill_tag"],
                    "difficulty":      slot["difficulty"],
                    "exercise_count":  slot["count"],
                    "percentage":      round(slot["count"] / max(total_ex, 1) * 100),
                }
                for slot in allocation
            ],
        }

    # ── Step 3: Get next exercise ─────────────────────────────────────────────

    def get_next_exercise(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Return the next exercise in the queue, or None when all are done.
        F25: includes "explanation" and "answer" fields (retained from v3).
        """
        data = self._store.load(session_id)
        if data is None:
            raise ValueError(f"Session '{session_id}' not found.")

        idx   = data.get("exercise_index", 0)
        queue = data.get("exercise_queue", [])

        if idx >= len(queue):
            if data.get("state") == "in_progress":
                data["state"] = "awaiting_survey"
                self._store.save(session_id, data)
            return None

        ex_id = queue[idx]
        ex    = data["exercise_details"].get(ex_id)
        if ex is None:
            return None

        choices = ex.get("choices") or []
        if ex.get("exercise_type") == "mcq" and choices:
            choices = list(choices)
            random.shuffle(choices)

        category  = ex.get("category", "")
        criterion = _CATEGORY_TO_CRITERION.get(category, category)

        return {
            "exercise_number":    idx + 1,
            "total_exercises":    len(queue),
            "exercise_id":        ex_id,
            "exercise_type":      ex.get("exercise_type", ""),
            "exercise_type_label": _EXERCISE_TYPE_LABELS.get(
                ex.get("exercise_type", ""), ex.get("exercise_type", "")
            ),
            "family":             ex.get("family", ""),
            "family_label":       ex.get("family_label", ""),
            "criterion":          criterion,
            "criterion_label":    _CRITERION_LABELS.get(criterion, criterion),
            "cefr_level":         ex.get("cefr_level", ""),
            "micro_skill":        ex.get("micro_skill", ""),
            "prompt":             ex.get("prompt", ""),
            "answer":             ex.get("answer", ""),       # F25 (from v3)
            "explanation":        ex.get("explanation", ""),  # F25 (from v3)
            "choices":            choices if choices else None,
        }

    # ── Step 4: Submit answer ─────────────────────────────────────────────────

    def submit_answer(
        self,
        session_id: str,
        exercise_id: str,
        student_answer: str,
        time_seconds: int = 0,
    ) -> Dict[str, Any]:
        data = self._store.load(session_id)
        if data is None:
            raise ValueError(f"Session '{session_id}' not found.")
        ex = data["exercise_details"].get(exercise_id)
        if ex is None:
            raise ValueError(f"Exercise '{exercise_id}' not found in session '{session_id}'.")

        result   = grade_answer(ex, student_answer)
        category = ex.get("category", "")
        criterion = _CATEGORY_TO_CRITERION.get(category, category)
        cefr     = ex.get("cefr_level", "B1")

        attempt: Dict[str, Any] = {
            "exercise_id":    exercise_id,
            "family":         ex.get("family", ""),
            "family_label":   ex.get("family_label", ""),
            "criterion":      criterion,
            "cefr_level":     cefr,
            "is_correct":     result["is_correct"],
            "time_seconds":   time_seconds,
            "student_answer": student_answer,
        }
        data["completed_exercises"].append(attempt)
        data["seen_exercise_ids"].append(exercise_id)
        if result["is_correct"]:
            data["correct_count"] = data.get("correct_count", 0) + 1

        crit_map = data.setdefault("results_by_criterion", {})
        cr = crit_map.setdefault(
            criterion, {"attempted": 0, "correct": 0, "families": {}}
        )
        cr["attempted"] += 1
        cr["correct"]   += int(result["is_correct"])
        fm = cr["families"].setdefault(
            ex.get("family", "unknown"),
            {"attempted": 0, "correct": 0,
             "family_label": ex.get("family_label", ""), "cefr_level": cefr},
        )
        fm["attempted"] += 1
        fm["correct"]   += int(result["is_correct"])

        data["exercise_index"] = data.get("exercise_index", 0) + 1
        self._store.save(session_id, data)

        remaining = len(data["exercise_queue"]) - data["exercise_index"]
        return {
            "exercise_id":         exercise_id,
            "is_correct":          result["is_correct"],
            "student_answer":      student_answer,
            "model_answer":        result["model_answer"],
            "explanation":         result["explanation"],
            "family_label":        result["family_label"],
            "micro_skill":         result["micro_skill"],
            "exercises_remaining": max(0, remaining),
        }

    # ── Step 5: Session results ───────────────────────────────────────────────

    def get_session_results(self, session_id: str) -> Dict[str, Any]:
        data = self._store.load(session_id)
        if data is None:
            raise ValueError(f"Session '{session_id}' not found.")
        completed  = data.get("completed_exercises", [])
        total      = len(completed)
        correct    = data.get("correct_count", 0)
        total_time = sum(ex.get("time_seconds", 0) for ex in completed)

        by_criterion: Dict[str, Any] = {}
        for criterion, cr in data.get("results_by_criterion", {}).items():
            att  = cr["attempted"]
            corr = cr["correct"]
            acc  = round(corr / att, 2) if att > 0 else 0.0
            weak_families = [
                fam for fam, fd in cr.get("families", {}).items()
                if fd["attempted"] > 0
                and (fd["correct"] / fd["attempted"]) < 0.60
            ]
            by_criterion[criterion] = {
                "criterion_label": _CRITERION_LABELS.get(criterion, criterion),
                "attempted":       att,
                "correct":         corr,
                "accuracy":        acc,
                "weak_families":   weak_families,
            }

        wrong_attempts = [ex for ex in completed if not ex["is_correct"]]
        suggestions    = _build_suggestions(wrong_attempts)

        return {
            "session_id":          session_id,
            "exercises_completed": total,
            "exercises_correct":   correct,
            "overall_accuracy":    round(correct / total, 2) if total > 0 else 0.0,
            "time_minutes":        round(total_time / 60, 1),
            "by_criterion":        by_criterion,
            "suggestions":         suggestions,
            "ready_for_survey":    True,
        }

    # ── Step 6: Submit survey ─────────────────────────────────────────────────

    def submit_survey(
        self,
        session_id: str,
        exercise_ratings: List[Dict[str, Any]],
        overall_comment: str = "",
    ) -> Dict[str, Any]:
        data = self._store.load(session_id)
        if data is None:
            raise ValueError(f"Session '{session_id}' not found.")

        completed  = data.get("completed_exercises", [])
        total      = len(completed)
        total_time = sum(ex.get("time_seconds", 0) for ex in completed)
        queue_len  = len(data.get("exercise_queue", [1]))

        skill_results: List[Dict[str, Any]] = []
        for criterion, cr in data.get("results_by_criterion", {}).items():
            for family, fd in cr.get("families", {}).items():
                att = fd["attempted"]
                if att == 0:
                    continue
                corr       = fd["correct"]
                acc        = round(corr / att, 2)
                cefr       = fd.get("cefr_level", "B1")
                difficulty = _CEFR_TO_DIFFICULTY.get(cefr, "consolidation")
                ready      = acc >= 0.80 and att >= 2
                signal     = (
                    "improving" if acc >= 0.75 else
                    "stable"    if acc >= 0.50 else
                    "needs_work"
                )
                skill_results.append({
                    "skill_tag":                family.lower(),
                    "criterion":                criterion,
                    "difficulty_practiced":     difficulty,
                    "exercises_attempted":      att,
                    "accuracy":                 acc,
                    "improvement_signal":       signal,
                    "ready_for_next_difficulty": ready,
                })

        abandoned       = max(0, queue_len - total)
        completion_rate = round(total / max(queue_len, 1), 2)
        engagement      = (
            "high"   if completion_rate >= 0.90 else
            "medium" if completion_rate >= 0.60 else
            "low"
        )

        result: Dict[str, Any] = {
            "session_id":               session_id,
            "student_id":               data.get("student_id", ""),
            "directive_id":             data.get("directive_id", ""),
            "session_date":             datetime.now(timezone.utc).date().isoformat(),
            "session_duration_minutes": round(total_time / 60, 1),
            "exercises_assigned":       queue_len,
            "exercises_completed":      total,
            "completion_rate":          completion_rate,
            "skill_results":            skill_results,
            "student_engagement": {
                "time_on_task_minutes": round(total_time / 60, 1),
                "abandoned_exercises":  abandoned,
                "engagement_signal":    engagement,
            },
            "lie_survey": {
                "exercise_ratings":   exercise_ratings,
                "overall_comment":    overall_comment,
                "reason_tag_summary": _summarize_reason_tags(exercise_ratings),
            },
        }

        data["state"]                   = "completed"
        data["practice_session_result"] = result
        self._store.save(session_id, data)
        return result

    # ── Text rendering (static, from v3 unchanged) ───────────────────────────

    @staticmethod
    def render_welcome(payload: Dict[str, Any]) -> str:
        sep  = "─" * 54
        opts = payload.get("session_length_options", [])
        lines = [sep, payload.get("welcome_message", ""), sep, "", "How long do you have today?", ""]
        for i, opt in enumerate(opts, 1):
            lines.append(f"  [{i}] {opt['minutes']} minutes  (~{opt['estimated_exercises']} exercises)")
        lines += ["", "Enter 1, 2 or 3:  "]
        floor   = payload.get("cefr_floor")
        ceiling = payload.get("cefr_ceiling")
        if floor:
            lines.append(f"\n  [CEFR] Exercises: {floor}–{ceiling or floor} level")
        return "\n".join(lines)

    @staticmethod
    def render_session_plan(plan: Dict[str, Any]) -> str:
        sep = "─" * 54
        lines = [
            sep,
            f"SESSION PLAN  ·  {plan['chosen_duration_minutes']} min  ·  "
            f"{plan['total_exercises']} exercises",
            sep, "",
        ]
        cefr_levels = plan.get("cefr_levels_selected", [])
        if cefr_levels:
            lines.append(f"  CEFR levels: {', '.join(cefr_levels)}")
            lines.append("")
        fallbacks = plan.get("family_fallbacks_used", [])
        if fallbacks:
            for fb in fallbacks:
                lines.append(
                    f"  [B2 fallback] {fb.get('requested_family','?')} → "
                    f"{fb.get('used_family','?')} ({fb.get('criterion','')})"
                )
            lines.append("")
        skipped = plan.get("slots_skipped_no_stock", [])
        if skipped:
            for sk in skipped:
                lines.append(f"  [B2 skipped] {sk.get('criterion','')} — no stock at {sk.get('min_cefr','?')}–{sk.get('max_cefr','?')}")
            lines.append("")
        for slot in plan.get("allocation", []):
            label = slot.get("criterion_label", slot.get("criterion", ""))
            skill = slot.get("skill_tag", "").replace("_", " ").title()
            lines.append(f"  {label}")
            if skill:
                lines.append(f"    area:       {skill}")
            lines.append(f"    exercises:  {slot['exercise_count']}  ({slot['percentage']}%)")
            lines.append(f"    difficulty: {slot['difficulty']}")
            lines.append("")
        lines.append("Press Enter when ready.")
        return "\n".join(lines)

    @staticmethod
    def render_exercise(exercise: Dict[str, Any]) -> str:
        sep         = "─" * 54
        num         = exercise.get("exercise_number", "?")
        total       = exercise.get("total_exercises", "?")
        crit        = exercise.get("criterion_label", exercise.get("criterion", ""))
        cefr        = exercise.get("cefr_level", "")
        family      = exercise.get("family_label", exercise.get("family", ""))
        etype_label = exercise.get("exercise_type_label", exercise.get("exercise_type", ""))
        lines = [
            sep,
            f"Exercise {num} / {total}  ·  {crit}  ·  Level {cefr}",
        ]
        if family:
            lines.append(f"Topic: {family}")
        lines += [sep, "", f"[ {etype_label.upper()} ]", "", exercise.get("prompt", ""), ""]
        choices = exercise.get("choices")
        if choices:
            lines.append("Options:")
            for i, ch in enumerate(choices, 1):
                lines.append(f"  {i}.  {ch}")
            lines += ["", "Your answer (type the option text or its number):  "]
        else:
            lines.append("Your answer:  ")
        return "\n".join(lines)

    @staticmethod
    def render_feedback(feedback: Dict[str, Any]) -> str:
        icon  = "✓  CORRECT!" if feedback["is_correct"] else "✗  INCORRECT"
        lines = [icon, ""]
        if not feedback["is_correct"]:
            lines.append(f"  Your answer:  {feedback['student_answer']}")
            lines.append(f"  Correct:      {feedback['model_answer']}")
            lines.append("")
        expl = feedback.get("explanation", "")
        if expl:
            lines.append(f"  Why: {expl}")
        if feedback.get("micro_skill"):
            lines.append(f"  Skill: {feedback['micro_skill'].replace('_', ' ')}")
        remaining = feedback.get("exercises_remaining", 0)
        lines += [
            "",
            f"  → {'Only ' + str(remaining) + ' more to go!' if remaining > 0 else 'That was the last one!'}",
        ]
        return "\n".join(lines)

    @staticmethod
    def render_results(results: Dict[str, Any]) -> str:
        sep     = "─" * 54
        correct = results["exercises_correct"]
        total   = results["exercises_completed"]
        pct     = int(results["overall_accuracy"] * 100)
        t_min   = results.get("time_minutes", 0)
        lines = [sep, "SESSION RESULTS", sep, "",
                 f"Score:  {correct} / {total} correct  ({pct}%)",
                 f"Time:   {t_min} min", "", "BY AREA:"]
        for criterion, cr in results.get("by_criterion", {}).items():
            label = cr.get("criterion_label", criterion)
            acc   = int(cr["accuracy"] * 100)
            flag  = "  ✓" if cr["accuracy"] >= 0.70 else "  ← needs work"
            lines.append(f"  {label:<32}  {cr['correct']}/{cr['attempted']}  ({acc}%){flag}")
        suggestions = results.get("suggestions", [])
        if suggestions:
            lines += ["", "WHAT TO REVIEW BEFORE YOUR NEXT SESSION:"]
            for s in suggestions:
                lines.append(f"  • {s['suggestion']}")
        lines += ["", sep]
        return "\n".join(lines)
