#!/usr/bin/env python3
"""
vocab_coach_ledger_update_v1_1.py
=====================================

Implements Component 3 of VOCABULARY_COACH_ENGINE_BUILD_PROMPT_V1.md: updates
a student's Vocabulary Coach ledger after a session has been generated and
graded (`vocab_coach_selection_engine_v1_1.py` + `vocab_coach_response_grader_v1_1.py`).

CHANGES FROM v1_0 (bug-fix pass only -- Leitner state machine, exposure
bookkeeping, and taught_items_export logic are untouched):

1. Required-file-load robustness fix: --session and --grading are both
   required CLI args, but v1_0's load_json() silently returned None for a
   missing/bad path (same pattern as the sibling selection engine). A
   missing/mistyped path produced an opaque
   `AttributeError: 'NoneType' object has no attribute 'get'` on
   `session.get("status")` instead of a clear message. main() now validates
   both immediately after loading and raises
   `SystemExit(f"Required file not found or invalid: {path}")` if either is
   None. --ledger is unaffected (already correctly optional -- may not exist
   yet for a first session, handled via `load_json(args.ledger, default=None)
   or empty_ledger(...)`).

2. Docstring correction: v1_0 referenced `vocab_coach_engine_v1_0_0.py`'s
   load_ledger/save_ledger functions as an "already established" precedent
   for in-place ledger mutation. No file by that name exists anywhere in
   this project (confirmed directly, and consistent with the same phantom
   reference appearing in the other two engine files' docstrings) -- this is
   corrected below to state the in-place-mutation rationale on its own
   merits (a ledger is runtime state, not authored content, so the project's
   "new versioned file" convention for authored/engine content does not
   apply to it) without citing a file that isn't real.

   v1_0 also stated the LRET hand-off piece was "explicitly DEFERRED... per
   the user's own scope decision to build backend engines only" -- the
   actual recorded build-scope answer was "Full stack together", not
   backend-only. The taught_items_export mechanism itself is unaffected by
   this correction (it's still the right shape to hand off to a future LRET
   read), but the false attribution is removed.

CLI (unchanged from v1_0):
    --session PATH     (vocab_coach_session artifact)
    --grading PATH      (vocab_coach_grading artifact)
    --ledger PATH       (may not exist yet)
    --output PATH       (default: same as --ledger, in place -- see note above)
"""
import argparse
import json
import os
from datetime import datetime, timedelta, timezone

ENGINE_VERSION = "vocab-coach-ledger-update-v1.1"

BOX_SPACING = {"box_1": 1, "box_2": 3, "box_3": 7}
BOX_PROMOTION = {"new": "box_1", "box_1": "box_2", "box_2": "box_3", "box_3": "mastered"}


def load_json(path, default=None):
    if not path or not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def require_json(path, label):
    """Like load_json, but for REQUIRED inputs: raises a clear, actionable
    error immediately instead of returning None and letting a downstream
    .get()/[] access crash with an opaque AttributeError/TypeError. (Fix in
    v1_1 -- see module docstring, change 1.)"""
    data = load_json(path, default=None)
    if data is None:
        raise SystemExit(f"Required file not found or invalid: {label} = {path!r}")
    return data


def empty_ledger(student_id):
    return {
        "schema_version": "vocab_coach_ledger_v1.0",
        "student_id": student_id,
        "sessions_completed": 0,
        "next_session_available_at": None,
        "cooldown_hours": 24.0,
        "exposure_counts": {},
        "items": {},
    }


def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def verdict_map(grading):
    return {row["phrase"]: row["verdict"] for row in grading.get("item_verdicts", [])}


def bump_exposure(ledger, unit_key, task_type, angle):
    exp = ledger.setdefault("exposure_counts", {})
    unit_exp = exp.setdefault(unit_key, {})
    unit_exp[task_type] = unit_exp.get(task_type, 0) + 1
    if angle:
        angle_bucket = unit_exp.setdefault("_angles", {}).setdefault(task_type, {})
        angle_bucket[angle] = angle_bucket.get(angle, 0) + 1


