#!/usr/bin/env python3
"""
VA / ST.ELLA Scorer Input Evidence Guard v1.4.7
================================================

Standalone targeted bridge.

Purpose:
- Preserve the v1.4.4 structural metadata fix: word_count, sentence_count,
  paragraph_count must be available to the scorer.
- Add scorer-readable detector evidence lists so local-error pressure is not
  silently zeroed by the premium scorer.
- Canonicalize universal detector family names such as G_VERB_PATTERN into the
  family names expected by the scorer and Priority Engine, for example
  VERB_PATTERN.
- Remove exact duplicate detector rows before scoring / priority routing.

Boundary:
- This file does not detect new errors.
- This file does not score IELTS bands.
- This file does not change the meaning, quote, severity, or criterion of a
  detector row.
- It performs metadata normalization, exact-deduplication, and scorer/priority
  field normalization only.
- It contains no essay-specific rules, no topic-specific rules, and no phrase
  bank.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ENGINE_ID = "VA_STELLA_SCORER_INPUT_EVIDENCE_GUARD"
ENGINE_VERSION = "1.4.7-standalone-metadata-and-evidence-contract"
SCHEMA_VERSION = "DETECTOR_OUTPUT_SCORER_EVIDENCE_GUARDED_V1_4_7"

FAMILY_ALIASES = {
    # Grammar / sentence control
    "G_VERB_PATTERN": "VERB_PATTERN",
    "G_MISSING_VERB": "CLAUSE_STRUCTURE",
    "G_SPACING": "GRAMMAR_PUNCTUATION",
    "G_COMMA_TRANSITION": "GRAMMAR_PUNCTUATION",
    "G_SV_AGREEMENT": "SUBJECT_VERB_AGREEMENT",
    "G_COMPARATIVE_FORM": "COMPARATIVE_FORM",
    "G_ARTICLE": "ARTICLE_DETERMINER",
    "G_VERB_FORM": "VERB_FORM",
    "G_VERB_TENSE": "VERB_TENSE",
    "G_WORD_ORDER": "WORD_ORDER",
    # Lexical / style
    "L_INFORMAL_VOCAB": "REGISTER",
    "S_INFORMAL_TONE": "REGISTER",
    "L_REPETITION": "REPETITION",
    "L_LIMITED_VOCAB": "LEXICAL_PRECISION",
    "L_WORD_CHOICE": "WORD_CHOICE",
    "L_COLLOCATION": "COLLOCATION",
    "L_WORD_FORM": "WORD_FORM",
    "L_SPELLING": "SPELLING",
    # Discourse / task response
    "A_UNDERDEVELOPED": "UNSUPPORTED_CLAIM",
    "A_OVERGENERALIZATION": "UNSUPPORTED_CLAIM",
    "A_WEAK_EXAMPLE": "WEAK_EXAMPLE",
    "A_REASONING_CHAIN": "REASONING_CHAIN",
    "C_SIMPLE_CONNECTORS": "TRANSITION",
    "C_REFERENCE": "REFERENCE_BREAK",
    "C_LOGICAL_PROGRESSION": "LOGICAL_PROGRESSION",
    "C_PARAGRAPH_STRUCTURE": "PARAGRAPH_STRUCTURE",
}

CANONICAL_RUBRIC_BY_FAMILY = {
    "VERB_PATTERN": "grammar",
    "CLAUSE_STRUCTURE": "grammar",
    "GRAMMAR_PUNCTUATION": "grammar",
    "SUBJECT_VERB_AGREEMENT": "grammar",
    "COMPARATIVE_FORM": "grammar",
    "ARTICLE_DETERMINER": "grammar",
    "VERB_FORM": "grammar",
    "VERB_TENSE": "grammar",
    "WORD_ORDER": "grammar",
    "REGISTER": "lexical_resource",
    "REPETITION": "lexical_resource",
    "LEXICAL_PRECISION": "lexical_resource",
    "WORD_CHOICE": "lexical_resource",
    "COLLOCATION": "lexical_resource",
    "WORD_FORM": "lexical_resource",
    "SPELLING": "lexical_resource",
    "UNSUPPORTED_CLAIM": "task_response",
    "WEAK_EXAMPLE": "task_response",
    "REASONING_CHAIN": "task_response",
    "TRANSITION": "coherence_cohesion",
    "REFERENCE_BREAK": "coherence_cohesion",
    "LOGICAL_PROGRESSION": "coherence_cohesion",
    "PARAGRAPH_STRUCTURE": "coherence_cohesion",
}

RUBRIC_ALIASES = {
    "grammar": "grammar",
    "grammatical_range_accuracy": "grammar",
    "grammatical_range_and_accuracy": "grammar",
    "lexical_resource": "lexical_resource",
    "academic_style": "lexical_resource",
    "argumentation": "task_response",
    "task_response": "task_response",
    "cohesion_coherence": "coherence_cohesion",
    "coherence_cohesion": "coherence_cohesion",
    "cohesion": "coherence_cohesion",
}

SEVERITY_BASE = {
    "critical": 1.15,
    "severe": 1.05,
    "high": 0.95,
    "medium": 0.62,
    "moderate": 0.62,
    "low": 0.28,
    "minor": 0.20,
}
CONFIDENCE_MULT = {
    "very_high": 1.00,
    "high": 0.95,
    "medium": 0.75,
    "moderate": 0.75,
    "low": 0.55,
    "very_low": 0.40,
}


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


def get_nested(d: Dict[str, Any], path: Iterable[str]) -> Any:
    cur: Any = d
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def ensure_dict_path(d: Dict[str, Any], path: Iterable[str]) -> Dict[str, Any]:
    cur = d
    for key in path:
        nxt = cur.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[key] = nxt
        cur = nxt
    return cur


def sentence_spans(text: str) -> List[Tuple[int, int, str]]:
    spans: List[Tuple[int, int, str]] = []
    for m in re.finditer(r"[^.!?\n]+(?:[.!?]+|$)", text or "", flags=re.M):
        sent = m.group(0).strip()
        if sent:
            spans.append((m.start(), m.end(), sent))
    if not spans and str(text or "").strip():
        spans.append((0, len(text), text.strip()))
    return spans


def paragraph_spans(text: str) -> List[Tuple[int, int, str]]:
    text = text or ""
    spans: List[Tuple[int, int, str]] = []
    for m in re.finditer(r"[^\n]+(?:\n(?!\n)[^\n]+)*", text, flags=re.M):
        para = m.group(0).strip()
        if para:
            spans.append((m.start(), m.end(), para))
    if not spans and text.strip():
        spans.append((0, len(text), text.strip()))
    return spans


def count_words(text: str) -> int:
    return len(re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text or ""))


def compute_metadata(text: str) -> Dict[str, Any]:
    return {
        "word_count": count_words(text),
        "sentence_count": len(sentence_spans(text)),
        "paragraph_count": len(paragraph_spans(text)),
        "character_count": len(text or ""),
        "task_schema_status": "complete",
        "task_schema_confidence": 0.72,
        "metadata_source": "scorer_input_evidence_guard_v1_4_7_from_essay_text",
    }


def coerce_positive_int(value: Any) -> int:
    try:
        v = int(float(value))
        return v if v > 0 else 0
    except Exception:
        return 0


def extract_submission_record(submission: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(submission, dict):
        return {}
    essays = submission.get("essays")
    if isinstance(essays, list) and essays and isinstance(essays[0], dict):
        return essays[0]
    return submission


def existing_metadata(record: Dict[str, Any]) -> Dict[str, int]:
    locations = [
        [],
        ["metadata"],
        ["generated_metadata"],
        ["detector_metric_profile", "shared"],
        ["scorer_payload", "metadata"],
        ["scorer_payload", "premium_metric_profile_mapped_metrics", "shared"],
    ]
    out = {"word_count": 0, "sentence_count": 0, "paragraph_count": 0}
    for field in out:
        for path in locations:
            src = record if not path else get_nested(record, path)
            if isinstance(src, dict):
                val = coerce_positive_int(src.get(field))
                if val:
                    out[field] = val
                    break
    return out


def attach_metadata(record: Dict[str, Any], meta: Dict[str, Any], *, source: str, changed_fields: List[str], submission_record: Dict[str, Any]) -> None:
    shared = {
        "word_count": int(meta.get("word_count") or 0),
        "sentence_count": int(meta.get("sentence_count") or 0),
        "paragraph_count": int(meta.get("paragraph_count") or 0),
        "task_schema_status": str(meta.get("task_schema_status") or "complete"),
        "task_schema_confidence": float(meta.get("task_schema_confidence") or 0.72),
    }
    record.update({
        "word_count": shared["word_count"],
        "sentence_count": shared["sentence_count"],
        "paragraph_count": shared["paragraph_count"],
    })
    for path in (["metadata"], ["generated_metadata"], ["detector_metric_profile", "shared"],
                 ["scorer_payload", "metadata"], ["scorer_payload", "premium_metric_profile_mapped_metrics", "shared"]):
        node = ensure_dict_path(record, path)
        node.update(shared)

    task_type = record.get("task_type") or submission_record.get("task_type") or "WT2"
    prompt_text = record.get("prompt_text") or submission_record.get("prompt_text") or submission_record.get("prompt") or ""
    essay_text = record.get("essay_text") or record.get("text") or submission_record.get("essay_text") or submission_record.get("text") or ""

    record["task_type"] = task_type
    record["prompt_text"] = prompt_text
    record["essay_text"] = essay_text
    task_profile = ensure_dict_path(record, ["task_profile"])
    task_profile.update({
        "task_type": task_type,
        "task_type_confidence": 0.95 if task_type else 0.0,
        "prompt_present": bool(prompt_text),
        "score_ready": True,
    })
    record["intake_record"] = {
        "prompt_text": prompt_text,
        "essay_text": essay_text,
        "task_type": task_type,
    }
    meta_node = ensure_dict_path(record, ["meta"])
    meta_node.update({"prompt_present": bool(prompt_text), "task_type": task_type})

    sp = ensure_dict_path(record, ["scorer_payload"])
    sp["word_count"] = shared["word_count"]
    sp["sentence_count"] = shared["sentence_count"]
    sp["paragraph_count"] = shared["paragraph_count"]
    sp["metadata"] = {**(sp.get("metadata") or {}), **shared, "prompt_text": prompt_text, "task_type": task_type}

    mapped = ensure_dict_path(record, ["scorer_payload", "premium_metric_profile_mapped_metrics"])
    mapped.update(shared)

    record["scorer_metadata_guard"] = {
        "status": "ok",
        "source": source,
        "changed_fields": changed_fields,
        "metadata_contract_complete": all(shared[k] > 0 for k in ("word_count", "sentence_count", "paragraph_count")),
        "guard_engine_id": ENGINE_ID,
        "guard_engine_version": ENGINE_VERSION,
    }


def norm_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def canonical_family(family: Any, issue_code: Any = None) -> str:
    raw = str(family or issue_code or "UNKNOWN").strip().upper()
    if not raw:
        return "UNKNOWN"
    if raw in FAMILY_ALIASES:
        return FAMILY_ALIASES[raw]
    for prefix in ("G_", "L_", "S_", "A_", "C_", "TR_"):
        if raw.startswith(prefix):
            stripped = raw[len(prefix):]
            return FAMILY_ALIASES.get(stripped, stripped)
    return raw


def canonical_rubric(row: Dict[str, Any], fam: str) -> str:
    raw = str(row.get("primary_rubric") or row.get("rubric") or row.get("criterion") or row.get("category") or "").strip().lower()
    return CANONICAL_RUBRIC_BY_FAMILY.get(fam) or RUBRIC_ALIASES.get(raw) or "grammar"


def confidence_numeric(value: Any) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    return CONFIDENCE_MULT.get(str(value or "medium").strip().lower(), 0.75)


def score_weight(row: Dict[str, Any]) -> float:
    explicit = row.get("score_weight") or row.get("score_charge_weight") or row.get("impact_weight")
    try:
        if explicit is not None and float(explicit) > 0:
            return round(float(explicit), 3)
    except Exception:
        pass
    sev = str(row.get("severity") or "medium").strip().lower()
    conf = confidence_numeric(row.get("confidence"))
    base = SEVERITY_BASE.get(sev, 0.62)
    return round(max(0.12, base * max(0.4, conf)), 3)


def stable_row_id(row: Dict[str, Any], idx: int, fam: str) -> str:
    existing = str(row.get("row_id") or row.get("detector_evidence_id") or "").strip()
    if existing:
        return existing
    seed = json.dumps({
        "i": idx,
        "family": fam,
        "quote": row.get("quote") or row.get("surface_quote") or row.get("excerpt"),
        "start": row.get("span_start") or row.get("start"),
        "end": row.get("span_end") or row.get("end"),
    }, sort_keys=True, ensure_ascii=False, default=str)
    return "det_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]


def dedup_key(row: Dict[str, Any], fam: str) -> Tuple[str, str, str, str, str]:
    return (
        str(row.get("row_id") or ""),
        fam,
        str(row.get("span_start") or row.get("start") or ""),
        str(row.get("span_end") or row.get("end") or ""),
        norm_text(row.get("quote") or row.get("surface_quote") or row.get("excerpt") or ""),
    )


def normalize_rows(rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    review: List[Dict[str, Any]] = []
    seen = set()
    duplicate_count = 0
    family_counts: Dict[str, int] = {}
    original_family_counts: Dict[str, int] = {}

    for idx, raw in enumerate(rows):
        if not isinstance(raw, dict):
            continue
        fam = canonical_family(raw.get("family"), raw.get("issue_code") or raw.get("error_type"))
        key = dedup_key(raw, fam)
        if key in seen:
            duplicate_count += 1
            continue
        seen.add(key)
        row = copy.deepcopy(raw)
        original_family = str(row.get("family") or row.get("error_type") or row.get("issue_code") or "UNKNOWN").strip().upper()
        rub = canonical_rubric(row, fam)
        weight = score_weight(row)
        chargeable = bool(row.get("chargeable", True)) and not bool(row.get("review_only", False))

        row["row_id"] = stable_row_id(row, idx, fam)
        row["source_family"] = original_family
        row["family"] = fam
        row["primary_family"] = fam
        row["criterion"] = rub
        row["rubric"] = rub
        row["category"] = rub
        row["primary_rubric"] = rub
        row["confidence"] = confidence_numeric(row.get("confidence"))
        row["severity"] = str(row.get("severity") or "medium").lower()
        row["score_weight"] = weight if chargeable else 0.0
        row["score_charge_weight"] = weight if chargeable else 0.0
        row["chargeable_for_scoring"] = chargeable
        row["chargeable"] = chargeable
        row["root_or_secondary"] = str(row.get("root_or_secondary") or row.get("root_or_symptom") or "root")
        row["source"] = row.get("source") or "detector_evidence_guard_v1_4_7"
        row["quote"] = row.get("quote") or row.get("surface_quote") or row.get("excerpt") or ""
        row["local_quote"] = row.get("local_quote") or row.get("quote") or row.get("surface_quote") or ""
        row["evidence_guard"] = {
            "canonicalized": original_family != fam,
            "original_family": original_family,
            "canonical_family": fam,
            "score_weight_source": "explicit" if raw.get("score_weight") or raw.get("score_charge_weight") or raw.get("impact_weight") else "severity_confidence_default",
        }
        family_counts[fam] = family_counts.get(fam, 0) + 1
        original_family_counts[original_family] = original_family_counts.get(original_family, 0) + 1
        if chargeable:
            out.append(row)
        else:
            review.append(row)

    qa = {
        "input_row_count": len(rows),
        "chargeable_row_count": len(out),
        "review_only_row_count": len(review),
        "duplicate_rows_removed": duplicate_count,
        "canonical_family_counts": family_counts,
        "original_family_counts": original_family_counts,
    }
    return out, review, qa


def extract_rows(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    for key in ("student_rows", "all_rows", "detector_rows", "diagnostic_rows"):
        value = record.get(key)
        if isinstance(value, list) and value:
            return [x for x in value if isinstance(x, dict)]
    payload = record.get("scorer_payload") or {}
    if isinstance(payload, dict):
        value = payload.get("chargeable_detector_rows")
        if isinstance(value, list) and value:
            return [x for x in value if isinstance(x, dict)]
    return []


# Gold v1.4.13 fix (stress-test Problem 3): local_language_damage_index and
# serious_error_sentence_ratio are read by the scorer (premium_unified_scorer_v1_4_1_fixed.py
# adapt_record()) from scorer_payload.premium_metric_profile_mapped_metrics.shared,
# but nothing in the Gold pipeline ever wrote either field there -- confirmed
# via a weak/medium/strong stress test where both sat flat at 0.0 regardless
# of actual chargeable-error density (21 vs 2 vs 1 chargeable rows). This is
# the safety-net signal meant to catch heavily-damaged writing even when the
# finer TR/CC rubric metrics (see evaluator_rubric_bridge_v1.py, a separate
# v1.4.13 fix) don't have coverage. Computed here from data this guard
# already builds (chargeable_detector_rows), not detected fresh.
#
# Severity note: every chargeable row observed across the stress test carried
# severity "moderate" uniformly -- det_vip does not currently vary this field
# in practice, so the primary signal here is chargeable-row DENSITY per
# sentence (which does differentiate: 21/12 sentences for a heavily-damaged
# essay vs 1/11 for a clean one), with severity/family used as a secondary,
# forward-compatible boost if det_vip's severity output becomes more granular.
STRUCTURAL_DAMAGE_FAMILIES = {
    "SUBJECT_VERB_AGREEMENT", "CLAUSE_STRUCTURE", "VERB_FORM", "VERB_TENSE",
    "FRAGMENT", "RUN_ON", "WORD_ORDER", "CONSTRUCTION", "VERB_PATTERN",
    "CONDITIONAL_STRUCTURE", "NOUN_NUMBER_COUNTABILITY", "ARTICLE_DETERMINER",
    "PREPOSITION_PATTERN",
}
HIGH_SEVERITY_LABELS = {"high", "severe", "critical", "major"}


def compute_damage_signals(chargeable_rows: List[Dict[str, Any]], sentence_count: int) -> Dict[str, float]:
    if not sentence_count or sentence_count <= 0 or not chargeable_rows:
        return {"local_language_damage_index": 0.0, "serious_error_sentence_ratio": 0.0}
    serious_sentence_idxs: set = set()
    for row in chargeable_rows:
        fam = str(row.get("family") or "").upper()
        sev = str(row.get("severity") or "medium").lower()
        if sev in HIGH_SEVERITY_LABELS or fam in STRUCTURAL_DAMAGE_FAMILIES:
            sidx = row.get("sentence_index")
            if sidx is not None:
                serious_sentence_idxs.add(sidx)
    local_damage = min(1.0, len(chargeable_rows) / max(sentence_count, 1))
    serious_ratio = min(1.0, len(serious_sentence_idxs) / max(sentence_count, 1))
    return {
        "local_language_damage_index": round(local_damage, 4),
        "serious_error_sentence_ratio": round(serious_ratio, 4),
    }


def enrich_detector(detector: Dict[str, Any], submission: Optional[Dict[str, Any]], strict: bool = False) -> Dict[str, Any]:
    out = copy.deepcopy(detector)
    if not isinstance(out.get("results"), list):
        raise ValueError("Detector output must contain results[].")
    submission_record = extract_submission_record(submission)
    fallback_text = str(submission_record.get("essay_text") or submission_record.get("text") or "")

    qa_records: List[Dict[str, Any]] = []
    total_chargeable = 0
    total_duplicates = 0

    for idx, rec in enumerate(out.get("results") or []):
        if not isinstance(rec, dict):
            continue
        text = str(rec.get("essay_text") or rec.get("text") or fallback_text or "")
        computed = compute_metadata(text)
        existing = existing_metadata(rec)
        final: Dict[str, Any] = {}
        changed: List[str] = []
        for key in ("word_count", "sentence_count", "paragraph_count"):
            if existing.get(key, 0) > 0:
                final[key] = existing[key]
            else:
                final[key] = int(computed.get(key) or 0)
                changed.append(key)
        final["task_schema_status"] = "complete"
        final["task_schema_confidence"] = 0.72
        if strict and any(int(final.get(k) or 0) <= 0 for k in ("word_count", "sentence_count", "paragraph_count")):
            raise ValueError(f"Cannot recover positive scorer metadata for results[{idx}].")
        attach_metadata(rec, final, source="existing_detector_metadata" if not changed else "recovered_from_essay_text", changed_fields=changed, submission_record=submission_record)

        raw_rows = extract_rows(rec)
        chargeable_rows, review_rows, row_qa = normalize_rows(raw_rows)
        # Expose scorer-readable lists first; this is the primary scorer adapter path.
        sp = ensure_dict_path(rec, ["scorer_payload"])
        sp["chargeable_detector_rows"] = chargeable_rows
        sp["review_only_detector_rows"] = review_rows
        sp["evidence_contract"] = {
            "detector_rows_are_scorer_readable": True,
            "weight_keys": ["score_weight", "score_charge_weight"],
            "chargeable_key": "chargeable_for_scoring",
            "canonical_family_policy": "universal_family_alias_map_v1_4_7",
        }

        # v1.4.13 Problem 3 fix: compute and expose the damage-signal fields
        # the scorer actually reads (see compute_damage_signals() docstring
        # above for why this previously sat flat at 0.0 on every run).
        damage_signals = compute_damage_signals(chargeable_rows, int(final.get("sentence_count") or 0))
        mapped_shared = ensure_dict_path(rec, ["scorer_payload", "premium_metric_profile_mapped_metrics", "shared"])
        mapped_shared.update(damage_signals)
        rec["scorer_input_damage_signals_audit"] = {
            **damage_signals,
            "chargeable_row_count": len(chargeable_rows),
            "sentence_count": int(final.get("sentence_count") or 0),
            "source": "scorer_input_evidence_guard_v1_4_7_compute_damage_signals",
        }
        # Keep standard paths canonical too so PE and ErrorMap consume deduped evidence.
        rec["student_rows"] = chargeable_rows + review_rows
        rec["all_rows"] = chargeable_rows + review_rows
        rec["evidence_guard_v1_4_7"] = row_qa
        total_chargeable += row_qa["chargeable_row_count"]
        total_duplicates += row_qa["duplicate_rows_removed"]
        qa_records.append({
            "essay_id": rec.get("essay_id"),
            "metadata_before": existing,
            "metadata_after": {k: int(final.get(k) or 0) for k in ("word_count", "sentence_count", "paragraph_count")},
            "changed_metadata_fields": changed,
            "row_qa": row_qa,
            "status": "ok" if all(int(final.get(k) or 0) > 0 for k in ("word_count", "sentence_count", "paragraph_count")) and row_qa["chargeable_row_count"] > 0 else "invalid",
        })

    out["schema_version"] = SCHEMA_VERSION
    out["source_detector_engine_id"] = detector.get("engine_id")
    out["source_detector_schema_version"] = detector.get("schema_version")
    out["scorer_evidence_guard"] = {
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "boundary": "Metadata + scorer/priority evidence normalization only; no detection or scoring performed.",
        "record_count": len(qa_records),
        "records": qa_records,
        "total_chargeable_rows_for_scoring": total_chargeable,
        "total_duplicate_rows_removed": total_duplicates,
        "all_records_ready_for_scorer": all(r.get("status") == "ok" for r in qa_records),
    }
    if strict and not out["scorer_evidence_guard"]["all_records_ready_for_scorer"]:
        raise ValueError("Scorer evidence guard failed readiness checks.")
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Scorer input metadata + evidence guard v1.4.7.")
    ap.add_argument("--detector", required=True, help="Detector output JSON.")
    ap.add_argument("--submission", required=False, help="Optional normalized submission JSON fallback.")
    ap.add_argument("--output", "-o", required=True, help="Output guarded detector JSON.")
    ap.add_argument("--strict", action="store_true", help="Fail if positive metadata or chargeable scorer evidence cannot be produced.")
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)

    detector = read_json(args.detector)
    submission = read_json(args.submission) if args.submission else None
    out = enrich_detector(detector, submission, strict=args.strict)
    write_json(args.output, out, pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
