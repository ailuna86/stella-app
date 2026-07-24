#!/usr/bin/env python3
"""
vocab_coach_selection_engine_v1_0.py
=====================================

Implements Component 1 of VOCABULARY_COACH_ENGINE_BUILD_PROMPT_V1.md: the
PEEL session-selection engine for ST.ELLA's Vocabulary Coach.

SCOPE NOTE -- read before assuming this wires into "the gold pipeline":
The build prompt this engine implements references a number of files as
already existing (`gold_engine_commands_full_v1_4_15.json`,
`lib/server/goldPipeline.ts`, `app/vocabulary-coach/page.tsx`,
`components/VocabularyCoachView.tsx`, `det_vip_v18d_3_topic_alignment_risk.py`,
`score_contract_builder_standalone.py`, `gold_lie_profile_builder_standalone_v1_4_3.py`,
`lret_engine_v1_12_1_meaning_sensitive_detector_families.py`). None of these
exist in any of this project's connected folders -- confirmed directly by
searching frontend_v5, full_premium, and gold/LRET before writing a single
line of this file. frontend_v5's actual frontend is a much simpler Next.js
app (`lib/server/pipeline.ts`, no vocabulary-coach route at all), and the
highest real LRET engine version present is v1.12.0, not v1.12.1.

Per explicit user instruction, this build proceeds as "backend engines only,
fully standalone": this engine (and its two siblings,
`vocab_coach_response_grader_v1_0.py` and `vocab_coach_ledger_update_v1_0.py`)
are pure, independently-runnable CLI scripts. They read real files that DO
exist (`vocab_coach_topic_bank_v1_3_0.json`, `vocab_coach_task_type_bank_v1_2_0.json`,
`vocab_coach_prompt_bank_v1_0_0.json`, and real LRET v1.12.0 session output --
its actual schema was read directly from `lret_v1_12_0_smoke_output_with_detector.json`
before writing the family-aggregation logic below, not guessed) and accept a
score-contract path as an optional input with a documented, resilient,
fail-safe-to-mid-band lookup, since no real score-contract file exists to
confirm the field name against. No pipeline wiring, no frontend, no editing
of any existing engine file is attempted here -- see the addendum for the
full list of what was built vs. explicitly deferred.

CLI:
    --ledger PATH               (may not exist yet -- first session for this student)
    --topic-bank PATH           (vocab_coach_topic_bank_v1_3_0.json)
    --task-type-bank PATH       (vocab_coach_task_type_bank_v1_2_0.json)
    --prompt-bank PATH          (vocab_coach_prompt_bank_v1_0_0.json)
    --score-contract PATH       (optional; missing/unrecognised -> mid-band default)
    --lret-sessions PATH [PATH ...]   (optional; zero or more real LRET session JSON files)
    --student-id STR
    --output PATH
    --cooldown-hours FLOAT      (default 24.0 -- a config value, not hardcoded elsewhere)
    --now ISO8601               (optional override, for deterministic testing)
"""
import argparse
import hashlib
import json
import os
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

ENGINE_VERSION = "vocab-coach-selection-engine-v1.0"
DEFAULT_COOLDOWN_HOURS = 24.0

CEFR_ORDER = ["A1", "A2", "B1", "B2", "C1", "C2"]

# ---------------------------------------------------------------------------
# LRET family mapping -- grounded in the REAL v1.12.0 output schema, read
# directly from lret_v1_12_0_smoke_output_with_detector.json before this
# mapping was written (fix_units[].error_family, clarify_units[].unit_type,
# enhance_units[].axis_candidates). Confirmed real values at inspection time:
#   fix error_family:      {"SPELLING", "WORD_FORM"}
#   clarify unit_type:     {"detector_flagged_broken_sentence",
#                            "repeated_generic_word_variation", "noun_phrase",
#                            "verb_phrase_or_predicate_chunk",
#                            "collocation_precision_menu"}
#   enhance axis_candidates: ["predicate_argument", "semantic_specificity",
#                              "collocation_naturalness", "paraphrase_range"]
# SPELLING/WORD_FORM and the two non-lexical clarify unit_types are grammar/
# form issues, out of Vocabulary Coach's lexical-precision scope (same
# boundary the grader enforces) -- they are tallied for visibility but never
# used to bias vocabulary-type selection.
# ---------------------------------------------------------------------------