def update_new_item(ledger, phrase, meta, verdict, session_index):
    items = ledger.setdefault("items", {})
    entry = items.get(phrase, {
        "state": "new",
        "box": "new",
        "source_bank": meta.get("source_bank", "topic"),
        "topic": meta.get("topic"),
        "subtopic": meta.get("subtopic"),
        "task_type": meta.get("task_type"),
        "angle": meta.get("angle"),
        "history": [],
    })
    entry["history"].append({"session_index": session_index, "verdict": verdict, "role": "new"})
    if verdict == "used_correctly":
        entry["box"] = BOX_PROMOTION["new"]  # -> box_1
        entry["next_due_session"] = session_index + BOX_SPACING[entry["box"]]
        entry["needs_reteaching"] = False
    else:
        entry["box"] = "new"
        entry["needs_reteaching"] = True
        entry.pop("next_due_session", None)
    entry["last_seen_session"] = session_index
    entry["last_outcome"] = verdict
    items[phrase] = entry


def update_review_item(ledger, phrase, review_meta, verdict, session_index):
    items = ledger.setdefault("items", {})
    entry = items.get(phrase)
    if entry is None:
        # Defensive: a review item should already exist in the ledger (it was
        # selected FROM the ledger by the selection engine). If it's somehow
        # missing, treat it as a fresh box_1 entry rather than crashing.
        entry = {
            "state": "review",
            "box": "box_1",
            "source_bank": review_meta.get("source_bank", "topic"),
            "topic": review_meta.get("topic"),
            "subtopic": review_meta.get("subtopic"),
            "task_type": review_meta.get("task_type"),
            "angle": review_meta.get("angle"),
            "history": [],
        }
    entry["history"].append({"session_index": session_index, "verdict": verdict, "role": "review"})
    current_box = entry.get("box", "box_1")
    if verdict == "used_correctly":
        new_box = BOX_PROMOTION.get(current_box, "box_1")
        entry["box"] = new_box
        if new_box == "mastered":
            entry.pop("next_due_session", None)
        else:
            entry["next_due_session"] = session_index + BOX_SPACING[new_box]
    else:
        entry["box"] = "box_1"
        entry["next_due_session"] = session_index + BOX_SPACING["box_1"]
    entry["last_seen_session"] = session_index
    entry["last_outcome"] = verdict
    items[phrase] = entry


def build_taught_items_export(ledger):
    out = []
    for phrase, entry in ledger.get("items", {}).items():
        out.append({
            "phrase": phrase,
            "box": entry.get("box"),
            "last_outcome": entry.get("last_outcome"),
            "last_seen_session": entry.get("last_seen_session"),
            "topic": entry.get("topic"),
            "subtopic": entry.get("subtopic"),
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--grading", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--output", default=None)
    args = ap.parse_args()

    # v1_1 fix: both required artifacts now raise a clear error on a
    # bad/missing path instead of silently becoming None.
    session = require_json(args.session, "--session")
    grading = require_json(args.grading, "--grading")

    if session.get("status") == "not_yet_available":
        raise SystemExit("Refusing to update ledger from a 'not_yet_available' session artifact -- no session was actually generated/graded.")

    student_id = session["student_id"]
    ledger = load_json(args.ledger, default=None) or empty_ledger(student_id)

    session_index = session["session_index"]
    rotation = session["rotation"]
    verdicts = verdict_map(grading)

    for item in session["prompt"]["suggested_vocabulary"]:
        phrase = item["phrase"]
        verdict = verdicts.get(phrase, "not_used")
        meta = {
            "source_bank": item.get("source_bank"),
            "topic": item.get("topic") or rotation.get("topic"),
            "subtopic": item.get("subtopic") or rotation.get("subtopic"),
            "task_type": item.get("task_type") or rotation.get("task_type"),
            "angle": item.get("angle") or rotation.get("angle"),
        }
        update_new_item(ledger, phrase, meta, verdict, session_index)

    for review in session.get("review_items", []):
        phrase = review["phrase"]
        verdict = verdicts.get(phrase, "not_used")
        update_review_item(ledger, phrase, review, verdict, session_index)

    bump_exposure(ledger, rotation["unit_key"], rotation["task_type"], rotation.get("angle"))

    ledger["sessions_completed"] = session_index
    cooldown_hours = session.get("cooldown_hours", ledger.get("cooldown_hours", 24.0))
    ledger["cooldown_hours"] = cooldown_hours
    ledger["next_session_available_at"] = iso(now_utc() + timedelta(hours=cooldown_hours))
    ledger["taught_items_export"] = build_taught_items_export(ledger)
    ledger["last_updated_by_engine_version"] = ENGINE_VERSION

    out_path = args.output or args.ledger
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=2, ensure_ascii=False)
    print(f"[vocab_coach_ledger_update] ledger written to {out_path} (sessions_completed={ledger['sessions_completed']})")


if __name__ == "__main__":
    main()
