#!/usr/bin/env python3
"""
Gold LIE Profile Builder v1.4.7 -- standalone
==============================================

Aggregates completed Gold artifacts into a learner-profile snapshot and simple
progress views. Imports no previous versions.

CHANGE FROM v1.4.6 (additive only -- no existing field, arg, or computation
removed or altered):

  Product-owner decision, verbatim: Practice and Writing Coach were only ever
  fed to this file as artifact_status() presence checks ({available,
  schema_version, engine_id} -- see --practice/--writing-coach below, both
  unchanged) -- zero actual performance/score/completion data was ever
  extracted from them, and Essay Revision was not an LIE input at all. PO's
  explicit call: "all 3 should!!!" feed real learned history, and the
  continuous-loop refresh path (gold_engine_...refreshLearnerProfile() in
  goldPipeline.ts, called after Practice/Writing Coach/Vocabulary Coach/Essay
  Revision activity between essays) needs a real history signal to refresh
  with, not just this-essay's on-demand artifact presence.

  This version adds exactly one new, optional, additive argument:
  --engagement-history, pointing at
  gold_engagement_history_aggregator_v1_0.py's output (real aggregated
  Practice/Writing-Coach/Essay-Revision history -- session-level accuracy
  trend + family repetition counts for Practice, pass-rate trend + most
  commonly failed mission for Writing Coach, AI-comparison/scoped-recheck
  usage + net-fixed-sentence counts for Essay Revision; see that engine's own
  module docstring for the full aggregation logic and thresholds). This file
  does NOT re-derive any of those numbers itself -- engagement_history_summary()
  below only re-shapes the aggregator's already-computed fields into the
  profile, the same "fold an already-aggregated artifact into the profile"
  role vocabulary_coach_summary() already plays for the vocab ledger.
  --practice and --writing-coach (this essay's own on-demand session
  artifacts, 07f_gold_practice_session.json / 07e_writing_coach_output.json)
  are UNCHANGED -- they still feed service_artifact_status's presence check
  only, since that's a genuinely different, still-useful signal ("did this
  essay's own on-demand practice/coaching session get generated") from
  --engagement-history's cross-session real performance history. Both are
  additive/optional, so a caller that doesn't supply --engagement-history
  (e.g. the full 27-stage orchestrator's essay-submission run, which is not
  changed by this version -- see gold_engine_commands_full_v1_4_20.json,
  still pinned to v1.4.6) gets engagement_history: null, exactly like any
  other not-yet-supplied optional artifact in this file.

CHANGE FROM v1.4.5 (additive only -- no existing field, arg, or computation
removed or altered):

  This is the cross-engine signal wiring task (see Pipeline_Frontend_Spec_v2.docx
  section 6 and LRET_v2_Spec.docx section 5.3): LRET's learning_intelligence_payload
  and the Vocabulary Coach ledger's PEEL verdict history needed to reach
  Priority Engine (priority_input_builder_standalone_v1_4_9.py), not just LIE.
  Confirmed directly in gold_full_pipeline_orchestrator_v1_4_9.py's
  STAGE_ORDER: "priority_input" (stage 16) runs BEFORE "lret_session" (stage
  22) -- this essay's own LRET pass has not happened yet when priority_input
  builds its input, so priority_input_builder can only use the PREVIOUS
  essay's LRET signal, carried forward the same way directive_adapter_cli
  already receives continuity via --learner-profile {prior_context}
  (gold_session_continuity_loader_v1.py's build_prior_context() already
  embeds the full prior "prior_learner_profile" -- i.e. exactly this file's
  own previous output -- with no changes needed to the continuity loader or
  to gold_profile_persist_v1.py, both of which already pass this file's
  output through unmodified/verbatim, confirmed by reading
  gold_profile_persist_v1.py's build_persisted_profile(): `out =
  copy.deepcopy(learner_profile)`).

  So this version adds exactly one new, narrow, additive field:
  `lexical_skill_signals`, a condensed copy of this essay's
  --lret artifact's learning_intelligence_payload.skill_signals (skill_id,
  score, confidence, evidence_count, status only -- no LRET-internal
  bookkeeping). This is squarely inside this file's existing stated boundary
  ("aggregates completed artifact evidence... preserves routing metadata"):
  it does not change next_best_action, does not decide which service to
  route to, and does not touch the roadmap. It exists purely so that this
  session's LRET signal survives into next session's {prior_context} (via
  the existing, unmodified persist/reload loop above) where
  priority_input_builder_standalone_v1_4_9.py's --lret argument can then
  read it as prior_learner_profile.lexical_skill_signals.

  Deliberately NOT changed: next_best_action_from(). Read directly: it picks
  between directive.gold_learning_directive (first choice), then the
  priority-normalized focus_areas' primary entry, then an ErrorMap capacity
  fallback -- i.e. it already always prefers Directive's answer over
  anything computed locally. If Priority Engine/Directive ever start
  weighting LRET/Vocabulary Coach signal into recommended_service, LIE's
  next_best_action benefits automatically, with zero changes needed here,
  because it reads Directive first. Making next_best_action_from() also
  read lexical_skill_signals directly would mean two different engines
  independently deciding "which service is next" from the same signal --
  a real risk of the two disagreeing -- so this version deliberately leaves
  that decision solely with Priority Engine/Directive, consistent with this
  file's own boundary statement ("Does not score, detect, generate
  feedback, classify LRET candidates, coach, or create practice").

Boundary (unchanged):
- Does not score, detect, generate feedback, classify LRET candidates, coach, or create practice.
- Only aggregates completed artifact evidence and preserves routing metadata.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "GOLD_LEARNER_PROFILE_STANDALONE_V1_4_7"
ENGINE_ID = "VA_STELLA_GOLD_LIE_PROFILE_BUILDER"
ENGINE_VERSION = "1.4.7-standalone-no-imports"

VOCAB_BOX_ORDER = ["new", "box_1", "box_2", "box_3", "mastered"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Optional[str], required: bool = True) -> Optional[Dict[str, Any]]:
    if not path:
        if required:
            raise ValueError("missing path")
        return None
    p = Path(path)
    if not p.exists():
        if required:
            raise FileNotFoundError(str(p))
        return None
    obj = json.loads(p.read_text(encoding="utf-8"))
    return obj if isinstance(obj, dict) else {"value": obj}


def write_json(path: Optional[str], data: Any, pretty: bool = False) -> None:
    if not path:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2 if pretty else None) + "\n", encoding="utf-8")


def artifact_status(obj: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "available": isinstance(obj, dict),
        "schema_version": obj.get("schema_version") if isinstance(obj, dict) else None,
        "engine_id": obj.get("engine_id") if isinstance(obj, dict) else None,
    }


def latest_error_profile(errormap: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    counts = errormap.get("counts") if isinstance(errormap, dict) and isinstance(errormap.get("counts"), dict) else {}
    by_capacity = errormap.get("counts_by_capacity") if isinstance(errormap, dict) and isinstance(errormap.get("counts_by_capacity"), dict) else {}
    by_criterion = errormap.get("counts_by_criterion") if isinstance(errormap, dict) and isinstance(errormap.get("counts_by_criterion"), dict) else {}
    total = int((errormap.get("summary") or {}).get("error_count") or sum(counts.values()) if isinstance(errormap, dict) else 0)
    return {"by_family": counts, "by_capacity": by_capacity, "by_criterion": by_criterion, "total_chargeable": total}


def focus_areas(priority: Optional[Dict[str, Any]], directive: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for obj in (directive, priority):
        if isinstance(obj, dict) and isinstance(obj.get("focus_areas"), list) and obj.get("focus_areas"):
            return [x for x in obj["focus_areas"] if isinstance(x, dict)]
    return []


def next_action_from(directive: Optional[Dict[str, Any]], focus: List[Dict[str, Any]], errormap: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if isinstance(directive, dict):
        gld = directive.get("gold_learning_directive") or {}
        if gld.get("next_best_capacity_domain") or gld.get("recommended_service"):
            return {
                "capacity_domain": gld.get("next_best_capacity_domain"),
                "skill_tag": gld.get("next_best_skill"),
                "recommended_service": gld.get("recommended_service"),
                "source": "directive",
            }
    primary = focus[0] if focus else None
    if isinstance(primary, dict):
        return {
            "capacity_domain": primary.get("capacity_domain"),
            "skill_tag": primary.get("skill_tag") or primary.get("skill_id"),
            "recommended_service": primary.get("recommended_service") or "writing_coach",
            "source": "priority_normalized",
        }
    profile = latest_error_profile(errormap)
    bycap = profile.get("by_capacity") or {}
    if bycap:
        cap = max(bycap.items(), key=lambda kv: kv[1])[0]
        return {"capacity_domain": cap, "skill_tag": cap, "recommended_service": "writing_coach", "source": "errormap_fallback"}
    return {"capacity_domain": None, "skill_tag": None, "recommended_service": None, "source": "none"}


def build_skill_progress(error_profile: Dict[str, Any], next_action: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for cap, count in sorted((error_profile.get("by_capacity") or {}).items(), key=lambda kv: (-kv[1], kv[0])):
        service = "writing_coach" if cap in {"sentence_control", "argument_development", "task_response_control"} else "lret" if cap in {"lexical_precision", "academic_style"} else "practice"
        rows.append({
            "capacity_domain": cap,
            "latest_error_count": int(count),
            "status": "priority_gap" if int(count) >= 5 else "monitor",
            "recommended_next_service": next_action.get("recommended_service") if cap == next_action.get("capacity_domain") else service,
        })
    return rows


def lexical_skill_signals_from_lret(lret: Optional[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
    """v1.4.6: condense this essay's LRET learning_intelligence_payload.skill_signals
    into the small shape persisted forward via _continuity/prior_context so a
    LATER essay's priority_input_builder can read it as
    prior_learner_profile.lexical_skill_signals (see module docstring --
    priority_input runs before lret_session within the SAME essay, so this
    essay's own LRET signal can only inform a future essay's priority
    computation, not this one). Returns None (not []) when no --lret was
    supplied or it had no populated skill_signals, so callers can distinguish
    "not tracked yet" from "tracked, empty"."""
    if not isinstance(lret, dict):
        return None
    signals = (lret.get("learning_intelligence_payload") or {}).get("skill_signals")
    if not isinstance(signals, list) or not signals:
        return None
    out = []
    for s in signals:
        if not isinstance(s, dict):
            continue
        out.append({
            "skill_id": s.get("skill_id"),
            "skill_name": s.get("skill_name"),
            "domain_id": s.get("domain_id"),
            "score": s.get("score"),
            "confidence": s.get("confidence"),
            "evidence_count": s.get("evidence_count"),
            "status": s.get("status"),
        })
    return out or None


def vocabulary_coach_summary(vocab_coach: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Unchanged from v1.4.4. Summarizes the Vocabulary Coach ledger (Leitner-box
    counts, sessions completed, a handful of recently-mastered items) into a
    small rollup suitable for the learner profile / progress page. Returns
    None (not an empty dict) when no ledger was provided, so callers can
    distinguish "not tracked yet" from "tracked, zero progress"."""
    if not isinstance(vocab_coach, dict):
        return None
    items = vocab_coach.get("items") or {}
    box_counts = {b: 0 for b in VOCAB_BOX_ORDER}
    recently_mastered = []
    for phrase, entry in items.items():
        box = entry.get("box")
        if box in box_counts:
            box_counts[box] += 1
        if box == "mastered":
            recently_mastered.append({
                "phrase": phrase,
                "topic": entry.get("topic"),
                "subtopic": entry.get("subtopic"),
                "last_seen_session": entry.get("last_seen_session"),
            })
    recently_mastered.sort(key=lambda x: x.get("last_seen_session") or 0, reverse=True)
    total_tracked = sum(box_counts.values())
    return {
        "sessions_completed": vocab_coach.get("sessions_completed", 0),
        "items_by_box": box_counts,
        "total_items_tracked": total_tracked,
        "mastered_count": box_counts.get("mastered", 0),
        "in_active_rotation_count": box_counts.get("new", 0) + box_counts.get("box_1", 0) + box_counts.get("box_2", 0) + box_counts.get("box_3", 0),
        "recently_mastered": recently_mastered[:5],
    }


