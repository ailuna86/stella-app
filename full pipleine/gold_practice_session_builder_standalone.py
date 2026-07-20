#!/usr/bin/env python3
"""
Gold Practice Session Builder — standalone
=========================================

Builds a concrete practice-session artifact from directive, ErrorMap, score
contract, and an exercise-bank JSONL. Imports no previous versions.

Boundary:
- This is a targeted Practice Engine file, separate from the Gold orchestrator.
- It does not score essays.
- It does not detect errors.
- It does not perform LRET or Writing Coach logic.
"""
from __future__ import annotations

import argparse
import json
import random
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = "GOLD_PRACTICE_SESSION_STANDALONE_V1"
ENGINE_ID = "VA_STELLA_GOLD_PRACTICE_SESSION_BUILDER"
ENGINE_VERSION = "1.0.0-standalone-no-imports"

DOMAIN_TO_CRITERION = {
    "sentence_control": "grammatical_range_accuracy",
    "lexical_precision": "lexical_resource",
    "academic_style": "lexical_resource",
    "argument_development": "task_response",
    "cohesion_control": "coherence_cohesion",
    "task_response_control": "task_response",
}
FAMILY_MAP = {
    "G_ARTICLE": "ARTICLE_DETERMINER",
    "G_DETERMINER": "ARTICLE_DETERMINER",
    "G_SV_AGREEMENT": "SUBJECT_VERB_AGREEMENT",
    "G_VERB_PATTERN": "VERB_FORM",
    "G_MISSING_VERB": "CLAUSE_STRUCTURE",
    "G_COMPARATIVE_FORM": "COMPARATIVES",
    "G_SPACING": "PUNCTUATION",
    "G_COMMA_TRANSITION": "PUNCTUATION",
    "L_REPETITION": "REPETITION",
    "L_LIMITED_VOCAB": "LEXICAL_PRECISION",
    "L_INFORMAL_VOCAB": "FORMALITY",
    "S_INFORMAL_TONE": "FORMALITY",
    "A_UNDERDEVELOPED": "CLAIM_SUPPORT",
    "A_OVERGENERALIZATION": "OVERGENERALIZATION",
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
    bank_path = Path(path)
    if not bank_path.exists():
        raise FileNotFoundError(str(bank_path))
    items: List[Dict[str, Any]] = []
    with bank_path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
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


def focus_from_directive(directive: Dict[str, Any]) -> List[Dict[str, Any]]:
    focus = directive.get("focus_areas") or []
    if isinstance(focus, list) and focus:
        return [f for f in focus if isinstance(f, dict)]
    primary = directive.get("primary_focus")
    return [primary] if isinstance(primary, dict) else []


def families_from_errormap(errormap: Dict[str, Any]) -> Counter:
    c = Counter()
    for e in errormap.get("errors", []) or []:
        if not isinstance(e, dict):
            continue
        fam = str(e.get("family") or "")
        if fam:
            c[FAMILY_MAP.get(fam, fam)] += 1
    return c


def item_key(item: Dict[str, Any]) -> str:
    return str(item.get("exercise_id") or item.get("id") or item.get("item_id") or f"line_{item.get('_bank_line')}")


def norm(s: Any) -> str:
    return str(s or "").strip().upper()


def matches(item: Dict[str, Any], family: str, criterion: str) -> bool:
    item_family = norm(item.get("family") or item.get("skill_family") or item.get("micro_skill"))
    item_cat = str(item.get("category") or item.get("criterion") or "").strip().lower()
    return item_family == norm(family) or item_cat == str(criterion).lower()


def choose_items(bank: List[Dict[str, Any]], directive: Dict[str, Any], errormap: Dict[str, Any], target_count: int) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    rng = random.Random(20260708)
    focus = focus_from_directive(directive)
    error_families = families_from_errormap(errormap)
    requested: List[Tuple[str, str, str]] = []
    for f in focus:
        domain = f.get("capacity_domain") or f.get("skill_tag") or "argument_development"
        criterion = f.get("criterion") or DOMAIN_TO_CRITERION.get(str(domain), "task_response")
        families = f.get("top_families") or f.get("families") or []
        mapped = [FAMILY_MAP.get(str(x), str(x)) for x in families if str(x).strip()]
        if not mapped:
            mapped = [fam for fam, _ in error_families.most_common(5)] or CRITERION_FALLBACK_FAMILIES.get(criterion, [])
        for fam in mapped[:4]:
            requested.append((str(domain), str(criterion), fam))
    if not requested:
        for fam, _ in error_families.most_common(8):
            requested.append(("mixed_review", "task_response", fam))
    rng.shuffle(requested)

    selected: List[Dict[str, Any]] = []
    used = set()
    selection_log: List[Dict[str, Any]] = []
    for domain, criterion, family in requested:
        candidates = [x for x in bank if item_key(x) not in used and matches(x, family, criterion)]
        if not candidates:
            for fallback in CRITERION_FALLBACK_FAMILIES.get(criterion, []):
                candidates = [x for x in bank if item_key(x) not in used and matches(x, fallback, criterion)]
                if candidates:
                    selection_log.append({"requested_family": family, "used_family": fallback, "reason": "criterion_family_fallback"})
                    family = fallback
                    break
        if candidates:
            rng.shuffle(candidates)
            item = dict(candidates[0])
            used.add(item_key(item))
            item["practice_focus"] = {"capacity_domain": domain, "criterion": criterion, "family": family}
            selected.append(item)
        if len(selected) >= target_count:
            break
    if len(selected) < target_count:
        remaining = [x for x in bank if item_key(x) not in used]
        rng.shuffle(remaining)
        for item in remaining[: target_count - len(selected)]:
            item = dict(item)
            item["practice_focus"] = {"capacity_domain": "mixed_review", "criterion": item.get("category") or "mixed", "family": item.get("family")}
            selected.append(item)
    return selected[:target_count], selection_log


def compact_exercise(item: Dict[str, Any], number: int, total: int) -> Dict[str, Any]:
    return {
        "exercise_number": number,
        "total_exercises": total,
        "exercise_id": item_key(item),
        "criterion": item.get("practice_focus", {}).get("criterion") or item.get("category") or item.get("criterion"),
        "capacity_domain": item.get("practice_focus", {}).get("capacity_domain"),
        "family": item.get("practice_focus", {}).get("family") or item.get("family"),
        "cefr_level": item.get("cefr") or item.get("cefr_level") or item.get("level"),
        "exercise_type": item.get("exercise_type") or item.get("type") or item.get("format"),
        "prompt": item.get("prompt") or item.get("question") or item.get("instruction") or item.get("stem"),
        "choices": item.get("choices") or item.get("options"),
        "answer_key": item.get("answer") or item.get("correct_answer") or item.get("answer_key"),
        "explanation": item.get("explanation") or item.get("rationale"),
    }


def build_session(directive: Dict[str, Any], errormap: Dict[str, Any], score_contract: Dict[str, Any], bank: List[Dict[str, Any]], target_count: int) -> Dict[str, Any]:
    selected, log = choose_items(bank, directive, errormap, target_count)
    exercises = [compact_exercise(item, i + 1, len(selected)) for i, item in enumerate(selected)]
    score = score_contract.get("released_score") or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "session_id": f"practice_{uuid.uuid4().hex[:12]}",
        "student_id": score_contract.get("student_id") or "student_unknown",
        "essay_id": score_contract.get("essay_id") or "essay_unknown",
        "source_score": score,
        "primary_focus": directive.get("primary_focus"),
        "exercise_count": len(exercises),
        "exercises": exercises,
        "selection_audit": {
            "target_count": target_count,
            "bank_size": len(bank),
            "fallbacks_used": log,
            "source": "directive_plus_errormap_plus_exercise_bank",
        },
        "boundary": "Practice session generated from upstream directive/ErrorMap and exercise bank only; no scoring or detection performed here.",
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build Gold practice session from directive/ErrorMap/exercise bank.")
    ap.add_argument("--directive", required=True)
    ap.add_argument("--errormap", required=True)
    ap.add_argument("--score-contract", required=True)
    ap.add_argument("--exercise-bank", required=True)
    ap.add_argument("--output", "-o", required=True)
    ap.add_argument("--count", type=int, default=7)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)
    session = build_session(read_json(args.directive), read_json(args.errormap), read_json(args.score_contract), load_bank(args.exercise_bank), max(1, args.count))
    write_json(args.output, session, pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
