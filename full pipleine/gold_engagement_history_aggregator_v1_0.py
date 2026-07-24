#!/usr/bin/env python3
"""
Gold Engagement History Aggregator v1.0 — standalone
======================================================

New engine (not a patch to any existing file). Built for the "continuous
loop" task (Pipeline_Frontend_Spec_v2.docx / product-owner decision:
"Practice and Writing Coach should feed LIE with real learned history, not
presence checks; Essay Revision should become a real LIE input"). This is
the Python side of that: it reads three real, already-persisted evidence
sources and aggregates them into one shared artifact that
gold_lie_profile_builder_standalone_v1_4_7.py's new --engagement-history
argument consumes.

The three sources, and why each is read the way it is:

  1. Practice (practice_results SQLite table, stella-frontend/lib/server/db.ts).
     This script does NOT open stella.db directly. Confirmed directly (not
     assumed) that Python's stdlib sqlite3 CAN open the WAL-mode file
     better-sqlite3 creates (ran `sqlite3.connect()` + a real SELECT against
     a live copy of this project's stella.db from this sandbox and got real
     rows back) -- so a direct-read path is technically possible. It was
     deliberately NOT used for two reasons: (a) stella-frontend/lib/server/
     store.ts already has tested, working query functions for exactly this
     data (practiceResultsFor/missionResultsFor) -- re-implementing the same
     two SELECTs in Python would be a second, independently-drifting copy of
     the same schema knowledge; (b) the real deployment target is Windows
     (this project's env), where a second OS process opening the same
     better-sqlite3-owned file concurrently carries more file-locking risk
     than the Linux sandbox this was verified in. So the Node side (see
     goldPipeline.ts's refreshLearnerProfile()) exports the two tables' rows
     for one student to a plain JSON file using its own already-working
     store.ts functions, and this script reads that JSON file like any other
     pipeline artifact -- consistent with this whole pipeline's file-in/
     file-out convention, and with zero new direct-SQL code path anywhere.
     --practice-mission-export points at that export file.

  2. Writing Coach (mission_results rows, inside the same export file above).
     outcome is one of pass/partial_pass/fail (also invalid_empty_response/
     invalid_incomplete_output/other, kept but not treated as fail — see
     runMissionGrading/route.ts, an incomplete/empty submission was never
     graded as a real attempt and store.ts's saveMissionResult() is only
     called for pass/partial_pass/fail in the current route).

  3. Essay Revision — the two per-essay artifacts already produced by
     runRevisionComparison() and runRevisionScopedRecheck() in goldPipeline.ts:
     {session_dir}/revision_comparisons/comparison_*.json (raw keys confirmed
     directly by reading how goldPipeline.ts's runRevisionComparison() maps
     them: model_available_to_student, generation_status, full_model_essay,
     full_model_word_count, items[]) and {session_dir}/revision_scoped_rechecks/
     recheck_*.json (raw keys confirmed the same way from
     runRevisionScopedRecheck(): summary.{sentences_rewritten, now_error_free,
     already_clean_rewrite, still_has_errors, introduced_new_error_sentences,
     total_errors_fixed, total_errors_introduced, honest_summary_text,
     scope_disclaimer, new_sentences_added, sentences_removed,
     truncated_for_cost_cap}, sentence_results[]). --session-dir points at the
     student's most recent essay's session directory.

Practice family aggregation and its one real limitation (documented, not
hidden): practice_results only stores one correct/total PAIR per whole
session, not per-exercise correctness (confirmed directly from db.ts's
schema and store.ts's savePracticeResult signature). A session's
exercise_ids can span several exercise families at once, so there is no way
to honestly attribute "N correct out of M" down to a single family from this
table alone -- doing so would be inventing precision the evidence doesn't
support, which is exactly what this file's boundary forbids. So this engine
reports two SEPARATE, both fully real things instead of one fabricated one:
  - session-level accuracy trend (exact — every session's correct/total is
    real and unambiguous at the session level).
  - family REPETITION counts (exact — how many times each family's exercises
    were served to this student, via a real join against the exercise bank's
    exercise_id -> family mapping, --exercise-bank
    va_exercise_bank_v11d_approved.jsonl). This answers "which families does
    this student keep coming back to", which is genuine, useful signal even
    without a per-family accuracy number attached to it.

Thresholds (all documented here, not buried in code):
  RECENT_WINDOW_DAYS = 7, PRIOR_WINDOW_DAYS = 7 (days 8-14 ago) -- same
    7-day window app/trainer/page.tsx's "Group focus this week" card already
    uses for weekly practice/mission aggregation, reused here for the same
    reason (a week is long enough to smooth day-to-day noise, short enough
    to be "recent").
  MIN_SESSIONS_FOR_WINDOW_STAT = 2 practice sessions / MIN_MISSIONS_FOR_WINDOW_STAT
    = 2 missions -- a single session/mission is not a trend, it's one data
    point. (app/trainer/page.tsx gates its own group-level suggestions at
    >=3 for a WHOLE GROUP across a week; 2 is used here because this is one
    student's own history, where the practical volume is much lower.)
  MIN_ITEMS_FOR_WINDOW_STAT = 5 practice items attempted in a window before
    reporting that window's accuracy percentage.
  TREND_STABLE_BAND_PCT = 5 -- a recent-vs-prior accuracy/pass-rate delta
    within +-5 percentage points is reported as "stable" rather than a false
    "improving"/"declining" read on noise.
  MIN_FAMILY_REPETITION_TO_SURFACE = 3 -- a practice family needs at least 3
    exposures across all history before it's surfaced in
    repeated_practice_families (same "don't call a 1-count coincidence a
    pattern" reasoning as lexical_signal_aggregator_v1_0.py's dominance
    gates).

Boundary:
- Does not score, detect, generate feedback, coach, or classify anything.
- Only aggregates three already-produced evidence sources (a JSON export of
  two SQLite tables, and per-essay revision artifact files already written
  by other engines) into one shared shape for the LIE profile builder.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "GOLD_ENGAGEMENT_HISTORY_STANDALONE_V1_0"
ENGINE_ID = "VA_STELLA_GOLD_ENGAGEMENT_HISTORY_AGGREGATOR"
ENGINE_VERSION = "1.0.0-standalone-no-imports"

RECENT_WINDOW_DAYS = 7
PRIOR_WINDOW_DAYS = 7
MIN_SESSIONS_FOR_WINDOW_STAT = 2
MIN_MISSIONS_FOR_WINDOW_STAT = 2
MIN_ITEMS_FOR_WINDOW_STAT = 5
TREND_STABLE_BAND_PCT = 5.0
MIN_FAMILY_REPETITION_TO_SURFACE = 3
TOP_N_FAMILIES = 5

PASS_LIKE_OUTCOMES = {"pass", "partial_pass"}
FAIL_LIKE_OUTCOMES = {"fail"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Optional[str]) -> Optional[Any]:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_json(path: str, data: Any, pretty: bool = False) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2 if pretty else None) + "\n", encoding="utf-8")


def parse_iso(ts: Any) -> Optional[datetime]:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        s = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def pct(numerator: float, denominator: float) -> Optional[float]:
    if not denominator:
        return None
    return round(100.0 * numerator / denominator, 1)


def trend_from_delta(recent_pct: Optional[float], prior_pct: Optional[float]) -> str:
    if recent_pct is None or prior_pct is None:
        return "insufficient_data"
    delta = recent_pct - prior_pct
    if abs(delta) <= TREND_STABLE_BAND_PCT:
        return "stable"
    return "improving" if delta > 0 else "declining"


# ---------------------------------------------------------------------------
# Practice
# ---------------------------------------------------------------------------

def load_exercise_family_map(bank_path: Optional[str]) -> Dict[str, Dict[str, str]]:
    """exercise_id -> {"family": ..., "family_label": ...}. Best-effort: a
    missing/unreadable bank file degrades to an empty map (family repetition
    just won't be reported), never an error -- this is a supplementary join,
    not a required input."""
    out: Dict[str, Dict[str, str]] = {}
    if not bank_path:
        return out
    p = Path(bank_path)
    if not p.exists():
        return out
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                eid = row.get("exercise_id")
                if not eid:
                    continue
                out[eid] = {
                    "family": row.get("family") or "unspecified",
                    "family_label": row.get("family_label") or (row.get("family") or "Unspecified").replace("_", " ").title(),
                }
    except Exception:
        return out
    return out


def build_practice_history(rows: List[Dict[str, Any]], family_map: Dict[str, Dict[str, str]]) -> Optional[Dict[str, Any]]:
    """rows: practice_results rows as exported by store.ts's practiceResultsFor
    ({at, correct, total, exerciseIds}). Returns None (not an empty dict) when
    there is no practice history at all, so callers can distinguish "not
    tracked yet" from "tracked, zero sessions"."""
    if not rows:
        return None

    now = datetime.now(timezone.utc)
    recent_cutoff = now - timedelta(days=RECENT_WINDOW_DAYS)
    prior_cutoff = now - timedelta(days=RECENT_WINDOW_DAYS + PRIOR_WINDOW_DAYS)

    total_sessions = len(rows)
    total_correct = 0
    total_attempted = 0
    recent_sessions = recent_correct = recent_attempted = 0
    prior_sessions = prior_correct = prior_attempted = 0
    family_counts: Dict[str, Dict[str, Any]] = {}
    last_practice_at: Optional[str] = None
    last_practice_dt: Optional[datetime] = None

    for row in rows:
        at = row.get("at")
        dt = parse_iso(at)
        correct = int(row.get("correct") or 0)
        total = int(row.get("total") or 0)
        total_correct += correct
        total_attempted += total
        if dt is not None and (last_practice_dt is None or dt > last_practice_dt):
            last_practice_dt = dt
            last_practice_at = at
        if dt is not None and dt >= recent_cutoff:
            recent_sessions += 1
            recent_correct += correct
            recent_attempted += total
        elif dt is not None and prior_cutoff <= dt < recent_cutoff:
            prior_sessions += 1
            prior_correct += correct
            prior_attempted += total

        for eid in row.get("exerciseIds") or []:
            fam_row = family_map.get(eid)
            fam = fam_row["family"] if fam_row else "unspecified"
            fam_label = fam_row["family_label"] if fam_row else "Unspecified"
            bucket = family_counts.setdefault(fam, {"family_label": fam_label, "times_practiced": 0, "sessions_touched": set()})
            bucket["times_practiced"] += 1
            bucket["sessions_touched"].add(at)

    recent_accuracy = pct(recent_correct, recent_attempted) if recent_attempted >= MIN_ITEMS_FOR_WINDOW_STAT and recent_sessions >= MIN_SESSIONS_FOR_WINDOW_STAT else None
    prior_accuracy = pct(prior_correct, prior_attempted) if prior_attempted >= MIN_ITEMS_FOR_WINDOW_STAT and prior_sessions >= MIN_SESSIONS_FOR_WINDOW_STAT else None

    repeated_families = sorted(
        (
            {
                "family": fam,
                "family_label": b["family_label"],
                "times_practiced": b["times_practiced"],
                "sessions_touched": len(b["sessions_touched"]),
            }
            for fam, b in family_counts.items()
            if b["times_practiced"] >= MIN_FAMILY_REPETITION_TO_SURFACE
        ),
        key=lambda r: -r["times_practiced"],
    )[:TOP_N_FAMILIES]

    return {
        "total_sessions": total_sessions,
        "total_items_attempted": total_attempted,
        "total_items_correct": total_correct,
        "overall_accuracy_pct": pct(total_correct, total_attempted),
        "recent_window_days": RECENT_WINDOW_DAYS,
        "recent_sessions": recent_sessions,
        "recent_items_attempted": recent_attempted,
        "recent_accuracy_pct": recent_accuracy,
        "prior_window_days": PRIOR_WINDOW_DAYS,
        "prior_sessions": prior_sessions,
        "prior_items_attempted": prior_attempted,
        "prior_accuracy_pct": prior_accuracy,
        "trend": trend_from_delta(recent_accuracy, prior_accuracy),
        "repeated_practice_families": repeated_families,
        "last_practice_at": last_practice_at,
        "min_sample_note": (
            "Window accuracy/trend fields are null until a window has at least "
            f"{MIN_SESSIONS_FOR_WINDOW_STAT} sessions AND {MIN_ITEMS_FOR_WINDOW_STAT} "
            "items attempted -- avoids a false trend read from one noisy session."
        ),
        "family_accuracy_note": (
            "Per-family accuracy is not reported: practice_results stores one "
            "correct/total pair per whole session, not per-exercise, so a "
            "session spanning multiple families cannot be honestly split by "
            "family. repeated_practice_families reports real exposure counts "
            "(which families keep being served), not accuracy."
        ),
    }


# ---------------------------------------------------------------------------
# Writing Coach
# ---------------------------------------------------------------------------

def build_writing_coach_history(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """rows: mission_results rows as exported by store.ts's missionResultsFor
    ({at, outcome, missionTitle}). Returns None when there is no mission
    history at all."""
    if not rows:
        return None

    now = datetime.now(timezone.utc)
    recent_cutoff = now - timedelta(days=RECENT_WINDOW_DAYS)
    prior_cutoff = now - timedelta(days=RECENT_WINDOW_DAYS + PRIOR_WINDOW_DAYS)

    total = pass_n = partial_n = fail_n = other_n = 0
    recent_total = recent_pass_like = 0
    prior_total = prior_pass_like = 0
    failed_titles: Dict[str, int] = {}
    last_mission_at: Optional[str] = None
    last_mission_dt: Optional[datetime] = None

    for row in rows:
        outcome = row.get("outcome")
        at = row.get("at")
        dt = parse_iso(at)
        total += 1
        if outcome == "pass":
            pass_n += 1
        elif outcome == "partial_pass":
            partial_n += 1
        elif outcome in FAIL_LIKE_OUTCOMES:
            fail_n += 1
            title = row.get("missionTitle")
            if title:
                failed_titles[title] = failed_titles.get(title, 0) + 1
        else:
            other_n += 1

        if dt is not None and (last_mission_dt is None or dt > last_mission_dt):
            last_mission_dt = dt
            last_mission_at = at
        if dt is not None and dt >= recent_cutoff:
            recent_total += 1
            if outcome in PASS_LIKE_OUTCOMES:
                recent_pass_like += 1
        elif dt is not None and prior_cutoff <= dt < recent_cutoff:
            prior_total += 1
            if outcome in PASS_LIKE_OUTCOMES:
                prior_pass_like += 1

    overall_pass_rate = pct(pass_n + partial_n, total) if total >= MIN_MISSIONS_FOR_WINDOW_STAT else None
    recent_pass_rate = pct(recent_pass_like, recent_total) if recent_total >= MIN_MISSIONS_FOR_WINDOW_STAT else None
    prior_pass_rate = pct(prior_pass_like, prior_total) if prior_total >= MIN_MISSIONS_FOR_WINDOW_STAT else None

    top_failed = sorted(failed_titles.items(), key=lambda kv: -kv[1])
    most_common_failed_mission = (
        {"mission_title": top_failed[0][0], "fail_count": top_failed[0][1]} if top_failed else None
    )

    return {
        "total_missions": total,
        "pass_count": pass_n,
        "partial_pass_count": partial_n,
        "fail_count": fail_n,
        "other_outcome_count": other_n,
        "overall_pass_rate_pct": overall_pass_rate,
        "recent_window_days": RECENT_WINDOW_DAYS,
        "recent_missions": recent_total,
        "recent_pass_rate_pct": recent_pass_rate,
        "prior_window_days": PRIOR_WINDOW_DAYS,
        "prior_missions": prior_total,
        "prior_pass_rate_pct": prior_pass_rate,
        "trend": trend_from_delta(recent_pass_rate, prior_pass_rate),
        "most_common_failed_mission": most_common_failed_mission,
        "last_mission_at": last_mission_at,
        "mission_title_coverage_note": (
            "most_common_failed_mission is only computed from rows that carry a "
            "mission_title -- older rows saved before the submit route started "
            "passing the mission title through will have missionTitle=null and "
            "are counted in fail_count but excluded from this breakdown."
        ),
    }


# ---------------------------------------------------------------------------
# Essay Revision
# ---------------------------------------------------------------------------

_STAMP_RE = re.compile(r"_(\d+)\.json$")


def _stamp_from_filename(name: str) -> Optional[int]:
    m = _STAMP_RE.search(name)
    return int(m.group(1)) if m else None


def _ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()


def build_essay_revision_history(session_dir: Optional[str]) -> Optional[Dict[str, Any]]:
    if not session_dir:
        return None
    base = Path(session_dir)
    if not base.exists():
        return None

    comparisons_dir = base / "revision_comparisons"
    recheck_dir = base / "revision_scoped_rechecks"
    has_comparisons = comparisons_dir.exists()
    has_rechecks = recheck_dir.exists()
    if not has_comparisons and not has_rechecks:
        return None

    ai_comparison_out: Optional[Dict[str, Any]] = None
    if has_comparisons:
        files = sorted(
            [f for f in comparisons_dir.glob("comparison_*.json")],
            key=lambda f: _stamp_from_filename(f.name) or 0,
        )
        if files:
            last = read_json(str(files[-1])) or {}
            last_stamp = _stamp_from_filename(files[-1].name)
            ai_comparison_out = {
                "attempts": len(files),
                "used": True,
                "last_generation_status": last.get("generation_status"),
                "last_model_available": bool(last.get("model_available_to_student")),
                "last_attempt_at": _ms_to_iso(last_stamp) if last_stamp else None,
            }

    scoped_recheck_out: Optional[Dict[str, Any]] = None
    if has_rechecks:
        files = sorted(
            [f for f in recheck_dir.glob("recheck_*.json")],
            key=lambda f: _stamp_from_filename(f.name) or 0,
        )
        if files:
            cumulative_rewritten = cumulative_now_free = cumulative_still_has_errors = 0
            cumulative_fixed = cumulative_introduced = 0
            for f in files:
                raw = read_json(str(f)) or {}
                summary = raw.get("summary") or {}
                cumulative_rewritten += int(summary.get("sentences_rewritten") or 0)
                cumulative_now_free += int(summary.get("now_error_free") or 0)
                cumulative_still_has_errors += int(summary.get("still_has_errors") or 0)
                cumulative_fixed += int(summary.get("total_errors_fixed") or 0)
                cumulative_introduced += int(summary.get("total_errors_introduced") or 0)

            last_raw = read_json(str(files[-1])) or {}
            last_summary = last_raw.get("summary") or {}
            last_stamp = _stamp_from_filename(files[-1].name)
            scoped_recheck_out = {
                "attempts": len(files),
                "used": True,
                "cumulative_sentences_rewritten": cumulative_rewritten,
                "cumulative_now_error_free": cumulative_now_free,
                "cumulative_still_has_errors": cumulative_still_has_errors,
                "cumulative_errors_fixed": cumulative_fixed,
                "cumulative_errors_introduced": cumulative_introduced,
                "cumulative_net_fixed": cumulative_fixed - cumulative_introduced,
                "last_attempt": {
                    "sentences_rewritten": last_summary.get("sentences_rewritten"),
                    "now_error_free": last_summary.get("now_error_free"),
                    "still_has_errors": last_summary.get("still_has_errors"),
                    "net_fixed": int(last_summary.get("total_errors_fixed") or 0) - int(last_summary.get("total_errors_introduced") or 0),
                    "honest_summary_text": last_summary.get("honest_summary_text"),
                },
                "last_attempt_at": _ms_to_iso(last_stamp) if last_stamp else None,
            }

    if ai_comparison_out is None and scoped_recheck_out is None:
        return None

    return {
        "ai_comparison": ai_comparison_out or {"attempts": 0, "used": False},
        "scoped_recheck": scoped_recheck_out or {"attempts": 0, "used": False},
    }


# ---------------------------------------------------------------------------

def build(
    practice_mission_export: Optional[Dict[str, Any]],
    family_map: Dict[str, Dict[str, str]],
    session_dir: Optional[str],
    student_id: Optional[str],
) -> Dict[str, Any]:
    export = practice_mission_export or {}
    practice_rows = export.get("practice_results") if isinstance(export.get("practice_results"), list) else []
    mission_rows = export.get("mission_results") if isinstance(export.get("mission_results"), list) else []

    practice_history = build_practice_history(practice_rows, family_map)
    writing_coach_history = build_writing_coach_history(mission_rows)
    essay_revision_history = build_essay_revision_history(session_dir)

    return {
        "schema_version": SCHEMA_VERSION,
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "student_id": student_id or export.get("student_id"),
        "sources": {
            "practice_available": practice_history is not None,
            "writing_coach_available": writing_coach_history is not None,
            "essay_revision_available": essay_revision_history is not None,
        },
        "practice_history": practice_history,
        "writing_coach_history": writing_coach_history,
        "essay_revision_history": essay_revision_history,
        "boundary": (
            "Aggregation only -- no new detection, scoring, feedback, or "
            "coaching. Combines the practice_results/mission_results SQLite "
            "history (exported to JSON by the Node side) and the per-essay "
            "revision artifacts already written by other engines into one "
            "shared shape for the LIE profile builder's --engagement-history "
            "argument."
        ),
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Aggregate Practice + Writing Coach + Essay Revision history into one shared engagement-history artifact.")
    ap.add_argument("--practice-mission-export", help="JSON file with {practice_results:[...], mission_results:[...]} for one student.")
    ap.add_argument("--exercise-bank", help="Path to va_exercise_bank_v11d_approved.jsonl, for exercise_id -> family lookup.")
    ap.add_argument("--session-dir", help="The student's most recent essay's session directory (for revision_comparisons/revision_scoped_rechecks).")
    ap.add_argument("--student-id")
    ap.add_argument("--output", "-o", required=True)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)

    export = read_json(args.practice_mission_export)
    family_map = load_exercise_family_map(args.exercise_bank)
    out = build(export if isinstance(export, dict) else None, family_map, args.session_dir, args.student_id)
    write_json(args.output, out, pretty=args.pretty)
    print(
        f"[gold_engagement_history_aggregator] wrote {args.output} "
        f"(practice_available={out['sources']['practice_available']}, "
        f"writing_coach_available={out['sources']['writing_coach_available']}, "
        f"essay_revision_available={out['sources']['essay_revision_available']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
