"""
practice_engine_v5.py
======================
STANDALONE FILE — no imports from practice_engine_v4.py.
All logic is inlined from v4. Three targeted changes below.

CHANGES vs practice_engine_v4.py
----------------------------------
I-3 / F5  SKILL TAG → BANK FAMILY TRANSLATION
          NEW: _SKILL_TAG_TO_FAMILY dict

          WHY THE CHANGE IS NEEDED
          -------------------------
          The Priority Engine (PE) and Feedback Engine generate skill tags
          based on IELTS criterion names, not exercise bank family codes.
          Five PE-generated skill tags have zero corresponding bank exercises
          because no bank family matches their exact name:

            TASK_COMPLETENESS   → maps to → CLAIM_SUPPORT
            GRAMMAR_CONTROL     → maps to → CLAUSE_STRUCTURE
            COMPARATIVE_FORM    → maps to → COMPARATIVES
            LEXICAL_CONTROL     → maps to → COLLOCATION
            PARAGRAPH_STRUCTURE → maps to → PARAGRAPH_PROGRESS

          In v4, when build_exercise_queue received one of these tags,
          _skill_matches() found no bank exercises, the slot returned [],
          and the B2 family fallback tried OTHER families for the criterion —
          serving exercises unrelated to the actual error. F5 QA (≥7 exercises)
          failed in sessions 024 and 025 because of this slot drain.

          Fix: in build_exercise_queue(), translate the requested skill_tag
          through _SKILL_TAG_TO_FAMILY BEFORE passing it to filter_bank().
          The translated tag matches the actual bank family code, so
          filter_bank() finds the correct exercises immediately.

          All five target bank families already exist in bank v10g.
          This change adds zero new bank dependencies.

I-3b      UPDATED _CRITERION_FAMILIES
          The five new families (COMPARATIVES, CLAUSE_STRUCTURE, COLLOCATION,
          PARAGRAPH_PROGRESS, CLAIM_SUPPORT) were already in _CRITERION_FAMILIES
          in v4 — no change needed there. _CRITERION_FAMILIES is reproduced
          verbatim from v4.

PE-OBS-3  CROSS-SESSION EXERCISE EXCLUSION
          NEW: seen_ids_path parameter on PracticeEngineV5.__init__()

          WHY THE CHANGE IS NEEDED
          -------------------------
          In v4, seen_exercise_ids is per-session only (stored in session JSON).
          When a new session starts, set_session_length() builds a fresh queue
          with no memory of exercises served in previous sessions. Students
          receive the same exercises repeatedly.

          Fix: optional seen_ids_path parameter. When provided:
          - At __init__: load cross-session seen IDs from the JSON file.
          - At set_session_length: merge cross-session seen IDs into the
            exclude set before build_exercise_queue() runs.
          - At submit_answer: append the new exercise ID to the cross-session
            file immediately (write-through, no batch). If the file grows
            beyond _SEEN_IDS_CAP entries, prune the oldest.
          - In start_session return payload: include _seen_ids_loaded (bool)
            and _cross_session_count (int) for F90 QA check.

          The seen_ids file is a flat JSON list of exercise ID strings.
          Convention: {data_dir}/student_seen_exercises.json

          When seen_ids_path is None (default), behaviour is identical to v4:
          no cross-session memory, no file I/O.

CLASS RENAME: PracticeEngineV5 (was PracticeEngineV4).
"""
# =============================================================================
# practice_engine_v5b.py — BUG-PE-1 FIX
# NEW FILE — do not delete. Do not overwrite practice_engine_v5.py.
# Date: 2026-07-03
# Based on: practice_engine_v5.py
#
# BUG-PE-1: filter_bank criterion_pool excluded TA exercises with blank
#   category field. All B1 TA exercises (TASK_COMPLETENESS, CLAIM_SUPPORT,
#   TASK_RESPONSE, etc.) have category="" in va_exercise_bank_v11d_approved.jsonl
#   and were invisible to filter_bank, causing TA slot to be skipped and F5 to
#   fail (5 exercises instead of >=7).
#
# FIX: criterion_pool now also matches by family via _CRITERION_FAMILIES.
#   OLD: if ex.get("category","") in target_cats and ...
#   NEW: if (ex.get("category","") in target_cats
#            or ex.get("family","") in target_families) and ...
#
# Verified: 71 real seen_ids, TA slot returns 50 CLAIM_SUPPORT B1 exercises,
#   full queue = 7 exercises, skipped=0, F5 passes.
# =============================================================================


