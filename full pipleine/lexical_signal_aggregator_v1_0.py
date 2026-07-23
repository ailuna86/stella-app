#!/usr/bin/env python3
"""
Lexical Signal Aggregator v1.0 — standalone
============================================

New engine (not a patch to any existing file). Builds one shared "lexical
signal" artifact from two real, already-existing signal sources that no
consumer engine currently reads for actual decision-making:

  1. LRET's `learning_intelligence_payload.skill_signals` /
     `.pattern_signals` (07d_lret_session.json, produced by
     lret_engine_v1_13_1_enhance_fail_closed.py). This is the CURRENT
     essay's fresh lexical-repair/collocation/meaning-clarity signal.
  2. Vocabulary Coach's per-item ledger `history` (each entry: `phrase` ->
     {"topic", "subtopic", "history": [{"session_index","verdict","role"}], ...},
     produced by vocab_coach_ledger_update_v1_1.py). This is a rolling,
     multi-session, topic-scoped signal.

Confirmed directly against real files before writing this (not assumed from
the audit paraphrase):
  - Real 07d_lret_session.json
    (gold_web_sessions/student_a6b7ca6d-.../07d_lret_session.json) has
    `learning_intelligence_payload.skill_signals` as a list of
    {skill_id, skill_name, domain_id, score, confidence, evidence_count,
    status} rows, plus `pattern_signals` as a list of
    {"pattern_id": "lexical_fix_family::<FAMILY>", "count": N}. All six
    skill_ids seen in the real sample are under domain_id
    "lexical_resource": lexical_repair_need, phrase_level_paraphrase_
    opportunity, positive_lexical_control, collocation_control,
    single_word_control, lexical_meaning_clarification_need.
  - vocab_coach_ledger_update_v1_1.py's update_new_item/update_review_item
    both append {"session_index", "verdict", "role"} to
    ledger["items"][phrase]["history"] on every session (confirmed reading
    the engine source directly) -- so the FULL per-attempt verdict history
    is available, not just `last_outcome` (the most recent verdict only).
    `topic`/`subtopic` are stored directly on each ledger item (populated
    from the vocab_coach_session rotation/prompt bank metadata), so no join
    against the topic bank is needed to get a topic-level grouping key.
  - vocab_coach_response_grader_v1_1.py's grade() ONLY ever sets verdict to
    "used_correctly"/"used_but_awkward"/"attempted_incorrectly" inside the
    `if llm_result and "per_item" in llm_result` branch -- i.e. only after a
    real LLM semantic check succeeded. Every other path (no --use-llm, no
    API key, call failure, LLM omitted an item) sets "needs_review". So
    "needs_review" reliably means "unverified", never "verified and fine" or
    "verified and wrong" -- excluding it entirely (per
    Pipeline_Frontend_Spec_v2.docx Section 6 / LRET_v2_Spec.docx Section 5.3)
    is not just a policy choice, it is the only sound reading given how the
    verdict is actually produced.

DESIGN NOTE -- why LRET and Vocab Coach are NOT collapsed into one identical
"family" key space:
  LRET's grouping is about the *type of lexical error* (collocation,
  word-form, meaning-clarity -- see pattern_signals' `lexical_fix_family::*`
  values). Vocab Coach's grouping is about *topic content* (environment,
  education, etc. -- see vocab_coach_topic_bank_v1_5_0.json's topics/
  subtopics). These are different axes over the same underlying "lexical
  resource" criterion, not two views of the same key. Forcing them into one
  key space would either lose the error-type detail LRET actually has, or
  invent topic tags LRET doesn't produce. Instead this engine outputs BOTH
  native-grained dicts (`lret_signal` keyed by skill_id, `vocab_coach_signal`
  keyed by topic) AND a `capacity_domain_rollup` that collapses both onto the
  two capacity_domain values the rest of the pipeline already recognizes for
  lexical weakness (lexical_precision, academic_style -- see
  priority_output_normalizer_standalone.py's CAPACITY_TO_SKILL /
  CRITERION_BY_FAMILY_PREFIX and directive_adapter_cli_v1_4_3.py's
  SERVICE_BY_CAPACITY), so consumer engines can compare this new signal
  against their existing capacity_domain-shaped priorities without inventing
  a taxonomy the rest of the system doesn't understand.

JUDGMENT CALLS (documented, not hidden):
  1. LRET skill_signal score direction is NOT uniform. Verified directly:
     "lexical_repair_need"=0.106 (low value, essay had few fix units -- LOW
     weakness) and "lexical_meaning_clarification_need"=1.0 (essay had a
     clarify-heavy ratio -- HIGH weakness) are already framed as "how much
     work is needed", so score IS weakness directly. But
     "positive_lexical_control"=0.872, "collocation_control"=0.439,
     "single_word_control"=0.122 are framed as competency/control levels, so
     a LOW score there means MORE weakness (i.e. inverted vs. the *_need
     skills). Treating all six uniformly as "lower score = more weakness"
     (the audit's paraphrase, given verbatim in the build brief) would
     silently mis-read the two *_need skills backwards -- e.g. it would
     flag a 0.106 lexical_repair_need (a GOOD sign: few repairs needed) as a
     severe weakness. This engine maps *_need / *_opportunity skills
     directly (score IS weakness) and *_control skills inverted
     (weakness = 1 - score), per SKILL_WEAKNESS_DIRECTION below.
  2. Vocab Coach weighting: attempted_incorrectly counts 2x used_but_awkward
     (1.0 vs 0.5) in the weighted-negative numerator, per the spec quote
     ("one is partial competence, the other is a real gap"). used_correctly
     counts as weighted-positive evidence (1.0), lowering the topic's
     weakness score. not_used is excluded (no attempt = no signal either
     way, distinct from needs_review's "unverified").
  3. Dominance gating: a family/topic is only reported as
     `dominant_lexical_weakness` if it clears evidence_count>=3,
     weakness_score>=0.55 AND confidence>=0.5. Below that, this engine still
     reports the raw per-family numbers (for diagnostics) but withholds a
     "dominant" call so a single noisy low-N signal cannot look like a
     confident recommendation to a consumer engine that isn't careful.

Boundary:
- Does not score, detect, generate feedback, coach, or classify LRET/Vocab
  Coach candidates.
- Only aggregates two already-produced artifacts into one shared shape.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "GOLD_LEXICAL_SIGNAL_V1_0"
ENGINE_ID = "VA_STELLA_LEXICAL_SIGNAL_AGGREGATOR"
ENGINE_VERSION = "1.0.0-standalone-no-imports"

# --- LRET skill_signal handling ---------------------------------------

# "direct": score already IS a weakness/need measure (higher score = more weakness).
# "inverse": score is a competency/control measure (higher score = less weakness).
SKILL_WEAKNESS_DIRECTION: Dict[str, str] = {
    "lexical_repair_need": "direct",
    "phrase_level_paraphrase_opportunity": "direct",
    "lexical_meaning_clarification_need": "direct",
    "positive_lexical_control": "inverse",
    "collocation_control": "inverse",
    "single_word_control": "inverse",
}

# Rolls each LRET skill_id onto the capacity_domain vocabulary the rest of
# the pipeline already uses (priority_output_normalizer / directive_adapter).
# "phrase_level_paraphrase_opportunity" is deliberately mapped to
# academic_style, not lexical_precision -- it's an ENHANCE-type opportunity
# (range/sophistication), not an error, so it shouldn't inflate a precision
# "wrongness" score.
SKILL_CAPACITY_DOMAIN: Dict[str, str] = {
    "lexical_repair_need": "lexical_precision",
    "collocation_control": "lexical_precision",
    "single_word_control": "lexical_precision",
    "lexical_meaning_clarification_need": "lexical_precision",
    "positive_lexical_control": "lexical_precision",
    "phrase_level_paraphrase_opportunity": "academic_style",
}

# --- Vocab Coach verdict weighting --------------------------------------

VOCAB_VERDICT_NEGATIVE_WEIGHT = {
    "attempted_incorrectly": 1.0,   # a real gap
    "used_but_awkward": 0.5,        # partial competence
}
VOCAB_VERDICT_POSITIVE_WEIGHT = {
    "used_correctly": 1.0,
}
VOCAB_EXCLUDED_VERDICTS = {"needs_review", "not_used"}
# needs_review: unverified (never counted, per spec quote -- see module docstring).
# not_used: no attempt was made, so there is no performance evidence either way.

# Every used_correctly/used_but_awkward/attempted_incorrectly verdict in the
# ledger is, by construction, LLM-verified (see module docstring). The ledger
# itself does not persist a per-history-entry confidence number, so this
# engine assigns a flat confidence for verdict-derived evidence, chosen to
# sit inside the real confidence range LRET's own skill_signals show
# (0.68-0.80 in the sample session read for this build).
VOCAB_COACH_SIGNAL_CONFIDENCE = 0.75

# --- Dominance gates -----------------------------------------------------
MIN_EVIDENCE_FOR_DOMINANCE = 3
MIN_WEAKNESS_FOR_DOMINANCE = 0.55
MIN_CONFIDENCE_FOR_DOMINANCE = 0.5
EVIDENCE_SATURATION = 8.0  # evidence_count reaches full weight in dominance scoring at this count


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Optional[str]) -> Optional[Dict[str, Any]]:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def write_json(path: str, data: Any, pretty: bool = False) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2 if pretty else None) + "\n", encoding="utf-8")


def build_lret_signal(lret_session: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Returns (skill_signal_dict, pattern_family_counts, available)."""
    if not isinstance(lret_session, dict):
        return {"available": False, "skills": {}, "pattern_families": {}}
    payload = lret_session.get("learning_intelligence_payload") or {}
    skills_out: Dict[str, Any] = {}
    for row in payload.get("skill_signals") or []:
        if not isinstance(row, dict):
            continue
        skill_id = row.get("skill_id")
        if not skill_id:
            continue
        score = row.get("score")
        if not isinstance(score, (int, float)):
            continue
        direction = SKILL_WEAKNESS_DIRECTION.get(skill_id)
        if direction is None:
            # Unknown skill_id (future LRET version). Fail safe: don't guess
            # a direction, just skip it from weakness scoring but keep it
            # visible in raw form for diagnostics.
            skills_out[skill_id] = {
                "weakness_score": None,
                "confidence": row.get("confidence"),
                "evidence_count": row.get("evidence_count"),
                "raw_score": score,
                "direction": "unknown",
                "capacity_domain": None,
                "source": "lret",
                "note": "Unrecognized skill_id -- direction/capacity_domain mapping not defined, weakness_score withheld.",
            }
            continue
        weakness = score if direction == "direct" else round(1.0 - score, 4)
        skills_out[skill_id] = {
            "weakness_score": round(float(weakness), 4),
            "confidence": row.get("confidence"),
            "evidence_count": row.get("evidence_count"),
            "raw_score": score,
            "direction": direction,
            "capacity_domain": SKILL_CAPACITY_DOMAIN.get(skill_id),
            "source": "lret",
        }

    pattern_families: Dict[str, int] = {}
    for row in payload.get("pattern_signals") or []:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("pattern_id") or "")
        fam = pid.split("::")[-1] if "::" in pid else pid
        if not fam:
            continue
        pattern_families[fam] = pattern_families.get(fam, 0) + int(row.get("count") or 0)

    return {"available": bool(skills_out or pattern_families), "skills": skills_out, "pattern_families": pattern_families}