def engagement_history_summary(engagement: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """v1.4.7: folds gold_engagement_history_aggregator_v1_0.py's already-
    computed practice/writing-coach/essay-revision aggregation into the
    profile. Deliberately thin -- every number here was already computed by
    that engine (session-level accuracy trend + family repetition counts,
    pass-rate trend + most-failed mission, AI-comparison/scoped-recheck usage
    + net-fixed-sentence counts); this function only re-shapes/passes them
    through, the same role vocabulary_coach_summary() plays for the vocab
    ledger. Returns None (not an empty dict) when no --engagement-history was
    supplied, so callers can distinguish "not tracked yet" from "tracked,
    zero history" -- same convention as vocabulary_coach_summary() and
    lexical_skill_signals_from_lret() above."""
    if not isinstance(engagement, dict):
        return None
    practice = engagement.get("practice_history")
    coach = engagement.get("writing_coach_history")
    revision = engagement.get("essay_revision_history")
    if practice is None and coach is None and revision is None:
        return None
    return {
        "practice_history": practice,
        "writing_coach_history": coach,
        "essay_revision_history": revision,
        "source_engine_version": engagement.get("engine_version"),
    }


def build(args: argparse.Namespace) -> Dict[str, Any]:
    submission = read_json(args.submission)
    contract = read_json(args.score_contract)
    errormap = read_json(args.errormap)
    priority = read_json(args.priority, required=False)
    directive = read_json(args.directive, required=False)
    feedback = read_json(args.feedback, required=False)
    evaluator = read_json(args.evaluator, required=False)
    lret = read_json(args.lret, required=False)
    coach = read_json(args.writing_coach, required=False)
    practice = read_json(args.practice, required=False)
    vocab_coach = read_json(args.vocabulary_coach, required=False)
    engagement = read_json(args.engagement_history, required=False)

    error_profile = latest_error_profile(errormap)
    focus = focus_areas(priority, directive)
    next_action = next_action_from(directive, focus, errormap)
    released = contract.get("released_score") or {}
    vocab_summary = vocabulary_coach_summary(vocab_coach)
    lexical_skill_signals = lexical_skill_signals_from_lret(lret)
    engagement_history = engagement_history_summary(engagement)
    profile = {
        "schema_version": SCHEMA_VERSION,
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "student_id": contract.get("student_id") or submission.get("student_id"),
        "latest_essay_id": contract.get("essay_id") or submission.get("essay_id"),
        "latest_score": {
            "overall_band": released.get("overall_band"),
            "criteria_bands": released.get("criteria_bands"),
            "score_confidence": contract.get("score_confidence"),
            "score_status": contract.get("score_status"),
            "progress_tracking_allowed": contract.get("progress_tracking_allowed"),
            "lie_update_allowed": contract.get("lie_update_allowed"),
        },
        "latest_error_profile": error_profile,
        "latest_priority_focus": focus,
        "service_artifact_status": {
            "feedback": artifact_status(feedback),
            "evaluator": artifact_status(evaluator),
            "lret": artifact_status(lret),
            "writing_coach": artifact_status(coach),
            "practice": artifact_status(practice),
            "vocabulary_coach": artifact_status(vocab_coach),
        },
        "vocabulary_coach_summary": vocab_summary,
        # v1.4.6: additive, narrow continuity field -- see module docstring.
        # Consumed downstream by priority_input_builder_standalone_v1_4_9.py's
        # --lret argument (via prior_context.prior_learner_profile), NOT by
        # anything in this file. Does not affect next_best_action below.
        "lexical_skill_signals": lexical_skill_signals,
        # v1.4.7: real cross-session Practice/Writing-Coach/Essay-Revision
        # history (see module docstring + engagement_history_summary() above).
        # Populated only when the caller supplies --engagement-history (the
        # continuous-loop refresh path always does; the full orchestrator's
        # essay-submission run does not yet -- see module docstring). Does not
        # affect next_best_action below, same reasoning as lexical_skill_signals:
        # this file does not want two independent "what should happen next"
        # computations that could disagree with Priority Engine/Directive.
        "engagement_history": engagement_history,
        "next_best_action": next_action,
        "learning_state": {
            "profile_update_allowed": bool(contract.get("lie_update_allowed")),
            "trend_update_allowed": bool(contract.get("progress_tracking_allowed")),
            "mastery_update_policy": "evidence_accumulation_only_until_student_attempts_practice_or_coach_tasks",
        },
        "boundary": "LIE profile aggregates completed Gold artifacts only; no scoring, detection, feedback generation, or exercise generation is performed here.",
    }
    return profile


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build Gold learner profile and progress artifacts.")
    ap.add_argument("--submission", required=True)
    ap.add_argument("--score-contract", required=True)
    ap.add_argument("--errormap", required=True)
    ap.add_argument("--priority")
    ap.add_argument("--directive")
    ap.add_argument("--feedback")
    ap.add_argument("--evaluator")
    ap.add_argument("--lret")
    ap.add_argument("--writing-coach")
    ap.add_argument("--practice")
    ap.add_argument("--vocabulary-coach")
    ap.add_argument("--engagement-history", help="Output of gold_engagement_history_aggregator_v1_0.py -- real cross-session Practice/Writing-Coach/Essay-Revision history (v1.4.7, optional/additive).")
    ap.add_argument("--output", "-o", required=True)
    ap.add_argument("--skills-progress-output")
    ap.add_argument("--learning-roadmap-output")
    ap.add_argument("--progress-snapshot-output")
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)
    profile = build(args)
    write_json(args.output, profile, args.pretty)
    skills = {
        "schema_version": "GOLD_SKILLS_PROGRESS_STANDALONE_V1_4_7",
        "created_at": now_iso(),
        "student_id": profile.get("student_id"),
        "latest_essay_id": profile.get("latest_essay_id"),
        "skills": build_skill_progress(profile.get("latest_error_profile") or {}, profile.get("next_best_action") or {}),
        "vocabulary_coach_summary": profile.get("vocabulary_coach_summary"),
        # v1.4.7: see engagement_history_summary() -- real cross-session
        # Practice/Writing-Coach/Essay-Revision history, null until the
        # continuous-loop refresh path supplies --engagement-history.
        "engagement_history": profile.get("engagement_history"),
        "boundary": "Progress view derived from latest profile evidence only.",
    }
    # v1.4.5: inserted the vocabulary_coach phase (new) between practice and
    # essay_revision, and renumbered essay_revision from phase 3 to phase 4.
    # See module docstring for why -- this was a real, reported gap, not a
    # hypothetical one: Vocabulary Coach never appeared in this sequence
    # before, despite being a live, working Gold-tier feature.
    roadmap = {
        "schema_version": "GOLD_LEARNING_ROADMAP_STANDALONE_V1_4_5",
        "created_at": now_iso(),
        "student_id": profile.get("student_id"),
        "roadmap": [
            {"phase": 1, "focus": (profile.get("next_best_action") or {}).get("capacity_domain"), "service": (profile.get("next_best_action") or {}).get("recommended_service"), "goal": "Stabilize the highest-priority weakness from the latest essay."},
            {"phase": 2, "focus": "controlled_transfer", "service": "practice", "goal": "Transfer the skill to short controlled tasks."},
            {"phase": 3, "focus": "vocabulary_building", "service": "vocabulary_coach", "goal": "Build topic vocabulary and academic word control through a short PEEL task."},
            {"phase": 4, "focus": "revision", "service": "essay_revision", "goal": "Apply the skill in a revised essay."},
        ],
        "boundary": "Roadmap orders upstream priorities; it does not generate lessons or scores.",
    }
    progress = {
        "schema_version": "GOLD_PROGRESS_SNAPSHOT_STANDALONE_V1_4_7",
        "created_at": now_iso(),
        "student_id": profile.get("student_id"),
        "latest_score": profile.get("latest_score"),
        "next_best_action": profile.get("next_best_action"),
        "vocabulary_coach_summary": profile.get("vocabulary_coach_summary"),
        # v1.4.7: see engagement_history_summary() -- null until the
        # continuous-loop refresh path supplies --engagement-history.
        "engagement_history": profile.get("engagement_history"),
        "boundary": "Snapshot only; no scoring or mastery update beyond aggregation.",
    }
    write_json(args.skills_progress_output, skills, args.pretty)
    write_json(args.learning_roadmap_output, roadmap, args.pretty)
    write_json(args.progress_snapshot_output, progress, args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
