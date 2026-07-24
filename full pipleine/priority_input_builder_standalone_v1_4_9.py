#!/usr/bin/env python3
"""
VA / ST.ELLA Priority Input Builder v1.4.9
==========================================

Extends v1.4.8 with the cross-engine signal wiring described in
Pipeline_Frontend_Spec_v2.docx section 6 and LRET_v2_Spec.docx section 5.3:
today the bias between LRET and Vocabulary Coach only runs one direction
(LRET's family tally biases what Vocabulary Coach selects next). The
reverse -- LRET's own learning_intelligence_payload skill signals AND
Vocabulary Coach's own PEEL contextual-fit verdicts (used_correctly /
used_but_awkward / attempted_incorrectly / not_used / needs_review, from
vocab_coach_response_grader_v1_1.py's grade()) -- did not feed Priority
Engine at all. Confirmed directly: v1.4.8 never referenced LRET or the
Vocabulary Coach ledger anywhere.

This version adds two new OPTIONAL inputs:

  --lret <path>
      Accepts EITHER shape, tried in this order (both real, both checked
      directly against real files in this project):
        1. A raw LRET session artifact (07d_lret_session.json), read at
           learning_intelligence_payload.skill_signals -- confirmed against
           a real populated file at
           gold_web_sessions/student_a6b7ca6d-2fc3-4eb3-9bca-0d32533038b1/.../07d_lret_session.json.
        2. A gold_session_continuity_loader_v1.py "prior_context" artifact,
           read at prior_learner_profile.lexical_skill_signals -- the shape
           produced once gold_lie_profile_builder_standalone_v1_4_6.py's new
           lexical_skill_signals field has been persisted and reloaded for a
           later essay.
      Why two shapes instead of one: this run's OWN lret_session artifact
      does not exist yet at the point priority_input runs. Checked directly
      in gold_full_pipeline_orchestrator_v1_4_9.py's STAGE_ORDER:
      "priority_input" is stage 16, "lret_session" is stage 22 -- this
      essay's own LRET pass has not happened yet when priority_input builds
      its input. LRET can therefore only inform THIS essay's priority
      computation via the *previous* essay's signal, carried forward the
      same way directive_adapter_cli already receives continuity via
      --learner-profile {prior_context}. The real orchestrator wiring (see
      gold_engine_commands_full_v1_4_20.json) passes {prior_context}, which
      is already produced at stage 1 -- well before priority_input runs.
      Shape 1 is kept for direct/manual/offline use and tests.

  --vocab-ledger <path>
      The student's persistent Vocabulary Coach ledger
      ({student_id}_vocab_coach_ledger.json, written by
      vocab_coach_ledger_update_v1_1.py). Unlike LRET, this has no ordering
      problem: the ledger is a standing, cross-session artifact updated on
      its own cooldown-gated cadence outside the per-essay orchestrator run
      (confirmed: vocab_coach_selection/vocab_coach_grading/
      vocab_coach_ledger_update are not in STAGE_ORDER at all), so whatever
      is on disk when priority_input runs already reflects everything prior
      to this moment.

Both are additive and optional -- a run without --lret/--vocab-ledger
behaves exactly like v1.4.8.

AGGREGATION DESIGN (read this before changing thresholds):

  Family-level, not per-word. Per LRET_v2_Spec.docx section 5.3 and
  Pipeline_Frontend_Spec_v2.docx section 6, both signal sources are folded
  into a shared, coarse "lexical family" axis that is the only one both
  sources can actually support without inventing new taxonomy:
    - "single_word"           <- LRET skill_id single_word_control
                                 <- vocab ledger items whose phrase is one token
    - "collocation_phrase"    <- LRET skill_id collocation_control (+
                                 phrase_level_paraphrase_opportunity)
                                 <- vocab ledger items whose phrase is 2+ tokens
    - "overall_lexical_control" <- LRET lexical_repair_need (need) /
                                 positive_lexical_control (strength) -- these
                                 two are complementary halves of the same
                                 fix-vs-keep ratio in LRET's own engine code
                                 (lret_engine_v1_13_1_enhance_fail_closed.py,
                                 build_learning_intelligence_payload()), so
                                 they share one family rather than two.
    - "meaning_clarity"       <- LRET lexical_meaning_clarification_need
                                 (need only; no vocab-ledger equivalent --
                                 grading verdicts judge USE, not meaning
                                 confusion, so this family is LRET-only).
  Why single_word / collocation_phrase and not the bank's own item "type"
  tag (collocation / phrasal_verb / noun_phrase / academic_collocation):
  checked vocab_coach_ledger_update_v1_1.py's update_new_item()/
  update_review_item() directly -- the per-item ledger entry only ever
  stores source_bank/topic/subtopic/task_type/angle, never the bank's
  "type" field. Token-count is the only family signal actually persisted in
  the ledger, and it is also exactly how LRET's own engine already buckets
  its KEEP evidence into single-word vs. phrase/collocation counts
  (keep_words = len(surface_tokens(unit_text)) == 1) -- reusing the same
  rule keeps both sources on one consistent axis without adding a ledger
  schema change (which would require touching
  vocab_coach_ledger_update_v1_1.py, out of scope here).

  needs_review is excluded ENTIRELY from aggregation (never counted toward
  attempts, never counted toward needs_work_score) -- it means "the grader
  could not verify this," not "the student got it wrong." A
  needs_review_excluded_count is still reported per family so the exclusion
  is visible/auditable, not silent.

  Verdict weights (needs_work_score, only from non-excluded verdicts):
    attempted_incorrectly = 2.0  -- a real gap: wrong meaning/nonsensical use.
    used_but_awkward      = 1.0  -- partial competence: meaning basically
                                    right, phrasing/register off. Counted,
                                    but at half the weight of a genuine
                                    misuse, per this task's explicit
                                    instruction that the two must not be
                                    treated the same.
    not_used               = 0.5 -- weakest signal of the three: the student
                                    never attempted the item at all, which
                                    could be an error-avoidance strategy but
                                    could equally be paragraph brevity/topic
                                    drift. Given the least benefit of the
                                    doubt of the "needs work" verdicts, but
                                    still counted (silently ignoring it would
                                    hide a real avoidance pattern).
    used_correctly         = 0.0 -- no needs_work contribution; counted
                                    toward correct_count instead.
  needs_work_ratio = needs_work_score / attempts_considered (attempts_considered
  excludes needs_review). status = "insufficient_data" (0 attempts),
  "functional" (ratio == 0), "monitor" (0 < ratio < 1.0), "priority_gap"
  (ratio >= 1.0 -- i.e. average severity is at least "every attempt was
  awkward" or worse).

  Recency: only ledger history entries with session_index >=
  (sessions_completed - --vocab-recent-sessions, default 5) are considered,
  per this task's explicit ask for "how often has this student been
  used_but_awkward vs attempted_incorrectly RECENTLY" rather than
  all-time history.

CONSUMPTION -- verified, not assumed:
  Positive/strength items (family strength >= threshold, or vocab status ==
  "functional" with >=3 recent attempts) are appended to
  evaluator_payload.strengths_profile, the SAME field v1.4.8 already
  populates from Evaluator. This is a genuinely-consumed path: Priority
  Engine's extract_strengths_profile() reads it, and strengths() puts each
  item into its own "strengths" output using the item's "skill" field --
  which v1.4.8's Evaluator-derived items never actually set (they only set
  "skill_id", so strengths() always fell back to its "LEXICAL_CONTROL"
  default regardless of actual domain -- a latent gap noted here, not fixed,
  since it is pre-existing v1.4.8 behavior, not something this version
  introduces). This version's OWN items set "skill" explicitly, mapped to
  real skill tokens Priority Engine's reason_for_skill()/generic_practice()
  already recognize (LEXICAL_FORM_CONTROL, COLLOCATION_CONTROL,
  LEXICAL_CONTROL, SEMANTIC_PHRASE_CONTROL).

  The full family-level need/strength/opportunity picture -- not just the
  strengths -- is additionally written to a new
  rec["lexical_coach_signal"] section for transparency, audit, and so a
  future Priority Engine version has a stable, already-aggregated place to
  read from.

  KNOWN, VERIFIED LIMITATION (do not assume otherwise): Priority Engine's own
  raw output (skill_profiles, strengths, primary_limiter, etc. -- including
  whatever this file feeds into evaluator_payload.strengths_profile) is NOT
  what "focus_areas" downstream (Directive / Writing Coach mission selection
  / LIE's next_best_action) is built from. Checked directly in
  priority_output_normalizer_standalone.py's build(): focus_areas is built
  exclusively from build_focus_from_errormap(errormap) -- the raw
  {priority} artifact (this file's real downstream consumer) is only used
  for a boolean "is it usable" check, never merged into focus_areas. So the
  lexical signal wired here IS genuinely consumed by Priority Engine itself
  (verified: it changes Priority Engine's own strengths/skill_profiles
  output), but does NOT yet change what recommended_service Directive/Writing
  Coach/LIE pick -- that would require its own change to
  priority_output_normalizer_standalone.py, which is a separate, currently
  unwired gap this task did not ask this file to close, and which this file
  does not silently claim to have closed.

Boundary (unchanged from v1.4.8):
- This file does not infer new priorities.
- This file does not score, detect, teach, generate exercises, or modify row
  classifications.
- It only prepares a stable input contract for the existing Priority Engine.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ENGINE_ID = "VA_STELLA_PRIORITY_INPUT_BUILDER"
ENGINE_VERSION = "1.4.9-adds-lret-and-vocab-coach-lexical-signal"
SCHEMA_VERSION = "PRIORITY_INPUT_BRIDGE_V1_4_9"

# Recoverability categories, as produced by Evaluator's per-sentence
# sentence_control assessment, mapped to a 0..1 scale for aggregation only.
_RECOVERABILITY_SCALE = {
    "full": 1.0,
    "partial": 0.5,
    "low": 0.0,
    "blocked": 0.0,
}

# --- v1.4.9: LRET / Vocabulary Coach lexical signal wiring --------------------

# Maps LRET learning_intelligence_payload.skill_signals[*].skill_id to
# (family, direction). direction is "need" (higher score = more work
# needed), "strength" (higher score = more competence), or "opportunity"
# (neither a gap nor a demonstrated strength -- informational only).
# Unknown/future skill_ids fall back to ("other_lexical", "opportunity") so
# a new LRET skill signal never crashes this file or gets silently dropped.
LRET_SKILL_FAMILY = {
    "lexical_repair_need": ("overall_lexical_control", "need"),
    "positive_lexical_control": ("overall_lexical_control", "strength"),
    "collocation_control": ("collocation_phrase", "strength"),
    "single_word_control": ("single_word", "strength"),
    "lexical_meaning_clarification_need": ("meaning_clarity", "need"),
    "phrase_level_paraphrase_opportunity": ("collocation_phrase", "opportunity"),
}

# Maps a family to a skill token Priority Engine already recognizes in
# reason_for_skill()/generic_practice() (LEXICAL_CONTROL, LEXICAL_PRECISION,
# COLLOCATION_CONTROL, LEXICAL_FORM_CONTROL, REGISTER_CONTROL,
# SEMANTIC_PHRASE_CONTROL).
FAMILY_SKILL_TOKEN = {
    "single_word": "LEXICAL_FORM_CONTROL",
    "collocation_phrase": "COLLOCATION_CONTROL",
    "overall_lexical_control": "LEXICAL_CONTROL",
    "meaning_clarity": "SEMANTIC_PHRASE_CONTROL",
    "other_lexical": "LEXICAL_CONTROL",
}

# Vocab Coach grading verdict taxonomy (vocab_coach_response_grader_v1_1.py
# grade() / build_judge_prompt()): used_correctly, used_but_awkward,
# attempted_incorrectly, not_used, needs_review. needs_review means
# "unverified" (no LLM ran, or it failed) -- never a judgment that the
# student got it wrong -- so it is excluded entirely below, not weighted low.
VOCAB_VERDICT_WEIGHTS = {
    "attempted_incorrectly": 2.0,
    "used_but_awkward": 1.0,
    "not_used": 0.5,
    "used_correctly": 0.0,
}
VOCAB_EXCLUDED_VERDICTS = {"needs_review"}

# Strength thresholds (documented, not tuned from data -- there is no
# labeled dataset for this yet; revisit once real usage accumulates).
LRET_STRENGTH_SCORE_THRESHOLD = 0.6
VOCAB_STRENGTH_MIN_ATTEMPTS = 3


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, data: Any, pretty: bool = False) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2 if pretty else None)
        f.write("\n")


def extract_submission_record(submission):
    if not isinstance(submission, dict):
        return {}
    essays = submission.get("essays")
    if isinstance(essays, list) and essays and isinstance(essays[0], dict):
        return essays[0]
    return submission


def first_result(obj):
    if isinstance(obj.get("results"), list) and obj["results"] and isinstance(obj["results"][0], dict):
        return obj["results"][0]
    return obj


def as_results(obj):
    if isinstance(obj.get("results"), list):
        return [x for x in obj["results"] if isinstance(x, dict)]
    return [obj] if isinstance(obj, dict) else []


def score_profile_by_id(scorer):
    out = {}
    if not isinstance(scorer, dict):
        return out
    results = scorer.get("results") if isinstance(scorer.get("results"), list) else [scorer]
    for idx, r in enumerate(results):
        if not isinstance(r, dict):
            continue
        eid = str(r.get("essay_id") or ((r.get("identity") or {}).get("essay_id")) or idx + 1)
        out[eid] = r
    return out


def _strengths_profile_from_evaluator(evaluator):
    """Map Evaluator's writing_coach_payload.current_strength_signals into the
    flat strengths_profile shape Priority Engine's extract_strengths_profile()
    already looks for at evaluator_payload.strengths_profile."""
    payloads = (evaluator.get("consumer_payloads") or {})
    signals = (payloads.get("writing_coach_payload") or {}).get("current_strength_signals") or []
    out = []
    for s in signals:
        if not isinstance(s, dict):
            continue
        out.append({
            "skill_id": s.get("skill_id"),
            "skill_name": s.get("skill_name"),
            "domain": s.get("domain"),
            "status": s.get("status"),
            "confidence": s.get("diagnostic_confidence"),
            "priority_index": s.get("priority_index"),
            "source": "evaluator_skill_observation_profile",
        })
    return out


def _semantic_summary_from_evaluator(evaluator):
    """Aggregate Evaluator's per-sentence sentence_control assessments (each
    carrying a categorical semantic_recoverability value) into the aggregate
    shape Priority Engine's extract_semantic() already looks for at
    layer0_5_semantic_recoverability.semantic_summary. This is arithmetic
    aggregation over Evaluator's own existing per-sentence output, not a new
    diagnosis."""
    payloads = (evaluator.get("consumer_payloads") or {})
    ercp = payloads.get("essay_revision_control_payload") or {}
    sentence_control = ercp.get("sentence_control") or []
    if not isinstance(sentence_control, list) or not sentence_control:
        return {}

    recov_values = []
    blocked = 0
    limited = 0
    affected = 0
    for s in sentence_control:
        if not isinstance(s, dict):
            continue
        recov_cat = str(s.get("semantic_recoverability") or "").lower()
        if recov_cat in _RECOVERABILITY_SCALE:
            recov_values.append(_RECOVERABILITY_SCALE[recov_cat])
        if recov_cat in ("low", "blocked"):
            blocked += 1
        if recov_cat == "partial":
            limited += 1
        status = str(s.get("language_control_status") or "").lower()
        if status in ("yellow", "red"):
            affected += 1

    n = len(sentence_control)
    mean_recoverability = round(sum(recov_values) / len(recov_values), 3) if recov_values else None

    return {
        "mean_recoverability": mean_recoverability,
        "blocked_sentence_count": blocked,
        "limited_sentence_count": limited,
        "affected_discourse_ratio": round(affected / n, 3) if n else None,
        "sentence_count": n,
        "sentence_assessments": sentence_control,
        "source": "evaluator_essay_revision_control_payload.sentence_control (aggregated)",
    }


def _lret_skill_signals(lret):
    """Defensively pull skill_signals out of either a raw LRET session
    artifact or a prior_context artifact (see module docstring for why both
    shapes are supported). Returns (signals, source_label)."""
    if not isinstance(lret, dict) or not lret:
        return [], None
    raw_signals = ((lret.get("learning_intelligence_payload") or {}).get("skill_signals"))
    if isinstance(raw_signals, list) and raw_signals:
        return [s for s in raw_signals if isinstance(s, dict)], "lret_session.learning_intelligence_payload.skill_signals"
    prior_signals = (((lret.get("prior_learner_profile") or {}).get("lexical_skill_signals")))
    if isinstance(prior_signals, list) and prior_signals:
        return [s for s in prior_signals if isinstance(s, dict)], "prior_context.prior_learner_profile.lexical_skill_signals"
    return [], None


def _aggregate_lret_family_signals(skill_signals):
    families = {}
    for s in skill_signals:
        skill_id = str(s.get("skill_id") or "")
        family, direction = LRET_SKILL_FAMILY.get(skill_id, ("other_lexical", "opportunity"))
        entry = families.setdefault(family, {"need_signals": [], "strength_signals": [], "opportunity_signals": []})
        row = {
            "skill_id": skill_id,
            "skill_name": s.get("skill_name"),
            "score": s.get("score"),
            "confidence": s.get("confidence"),
            "evidence_count": s.get("evidence_count"),
            "status": s.get("status"),
        }
        bucket_key = {"need": "need_signals", "strength": "strength_signals"}.get(direction, "opportunity_signals")
        entry[bucket_key].append(row)

    out = {}
    for fam, data in families.items():
        need_scores = [x["score"] for x in data["need_signals"] if isinstance(x.get("score"), (int, float))]
        strength_scores = [x["score"] for x in data["strength_signals"] if isinstance(x.get("score"), (int, float))]
        out[fam] = {
            **data,
            "mean_need_score": round(sum(need_scores) / len(need_scores), 3) if need_scores else None,
            "mean_strength_score": round(sum(strength_scores) / len(strength_scores), 3) if strength_scores else None,
        }
    return out


def _phrase_family(phrase):
    """Token-count heuristic. See module docstring for why this is used
    instead of the vocab bank's own item "type" tag (not persisted in the
    ledger) -- and note it mirrors LRET's own single-word-vs-phrase rule."""
    tokens = [t for t in re.split(r"\s+", str(phrase or "").strip()) if t]
    return "single_word" if len(tokens) <= 1 else "collocation_phrase"