CLARIFY_UNIT_TYPE_TO_FAMILY = {
    "collocation_precision_menu": "COLLOCATION",
    "noun_phrase": "NOUN_PHRASE",
    "verb_phrase_or_predicate_chunk": "PHRASAL_VERB_OR_PREDICATE",
}
ENHANCE_AXIS_TO_FAMILY = {
    "collocation_naturalness": "COLLOCATION",
    "semantic_specificity": "LEXICAL_PRECISION",
    "predicate_argument": "LEXICAL_PRECISION",
    "paraphrase_range": "PARAPHRASE_RANGE",
}
OUT_OF_SCOPE_FAMILIES = {"SPELLING", "WORD_FORM", "OTHER_NON_LEXICAL"}

# Maps a dominant LRET family to the topic-bank `type` value PEEL should lean
# toward this session. LEXICAL_PRECISION/PARAPHRASE_RANGE don't have a clean
# 1:1 topic-bank type (the bank only tags collocation/noun_phrase/phrasal_verb)
# -- mapped to noun_phrase as a documented approximation (vague-word/precision
# issues most often show up as under-specified noun choices), not a certainty.
FAMILY_TO_TOPIC_TYPE_BIAS = {
    "COLLOCATION": "collocation",
    "NOUN_PHRASE": "noun_phrase",
    "PHRASAL_VERB_OR_PREDICATE": "phrasal_verb",
    "LEXICAL_PRECISION": "noun_phrase",
    "PARAPHRASE_RANGE": "noun_phrase",
}

ANGLE_TASK_TYPES = {
    "advantages_disadvantages": ["advantage", "disadvantage"],
    "cause_effect_problem_solution": ["cause", "effect", "problem", "solution"],
    "discussion": ["side_a", "side_b"],
}
ALL_TASK_TYPES = ["opinion", "advantages_disadvantages", "cause_effect_problem_solution", "discussion"]


