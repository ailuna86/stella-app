#!/usr/bin/env python3
"""
vocab_coach_ledger_update_v1_0.py
=====================================

Implements Component 3 of VOCABULARY_COACH_ENGINE_BUILD_PROMPT_V1.md: updates
a student's Vocabulary Coach ledger after a session has been generated and
graded (`vocab_coach_selection_engine_v1_0.py` + `vocab_coach_response_grader_v1_0.py`).

Leitner box state machine (per the build prompt's Architecture point 4):
    new --(used_correctly)--> box_1 --(success)--> box_2 --(success)--> box_3 --(success)--> mastered
    any box_N --(anything else)--> box_1 (demotion)
Spacing (day-based, matched to session index since cadence is daily/bi-daily):
    box_1 retested at current_session_index + 1
    box_2 retested at current_session_index + 3
    box_3 retested at current_session_index + 7
    mastered: no further next_due_session, kept in history, never re-served as new or review.

NOTE ON FILE-MUTABILITY CONVENTION: this project's standing rule is "never
edit or overwrite a file -- write a new version-numbered file instead". That
rule has consistently been applied throughout this project to *authored
content* (engine code, vocabulary banks, spec documents) -- not to runtime
state. A ledger is, by definition, mutable per-student state that evolves
every session (the same way `vocab_coach_engine_v1_0_0.py`'s own
`load_ledger`/`save_ledger` functions already read-modify-write a single
ledger file in place). This engine follows that same established precedent:
it writes the updated ledger back to the same path it read from (or to
--output if given), not a new version-numbered file. This is called out
explicitly here so it is not mistaken for a violation of the versioning rule.

PEEL -> LRET transfer-check hand-off (build prompt's Component 4, final
paragraph): the LRET-side annotation (`lret_engine_v1_13_0...py` reading a
`--vocabulary-coach-ledger` flag and tagging `confirmed_transfer_from_peel`)
is explicitly DEFERRED in this pass, per the user's own scope decision to
build backend engines only (no pipeline/engine-to-engine wiring, since the
gold-pipeline files this would hook into don't exist). What IS built here,
per the build prompt's own fallback allowance ("the ledger must still export
the taught-item list in a shape ready for LRET to consume later"): every
ledger write includes a `taught_items_export` list -- phrase, box, last
outcome, last session -- in a stable, simple shape, ready for a future LRET
version to read whenever that integration is actually built.

CLI:
    --session PATH     (vocab_coach_session artifact)
    --grading PATH      (vocab_coach_grading artifact)
    --ledger PATH       (may not exist yet)
    --output PATH       (default: same as --ledger, in place -- see note above)
"""
import argparse
import json
import os
from datetime import datetime, timedelta, timezone

ENGINE_VERSION = "vocab-coach-ledger-update-v1.0"

BOX_SPACING = {"box_1": 1, "box_2": 3, "box_3": 7}
BOX_PROMOTION = {"new": "box_1", "box_1": "box_2", "box_2": "box_3", "box_3": "mastered"}


def load_json(path, default=None):
    if not path or not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


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

    session = load_json(args.session)
    grading = load_json(args.grading)

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