def _aggregate_vocab_ledger_family_signals(ledger, recent_sessions_window):
    if not isinstance(ledger, dict):
        return {}
    items = ledger.get("items") if isinstance(ledger.get("items"), dict) else {}
    sessions_completed = int(ledger.get("sessions_completed") or 0)
    cutoff = max(0, sessions_completed - max(0, recent_sessions_window))

    families = {}
    for phrase, entry in items.items():
        if not isinstance(entry, dict):
            continue
        fam = _phrase_family(phrase)
        fam_data = families.setdefault(fam, {
            "correct_count": 0,
            "used_but_awkward_count": 0,
            "attempted_incorrectly_count": 0,
            "not_used_count": 0,
            "needs_review_excluded_count": 0,
            "needs_work_score": 0.0,
            "_phrases_considered": set(),
        })
        history = entry.get("history") if isinstance(entry.get("history"), list) else []
        for h in history:
            if not isinstance(h, dict):
                continue
            sess_idx = h.get("session_index")
            if isinstance(sess_idx, int) and sess_idx < cutoff:
                continue  # outside the recency window
            verdict = h.get("verdict")
            if verdict in VOCAB_EXCLUDED_VERDICTS:
                fam_data["needs_review_excluded_count"] += 1
                continue
            weight = VOCAB_VERDICT_WEIGHTS.get(verdict)
            if weight is None:
                continue  # unknown/future verdict value: ignored defensively, not counted either way
            fam_data["_phrases_considered"].add(phrase)
            fam_data["needs_work_score"] += weight
            if verdict == "used_correctly":
                fam_data["correct_count"] += 1
            elif verdict == "used_but_awkward":
                fam_data["used_but_awkward_count"] += 1
            elif verdict == "attempted_incorrectly":
                fam_data["attempted_incorrectly_count"] += 1
            elif verdict == "not_used":
                fam_data["not_used_count"] += 1

    out = {}
    for fam, d in families.items():
        attempts = d["correct_count"] + d["used_but_awkward_count"] + d["attempted_incorrectly_count"] + d["not_used_count"]
        needs_work_ratio = round(d["needs_work_score"] / attempts, 3) if attempts else None
        if attempts == 0:
            status = "insufficient_data"
        elif needs_work_ratio == 0:
            status = "functional"
        elif needs_work_ratio >= 1.0:
            status = "priority_gap"
        else:
            status = "monitor"
        out[fam] = {
            "correct_count": d["correct_count"],
            "used_but_awkward_count": d["used_but_awkward_count"],
            "attempted_incorrectly_count": d["attempted_incorrectly_count"],
            "not_used_count": d["not_used_count"],
            "needs_review_excluded_count": d["needs_review_excluded_count"],
            "attempts_considered": attempts,
            "needs_work_score": round(d["needs_work_score"], 3),
            "needs_work_ratio": needs_work_ratio,
            "status": status,
            "distinct_phrases_considered": len(d["_phrases_considered"]),
        }
    return out