def load_json(path, default=None):
    if not path or not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def now_utc(override_iso=None):
    if override_iso:
        dt = datetime.fromisoformat(override_iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

def empty_ledger(student_id):
    return {
        "schema_version": "vocab_coach_ledger_v1.0",
        "student_id": student_id,
        "sessions_completed": 0,
        "next_session_available_at": None,
        "cooldown_hours": DEFAULT_COOLDOWN_HOURS,
        "exposure_counts": {},   # unit_key -> task_type -> count ; unit_key -> task_type -> "_angles" -> angle -> count
        "items": {},             # phrase -> {state, box, ...}
    }


def load_ledger(path, student_id):
    data = load_json(path, default=None)
    if data is None:
        return empty_ledger(student_id)
    return data


def cooldown_check(ledger, now):
    ts = ledger.get("next_session_available_at")
    if not ts:
        return True, None
    available_at = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if available_at.tzinfo is None:
        available_at = available_at.replace(tzinfo=timezone.utc)
    if now < available_at:
        return False, iso(available_at)
    return True, None


# ---------------------------------------------------------------------------
# Topic bank unit enumeration (derived from the bank's own structure, not
# hardcoded, so it stays correct if the bank's shape changes again)
# ---------------------------------------------------------------------------

def enumerate_units(topic_bank):
    """Returns list of dicts: {unit_key, topic, subtopic (or None)}.
    Excludes 'general' buckets -- documented in the topic bank itself as not
    a real classification subtopic, just a shared cross-cutting pool."""
    units = []
    for topic, tdata in topic_bank["topics"].items():
        if "subtopics" in tdata:
            for sub in tdata["subtopics"]:
                if sub == "general":
                    continue
                units.append({"unit_key": f"{topic}::{sub}", "topic": topic, "subtopic": sub})
        else:
            units.append({"unit_key": topic, "topic": topic, "subtopic": None})
    return units


def topic_item_cefr(topic_bank, topic, subtopic, phrase):
    tdata = topic_bank["topics"][topic]
    items = tdata["subtopics"][subtopic]["items"] if subtopic else tdata["items"]
    for it in items:
        if it["phrase"] == phrase:
            return it.get("cefr_estimate"), it.get("type")
    return None, None


# ---------------------------------------------------------------------------
# LRET history aggregation (real schema, see mapping tables above)
# ---------------------------------------------------------------------------

def aggregate_lret_family_tally(lret_session_paths):
    tally = Counter()
    raw_tally = Counter()  # for audit -- what the raw fields actually were
    sessions_read = []
    for path in lret_session_paths or []:
        data = load_json(path, default=None)
        if data is None:
            print(f"[vocab_coach_selection] WARNING: could not read LRET session {path}, skipping", file=sys.stderr)
            continue
        sessions_read.append(path)
        for u in data.get("fix_units", []):
            fam = u.get("error_family") or u.get("detector_family")
            if fam:
                raw_tally[f"FIX:{fam}"] += 1  # tallied for audit, not used for bias (out of lexical scope)
        for u in data.get("clarify_units", []):
            ut = u.get("unit_type")
            raw_tally[f"CLARIFY:{ut}"] += 1
            fam = CLARIFY_UNIT_TYPE_TO_FAMILY.get(ut)
            if fam:
                tally[fam] += 1
        for u in data.get("enhance_units", []):
            for axis in u.get("axis_candidates", []) or []:
                raw_tally[f"ENHANCE:{axis}"] += 1
                fam = ENHANCE_AXIS_TO_FAMILY.get(axis)
                if fam:
                    tally[fam] += 1
    return tally, raw_tally, sessions_read


# ---------------------------------------------------------------------------
# Score-contract level gating (no real file exists to confirm field names
# against -- resilient multi-key lookup, documented fail-safe to mid-band)
# ---------------------------------------------------------------------------

def extract_lexical_band(score_contract):
    if not score_contract:
        return None, "no_score_contract_provided"
    candidates = [
        ("lexical_resource_band_estimate", lambda c: c.get("lexical_resource_band_estimate")),
        ("criteria.lexical_resource.band", lambda c: (c.get("criteria") or {}).get("lexical_resource", {}).get("band")),
        ("lexical_resource.band", lambda c: (c.get("lexical_resource") or {}).get("band")),
        ("overall_band_estimate", lambda c: c.get("overall_band_estimate")),
        ("overall_band", lambda c: c.get("overall_band")),
    ]
    for key_name, getter in candidates:
        try:
            val = getter(score_contract)
        except Exception:
            val = None
        if isinstance(val, (int, float)):
            return float(val), key_name
    return None, "no_recognised_band_field"


def ielts_band_to_cefr(band):
    # Standard rough IELTS<->CEFR correspondence (commonly cited approximate
    # mapping, not a precision instrument).
    if band is None:
        return "B1"
    if band < 4.5:
        return "A2"
    if band < 5.5:
        return "B1"
    if band < 6.5:
        return "B1"
    if band < 7.0:
        return "B2"
    if band < 8.0:
        return "C1"
    return "C2"


def target_cefr_set(band):
    current = ielts_band_to_cefr(band)
    idx = CEFR_ORDER.index(current)
    nxt = CEFR_ORDER[min(idx + 1, len(CEFR_ORDER) - 1)]
    return {current, nxt}


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------

def exposure_count(ledger, unit_key, task_type, angle=None):
    unit_exp = ledger.get("exposure_counts", {}).get(unit_key, {})
    if angle:
        return unit_exp.get("_angles", {}).get(task_type, {}).get(angle, 0)
    return unit_exp.get(task_type, 0)


def pick_least_exposed(options, key_fn, seed):
    counts = [(key_fn(o), o) for o in options]
    min_count = min(c for c, _ in counts)
    tied = [o for c, o in counts if c == min_count]
    rnd = random.Random(seed)
    return rnd.choice(tied)


def unit_bias_affinity(unit, topic_bank, dominant_family):
    """Counts how many items in this unit's FULL topic-bank pool (all
    ~15-25 items, not just the 3-4-item shortlist attached to any one
    prompt-bank entry) match the topic-`type` the dominant LRET family biases
    toward. Used to break ties among units that are equally due for breadth-
    rotation (see choose_rotation_target) -- deliberately NOT restricted to
    a single prompt's suggested_vocabulary, since (as this build's own
    verification pass found) every prompt-bank entry within one unit reuses
    the exact same 3-item topic-vocabulary shortlist regardless of
    scenario/task_type/angle, so scoring only that shortlist can never
    actually differentiate candidates WITHIN a unit. The unit's full pool
    does vary in type composition, which is what makes this a real signal."""
    if not dominant_family:
        return 0.0
    bias_type = FAMILY_TO_TOPIC_TYPE_BIAS.get(dominant_family)
    if not bias_type:
        return 0.0
    tdata = topic_bank["topics"][unit["topic"]]
    items = tdata["subtopics"][unit["subtopic"]]["items"] if unit["subtopic"] else tdata["items"]
    if not items:
        return 0.0
    # PROPORTION, not raw count -- units vary hugely in size (6 to 87 items),
    # so a raw count would always favour the biggest unit regardless of its
    # actual type mix (confirmed directly: an earlier version of this
    # function used raw counts and both a collocation-dominant and a
    # noun_phrase-dominant synthetic student ended up routed to
    # work_and_employment, the single largest unit at 87 items, purely
    # because its absolute counts of both types dwarfed every smaller unit's
    # -- caught by this build's own verification test, not a hypothetical).
    return sum(1 for it in items if it.get("type") == bias_type) / len(items)


def choose_rotation_target(ledger, units, seed_base, topic_bank=None, dominant_family=None):
    """Decides the breadth-rotation axis: which unit and which task_type to
    serve this session (Architecture point 1's first bullet -- "not essay-
    driven... rotates for breadth"). Angle is deliberately NOT decided here
    (see select_candidate/score_candidate below for why).

    Unit selection is breadth-first (least total exposure always wins), but
    when multiple units are EQUALLY due (a real, common case -- e.g. every
    unit starts at 0 exposure for a brand-new student, so all 22 units are
    tied on session 1), the LRET family bias is used to break that tie in
    favour of the unit whose full topic-bank pool best matches the student's
    flagged family (see unit_bias_affinity). This never overrides breadth --
    a unit that is MORE overdue than the rest is always served regardless of
    bias; bias only ever chooses among units that are already equally due.

    This two-level design (bias breaks unit-ties here, AND biases candidate/
    angle choice within the chosen unit in select_candidate) was arrived at
    after this build's own verification pass found that scoring candidates
    within a single fixed unit was a structural no-op: every prompt-bank
    entry in one unit shares an identical 3-item topic-vocabulary shortlist,
    so no amount of within-unit candidate scoring could ever produce a
    different vocabulary type mix. Real type variety exists only ACROSS
    units (each unit's own ~15-25 item pool has a different type
    composition), so that is where bias needed to actually operate to be a
    real signal rather than the no-op the build prompt explicitly warns
    against ("If the bias logic is a no-op in practice, that's a build
    failure, not a minor gap.").
    """
    def unit_key_fn(u):
        base = sum(exposure_count(ledger, u["unit_key"], tt) for tt in ALL_TASK_TYPES)
        return base

    counts = [(unit_key_fn(u), u) for u in units]
    min_count = min(c for c, _ in counts)
    tied_units = [u for c, u in counts if c == min_count]

    if len(tied_units) > 1 and dominant_family and topic_bank is not None:
        affinities = [(unit_bias_affinity(u, topic_bank, dominant_family), u) for u in tied_units]
        max_affinity = max(a for a, _ in affinities)
        if max_affinity > 0:
            tied_units = [u for a, u in affinities if a == max_affinity]

    rnd = random.Random(f"{seed_base}:unit")
    unit = rnd.choice(tied_units)

    task_type = pick_least_exposed(
        ALL_TASK_TYPES,
        lambda tt: exposure_count(ledger, unit["unit_key"], tt),
        seed=f"{seed_base}:tt",
    )
    return unit, task_type


def least_exposed_angles(ledger, unit_key, task_type):
    """Returns the set of angle(s) tied for least exposure for this
    (unit, task_type), used as a rotation-breadth tie-break underneath bias
    scoring -- not a hard pre-filter."""
    if task_type not in ANGLE_TASK_TYPES:
        return None
    angles = ANGLE_TASK_TYPES[task_type]
    counts = [(exposure_count(ledger, unit_key, task_type, angle=a), a) for a in angles]
    min_count = min(c for c, _ in counts)
    return {a for c, a in counts if c == min_count}


# ---------------------------------------------------------------------------
# Candidate filtering + scoring
# ---------------------------------------------------------------------------

def filter_candidates(prompt_bank, unit, task_type):
    """Returns every single-subtopic prompt-bank entry for this
    (topic/subtopic, task_type), across ALL angles (or the sole no-angle
    entries for 'opinion'). Angle is resolved by scoring, not by filtering."""
    out = []
    for p in prompt_bank["prompts"]:
        if p["topic"] != unit["topic"]:
            continue
        if p["task_type"] != task_type:
            continue
        p_subs = p.get("subtopics", [])
        if len(p_subs) > 1:
            continue  # multi-subtopic worked examples are a bonus pool, not part of core rotation
        if unit["subtopic"]:
            if p_subs != [unit["subtopic"]]:
                continue
        else:
            if p_subs:
                continue
        out.append(p)
    return out


def score_candidate(candidate, topic_bank, dominant_family, target_cefr, preferred_angles):
    family_score = 0
    level_score = 0
    bias_type = FAMILY_TO_TOPIC_TYPE_BIAS.get(dominant_family) if dominant_family else None
    for item in candidate.get("suggested_vocabulary", []):
        if item.get("source_bank") != "topic":
            continue
        cefr, item_type = topic_item_cefr(topic_bank, item["topic"], item.get("subtopic"), item["phrase"])
        if bias_type and item_type == bias_type:
            family_score += 1
        if cefr in target_cefr:
            level_score += 1
    # Angle-rotation tie-break: only ever decides between candidates that are
    # ALREADY tied on family_score + level_score (see max()-tuple ordering in
    # select_candidate) -- so it provides breadth ("don't always pick the
    # same angle") without ever overriding a genuine bias/level signal.
    angle_rotation_score = 0
    if preferred_angles is not None and candidate.get("angle") in preferred_angles:
        angle_rotation_score = 1
    return (family_score, level_score, angle_rotation_score)


def select_candidate(candidates, topic_bank, dominant_family, target_cefr, preferred_angles, seed):
    scored = [
        (score_candidate(c, topic_bank, dominant_family, target_cefr, preferred_angles), c)
        for c in candidates
    ]
    max_score = max(scored, key=lambda x: x[0])[0]
    tied = [c for s, c in scored if s == max_score]
    rnd = random.Random(seed)
    return rnd.choice(tied)


# ---------------------------------------------------------------------------
# Review items due
# ---------------------------------------------------------------------------

def due_review_items(ledger, current_session_index):
    due = []
    for phrase, item in ledger.get("items", {}).items():
        box = item.get("box")
        if box in ("box_1", "box_2", "box_3") and item.get("next_due_session", 0) <= current_session_index:
            due.append({
                "phrase": phrase,
                "box": box,
                "source_bank": item.get("source_bank"),
                "topic": item.get("topic"),
                "subtopic": item.get("subtopic"),
                "task_type": item.get("task_type"),
                "angle": item.get("angle"),
                "note": "Weave a natural retest of this item into your paragraph, or add one extra sentence using it correctly.",
            })
    return due


def render_target_items(vocab_list):
    return ", ".join(f"'{v['phrase']}'" for v in vocab_list)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--topic-bank", required=True)
    ap.add_argument("--task-type-bank", required=True)
    ap.add_argument("--prompt-bank", required=True)
    ap.add_argument("--score-contract", default=None)
    ap.add_argument("--lret-sessions", nargs="*", default=[])
    ap.add_argument("--student-id", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--cooldown-hours", type=float, default=DEFAULT_COOLDOWN_HOURS)
    ap.add_argument("--now", default=None)
    args = ap.parse_args()

    topic_bank = load_json(args.topic_bank)
    task_type_bank = load_json(args.task_type_bank)
    prompt_bank = load_json(args.prompt_bank)
    score_contract = load_json(args.score_contract, default=None)

    ledger = load_ledger(args.ledger, args.student_id)
    now = now_utc(args.now)

    ok, available_at = cooldown_check(ledger, now)
    if not ok:
        result = {
            "artifact_type": "vocab_coach_session",
            "schema_version": "vocab_coach_session_v1.0",
            "engine_version": ENGINE_VERSION,
            "student_id": args.student_id,
            "generated_at": iso(now),
            "status": "not_yet_available",
            "next_session_available_at": available_at,
        }
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        print(f"[vocab_coach_selection] cooldown active, not yet available at {available_at}")
        return

    current_session_index = ledger.get("sessions_completed", 0) + 1
    units = enumerate_units(topic_bank)

    # LRET aggregation runs BEFORE rotation now (not after), because the
    # dominant family is used to break unit-level rotation ties -- see
    # choose_rotation_target's docstring for why this had to move.
    lret_tally, lret_raw_tally, sessions_read = aggregate_lret_family_tally(args.lret_sessions)
    dominant_family, dominant_count = (None, 0)
    if lret_tally:
        dominant_family, dominant_count = lret_tally.most_common(1)[0]
    bias_applied = dominant_family is not None and dominant_count >= 1

    seed_base = f"{args.student_id}:{current_session_index}"
    unit, task_type = choose_rotation_target(
        ledger, units, seed_base,
        topic_bank=topic_bank,
        dominant_family=dominant_family if bias_applied else None,
    )

    candidates = filter_candidates(prompt_bank, unit, task_type)
    if not candidates:
        raise SystemExit(f"No prompt-bank candidates found for {unit['unit_key']} / {task_type} -- bank coverage gap.")

    band_value, band_source = extract_lexical_band(score_contract)
    target_cefr = target_cefr_set(band_value)

    preferred_angles = least_exposed_angles(ledger, unit["unit_key"], task_type)

    chosen = select_candidate(
        candidates, topic_bank,
        dominant_family if bias_applied else None,
        target_cefr,
        preferred_angles,
        seed=f"{seed_base}:candidate",
    )
    angle = chosen.get("angle")

    due_items = due_review_items(ledger, current_session_index)

    instruction_final = chosen["instruction_template"].format(
        target_items=render_target_items(chosen["suggested_vocabulary"])
    )

    session = {
        "artifact_type": "vocab_coach_session",
        "schema_version": "vocab_coach_session_v1.0",
        "engine_version": ENGINE_VERSION,
        "session_id": hashlib.sha256(f"{args.student_id}:{current_session_index}:{now.isoformat()}".encode()).hexdigest()[:16],
        "student_id": args.student_id,
        "generated_at": iso(now),
        "status": "generated",
        "session_index": current_session_index,
        "cooldown_hours": args.cooldown_hours,
        "rotation": {
            "unit_key": unit["unit_key"],
            "topic": unit["topic"],
            "subtopic": unit["subtopic"],
            "task_type": task_type,
            "angle": angle,
        },
        "prompt": {
            "prompt_id": chosen["prompt_id"],
            "topic": chosen["topic"],
            "subtopics": chosen.get("subtopics", []),
            "task_type": chosen["task_type"],
            "angle": chosen.get("angle"),
            "scenario_text": chosen["scenario_text"],
            "instruction_template": chosen["instruction_template"],
            "instruction_final": instruction_final,
            "suggested_vocabulary": chosen["suggested_vocabulary"],
        },
        "review_items": due_items,
        "lret_family_bias": {
            "family_tally": dict(lret_tally),
            "raw_field_tally_audit": dict(lret_raw_tally),
            "sessions_read": sessions_read,
            "dominant_family": dominant_family,
            "dominant_count": dominant_count,
            "bias_applied": bias_applied,
            "topic_type_biased_toward": FAMILY_TO_TOPIC_TYPE_BIAS.get(dominant_family) if bias_applied else None,
            "note": (
                "No LRET session history provided or no vocab-relevant family found -- "
                "candidate selection fell back to level-fit + random tie-break, no LRET bias applied."
                if not bias_applied else
                f"Dominant LRET family '{dominant_family}' (count={dominant_count}) biased candidate "
                f"selection toward topic-bank items of type '{FAMILY_TO_TOPIC_TYPE_BIAS.get(dominant_family)}'."
            ),
        },
        "level_gate": {
            "band_value_used": band_value,
            "band_source": band_source,
            "target_cefr": sorted(target_cefr),
        },
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(session, f, indent=2, ensure_ascii=False)
    print(f"[vocab_coach_selection] session {session['session_id']} written to {args.output}")


if __name__ == "__main__":
    main()