def build_vocab_coach_signal(ledger: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(ledger, dict):
        return {"available": False, "topics": {}}
    items = ledger.get("items") or {}
    by_topic: Dict[str, Dict[str, int]] = {}
    for phrase, entry in items.items():
        if not isinstance(entry, dict):
            continue
        topic = entry.get("topic") or "unknown_topic"
        history = entry.get("history")
        # Fall back to last_outcome only if no history array is present at
        # all (defensive -- v1.1 ledgers always write history, but this
        # keeps the engine from silently producing zero evidence against an
        # older/partial ledger).
        verdict_rows = history if isinstance(history, list) and history else (
            [{"verdict": entry.get("last_outcome")}] if entry.get("last_outcome") else []
        )
        bucket = by_topic.setdefault(topic, {"attempted_incorrectly": 0, "used_but_awkward": 0, "used_correctly": 0})
        for row in verdict_rows:
            if not isinstance(row, dict):
                continue
            verdict = row.get("verdict")
            if verdict in VOCAB_EXCLUDED_VERDICTS or verdict is None:
                continue
            if verdict in bucket:
                bucket[verdict] += 1

    topics_out: Dict[str, Any] = {}
    for topic, counts in by_topic.items():
        incorrect = counts["attempted_incorrectly"]
        awkward = counts["used_but_awkward"]
        correct = counts["used_correctly"]
        evidence_count = incorrect + awkward + correct
        if evidence_count == 0:
            continue
        weighted_negative = incorrect * VOCAB_VERDICT_NEGATIVE_WEIGHT["attempted_incorrectly"] + awkward * VOCAB_VERDICT_NEGATIVE_WEIGHT["used_but_awkward"]
        weighted_positive = correct * VOCAB_VERDICT_POSITIVE_WEIGHT["used_correctly"]
        weakness_score = round(weighted_negative / (weighted_negative + weighted_positive), 4) if (weighted_negative + weighted_positive) > 0 else 0.0
        topics_out[topic] = {
            "weakness_score": weakness_score,
            "confidence": VOCAB_COACH_SIGNAL_CONFIDENCE,
            "evidence_count": evidence_count,
            "verdict_counts": {"attempted_incorrectly": incorrect, "used_but_awkward": awkward, "used_correctly": correct},
            "capacity_domain": "lexical_precision",
            "source": "vocab_coach",
        }
    return {"available": bool(topics_out), "topics": topics_out}


def build_capacity_domain_rollup(lret_signal: Dict[str, Any], vocab_signal: Dict[str, Any]) -> Dict[str, Any]:
    contributions: Dict[str, List[Dict[str, Any]]] = {"lexical_precision": [], "academic_style": []}
    for skill_id, row in lret_signal.get("skills", {}).items():
        dom = row.get("capacity_domain")
        if dom in contributions and isinstance(row.get("weakness_score"), (int, float)):
            contributions[dom].append({
                "label": skill_id, "weakness_score": row["weakness_score"],
                "confidence": row.get("confidence") or 0.5, "evidence_count": row.get("evidence_count") or 0,
                "source": "lret",
            })
    for topic, row in vocab_signal.get("topics", {}).items():
        dom = row.get("capacity_domain")
        if dom in contributions:
            contributions[dom].append({
                "label": topic, "weakness_score": row["weakness_score"],
                "confidence": row.get("confidence") or 0.5, "evidence_count": row.get("evidence_count") or 0,
                "source": "vocab_coach",
            })

    rollup: Dict[str, Any] = {}
    for dom, rows in contributions.items():
        if not rows:
            continue
        total_weight = sum((r["evidence_count"] or 0) * r["confidence"] for r in rows)
        if total_weight <= 0:
            continue
        weighted_avg = sum(r["weakness_score"] * (r["evidence_count"] or 0) * r["confidence"] for r in rows) / total_weight
        total_evidence = sum(r["evidence_count"] or 0 for r in rows)
        sources = sorted({r["source"] for r in rows})
        rollup[dom] = {
            "weakness_score": round(weighted_avg, 4),
            "evidence_count": total_evidence,
            "confidence": round(sum(r["confidence"] for r in rows) / len(rows), 4),
            "source": "both" if len(sources) > 1 else sources[0],
            "contributors": sorted(rows, key=lambda r: -r["weakness_score"])[:5],
        }
    return rollup


def find_dominant(lret_signal: Dict[str, Any], vocab_signal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for skill_id, row in lret_signal.get("skills", {}).items():
        if not isinstance(row.get("weakness_score"), (int, float)):
            continue
        candidates.append({**row, "key": skill_id, "granularity": "lret_skill"})
    for topic, row in vocab_signal.get("topics", {}).items():
        candidates.append({**row, "key": topic, "granularity": "vocab_coach_topic"})

    best = None
    best_score = -1.0
    for c in candidates:
        weakness = c.get("weakness_score") or 0.0
        confidence = c.get("confidence") or 0.0
        evidence = c.get("evidence_count") or 0
        if weakness < MIN_WEAKNESS_FOR_DOMINANCE or evidence < MIN_EVIDENCE_FOR_DOMINANCE or confidence < MIN_CONFIDENCE_FOR_DOMINANCE:
            continue
        evidence_factor = min(1.0, evidence / EVIDENCE_SATURATION)
        rank_score = weakness * confidence * evidence_factor
        if rank_score > best_score:
            best_score = rank_score
            best = c
    return best


def build(lret_session: Optional[Dict[str, Any]], vocab_ledger: Optional[Dict[str, Any]], student_id: Optional[str]) -> Dict[str, Any]:
    lret_signal = build_lret_signal(lret_session)
    vocab_signal = build_vocab_coach_signal(vocab_ledger)
    rollup = build_capacity_domain_rollup(lret_signal, vocab_signal)
    dominant = find_dominant(lret_signal, vocab_signal)

    return {
        "schema_version": SCHEMA_VERSION,
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "student_id": student_id or (lret_session or {}).get("identity", {}).get("student_id") if isinstance(lret_session, dict) else student_id,
        "sources": {
            "lret_available": lret_signal["available"],
            "vocab_coach_available": vocab_signal["available"],
        },
        "lret_signal": lret_signal.get("skills", {}),
        "lret_error_pattern_families": lret_signal.get("pattern_families", {}),
        "vocab_coach_signal": vocab_signal.get("topics", {}),
        "capacity_domain_rollup": rollup,
        "dominant_lexical_weakness": dominant,
        "dominance_gates": {
            "min_evidence_count": MIN_EVIDENCE_FOR_DOMINANCE,
            "min_weakness_score": MIN_WEAKNESS_FOR_DOMINANCE,
            "min_confidence": MIN_CONFIDENCE_FOR_DOMINANCE,
        },
        "boundary": (
            "Aggregation only -- no new detection, scoring, or teaching. Combines "
            "LRET's learning_intelligence_payload and Vocabulary Coach's ledger "
            "verdict history (both already-produced artifacts) into one shared "
            "shape for the LIE profile builder and Priority Engine input chain."
        ),
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Aggregate LRET + Vocabulary Coach signals into a shared lexical-signal artifact.")
    ap.add_argument("--lret-session", help="Path to a 07d_lret_session.json artifact (current or prior run).")
    ap.add_argument("--vocab-ledger", help="Path to a student's vocab_coach ledger JSON.")
    ap.add_argument("--student-id")
    ap.add_argument("--output", "-o", required=True)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)

    lret_session = read_json(args.lret_session)
    vocab_ledger = read_json(args.vocab_ledger)
    out = build(lret_session, vocab_ledger, args.student_id)
    write_json(args.output, out, pretty=args.pretty)
    print(f"[lexical_signal_aggregator] wrote {args.output} (lret_available={out['sources']['lret_available']}, vocab_coach_available={out['sources']['vocab_coach_available']}, dominant={ (out['dominant_lexical_weakness'] or {}).get('key') })")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