def _build_lexical_coach_signal(lret, vocab_ledger, recent_sessions_window):
    skill_signals, lret_source = _lret_skill_signals(lret)
    lret_families = _aggregate_lret_family_signals(skill_signals) if skill_signals else {}
    vocab_families = _aggregate_vocab_ledger_family_signals(vocab_ledger, recent_sessions_window) if isinstance(vocab_ledger, dict) else {}

    all_families = sorted(set(lret_families) | set(vocab_families))
    families_out = {}
    for fam in all_families:
        lret_fam = lret_families.get(fam)
        vocab_fam = vocab_families.get(fam)
        priority_flag = (
            (isinstance(lret_fam, dict) and (lret_fam.get("mean_need_score") or 0) >= 0.5)
            or (isinstance(vocab_fam, dict) and vocab_fam.get("status") == "priority_gap")
        )
        families_out[fam] = {
            "lret": lret_fam,
            "vocab_coach": vocab_fam,
            "combined_priority_flag": bool(priority_flag),
        }

    return {
        "engine_note": "Family-level aggregation of LRET's learning_intelligence_payload skill_signals and the Vocabulary Coach ledger's recent verdict history. needs_review is excluded entirely (see engine docstring); used_but_awkward and attempted_incorrectly are weighted differently, not equally.",
        "lret_input_supplied": bool(skill_signals),
        "lret_signal_source": lret_source,
        "vocab_ledger_input_supplied": isinstance(vocab_ledger, dict) and bool(vocab_ledger),
        "vocab_recent_sessions_window": recent_sessions_window,
        "families": families_out,
    }