from __future__ import annotations

import json
import random
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# =============================================================================
# CROSS-SESSION SEEN-IDS CAP
# =============================================================================

_SEEN_IDS_CAP: int = 2000
# If the cross-session seen-IDs file exceeds this many entries, the oldest
# entries are pruned on write. Prevents unbounded file growth.
# With a ~3000-exercise bank the student should never hit this ceiling before
# the bank is refreshed.

# =============================================================================
# SKILL TAG → BANK FAMILY TRANSLATION  (NEW in v5)
# =============================================================================

_SKILL_TAG_TO_FAMILY: Dict[str, str] = {
    # PE / FE tag           → bank family code (must exist in bank v10g)
    "TASK_COMPLETENESS":    "CLAIM_SUPPORT",
    "GRAMMAR_CONTROL":      "CLAUSE_STRUCTURE",
    "COMPARATIVE_FORM":     "COMPARATIVES",
    "LEXICAL_CONTROL":      "COLLOCATION",
    "PARAGRAPH_STRUCTURE":  "PARAGRAPH_PROGRESS",
}
# To add more mappings: append here. The translation fires only in
# build_exercise_queue() — filter_bank() and all other code are unchanged.

# =============================================================================
# CATEGORY / CEFR MAPPINGS  (from v4, unchanged)
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

# Gold v1.4.13 fix (stress-test Problem 1): filter_bank()'s target_cats/
# target_families lookups are keyed by the four INTERNAL criterion IDs above
# (grammatical_range_accuracy / lexical_resource / task_achievement /
# coherence_cohesion). But the directive/priority-engine layer passes
# human-readable focus-area DISPLAY labels as `criterion` -- "grammar",
# "Organization", "Argumentation", "Reasoning Competence" -- which only
# coincidentally match an internal key for "lexical_resource" (identical
# spelling in both schemes). Every other criterion silently returned zero
# bank stock on every run, for every student: a stress-test essay whose
# focus-area list happened to have zero "lexical_resource" slots (e.g. a
# strong essay steered toward Organization/Argumentation practice) got
# 0 exercises delivered, flagged "no_exercises_available" by Gold's QA.
# This translation runs at the top of filter_bank(), the same call site the
# existing skill_tag -> _SKILL_TAG_TO_FAMILY translation already uses.
# Covers both the two labels Directive's own focus_areas use ("grammar",
# "lexical_resource" -- already correctly-cased internal-ish names) AND all
# 12 Evaluator skill_observation_profile domain names that
# enrich_focus_areas_with_evaluator() (gold_practice_engine_bridge_v1.py)
# copies verbatim into criterion when it merges gap_targets_for_practice into
# focus_areas -- found by tracing where the strong essay's 11-item target
# list (Organization x5, Argumentation x4, Reasoning Competence x1) actually
# came from: NOT directive.focus_areas (which only ever has 1-2 ranked
# items), but this evaluator-gap-target merge, using the Evaluator's raw
# domain strings, several of which ("Cohesion", "Lexical Control", "Grammar
# Production", "Advanced Lexical Competence", etc.) were previously entirely
# unmapped -- same zero-stock bug as "Organization"/"Argumentation" below.
_FOCUS_CRITERION_TO_INTERNAL: Dict[str, str] = {
    "grammar":                       "grammatical_range_accuracy",
    "lexical_resource":              "lexical_resource",
    "Organization":                  "coherence_cohesion",
    "Cohesion":                      "coherence_cohesion",
    "Argumentation":                 "task_achievement",
    "Task Understanding":            "task_achievement",
    "Content Development":           "task_achievement",
    "Information Processing":        "task_achievement",
    "Grammar Production":            "grammatical_range_accuracy",
    "Lexical Control":               "lexical_resource",
    "Advanced Lexical Competence":   "lexical_resource",
    "Style & Reader Impact":         "lexical_resource",
    # "Reasoning Competence" / "Thinking Competence" don't cleanly correspond
    # to any of the four IELTS criteria as currently modeled by this engine;
    # routed to task_achievement provisionally (their skill_tags --
    # causal_reasoning, counterargument_development, evaluation_of_alternatives
    # -- are closest to Task Response/argumentation quality). Revisit if
    # Priority Engine's domain taxonomy changes.
    "Reasoning Competence":          "task_achievement",
    "Thinking Competence":           "task_achievement",
}

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

