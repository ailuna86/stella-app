#!/usr/bin/env python3
"""
vocab_coach_selection_engine_v1_2.py
=====================================

Implements Academic_Words_Redesign_Spec_v1.docx Section 3 (selection-engine-
runtime picking of bare academic words) and Section 4 (hint-on-request) on
top of vocab_coach_selection_engine_v1_1.py. v1_1's rotation, LRET-bias,
candidate filtering/scoring, and Leitner-review logic are UNTOUCHED -- every
function below that isn't new is byte-identical to v1_1. v1_1 is left on
disk, unused, per project convention (new versioned file, not an in-place
edit).

CHANGES FROM v1_1:

1. New: academic word selection. After choose_rotation_target() picks the
   unit/task_type (unchanged), this engine reads that unit's `academic_words`
   pool from the topic bank (vocab_coach_topic_bank_v1_5_0.json -- the new
   `academic_words` key added per-unit in that build) and picks up to
   ACADEMIC_WORD_CAP (2) least-exposed bare words, same
   least-exposed-with-random-tiebreak pattern already used everywhere else
   in this file (pick_least_exposed/select_candidate). Falls back to []
   (not a crash) if the topic bank in use predates this field -- so this
   engine works unchanged against v1_4_0 or earlier, it just serves zero
   academic words in that case (see `academic_words_available_in_bank` in
   the output, and the "old bank still works" note in main()).

2. No new ledger schema. Per the spec's own instruction ("reuses the
   existing exposure_counts structure... additive, not a ledger schema
   change"), the actual implementation below goes one step further and
   needs NO new bookkeeping at all: academic words are appended into the
   SAME `suggested_vocabulary` list the topic-bank items already live in,
   tagged `source_bank: "academic_word"`. vocab_coach_ledger_update_v1_1.py's
   update_new_item() already iterates that list generically by phrase/
   source_bank/topic/subtopic -- it does not care whether a phrase is a
   3-word collocation or a bare word -- so a picked-but-not-yet-taught
   academic word gets its own ledger["items"][word] entry, Leitner box, and
   history the FIRST time it's served, exactly like a topic-bank item does.
   Least-exposed selection for NEXT time then reads that same
   ledger["items"][word]["history"] length (see academic_word_exposure()
   below) -- there was no need to invent a parallel exposure_counts
   sub-structure once the existing per-item history already tracks
   "how many times has the student seen this exact word". No changes to
   vocab_coach_ledger_update_v1_1.py or vocab_coach_response_grader_v1_1.py
   were needed for this feature (grader already judges semantic correctness
   per-phrase via LLM judge, regardless of phrase length or source_bank --
   re-verified, not just assumed, against _call_llm_judge()'s prompt, which
   asks whether "the item appears, used with its correct meaning, in a
   sensible, natural context" -- true for a bare word exactly as for a
   collocation).

3. New: structural_hint pass-through, not shown by default. Each picked
   academic word carries its `structural_hint` (e.g. "usually with
   'among/in' + a group/context noun") into suggested_vocabulary exactly as
   authored in the bank. This engine does not decide display policy -- per
   spec Section 4, the hint is surfaced by the frontend PEEL session UI only
   if the student explicitly asks ("Need a hint?", collapsed by default).
   The field is simply present in the data for that UI to read on request.

4. New: mission composition cap. ACADEMIC_WORD_CAP = 2, applied AFTER the
   existing topic/task-type suggested_vocabulary is chosen (unchanged
   count/logic) -- so a mission ends up with the existing 1-3 topic items
   plus up to 2 academic words, never all-academic or all-topic, per spec
   Section 3.1.

CLI (unchanged from v1_1 -- point --topic-bank at
vocab_coach_topic_bank_v1_5_0.json to get academic words; v1_4_0 still works,
just yields zero academic words):
    --ledger PATH               (may not exist yet -- first session for this student)
    --topic-bank PATH           (vocab_coach_topic_bank_v1_5_0.json recommended)
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

ENGINE_VERSION = "vocab-coach-selection-engine-v1.2"
DEFAULT_COOLDOWN_HOURS = 24.0
ACADEMIC_WORD_CAP = 2

CEFR_ORDER = ["A1", "A2", "B1", "B2", "C1", "C2"]

# ---------------------------------------------------------------------------
# LRET family mapping -- unchanged from v1_1, see that file's header for the
# full grounding notes against the real v1.12.x LRET output schema.
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


def require_json(path, label):
    """Like load_json, but for REQUIRED inputs: raises a clear, actionable
    error immediately instead of returning None and letting a downstream
    function crash with an opaque TypeError."""
    data = load_json(path, default=None)
    if data is None:
        raise SystemExit(f"Required file not found or invalid: {label} = {path!r}")
    return data


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
        "items": {},             # phrase -> {state, box, ...}  (also holds academic-word exposure history -- see academic_word_exposure())
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
# Topic bank unit enumeration (unchanged from v1_1)
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
# NEW in v1_2: academic word pool lookup + least-exposed selection
# ---------------------------------------------------------------------------

def academic_words_pool(topic_bank, unit):
    """Returns this unit's academic_words list as authored by
    build_topic_bank_v1_5_0.py, or [] if the bank in use predates this field
    (e.g. vocab_coach_topic_bank_v1_4_0.json) -- a graceful no-op, not a
    crash, so this engine still runs against an older bank."""
    tdata = topic_bank["topics"].get(unit["topic"], {})
    if unit["subtopic"]:
        sub = tdata.get("subtopics", {}).get(unit["subtopic"], {})
        return sub.get("academic_words", []) or []
    return tdata.get("academic_words", []) or []


def bank_has_academic_words(topic_bank, units):
    """Cheap global check used only for an honest status flag in the output
    (see main()) -- does NOT affect selection, which already degrades
    per-unit via academic_words_pool()'s [] fallback above."""
    return any(academic_words_pool(topic_bank, u) for u in units)