def _strengths_from_lexical_signal(lexical_signal):
    out = []
    for fam, data in (lexical_signal.get("families") or {}).items():
        lret_fam = data.get("lret") or {}
        vocab_fam = data.get("vocab_coach") or {}
        mean_strength = lret_fam.get("mean_strength_score")
        lret_is_strength = isinstance(mean_strength, (int, float)) and mean_strength >= LRET_STRENGTH_SCORE_THRESHOLD
        vocab_is_strength = (
            vocab_fam.get("status") == "functional"
            and int(vocab_fam.get("attempts_considered") or 0) >= VOCAB_STRENGTH_MIN_ATTEMPTS
        )
        if not (lret_is_strength or vocab_is_strength):
            continue
        confidence = "high" if (lret_is_strength and vocab_is_strength) else "medium"
        evidence_bits = []
        if lret_is_strength:
            evidence_bits.append("LRET mean_strength_score=" + str(mean_strength))
        if vocab_is_strength:
            evidence_bits.append("vocab ledger " + str(vocab_fam.get("correct_count")) + "/" + str(vocab_fam.get("attempts_considered")) + " recent used_correctly")
        out.append({
            "skill_id": "lexical_family_" + fam,
            "skill_name": fam.replace("_", " ").title() + " (lexical)",
            "skill": FAMILY_SKILL_TOKEN.get(fam, "LEXICAL_CONTROL"),
            "domain": "lexical_resource",
            "status": "functional",
            "confidence": confidence,
            "priority_index": None,
            "source": "lret_vocab_coach_lexical_signal",
            "evidence": "; ".join(evidence_bits),
        })
    return out