# B2 fix (from v4): maps each IELTS criterion to all exercise bank family
# codes that serve it. Used for family-level fallback when a requested family
# has no B1/B2 stock.
_CRITERION_FAMILIES: Dict[str, List[str]] = {
    "grammatical_range_accuracy": [
        "ARTICLE_DETERMINER", "CLAUSE_STRUCTURE", "COMPARATIVES",
        "CONDITIONALS", "COUNTABLE_UNCOUNTABLE", "FRAGMENTS_RUNONS",
        "MODALS", "NOUN_NUMBER_COUNTABILITY", "PASSIVE_VOICE",
        "PREPOSITIONS", "PREPOSITION_PATTERN", "PRONOUN_REFERENCE",
        "PUNCTUATION", "RELATIVE_CLAUSES", "SENTENCE_VARIETY",
        "SUBJECT_VERB_AGREEMENT", "VERB_FORM", "VERB_TENSE",
        "WORD_FORM", "WORD_ORDER",
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
# PRIORITY WEIGHTS / DISPLAY LABELS  (from v4, unchanged)
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
# BANK LOADING  (from v4, unchanged)
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
# CROSS-SESSION SEEN-IDS I/O  (NEW in v5)
# =============================================================================

def _load_cross_session_ids(path: str) -> List[str]:
    """
    Load seen exercise IDs from the cross-session file.
    Returns an empty list if the file does not exist or is malformed.
    """
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(x) for x in data]
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _save_cross_session_ids(path: str, ids: List[str]) -> None:
    """
    Write the seen-IDs list to file.
    Prunes to the most recent _SEEN_IDS_CAP entries if over the limit.
    Creates parent directories if needed.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    capped = ids[-_SEEN_IDS_CAP:] if len(ids) > _SEEN_IDS_CAP else ids
    p.write_text(json.dumps(capped, ensure_ascii=False), encoding="utf-8")


# =============================================================================
# EXERCISE FILTERING & SELECTION  (from v4, unchanged)
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

    B2 fix (from v4): When CEFR floor/ceiling is active and no exercises exist
    within the valid range, return [] instead of falling through to
    criterion_pool (which ignores CEFR). The caller (build_exercise_queue)
    handles the empty result by trying a different family — never relaxing
    the CEFR floor.
    """
    exclude_ids = exclude_ids or set()
    # Gold v1.4.13 fix: normalize focus-area display labels ("grammar",
    # "Organization", "Argumentation", "Reasoning Competence") to the internal
    # criterion IDs _CRITERION_TO_CATEGORIES/_CRITERION_FAMILIES are keyed by,
    # before either lookup. See _FOCUS_CRITERION_TO_INTERNAL definition for
    # why this was silently returning zero stock for every criterion except
    # "lexical_resource".
    criterion = _FOCUS_CRITERION_TO_INTERNAL.get(criterion, criterion)
    target_cats = _CRITERION_TO_CATEGORIES.get(criterion, set())
    target_cefr = _DIFFICULTY_TO_CEFR.get(difficulty, "B1")

    target_families = set(_CRITERION_FAMILIES.get(criterion, []))
    criterion_pool = [
        ex for ex in bank
        if (ex.get("category", "") in target_cats
            or ex.get("family", "") in target_families)
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

    if min_cefr or max_cefr:
        return []

    return criterion_pool


# =============================================================================
# ALLOCATION  (from v4, unchanged)
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

    B2 fix (from v4): When filter_bank returns [] for a requested family:
      1. Try other families within the same criterion at the SAME CEFR range.
      2. If all families exhausted at correct CEFR: skip slot entirely.
      NEVER relax min_cefr — no CEFR violation allowed.

    I-3 fix (v5 NEW): Translate requested skill_tag through _SKILL_TAG_TO_FAMILY
      before passing to filter_bank(). Fixes zero-stock slots caused by PE
      generating skill tags that don't match any bank family name.

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
        criterion     = slot["criterion"]
        original_tag  = slot["skill_tag"]

        # I-3 fix: translate PE skill tag to bank family code if needed
        resolved_tag  = _SKILL_TAG_TO_FAMILY.get(original_tag, original_tag)

        # First attempt: resolved family at correct CEFR
        pool = filter_bank(
            bank,
            criterion   = criterion,
            difficulty  = slot["difficulty"],
            skill_tag   = resolved_tag,
            exclude_ids = exclude_ids,
            min_cefr    = min_cefr,
            max_cefr    = max_cefr,
        )

        # B2 fix: if empty, try alternative families for the same criterion
        if not pool and (min_cefr or max_cefr):
            alt_families = _CRITERION_FAMILIES.get(criterion, [])
            for alt_family in alt_families:
                if alt_family.lower() == (resolved_tag or "").lower():
                    continue
                if resolved_tag and _skill_matches(
                    {"family": alt_family, "micro_skill": ""}, resolved_tag
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
                        "requested_family": original_tag,
                        "resolved_family":  resolved_tag,
                        "used_family":      alt_family,
                        "criterion":        criterion,
                        "reason": (
                            f"No exercises at CEFR {min_cefr}–{max_cefr} "
                            f"for family '{resolved_tag}' (original tag: '{original_tag}'). "
                            f"Serving '{alt_family}' at correct CEFR instead."
                        ),
                    })
                    break

        if not pool:
            slots_skipped.append({
                "criterion":        criterion,
                "requested_family": original_tag,
                "resolved_family":  resolved_tag,
                "min_cefr":         min_cefr,
                "max_cefr":         max_cefr,
                "note": (
                    f"Slot skipped — no exercises at CEFR {min_cefr}–{max_cefr} "
                    f"for criterion '{criterion}' (any family). "
                    "CEFR floor NOT relaxed."
                ),
            })
            continue

        random.shuffle(pool)
        selected = pool[: slot["count"]]
        exclude_ids.update(ex["exercise_id"] for ex in selected)
        if selected:
            groups.append(selected)

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
# GRADING  (from v4, unchanged)
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
# SESSION STATE PERSISTENCE  (from v4, unchanged)
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
# HELPERS  (from v4, unchanged)
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

class PracticeEngineV5:
    """
    Standalone interactive practice engine for IELTS / VA English.

    Changes from PracticeEngineV4:
      - I-3: build_exercise_queue() translates skill tags through
        _SKILL_TAG_TO_FAMILY before bank lookup. Fixes zero-stock slots for
        TASK_COMPLETENESS, GRAMMAR_CONTROL, COMPARATIVE_FORM, LEXICAL_CONTROL,
        PARAGRAPH_STRUCTURE — the five PE tags with no direct bank match.
      - PE-OBS-3: seen_ids_path parameter enables cross-session exercise
        exclusion. When provided, seen IDs persist across sessions in a JSON
        file; new IDs are written through immediately on submit_answer().
      - B2 fix and F24/F25 retained from v4.

    Session lifecycle:
        start_session()        → WelcomePayload (now includes _seen_ids_loaded)
        set_session_length()   → SessionPlan (includes family_fallbacks_used)
        get_next_exercise()    → Exercise dict | None
        submit_answer()        → ExerciseFeedback
        get_session_results()  → SessionResults
        submit_survey()        → PracticeSessionResult v2
    """

    def __init__(
        self,
        bank_path: str,
        session_dir: str = "./practice_sessions",
        seen_ids_path: Optional[str] = None,   # PE-OBS-3: cross-session file
    ) -> None:
        self.bank: List[Dict[str, Any]] = load_bank(bank_path)
        self._store = _SessionStore(session_dir)

        # PE-OBS-3: load cross-session seen IDs if path provided
        self._seen_ids_path: Optional[str] = seen_ids_path
        if seen_ids_path:
            self._cross_session_seen_ids: List[str] = _load_cross_session_ids(seen_ids_path)
        else:
            self._cross_session_seen_ids = []

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
            "family_fallbacks_used":  [],
            "slots_skipped_no_stock": [],
            # PE-OBS-3 metadata for F90 QA
            "_seen_ids_loaded":       len(self._cross_session_seen_ids) > 0,
            "_cross_session_count":   len(self._cross_session_seen_ids),
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
            # PE-OBS-3: expose for F90 QA check
            "_seen_ids_loaded":     len(self._cross_session_seen_ids) > 0,
            "_cross_session_count": len(self._cross_session_seen_ids),
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
        PE-OBS-3: cross-session seen IDs are merged into the exclude set.
        B2 fix: family fallbacks and skipped slots are captured from
        _last_queue_metadata and stored in session_data for QA.
        """
        valid   = sorted(_TIME_BUDGETS_SECONDS.keys())
        minutes = min(valid, key=lambda m: abs(m - minutes))

        data = self._store.load(session_id)
        if data is None:
            raise ValueError(f"Session '{session_id}' not found.")

        allocation = compute_allocation(data["focus_areas"], minutes)

        # PE-OBS-3: merge current-session and cross-session seen IDs
        session_seen = set(data.get("seen_exercise_ids", []))
        cross_seen   = set(self._cross_session_seen_ids)
        exclude      = session_seen | cross_seen

        queue = build_exercise_queue(
            self.bank,
            allocation,
            exclude,
            min_cefr = data.get("_cefr_floor"),
            max_cefr = data.get("_cefr_ceiling"),
        )

        meta = dict(_last_queue_metadata)

        data["chosen_minutes"]          = minutes
        data["allocation"]              = allocation
        data["exercise_queue"]          = [ex["exercise_id"] for ex in queue]
        data["exercise_details"]        = {ex["exercise_id"]: ex for ex in queue}
        data["exercise_index"]          = 0
        data["state"]                   = "in_progress"
        data["family_fallbacks_used"]   = meta.get("family_fallbacks_used", [])
        data["slots_skipped_no_stock"]  = meta.get("slots_skipped_no_stock", [])
        data["_cross_session_excluded"] = len(cross_seen)
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
            "family_fallbacks_used":    meta.get("family_fallbacks_used", []),
            "slots_skipped_no_stock":   meta.get("slots_skipped_no_stock", []),
            "_cross_session_excluded":  len(cross_seen),
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
        F25: includes "explanation" and "answer" fields (retained from v3/v4).
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
            "answer":             ex.get("answer", ""),       # F25
            "explanation":        ex.get("explanation", ""),  # F25
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

        # PE-OBS-3: write through to cross-session file immediately
        if self._seen_ids_path:
            if exercise_id not in self._cross_session_seen_ids:
                self._cross_session_seen_ids.append(exercise_id)
                _save_cross_session_ids(self._seen_ids_path, self._cross_session_seen_ids)

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

    # ── Text rendering (static, from v4 unchanged) ───────────────────────────

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
        cross = payload.get("_cross_session_count", 0)
        if cross:
            lines.append(f"  [Memory] {cross} exercises from previous sessions excluded.")
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
        cross_ex = plan.get("_cross_session_excluded", 0)
        if cross_ex:
            lines.append(f"  [Memory] {cross_ex} previously-seen exercises excluded.")
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
                lines.append(
                    f"  [B2 skipped] {sk.get('criterion','')} — "
                    f"no stock at {sk.get('min_cefr','?')}–{sk.get('max_cefr','?')}"
                )
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