def academic_word_exposure(ledger, word):
    """How many times this exact word has already been served to this
    student, reusing the ledger's existing items[phrase].history the same
    way a topic-bank collocation's exposure is implicitly tracked -- see
    module docstring change 2 for why no new ledger structure was needed."""
    entry = ledger.get("items", {}).get(word)
    if not entry:
        return 0
    return len(entry.get("history", []))


def pick_academic_words(ledger, topic_bank, unit, cap=ACADEMIC_WORD_CAP, seed=None):
    """Least-exposed-with-random-tiebreak pick from this unit's academic_words
    pool, same pattern as pick_least_exposed() elsewhere in this file. Picks
    up to `cap` words; fewer if the pool itself is smaller than `cap` (should
    not happen post-v1.5.0 -- every unit has >= 6 words -- but handled
    defensively rather than assumed)."""
    pool = academic_words_pool(topic_bank, unit)
    if not pool:
        return []
    rnd = random.Random(seed)
    scored = sorted(
        ((academic_word_exposure(ledger, w["word"]), rnd.random(), w) for w in pool),
        key=lambda t: (t[0], t[1]),
    )
    return [w for _, _, w in scored[:cap]]


def academic_words_to_vocab_items(picks, unit):
    """Shapes picked academic_words bank entries into the same
    suggested_vocabulary item shape topic-bank entries already use (a dict
    with a 'phrase' key, since render_target_items() and the ledger update
    script both key off 'phrase') -- so no downstream code needs to know the
    difference between a collocation and a bare word."""
    out = []
    for w in picks:
        out.append({
            "phrase": w["word"],
            "source_bank": "academic_word",
            "topic": unit["topic"],
            "subtopic": unit["subtopic"],
            "part_of_speech": w.get("part_of_speech"),
            "cefr_estimate": w.get("cefr_estimate"),
            # Present in the data for the frontend to show ONLY on request
            # (spec Section 4) -- this engine does not decide display policy.
            "structural_hint": w.get("structural_hint"),
        })
    return out


# ---------------------------------------------------------------------------
# LRET history aggregation (unchanged from v1_1)
# ---------------------------------------------------------------------------

