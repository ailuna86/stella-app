#!/usr/bin/env python3
"""
Gold Practice Session Builder v1.4.3 — standalone
=================================================

Builds a concrete practice-session artifact from a Gold directive, ErrorMap,
score contract, and exercise-bank JSONL. Imports no previous versions.

Boundary:
- This is a targeted Practice Engine bridge, separate from the orchestrator.
- It does not score essays.
- It does not detect errors.
- It does not provide LRET suggestions or Writing Coach missions.
"""
from __future__ import annotations

import argparse
import json
import random
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = "GOLD_PRACTICE_SESSION_STANDALONE_V1_4_3"
ENGINE_ID = "VA_STELLA_GOLD_PRACTICE_SESSION_BUILDER"
ENGINE_VERSION = "1.4.3-standalone-no-imports"

DOMAIN_TO_CRITERION = {
    "sentence_control": "grammatical_range_accuracy",
    "lexical_precision": "lexical_resource",
    "academic_style": "lexical_resource",
    "argument_development": "task_response",
    "cohesion_control": "coherence_cohesion",
    "task_response_control": "task_response",
}
FAMILY_MAP = {
    "G_ARTICLE": "ARTICLE_DETERMINER", "G_DETERMINER": "ARTICLE_DETERMINER",
    "G_SV_AGREEMENT": "SUBJECT_VERB_AGREEMENT", "G_VERB_PATTERN": "VERB_FORM",
    "G_MISSING_VERB": "CLAUSE_STRUCTURE", "G_COMPARATIVE_FORM": "COMPARATIVES",
    "G_SPACING": "PUNCTUATION", "G_COMMA_TRANSITION": "PUNCTUATION",
    "L_REPETITION": "REPETITION", "L_LIMITED_VOCAB": "LEXICAL_PRECISION",
    "L_INFORMAL_VOCAB": "FORMALITY", "S_INFORMAL_TONE": "FORMALITY",
    "A_UNDERDEVELOPED": "CLAIM_SUPPORT", "A_OVERGENERALIZATION": "OVERGENERALIZATION",
    "C_SIMPLE_CONNECTORS": "TRANSITIONS",
}
CRITERION_FALLBACK_FAMILIES = {
    "grammatical_range_accuracy": ["CLAUSE_STRUCTURE", "VERB_FORM", "ARTICLE_DETERMINER", "SUBJECT_VERB_AGREEMENT", "PUNCTUATION"],
    "lexical_resource": ["LEXICAL_PRECISION", "COLLOCATION", "WORD_CHOICE", "REPETITION", "FORMALITY"],
    "task_response": ["CLAIM_SUPPORT", "TASK_RESPONSE", "POSITION_CLARITY", "EXAMPLE_QUALITY", "OVERGENERALIZATION"],
    "coherence_cohesion": ["TRANSITIONS", "REFERENCE_COHESION", "PARAGRAPH_PROGRESS", "DISCOURSE_LINKING"],
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: str) -> Any:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(path: str, data: Any, pretty: bool = False) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2 if pretty else None) + "\n", encoding="utf-8")