def build_priority_input(detector, submission, scorer=None, evaluator=None, lret=None, vocab_ledger=None, vocab_recent_sessions_window=5):
    out = copy.deepcopy(detector)
    submission_record = extract_submission_record(submission)
    scorer_by_id = score_profile_by_id(scorer)
    prompt_text = submission_record.get("prompt_text") or submission_record.get("prompt") or ""
    essay_text = submission_record.get("essay_text") or submission_record.get("text") or ""
    task_type = submission_record.get("task_type") or "WT2"

    strengths_profile = []
    semantic_summary = {}
    evaluator_available = isinstance(evaluator, dict) and bool(evaluator)
    if evaluator_available:
        strengths_profile = _strengths_profile_from_evaluator(evaluator)
        semantic_summary = _semantic_summary_from_evaluator(evaluator)

    # v1.4.9: LRET + Vocabulary Coach lexical signal (additive; both inputs optional).
    lexical_signal = _build_lexical_coach_signal(lret, vocab_ledger, vocab_recent_sessions_window)
    lexical_signal_available = bool(lexical_signal.get("lret_input_supplied") or lexical_signal.get("vocab_ledger_input_supplied"))
    lexical_strengths = _strengths_from_lexical_signal(lexical_signal) if lexical_signal_available else []
    if lexical_strengths:
        # v1.4.9 fix (found during verification, not assumed): Priority Engine's
        # strengths() only processes essay.strengths_profile[:5] (see
        # priority_engine_v4_4_selfcontained.py). Evaluator alone already
        # contributes up to 5+ items on a real essay (confirmed: 8 on the
        # real test essay used to verify this file), so appending the new
        # lexical items AFTER them meant they never survived the slice --
        # present in the JSON, but never actually reaching Priority Engine's
        # own "strengths" output. Prepending them instead means they are
        # never silently dropped by that pre-existing slice.
        strengths_profile = lexical_strengths + strengths_profile

    records = as_results(out)
    quality_records = []
    for idx, rec in enumerate(records):
        eid = str(rec.get("essay_id") or ((rec.get("identity") or {}).get("essay_id")) or idx + 1)
        rec["essay_id"] = eid
        rec["student_id"] = rec.get("student_id") or submission_record.get("student_id")
        rec["task_type"] = rec.get("task_type") or task_type
        rec["prompt_text"] = rec.get("prompt_text") or prompt_text
        rec["essay_text"] = rec.get("essay_text") or essay_text
        rec["intake_record"] = {
            "prompt_text": rec.get("prompt_text") or "",
            "essay_text": rec.get("essay_text") or "",
            "task_type": rec.get("task_type") or task_type,
        }
        rec["task_profile"] = {**(rec.get("task_profile") or {}), "task_type": rec.get("task_type") or task_type, "prompt_present": bool(rec.get("prompt_text")), "score_ready": True}
        rec["meta"] = {**(rec.get("meta") or {}), "prompt_present": bool(rec.get("prompt_text")), "task_type": rec.get("task_type") or task_type}

        # Priority Engine prefers student_rows before scorer_payload rows. Make sure
        # this path contains canonical rows produced by the v1.4.7 evidence guard.
        sp = rec.get("scorer_payload") if isinstance(rec.get("scorer_payload"), dict) else {}
        chargeable = sp.get("chargeable_detector_rows") if isinstance(sp.get("chargeable_detector_rows"), list) else []
        review = sp.get("review_only_detector_rows") if isinstance(sp.get("review_only_detector_rows"), list) else []
        if chargeable:
            rec["student_rows"] = chargeable + review
            rec["all_rows"] = chargeable + review

        scorer_record = scorer_by_id.get(eid)
        if scorer_record:
            # Duplicate the fields that Priority Engine can read even if --scorer
            # merge behavior changes in a future version.
            if "score_profile" in scorer_record:
                rec["score_profile"] = scorer_record["score_profile"]
            if "rubric_impact_map" in scorer_record:
                rec["rubric_impact_map"] = scorer_record["rubric_impact_map"]
            if "score_explanation_payload" in scorer_record:
                rec["score_explanation_payload"] = scorer_record["score_explanation_payload"]

        # v1.4.8: map Evaluator's output into the paths Priority Engine already
        # knows how to read. Both are additive -- absent if --evaluator wasn't
        # supplied, so v1.4.7 behavior is preserved exactly when it isn't.
        if evaluator_available:
            rec["evaluator_payload"] = {
                **(rec.get("evaluator_payload") or {}),
                "strengths_profile": strengths_profile,
            }
            if semantic_summary:
                rec["layer0_5_semantic_recoverability"] = {
                    **(rec.get("layer0_5_semantic_recoverability") or {}),
                    "semantic_summary": semantic_summary,
                }
            rec["evaluator_available"] = True
        elif lexical_strengths:
            # v1.4.9: even with no Evaluator input, the LRET/vocab-coach
            # lexical strengths should still reach the same consumed path.
            rec["evaluator_payload"] = {
                **(rec.get("evaluator_payload") or {}),
                "strengths_profile": strengths_profile,
            }
            rec["evaluator_available"] = False
        else:
            rec["evaluator_available"] = False

        # v1.4.9: additive lexical_coach_signal section (present regardless of
        # whether it changed evaluator_payload.strengths_profile above -- this
        # is the full need/strength/opportunity picture, not just the
        # strengths subset).
        if lexical_signal_available:
            rec["lexical_coach_signal"] = lexical_signal

        families = sorted({str(r.get("family") or "") for r in rec.get("student_rows", []) if isinstance(r, dict)})
        unknown_count = sum(1 for r in rec.get("student_rows", []) if isinstance(r, dict) and str(r.get("family") or "").upper().startswith("UNKNOWN"))
        quality_records.append({
            "essay_id": eid,
            "row_count": len(rec.get("student_rows", []) if isinstance(rec.get("student_rows"), list) else []),
            "unique_family_count": len([f for f in families if f]),
            "unknown_family_count": unknown_count,
            "task_type": rec.get("task_type"),
            "prompt_present": bool(rec.get("prompt_text")),
            "evaluator_available": rec["evaluator_available"],
            "lexical_coach_signal_available": lexical_signal_available,
            "status": "ok" if rec.get("task_type") and rec.get("prompt_text") and unknown_count == 0 else "needs_attention",
        })

    out["schema_version"] = SCHEMA_VERSION
    out["source_detector_schema_version"] = detector.get("schema_version")
    out["priority_input_builder"] = {
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "boundary": "Input-contract normalization only; Priority Engine still owns priority inference.",
        "evaluator_input_supplied": evaluator_available,
        "lexical_coach_signal_supplied": lexical_signal_available,
        "records": quality_records,
        "all_records_priority_ready": all(r["status"] == "ok" for r in quality_records),
    }
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build Priority Engine input v1.4.9.")
    ap.add_argument("--detector", required=True, help="Guarded detector JSON.")
    ap.add_argument("--submission", required=True, help="Normalized submission JSON.")
    ap.add_argument("--scorer", required=False, help="Optional scorer output JSON.")
    ap.add_argument("--evaluator", required=False, help="Optional Evaluator/WKE output JSON (v8.3+).")
    ap.add_argument("--lret", required=False, help="Optional LRET session artifact OR prior_context artifact (see module docstring for both accepted shapes).")
    ap.add_argument("--vocab-ledger", required=False, help="Optional Vocabulary Coach ledger JSON ({student_id}_vocab_coach_ledger.json).")
    ap.add_argument("--vocab-recent-sessions", type=int, default=5, help="How many most-recent ledger sessions to consider per item history (default 5).")
    ap.add_argument("--output", "-o", required=True)
    ap.add_argument("--pretty", action="store_true")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args(argv)

    detector = read_json(args.detector)
    submission = read_json(args.submission)
    scorer = read_json(args.scorer) if args.scorer else None
    evaluator = read_json(args.evaluator) if args.evaluator else None
    lret = read_json(args.lret) if args.lret else None
    vocab_ledger = read_json(args.vocab_ledger) if args.vocab_ledger else None
    out = build_priority_input(detector, submission, scorer, evaluator, lret, vocab_ledger, args.vocab_recent_sessions)
    if args.strict and not (out.get("priority_input_builder") or {}).get("all_records_priority_ready"):
        raise SystemExit("Priority input is not ready.")
    write_json(args.output, out, pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