def aggregate_lret_family_tally(lret_session_paths):
    tally = Counter()
    raw_tally = Counter()
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
                raw_tally[f"FIX:{fam}"] += 1
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
# Score-contract level gating (unchanged from v1_1)
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
# Rotation (unchanged from v1_1)
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
    if not dominant_family:
        return 0.0
    bias_type = FAMILY_TO_TOPIC_TYPE_BIAS.get(dominant_family)
    if not bias_type:
        return 0.0
    tdata = topic_bank["topics"][unit["topic"]]
    items = tdata["subtopics"][unit["subtopic"]]["items"] if unit["subtopic"] else tdata["items"]
    if not items:
        return 0.0
    return sum(1 for it in items if it.get("type") == bias_type) / len(items)


def choose_rotation_target(ledger, units, seed_base, topic_bank=None, dominant_family=None):
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
    if task_type not in ANGLE_TASK_TYPES:
        return None
    angles = ANGLE_TASK_TYPES[task_type]
    counts = [(exposure_count(ledger, unit_key, task_type, angle=a), a) for a in angles]
    min_count = min(c for c, _ in counts)
    return {a for c, a in counts if c == min_count}


# ---------------------------------------------------------------------------
# Candidate filtering + scoring (unchanged from v1_1)
# ---------------------------------------------------------------------------

def filter_candidates(prompt_bank, unit, task_type):
    out = []
    for p in prompt_bank["prompts"]:
        if p["topic"] != unit["topic"]:
            continue
        if p["task_type"] != task_type:
            continue
        p_subs = p.get("subtopics", [])
        if len(p_subs) > 1:
            continue
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
# Review items due (unchanged from v1_1)
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

    topic_bank = require_json(args.topic_bank, "--topic-bank")
    task_type_bank = require_json(args.task_type_bank, "--task-type-bank")
    prompt_bank = require_json(args.prompt_bank, "--prompt-bank")
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

    # --- NEW in v1_2: pick up to ACADEMIC_WORD_CAP least-exposed academic
    # words from this unit's pool and append them to suggested_vocabulary,
    # capped so a mission is never all-academic or all-topic (spec §3.1).
    academic_picks = pick_academic_words(ledger, topic_bank, unit, cap=ACADEMIC_WORD_CAP, seed=f"{seed_base}:academic")
    academic_vocab_items = academic_words_to_vocab_items(academic_picks, unit)
    merged_suggested_vocabulary = list(chosen["suggested_vocabulary"]) + academic_vocab_items

    instruction_final = chosen["instruction_template"].format(
        target_items=render_target_items(merged_suggested_vocabulary)
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
            # Topic-bank items + up to ACADEMIC_WORD_CAP academic words,
            # merged -- ledger_update_v1_1.py's update_new_item() already
            # iterates this list generically by phrase/source_bank/topic/
            # subtopic, so no downstream ledger-side change was needed to
            # support the new source_bank: "academic_word" entries.
            "suggested_vocabulary": merged_suggested_vocabulary,
        },
        "review_items": due_items,
        "academic_vocabulary": {
            "picked": academic_vocab_items,
            "cap": ACADEMIC_WORD_CAP,
            "pool_size_in_unit": len(academic_words_pool(topic_bank, unit)),
            "bank_has_academic_words": bank_has_academic_words(topic_bank, units),
            "note": (
                "Picked from vocab_coach_topic_bank_v1_5_0.json's academic_words pool for this unit, "
                "least-exposed first (reusing ledger.items[phrase].history as the exposure signal). "
                "structural_hint is included in the data for the frontend to surface ONLY on explicit "
                "student request ('Need a hint?', collapsed by default) -- never shown up front, per "
                "Academic_Words_Redesign_Spec_v1.docx Section 4."
                if academic_vocab_items else
                "No academic words picked -- either this unit's pool is empty or the topic bank in use "
                "predates the academic_words field (e.g. v1_4_0). Not an error; degrades gracefully."
            ),
        },
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