def load_bank(path: str) -> List[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    items: List[Dict[str, Any]] = []
    for line_no, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            obj.setdefault("_bank_line", line_no)
            items.append(obj)
    if not items:
        raise ValueError(f"exercise bank contains no valid JSONL items: {path}")
    return items


def item_key(item: Dict[str, Any]) -> str:
    return str(item.get("exercise_id") or item.get("id") or item.get("item_id") or f"line_{item.get('_bank_line')}")


def norm(s: Any) -> str:
    return str(s or "").strip().upper()


def item_fields(item: Dict[str, Any]) -> List[str]:
    vals = []
    for key in ("family", "skill_family", "micro_skill", "skill", "skill_id", "capacity_domain", "category", "criterion", "rubric"):
        val = item.get(key)
        if val is not None:
            vals.append(norm(val))
    return vals


def matches(item: Dict[str, Any], family: str, criterion: str, capacity: str) -> bool:
    fields = item_fields(item)
    return norm(family) in fields or norm(criterion) in fields or norm(capacity) in fields


def focus_from_directive(directive: Dict[str, Any]) -> List[Dict[str, Any]]:
    focus = directive.get("focus_areas") if isinstance(directive, dict) else []
    if isinstance(focus, list) and focus:
        return [f for f in focus if isinstance(f, dict)]
    primary = directive.get("primary_focus") if isinstance(directive, dict) else None
    return [primary] if isinstance(primary, dict) else []


def fallback_focus_from_errormap(errormap: Dict[str, Any]) -> List[Dict[str, Any]]:
    counts = Counter()
    fams: Dict[str, Counter] = {}
    for e in errormap.get("errors", []) or []:
        if not isinstance(e, dict) or e.get("chargeable") is False:
            continue
        cap = str(e.get("capacity_domain") or "mixed_review")
        counts[cap] += 1
        fams.setdefault(cap, Counter())[str(e.get("family") or "UNKNOWN_FAMILY")] += 1
    out = []
    for i, (cap, count) in enumerate(counts.most_common(5), start=1):
        out.append({
            "rank": i,
            "capacity_domain": cap,
            "criterion": DOMAIN_TO_CRITERION.get(cap, "task_response"),
            "top_families": [f for f, _ in fams.get(cap, Counter()).most_common(5)],
            "evidence_count": count,
            "priority_reason": "Fallback from ErrorMap evidence.",
        })
    return out


def requested_targets(directive: Dict[str, Any], errormap: Dict[str, Any]) -> Tuple[List[Tuple[str, str, str]], Optional[Dict[str, Any]]]:
    focus = focus_from_directive(directive) or fallback_focus_from_errormap(errormap)
    primary = focus[0] if focus else None
    req: List[Tuple[str, str, str]] = []
    for f in focus:
        cap = str(f.get("capacity_domain") or f.get("skill_tag") or "mixed_review")
        criterion = str(f.get("criterion") or DOMAIN_TO_CRITERION.get(cap, "task_response"))
        families = f.get("top_families") or []
        mapped = [FAMILY_MAP.get(str(x), str(x)) for x in families if str(x).strip()]
        if not mapped:
            mapped = CRITERION_FALLBACK_FAMILIES.get(criterion, [])
        for fam in mapped[:5]:
            req.append((cap, criterion, fam))
    return req, primary


def choose_items(bank: List[Dict[str, Any]], directive: Dict[str, Any], errormap: Dict[str, Any], target_count: int) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    rng = random.Random(20260708)
    req, primary = requested_targets(directive, errormap)
    if not req:
        req = [("mixed_review", "task_response", "CLAIM_SUPPORT")]
    selected: List[Dict[str, Any]] = []
    used = set()
    audit: List[Dict[str, Any]] = []
    # Try exact target matches first.
    for cap, criterion, family in req:
        candidates = [x for x in bank if item_key(x) not in used and matches(x, family, criterion, cap)]
        if candidates:
            rng.shuffle(candidates)
            item = dict(candidates[0])
            item["practice_focus"] = {"capacity_domain": cap, "criterion": criterion, "family": family, "match_mode": "targeted"}
            selected.append(item)
            used.add(item_key(item))
        if len(selected) >= target_count:
            break
    # Controlled fallback remains attached to the primary focus, not null/mixed-only.
    if len(selected) < target_count:
        pcap = str((primary or {}).get("capacity_domain") or "mixed_review")
        pcrit = str((primary or {}).get("criterion") or DOMAIN_TO_CRITERION.get(pcap, "task_response"))
        fallback_families = CRITERION_FALLBACK_FAMILIES.get(pcrit, [])
        for fam in fallback_families:
            candidates = [x for x in bank if item_key(x) not in used and matches(x, fam, pcrit, pcap)]
            rng.shuffle(candidates)
            for x in candidates:
                item = dict(x)
                item["practice_focus"] = {"capacity_domain": pcap, "criterion": pcrit, "family": fam, "match_mode": "criterion_fallback"}
                selected.append(item)
                used.add(item_key(item))
                audit.append({"fallback_family": fam, "criterion": pcrit, "capacity_domain": pcap})
                if len(selected) >= target_count:
                    break
            if len(selected) >= target_count:
                break
    if len(selected) < target_count:
        remaining = [x for x in bank if item_key(x) not in used]
        rng.shuffle(remaining)
        pcap = str((primary or {}).get("capacity_domain") or "mixed_review")
        pcrit = str((primary or {}).get("criterion") or "mixed")
        for x in remaining[: target_count - len(selected)]:
            item = dict(x)
            item["practice_focus"] = {"capacity_domain": pcap, "criterion": pcrit, "family": item.get("family"), "match_mode": "bank_fill"}
            selected.append(item)
            audit.append({"fallback": "bank_fill", "capacity_domain": pcap})
    return selected[:target_count], primary, audit


def compact(item: Dict[str, Any], n: int, total: int) -> Dict[str, Any]:
    focus = item.get("practice_focus") or {}
    return {
        "exercise_number": n,
        "total_exercises": total,
        "exercise_id": item_key(item),
        "criterion": focus.get("criterion") or item.get("category") or item.get("criterion"),
        "capacity_domain": focus.get("capacity_domain"),
        "family": focus.get("family") or item.get("family"),
        "match_mode": focus.get("match_mode"),
        "cefr_level": item.get("cefr") or item.get("cefr_level") or item.get("level"),
        "exercise_type": item.get("exercise_type") or item.get("type") or item.get("format"),
        "prompt": item.get("prompt") or item.get("question") or item.get("instruction") or item.get("stem"),
        "choices": item.get("choices") or item.get("options"),
        "answer_key": item.get("answer") or item.get("correct_answer") or item.get("answer_key"),
        "explanation": item.get("explanation") or item.get("rationale"),
    }


def build(directive: Dict[str, Any], errormap: Dict[str, Any], contract: Dict[str, Any], bank: List[Dict[str, Any]], target_count: int) -> Dict[str, Any]:
    selected, primary, audit = choose_items(bank, directive, errormap, target_count)
    exercises = [compact(item, i + 1, len(selected)) for i, item in enumerate(selected)]
    score = contract.get("released_score") or {}
    targeted_count = sum(1 for e in exercises if e.get("match_mode") in {"targeted", "criterion_fallback"})
    return {
        "schema_version": SCHEMA_VERSION,
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "session_id": f"practice_{uuid.uuid4().hex[:12]}",
        "student_id": contract.get("student_id") or "student_unknown",
        "essay_id": contract.get("essay_id") or "essay_unknown",
        "source_score": score,
        "primary_focus": primary,
        "exercise_count": len(exercises),
        "targeted_exercise_count": targeted_count,
        "exercises": exercises,
        "selection_audit": {
            "target_count": target_count,
            "bank_size": len(bank),
            "fallbacks_used": audit,
            "quality_flags": {
                "has_primary_focus": isinstance(primary, dict),
                "has_exercises": bool(exercises),
                "targeted_ratio": round(targeted_count / max(1, len(exercises)), 3),
            },
        },
        "boundary": "Practice session builder selects existing exercise-bank items only; it does not score, detect, or generate LRET/coaching content.",
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build a targeted Gold practice session from directive evidence.")
    ap.add_argument("--directive", required=True)
    ap.add_argument("--errormap", required=True)
    ap.add_argument("--score-contract", required=True)
    ap.add_argument("--exercise-bank", required=True)
    ap.add_argument("--output", "-o", required=True)
    ap.add_argument("--target-count", type=int, default=7)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)
    out = build(read_json(args.directive), read_json(args.errormap), read_json(args.score_contract), load_bank(args.exercise_bank), args.target_count)
    write_json(args.output, out, pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
