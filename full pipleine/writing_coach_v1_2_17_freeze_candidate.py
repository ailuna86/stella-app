#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VA / ST.ELLA — Writing Coach V1.2.12 Proactive Adaptive Planner with Attempt CLI
=====================================================

Standalone, no-API, no-external-dependency implementation of a move-based Writing Coach.

Core V1.2 decisions:
- Writing Coach trains transferable writing moves, not direct correction of the original essay.
- The Micro-Writing Move Bank is external JSON, not embedded in this code.
- The student's essay is used for diagnosis, topic context, and optional noticing examples, not as the default repair worksheet.
- The engine selects: microskill -> micro-writing move -> near-transfer production/transformation mission.
- Prescription signals are separated from performance/mastery signals. Mastery update is emitted only after mission evaluation.

CLI examples:

Generate a mission:
python writing_coach_v1_2_10_freeze_candidate.py \
  --move-bank micro_writing_move_bank_simple_v1.json \
  --evaluator original_evaluator_output.json \
  --intake 00_intake_assessment.json \
  --detector 01_detector_output.json \
  --errormap 01b_errormap_v3.json \
  --score-contract 02d_final_score_contract.json \
  --priority 03_pe_output.json \
  --directive 04_directive_v2.json \
  --feedback 05_fe_output.json \
  --feedback-report 06_feedback_report_v6c.json \
  --ontology writing_competency_ontology_v3.json \
  --clusters VA_microskill_clustering_v3.json \
  --output writing_coach_v1_2_10_output.json \
  --pretty

Evaluate a student response to a generated mission:
python writing_coach_v1_2_10_freeze_candidate.py \
  --evaluate-mission writing_coach_v1_2_10_output.json \
  --student-response student_response.txt \
  --output writing_coach_v1_2_10_mission_result.json \
  --pretty
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ENGINE_ID = "WRITING_COACH"
ENGINE_VERSION = "writing_coach_v1.2.10_proactive_adaptive_cli"
OUTPUT_SCHEMA = "WRITING_COACH_OUTPUT_V1_2_10_PROACTIVE_ADAPTIVE_CLI"
MISSION_RESULT_SCHEMA = "WRITING_COACH_MISSION_RESULT_V1_2_10"

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(prefix: str, *parts: Any, n: int = 12) -> str:
    raw = "|".join(str(p) for p in parts)
    h = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:n]
    return f"{prefix}_{h}"


def clamp(x: Any, lo: float = 0.0, hi: float = 1.0, default: float = 0.0) -> float:
    try:
        f = float(x)
    except Exception:
        return default
    if math.isnan(f):
        return default
    return max(lo, min(hi, f))


def clean_text(x: Any) -> str:
    return re.sub(r"\s+", " ", str(x or "")).strip()


def safe_get(d: Any, path: str, default: Any = None) -> Any:
    cur = d
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return default
    return cur


def load_json(path: Optional[str], default: Any = None, required: bool = False) -> Any:
    if not path:
        if required:
            raise FileNotFoundError("Required JSON path was not provided.")
        return default
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"JSON input not found: {p}")
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, payload: Any, pretty: bool = False) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        if pretty:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        else:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))


def is_valid_mission_result(result: Optional[Dict[str, Any]]) -> bool:
    """Return True for a structurally valid submitted/evaluated mission result.

    V1.2.10 distinguishes STRUCTURAL validity from MASTERY validity.
    Invalid/incomplete attempts may be stored as attempt history, but they must
    not update mastery or unlock skills.
    """
    if not isinstance(result, dict) or not result:
        return False
    outcome = result.get("outcome")
    mission_id = result.get("mission_id")
    skill_id = result.get("target_skill_id") or result.get("skill_id")
    allowed = {
        "pass", "partial_pass", "fail", "invalid",
        "invalid_incomplete_output", "invalid_empty_response", "invalid_off_task"
    }
    if outcome not in allowed:
        return False
    if not mission_id or not skill_id:
        return False
    try:
        float(result.get("mission_score", result.get("score", 0.0)))
    except Exception:
        return False
    return True


def is_mastery_valid_result(result: Optional[Dict[str, Any]]) -> bool:
    """Return True only when the result can update mastery."""
    if not is_valid_mission_result(result):
        return False
    if result.get("mastery_update_allowed") is False:
        return False
    if safe_get(result, "lie_update_decision.mastery_update_allowed") is False:
        return False
    return result.get("outcome") in {"pass", "partial_pass", "fail"}

def normalize_mission_result_for_history(result: Dict[str, Any]) -> Dict[str, Any]:
    rid = result.get("result_id") or stable_id("wc_result", result.get("mission_id"), result.get("outcome"), n=12)
    mastery_allowed = is_mastery_valid_result(result)
    return {
        "result_id": rid,
        "mission_id": result.get("mission_id"),
        "skill_id": result.get("target_skill_id") or result.get("skill_id"),
        "skill_name": result.get("target_skill_name") or result.get("skill_name"),
        "move_id": safe_get(result, "selected_move.move_id") or result.get("move_id"),
        "outcome": result.get("outcome"),
        "score": result.get("mission_score", result.get("score", 0.0)),
        "confidence": result.get("confidence"),
        "critical_failure": bool(result.get("critical_failure", False)),
        "mastery_update_allowed": mastery_allowed,
        "attempt_status": result.get("attempt_status") or safe_get(result, "completion_gate.status"),
        "created_at": result.get("created_at") or now_iso(),
    }


def update_coach_state_with_result(state: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    """Append a structurally valid attempt to state and refresh active-cycle progress.

    V1.2.10: incomplete/invalid attempts may be stored for behavior history, but
    they are NOT counted as valid mastery missions and cannot unlock skills.
    """
    state = dict(state or {})
    if not is_valid_mission_result(result):
        state.setdefault("state_warnings", []).append("previous_mission_result_missing_or_invalid")
        return normalize_coach_state_contract(state)
    row = normalize_mission_result_for_history(result)
    history = [dict(h) for h in state.get("mission_history", []) if isinstance(h, dict)]
    if row["result_id"] not in {h.get("result_id") for h in history}:
        history.append(row)
    state["mission_history"] = history[-30:]
    active_skill_id = state.get("active_skill_id") or safe_get(state, "active_coaching_cycle.active_skill.skill_id")
    skill_history = [
        h for h in state["mission_history"]
        if h.get("skill_id") == active_skill_id
        and h.get("outcome") in {"pass", "partial_pass", "fail"}
        and h.get("mastery_update_allowed", True) is True
    ]
    recent = skill_history[-5:]
    valid = len(skill_history)
    avg = sum(float(h.get("score") or 0.0) for h in recent) / len(recent) if recent else 0.0
    recent3 = skill_history[-3:]
    functional = (
        len(recent3) >= 3
        and sum(1 for h in recent3 if h.get("outcome") == "pass") >= 2
        and sum(float(h.get("score") or 0.0) for h in recent3) / len(recent3) >= 0.75
        and not any(h.get("critical_failure") for h in recent3)
    )
    stable = (
        len(recent) >= 5
        and sum(1 for h in recent if h.get("outcome") == "pass") >= 4
        and avg >= 0.80
        and not any(h.get("critical_failure") for h in recent)
    )
    if isinstance(state.get("active_coaching_cycle"), dict):
        state["active_coaching_cycle"].setdefault("progress_snapshot", {})
        state["active_coaching_cycle"]["progress_snapshot"].update({
            "valid_missions_for_active_skill": valid,
            "recent_outcomes": [h.get("outcome") for h in recent],
            "recent_average": round(avg, 3),
            "functional_achieved": functional,
            "stable_achieved": stable,
            "last_result_outcome": row.get("outcome"),
            "last_attempt_mastery_update_allowed": bool(row.get("mastery_update_allowed")),
        })
        state["active_coaching_cycle"]["status"] = "stable_ready" if stable else "functional_ready" if functional else "active"
        state["active_coaching_cycle"]["last_updated_at"] = now_iso()
    return normalize_coach_state_contract(state)

def extract_mission_for_cli(payload: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Return the mission object if the payload is a generated Writing Coach mission/output.

    V1.2.10 deliberately rejects mission-result/state files before the user is
    prompted to type an answer. This prevents the confusing situation where a
    result JSON is shown as a blank generic mission and only fails after the
    student has typed a response.
    """
    if not isinstance(payload, dict):
        return None, "Input is not a JSON object."

    schema = str(payload.get("schema_version") or "")
    keys = set(payload.keys())

    if schema.startswith("WRITING_COACH_MISSION_RESULT") or {"result_id", "mission_score", "outcome"}.intersection(keys) >= {"result_id"}:
        return None, (
            "You passed a mission RESULT file, not a mission/output file. "
            "Use a generated Writing Coach output that contains 'today_mission', "
            "for example writing_coach_v1_2_10_output.json or a daily output JSON."
        )

    if schema.startswith("WRITING_COACH_STATE") or ("active_coaching_cycle" in payload and "today_mission" not in payload):
        return None, (
            "You passed a COACH STATE file, not a mission/output file. "
            "Run daily generation first to create a mission, then pass that daily output to --attempt-mission."
        )

    for key in ("today_mission", "today_micromission", "mission"):
        m = payload.get(key)
        if isinstance(m, dict) and m:
            return m, None

    # Accept a raw mission object only if it has mission-like fields.
    if any(k in payload for k in ("required_output", "stimulus", "student_instruction", "observable_units")):
        return payload, None

    return None, (
        "Mission payload does not contain today_mission/today_micromission/mission. "
        "Use a generated Writing Coach output JSON, not an attempt result or state file."
    )


def assert_mission_payload_for_cli(payload: Dict[str, Any], source_path: str = "") -> None:
    mission, error = extract_mission_for_cli(payload)
    if error:
        detail = f"\nFile: {source_path}" if source_path else ""
        raise ValueError(error + detail)


def terminal_mission_text(mission_payload: Dict[str, Any]) -> str:
    mission, error = extract_mission_for_cli(mission_payload)
    if error:
        return "ERROR: " + error + "\n"
    assert mission is not None
    title = mission.get("title", "Writing Coach Mission")
    timebox = mission.get("timebox_minutes", 10)
    goal = mission.get("student_goal") or mission.get("student_rationale") or "Complete the writing move."
    lines = [f"\n=== {title} ===", f"Time: {timebox} minutes", "", f"Goal: {goal}"]
    prompt = mission.get("source_prompt")
    if prompt:
        lines += ["", f"Topic / prompt context: {prompt}"]
    stim = mission.get("stimulus") or {}
    items = stim.get("items") or []
    if items:
        lines += ["", "Write one response for each item:"]
        for i, item in enumerate(items, 1):
            rough = item.get("rough_input") if isinstance(item, dict) else str(item)
            lines.append(f"{i}. {rough}")
    else:
        instr = mission.get("student_instruction") or "Write your response."
        lines += ["", instr]
    required = mission.get("required_output") or {}
    if required:
        lines += ["", "Required output:"]
        if required.get("required_items") is not None:
            lines.append(f"- {required.get('required_items')} item(s)")
        if required.get("line_rule"):
            lines.append(f"- {required.get('line_rule')}")
        if required.get("length_guidance"):
            lines.append(f"- {required.get('length_guidance')}")
    checklist = mission.get("success_checklist") or []
    if checklist:
        lines += ["", "Success checklist:"]
        for c in checklist:
            lines.append(f"- {c}")
    return "\n".join(lines).strip() + "\n"


def read_interactive_response() -> str:
    print("\nType your answer below. Press Enter on an empty line when finished.")
    print("You may also type END, DONE, SUBMIT, or STOP on a separate line to finish.\n")
    buf: List[str] = []
    stop_tokens = {"end", "done", "submit", "stop", "finish"}
    while True:
        try:
            line = input()
        except EOFError:
            break
        raw = line.strip()
        if not raw:
            break
        if raw.lower() in stop_tokens:
            break
        # Learners sometimes type ENTER literally after END/submit instructions.
        # Treat a standalone ENTER as a UI control token, not as an answer.
        if raw.lower() == "enter":
            continue
        buf.append(line)
    return "\n".join(buf).strip()

def as_list(x: Any) -> List[Any]:
    if x is None:
        return []
    if isinstance(x, list):
        return x
    return [x]


def words(text: str) -> List[str]:
    return re.findall(r"[A-Za-z][A-Za-z'\-]*", text.lower())


def _simple_stem(token: str) -> str:
    t = token.lower().strip("'’")
    for suf in ("ing", "ers", "er", "ed", "s"):
        if len(t) > 4 and t.endswith(suf):
            return t[: -len(suf)]
    return t


def _content_terms(text: str) -> List[str]:
    stop = {
        "the", "a", "an", "and", "or", "to", "of", "for", "with", "by", "in", "on", "at", "from",
        "there", "are", "is", "be", "can", "could", "might", "may", "should", "would", "will", "many",
        "people", "older", "old", "more", "less", "fewer", "when", "because", "so", "this", "that"
    }
    return [_simple_stem(w) for w in words(text) if _simple_stem(w) not in stop]


def _semantic_match_score(sentence: str, rough_input: str) -> float:
    sw = set(_content_terms(sentence))
    rw = set(_content_terms(rough_input))
    if not sw or not rw:
        return 0.0
    score = 0.0
    high_value = {
        "healthcare", "government", "pay", "spend", "retire", "worker", "work", "economy", "slow",
        "grandparent", "parent", "child", "care", "tradition", "culture", "generation", "experience", "advice", "society"
    }
    for term in sw & rw:
        score += 2.5 if term in high_value else 1.0
    # synonym / morphology bridges used in this mission family
    bridges = [
        ({"work", "worker", "workforce", "working"}, {"worker", "work", "economy"}),
        ({"child", "children"}, {"child", "children"}),
        ({"spend", "pay", "cost", "money"}, {"pay", "spend", "government"}),
        ({"teach", "preserve", "tradition", "culture"}, {"tradition", "generation"}),
    ]
    sent_lower = sentence.lower()
    rough_lower = rough_input.lower()
    for left, right in bridges:
        if any(x in sent_lower for x in left) and any(y in rough_lower for y in right):
            score += 1.5
    return score


def parse_response_items(text: str, required_items: int = 1, stimulus_items: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Parse a student's numbered/unnumbered mission response.

    V1.2.10 keeps explicit numbering and improves unnumbered matching.
    - "3. ..." maps to item 3.
    - An unnumbered sentence is semantically matched to the rough-input item when possible.
    - END/DONE/SUBMIT/STOP/ENTER control tokens are ignored.
    """
    required_items = max(1, int(required_items or 1))
    control_tokens = {"end", "done", "submit", "stop", "finish", "enter"}
    raw_lines = []
    for ln in str(text or "").splitlines():
        raw = ln.strip()
        if not raw:
            continue
        if raw.lower() in control_tokens:
            # END should behave as terminal terminator; subsequent literal ENTER is also ignored.
            if raw.lower() in {"end", "done", "submit", "stop", "finish"}:
                break
            continue
        raw_lines.append(raw)

    # If the response was pasted as one inline numbered string, split it.
    # Use the already filtered raw_lines, not the original text, so control tokens
    # such as END/DONE are not accidentally absorbed into the last answer.
    filtered_text = "\n".join(raw_lines)
    if len(raw_lines) <= 1 and re.search(r"\b\d+[.)]\s+", filtered_text):
        parts = re.split(r"(?=\b\d+[.)]\s+)", filtered_text)
        raw_lines = []
        for p in parts:
            p = p.strip()
            if not p:
                continue
            first = p.splitlines()[0].strip().lower()
            if first in control_tokens:
                break
            raw_lines.append(p)

    numbered: Dict[int, Dict[str, Any]] = {}
    overflow: List[Dict[str, Any]] = []
    unnumbered: List[str] = []
    warnings: List[str] = []

    for raw in raw_lines:
        m = re.match(r"^\s*(\d+)[.)]\s*(.+?)\s*$", raw)
        if m:
            num = int(m.group(1))
            sent = clean_text(m.group(2))
            if not sent:
                continue
            entry = {"item_number": num, "text": sent, "raw_line": raw, "explicit_number": True, "assignment_reason": "explicit_number"}
            if num < 1 or num > required_items:
                overflow.append(entry)
                warnings.append(f"item_number_out_of_range:{num}")
            elif num in numbered:
                overflow.append(entry)
                warnings.append(f"duplicate_item_number:{num}")
            else:
                numbered[num] = entry
        else:
            sent = re.sub(r"^\s*(?:[-*•])\s*", "", raw).strip()
            if sent:
                unnumbered.append(clean_text(sent))

    stimulus_items = stimulus_items or []
    missing_slots = [i for i in range(1, required_items + 1) if i not in numbered]
    for sent in unnumbered:
        if not missing_slots:
            overflow.append({"item_number": None, "text": sent, "raw_line": sent, "explicit_number": False})
            warnings.append("extra_unnumbered_response_beyond_required_items")
            continue
        # Try semantic assignment first.
        best_num = None
        best_score = 0.0
        for num in missing_slots:
            rough = ""
            if num - 1 < len(stimulus_items) and isinstance(stimulus_items[num - 1], dict):
                rough = str(stimulus_items[num - 1].get("rough_input") or "")
            score = _semantic_match_score(sent, rough)
            if score > best_score:
                best_score = score
                best_num = num
        if best_num is not None and best_score >= 2.0:
            num = best_num
            warnings.append("unnumbered_response_semantically_matched")
            reason = f"semantic_match_score:{best_score:.2f}"
        else:
            num = missing_slots[0]
            warnings.append("unnumbered_response_assigned_sequentially")
            reason = "sequential_missing_slot"
        numbered[num] = {"item_number": num, "text": sent, "raw_line": sent, "explicit_number": False, "assignment_reason": reason}
        missing_slots = [i for i in range(1, required_items + 1) if i not in numbered]

    submitted_numbers = sorted(numbered)
    missing_numbers = [i for i in range(1, required_items + 1) if i not in numbered]
    if submitted_numbers and submitted_numbers != list(range(1, len(submitted_numbers) + 1)):
        warnings.append("numbering_skips_or_starts_late")
    if missing_numbers:
        warnings.append("missing_required_numbered_items")
    if any(not e.get("explicit_number") for e in numbered.values()):
        warnings.append("some_responses_without_explicit_numbering")

    submitted_items = [numbered[i] for i in submitted_numbers]
    return {
        "submitted_items": submitted_items,
        "submitted_by_number": numbered,
        "submitted_item_numbers": submitted_numbers,
        "missing_item_numbers": missing_numbers,
        "overflow_items": overflow,
        "numbering_warnings": sorted(set(warnings)),
        "submitted_count": len(submitted_items),
    }

def split_lines(text: str) -> List[str]:
    # Backwards-compatible helper: returns only sentence text, without item numbers.
    parsed = parse_response_items(text, required_items=999)
    return [x["text"] for x in parsed.get("submitted_items", [])]


def contains_any(text: str, needles: Iterable[str]) -> bool:
    t = text.lower()
    return any(n.lower() in t for n in needles)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class EvidenceSignal:
    skill_id: str
    skill_name: str = ""
    domain: str = ""
    source: str = "unknown"
    pressure: float = 0.0
    confidence: float = 0.5
    mastery_estimate: Optional[float] = None
    evidence_count: int = 1
    evidence_ids: List[str] = field(default_factory=list)
    families: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    status: str = "observed"
    evaluation_bucket: Optional[str] = None


@dataclass
class SkillCandidate:
    skill_id: str
    skill_name: str
    domain: str
    mastery_estimate: float
    selection_score: float
    priority_score: float
    pressure: float
    confidence: float
    evidence_count: int
    evaluation_bucket: str
    sources: List[str]
    evidence_ids: List[str]
    families: List[str]
    reasons: List[str]
    prerequisites: List[str] = field(default_factory=list)
    dependency_ready: bool = True
    blocking_prerequisites: List[str] = field(default_factory=list)
    recommended_difficulty: str = "controlled"
    recommended_duration: int = 10


@dataclass
class MoveCandidate:
    move_id: str
    move_name: str
    primary_microskill: str
    move_family: str
    selection_score: float
    fit_score: float
    difficulty_fit: float
    source_policy_ok: bool
    target_skill_role_in_move: str
    target_skill_role_explanation: str
    reason: str


# ---------------------------------------------------------------------------
# Resource loading and validation
# ---------------------------------------------------------------------------


class MoveBank:
    """External Micro-Writing Move Bank loader/validator."""

    REQUIRED_TEMPLATE_FIELDS = {
        "move_id",
        "move_name",
        "primary_microskill",
        "move_family",
        "training_mode",
        "student_goal",
        "default_timebox_minutes",
        "observable_units",
        "mission_blueprint",
    }

    def __init__(self, payload: Dict[str, Any]):
        self.payload = payload or {}
        self.schema_version = self.payload.get("schema_version", "UNKNOWN")
        self.bank_id = self.payload.get("bank_id", "UNKNOWN_MOVE_BANK")
        self.moves = self.payload.get("moves", [])
        self.index_by_skill: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self.index_by_move: Dict[str, Dict[str, Any]] = {}
        self.warnings: List[str] = []
        self.errors: List[str] = []
        self._validate_and_index()

    @classmethod
    def from_path(cls, path: str) -> "MoveBank":
        return cls(load_json(path, required=True))

    def _validate_and_index(self) -> None:
        if not isinstance(self.moves, list) or not self.moves:
            self.errors.append("Move bank must contain a non-empty 'moves' list.")
            return
        for i, move in enumerate(self.moves):
            if not isinstance(move, dict):
                self.errors.append(f"Move at index {i} is not an object.")
                continue
            missing = sorted(self.REQUIRED_TEMPLATE_FIELDS - set(move.keys()))
            if missing:
                self.errors.append(f"Move {move.get('move_id', i)} missing fields: {missing}")
                continue
            skill = move.get("primary_microskill")
            move_id = move.get("move_id")
            if move_id in self.index_by_move:
                self.errors.append(f"Duplicate move_id: {move_id}")
                continue
            if not isinstance(move.get("observable_units"), list) or not move.get("observable_units"):
                self.errors.append(f"Move {move_id} must contain non-empty observable_units.")
                continue
            self.index_by_skill[skill].append(move)
            self.index_by_move[move_id] = move
            for s in move.get("secondary_microskills", []) or []:
                self.index_by_skill[s].append(move)
        bank_label = f"{self.bank_id} {self.schema_version}".lower()
        if len(self.moves) < 40:
            self.warnings.append("move_bank_dev_size_only_not_production_ready_minimum_recommended_40_moves")
        if any(token in bank_label for token in ["simple", "dev", "test", "demo"]):
            self.warnings.append("move_bank_marked_as_development_or_simple_bank")

    def get_for_skill(self, skill_id: str) -> List[Dict[str, Any]]:
        return list(self.index_by_skill.get(skill_id, []))

    def get(self, move_id: str) -> Optional[Dict[str, Any]]:
        return self.index_by_move.get(move_id)

    def validate_or_raise(self) -> None:
        if self.errors:
            raise ValueError("Invalid move bank:\n" + "\n".join(f"- {e}" for e in self.errors))


SKILL_DOMAIN_OVERRIDES = {
    # Grammar / sentence production
    "verb_form_control": "Grammar Production",
    "agreement_control": "Grammar Production",
    "tense_control": "Grammar Production",
    "simple_sentence_construction": "Grammar Production",
    "comparison_structure_control": "Grammar Production",
    "article_control": "Grammar Production",
    "noun_form_control": "Grammar Production",
    "sentence_boundary_control": "Grammar Production",
    "complex_sentence_construction": "Grammar Production",
    "compound_sentence_construction": "Grammar Production",
    "punctuation_control": "Grammar Production",
    "sentence_variety": "Grammar Production",
    "meaning_recovery": "Grammar Production",
    # Lexical control
    "lexical_precision": "Lexical Control",
    "semantic_compatibility": "Lexical Control",
    "word_form_control": "Lexical Control",
    "spelling_control": "Lexical Control",
    "academic_register_control": "Lexical Control",
    "lx_predicate_argument_compatibility": "Lexical Control",
    "lx_semantic_accuracy": "Lexical Control",
    "paraphrasing_ability": "Lexical Control",
    # Argumentation
    "arg_claim_generation": "Argumentation",
    "arg_claim_specificity": "Argumentation",
    "arg_claim_precision": "Argumentation",
    "arg_reason_generation": "Argumentation",
    "arg_reasoning_chain_completeness": "Argumentation",
    "arg_support_generation": "Argumentation",
    "arg_support_alignment": "Argumentation",
    "arg_example_generation": "Argumentation",
    "arg_example_specificity": "Argumentation",
    "generate_explanations": "Argumentation",
    # Organization / cohesion
    "paragraph_planning": "Organization",
    "topic_sentence_control": "Organization",
    "logical_sequencing": "Cohesion",
    "transition_control": "Cohesion",
    "reference_management": "Cohesion",
    "example_integration": "Cohesion",
    # Task response
    "identify_task_type": "Task Response",
    "identify_purpose": "Task Response",
    "identify_required_components": "Task Response",
    "maintain_task_focus": "Task Response",
    "thesis_construction": "Task Response",
}

def infer_domain_from_skill_id(skill_id: str) -> str:
    sid = str(skill_id or "")
    if sid in SKILL_DOMAIN_OVERRIDES:
        return SKILL_DOMAIN_OVERRIDES[sid]
    if sid.startswith(("arg_", "claim_", "reason_")):
        return "Argumentation"
    if any(k in sid for k in ["verb", "sentence", "clause", "article", "agreement", "tense", "punctuation", "comparison"]):
        return "Grammar Production"
    if any(k in sid for k in ["lexical", "semantic", "collocation", "word", "spelling", "register", "paraphrase"]):
        return "Lexical Control"
    if any(k in sid for k in ["paragraph", "topic_sentence", "transition", "reference", "cohesion", "sequencing"]):
        return "Organization"
    if any(k in sid for k in ["task", "purpose", "prompt", "thesis"]):
        return "Task Response"
    return "unknown"


class OntologyResources:
    def __init__(self, ontology: Dict[str, Any], clusters: Dict[str, Any]):
        self.ontology = ontology or {}
        self.clusters = clusters or {}
        self.skill_meta: Dict[str, Dict[str, Any]] = {}
        self.cluster_meta: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        for rec in self.ontology.get("micro_skills", []) or []:
            sid = rec.get("skill_id")
            if sid:
                self.skill_meta[sid] = rec
        for rec in self.clusters.get("micro_skill_records", []) or []:
            sid = rec.get("skill_id")
            if sid:
                self.cluster_meta[sid] = rec

    def name(self, skill_id: str) -> str:
        return self.skill_meta.get(skill_id, {}).get("skill_name") or self.cluster_meta.get(skill_id, {}).get("skill_name") or prettify_skill(skill_id)

    def domain(self, skill_id: str) -> str:
        raw = self.skill_meta.get(skill_id, {}).get("macro_domain") or self.cluster_meta.get(skill_id, {}).get("macro_domain") or "unknown"
        # If raw is a code, try to expand from macro_domains.
        for d in self.ontology.get("macro_domains", []) or []:
            if d.get("domain_id") == raw or d.get("id") == raw:
                expanded = d.get("domain_name") or d.get("name") or raw
                return expanded if expanded and expanded != "unknown" else infer_domain_from_skill_id(skill_id)
        if not raw or str(raw).lower() == "unknown":
            return infer_domain_from_skill_id(skill_id)
        return raw

    def bucket(self, skill_id: str) -> str:
        return (
            self.cluster_meta.get(skill_id, {}).get("primary_evaluation_bucket")
            or self.skill_meta.get(skill_id, {}).get("primary_evaluation_bucket")
            or "hybrid_single_essay_plus_multi_essay_tracking"
        )

    def dependencies(self, skill_id: str) -> List[str]:
        deps = self.skill_meta.get(skill_id, {}).get("dependencies") or []
        if isinstance(deps, list):
            return [str(x) for x in deps if str(x)]
        return []


# ---------------------------------------------------------------------------
# Skill mapping and evidence extraction
# ---------------------------------------------------------------------------


STATE_SCHEMA = "WRITING_COACH_STATE_V1_2_10"


def normalize_coach_state_contract(state: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize every state output path to the V1.2.10 contract."""
    if not isinstance(state, dict):
        return state
    state["schema_version"] = STATE_SCHEMA
    cycle = state.get("active_coaching_cycle")
    if isinstance(cycle, dict):
        active = cycle.get("active_skill")
        if isinstance(active, dict):
            raw_status = active.get("current_status")
            # Fix legacy states where domain was accidentally written into current_status.
            known_domains = {"Grammar Production", "Lexical Control", "Argumentation", "Organization", "Cohesion", "Academic Style"}
            if raw_status in known_domains:
                active["domain"] = raw_status
                active["current_status"] = "developing"
            active.setdefault("domain", state.get("target_domain") or "unknown")
            if active.get("current_status") not in {"unknown", "weak", "developing", "practicing", "emerging", "functional", "stable", "mastered", "regressing", "active", "functional_ready", "stable_ready"}:
                active["current_status"] = "developing"
        cycle["active_skill"] = active
        # Same normalization inside nested queue/state if needed.
        state["active_coaching_cycle"] = cycle
    return state


def prettify_skill(skill_id: str) -> str:
    return " ".join(w.capitalize() for w in re.split(r"[_\-]+", str(skill_id)))


FAMILY_TO_SKILLS = {
    "VERB_FORM": ["verb_form_control", "simple_sentence_construction"],
    "VERB_PATTERN": ["verb_form_control", "simple_sentence_construction"],
    "SUBJECT_VERB_AGREEMENT": ["agreement_control", "simple_sentence_construction"],
    "SV_AGREEMENT": ["agreement_control", "simple_sentence_construction"],
    "CLAUSE_STRUCTURE": ["simple_sentence_construction", "meaning_recovery"],
    "CONSTRUCTION": ["simple_sentence_construction"],
    "FRAGMENT": ["simple_sentence_construction"],
    "RUN_ON": ["sentence_boundary_control"],
    "COMPARATIVE_FORM": ["comparison_structure_control"],
    "ARTICLE_DETERMINER": ["article_control"],
    "NOUN_NUMBER_COUNTABILITY": ["article_control"],
    "WORD_FORM": ["word_form_control", "lexical_precision"],
    "SPELLING": ["spelling_control"],
    "WORD_CHOICE": ["lexical_precision", "semantic_compatibility"],
    "COLLOCATION": ["semantic_compatibility", "lexical_precision"],
    "LEXICAL_PRECISION": ["lexical_precision"],
    "PRECISION": ["lexical_precision"],
    "SEMANTIC_COMBINATION": ["semantic_compatibility"],
    "REPETITION": ["lexical_precision"],
    "REGISTER": ["academic_register_control"],
    "TASK_COMPLETENESS": ["identify_required_components"],
    "PROMPT_COVERAGE": ["maintain_task_focus", "identify_required_components"],
    "PROMPT_RELEVANCE": ["maintain_task_focus"],
    "PARAGRAPH_STRUCTURE": ["paragraph_planning", "topic_sentence_control"],
    "TOPIC_CONTINUITY": ["logical_sequencing"],
    "TRANSITION": ["transition_control"],
    "MISSING_TRANSITION": ["transition_control"],
    "REFERENCE_COHESION": ["reference_management"],
    "REFERENCE_BREAK": ["reference_management"],
    "REASONING_CHAIN": ["arg_reasoning_chain_completeness"],
    "INCOMPLETE_ARGUMENT": ["arg_reasoning_chain_completeness"],
    "UNSUPPORTED_CLAIM": ["arg_support_generation", "arg_reason_generation"],
    "WEAK_EXAMPLE": ["example_integration", "arg_example_specificity"],
    "CLAIM_SUPPORT_LINK": ["arg_support_alignment"],
    "POSITION_CLARITY": ["arg_claim_generation"],
}

DIRECTIVE_TAG_TO_SKILL = {
    "LEXICAL_CONTROL": "lexical_precision",
    "COMPARATIVE_FORM": "comparison_structure_control",
    "TASK_COMPLETENESS": "identify_required_components",
    "PARAGRAPH_STRUCTURE": "paragraph_planning",
    "GRAMMAR_CONTROL": "simple_sentence_construction",
    "VERB_FORM_PATTERN_CONTROL": "verb_form_control",
    "QUANTITY_EXPRESSIONS": "comparison_structure_control",
}

# Hand-authored dependency overrides for common universal skills.
# This is universal skill dependency logic, not essay-specific logic.
DEPENDENCY_OVERRIDES = {
    "comparison_structure_control": ["simple_sentence_construction"],
    "lexical_precision": ["semantic_compatibility"],
    "arg_reasoning_chain_completeness": ["arg_claim_generation"],
    "arg_support_generation": ["arg_claim_generation", "arg_reason_generation"],
    "example_integration": ["arg_claim_generation", "arg_reason_generation"],
    "topic_sentence_control": ["paragraph_planning"],
}


def family_to_skills(family: str) -> List[str]:
    fam = str(family or "").upper().replace("G_", "").replace("L_", "").replace("A_", "").replace("C_", "")
    return FAMILY_TO_SKILLS.get(fam, [])


class EvidenceExtractor:
    def __init__(self, resources: OntologyResources):
        self.resources = resources

    def extract(self, inputs: Dict[str, Any]) -> List[EvidenceSignal]:
        signals: List[EvidenceSignal] = []
        signals.extend(self._from_evaluator(inputs.get("evaluator") or {}))
        signals.extend(self._from_priority(inputs.get("priority") or {}))
        signals.extend(self._from_directive(inputs.get("directive") or {}))
        signals.extend(self._from_errormap(inputs.get("errormap") or {}))
        signals.extend(self._from_feedback(inputs.get("feedback") or {}))
        signals.extend(self._from_feedback_report(inputs.get("feedback_report") or {}))
        return signals

    def _sig(self, skill_id: str, source: str, pressure: float, confidence: float, **kw: Any) -> EvidenceSignal:
        evaluation_bucket = kw.pop("evaluation_bucket", None) or self.resources.bucket(skill_id)
        return EvidenceSignal(
            skill_id=skill_id,
            skill_name=self.resources.name(skill_id),
            domain=self.resources.domain(skill_id),
            source=source,
            pressure=clamp(pressure),
            confidence=clamp(confidence, default=0.5),
            evaluation_bucket=evaluation_bucket,
            **kw,
        )

    def _from_evaluator(self, evaluator: Dict[str, Any]) -> List[EvidenceSignal]:
        out: List[EvidenceSignal] = []
        for rec in evaluator.get("skill_observation_profile", []) or []:
            sid = rec.get("skill_id")
            if not sid:
                continue
            status = rec.get("status") or "observed"
            if status in {"not_applicable_to_task_type", "not_observed"}:
                # Only slot-only/not observed may become diagnostic with low confidence.
                if rec.get("skill_signal") not in {"gap", "tracking_needed"}:
                    continue
            pidx = clamp(rec.get("priority_index", 0.0), 0, 1, 0)
            conf = clamp(rec.get("diagnostic_confidence", 0.45), 0, 1, 0.45)
            # Compute mastery estimate from competence_vector when available.
            mastery = None
            vec = rec.get("competence_vector") or {}
            vals = []
            for k, v in vec.items():
                if isinstance(v, (int, float)):
                    if "reader_effort" in k or "error" in k:
                        vals.append(1 - clamp(v))
                    else:
                        vals.append(clamp(v))
            if vals:
                mastery = sum(vals) / len(vals)
            # If observed_low_evidence/gap, increase pressure.
            status_pressure = {
                "observed_low_evidence": 0.48,
                "observed_slot_only": 0.46,
                "not_observed": 0.42,
                "observed": 0.18,
            }.get(status, 0.25)
            pressure = max(pidx, status_pressure if (mastery is None or mastery < 0.62) else pidx)
            out.append(self._sig(
                sid,
                "evaluator",
                pressure,
                conf,
                mastery_estimate=mastery,
                evidence_count=len(rec.get("evidence_ids") or []),
                evidence_ids=list(rec.get("evidence_ids") or []),
                notes=[clean_text(rec.get("diagnostic_note")), clean_text(rec.get("llm_depth_note"))],
                status=status,
                evaluation_bucket=rec.get("evaluation_bucket") or self.resources.bucket(sid),
            ))
        return out

    def _from_priority(self, priority: Dict[str, Any]) -> List[EvidenceSignal]:
        out: List[EvidenceSignal] = []
        results = priority.get("results") or []
        if not results:
            return out
        r = results[0]
        primary = r.get("primary_limiter") or {}
        if primary:
            # Use exact skill if available, plus family mapped skills.
            skill = primary.get("skill")
            if skill:
                mapped = DIRECTIVE_TAG_TO_SKILL.get(skill, skill.lower())
                out.append(self._sig(
                    mapped,
                    "priority_engine.primary_limiter",
                    pressure=min(1.0, float(primary.get("pressure") or 0) / 9.0),
                    confidence=clamp(safe_get(primary, "confidence_envelope.mean_evidence_confidence", 0.7), default=0.7),
                    evidence_count=len(primary.get("evidence") or []),
                    evidence_ids=[e.get("row_id") for e in primary.get("evidence") or [] if e.get("row_id")],
                    families=[x.get("family") for x in primary.get("dominant_families") or [] if x.get("family")],
                    notes=[clean_text(primary.get("reason"))],
                ))
            for famrec in primary.get("dominant_families") or []:
                fam = famrec.get("family")
                count = famrec.get("count") or 1
                for sid in family_to_skills(fam):
                    out.append(self._sig(
                        sid,
                        "priority_engine.family_map",
                        pressure=clamp(0.22 + 0.12 * float(count), 0, 1),
                        confidence=clamp(safe_get(primary, "confidence_envelope.mean_evidence_confidence", 0.7), default=0.7),
                        evidence_count=int(count),
                        families=[fam],
                        notes=[f"Dominant family from Priority Engine: {fam}×{count}"],
                    ))
        for sp in r.get("skill_profiles") or []:
            sid = DIRECTIVE_TAG_TO_SKILL.get(sp.get("skill"), str(sp.get("skill", "")).lower())
            if sid:
                out.append(self._sig(
                    sid,
                    "priority_engine.skill_profile",
                    pressure=clamp(float(sp.get("pressure") or 0) / 9.0),
                    confidence=clamp(sp.get("confidence", 0.55), default=0.55),
                    notes=[clean_text(sp.get("reason"))],
                    evidence_count=len(sp.get("evidence") or []),
                ))
        return out

    def _from_directive(self, directive: Dict[str, Any]) -> List[EvidenceSignal]:
        out: List[EvidenceSignal] = []
        for fa in directive.get("focus_areas") or []:
            tag = fa.get("skill_tag") or fa.get("criterion")
            sid = DIRECTIVE_TAG_TO_SKILL.get(tag, str(tag or "").lower())
            rank = int(fa.get("rank") or 9)
            base_pressure = max(0.2, 1.0 - (rank - 1) * 0.14)
            # Recurrence makes it more important but not necessarily the exact next prerequisite.
            sessions = fa.get("sessions_flagged") or 0
            recurrence = 0.08 if sessions and int(sessions) >= 3 else 0.0
            out.append(self._sig(
                sid,
                "directive.focus_area",
                pressure=clamp(base_pressure + recurrence),
                confidence=0.68,
                evidence_count=1,
                families=list(fa.get("dominant_families") or []),
                notes=[clean_text(fa.get("profile_note")), clean_text(fa.get("priority_reason"))],
            ))
            for fam in fa.get("dominant_families") or []:
                for msid in family_to_skills(fam):
                    out.append(self._sig(
                        msid,
                        "directive.family_map",
                        pressure=clamp(base_pressure * 0.7),
                        confidence=0.65,
                        evidence_count=1,
                        families=[fam],
                        notes=[f"Directive dominant family: {fam}"],
                    ))
        return out

    def _from_errormap(self, errormap: Dict[str, Any]) -> List[EvidenceSignal]:
        out: List[EvidenceSignal] = []
        counts: Counter[str] = Counter()
        row_ids: Dict[str, List[str]] = defaultdict(list)
        notes: Dict[str, List[str]] = defaultdict(list)
        for e in errormap.get("errors") or []:
            fam = e.get("error_type") or e.get("family")
            if not fam:
                continue
            counts[fam] += 1
            if e.get("error_id"):
                row_ids[fam].append(e["error_id"])
            loc = e.get("location") or {}
            q = clean_text(loc.get("excerpt") or e.get("quote") or "")
            if q:
                notes[fam].append(q)
        for fam, cnt in counts.items():
            for sid in family_to_skills(fam):
                out.append(self._sig(
                    sid,
                    "detector_or_errormap",
                    pressure=clamp(0.22 + 0.11 * cnt),
                    confidence=0.74,
                    evidence_count=cnt,
                    evidence_ids=row_ids.get(fam, []),
                    families=[fam],
                    notes=[f"{cnt} detector/errormap row(s) mapped from {fam}"] + notes.get(fam, [])[:3],
                ))
        return out

    def _from_feedback(self, feedback: Dict[str, Any]) -> List[EvidenceSignal]:
        out: List[EvidenceSignal] = []
        bundles = feedback.get("bundles") or []
        if not bundles:
            return out
        sf = (bundles[0].get("student_feedback") or {}) if isinstance(bundles[0], dict) else {}
        for p in sf.get("top_learning_priorities") or []:
            tid = p.get("target_id") or ""
            sid = DIRECTIVE_TAG_TO_SKILL.get(tid, None)
            # fallback by family in examples
            families = []
            for ex in p.get("examples") or []:
                if ex.get("family"):
                    families.append(ex["family"])
            if not sid and families:
                mapped = []
                for fam in families:
                    mapped.extend(family_to_skills(fam))
                sid = mapped[0] if mapped else None
            if sid:
                rank = int(p.get("priority_number") or 9)
                out.append(self._sig(
                    sid,
                    "feedback.learning_priority",
                    pressure=clamp(0.82 - 0.1 * (rank - 1)),
                    confidence=0.66,
                    evidence_count=max(1, len(p.get("examples") or [])),
                    families=families,
                    notes=[clean_text(p.get("why_this_matters")), clean_text(p.get("practice_focus"))],
                ))
        return out

    def _from_feedback_report(self, report: Dict[str, Any]) -> List[EvidenceSignal]:
        out: List[EvidenceSignal] = []
        for fa in report.get("focus_area_feedback") or []:
            tag = fa.get("skill_tag") or fa.get("criterion")
            sid = DIRECTIVE_TAG_TO_SKILL.get(tag, None)
            families = []
            for e in fa.get("annotated_errors") or []:
                if e.get("family"):
                    families.append(e["family"])
            if not sid and families:
                mapped = []
                for fam in families:
                    mapped.extend(family_to_skills(fam))
                sid = mapped[0] if mapped else None
            if sid:
                rank = int(fa.get("rank") or 9)
                out.append(self._sig(
                    sid,
                    "feedback_report.focus_area",
                    pressure=clamp(0.78 - 0.1 * (rank - 1)),
                    confidence=0.64,
                    evidence_count=max(1, len(fa.get("annotated_errors") or [])),
                    families=families,
                    notes=[clean_text(fa.get("summary")), clean_text(fa.get("improvement_tip"))],
                ))
        return out


# ---------------------------------------------------------------------------
# Candidate ranking
# ---------------------------------------------------------------------------


class SkillSelector:
    def __init__(self, resources: OntologyResources, move_bank: MoveBank):
        self.resources = resources
        self.move_bank = move_bank

    def build_candidates(self, signals: List[EvidenceSignal], timebox: int = 10) -> List[SkillCandidate]:
        grouped: Dict[str, List[EvidenceSignal]] = defaultdict(list)
        for s in signals:
            if s.skill_id:
                grouped[s.skill_id].append(s)

        candidates: List[SkillCandidate] = []
        for sid, ss in list(grouped.items()):
            # We only select skills with at least one available move, but prereq dependencies can also become candidates
            # if they have move support and downstream pressure.
            pressure = clamp(sum(s.pressure * max(1, s.evidence_count) for s in ss) / max(1, sum(max(1, s.evidence_count) for s in ss)))
            confidence = clamp(sum(s.confidence for s in ss) / len(ss), default=0.5)
            evidence_count = sum(max(1, s.evidence_count) for s in ss)
            mastery_values = [s.mastery_estimate for s in ss if s.mastery_estimate is not None]
            if mastery_values:
                mastery = clamp(sum(mastery_values) / len(mastery_values), default=0.5)
            else:
                # Higher pressure implies lower mastery; not a score, just a learning-state estimate.
                mastery = clamp(0.72 - 0.42 * pressure - 0.015 * min(evidence_count, 10), default=0.5)
            mastery_gap = 1.0 - mastery
            bucket = self.resources.bucket(sid)
            bucket_weight = {
                "single_essay_observable": 0.95,
                "hybrid_single_essay_plus_multi_essay_tracking": 0.90,
                "hybrid_essay_observable_plus_practice_required": 0.88,
                "practice_exercise_required": 0.72,
            }.get(bucket, 0.85)
            priority_score = clamp(0.56 * pressure + 0.28 * mastery_gap + 0.16 * confidence)

            # Move availability strongly affects trainability; no external move means not selectable.
            move_available = bool(self.move_bank.get_for_skill(sid))
            trainability = 1.0 if move_available else 0.0
            selection_score = clamp(
                0.34 * priority_score
                + 0.22 * mastery_gap
                + 0.16 * pressure
                + 0.10 * confidence
                + 0.10 * bucket_weight
                + 0.08 * trainability
            )
            if not move_available:
                selection_score *= 0.35

            deps = DEPENDENCY_OVERRIDES.get(sid, []) or self.resources.dependencies(sid)
            blocking = self._blocking_prereqs(sid, deps, grouped)
            dependency_ready = not blocking
            if not dependency_ready:
                selection_score *= 0.72
                # Push prerequisite candidates up using downstream pressure.
                for dep in blocking:
                    if self.move_bank.get_for_skill(dep):
                        grouped.setdefault(dep, [])
                        dep_signal = EvidenceSignal(
                            skill_id=dep,
                            skill_name=self.resources.name(dep),
                            domain=self.resources.domain(dep),
                            source=f"dependency_from:{sid}",
                            pressure=max(pressure * 0.88, 0.55),
                            confidence=min(confidence, 0.74),
                            mastery_estimate=None,
                            evidence_count=evidence_count,
                            families=[f for s in ss for f in s.families],
                            notes=[f"Prerequisite for {sid} ({self.resources.name(sid)})"],
                            evaluation_bucket=self.resources.bucket(dep),
                        )
                        grouped[dep].append(dep_signal)

            c = SkillCandidate(
                skill_id=sid,
                skill_name=self.resources.name(sid),
                domain=self.resources.domain(sid),
                mastery_estimate=round(mastery, 3),
                selection_score=round(selection_score, 3),
                priority_score=round(priority_score, 3),
                pressure=round(pressure, 3),
                confidence=round(confidence, 3),
                evidence_count=evidence_count,
                evaluation_bucket=bucket,
                sources=sorted({s.source for s in ss}),
                evidence_ids=sorted({eid for s in ss for eid in s.evidence_ids if eid}),
                families=sorted({f for s in ss for f in s.families if f}),
                reasons=self._summarize_reasons(ss, priority_score, mastery_gap, bucket, move_available, blocking),
                prerequisites=deps,
                dependency_ready=dependency_ready,
                blocking_prerequisites=blocking,
                recommended_difficulty=self._difficulty(mastery, confidence),
                recommended_duration=timebox,
            )
            candidates.append(c)

        # Second pass: include newly injected prerequisite grouped signals that were not processed.
        seen = {c.skill_id for c in candidates}
        for sid, ss in list(grouped.items()):
            if sid not in seen:
                pressure = clamp(sum(s.pressure for s in ss) / len(ss), default=0.55)
                confidence = clamp(sum(s.confidence for s in ss) / len(ss), default=0.6)
                evidence_count = sum(max(1, s.evidence_count) for s in ss)
                mastery = clamp(0.72 - 0.42 * pressure - 0.015 * min(evidence_count, 10), default=0.45)
                mastery_gap = 1 - mastery
                priority_score = clamp(0.56 * pressure + 0.28 * mastery_gap + 0.16 * confidence)
                move_available = bool(self.move_bank.get_for_skill(sid))
                selection_score = clamp(0.36 * priority_score + 0.26 * mastery_gap + 0.18 * pressure + 0.10 * confidence + 0.10 * (1.0 if move_available else 0.0))
                if not move_available:
                    selection_score *= 0.35
                candidates.append(SkillCandidate(
                    skill_id=sid,
                    skill_name=self.resources.name(sid),
                    domain=self.resources.domain(sid),
                    mastery_estimate=round(mastery, 3),
                    selection_score=round(selection_score, 3),
                    priority_score=round(priority_score, 3),
                    pressure=round(pressure, 3),
                    confidence=round(confidence, 3),
                    evidence_count=evidence_count,
                    evaluation_bucket=self.resources.bucket(sid),
                    sources=sorted({s.source for s in ss}),
                    evidence_ids=sorted({eid for s in ss for eid in s.evidence_ids if eid}),
                    families=sorted({f for s in ss for f in s.families if f}),
                    reasons=self._summarize_reasons(ss, priority_score, mastery_gap, self.resources.bucket(sid), move_available, []),
                    prerequisites=DEPENDENCY_OVERRIDES.get(sid, []) or self.resources.dependencies(sid),
                    dependency_ready=True,
                    blocking_prerequisites=[],
                    recommended_difficulty=self._difficulty(mastery, confidence),
                    recommended_duration=timebox,
                ))
        # Sort: dependency-ready first only if close; no move availability should sink.
        candidates.sort(key=lambda c: (bool(self.move_bank.get_for_skill(c.skill_id)), c.dependency_ready, c.selection_score), reverse=True)
        return candidates

    def _blocking_prereqs(self, sid: str, deps: List[str], grouped: Dict[str, List[EvidenceSignal]]) -> List[str]:
        blocking = []
        for dep in deps:
            # If dependency already has strong mastery evidence, it is not blocking. If absent, assume unknown/block when target is advanced.
            dep_sigs = grouped.get(dep, [])
            mastery_values = [s.mastery_estimate for s in dep_sigs if s.mastery_estimate is not None]
            if mastery_values and sum(mastery_values) / len(mastery_values) >= 0.68:
                continue
            # Do not block on dependencies with no available move; otherwise Coach can dead-end.
            if self.move_bank.get_for_skill(dep):
                blocking.append(dep)
        return blocking

    def _summarize_reasons(self, ss: List[EvidenceSignal], priority_score: float, mastery_gap: float, bucket: str, move_available: bool, blocking: List[str]) -> List[str]:
        reasons = []
        source_names = ", ".join(sorted({s.source for s in ss}))
        fams = Counter(f for s in ss for f in s.families if f)
        if source_names:
            reasons.append(f"Evidence sources: {source_names}.")
        if fams:
            reasons.append("Repeated signal families: " + ", ".join(f"{k}×{v}" for k, v in fams.most_common(4)) + ".")
        if priority_score >= 0.65:
            reasons.append("High current learning priority.")
        elif priority_score >= 0.48:
            reasons.append("Moderate current learning priority.")
        if mastery_gap >= 0.45:
            reasons.append("Large mastery gap.")
        reasons.append(f"Evaluation bucket: {bucket}.")
        if not move_available:
            reasons.append("No external move-bank entry available; candidate cannot be selected for student mission.")
        if blocking:
            reasons.append("Blocked by prerequisite(s): " + ", ".join(blocking) + ".")
        # Keep short, avoid leaking detector internals in student view.
        return reasons

    def _difficulty(self, mastery: float, confidence: float) -> str:
        if mastery < 0.42:
            return "scaffolded"
        if mastery < 0.62:
            return "controlled"
        if confidence < 0.55:
            return "diagnostic_controlled"
        return "near_transfer"


# ---------------------------------------------------------------------------
# Move selection and mission building
# ---------------------------------------------------------------------------


class MoveSelector:
    def __init__(self, move_bank: MoveBank):
        self.move_bank = move_bank

    def select(self, skill: SkillCandidate, context: Dict[str, Any]) -> Tuple[Dict[str, Any], List[MoveCandidate]]:
        moves = self.move_bank.get_for_skill(skill.skill_id)
        candidates: List[MoveCandidate] = []
        desired_minutes = int(context.get("timebox_minutes") or skill.recommended_duration or 10)
        for m in moves:
            source_policy = m.get("source_policy", {}) or {}
            own_essay_use = source_policy.get("own_essay_use", "diagnostic_reference_only")
            source_policy_ok = own_essay_use != "default_repair_material"
            dur = int(m.get("default_timebox_minutes") or desired_minutes)
            duration_fit = 1.0 - min(1.0, abs(dur - desired_minutes) / max(5, desired_minutes))
            diff_levels = m.get("difficulty_levels") or ["controlled"]
            difficulty_fit = 1.0 if skill.recommended_difficulty in diff_levels else 0.74
            mode = m.get("training_mode", "")
            production_bonus = 1.0 if mode in {"production", "transformation", "production_transformation", "expansion", "compression"} else 0.68
            observable_quality = min(1.0, len(m.get("observable_units") or []) / 4.0)
            role = "primary_target" if skill.skill_id == m.get("primary_microskill") else "secondary_target" if skill.skill_id in (m.get("secondary_microskills") or []) else "compatible_related_target"
            role_bonus = 1.0 if role == "primary_target" else 0.92 if role == "secondary_target" else 0.72
            fit_score = clamp(0.24 * duration_fit + 0.20 * difficulty_fit + 0.18 * production_bonus + 0.18 * observable_quality + 0.10 * (1.0 if source_policy_ok else 0.0) + 0.10 * role_bonus)
            selection_score = clamp(0.60 * fit_score + 0.40 * skill.selection_score)
            candidates.append(MoveCandidate(
                move_id=m["move_id"],
                move_name=m["move_name"],
                primary_microskill=m["primary_microskill"],
                move_family=m.get("move_family", ""),
                selection_score=round(selection_score, 3),
                fit_score=round(fit_score, 3),
                difficulty_fit=round(difficulty_fit, 3),
                source_policy_ok=source_policy_ok,
                target_skill_role_in_move=role,
                target_skill_role_explanation=(
                    "Move directly targets the selected microskill." if role == "primary_target" else
                    "Move trains the selected microskill as a secondary target inside a broader writing-production move." if role == "secondary_target" else
                    "Move is compatible but not explicitly tagged as primary/secondary for the selected microskill."
                ),
                reason=f"duration_fit={duration_fit:.2f}; difficulty_fit={difficulty_fit:.2f}; production_mode={production_bonus:.2f}; observable_quality={observable_quality:.2f}; target_role={role}",
            ))
        candidates.sort(key=lambda x: x.selection_score, reverse=True)
        if not candidates:
            raise ValueError(f"No move-bank entry available for selected skill: {skill.skill_id}")
        selected = self.move_bank.get(candidates[0].move_id)
        if not selected:
            raise ValueError(f"Selected move not found: {candidates[0].move_id}")
        return selected, candidates


class MissionBuilder:
    def __init__(self, resources: OntologyResources):
        self.resources = resources

    def _target_skill_role_in_move(self, skill_id: str, move: Dict[str, Any]) -> str:
        if skill_id == move.get("primary_microskill"):
            return "primary_target"
        if skill_id in (move.get("secondary_microskills") or []):
            return "secondary_target"
        return "compatible_related_target"

    def _target_skill_role_explanation(self, skill_id: str, move: Dict[str, Any]) -> str:
        role = self._target_skill_role_in_move(skill_id, move)
        if role == "primary_target":
            return "The selected move directly targets this microskill as its primary training focus."
        if role == "secondary_target":
            return "The selected move trains this microskill inside a broader writing-production move; it is evaluated through the observable units."
        return "The selected move is compatible with the target skill, but the move bank does not list it as a primary or secondary microskill."

    def build(self, skill: SkillCandidate, move: Dict[str, Any], move_candidates: List[MoveCandidate], context: Dict[str, Any]) -> Dict[str, Any]:
        prompt = context.get("prompt_text") or "the current writing topic"
        topic = self._topic_label(prompt)
        timebox = int(context.get("timebox_minutes") or move.get("default_timebox_minutes") or skill.recommended_duration or 10)
        mission_id = stable_id("wc_mission", skill.skill_id, move.get("move_id"), prompt, now_iso(), n=10)
        blueprint = move.get("mission_blueprint", {}) or {}
        steps = self._instantiate_steps(blueprint.get("steps") or [], topic, prompt, skill, move)
        stimulus = self._instantiate_stimulus(move, context, topic, prompt)
        required_output = self._instantiate_required_output(move, blueprint, timebox)
        success_checklist = self._student_checklist(move)
        observable_units = self._normalize_observable_units(move)
        title = self._instantiate_text(blueprint.get("title") or move.get("move_name") or "Writing Move Mission", topic, prompt, skill, move)
        student_instruction = self._build_student_instruction(title, move, steps, stimulus, required_output, timebox)

        return {
            "mission_id": mission_id,
            "mission_version": "writing_coach_v1_2_10_move_mission",
            "target_skill_id": skill.skill_id,
            "target_skill_name": skill.skill_name,
            "target_domain": skill.domain,
            "target_skill_role_in_move": self._target_skill_role_in_move(skill.skill_id, move),
            "target_skill_role_explanation": self._target_skill_role_explanation(skill.skill_id, move),
            "selected_move": {
                "move_id": move.get("move_id"),
                "move_name": move.get("move_name"),
                "move_family": move.get("move_family"),
                "training_mode": move.get("training_mode"),
                "primary_microskill": move.get("primary_microskill"),
                "secondary_microskills": move.get("secondary_microskills") or [],
                "target_skill_role_in_move": self._target_skill_role_in_move(skill.skill_id, move),
                "target_skill_role_explanation": self._target_skill_role_explanation(skill.skill_id, move),
            },
            "timebox_minutes": timebox,
            "difficulty": skill.recommended_difficulty,
            "activity_category": "WRITING_COACH_MICRO_PRODUCTION",
            "title": title,
            "student_goal": self._instantiate_text(move.get("student_goal") or "Train one writing skill.", topic, prompt, skill, move),
            "source_prompt": prompt,
            "source_policy": move.get("source_policy") or {"own_essay_use": "diagnostic_reference_only"},
            "stimulus": stimulus,
            "steps": steps,
            "required_output": required_output,
            "student_instruction": student_instruction,
            "success_checklist": success_checklist,
            "observable_units": observable_units,
            "scoring": self._scoring(move),
            "student_rationale": self._student_rationale(skill, move),
            "teacher_rationale": self._teacher_rationale(skill, move, move_candidates),
            "debug_rationale": {
                "skill_reasons": skill.reasons,
                "sources": skill.sources,
                "families": skill.families,
                "evidence_ids": skill.evidence_ids[:20],
                "move_candidate_rankings": [asdict(c) for c in move_candidates[:8]],
            },
            "repeat_policy": self._repeat_policy(skill, move),
            "domain_adapter": {
                "domain": "ielts_academic_writing" if "ielts" in str(context.get("domain", "ielts_academic_writing")).lower() else context.get("domain", "general_writing"),
                "adapter_role": "presentation_and_prompt_context_only",
                "core_logic_dependency": False,
            },
        }

    def _topic_label(self, prompt: str) -> str:
        p = clean_text(prompt)
        if not p:
            return "the topic"
        if "older people" in p.lower() or "ageing" in p.lower() or "aging" in p.lower():
            return "ageing population"
        if len(p) > 90:
            return p[:87].rstrip() + "..."
        return p

    def _instantiate_text(self, text: str, topic: str, prompt: str, skill: SkillCandidate, move: Dict[str, Any]) -> str:
        return (str(text or "")
                .replace("{topic}", topic)
                .replace("{prompt}", prompt)
                .replace("{skill_name}", skill.skill_name)
                .replace("{move_name}", move.get("move_name", "writing move")))

    def _instantiate_steps(self, steps: List[str], topic: str, prompt: str, skill: SkillCandidate, move: Dict[str, Any]) -> List[str]:
        out = [self._instantiate_text(s, topic, prompt, skill, move) for s in steps]
        if not out:
            out = [
                f"Read the topic: {topic}.",
                "Study the model structure.",
                "Write your own new answer using the same writing move.",
                "Check your answer against the success checklist.",
            ]
        return out

    def _instantiate_stimulus(self, move: Dict[str, Any], context: Dict[str, Any], topic: str, prompt: str) -> Dict[str, Any]:
        stimulus_cfg = move.get("stimulus", {}) or {}
        items = list(stimulus_cfg.get("items") or [])
        # Prefer topic-matched items if the simple bank provides them.
        topic_items = []
        for item in items:
            if not isinstance(item, dict):
                continue
            tags = [str(t).lower() for t in item.get("topic_tags", [])]
            if "ageing" in topic.lower() and any(t in {"ageing", "aging", "elderly", "society"} for t in tags):
                topic_items.append(item)
        selected_items = topic_items or [i for i in items if isinstance(i, dict)]
        required_count = int(stimulus_cfg.get("required_item_count") or min(3, len(selected_items)) or 3)
        selected_items = selected_items[:required_count]
        if not selected_items:
            selected_items = self._fallback_items(move, topic)
        return {
            "stimulus_type": stimulus_cfg.get("stimulus_type", "near_transfer_prompts"),
            "topic_context": topic,
            "own_essay_use": (move.get("source_policy") or {}).get("own_essay_use", "diagnostic_reference_only"),
            "items": selected_items,
            "noticing_example": self._safe_noticing_example(context),
        }

    def _fallback_items(self, move: Dict[str, Any], topic: str) -> List[Dict[str, Any]]:
        fam = move.get("move_family", "")
        if fam in {"clarity", "accuracy"}:
            return [
                {"rough_input": "older people / need healthcare / government pays", "expected_move": "write one clear sentence"},
                {"rough_input": "fewer workers / economy slows", "expected_move": "write one clear sentence"},
                {"rough_input": "grandparents / teach traditions / children", "expected_move": "write one clear sentence"},
            ]
        if fam == "precision":
            return [
                {"weak_phrase": "older people help society", "meaning_target": "professional experience"},
                {"weak_phrase": "good things for families", "meaning_target": "childcare support"},
                {"weak_phrase": "government problems", "meaning_target": "higher pension and healthcare costs"},
            ]
        if fam == "argument_development":
            return [
                {"claim": "An ageing population can benefit society.", "task": "Add a reason and consequence."},
                {"claim": "An ageing population can create pressure on governments.", "task": "Add a reason and consequence."},
            ]
        return [{"topic": topic, "task": "Produce a short new written response using the target writing move."}]

    def _safe_noticing_example(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        # One short example from the old essay is allowed for noticing only, not as a repair worksheet.
        errormap = context.get("errormap") or {}
        for e in errormap.get("errors") or []:
            loc = e.get("location") or {}
            excerpt = clean_text(loc.get("excerpt"))
            sentence = clean_text(loc.get("sentence"))
            if excerpt and sentence:
                return {
                    "use": "noticing_only_not_repair_task",
                    "excerpt": excerpt,
                    "sentence": sentence,
                    "family": e.get("error_type"),
                    "note": "This explains why the skill was selected. The mission itself uses new near-transfer writing, not direct essay correction.",
                }
        return None

    def _instantiate_required_output(self, move: Dict[str, Any], blueprint: Dict[str, Any], timebox: int) -> Dict[str, Any]:
        ro = blueprint.get("required_output", {}) or {}
        required_items = ro.get("required_items")
        if required_items is None:
            required_items = 3 if timebox <= 10 else 5
        return {
            "type": ro.get("type", "short_written_response"),
            "required_items": int(required_items),
            "line_rule": ro.get("line_rule", "Write one answer per numbered line."),
            "length_guidance": ro.get("length_guidance", "1 sentence per item unless the mission asks for a chain."),
        }

    def _normalize_observable_units(self, move: Dict[str, Any]) -> List[Dict[str, Any]]:
        units = []
        for u in move.get("observable_units") or []:
            if isinstance(u, str):
                units.append({"unit_id": u, "description": prettify_skill(u), "weight": 1.0, "critical": False, "score_type": "0_1"})
            elif isinstance(u, dict):
                units.append({
                    "unit_id": u.get("unit_id"),
                    "description": u.get("description") or prettify_skill(u.get("unit_id", "unit")),
                    "weight": float(u.get("weight", 1.0)),
                    "critical": bool(u.get("critical", False)),
                    "score_type": u.get("score_type", "0_1"),
                })
        total = sum(max(0.0, u["weight"]) for u in units) or 1.0
        for u in units:
            u["weight"] = round(max(0.0, u["weight"]) / total, 3)
        return units

    def _student_checklist(self, move: Dict[str, Any]) -> List[str]:
        checklist = move.get("success_checklist") or []
        return [clean_text(x) for x in checklist if clean_text(x)] or ["I completed the required number of items.", "Each answer clearly expresses one idea.", "I checked grammar and word choice before submitting."]

    def _scoring(self, move: Dict[str, Any]) -> Dict[str, Any]:
        sc = move.get("scoring", {}) or {}
        return {
            "pass_threshold": float(sc.get("pass_threshold", 0.80)),
            "partial_threshold": float(sc.get("partial_threshold", 0.60)),
            "critical_failure_policy": sc.get("critical_failure_policy", "critical observable-unit failure caps result at fail unless evidence is partial and recoverable"),
            "outcomes": {
                "pass": "score >= pass_threshold and no critical failure",
                "partial_pass": "partial_threshold <= score < pass_threshold or minor recoverable gap",
                "fail": "score < partial_threshold or critical failure",
                "invalid": "empty/off-task/not enough evidence",
            },
        }

    def _build_student_instruction(self, title: str, move: Dict[str, Any], steps: List[str], stimulus: Dict[str, Any], required_output: Dict[str, Any], timebox: int) -> str:
        parts = [f"{title}", f"Time: {timebox} minutes", "", "What to do:"]
        parts.extend([f"{i+1}. {s}" for i, s in enumerate(steps)])
        parts.append("")
        parts.append("Write your answer:")
        parts.append(f"- {required_output['required_items']} item(s)")
        parts.append(f"- {required_output['line_rule']}")
        parts.append(f"- {required_output['length_guidance']}")
        return "\n".join(parts)

    def _student_rationale(self, skill: SkillCandidate, move: Dict[str, Any]) -> str:
        return (
            f"Today you will practise {skill.skill_name.lower()} through a short writing move: "
            f"{move.get('move_name')}. The goal is to produce new, clearer writing — not to rewrite the old essay."
        )

    def _teacher_rationale(self, skill: SkillCandidate, move: Dict[str, Any], move_candidates: List[MoveCandidate]) -> str:
        role = self._target_skill_role_in_move(skill.skill_id, move)
        return (
            f"Selected by move-based Writing Coach V1.2.10. skill={skill.skill_id}; "
            f"selection_score={skill.selection_score}; priority_score={skill.priority_score}; "
            f"mastery_estimate={skill.mastery_estimate}; confidence={skill.confidence}; "
            f"bucket={skill.evaluation_bucket}; selected_move={move.get('move_id')}; "
            f"target_skill_role_in_move={role}; "
            f"move_fit={move_candidates[0].fit_score if move_candidates else None}."
        )

    def _repeat_policy(self, skill: SkillCandidate, move: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "if_fail": "repeat same microskill with easier stimulus and heavier model support",
            "if_partial_pass": "repeat same move family with one constraint reduced",
            "if_pass": "repeat same microskill once in a new topic before unlocking dependent skill",
            "functional_rule": "pass 2 of last 3 valid move missions with average >= 0.75 and no critical failure",
            "stable_rule": "pass 4 of last 5 valid move missions with average >= 0.80 across at least 2 topics",
        }


# ---------------------------------------------------------------------------
# Mission evaluation
# ---------------------------------------------------------------------------


class MissionEvaluator:
    """Rule-based evaluator for short Writing Coach move missions.

    V1.2.10 adds a hard completion gate and a student-facing Attempt Feedback
    Layer. The evaluator is intentionally conservative: incomplete output is
    not mastery evidence, even if the one submitted sentence looks good.
    """

    FINITE_VERBS = {
        "is", "are", "was", "were", "be", "am", "has", "have", "had", "do", "does", "did",
        "can", "could", "should", "would", "will", "may", "might", "must", "need", "needs",
        "make", "makes", "help", "helps", "increase", "increases", "reduce", "reduces", "support",
        "supports", "teach", "teaches", "create", "creates", "lead", "leads", "allow", "allows",
        "provide", "provides", "preserve", "preserves", "pay", "pays", "retire", "retires", "slow", "slows"
    }
    # V1.2.12 fix: the old fallback "\b\w+(?:s|ed)\b" regex treated ANY word
    # ending in -s/-ed as a verb, so plural nouns after a determiner/quantifier
    # ("the workers", "fewer services") were misread as containing a finite
    # verb. This doesn't require full POS tagging -- it just excludes the most
    # common false-positive shape (determiner/quantifier immediately before the
    # -s/-ed word). It will not catch every case (e.g. adjective + plural noun,
    # "hard workers"), but it fixes the demonstrated failure and the common
    # article/quantifier + plural-noun pattern.
    _NOUN_PHRASE_DETERMINERS = {
        "the", "a", "an", "this", "that", "these", "those", "fewer", "more", "many",
        "few", "several", "some", "each", "every", "such", "no", "any", "all",
        "both", "either", "neither", "his", "her", "their", "our", "its", "your", "my",
    }

    @classmethod
    def _has_plausible_finite_verb(cls, line: str) -> bool:
        toks = [w.lower() for w in words(line)]
        if any(v in toks for v in cls.FINITE_VERBS):
            return True
        for i, t in enumerate(toks):
            if re.fullmatch(r"\w+(?:s|ed)", t):
                prev = toks[i - 1] if i > 0 else ""
                if prev in cls._NOUN_PHRASE_DETERMINERS:
                    continue
                return True
        return False

    SUBJECT_HINTS = {
        "people", "government", "governments", "society", "families", "family", "older", "elderly",
        "population", "workers", "children", "they", "it", "this", "that", "parents", "grandparents",
        "experience", "advice", "healthcare", "economy", "culture"
    }
    VAGUE_WORDS = {"things", "stuff", "good", "bad", "nice", "many ways", "some kinds", "something", "good ability"}
    INFORMAL = {"kids", "stuff", "really", "a lot", "things", "okay", "ok"}

    def evaluate(self, mission_payload: Dict[str, Any], response_text: str) -> Dict[str, Any]:
        mission, payload_error = extract_mission_for_cli(mission_payload)
        if payload_error or not mission:
            raise ValueError(payload_error or "Mission payload does not contain today_mission/today_micromission/mission.")

        required_items = int(safe_get(mission, "required_output.required_items", 1) or 1)
        parsed_response = parse_response_items(response_text, required_items, safe_get(mission, "stimulus.items", []) or [])
        lines = [x.get("text", "") for x in parsed_response.get("submitted_items", [])]
        observable_units = mission.get("observable_units") or []
        submitted_items = int(parsed_response.get("submitted_count", len(lines)))
        completion_ratio = min(1.0, submitted_items / max(1, required_items))
        empty_response = not clean_text(response_text)
        incomplete_output = (not empty_response) and submitted_items < required_items
        completion_gate_passed = (not empty_response) and submitted_items >= required_items

        unit_scores: List[Dict[str, Any]] = []
        critical_failure = False
        for u in observable_units:
            uid = u.get("unit_id") if isinstance(u, dict) else str(u)
            weight = float(u.get("weight", 1.0)) if isinstance(u, dict) else 1.0
            critical = bool(u.get("critical", False)) if isinstance(u, dict) else False
            score, note = self._score_unit(uid, lines, response_text, required_items, mission)
            if critical and score < 0.5:
                critical_failure = True
            unit_scores.append({
                "unit_id": uid,
                "score": round(score, 3),
                "weight": weight,
                "critical": critical,
                "note": note,
            })

        total_weight = sum(max(0, u["weight"]) for u in unit_scores) or 1.0
        raw_observable_score = sum(u["score"] * max(0, u["weight"]) for u in unit_scores) / total_weight

        pass_t = float(safe_get(mission, "scoring.pass_threshold", 0.8) or 0.8)
        part_t = float(safe_get(mission, "scoring.partial_threshold", 0.6) or 0.6)

        if empty_response:
            outcome = "invalid_empty_response"
            mission_score = 0.0
            mastery_update_allowed = False
            attempt_status = "invalid_empty_response"
        elif incomplete_output:
            outcome = "invalid_incomplete_output"
            mission_score = 0.0
            mastery_update_allowed = False
            attempt_status = "invalid_incomplete_output"
        elif critical_failure and raw_observable_score < 0.78:
            outcome = "fail"
            mission_score = raw_observable_score
            mastery_update_allowed = True
            attempt_status = "complete_evaluated"
        elif raw_observable_score >= pass_t:
            outcome = "pass"
            mission_score = raw_observable_score
            mastery_update_allowed = True
            attempt_status = "complete_evaluated"
        elif raw_observable_score >= part_t:
            outcome = "partial_pass"
            mission_score = raw_observable_score
            mastery_update_allowed = True
            attempt_status = "complete_evaluated"
        else:
            outcome = "fail"
            mission_score = raw_observable_score
            mastery_update_allowed = True
            attempt_status = "complete_evaluated"

        confidence = self._confidence(lines, observable_units, required_items, completion_gate_passed)
        result_id = stable_id("wc_result", mission.get("mission_id"), response_text, outcome, n=12)
        created = now_iso()
        skill_id = mission.get("target_skill_id")
        skill_name = mission.get("target_skill_name")
        completion_gate = {
            "status": "passed" if completion_gate_passed else attempt_status,
            "required_items": required_items,
            "submitted_items": submitted_items,
            "submitted_item_numbers": parsed_response.get("submitted_item_numbers", []),
            "missing_item_numbers": parsed_response.get("missing_item_numbers", []),
            "completion_ratio": round(completion_ratio, 3),
            "hard_gate": True,
            "mastery_update_allowed": mastery_update_allowed,
            "numbering_warnings": parsed_response.get("numbering_warnings", []),
            "message": self._completion_gate_message(submitted_items, required_items, empty_response),
        }
        item_feedback = self._item_feedback(mission, parsed_response, required_items)
        feedback_bundle = self._feedback(
            outcome=outcome,
            unit_scores=unit_scores,
            mission=mission,
            lines=lines,
            required_items=required_items,
            completion_gate=completion_gate,
            item_feedback=item_feedback,
            mastery_update_allowed=mastery_update_allowed,
        )
        lie_payload = self._lie_result_payload(
            mission, result_id, mission_score, raw_observable_score, outcome, confidence, unit_scores,
            created, mastery_update_allowed, completion_gate
        )
        return {
            "schema_version": MISSION_RESULT_SCHEMA,
            "result_id": result_id,
            "created_at": created,
            "mission_id": mission.get("mission_id"),
            "target_skill_id": skill_id,
            "target_skill_name": skill_name,
            "selected_move": mission.get("selected_move"),
            "response_summary": {
                "line_count": submitted_items,
                "word_count": len(words(response_text)),
                "required_items": required_items,
                "completion_ratio": round(completion_ratio, 3),
                "submitted_item_numbers": parsed_response.get("submitted_item_numbers", []),
                "missing_item_numbers": parsed_response.get("missing_item_numbers", []),
                "numbering_warnings": parsed_response.get("numbering_warnings", []),
            },
            "completion_gate": completion_gate,
            "observable_unit_scores": unit_scores,
            "raw_observable_score_before_gate": round(raw_observable_score, 3),
            "mission_score": round(mission_score, 3),
            "outcome": outcome,
            "attempt_status": attempt_status,
            "confidence": round(confidence, 3),
            "critical_failure": critical_failure,
            "mastery_update_allowed": mastery_update_allowed,
            "feedback": feedback_bundle["legacy_feedback"],
            "student_feedback": feedback_bundle["student_feedback"],
            "teacher_feedback": feedback_bundle["teacher_feedback"],
            "debug_evaluation": feedback_bundle["debug_evaluation"],
            "lie_update_decision": feedback_bundle["lie_update_decision"],
            "learning_intelligence_payload": lie_payload,
        }

    def _completion_gate_message(self, submitted: int, required: int, empty: bool) -> str:
        if empty:
            return f"No answer was submitted. This mission requires {required} item(s)."
        if submitted < required:
            return f"You submitted {submitted} item(s), but the mission requires {required}. Complete all required items before this can count as skill evidence."
        return "Required output count met. The attempt can be evaluated as skill evidence."

    def _score_unit(self, uid: str, lines: List[str], text: str, required_items: int, mission: Dict[str, Any]) -> Tuple[float, str]:
        uid = str(uid or "")
        if uid in {"minimum_output_met", "required_items_met"}:
            score = min(1.0, len(lines) / max(1, required_items))
            return score, f"Detected {len(lines)} item(s); required {required_items}. This is a hard completion gate in V1.2.10."
        if uid in {"subject_present", "clear_subject"}:
            vals = [1.0 if any(w in line.lower().split() for w in self.SUBJECT_HINTS) or re.match(r"^[A-Z]?[a-z]+\s+", line) else 0.4 for line in lines]
            return (sum(vals) / len(vals) if vals else 0.0), "Checks whether each line has an explicit subject or subject-like noun phrase."
        if uid in {"finite_verb_present", "main_verb_present"}:
            vals = [1.0 if self._has_plausible_finite_verb(line) else 0.25 for line in lines]
            return (sum(vals) / len(vals) if vals else 0.0), "Checks for a finite/main verb in each response line."
        if uid in {"verb_form_correct", "pattern_accuracy"}:
            # NOTE (V1.2.12 fix): the old r"\bso \w+er\b" pattern false-positived on
            # perfectly correct usage like "so fewer workers remain" / "so more people
            # need care" (comparative quantifier in a result clause), not just the
            # intended error class ("so older" where "much older" was meant). "fewer"
            # and "more" are excluded; other comparatives after "so" are still flagged.
            bad_patterns = [r"\bhas to spent\b", r"\bhave to spent\b", r"\bcan gives\b", r"\bthis make\b", r"\bthey helps\b", r"\bmore \w+er\b", r"\bso (?!fewer\b|more\b)\w+er\b"]
            bad = sum(1 for line in lines if any(re.search(p, line.lower()) for p in bad_patterns))
            return max(0.0, 1.0 - bad / max(1, len(lines))), f"Detected {bad} obvious blocked pattern(s)."
        if uid in {"complete_meaning", "complete_recoverable_idea"}:
            vals = [1.0 if len(words(line)) >= 6 and any(v in words(line) for v in self.FINITE_VERBS) else 0.45 if len(words(line)) >= 4 else 0.2 for line in lines]
            return (sum(vals) / len(vals) if vals else 0.0), "Checks whether each line expresses a recoverable idea."
        if uid in {"clarity", "reader_effort_reduced"}:
            penalties = 0
            for line in lines:
                if len(words(line)) > 32:
                    penalties += 1
                if contains_any(line, ["some kinds of things", "good ability", "in many ways and situations"]):
                    penalties += 1
            return max(0.0, 1.0 - penalties / max(1, len(lines) * 1.5)), "Checks for clear, manageable expression without high-effort vague chunks."
        if uid in {"lexical_precision", "specific_meaning", "precision_gain"}:
            vague_hits = sum(1 for line in lines if any(v in line.lower() for v in self.VAGUE_WORDS))
            return max(0.0, 1.0 - vague_hits / max(1, len(lines))), f"Detected {vague_hits} vague wording hit(s)."
        if uid in {"academic_register", "formality"}:
            informal_hits = sum(1 for line in lines if any(x in line.lower() for x in self.INFORMAL))
            return max(0.0, 1.0 - informal_hits / max(1, len(lines))), f"Detected {informal_hits} informal wording hit(s)."
        if uid in {"claim_present", "arguable_claim"}:
            vals = [1.0 if contains_any(line, ["should", "can", "because", "benefit", "problem", "outweigh", "important", "pressure"]) else 0.45 for line in lines]
            return (sum(vals) / len(vals) if vals else 0.0), "Checks whether the answer contains a claim that can be supported."
        if uid in {"reason_present", "consequence_present", "causal_link"}:
            vals = [1.0 if contains_any(line, ["because", "therefore", "as a result", "this means", "which can", "so"]) else 0.35 for line in lines]
            return (sum(vals) / len(vals) if vals else 0.0), "Checks whether the answer includes a reason or consequence link."
        if uid in {"example_to_claim_link", "support_alignment"}:
            vals = [1.0 if contains_any(line, ["for example", "this shows", "this supports", "because", "therefore"]) else 0.4 for line in lines]
            return (sum(vals) / len(vals) if vals else 0.0), "Checks whether support is connected to the claim."
        return 0.65, "No specialized rule for this observable unit; neutral partial score applied."

    def _confidence(self, lines: List[str], units: List[Any], required_items: int, completion_gate_passed: bool) -> float:
        if not completion_gate_passed:
            return clamp(0.20 + 0.30 * min(1.0, len(lines) / max(1, required_items)))
        output_factor = min(1.0, len(lines) / max(1, required_items))
        unit_factor = min(1.0, len(units) / 4.0)
        length_factor = min(1.0, sum(len(words(x)) for x in lines) / max(20, required_items * 7))
        return clamp(0.45 * output_factor + 0.30 * unit_factor + 0.25 * length_factor)

    def _item_feedback(self, mission: Dict[str, Any], parsed_response: Dict[str, Any], required_items: int) -> List[Dict[str, Any]]:
        items = safe_get(mission, "stimulus.items", []) or []
        feedback: List[Dict[str, Any]] = []
        submitted_by_number = parsed_response.get("submitted_by_number", {}) or {}
        for i in range(required_items):
            item_number = i + 1
            item = items[i] if i < len(items) and isinstance(items[i], dict) else {}
            rough_input = item.get("rough_input")
            entry = submitted_by_number.get(item_number)
            if entry:
                sent = entry.get("text", "")
                strengths, issues = self._sentence_strengths_issues(sent, rough_input)
                quality_level = self._sentence_quality_level(sent, issues)
                feedback.append({
                    "item_number": item_number,
                    "rough_input": rough_input,
                    "student_sentence": sent,
                    "status": "submitted",
                    "explicit_number_used": bool(entry.get("explicit_number")),
                    "assignment_reason": entry.get("assignment_reason"),
                    "strengths": strengths,
                    "issues": issues,
                    "sentence_quality_level": quality_level,
                    "is_acceptable_for_target_move": quality_level in {"strong_for_current_move", "basic_functional"},
                    "needs_higher_band_upgrade": quality_level == "functional_but_needs_upgrade",
                    "suggested_revision": self._suggest_revision(sent, rough_input),
                    "explanation": self._line_explanation(issues),
                    "how_to_improve": self._how_to_improve(issues, sent, rough_input),
                })
            else:
                feedback.append({
                    "item_number": item_number,
                    "rough_input": rough_input,
                    "student_sentence": None,
                    "status": "missing",
                    "strengths": [],
                    "issues": ["missing_required_item"],
                    "is_acceptable_for_target_move": False,
                    "suggested_revision": self._model_from_rough_input(rough_input),
                    "explanation": "This item was not submitted, so the Coach cannot evaluate the target skill for this part of the mission.",
                    "how_to_improve": "Write one complete sentence for this rough idea.",
                })
        # Preserve extra/invalid-number responses as overflow feedback rather than silently dropping them.
        for entry in parsed_response.get("overflow_items", []) or []:
            sent = entry.get("text", "")
            strengths, issues = self._sentence_strengths_issues(sent, None)
            issues = ["numbering_or_extra_item_issue"] + issues
            feedback.append({
                "item_number": entry.get("item_number"),
                "rough_input": None,
                "student_sentence": sent,
                "status": "extra_or_out_of_range",
                "explicit_number_used": bool(entry.get("explicit_number")),
                "strengths": strengths,
                "issues": issues,
                "is_acceptable_for_target_move": False,
                "suggested_revision": self._suggest_revision(sent, None),
                "explanation": "This response has a duplicate, missing, or out-of-range number, so it cannot be matched cleanly to a required item.",
                "how_to_improve": "Use the exact required numbers: 1, 2, 3, 4, 5.",
            })
        return feedback

    def _sentence_strengths_issues(self, sentence: str, rough_input: Optional[str] = None) -> Tuple[List[str], List[str]]:
        ws = words(sentence)
        lower = sentence.lower()
        rough = (rough_input or "").lower()
        strengths: List[str] = []
        issues: List[str] = []

        if len(ws) >= 8:
            strengths.append("meaning_is_recoverable")
        elif len(ws) >= 5:
            strengths.append("meaning_is_partly_recoverable")
            issues.append("sentence_underdeveloped: add a clearer result or consequence")
        else:
            issues.append("sentence_too_short_or_underdeveloped")

        if self._has_plausible_finite_verb(sentence):
            strengths.append("finite_verb_present")
        else:
            issues.append("finite_verb_missing_or_unclear")

        if any(w in lower.split() for w in self.SUBJECT_HINTS) or re.match(r"^[A-Z]?[a-z]+\s+", sentence):
            strengths.append("subject_present")
        else:
            issues.append("subject_missing_or_unclear")

        # Prompt-item semantic alignment checks.
        if ("retire" in rough or "economy" in rough) and ("retire" in lower or "work" in lower or "econom" in lower):
            strengths.append("idea_matches_prompt_item")
        if "grandparents" in rough and all(k in lower for k in ["grandparents", "parents", "children"]):
            strengths.append("idea_matches_prompt_item")
        if ("healthcare" in rough or "government" in rough) and ("health" in lower or "government" in lower or "spend" in lower or "pay" in lower):
            strengths.append("idea_matches_prompt_item")
        if ("traditions" in rough or "generation" in rough) and ("tradition" in lower or "generation" in lower or "teach" in lower):
            strengths.append("idea_matches_prompt_item")
        if ("experience" in rough or "advice" in rough) and ("experience" in lower or "advice" in lower or "society" in lower):
            strengths.append("idea_matches_prompt_item")

        if re.search(r"\b(might|may|can|could|should)\s+help\b", lower):
            strengths.append("modal_verb_pattern_ok")

        bad_patterns = ["has to spent", "have to spent", "can gives", "this make", "they helps", "more stronger", "so older"]
        found = [p for p in bad_patterns if p in lower]
        if found:
            issues.append("verb_or_comparison_pattern_error: " + ", ".join(found))
        else:
            strengths.append("no_blocked_verb_pattern_detected")

        # High-value local feedback for common learner wording in this mission.
        if re.search(r"\bless\s+(people|workers|children|citizens|students)\b", lower):
            issues.append("comparative_quantifier_error: use 'fewer' with countable plural nouns such as people or workers")
        if re.search(r"\bthere\s+(are|is)\s+(fewer|less|many|more)?\s*people\s+working\b", lower):
            issues.append("weak_academic_structure: avoid 'there are people working'; use 'the workforce shrinks' or 'the number of workers declines'")
        if re.search(r"\bmany people retire\s+and\b", lower):
            issues.append("weak_sentence_connection: replace a simple 'and' chain with a clearer cause-result structure")
        if re.search(r"\b(taking care with|take care with|care with)\b", lower):
            issues.append("preposition_collocation_error: use 'care for children', 'take care of children', or 'by caring for children'")
        if re.search(r"\bhelp\s+\w+\s+with\s+taking\s+care\b", lower):
            issues.append("unnatural_gerund_phrase: use 'help parents by caring for children'")
        if re.search(r"\bwith\s+taking\s+care\s+of\b", lower):
            issues.append("unnatural_phrase_structure: use 'by taking care of' or 'by caring for'")

        vague = [v for v in self.VAGUE_WORDS if v in lower]
        if vague:
            issues.append("vague_wording: " + ", ".join(vague[:3]))

        # Higher-band pressure: do not call a low-level sentence 'fine' just because it is recoverable.
        if rough and not any(i.startswith(("verb_or_comparison_pattern_error", "finite_verb", "subject_missing")) for i in issues):
            if len(ws) < 12 or "there are" in lower or " and " in lower:
                issues.append("higher_band_upgrade: make the sentence more precise, natural, and academically connected")

        return strengths, issues

    def _sentence_quality_level(self, sentence: str, issues: List[str]) -> str:
        if any(str(i).startswith(("verb_or_comparison_pattern_error", "finite_verb", "subject_missing", "preposition_collocation_error")) for i in issues):
            return "needs_fix"
        if any(str(i).startswith(("comparative_quantifier_error", "weak_academic_structure", "weak_sentence_connection", "higher_band_upgrade", "vague_wording", "unnatural")) for i in issues):
            return "functional_but_needs_upgrade"
        if len(words(sentence)) >= 10:
            return "strong_for_current_move"
        return "basic_functional"

    def _line_explanation(self, issues: List[str]) -> str:
        if not issues:
            return "The sentence is clear and accurate enough for the target move."
        if "missing_required_item" in issues:
            return "The answer is incomplete because this required item is missing."
        if any(str(i).startswith("comparative_quantifier_error") for i in issues):
            return "The idea is understandable, but 'less people' is not accurate academic English. Use 'fewer people' or, better, 'fewer workers'."
        if any(str(i).startswith("weak_academic_structure") for i in issues):
            return "The sentence is understandable but too basic. For higher-level writing, replace 'there are people working' with a more precise phrase such as 'the workforce shrinks'."
        if any(str(i).startswith("weak_sentence_connection") for i in issues):
            return "The sentence uses a simple 'and' chain. A stronger academic sentence shows the cause-result relationship more clearly."
        if any(str(i).startswith("preposition_collocation_error") for i in issues):
            return "The idea is understandable, but the phrase after 'help parents' is unnatural. Use 'by caring for children' or 'take care of children'."
        if any(str(i).startswith("unnatural") for i in issues):
            return "The sentence has a useful subject and verb, but the phrase structure should be made more natural."
        if any(str(i).startswith("higher_band_upgrade") for i in issues):
            return "This is recoverable, but the Coach should push it above basic B1 phrasing toward clearer academic expression."
        return "Focus on making the sentence complete, clear, natural, and precise before increasing difficulty."

    def _how_to_improve(self, issues: List[str], sentence: str, rough_input: Optional[str]) -> str:
        if "missing_required_item" in issues:
            return "Write one complete sentence for this rough idea."
        actions = []
        if any(str(i).startswith("comparative_quantifier_error") for i in issues):
            actions.append("Change 'less people' to 'fewer people' or, more naturally here, 'fewer workers'.")
        if any(str(i).startswith("weak_academic_structure") for i in issues):
            actions.append("Replace 'there are fewer people working' with 'the workforce shrinks' or 'the number of workers declines'.")
        if any(str(i).startswith("weak_sentence_connection") for i in issues):
            actions.append("Use a cause-result structure: 'When/As many people retire, ..., which can ...'.")
        if any(str(i).startswith("preposition_collocation_error") for i in issues) or any(str(i).startswith("unnatural_gerund_phrase") for i in issues):
            actions.append("Replace 'with taking care with children' with 'by caring for children' or 'by taking care of children'.")
        if any(str(i).startswith("higher_band_upgrade") for i in issues) and not actions:
            actions.append("Add a precise academic noun and a consequence, not only a simple statement.")
        if actions:
            return " ".join(actions)
        if not issues:
            return "The sentence is accurate. Try a stronger academic version if you want to raise the level."
        return "Revise the sentence so it has one clear subject, one correct main verb, natural word combinations, and a clear result."

    def _suggest_revision(self, sentence: str, rough_input: Optional[str]) -> str:
        # Prefer a high-quality model aligned with the exact rough input, not a generic low-level correction.
        model = self._model_from_rough_input(rough_input)
        if model:
            return model
        s = clean_text(sentence)
        replacements = {
            "has to spent": "has to spend",
            "have to spent": "have to spend",
            "this make": "this makes",
            "they helps": "they help",
            "more stronger": "stronger",
            "so older": "very old",
            "less people working": "fewer workers",
            "there are less people working": "the workforce shrinks",
            "there are fewer people working": "the workforce shrinks",
            "with taking care with children": "by caring for children",
            "taking care with children": "caring for children",
            "take care with children": "take care of children",
        }
        for a, b in replacements.items():
            s = re.sub(re.escape(a), b, s, flags=re.IGNORECASE)
        if s and s[-1] not in ".!?":
            s += "."
        return s or "Write one complete academic sentence with a clear subject, verb, and result."

    def _model_from_rough_input(self, rough_input: Optional[str]) -> str:
        r = (rough_input or "").lower()
        if "healthcare" in r or "government pays" in r:
            return "An ageing population increases healthcare costs, forcing governments to spend more on public services."
        if "retire" in r or "economy slows" in r:
            return "When many people retire, the workforce shrinks, which can slow economic growth."
        if "grandparents" in r or "care for children" in r:
            return "Grandparents can support working parents by helping to care for children."
        if "traditions" in r or "younger generation" in r:
            return "Older people can preserve cultural traditions by teaching them to younger generations."
        if "experience" in r or "advice" in r:
            return "Older people’s experience can benefit society because they often offer practical advice."
        return ""

    def _feedback(self, outcome: str, unit_scores: List[Dict[str, Any]], mission: Dict[str, Any], lines: List[str],
                  required_items: int, completion_gate: Dict[str, Any], item_feedback: List[Dict[str, Any]],
                  mastery_update_allowed: bool) -> Dict[str, Any]:
        weakest = sorted(unit_scores, key=lambda x: x["score"])[:2]
        submitted = len(lines)
        missing = [it.get("item_number") for it in item_feedback if it.get("status") == "missing" and isinstance(it.get("item_number"), int)]
        submitted_feedback = [it for it in item_feedback if it.get("status") == "submitted"]
        if outcome == "invalid_empty_response":
            summary = f"No answer was submitted. Please complete all {required_items} items."
            next_action = "submit_required_items"
        elif outcome == "invalid_incomplete_output":
            if submitted_feedback:
                summary = f"You submitted {submitted}/{required_items} required items. I reviewed the submitted sentence(s) below, but complete all {required_items} items before this mission can count."
            else:
                summary = f"You submitted {submitted}/{required_items} required items. Complete all {required_items} items before this mission can count."
            next_action = "complete_missing_items"
        elif outcome == "pass":
            summary = "Good control of the target move. Repeat once with a new topic before unlocking the next skill."
            next_action = "repeat_or_upgrade_based_on_outcome"
        elif outcome == "partial_pass":
            summary = "Partly successful. Repeat the same move with a little more structure before increasing difficulty."
            next_action = "repeat_with_scaffold"
        else:
            summary = "The target move is not controlled yet. Repeat with a model and fewer items."
            next_action = "repeat_easier"
        format_feedback = completion_gate.get("message")
        student_feedback = {
            "overall_comment": summary,
            "format_feedback": format_feedback,
            "what_went_well": self._aggregate_strengths(item_feedback),
            "what_to_fix_first": self._prioritize_issues(item_feedback, completion_gate),
            "item_feedback": item_feedback,
            "submitted_items_reviewed": [it.get("item_number") for it in submitted_feedback],
            "missing_items": missing,
            "numbering_feedback": self._numbering_feedback(completion_gate),
            "next_action": next_action,
            "try_again_instruction": self._try_again_instruction(mission, missing, outcome),
        }
        teacher_feedback = {
            "outcome": outcome,
            "mastery_update_allowed": mastery_update_allowed,
            "completion_gate": completion_gate,
            "weakest_observable_units": weakest,
            "interpretation": "Incomplete output is treated as non-mastery evidence in V1.2.10." if not mastery_update_allowed else "Complete attempt can be used as mission-level skill evidence.",
        }
        debug_evaluation = {
            "raw_line_count": submitted,
            "required_items": required_items,
            "observable_unit_scores": unit_scores,
            "hard_gate_applied": not completion_gate.get("mastery_update_allowed", False),
            "submitted_item_numbers": completion_gate.get("submitted_item_numbers", []),
            "missing_item_numbers": completion_gate.get("missing_item_numbers", []),
            "numbering_warnings": completion_gate.get("numbering_warnings", []),
            "score_policy": "mission_score is set to 0.0 when the hard completion gate fails; raw_observable_score_before_gate is kept for debugging.",
        }
        lie_update_decision = {
            "mastery_update_allowed": mastery_update_allowed,
            "reason": "complete_attempt" if mastery_update_allowed else completion_gate.get("status"),
            "emission_type": "performance_evidence" if mastery_update_allowed else "attempt_record_only_not_mastery_evidence",
        }
        return {
            "legacy_feedback": {
                "summary": summary,
                "weakest_observable_units": weakest,
                "next_action": next_action,
            },
            "student_feedback": student_feedback,
            "teacher_feedback": teacher_feedback,
            "debug_evaluation": debug_evaluation,
            "lie_update_decision": lie_update_decision,
        }

    # v1.2.18: student-facing "What went well" / "Fix first" text was raw
    # internal codes ("subject_present", "higher_band_upgrade",
    # "llm_flagged_issue"), reported directly by a real user as confusing
    # and unhelpful. Two separate bugs, both universal (not tied to any
    # particular mission or essay):
    #   1. _aggregate_strengths returned the strength CODE itself
    #      (strengths.append("subject_present") etc., a small closed set --
    #      see STRENGTH_CODE_TO_STUDENT_TEXT below) with no translation step
    #      at all.
    #   2. _prioritize_issues did `str(issue).split(":")[0]`, which keeps
    #      only the machine code before the colon and throws away the
    #      human-readable description after it -- even though every issue
    #      string already carries a good description
    #      ("higher_band_upgrade: make the sentence more precise, natural,
    #      and academically connected"). Fixed to count by code (so
    #      frequency-based prioritization is unchanged) but display the
    #      description that was already there, instead of discarding it.
    STRENGTH_CODE_TO_STUDENT_TEXT: Dict[str, str] = {
        "subject_present": "You included a clear subject",
        "finite_verb_present": "Your sentence had a clear main verb",
        "meaning_is_recoverable": "Your meaning was clear",
        "meaning_is_partly_recoverable": "Your meaning was mostly clear",
        "idea_matches_prompt_item": "Your sentence matched the prompt",
        "modal_verb_pattern_ok": "You used the modal verb pattern correctly",
        "no_blocked_verb_pattern_detected": "You avoided the verb pattern this mission is checking for",
    }

    def _aggregate_strengths(self, item_feedback: List[Dict[str, Any]]) -> List[str]:
        c = Counter()
        for item in item_feedback:
            for s in item.get("strengths") or []:
                c[s] += 1
        return [self.STRENGTH_CODE_TO_STUDENT_TEXT.get(k, k.replace("_", " ")) for k, _ in c.most_common(4)]

    def _prioritize_issues(self, item_feedback: List[Dict[str, Any]], completion_gate: Dict[str, Any]) -> List[str]:
        c = Counter()
        label_for_code: Dict[str, str] = {}
        for item in item_feedback:
            if item.get("status") == "submitted":
                for issue in item.get("issues") or []:
                    issue_str = str(issue)
                    if ":" in issue_str:
                        code, desc = issue_str.split(":", 1)
                        code, desc = code.strip(), desc.strip()
                    else:
                        code, desc = issue_str.strip(), issue_str.strip()
                    c[code] += 1
                    if desc and (code not in label_for_code or len(desc) > len(label_for_code[code])):
                        label_for_code[code] = desc
        submitted_issues = [label_for_code.get(k, k.replace("_", " ")) for k, _ in c.most_common(3)]
        if not completion_gate.get("mastery_update_allowed", False):
            return ["Complete all the required items first."] + submitted_issues
        return submitted_issues or ["Keep practicing this skill with a new topic."]

    def _numbering_feedback(self, completion_gate: Dict[str, Any]) -> str:
        warnings = completion_gate.get("numbering_warnings") or []
        missing = completion_gate.get("missing_item_numbers") or []
        submitted = completion_gate.get("submitted_item_numbers") or []
        if "numbering_skips_or_starts_late" in warnings:
            return f"You submitted item number(s) {submitted}. Missing required number(s): {missing}. Keep the original item numbers so the Coach can match each sentence correctly."
        if missing:
            return f"Missing required item number(s): {missing}."
        return "Numbering is complete."

    def _try_again_instruction(self, mission: Dict[str, Any], missing: List[int], outcome: str) -> str:
        if outcome in {"invalid_empty_response", "invalid_incomplete_output"}:
            if missing:
                return f"Add the missing numbered sentence(s): {', '.join(map(str, missing))}. Then submit all required items together."
            return "Submit the full required numbered list."
        return "Use the feedback above, then complete the next assigned Writing Coach mission."

    def _lie_result_payload(self, mission: Dict[str, Any], result_id: str, score: float, raw_score: float,
                            outcome: str, confidence: float, unit_scores: List[Dict[str, Any]], created: str,
                            mastery_update_allowed: bool, completion_gate: Dict[str, Any]) -> Dict[str, Any]:
        payload = {
            "source_engine_id": ENGINE_ID,
            "source_run_id": result_id,
            "profile_type": "coaching_mission_result",
            "emission_type": "performance_evidence" if mastery_update_allowed else "attempt_record_only_not_mastery_evidence",
            "mission_id": mission.get("mission_id"),
            "mastery_update_allowed": mastery_update_allowed,
            "attempt_status": completion_gate.get("status"),
            "completion_gate": completion_gate,
            "skill_signals": [],
            "metric_signals": [],
            "behavioral_events": [
                {
                    "event_id": stable_id("event", result_id, "submitted", n=12),
                    "event_type": "micromission_submitted" if mastery_update_allowed else "micromission_attempt_incomplete_or_invalid",
                    "mission_id": mission.get("mission_id"),
                    "outcome": outcome,
                    "created_at": created,
                }
            ],
            "privacy_classification": "learning_analytics",
        }
        if mastery_update_allowed:
            status = "emerging" if outcome == "pass" else "practicing" if outcome == "partial_pass" else "weak"
            payload["skill_signals"] = [
                {
                    "skill_id": mission.get("target_skill_id"),
                    "skill_name": mission.get("target_skill_name"),
                    "score": round(score, 3),
                    "confidence": round(confidence, 3),
                    "status": status,
                    "outcome": outcome,
                    "evidence_count": 1,
                    "evidence_row_ids": [result_id],
                }
            ]
            payload["metric_signals"] = [
                {
                    "metric_id": u["unit_id"],
                    "target_skill_id": mission.get("target_skill_id"),
                    "value": u["score"],
                    "source": "mission_observable_units",
                }
                for u in unit_scores
            ]
        else:
            payload["attempt_record"] = {
                "skill_id": mission.get("target_skill_id"),
                "skill_name": mission.get("target_skill_name"),
                "outcome": outcome,
                "mission_score": round(score, 3),
                "raw_observable_score_before_gate": round(raw_score, 3),
                "reason": completion_gate.get("status"),
            }
        return payload


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


class MoveBasedWritingCoach:
    def __init__(self, move_bank: MoveBank, resources: OntologyResources):
        move_bank.validate_or_raise()
        self.move_bank = move_bank
        self.resources = resources
        self.extractor = EvidenceExtractor(resources)
        self.skill_selector = SkillSelector(resources, move_bank)
        self.move_selector = MoveSelector(move_bank)
        self.mission_builder = MissionBuilder(resources)

    def generate(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        identity = self._identity(inputs)
        context = self._context(inputs, identity)
        signals = self.extractor.extract(inputs)
        candidates = self.skill_selector.build_candidates(signals, timebox=context["timebox_minutes"])
        selectable = [c for c in candidates if self.move_bank.get_for_skill(c.skill_id) and c.dependency_ready]
        if not selectable:
            selectable = [c for c in candidates if self.move_bank.get_for_skill(c.skill_id)]
        if not selectable:
            raise ValueError("No selectable skill has an external move-bank entry. Add move-bank entries for top candidate skills.")
        selected_skill = selectable[0]
        selected_move, move_candidates = self.move_selector.select(selected_skill, context)
        mission = self.mission_builder.build(selected_skill, selected_move, move_candidates, {**context, **inputs})
        created = now_iso()
        run_id = stable_id("run", identity.get("student_id"), identity.get("submission_id"), created, n=16)
        output = {
            "schema_version": OUTPUT_SCHEMA,
            "contract_compatibility": "WRITING_COACH_OUTPUT_V1_2_10",
            "identity": identity,
            "student_id": identity.get("student_id"),
            "run": {
                "run_id": run_id,
                "engine_id": ENGINE_ID,
                "engine_version": ENGINE_VERSION,
                "created_at": created,
                "runtime_mode": "standalone_cli",
                "model_used": None,
                "move_bank_id": self.move_bank.bank_id,
                "move_bank_schema_version": self.move_bank.schema_version,
                "resource_version": f"ontology={safe_get(inputs.get('ontology') or {}, 'schema_version', 'unknown')};clusters={safe_get(inputs.get('clusters') or {}, 'schema_version', 'unknown')}",
            },
            "coach_decision": {
                "selected_skill_id": selected_skill.skill_id,
                "selected_skill_name": selected_skill.skill_name,
                "selected_move_id": selected_move.get("move_id"),
                "selected_move_name": selected_move.get("move_name"),
                "target_skill_role_in_move": mission.get("target_skill_role_in_move"),
                "target_skill_role_explanation": mission.get("target_skill_role_explanation"),
                "selection_policy": "move_based_dependency_aware_next_best_microskill_v1_2_10",
                "student_essay_use_policy": "diagnostic_input_and_optional_noticing_only_not_default_repair_material",
                "student_rationale": mission["student_rationale"],
                "teacher_rationale": mission["teacher_rationale"],
                "candidate_rankings": [self._candidate_to_json(c) for c in candidates[:12]],
                "move_candidate_rankings": [asdict(c) for c in move_candidates[:8]],
                "dependency_ready": selected_skill.dependency_ready,
                "blocking_prerequisites": selected_skill.blocking_prerequisites,
            },
            "today_mission": mission,
            "coaching_plan": self._coaching_plan(selected_skill, candidates),
            "next_steps": [
                "Complete today_mission only; do not rewrite the full essay.",
                "Submit the requested numbered output.",
                "Use the mission result to decide repeat, scaffold, or upgrade.",
            ],
            "practice_recommendations": [
                {
                    "target_skill_id": selected_skill.skill_id,
                    "move_id": selected_move.get("move_id"),
                    "exercise_type": "micro_writing_move",
                    "difficulty": selected_skill.recommended_difficulty,
                    "duration_minutes": mission.get("timebox_minutes"),
                    "routing_note": "Writing Coach prescription; Practice Engine may provide supporting drills but should not replace the move mission.",
                }
            ],
            "micro_lesson": self._micro_lesson(selected_skill, selected_move),
            "mastery_state_snapshot": {
                "selected_skill": {
                    "skill_id": selected_skill.skill_id,
                    "skill_name": selected_skill.skill_name,
                    "current_mastery_estimate": selected_skill.mastery_estimate,
                    "status": self._status_from_mastery(selected_skill.mastery_estimate),
                    "confidence": selected_skill.confidence,
                    "evidence_bucket": selected_skill.evaluation_bucket,
                    "note": "Prescription snapshot only; not a mastery update.",
                },
                "nearby_skills": [
                    {
                        "skill_id": c.skill_id,
                        "skill_name": c.skill_name,
                        "mastery_estimate": c.mastery_estimate,
                        "selection_score": c.selection_score,
                        "priority_score": c.priority_score,
                        "status": self._status_from_mastery(c.mastery_estimate),
                        "has_move_bank_entry": bool(self.move_bank.get_for_skill(c.skill_id)),
                    }
                    for c in candidates[:8]
                ],
            },
            "learning_intelligence_payload": self._lie_prescription_payload(run_id, identity, selected_skill, selected_move, mission, created),
            "qa": self._qa(inputs, selected_skill, selected_move, mission, candidates),
        }
        return output

    def _identity(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        detector = inputs.get("detector") or {}
        score_contract = inputs.get("score_contract") or {}
        directive = inputs.get("directive") or {}
        evaluator = inputs.get("evaluator") or {}
        ident = {}
        for source in [safe_get(detector, "results.0.identity", {}), score_contract, directive, safe_get(evaluator, "metadata", {})]:
            if not isinstance(source, dict):
                continue
            for k in ["student_id", "institution_id", "class_id", "teacher_id", "essay_id", "submission_id", "prompt_id", "batch_id", "draft_id", "parent_submission_id", "session_id"]:
                if k not in ident or ident.get(k) in {None, ""}:
                    ident[k] = source.get(k)
        return ident

    def _context(self, inputs: Dict[str, Any], identity: Dict[str, Any]) -> Dict[str, Any]:
        intake = inputs.get("intake") or {}
        detector = inputs.get("detector") or {}
        prompt = safe_get(detector, "results.0.intake_record.prompt_text") or safe_get(detector, "results.0.intake_record.prompt") or ""
        essay_text = safe_get(detector, "results.0.intake_record.essay_text") or safe_get(detector, "results.0.intake_record.raw_text") or ""
        timebox = int(intake.get("practice_minutes_per_day") or 10)
        timebox = max(5, min(20, timebox))
        return {
            "timebox_minutes": timebox,
            "prompt_text": prompt,
            "essay_text": essay_text,
            "domain": "ielts_academic_writing",
            "goal_band": intake.get("goal_band"),
            "session_type": intake.get("session_type"),
            "identity": identity,
        }

    def _candidate_to_json(self, c: SkillCandidate) -> Dict[str, Any]:
        d = asdict(c)
        d["has_move_bank_entry"] = bool(self.move_bank.get_for_skill(c.skill_id))
        return d

    def _coaching_plan(self, selected: SkillCandidate, candidates: List[SkillCandidate]) -> List[Dict[str, Any]]:
        plan = [{
            "priority": 1,
            "skill_id": selected.skill_id,
            "skill_name": selected.skill_name,
            "message": f"Train {selected.skill_name} through one production/transformation writing move.",
            "timebox_minutes": selected.recommended_duration,
            "success_measure": "Pass/partial/fail from observable units, not essay score.",
        }]
        rank = 2
        for c in candidates:
            if c.skill_id == selected.skill_id:
                continue
            if not self.move_bank.get_for_skill(c.skill_id):
                continue
            plan.append({
                "priority": rank,
                "skill_id": c.skill_id,
                "skill_name": c.skill_name,
                "message": f"Monitor next: {c.skill_name}.",
                "condition": f"Train after {selected.skill_name} becomes functional or if new evidence raises priority.",
            })
            rank += 1
            if rank > 5:
                break
        return plan

    # v1.4.13 Gold pipeline fix (stress-test Problem 8): the production move
    # bank (micro_writing_move_bank_v1_flat_adapted.json, MICRO_WRITING_MOVE_V1
    # schema) has no "do"/"avoid" keys on ANY of its 44 moves -- confirmed by
    # direct inspection, this is a systemic schema mismatch, not specific to
    # any one move. Every move instead carries a `success_checklist` (self-
    # check questions) and `observable_units` (scored criteria, each with a
    # human-authored description). Previously `_micro_lesson()` only ever read
    # "do"/"avoid", so micro_lesson.do/avoid shipped empty on every single run
    # regardless of skill or essay. Fixed by falling back to that real content:
    # success_checklist for "do" (already phrased as actionable self-check
    # guidance), and the descriptions of critical observable_units that
    # signal a negative/avoidance constraint for "avoid" -- both sourced
    # directly from vetted move-bank text, no new prose synthesized.
    _AVOID_SIGNAL_TERMS = ("removed", "without", "avoid", "not ", "no ", "vague", "informal")

    def _derive_do_avoid(self, move: Dict[str, Any]) -> Tuple[List[Any], List[Any]]:
        do = list(move.get("do") or [])
        avoid = list(move.get("avoid") or [])
        if not do:
            do = list(move.get("success_checklist") or [])
        if not avoid:
            for unit in (move.get("observable_units") or []):
                if not unit.get("critical"):
                    continue
                desc = str(unit.get("description") or "").strip()
                if desc and any(term in desc.lower() for term in self._AVOID_SIGNAL_TERMS):
                    avoid.append(desc)
        return do, avoid

    def _micro_lesson(self, skill: SkillCandidate, move: Dict[str, Any]) -> Dict[str, Any]:
        do, avoid = self._derive_do_avoid(move)
        return {
            "skill_id": skill.skill_id,
            "title": f"Mini-lesson: {move.get('move_name')}",
            "teaching_point": move.get("teaching_point") or move.get("student_goal") or f"Practise {skill.skill_name} in a short writing move.",
            "do": do,
            "avoid": avoid,
        }

    def _status_from_mastery(self, mastery: float) -> str:
        if mastery < 0.35:
            return "weak"
        if mastery < 0.6:
            return "developing"
        if mastery < 0.78:
            return "emerging"
        return "functional"

    def _lie_prescription_payload(self, run_id: str, identity: Dict[str, Any], skill: SkillCandidate, move: Dict[str, Any], mission: Dict[str, Any], created: str) -> Dict[str, Any]:
        return {
            "source_engine_id": ENGINE_ID,
            "source_run_id": run_id,
            "submission_id": identity.get("submission_id"),
            "essay_id": identity.get("essay_id"),
            "student_id": identity.get("student_id"),
            "profile_type": "coaching_prescription",
            "emission_type": "prescription_only_not_performance_evidence",
            "prescription_signals": [
                {
                    "skill_id": skill.skill_id,
                    "skill_name": skill.skill_name,
                    "move_id": move.get("move_id"),
                    "move_name": move.get("move_name"),
                    "selection_score": skill.selection_score,
                    "priority_score": skill.priority_score,
                    "mastery_estimate_snapshot": skill.mastery_estimate,
                    "confidence": skill.confidence,
                    "status_snapshot": self._status_from_mastery(skill.mastery_estimate),
                    "evidence_bucket": skill.evaluation_bucket,
                    "mission_id": mission.get("mission_id"),
                }
            ],
            "skill_performance_signals": [],
            "metric_signals": [
                {
                    "metric_id": u.get("unit_id"),
                    "target_skill_id": skill.skill_id,
                    "expected_evidence_type": u.get("score_type"),
                    "weight": u.get("weight"),
                }
                for u in mission.get("observable_units") or []
            ],
            "behavioral_events": [
                {
                    "event_id": stable_id("event", run_id, mission.get("mission_id"), n=12),
                    "student_id": identity.get("student_id"),
                    "submission_id": identity.get("submission_id"),
                    "session_id": identity.get("session_id"),
                    "event_type": "micromission_assigned",
                    "skill_ids": [skill.skill_id],
                    "move_id": move.get("move_id"),
                    "mission_id": mission.get("mission_id"),
                    "outcome": "assigned",
                    "created_at": created,
                }
            ],
            "confidence": skill.confidence,
            "privacy_classification": "learning_analytics",
            "notes": ["Prescription only; mastery update requires mission submission and evaluation."],
        }

    def _qa(self, inputs: Dict[str, Any], skill: SkillCandidate, move: Dict[str, Any], mission: Dict[str, Any], candidates: List[SkillCandidate]) -> Dict[str, Any]:
        warnings = []
        errors = []
        if not self.move_bank.get_for_skill(skill.skill_id):
            errors.append("selected_skill_has_no_move_bank_entry")
        if not mission.get("required_output", {}).get("required_items"):
            errors.append("mission_required_items_missing")
        if (move.get("source_policy") or {}).get("own_essay_use") == "default_repair_material":
            errors.append("move_uses_own_essay_as_default_repair_material")
        instr = mission.get("student_instruction", "") + " " + mission.get("student_rationale", "")
        if contains_any(instr, ["detector", "errormap", "priority_score", "control_proxy", "evidence rows"]):
            warnings.append("student_text_may_contain_internal_diagnostics")
        if len(mission.get("observable_units") or []) < 3:
            warnings.append("mission_has_fewer_than_3_observable_units")
        if not mission.get("stimulus", {}).get("items"):
            warnings.append("mission_has_no_stimulus_items")
        if not candidates:
            errors.append("no_skill_candidates_generated")
        if skill.domain == "unknown" or mission.get("target_domain") == "unknown":
            warnings.append("domain_unknown_for_selected_skill")
        if mission.get("mission_version") and "v1_2_10" not in str(mission.get("mission_version")):
            warnings.append("mission_version_label_mismatch")
        role = mission.get("target_skill_role_in_move") or safe_get(mission, "selected_move.target_skill_role_in_move")
        if role == "secondary_target":
            warnings.append("target_skill_is_secondary_inside_selected_move_explained")
        elif role == "compatible_related_target":
            warnings.append("target_skill_not_explicitly_primary_or_secondary_in_selected_move")
        if len(self.move_bank.moves) < 40:
            warnings.append("external_move_bank_dev_size_only_not_production_ready")
        if any(token in f"{self.move_bank.bank_id} {self.move_bank.schema_version}".lower() for token in ["simple", "dev", "test", "demo"]):
            warnings.append("external_move_bank_marked_dev_or_simple")
        source_audit = {
            "evaluator": bool(inputs.get("evaluator")),
            "detector": bool(inputs.get("detector")),
            "errormap": bool(inputs.get("errormap")),
            "scorer": bool(inputs.get("scorer")),
            "verifier": bool(inputs.get("verifier")),
            "adjudicated": bool(inputs.get("adjudicated")),
            "scorer_metrics": bool(inputs.get("scorer_metrics")),
            "score_contract": bool(inputs.get("score_contract")),
            "priority": bool(inputs.get("priority")),
            "directive": bool(inputs.get("directive")),
            "feedback": bool(inputs.get("feedback")),
            "feedback_report": bool(inputs.get("feedback_report")),
            "intake": bool(inputs.get("intake")),
            "move_bank": True,
            "ontology": bool(inputs.get("ontology")),
            "clusters": bool(inputs.get("clusters")),
        }
        status = "error" if errors else "ok_with_warnings" if warnings else "ok"
        return {
            "status": status,
            "warnings": warnings,
            "errors": errors,
            "confidence": skill.confidence,
            "source_audit": source_audit,
            "move_bank_audit": {
                "bank_id": self.move_bank.bank_id,
                "schema_version": self.move_bank.schema_version,
                "move_count": len(self.move_bank.moves),
                "warnings": self.move_bank.warnings,
                "errors": self.move_bank.errors,
            },
        }




# ---------------------------------------------------------------------------
# Proactive adaptive planning layer (V1.2)
# ---------------------------------------------------------------------------


class ProactiveAdaptiveWritingCoach(MoveBasedWritingCoach):
    """V1.2 wrapper around the move-based coach.

    V1.2.10 selects a skill, generates today's move mission, and plans the proactive cycle:
    - V1.2 introduced proactive planning;
    - V1.2.10 is the freeze-candidate consistency/safety patch;
    - after-essay prescription starts/refreshed an active cycle;
    - daily continuation can run from saved coach state + latest mission result;
    - output includes rolling 7-day adaptive plan, next-run trigger, home card, and state export.
    """

    def generate(self, inputs: Dict[str, Any], coach_state: Optional[Dict[str, Any]] = None,
                 last_result: Optional[Dict[str, Any]] = None, plan_horizon_days: int = 7) -> Dict[str, Any]:
        base = super().generate(inputs)
        base["schema_version"] = OUTPUT_SCHEMA
        base["contract_compatibility"] = "WRITING_COACH_OUTPUT_V1_2_10"
        base["run"]["engine_version"] = ENGINE_VERSION
        base["run"]["planner_mode"] = "after_essay_prescription_or_refresh"
        base["run"]["plan_horizon_days"] = int(plan_horizon_days)

        identity = base.get("identity") or {}
        mission = base.get("today_mission") or {}
        selected = base.get("coach_decision") or {}
        candidates = selected.get("candidate_rankings") or []
        move_candidates = selected.get("move_candidate_rankings") or []
        history = self._history_with_last(coach_state, last_result)
        cycle = self._build_active_cycle(
            identity=identity,
            selected=selected,
            mission=mission,
            candidates=candidates,
            coach_state=coach_state,
            last_result=last_result,
            history=history,
            created=base["run"].get("created_at") or now_iso(),
        )
        rolling_plan = self._build_rolling_plan(cycle, mission, candidates, history, int(plan_horizon_days))
        state_export = self._coach_state_export(base, cycle, rolling_plan, history, candidates, move_candidates)

        old_priority_queue = base.pop("coaching_plan", [])
        base["active_coaching_cycle"] = cycle
        base["coaching_plan"] = rolling_plan
        base["skill_priority_queue"] = old_priority_queue
        base["student_home_card"] = self._student_home_card(cycle, mission, rolling_plan)
        base["next_run_trigger"] = self._next_run_trigger(cycle)
        base["daily_coach_policy"] = self._daily_coach_policy(cycle)
        base["mission_result_routing"] = self._mission_result_routing(cycle)
        base["weekly_review_policy"] = self._weekly_review_policy(cycle)
        base["plan_refresh_policy"] = self._plan_refresh_policy(cycle)
        base["coach_state_export"] = state_export
        base["learning_intelligence_payload"]["active_cycle_id"] = cycle.get("cycle_id")
        base["learning_intelligence_payload"]["plan_horizon_days"] = int(plan_horizon_days)
        base["learning_intelligence_payload"]["behavioral_events"].append({
            "event_id": stable_id("event", base["run"].get("run_id"), "cycle_plan_created", n=12),
            "student_id": identity.get("student_id"),
            "submission_id": identity.get("submission_id"),
            "session_id": identity.get("session_id"),
            "event_type": "coaching_cycle_planned",
            "cycle_id": cycle.get("cycle_id"),
            "mission_id": mission.get("mission_id"),
            "outcome": "planned",
            "created_at": base["run"].get("created_at"),
        })
        qa = base.get("qa") or {}
        qa.setdefault("warnings", [])
        qa.setdefault("errors", [])
        if not rolling_plan.get("daily_slots"):
            qa["warnings"].append("rolling_plan_has_no_daily_slots")
        if not base.get("next_run_trigger"):
            qa["warnings"].append("next_run_trigger_missing")
        qa["planner_audit"] = {
            "active_cycle_created": bool(cycle.get("cycle_id")),
            "rolling_plan_created": bool(rolling_plan.get("daily_slots")),
            "coach_state_export_created": bool(state_export),
            "student_home_card_created": bool(base.get("student_home_card")),
            "daily_continuation_supported": True,
        }
        semantic_checks = {
            "version_labels_v1_2_10": (
                base.get("contract_compatibility") == "WRITING_COACH_OUTPUT_V1_2_10"
                and "v1_2_10" in str(mission.get("mission_version", ""))
            ),
            "selected_skill_domain_known": mission.get("target_domain") != "unknown",
            "target_skill_role_declared": bool(mission.get("target_skill_role_in_move")),
            "move_bank_production_ready": len(self.move_bank.moves) >= 40 and not any(token in f"{self.move_bank.bank_id} {self.move_bank.schema_version}".lower() for token in ["simple", "dev", "test", "demo"]),
            "lie_policy_platform_preferred": base.get("next_run_trigger", {}).get("requires_lie_profile") == "platform_preferred_standalone_optional",
        }
        qa["semantic_consistency_audit"] = semantic_checks
        for check_name, ok in semantic_checks.items():
            if not ok and check_name != "move_bank_production_ready":
                qa.setdefault("warnings", []).append(check_name + "_failed")
        qa["status"] = "error" if qa.get("errors") else "ok_with_warnings" if qa.get("warnings") else "ok"
        base["qa"] = qa
        return base

    def generate_daily(self, coach_state: Dict[str, Any], last_result: Optional[Dict[str, Any]] = None,
                       inputs: Optional[Dict[str, Any]] = None, plan_horizon_days: int = 7) -> Dict[str, Any]:
        """Generate today's mission from saved coach state; no new essay required."""
        inputs = inputs or {}
        last_result_valid = is_valid_mission_result(last_result)
        history = self._history_with_last(coach_state, last_result)
        active = self._active_from_state(coach_state)
        unlocked_skill = self._maybe_unlock_next_skill(active, coach_state, history)
        if unlocked_skill:
            active = unlocked_skill
        selected_skill = self._skill_from_active_state(active, history)
        context = self._context_from_state(coach_state, inputs)
        selected_move, move_candidates = self.move_selector.select(selected_skill, context)
        mission = self.mission_builder.build(selected_skill, selected_move, move_candidates, {**context, **inputs})
        if last_result_valid and not is_mastery_valid_result(last_result):
            # V1.2.10: incomplete/invalid attempts are not upgrades. Reassign
            # the same pending mission so the student can complete it.
            previous_mid = (last_result or {}).get("mission_id") or coach_state.get("last_assigned_mission_id")
            if previous_mid:
                mission["mission_id"] = previous_mid
            mission["reassignment_policy"] = {
                "reassigned_after_invalid_or_incomplete_attempt": True,
                "previous_outcome": (last_result or {}).get("outcome"),
                "reason": "previous_attempt_not_mastery_evidence",
                "student_message": "Please complete the same mission before moving on.",
            }
        created = now_iso()
        identity = coach_state.get("identity") or context.get("identity") or {}
        run_id = stable_id("run", identity.get("student_id"), selected_skill.skill_id, created, n=16)
        selected = {
            "selected_skill_id": selected_skill.skill_id,
            "selected_skill_name": selected_skill.skill_name,
            "selected_move_id": selected_move.get("move_id"),
            "selected_move_name": selected_move.get("move_name"),
            "target_skill_role_in_move": mission.get("target_skill_role_in_move"),
            "target_skill_role_explanation": mission.get("target_skill_role_explanation"),
            "selection_policy": "proactive_daily_continuation_v1_2_10",
            "student_essay_use_policy": "no_new_essay_required_use_saved_cycle_state",
            "student_rationale": mission.get("student_rationale"),
            "teacher_rationale": "Daily continuation generated from coach_state_export and latest mission result.",
            "candidate_rankings": [self._candidate_to_json(selected_skill)] + list(coach_state.get("next_skill_queue", []))[:8],
            "move_candidate_rankings": [asdict(c) for c in move_candidates[:8]],
            "dependency_ready": selected_skill.dependency_ready,
            "blocking_prerequisites": selected_skill.blocking_prerequisites,
        }
        cycle = self._build_active_cycle(identity, selected, mission, selected.get("candidate_rankings", []), coach_state, last_result, history, created)
        rolling_plan = self._build_rolling_plan(cycle, mission, selected.get("candidate_rankings", []), history, int(plan_horizon_days))
        output = {
            "schema_version": OUTPUT_SCHEMA,
            "contract_compatibility": "WRITING_COACH_OUTPUT_V1_2_10",
            "identity": identity,
            "student_id": identity.get("student_id"),
            "run": {
                "run_id": run_id,
                "engine_id": ENGINE_ID,
                "engine_version": ENGINE_VERSION,
                "created_at": created,
                "runtime_mode": "standalone_cli",
                "planner_mode": "daily_continuation_no_new_essay",
                "model_used": None,
                "move_bank_id": self.move_bank.bank_id,
                "move_bank_schema_version": self.move_bank.schema_version,
                "resource_version": "daily_state_continuation",
                "plan_horizon_days": int(plan_horizon_days),
            },
            "coach_decision": selected,
            "today_mission": mission,
            "active_coaching_cycle": cycle,
            "coaching_plan": rolling_plan,
            "skill_priority_queue": list(coach_state.get("next_skill_queue", []))[:8],
            "next_steps": [
                "Complete today_mission only; do not rewrite the full essay.",
                "Submit the requested numbered output.",
                "The next daily run should use coach_state_export plus the mission result.",
            ],
            "student_home_card": self._student_home_card(cycle, mission, rolling_plan),
            "next_run_trigger": self._next_run_trigger(cycle),
            "daily_coach_policy": self._daily_coach_policy(cycle),
            "mission_result_routing": self._mission_result_routing(cycle),
            "weekly_review_policy": self._weekly_review_policy(cycle),
            "plan_refresh_policy": self._plan_refresh_policy(cycle),
            "practice_recommendations": [{
                "target_skill_id": selected_skill.skill_id,
                "move_id": selected_move.get("move_id"),
                "exercise_type": "micro_writing_move",
                "difficulty": selected_skill.recommended_difficulty,
                "duration_minutes": mission.get("timebox_minutes"),
                "routing_note": "Daily Writing Coach continuation; Practice Engine may support but should not replace the move mission.",
            }],
            "micro_lesson": self._micro_lesson(selected_skill, selected_move),
            "mastery_state_snapshot": {
                "selected_skill": {
                    "skill_id": selected_skill.skill_id,
                    "skill_name": selected_skill.skill_name,
                    "current_mastery_estimate": selected_skill.mastery_estimate,
                    "status": self._status_from_mastery(selected_skill.mastery_estimate),
                    "confidence": selected_skill.confidence,
                    "evidence_bucket": selected_skill.evaluation_bucket,
                    "note": "Daily continuation snapshot only; mastery update requires mission result.",
                },
                "recent_history": history[-5:],
            },
            "learning_intelligence_payload": self._lie_prescription_payload(run_id, identity, selected_skill, selected_move, mission, created),
            "qa": {
                "status": "ok",
                "warnings": [],
                "errors": [],
                "confidence": selected_skill.confidence,
                "source_audit": {
                    "coach_state": True,
                    "last_mission_result": last_result_valid,
                    "move_bank": True,
                    "new_essay_required": False,
                    "ontology": bool(inputs.get("ontology")),
                    "clusters": bool(inputs.get("clusters")),
                },
                "move_bank_audit": {
                    "bank_id": self.move_bank.bank_id,
                    "schema_version": self.move_bank.schema_version,
                    "move_count": len(self.move_bank.moves),
                    "warnings": self.move_bank.warnings,
                    "errors": self.move_bank.errors,
                },
                "planner_audit": {
                    "daily_continuation_supported": True,
                    "used_previous_result": last_result_valid,
                    "unlocked_next_skill": bool(unlocked_skill),
                },
            },
        }
        output["learning_intelligence_payload"]["active_cycle_id"] = cycle.get("cycle_id")
        # V1.2.10 freeze-candidate semantic QA for daily continuation.
        qa = output.get("qa") or {}
        qa.setdefault("warnings", [])
        if last_result and not last_result_valid:
            qa["warnings"].append("previous_mission_result_missing_or_invalid")
            qa["result_status"] = "no_valid_previous_result"
        elif last_result_valid and not is_mastery_valid_result(last_result):
            qa["warnings"].append("previous_attempt_not_mastery_evidence_reassigning_same_mission")
            qa["result_status"] = "valid_attempt_not_mastery_evidence"
        else:
            qa["result_status"] = "valid_previous_result" if last_result_valid else "no_previous_result_supplied"
        if selected_skill.domain == "unknown" or mission.get("target_domain") == "unknown":
            qa["warnings"].append("domain_unknown_for_selected_skill")
        if mission.get("target_skill_role_in_move") == "secondary_target":
            qa["warnings"].append("target_skill_is_secondary_inside_selected_move_explained")
        if len(self.move_bank.moves) < 40:
            qa["warnings"].append("external_move_bank_dev_size_only_not_production_ready")
        if any(token in f"{self.move_bank.bank_id} {self.move_bank.schema_version}".lower() for token in ["simple", "dev", "test", "demo"]):
            qa["warnings"].append("external_move_bank_marked_dev_or_simple")
        qa["semantic_consistency_audit"] = {
            "version_labels_v1_2_10": output.get("contract_compatibility") == "WRITING_COACH_OUTPUT_V1_2_10" and "v1_2_10" in str(mission.get("mission_version", "")),
            "selected_skill_domain_known": mission.get("target_domain") != "unknown",
            "target_skill_role_declared": bool(mission.get("target_skill_role_in_move")),
            "move_bank_production_ready": len(self.move_bank.moves) >= 40 and not any(token in f"{self.move_bank.bank_id} {self.move_bank.schema_version}".lower() for token in ["simple", "dev", "test", "demo"]),
            "lie_policy_platform_preferred": output.get("next_run_trigger", {}).get("requires_lie_profile") == "platform_preferred_standalone_optional",
        }
        qa["status"] = "error" if qa.get("errors") else "ok_with_warnings" if qa.get("warnings") else "ok"
        output["qa"] = qa
        output["coach_state_export"] = self._coach_state_export(output, cycle, rolling_plan, history, selected.get("candidate_rankings", []), selected.get("move_candidate_rankings", []))
        return output

    def weekly_review(self, coach_state: Dict[str, Any], last_result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        history = self._history_with_last(coach_state, last_result)
        active = self._active_from_state(coach_state)
        valid = [h for h in history if h.get("outcome") in {"pass", "partial_pass", "fail"}]
        recent = valid[-7:]
        pass_count = sum(1 for h in recent if h.get("outcome") == "pass")
        avg = round(sum(float(h.get("score", 0.0)) for h in recent) / max(1, len(recent)), 3)
        functional = self._functional_achieved(history, active.get("skill_id"))
        stable = self._stable_achieved(history, active.get("skill_id"))
        return {
            "schema_version": "WRITING_COACH_WEEKLY_REVIEW_V1_2_1",
            "created_at": now_iso(),
            "cycle_id": coach_state.get("cycle_id") or safe_get(coach_state, "active_coaching_cycle.cycle_id"),
            "active_skill_id": active.get("skill_id"),
            "active_skill_name": active.get("skill_name"),
            "valid_missions_reviewed": len(recent),
            "pass_count": pass_count,
            "average_score": avg,
            "functional_achieved": functional,
            "stable_achieved": stable,
            "recommendation": "unlock_next_skill" if functional else "continue_active_skill",
            "student_summary": self._weekly_student_summary(active, recent, avg, functional, stable),
            "learning_intelligence_payload": {
                "source_engine_id": ENGINE_ID,
                "profile_type": "weekly_coaching_review",
                "emission_type": "learning_analytics_summary",
                "active_skill_id": active.get("skill_id"),
                "valid_missions_reviewed": len(recent),
                "pass_count": pass_count,
                "average_score": avg,
                "functional_achieved": functional,
                "stable_achieved": stable,
            },
        }

    # -------------------------- planning helpers --------------------------

    def _history_with_last(self, coach_state: Optional[Dict[str, Any]], last_result: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        history = []
        if isinstance(coach_state, dict):
            for h in coach_state.get("mission_history", []) or safe_get(coach_state, "coach_state_export.mission_history", []) or []:
                if isinstance(h, dict):
                    history.append(dict(h))
        if is_valid_mission_result(last_result):
            row = normalize_mission_result_for_history(last_result)
            if row.get("result_id") not in {h.get("result_id") for h in history}:
                history.append(row)
        return history[-30:]

    def _build_active_cycle(self, identity: Dict[str, Any], selected: Dict[str, Any], mission: Dict[str, Any],
                            candidates: List[Dict[str, Any]], coach_state: Optional[Dict[str, Any]],
                            last_result: Optional[Dict[str, Any]], history: List[Dict[str, Any]], created: str) -> Dict[str, Any]:
        skill_id = selected.get("selected_skill_id") or mission.get("target_skill_id")
        skill_name = selected.get("selected_skill_name") or mission.get("target_skill_name") or prettify_skill(skill_id)
        move_id = selected.get("selected_move_id") or safe_get(mission, "selected_move.move_id")
        old_cycle_id = None
        if isinstance(coach_state, dict):
            old_cycle_id = coach_state.get("cycle_id") or safe_get(coach_state, "active_coaching_cycle.cycle_id")
            old_active = coach_state.get("active_skill_id") or safe_get(coach_state, "active_coaching_cycle.active_skill.skill_id")
            if old_active and old_active != skill_id:
                old_cycle_id = None
        cycle_id = old_cycle_id or stable_id("cycle", identity.get("student_id"), skill_id, identity.get("submission_id") or created, n=12)
        valid_for_skill = [h for h in history if h.get("skill_id") == skill_id and h.get("outcome") in {"pass", "partial_pass", "fail"}]
        functional = self._functional_achieved(history, skill_id)
        stable = self._stable_achieved(history, skill_id)
        status = "stable_ready" if stable else "functional_ready" if functional else "active"
        queue = self._next_skill_queue(skill_id, candidates)
        return {
            "cycle_id": cycle_id,
            "cycle_type": "proactive_adaptive_skill_cycle",
            "status": status,
            "started_at": safe_get(coach_state or {}, "started_at") or safe_get(coach_state or {}, "active_coaching_cycle.started_at") or created,
            "last_updated_at": created,
            "started_from_submission_id": identity.get("submission_id"),
            "requires_new_essay": False,
            "active_skill": {
                "skill_id": skill_id,
                "skill_name": skill_name,
                "target_status": "functional",
                "current_status": self._status_from_mastery(self._candidate_value(candidates, skill_id, "mastery_estimate")),
                "domain": mission.get("target_domain") or self._candidate_value(candidates, skill_id, "domain") or "unknown",
                "mastery_estimate_snapshot": self._candidate_value(candidates, skill_id, "mastery_estimate"),
                "selection_score_snapshot": self._candidate_value(candidates, skill_id, "selection_score"),
            },
            "active_move": {
                "move_id": move_id,
                "move_name": selected.get("selected_move_name") or safe_get(mission, "selected_move.move_name"),
                "move_family": safe_get(mission, "selected_move.move_family"),
                "training_mode": safe_get(mission, "selected_move.training_mode"),
            },
            "goal_for_cycle": f"Move {skill_name} from developing/emerging control to functional use in short writing production.",
            "success_rules": {
                "functional_rule": "pass 2 of last 3 valid missions with average >= 0.75 and no critical failure",
                "stable_rule": "pass 4 of last 5 valid missions with average >= 0.80 across at least 2 topics when topic data is available",
                "unlock_rule": "unlock next queued skill after functional_rule is met, unless a new essay refreshes priorities",
            },
            "progress_snapshot": {
                "valid_missions_for_active_skill": len(valid_for_skill),
                "recent_outcomes": [h.get("outcome") for h in valid_for_skill[-5:]],
                "recent_average": self._avg_score(valid_for_skill[-5:]),
                "functional_achieved": functional,
                "stable_achieved": stable,
                "last_result_outcome": (last_result or {}).get("outcome"),
            },
            "next_skill_queue": queue,
        }

    def _build_rolling_plan(self, cycle: Dict[str, Any], mission: Dict[str, Any], candidates: List[Dict[str, Any]],
                            history: List[Dict[str, Any]], horizon_days: int) -> Dict[str, Any]:
        skill = cycle.get("active_skill") or {}
        move = cycle.get("active_move") or {}
        horizon_days = max(3, min(14, int(horizon_days or 7)))
        slots = []
        slots.append({
            "day_index": 1,
            "status": "assigned",
            "skill_id": skill.get("skill_id"),
            "skill_name": skill.get("skill_name"),
            "move_id": move.get("move_id"),
            "move_family": move.get("move_family"),
            "mission_id": mission.get("mission_id"),
            "student_task": mission.get("title"),
            "timebox_minutes": mission.get("timebox_minutes", 10),
        })
        if horizon_days >= 2:
            slots.append({
                "day_index": 2,
                "status": "conditional",
                "if_previous_pass": "repeat same active skill with a new near-transfer topic or slightly less scaffold",
                "if_previous_partial_pass": "repeat same move family with one extra model/scaffold",
                "if_previous_fail": "repeat same microskill with fewer items and heavier support",
            })
        if horizon_days >= 3:
            slots.append({
                "day_index": 3,
                "status": "conditional",
                "if_two_recent_passes": "mark active skill functional and unlock the next queued skill if no new essay changes priorities",
                "otherwise": "continue active skill until functional_rule is met",
            })
        if horizon_days >= 4:
            slots.append({
                "day_index": 4,
                "status": "planned_policy",
                "action": "near_transfer_or_upgrade",
                "rule": "use a new topic or a harder transformation only if the previous result was pass/partial_pass",
            })
        if horizon_days >= 5:
            slots.append({
                "day_index": 5,
                "status": "planned_policy",
                "action": "mini_review_or_mixed_transfer",
                "rule": "brief review of active skill plus one contrast with a nearby skill",
            })
        if horizon_days >= 6:
            slots.append({
                "day_index": 6,
                "status": "optional",
                "action": "essay_application_checkpoint",
                "rule": "apply the active skill to one paragraph or one essay-plan component, not a full rewrite",
            })
        if horizon_days >= 7:
            slots.append({
                "day_index": 7,
                "status": "review",
                "action": "weekly_mastery_review",
                "rule": "summarize outcomes, decide continue/unlock/refresh from new essay",
            })
        for d in range(8, horizon_days + 1):
            slots.append({
                "day_index": d,
                "status": "rolling_extension",
                "action": "continue_cycle_or_refresh_from_latest_evidence",
            })
        return {
            "plan_id": stable_id("plan", cycle.get("cycle_id"), mission.get("mission_id"), n=12),
            "plan_type": "adaptive_rolling_plan",
            "horizon_days": horizon_days,
            "fixed_or_adaptive": "adaptive_not_fixed",
            "student_visible_summary": f"This cycle trains {skill.get('skill_name')} through short writing moves. Today: {mission.get('title')}.",
            "system_goal": cycle.get("goal_for_cycle"),
            "daily_slots": slots,
            "do_not_pre_generate_all_missions": True,
            "why_not_fixed": "Future missions depend on pass/partial/fail results, new essay evidence, and LIE mastery updates.",
        }

    def _student_home_card(self, cycle: Dict[str, Any], mission: Dict[str, Any], rolling_plan: Dict[str, Any]) -> Dict[str, Any]:
        skill = safe_get(cycle, "active_skill.skill_name", "today's writing skill")
        return {
            "title": "Today’s 10-minute Writing Coach",
            "message": f"Practise {skill.lower()} through one short writing move.",
            "button_text": "Start today’s mission",
            "mission_title": mission.get("title"),
            "timebox_minutes": mission.get("timebox_minutes", 10),
            "weekly_focus": skill,
            "streak_goal": "Complete 3 Writing Coach missions this week.",
            "student_visible_plan_summary": rolling_plan.get("student_visible_summary"),
        }

    def _next_run_trigger(self, cycle: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "trigger_type": "after_mission_submission_or_next_scheduled_day",
            "default_frequency": "daily_when_student_is_active",
            "minimum_interval_hours": 20,
            "requires_new_essay": False,
            "requires_lie_profile": "platform_preferred_standalone_optional",
            "requires_coach_state": True,
            "requires_previous_mission_result": "preferred_but_not_required_for_first_daily_continuation",
            "next_mode": "daily_continuation_no_new_essay",
            "input_contract": ["coach_state_export", "latest_mission_result_optional", "external_move_bank"],
        }

    def _daily_coach_policy(self, cycle: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "student_should_run_manually": False,
            "product_should_assign_proactively": True,
            "new_essay_required_each_day": False,
            "lie_profile_policy": "preferred in platform mode; optional in standalone CLI mode",
            "daily_decision_order": [
                "read coach_state_export",
                "read latest mission result if available",
                "check functional/stable rules",
                "repeat, scaffold, upgrade, or unlock next skill",
                "generate one new today_mission",
            ],
        }

    def _mission_result_routing(self, cycle: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "on_pass": "record performance evidence; repeat same skill once with new topic or unlock if functional_rule is met",
            "on_partial_pass": "record partial evidence; repeat same move family with one more scaffold",
            "on_fail": "record weak evidence; repeat same microskill with easier stimulus and fewer items",
            "on_invalid": "do not update mastery; reassign the same mission or clarify output format",
            "after_functional": "unlock next skill from next_skill_queue unless new essay refreshes priorities",
        }

    def _weekly_review_policy(self, cycle: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "review_after_valid_missions": 5,
            "review_after_days": 7,
            "outputs": ["student_progress_summary", "active_skill_status", "unlock_or_continue_decision", "next_week_focus"],
            "requires_human_review": False,
        }

    def _plan_refresh_policy(self, cycle: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "refresh_when_new_essay_scored": True,
            "refresh_when_revision_result_available": True,
            "refresh_when_lie_detects_regression": True,
            "preserve_cycle_if_same_skill_remains_top_priority": True,
            "do_not_discard_history": True,
        }

    def _coach_state_export(self, output: Dict[str, Any], cycle: Dict[str, Any], rolling_plan: Dict[str, Any],
                            history: List[Dict[str, Any]], candidates: List[Dict[str, Any]],
                            move_candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
        mission = output.get("today_mission") or {}
        identity = output.get("identity") or {}
        state = {
            "schema_version": STATE_SCHEMA,
            "created_at": output.get("run", {}).get("created_at") or now_iso(),
            "generated_by_run_id": output.get("run", {}).get("run_id"),
            "identity": identity,
            "cycle_id": cycle.get("cycle_id"),
            "active_coaching_cycle": cycle,
            "active_skill_id": safe_get(cycle, "active_skill.skill_id"),
            "active_skill_name": safe_get(cycle, "active_skill.skill_name"),
            "active_move_id": safe_get(cycle, "active_move.move_id"),
            "active_move_family": safe_get(cycle, "active_move.move_family"),
            "last_assigned_mission_id": mission.get("mission_id"),
            "last_assigned_move_id": safe_get(mission, "selected_move.move_id"),
            "mission_history": history,
            "next_skill_queue": cycle.get("next_skill_queue") or self._next_skill_queue(safe_get(cycle, "active_skill.skill_id"), candidates),
            "plan_horizon_days": rolling_plan.get("horizon_days", 7),
            "prompt_text": mission.get("source_prompt"),
            "topic_context": safe_get(mission, "stimulus.topic_context"),
            "domain": safe_get(mission, "domain_adapter.domain", "ielts_academic_writing"),
            "timebox_minutes": mission.get("timebox_minutes", 10),
            "selected_move_candidate_snapshot": move_candidates[:5],
            "daily_run_contract": {
                "mode": "daily_continuation_no_new_essay",
                "required_inputs": ["coach_state_export", "external_move_bank"],
                "optional_inputs": ["last_mission_result", "ontology", "clusters"],
            },
        }
        return normalize_coach_state_contract(state)

    def _active_from_state(self, coach_state: Dict[str, Any]) -> Dict[str, Any]:
        cycle = coach_state.get("active_coaching_cycle") or {}
        return {
            "skill_id": coach_state.get("active_skill_id") or safe_get(cycle, "active_skill.skill_id"),
            "skill_name": coach_state.get("active_skill_name") or safe_get(cycle, "active_skill.skill_name"),
            "move_id": coach_state.get("active_move_id") or safe_get(cycle, "active_move.move_id"),
            "move_family": coach_state.get("active_move_family") or safe_get(cycle, "active_move.move_family"),
            "mastery_estimate": safe_get(cycle, "active_skill.mastery_estimate_snapshot", 0.5),
            "selection_score": safe_get(cycle, "active_skill.selection_score_snapshot", 0.5),
        }

    def _maybe_unlock_next_skill(self, active: Dict[str, Any], coach_state: Dict[str, Any], history: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        skill_id = active.get("skill_id")
        if not skill_id or not self._functional_achieved(history, skill_id):
            return None
        queue = coach_state.get("next_skill_queue") or safe_get(coach_state, "active_coaching_cycle.next_skill_queue", []) or []
        for q in queue:
            if not isinstance(q, dict):
                continue
            sid = q.get("skill_id")
            if sid and sid != skill_id and self.move_bank.get_for_skill(sid):
                return {
                    "skill_id": sid,
                    "skill_name": q.get("skill_name") or self.resources.name(sid),
                    "move_id": None,
                    "move_family": None,
                    "mastery_estimate": q.get("mastery_estimate", 0.5),
                    "selection_score": q.get("selection_score", 0.5),
                    "unlocked_from_previous_skill": skill_id,
                }
        return None

    def _skill_from_active_state(self, active: Dict[str, Any], history: List[Dict[str, Any]]) -> SkillCandidate:
        sid = active.get("skill_id")
        if not sid:
            raise ValueError("coach_state does not contain active_skill_id.")
        outcome = (history[-1].get("outcome") if history else None)
        difficulty = "scaffolded" if outcome in {"fail", "invalid"} else "controlled" if outcome == "partial_pass" else "near_transfer"
        mastery = clamp(active.get("mastery_estimate", 0.5), default=0.5)
        if outcome == "pass":
            mastery = clamp(mastery + 0.06)
        elif outcome == "partial_pass":
            mastery = clamp(mastery + 0.025)
        elif outcome == "fail":
            mastery = clamp(mastery - 0.035)
        return SkillCandidate(
            skill_id=sid,
            skill_name=active.get("skill_name") or self.resources.name(sid),
            domain=self.resources.domain(sid),
            mastery_estimate=round(mastery, 3),
            selection_score=round(clamp(active.get("selection_score", 0.5), default=0.5), 3),
            priority_score=round(clamp(active.get("selection_score", 0.5), default=0.5), 3),
            pressure=0.5,
            confidence=0.66,
            evidence_count=max(1, len([h for h in history if h.get("skill_id") == sid])),
            evaluation_bucket=self.resources.bucket(sid),
            sources=["coach_state", "latest_mission_result" if history else "coach_state_only"],
            evidence_ids=[h.get("result_id") for h in history[-5:] if h.get("result_id")],
            families=[],
            reasons=["Daily continuation from active coaching cycle.", f"Latest outcome: {outcome or 'none yet'}.", "No new essay is required for daily continuation."],
            prerequisites=[],
            dependency_ready=True,
            blocking_prerequisites=[],
            recommended_difficulty=difficulty,
            recommended_duration=10,
        )

    def _context_from_state(self, coach_state: Dict[str, Any], inputs: Dict[str, Any]) -> Dict[str, Any]:
        prompt = coach_state.get("prompt_text") or coach_state.get("topic_context") or safe_get(inputs, "detector.results.0.intake_record.prompt_text") or "the current writing topic"
        timebox = int(coach_state.get("timebox_minutes") or 10)
        timebox = max(5, min(20, timebox))
        identity = coach_state.get("identity") or safe_get(inputs, "detector.results.0.identity", {}) or {}
        return {
            "timebox_minutes": timebox,
            "prompt_text": prompt,
            "essay_text": "",
            "domain": coach_state.get("domain") or "ielts_academic_writing",
            "goal_band": None,
            "session_type": "daily_continuation",
            "identity": identity,
        }

    def _next_skill_queue(self, active_skill_id: str, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        queue = []
        for c in candidates or []:
            if not isinstance(c, dict):
                continue
            sid = c.get("skill_id")
            if not sid or sid == active_skill_id:
                continue
            if not self.move_bank.get_for_skill(sid):
                continue
            queue.append({
                "skill_id": sid,
                "skill_name": c.get("skill_name") or self.resources.name(sid),
                "mastery_estimate": c.get("mastery_estimate"),
                "selection_score": c.get("selection_score"),
                "priority_score": c.get("priority_score"),
                "dependency_ready": c.get("dependency_ready"),
                "condition": f"Unlock after {prettify_skill(active_skill_id)} becomes functional or if a new essay refreshes priorities.",
            })
            if len(queue) >= 8:
                break
        return queue

    def _functional_achieved(self, history: List[Dict[str, Any]], skill_id: Optional[str]) -> bool:
        rows = [h for h in history if h.get("skill_id") == skill_id and h.get("outcome") in {"pass", "partial_pass", "fail"}]
        recent = rows[-3:]
        if len(recent) < 2:
            return False
        pass_count = sum(1 for h in recent if h.get("outcome") == "pass")
        avg = self._avg_score(recent)
        crit = any(bool(h.get("critical_failure")) for h in recent)
        return pass_count >= 2 and avg >= 0.75 and not crit

    def _stable_achieved(self, history: List[Dict[str, Any]], skill_id: Optional[str]) -> bool:
        rows = [h for h in history if h.get("skill_id") == skill_id and h.get("outcome") in {"pass", "partial_pass", "fail"}]
        recent = rows[-5:]
        if len(recent) < 5:
            return False
        pass_count = sum(1 for h in recent if h.get("outcome") == "pass")
        avg = self._avg_score(recent)
        crit = any(bool(h.get("critical_failure")) for h in recent)
        return pass_count >= 4 and avg >= 0.80 and not crit

    def _avg_score(self, rows: List[Dict[str, Any]]) -> float:
        vals = []
        for h in rows or []:
            try:
                vals.append(float(h.get("score", 0.0)))
            except Exception:
                pass
        return round(sum(vals) / max(1, len(vals)), 3)

    def _candidate_value(self, candidates: List[Dict[str, Any]], skill_id: str, key: str) -> Any:
        for c in candidates or []:
            if isinstance(c, dict) and c.get("skill_id") == skill_id:
                return c.get(key)
        return None

    def _weekly_student_summary(self, active: Dict[str, Any], recent: List[Dict[str, Any]], avg: float, functional: bool, stable: bool) -> str:
        skill = active.get("skill_name") or prettify_skill(active.get("skill_id", "active skill"))
        if stable:
            return f"You have shown stable control of {skill}. The Coach can move to a new focus."
        if functional:
            return f"You have shown functional control of {skill}. One transfer task or the next skill can follow."
        if recent:
            return f"You are still building {skill}. Average recent mission score: {avg}. Continue short daily writing moves."
        return f"This cycle is ready to start training {skill}."




def terminal_attempt_feedback_text(result: Dict[str, Any]) -> str:
    sf = result.get("student_feedback") or {}
    lines = []
    overall = sf.get("overall_comment") or safe_get(result, "feedback.summary")
    if overall:
        lines.append(overall)
    numbering = sf.get("numbering_feedback")
    if numbering:
        lines.append(f"Numbering: {numbering}")
    for item in sf.get("item_feedback") or []:
        if item.get("status") != "submitted":
            continue
        num = item.get("item_number")
        lines.append("")
        lines.append(f"Item {num} feedback:")
        lines.append(f"Your sentence: {item.get('student_sentence')}")
        strengths = item.get("strengths") or []
        issues = item.get("issues") or []
        quality = item.get("sentence_quality_level")
        if quality:
            lines.append("Level: " + str(quality))
        if strengths:
            lines.append("What works: " + "; ".join(str(x) for x in strengths[:4]))
        if issues:
            lines.append("Fix/improve: " + "; ".join(str(x) for x in issues[:5]))
        else:
            lines.append("Fix/improve: no accuracy problem detected; try the suggested version to raise the level.")
        if item.get("how_to_improve"):
            lines.append("How: " + str(item.get("how_to_improve")))
        if item.get("suggested_revision"):
            lines.append("Suggested version: " + str(item.get("suggested_revision")))
        if item.get("explanation"):
            lines.append("Why: " + str(item.get("explanation")))
    missing = sf.get("missing_items") or []
    if missing:
        lines.append("")
        lines.append("Missing items: " + ", ".join(map(str, missing)))
        if sf.get("try_again_instruction"):
            lines.append(str(sf.get("try_again_instruction")))
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# V1.2.10 Adaptive Mission Variant Generator + Focused Retry Overrides
# ---------------------------------------------------------------------------

ADAPTIVE_VARIANT_SCHEMA = "WRITING_COACH_ADAPTIVE_VARIANT_V1_2_10"


def _expected_item_numbers_from_mission(mission: Dict[str, Any]) -> List[int]:
    ro = mission.get("required_output") or {}
    nums = ro.get("expected_item_numbers") or ro.get("required_item_numbers")
    if isinstance(nums, list):
        out = []
        for n in nums:
            try:
                i = int(n)
                if i > 0 and i not in out:
                    out.append(i)
            except Exception:
                pass
        if out:
            return out
    items = safe_get(mission, "stimulus.items", []) or []
    from_items = []
    for idx, item in enumerate(items, 1):
        if isinstance(item, dict):
            n = item.get("original_item_number") or item.get("item_number")
            try:
                n = int(n)
            except Exception:
                n = idx
            if n > 0 and n not in from_items:
                from_items.append(n)
    if from_items:
        return from_items
    required = int(ro.get("required_items") or 1)
    return list(range(1, required + 1))


def _full_source_items_from_mission_or_state(mission: Dict[str, Any], coach_state: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    items = safe_get(mission, "stimulus.full_source_items", None) or safe_get(mission, "stimulus.items", []) or []
    if not items and isinstance(coach_state, dict):
        items = safe_get(coach_state, "active_variant_context.full_source_items", []) or []
    out = []
    for idx, item in enumerate(items, 1):
        if isinstance(item, dict):
            d = dict(item)
            d.setdefault("original_item_number", d.get("item_number", idx))
            out.append(d)
    return out


def _items_for_numbers(full_items: List[Dict[str, Any]], numbers: List[int]) -> List[Dict[str, Any]]:
    by_num = {}
    for idx, item in enumerate(full_items, 1):
        n = item.get("original_item_number") or item.get("item_number") or idx
        try:
            n = int(n)
        except Exception:
            n = idx
        d = dict(item)
        d["original_item_number"] = n
        by_num[n] = d
    result = []
    for n in numbers:
        if n in by_num:
            result.append(dict(by_num[n]))
        else:
            result.append({"original_item_number": n, "rough_input": f"item {n}", "expected_move": "write one clear sentence"})
    return result


def _variant_topic_items(topic_key: str) -> List[Dict[str, Any]]:
    """Small universal fallback variant bank for visible adaptation.

    This is not a production Move Bank replacement. It only prevents the standalone
    CLI from visibly repeating the exact same task after valid attempts.
    """
    banks = {
        "technology_work": [
            {"original_item_number": 1, "topic_tags": ["technology", "work"], "rough_input": "automation / replace routine jobs / workers retrain", "expected_move": "write one clear sentence"},
            {"original_item_number": 2, "topic_tags": ["technology", "economy"], "rough_input": "online platforms / create flexible jobs / income opportunities", "expected_move": "write one clear sentence"},
            {"original_item_number": 3, "topic_tags": ["technology", "education"], "rough_input": "students / use digital tools / learn independently", "expected_move": "write one clear sentence"},
            {"original_item_number": 4, "topic_tags": ["technology", "society"], "rough_input": "too much screen time / reduce face-to-face communication", "expected_move": "write one clear sentence"},
            {"original_item_number": 5, "topic_tags": ["technology", "government"], "rough_input": "governments / regulate data use / protect privacy", "expected_move": "write one clear sentence"},
        ],
        "urban_life": [
            {"original_item_number": 1, "topic_tags": ["cities", "housing"], "rough_input": "cities grow / housing demand rises / rents increase", "expected_move": "write one clear sentence"},
            {"original_item_number": 2, "topic_tags": ["cities", "transport"], "rough_input": "more commuters / roads become crowded / travel takes longer", "expected_move": "write one clear sentence"},
            {"original_item_number": 3, "topic_tags": ["cities", "jobs"], "rough_input": "large cities / offer more jobs / attract young people", "expected_move": "write one clear sentence"},
            {"original_item_number": 4, "topic_tags": ["cities", "environment"], "rough_input": "urban growth / reduce green spaces / affect public health", "expected_move": "write one clear sentence"},
            {"original_item_number": 5, "topic_tags": ["cities", "services"], "rough_input": "local councils / improve public transport / reduce congestion", "expected_move": "write one clear sentence"},
        ],
        "education_choices": [
            {"original_item_number": 1, "topic_tags": ["education", "skills"], "rough_input": "students / learn practical skills / prepare for work", "expected_move": "write one clear sentence"},
            {"original_item_number": 2, "topic_tags": ["education", "pressure"], "rough_input": "too many exams / increase stress / reduce motivation", "expected_move": "write one clear sentence"},
            {"original_item_number": 3, "topic_tags": ["education", "technology"], "rough_input": "online courses / make learning flexible / support adults", "expected_move": "write one clear sentence"},
            {"original_item_number": 4, "topic_tags": ["education", "teamwork"], "rough_input": "group projects / teach cooperation / improve communication", "expected_move": "write one clear sentence"},
            {"original_item_number": 5, "topic_tags": ["education", "equity"], "rough_input": "schools / provide equal support / reduce achievement gaps", "expected_move": "write one clear sentence"},
        ],
    }
    return [dict(x) for x in banks.get(topic_key, banks["technology_work"])]


def _choose_next_variant_key(history: List[Dict[str, Any]], last_result: Optional[Dict[str, Any]]) -> str:
    seed = len(history) + (1 if last_result else 0)
    keys = ["technology_work", "urban_life", "education_choices"]
    return keys[seed % len(keys)]


def parse_response_items(text: str, required_items: int = 1, stimulus_items: Optional[List[Dict[str, Any]]] = None,
                         expected_item_numbers: Optional[List[int]] = None) -> Dict[str, Any]:
    """Parse numbered/unnumbered responses with original item-number awareness.

    V1.2.10 supports focused retry missions where expected item numbers may be
    [1,2,4,5] rather than [1,2,3,4]. Explicit numbering is preserved.
    """
    expected_numbers = []
    if expected_item_numbers:
        for n in expected_item_numbers:
            try:
                i = int(n)
                if i > 0 and i not in expected_numbers:
                    expected_numbers.append(i)
            except Exception:
                pass
    if not expected_numbers:
        required_items = max(1, int(required_items or 1))
        expected_numbers = list(range(1, required_items + 1))
    required_items = len(expected_numbers)
    expected_set = set(expected_numbers)

    control_tokens = {"end", "done", "submit", "stop", "finish", "enter"}
    raw_lines = []
    for ln in str(text or "").splitlines():
        raw = ln.strip()
        if not raw:
            continue
        if raw.lower() in control_tokens:
            if raw.lower() in {"end", "done", "submit", "stop", "finish"}:
                break
            continue
        raw_lines.append(raw)

    filtered_text = "\n".join(raw_lines)
    if len(raw_lines) <= 1 and re.search(r"\b\d+[.)]\s+", filtered_text):
        parts = re.split(r"(?=\b\d+[.)]\s+)", filtered_text)
        raw_lines = []
        for p in parts:
            p = p.strip()
            if not p:
                continue
            first = p.splitlines()[0].strip().lower()
            if first in control_tokens:
                break
            raw_lines.append(p)

    numbered: Dict[int, Dict[str, Any]] = {}
    overflow: List[Dict[str, Any]] = []
    unnumbered: List[str] = []
    warnings: List[str] = []

    for raw in raw_lines:
        m = re.match(r"^\s*(\d+)[.)]\s*(.+?)\s*$", raw)
        if m:
            num = int(m.group(1))
            sent = clean_text(m.group(2))
            if not sent:
                continue
            entry = {"item_number": num, "text": sent, "raw_line": raw, "explicit_number": True, "assignment_reason": "explicit_number"}
            if num not in expected_set:
                overflow.append(entry)
                warnings.append(f"item_number_not_expected:{num}")
            elif num in numbered:
                overflow.append(entry)
                warnings.append(f"duplicate_item_number:{num}")
            else:
                numbered[num] = entry
        else:
            sent = re.sub(r"^\s*(?:[-*•])\s*", "", raw).strip()
            if sent:
                unnumbered.append(clean_text(sent))

    stimulus_items = stimulus_items or []
    by_original = {}
    for idx, item in enumerate(stimulus_items, 1):
        if isinstance(item, dict):
            try:
                num = int(item.get("original_item_number") or item.get("item_number") or idx)
            except Exception:
                num = idx
            by_original[num] = item
    missing_slots = [i for i in expected_numbers if i not in numbered]
    for sent in unnumbered:
        if not missing_slots:
            overflow.append({"item_number": None, "text": sent, "raw_line": sent, "explicit_number": False})
            warnings.append("extra_unnumbered_response_beyond_required_items")
            continue
        best_num = None
        best_score = 0.0
        for num in missing_slots:
            item = by_original.get(num) or {}
            rough = str(item.get("rough_input") or "") if isinstance(item, dict) else ""
            score = _semantic_match_score(sent, rough)
            if score > best_score:
                best_score = score
                best_num = num
        if best_num is not None and best_score >= 2.0:
            num = best_num
            warnings.append("unnumbered_response_semantically_matched")
            reason = f"semantic_match_score:{best_score:.2f}"
        else:
            num = missing_slots[0]
            warnings.append("unnumbered_response_assigned_sequentially")
            reason = "sequential_expected_slot"
        numbered[num] = {"item_number": num, "text": sent, "raw_line": sent, "explicit_number": False, "assignment_reason": reason}
        missing_slots = [i for i in expected_numbers if i not in numbered]

    submitted_nums = [n for n in expected_numbers if n in numbered]
    missing_nums = [n for n in expected_numbers if n not in numbered]
    return {
        "required_items": required_items,
        "expected_item_numbers": expected_numbers,
        "submitted_count": len(submitted_nums),
        "submitted_item_numbers": submitted_nums,
        "missing_item_numbers": missing_nums,
        "submitted_by_number": {n: numbered[n] for n in submitted_nums},
        "submitted_items": [numbered[n] for n in submitted_nums],
        "overflow_items": overflow,
        "numbering_warnings": warnings,
        "numbering_feedback": _numbering_feedback_from_parse(expected_numbers, submitted_nums, missing_nums, warnings),
    }


def _numbering_feedback_from_parse(expected: List[int], submitted: List[int], missing: List[int], warnings: List[str]) -> str:
    if missing:
        return f"You submitted item number(s) {submitted}. Missing required number(s): {missing}. Keep the original item numbers so the Coach can match each sentence correctly."
    if warnings:
        return "Numbering was accepted, but check: " + ", ".join(warnings)
    return "Numbering is complete and correctly matched."


def _terminal_mission_text_v126(mission_payload: Dict[str, Any]) -> str:
    mission = mission_payload.get("today_mission") or mission_payload.get("mission") or mission_payload
    title = mission.get("title", "Writing Coach Mission")
    timebox = mission.get("timebox_minutes", 10)
    goal = mission.get("student_goal") or mission.get("student_rationale") or "Complete the writing move."
    lines = [f"\n=== {title} ===", f"Time: {timebox} minutes", "", f"Goal: {goal}"]
    variant = mission.get("adaptive_variant") or {}
    if variant:
        lines += ["", f"Adaptive focus: {variant.get('student_message') or variant.get('adaptation_type')}"]
    prompt = mission.get("source_prompt")
    if prompt:
        lines += ["", f"Topic / prompt context: {prompt}"]
    stim = mission.get("stimulus") or {}
    items = stim.get("items") or []
    if items:
        lines += ["", "Write one response for each item:"]
        for idx, item in enumerate(items, 1):
            rough = item.get("rough_input") if isinstance(item, dict) else str(item)
            num = idx
            if isinstance(item, dict):
                try:
                    num = int(item.get("original_item_number") or item.get("item_number") or idx)
                except Exception:
                    num = idx
            lines.append(f"{num}. {rough}")
    else:
        instr = mission.get("student_instruction") or "Write your response."
        lines += ["", instr]
    required = mission.get("required_output") or {}
    if required:
        lines += ["", "Required output:"]
        if required.get("required_items") is not None:
            lines.append(f"- {required.get('required_items')} item(s)")
        if required.get("expected_item_numbers"):
            lines.append(f"- Use these item numbers: {required.get('expected_item_numbers')}")
        if required.get("line_rule"):
            lines.append(f"- {required.get('line_rule')}")
        if required.get("length_guidance"):
            lines.append(f"- {required.get('length_guidance')}")
    checklist = mission.get("success_checklist") or []
    if checklist:
        lines += ["", "Success checklist:"]
        for c in checklist:
            lines.append(f"- {c}")
    return "\n".join(lines).strip() + "\n"

terminal_mission_text = _terminal_mission_text_v126


def _mission_item_feedback_v126(self, mission: Dict[str, Any], parsed_response: Dict[str, Any], required_items: int) -> List[Dict[str, Any]]:
    items = safe_get(mission, "stimulus.items", []) or []
    by_num: Dict[int, Dict[str, Any]] = {}
    for idx, item in enumerate(items, 1):
        if isinstance(item, dict):
            try:
                num = int(item.get("original_item_number") or item.get("item_number") or idx)
            except Exception:
                num = idx
            by_num[num] = item
    expected_nums = parsed_response.get("expected_item_numbers") or list(range(1, required_items + 1))
    feedback: List[Dict[str, Any]] = []
    submitted_by_number = parsed_response.get("submitted_by_number", {}) or {}
    for item_number in expected_nums:
        item = by_num.get(item_number, {})
        rough_input = item.get("rough_input") if isinstance(item, dict) else None
        entry = submitted_by_number.get(item_number)
        if entry:
            sent = entry.get("text", "")
            strengths, issues = self._sentence_strengths_issues(sent, rough_input)
            quality_level = self._sentence_quality_level(sent, issues)
            feedback.append({
                "item_number": item_number,
                "rough_input": rough_input,
                "student_sentence": sent,
                "status": "submitted",
                "explicit_number_used": bool(entry.get("explicit_number")),
                "assignment_reason": entry.get("assignment_reason"),
                "strengths": strengths,
                "issues": issues,
                "sentence_quality_level": quality_level,
                "is_acceptable_for_target_move": quality_level in {"strong_for_current_move", "basic_functional"},
                "needs_higher_band_upgrade": quality_level == "functional_but_needs_upgrade",
                "suggested_revision": self._suggest_revision(sent, rough_input),
                "explanation": self._line_explanation(issues),
                "how_to_improve": self._how_to_improve(issues, sent, rough_input),
            })
        else:
            feedback.append({
                "item_number": item_number,
                "rough_input": rough_input,
                "student_sentence": None,
                "status": "missing",
                "strengths": [],
                "issues": ["missing_required_item"],
                "is_acceptable_for_target_move": False,
                "suggested_revision": self._model_from_rough_input(rough_input),
                "explanation": "This item was not submitted, so the Coach cannot evaluate the target skill for this part of the mission.",
                "how_to_improve": "Write one complete sentence for this rough idea.",
            })
    for entry in parsed_response.get("overflow_items", []) or []:
        sent = entry.get("text", "")
        strengths, issues = self._sentence_strengths_issues(sent, None)
        issues = ["numbering_or_extra_item_issue"] + issues
        feedback.append({
            "item_number": entry.get("item_number"),
            "rough_input": None,
            "student_sentence": sent,
            "status": "extra_or_out_of_range",
            "explicit_number_used": bool(entry.get("explicit_number")),
            "strengths": strengths,
            "issues": issues,
            "is_acceptable_for_target_move": False,
            "suggested_revision": self._suggest_revision(sent, None),
            "explanation": "This response has a duplicate, missing, or unexpected number, so it cannot be matched cleanly to a required item.",
            "how_to_improve": f"Use the exact required numbers: {expected_nums}.",
        })
    return feedback

MissionEvaluator._item_feedback = _mission_item_feedback_v126


def _evaluate_v126(self, mission_payload: Dict[str, Any], response_text: str) -> Dict[str, Any]:
    mission = mission_payload.get("today_mission") or mission_payload.get("today_micromission") or mission_payload.get("mission") or {}
    if not mission:
        raise ValueError("Mission payload does not contain today_mission/today_micromission/mission.")
    expected_numbers = _expected_item_numbers_from_mission(mission)
    required_items = len(expected_numbers)
    parsed_response = parse_response_items(response_text, required_items, safe_get(mission, "stimulus.items", []) or [], expected_numbers)
    lines = [x.get("text", "") for x in parsed_response.get("submitted_items", [])]
    observable_units = mission.get("observable_units") or []
    submitted_items = int(parsed_response.get("submitted_count", len(lines)))
    completion_ratio = min(1.0, submitted_items / max(1, required_items))
    empty_response = not clean_text(response_text)
    incomplete_output = (not empty_response) and submitted_items < required_items
    completion_gate_passed = (not empty_response) and submitted_items >= required_items

    unit_scores: List[Dict[str, Any]] = []
    critical_failure = False
    for u in observable_units:
        uid = u.get("unit_id") if isinstance(u, dict) else str(u)
        weight = float(u.get("weight", 1.0)) if isinstance(u, dict) else 1.0
        critical = bool(u.get("critical", False)) if isinstance(u, dict) else False
        score, note = self._score_unit(uid, lines, response_text, required_items, mission)
        if uid in {"minimum_output_met", "required_items_met"}:
            note = f"Detected {submitted_items} expected item(s); required {required_items}. Expected numbers: {expected_numbers}. This is a hard completion gate in V1.2.10."
        if critical and score < 0.5:
            critical_failure = True
        unit_scores.append({"unit_id": uid, "score": round(score, 3), "weight": weight, "critical": critical, "note": note})

    total_weight = sum(max(0, u["weight"]) for u in unit_scores) or 1.0
    raw_observable_score = sum(u["score"] * max(0, u["weight"]) for u in unit_scores) / total_weight
    pass_t = float(safe_get(mission, "scoring.pass_threshold", 0.8) or 0.8)
    part_t = float(safe_get(mission, "scoring.partial_threshold", 0.6) or 0.6)
    if empty_response:
        outcome = "invalid_empty_response"; mission_score = 0.0; mastery_update_allowed = False; attempt_status = "invalid_empty_response"
    elif incomplete_output:
        outcome = "invalid_incomplete_output"; mission_score = 0.0; mastery_update_allowed = False; attempt_status = "invalid_incomplete_output"
    elif critical_failure and raw_observable_score < 0.78:
        outcome = "fail"; mission_score = raw_observable_score; mastery_update_allowed = True; attempt_status = "complete_evaluated"
    elif raw_observable_score >= pass_t:
        outcome = "pass"; mission_score = raw_observable_score; mastery_update_allowed = True; attempt_status = "complete_evaluated"
    elif raw_observable_score >= part_t:
        outcome = "partial_pass"; mission_score = raw_observable_score; mastery_update_allowed = True; attempt_status = "complete_evaluated"
    else:
        outcome = "fail"; mission_score = raw_observable_score; mastery_update_allowed = True; attempt_status = "complete_evaluated"

    confidence = self._confidence(lines, observable_units, required_items, completion_gate_passed)
    result_id = stable_id("wc_result", mission.get("mission_id"), response_text, outcome, n=12)
    created = now_iso()
    completion_gate = {
        "status": "passed" if completion_gate_passed else attempt_status,
        "required_items": required_items,
        "expected_item_numbers": expected_numbers,
        "submitted_items": submitted_items,
        "submitted_item_numbers": parsed_response.get("submitted_item_numbers", []),
        "missing_item_numbers": parsed_response.get("missing_item_numbers", []),
        "completion_ratio": round(completion_ratio, 3),
        "hard_gate": True,
        "mastery_update_allowed": mastery_update_allowed,
        "numbering_warnings": parsed_response.get("numbering_warnings", []),
        "numbering_feedback": parsed_response.get("numbering_feedback"),
        "message": self._completion_gate_message(submitted_items, required_items, empty_response),
    }
    item_feedback = self._item_feedback(mission, parsed_response, required_items)
    feedback_bundle = self._feedback(outcome, unit_scores, mission, lines, required_items, completion_gate, item_feedback, mastery_update_allowed)
    lie_payload = self._lie_result_payload(mission, result_id, mission_score, raw_observable_score, outcome, confidence, unit_scores, created, mastery_update_allowed, completion_gate)
    return {
        "schema_version": MISSION_RESULT_SCHEMA,
        "result_id": result_id,
        "created_at": created,
        "mission_id": mission.get("mission_id"),
        "target_skill_id": mission.get("target_skill_id"),
        "target_skill_name": mission.get("target_skill_name"),
        "selected_move": mission.get("selected_move"),
        "adaptive_variant": mission.get("adaptive_variant"),
        "response_summary": {
            "line_count": submitted_items,
            "word_count": len(words(response_text)),
            "required_items": required_items,
            "expected_item_numbers": expected_numbers,
            "completion_ratio": round(completion_ratio, 3),
            "submitted_item_numbers": parsed_response.get("submitted_item_numbers", []),
            "missing_item_numbers": parsed_response.get("missing_item_numbers", []),
            "numbering_warnings": parsed_response.get("numbering_warnings", []),
        },
        "completion_gate": completion_gate,
        "observable_unit_scores": unit_scores,
        "raw_observable_score_before_gate": round(raw_observable_score, 3),
        "mission_score": round(mission_score, 3),
        "outcome": outcome,
        "attempt_status": attempt_status,
        "confidence": round(confidence, 3),
        "critical_failure": critical_failure,
        "mastery_update_allowed": mastery_update_allowed,
        "feedback": feedback_bundle["legacy_feedback"],
        "student_feedback": feedback_bundle["student_feedback"],
        "teacher_feedback": feedback_bundle["teacher_feedback"],
        "debug_evaluation": feedback_bundle["debug_evaluation"],
        "lie_update_decision": feedback_bundle["lie_update_decision"],
        "learning_intelligence_payload": lie_payload,
    }

MissionEvaluator.evaluate = _evaluate_v126


def _apply_adaptive_variant_to_mission(mission: Dict[str, Any], last_result: Optional[Dict[str, Any]], history: List[Dict[str, Any]], coach_state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Make adaptation visible in the actual mission content."""
    mission = json.loads(json.dumps(mission))
    full_items = _full_source_items_from_mission_or_state(mission, coach_state)
    if not full_items:
        full_items = _items_for_numbers(safe_get(mission, "stimulus.items", []) or [], list(range(1, int(safe_get(mission, "required_output.required_items", 5) or 5) + 1)))
    for idx, item in enumerate(full_items, 1):
        item.setdefault("original_item_number", idx)
    outcome = (last_result or {}).get("outcome")
    mastery_valid = is_mastery_valid_result(last_result)

    if is_valid_mission_result(last_result) and not mastery_valid:
        missing = safe_get(last_result, "student_feedback.missing_items", None) or safe_get(last_result, "completion_gate.missing_item_numbers", None) or []
        try:
            missing = [int(x) for x in missing]
        except Exception:
            missing = []
        if missing:
            focus_items = _items_for_numbers(full_items, missing)
            mission["mission_id"] = stable_id("wc_mission", mission.get("mission_id"), "missing_retry", missing, n=10)
            mission["title"] = "Complete Missing Sentences"
            mission["student_goal"] = "Complete only the missing sentence(s) from the previous attempt, using the original item numbers."
            mission.setdefault("stimulus", {})["items"] = focus_items
            mission["stimulus"]["full_source_items"] = full_items
            mission["stimulus"]["focus_item_numbers"] = missing
            mission["required_output"]["required_items"] = len(missing)
            mission["required_output"]["expected_item_numbers"] = missing
            mission["required_output"]["line_rule"] = f"Write only the missing numbered line(s): {missing}. Keep the original numbers."
            mission["adaptive_variant"] = {
                "schema_version": ADAPTIVE_VARIANT_SCHEMA,
                "adaptation_type": "focused_missing_items_retry",
                "previous_outcome": outcome,
                "parent_mission_id": (last_result or {}).get("mission_id"),
                "focus_item_numbers": missing,
                "full_mission_required_items": len(full_items),
                "visible_change": "shows_only_missing_items",
                "mastery_policy": "still_not_mastery_evidence_until_focused_retry_is_complete",
                "student_message": "Your previous attempt was incomplete. Today you only need to complete the missing numbered sentence(s).",
            }
            mission["reassignment_policy"] = {
                "reassigned_after_invalid_or_incomplete_attempt": True,
                "previous_outcome": outcome,
                "reason": "previous_attempt_not_mastery_evidence_but_retry_is_focused_on_missing_items",
                "student_message": "Complete the missing item(s); do not rewrite the sentences you already submitted unless you want to improve them.",
            }
            return mission
        # no missing data: keep same content but label as retry
        mission.setdefault("adaptive_variant", {"schema_version": ADAPTIVE_VARIANT_SCHEMA})
        mission["adaptive_variant"].update({
            "adaptation_type": "full_retry_missing_data_unavailable",
            "previous_outcome": outcome,
            "student_message": "The previous attempt was not valid for mastery. Please complete the full mission again.",
        })
        return mission

    if mastery_valid and outcome in {"pass", "partial_pass"}:
        key = _choose_next_variant_key(history, last_result)
        items = _variant_topic_items(key)
        mission["mission_id"] = stable_id("wc_mission", mission.get("target_skill_id"), safe_get(mission, "selected_move.move_id"), key, len(history), n=10)
        mission["title"] = "Clear Sentence Builder - New Topic Variant"
        mission["student_goal"] = "Apply the same sentence-control skill to a new topic with slightly less support."
        mission["source_prompt"] = {
            "technology_work": "New technologies are changing the way people work and study. Do the advantages outweigh the disadvantages?",
            "urban_life": "Many people are moving to large cities. What problems can this cause, and how can they be solved?",
            "education_choices": "Some people think schools should teach more practical skills. To what extent do you agree?",
        }.get(key, mission.get("source_prompt"))
        mission.setdefault("stimulus", {})["items"] = items
        mission["stimulus"]["full_source_items"] = items
        mission["stimulus"]["topic_context"] = key.replace("_", " ")
        mission["required_output"]["required_items"] = len(items)
        mission["required_output"]["expected_item_numbers"] = [1, 2, 3, 4, 5]
        mission["required_output"]["line_rule"] = "Write one sentence for each numbered item. Use numbers 1-5."
        mission["adaptive_variant"] = {
            "schema_version": ADAPTIVE_VARIANT_SCHEMA,
            "adaptation_type": "near_transfer_new_topic" if outcome == "pass" else "same_skill_more_scaffolded_transfer",
            "previous_outcome": outcome,
            "previous_score": (last_result or {}).get("mission_score"),
            "variant_topic_key": key,
            "visible_change": "new_topic_and_new_rough_inputs",
            "student_message": "You completed the previous attempt. Now apply the same skill to a new topic.",
        }
        return mission

    if mastery_valid and outcome == "fail":
        # Easier focused remediation: fewer items, more scaffold.
        focus = [1, 2, 3]
        focus_items = _items_for_numbers(full_items, focus)
        mission["mission_id"] = stable_id("wc_mission", mission.get("mission_id"), "easier_retry", len(history), n=10)
        mission["title"] = "Clear Sentence Builder - Easier Retry"
        mission["student_goal"] = "Repeat the same skill with fewer items and stronger structure."
        mission.setdefault("stimulus", {})["items"] = focus_items
        mission["stimulus"]["full_source_items"] = full_items
        mission["required_output"]["required_items"] = len(focus)
        mission["required_output"]["expected_item_numbers"] = focus
        mission["required_output"]["line_rule"] = f"Write only these numbered sentence(s): {focus}."
        mission["adaptive_variant"] = {
            "schema_version": ADAPTIVE_VARIANT_SCHEMA,
            "adaptation_type": "easier_retry_after_fail",
            "previous_outcome": outcome,
            "focus_item_numbers": focus,
            "visible_change": "fewer_items_more_scaffold",
            "student_message": "The previous complete attempt was weak, so this retry uses fewer items.",
        }
        return mission

    mission.setdefault("required_output", {})["expected_item_numbers"] = _expected_item_numbers_from_mission(mission)
    mission.setdefault("adaptive_variant", {"schema_version": ADAPTIVE_VARIANT_SCHEMA, "adaptation_type": "baseline_first_assignment", "visible_change": "none_yet"})
    return mission

# Preserve original method for after-essay generation but add expected numbers to the mission.
_ORIG_MISSION_BUILDER_BUILD = MissionBuilder.build

def _mission_builder_build_v126(self, skill, move, move_candidates, context):
    mission = _ORIG_MISSION_BUILDER_BUILD(self, skill, move, move_candidates, context)
    mission["mission_version"] = "writing_coach_v1_2_10_move_mission"
    mission.setdefault("required_output", {})["expected_item_numbers"] = _expected_item_numbers_from_mission(mission)
    # Ensure full source items store original numbers.
    items = safe_get(mission, "stimulus.items", []) or []
    full = []
    for idx, item in enumerate(items, 1):
        if isinstance(item, dict):
            d = dict(item); d.setdefault("original_item_number", idx); full.append(d)
    if full:
        mission.setdefault("stimulus", {})["items"] = full
        mission["stimulus"]["full_source_items"] = full
    mission.setdefault("adaptive_variant", {"schema_version": ADAPTIVE_VARIANT_SCHEMA, "adaptation_type": "baseline_first_assignment", "visible_change": "none_yet"})
    return mission

MissionBuilder.build = _mission_builder_build_v126

_ORIG_GENERATE_DAILY = ProactiveAdaptiveWritingCoach.generate_daily

def _generate_daily_v126(self, coach_state: Dict[str, Any], last_result: Optional[Dict[str, Any]] = None,
                         inputs: Optional[Dict[str, Any]] = None, plan_horizon_days: int = 7) -> Dict[str, Any]:
    out = _ORIG_GENERATE_DAILY(self, coach_state, last_result, inputs, plan_horizon_days)
    history = self._history_with_last(coach_state, last_result)
    mission = out.get("today_mission") or {}
    adapted = _apply_adaptive_variant_to_mission(mission, last_result, history, coach_state)
    out["today_mission"] = adapted
    variant = adapted.get("adaptive_variant") or {}
    out.setdefault("coach_decision", {})["adaptive_variant_decision"] = variant
    out.setdefault("qa", {}).setdefault("planner_audit", {})["adaptive_variant_generated"] = bool(variant)
    out.setdefault("qa", {}).setdefault("semantic_consistency_audit", {})["visible_mission_adaptation_supported"] = True
    if variant.get("adaptation_type") == "focused_missing_items_retry":
        # Replace policy wording from old exact reassign to focused retry.
        warnings = out.setdefault("qa", {}).setdefault("warnings", [])
        if "previous_attempt_not_mastery_evidence_reassigning_same_mission" in warnings:
            warnings.remove("previous_attempt_not_mastery_evidence_reassigning_same_mission")
        warnings.append("previous_attempt_not_mastery_evidence_generating_focused_retry")
    # Rebuild dependent visible cards/state with adapted mission id/content.
    if out.get("active_coaching_cycle"):
        out["active_coaching_cycle"].setdefault("active_move", {})["move_id"] = safe_get(adapted, "selected_move.move_id")
    if out.get("student_home_card"):
        out["student_home_card"]["mission_title"] = adapted.get("title")
        out["student_home_card"]["message"] = (variant.get("student_message") or out["student_home_card"].get("message"))
    if out.get("coaching_plan", {}).get("daily_slots"):
        out["coaching_plan"]["daily_slots"][0]["mission_id"] = adapted.get("mission_id")
        out["coaching_plan"]["daily_slots"][0]["student_task"] = adapted.get("title")
        out["coaching_plan"]["daily_slots"][0]["adaptive_variant"] = variant.get("adaptation_type")
    out["coach_state_export"] = self._coach_state_export(out, out.get("active_coaching_cycle", {}), out.get("coaching_plan", {}), history, out.get("coach_decision", {}).get("candidate_rankings", []), out.get("coach_decision", {}).get("move_candidate_rankings", []))
    out["coach_state_export"]["active_variant_context"] = {
        "schema_version": ADAPTIVE_VARIANT_SCHEMA,
        "last_variant": variant,
        "full_source_items": safe_get(adapted, "stimulus.full_source_items", []) or safe_get(adapted, "stimulus.items", []),
    }
    out["coach_state_export"] = normalize_coach_state_contract(out["coach_state_export"])
    return out

ProactiveAdaptiveWritingCoach.generate_daily = _generate_daily_v126


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# V1.2.10 Local Error + Upgrade Feedback Split Overrides
# ---------------------------------------------------------------------------

LOCAL_FEEDBACK_SCHEMA = "WRITING_COACH_LOCAL_FEEDBACK_SPLIT_V1_2_10"


def _issue_key(issue: Any) -> str:
    return str(issue or "").split(":", 1)[0].strip()


def _has_issue(issues: List[Any], *prefixes: str) -> bool:
    return any(_issue_key(i).startswith(prefixes) for i in (issues or []))


def _format_issue_label(issue: Any) -> str:
    s = str(issue or "").strip()
    if ":" in s:
        key, rest = s.split(":", 1)
        return f"{key.strip()}: {rest.strip()}"
    return s


def _wc128_sentence_strengths_issues(self, sentence: str, rough_input: Optional[str] = None) -> Tuple[List[str], List[str]]:
    """V1.2.10: detect local correctness errors separately from upgrade needs."""
    ws = words(sentence)
    lower = sentence.lower()
    rough = (rough_input or "").lower()
    strengths: List[str] = []
    issues: List[str] = []

    if len(ws) >= 8:
        strengths.append("meaning_is_recoverable")
    elif len(ws) >= 5:
        strengths.append("meaning_is_partly_recoverable")
        issues.append("sentence_underdeveloped: add a clearer result, consequence, or academic noun phrase")
    else:
        issues.append("sentence_too_short_or_underdeveloped: write one complete sentence of about 10-18 words")

    if self._has_plausible_finite_verb(sentence):
        strengths.append("finite_verb_present")
    else:
        issues.append("finite_verb_missing_or_unclear: add one clear main verb")

    if any(w in lower.split() for w in self.SUBJECT_HINTS) or re.match(r"^[A-Z]?[a-z]+\s+", sentence):
        strengths.append("subject_present")
    else:
        issues.append("subject_missing_or_unclear: begin with a clear subject")

    # Prompt-item semantic alignment checks.
    if ("retire" in rough or "economy" in rough) and ("retire" in lower or "work" in lower or "econom" in lower or "worker" in lower or "workforce" in lower):
        strengths.append("idea_matches_prompt_item")
    if "grandparents" in rough and all(k in lower for k in ["grandparents", "parents", "children"]):
        strengths.append("idea_matches_prompt_item")
    if ("healthcare" in rough or "government" in rough or "government pays" in rough) and ("health" in lower or "government" in lower or "spend" in lower or "pay" in lower or "fund" in lower or "public" in lower):
        strengths.append("idea_matches_prompt_item")
    if ("traditions" in rough or "generation" in rough) and ("tradition" in lower or "generation" in lower or "teach" in lower):
        strengths.append("idea_matches_prompt_item")
    if ("experience" in rough or "advice" in rough) and ("experience" in lower or "advice" in lower or "society" in lower):
        strengths.append("idea_matches_prompt_item")

    if re.search(r"\b(might|may|can|could|should)\s+help\b", lower):
        strengths.append("modal_verb_pattern_ok")

    # Blocked grammar/word-form patterns.
    bad_patterns = ["has to spent", "have to spent", "can gives", "this make", "they helps", "more stronger", "so older"]
    found = [p for p in bad_patterns if p in lower]
    if found:
        issues.append("verb_or_comparison_pattern_error: " + ", ".join(found))
    else:
        strengths.append("no_blocked_verb_pattern_detected")

    # V1.2.10 high-value local error detection.
    if re.search(r"\bless\s+(people|workers|children|citizens|students)\b", lower):
        issues.append("comparative_quantifier_error: use 'fewer' with countable plural nouns such as people or workers")
    if re.search(r"\bthere\s+(are|is)\s+(fewer|less|many|more)?\s*people\s+working\b", lower):
        issues.append("weak_academic_structure: avoid 'there are people working'; use 'the workforce shrinks' or 'the number of workers declines'")
    if re.search(r"\bmany people retire\s+and\b", lower):
        issues.append("weak_sentence_connection: replace a simple 'and' chain with a clearer cause-result structure")

    # Healthcare/government local correctness patterns.
    if re.search(r"\bcovered\s+with\s+(the\s+)?government\b", lower):
        issues.append("passive_preposition_error: use 'covered by the government' or 'funded by the government', not 'covered with government'")
    if re.search(r"\b(covered|funded|paid|provided)\s+(by|with)?\s*government\b", lower) and "the government" not in lower and "governments" not in lower:
        issues.append("article_determiner_error: use 'the government' when referring to a public authority in this sentence")
    if re.search(r"\bcovered\s+with\b", lower):
        issues.append("collocation_error: healthcare is usually 'covered by', 'funded by', 'paid for by', or 'provided by' the government")
    if "extensive healthcare" in lower and ("government" in lower or "public" in lower):
        issues.append("lexical_precision_upgrade: 'healthcare costs', 'public healthcare services', or 'government-funded healthcare' is more precise")

    # Grandparents/care local patterns.
    if re.search(r"\b(taking care with|take care with|care with)\b", lower):
        issues.append("preposition_collocation_error: use 'care for children', 'take care of children', or 'by caring for children'")
    if re.search(r"\bhelp\s+\w+\s+with\s+taking\s+care\b", lower):
        issues.append("unnatural_gerund_phrase: use 'help parents by caring for children'")
    if re.search(r"\bwith\s+(take|taking)\s+care\s+children\b", lower):
        issues.append("missing_preposition_error: use 'take care of children' or 'care for children'")
    if re.search(r"\bwith\s+taking\s+care\s+of\b", lower):
        issues.append("unnatural_phrase_structure: use 'by taking care of' or 'by caring for'")

    vague = [v for v in self.VAGUE_WORDS if v in lower]
    if vague:
        issues.append("vague_wording: " + ", ".join(vague[:3]))

    # Higher-band pressure: add upgrade issue even if local correctness exists,
    # but keep it separate so the terminal can show minimal vs upgraded versions.
    if rough:
        if len(ws) < 12 or "there are" in lower or " and " in lower or _has_issue(issues, "lexical_precision_upgrade"):
            issues.append("higher_band_upgrade: make the sentence more precise, natural, and academically connected")

    return strengths, issues


def _wc128_sentence_quality_level(self, sentence: str, issues: List[str]) -> str:
    if _has_issue(
        issues,
        "verb_or_comparison_pattern_error",
        "finite_verb",
        "subject_missing",
        "passive_preposition_error",
        "article_determiner_error",
        "collocation_error",
        "preposition_collocation_error",
        "missing_preposition_error",
        "unnatural_gerund_phrase",
        "unnatural_phrase_structure",
        "comparative_quantifier_error",
    ):
        return "needs_local_fix_then_upgrade"
    if _has_issue(issues, "weak_academic_structure", "weak_sentence_connection", "higher_band_upgrade", "vague_wording", "lexical_precision_upgrade", "sentence_underdeveloped"):
        return "functional_but_needs_upgrade"
    if len(words(sentence)) >= 10:
        return "strong_for_current_move"
    return "basic_functional"


def _wc128_minimal_correction(self, sentence: str, rough_input: Optional[str], issues: Optional[List[str]] = None) -> str:
    s = clean_text(sentence)
    issues = issues or []
    # Healthcare/government phrase repairs.
    s = re.sub(r"\bcovered\s+with\s+government\b", "funded by the government", s, flags=re.IGNORECASE)
    s = re.sub(r"\bcovered\s+with\s+the\s+government\b", "funded by the government", s, flags=re.IGNORECASE)
    s = re.sub(r"\bcovered\s+by\s+government\b", "covered by the government", s, flags=re.IGNORECASE)
    s = re.sub(r"\bfunded\s+by\s+government\b", "funded by the government", s, flags=re.IGNORECASE)
    s = re.sub(r"\bprovided\s+by\s+government\b", "provided by the government", s, flags=re.IGNORECASE)
    s = re.sub(r"\bpaid\s+by\s+government\b", "paid for by the government", s, flags=re.IGNORECASE)
    # Common mission repairs.
    replacements = {
        "has to spent": "has to spend",
        "have to spent": "have to spend",
        "this make": "this makes",
        "they helps": "they help",
        "more stronger": "stronger",
        "less people working": "fewer people working",
        "there are less people working": "there are fewer people working",
        "with taking care with children": "by caring for children",
        "taking care with children": "caring for children",
        "take care with children": "take care of children",
        "with take care children": "by caring for children",
        "with taking care children": "by caring for children",
    }
    for a, b in replacements.items():
        s = re.sub(re.escape(a), b, s, flags=re.IGNORECASE)
    # If the minimal correction still sounds elliptical for item 1, make a conservative grammatical version.
    r = (rough_input or "").lower()
    lower = s.lower()
    if ("healthcare" in r or "government pays" in r) and _has_issue(issues, "passive_preposition_error", "collocation_error", "article_determiner_error"):
        if "older people" in lower and "health" in lower:
            s = "Older people often need extensive healthcare funded by the government."
    if not s:
        s = "Write one complete sentence with a clear subject, verb, and natural word combination."
    if s[-1] not in ".!?":
        s += "."
    return s


def _wc128_upgraded_revision(self, sentence: str, rough_input: Optional[str], issues: Optional[List[str]] = None) -> str:
    r = (rough_input or "").lower()
    if "healthcare" in r or "government pays" in r:
        return "An ageing population increases demand for government-funded healthcare, which can put pressure on public spending."
    if "retire" in r or "economy slows" in r:
        return "When many people retire, the workforce shrinks, which can slow economic growth."
    if "grandparents" in r or "care for children" in r:
        return "Grandparents can support working parents by helping to care for children."
    if "traditions" in r or "younger generation" in r:
        return "Older people can preserve cultural traditions by passing them on to younger generations."
    if "experience" in r or "advice" in r:
        return "Older people’s experience can benefit society because they often offer practical advice."
    return self._minimal_correction(sentence, rough_input, issues)


def _wc128_feedback_layers(self, sentence: str, rough_input: Optional[str], strengths: List[str], issues: List[str]) -> Dict[str, Any]:
    correctness_issues = [i for i in issues if _has_issue([i],
        "verb_or_comparison_pattern_error", "finite_verb", "subject_missing", "passive_preposition_error",
        "article_determiner_error", "collocation_error", "preposition_collocation_error", "missing_preposition_error",
        "comparative_quantifier_error")]
    naturalness_issues = [i for i in issues if _has_issue([i],
        "unnatural_gerund_phrase", "unnatural_phrase_structure", "weak_academic_structure", "weak_sentence_connection", "lexical_precision_upgrade")]
    upgrade_issues = [i for i in issues if _has_issue([i], "higher_band_upgrade", "vague_wording", "sentence_underdeveloped")]
    minimal = self._minimal_correction(sentence, rough_input, issues)
    upgraded = self._upgraded_revision(sentence, rough_input, issues)
    return {
        "schema_version": LOCAL_FEEDBACK_SCHEMA,
        "correctness_fix": {
            "needed": bool(correctness_issues),
            "issues": correctness_issues,
            "minimal_corrected_version": minimal,
            "explanation": self._correctness_explanation(correctness_issues),
        },
        "naturalness_fix": {
            "needed": bool(naturalness_issues),
            "issues": naturalness_issues,
            "natural_version": minimal if naturalness_issues and not correctness_issues else upgraded,
            "explanation": self._naturalness_explanation(naturalness_issues),
        },
        "higher_band_upgrade": {
            "needed": bool(upgrade_issues) or minimal != upgraded,
            "issues": upgrade_issues,
            "upgraded_academic_version": upgraded,
            "explanation": "This version expresses the same idea with clearer academic vocabulary, stronger cause-effect logic, and more natural collocation.",
        },
        "recommended_learning_order": [x for x in [
            "fix_local_accuracy" if correctness_issues else None,
            "make_wording_natural" if naturalness_issues else None,
            "upgrade_academic_expression" if (upgrade_issues or minimal != upgraded) else None,
        ] if x],
    }


def _wc128_correctness_explanation(self, issues: List[str]) -> str:
    if not issues:
        return "No major local accuracy error was detected in the submitted sentence."
    if _has_issue(issues, "passive_preposition_error", "collocation_error"):
        return "The phrase with 'government' uses the wrong preposition/collocation. In English, services are usually funded by, covered by, paid for by, or provided by the government."
    if _has_issue(issues, "article_determiner_error"):
        return "Use 'the government' when you mean the public authority responsible for funding or services."
    if _has_issue(issues, "comparative_quantifier_error"):
        return "Use 'fewer' with countable plural nouns such as workers or people."
    if _has_issue(issues, "preposition_collocation_error", "missing_preposition_error"):
        return "The care phrase needs the correct preposition: care for children, take care of children, or help by caring for children."
    return "Fix the local grammar or word-combination problem before focusing on style."


def _wc128_naturalness_explanation(self, issues: List[str]) -> str:
    if not issues:
        return "The sentence is reasonably natural at the local level."
    if _has_issue(issues, "weak_academic_structure"):
        return "The wording is understandable but too spoken/basic; use a more precise academic structure."
    if _has_issue(issues, "weak_sentence_connection"):
        return "Use a clearer cause-result relationship instead of a simple 'and' chain."
    if _has_issue(issues, "lexical_precision_upgrade"):
        return "Choose a more precise noun phrase such as healthcare costs, public healthcare services, or government-funded healthcare."
    if _has_issue(issues, "unnatural"):
        return "The meaning is recoverable, but the phrase order is not natural."
    return "Make the sentence sound more natural and precise."


def _wc128_line_explanation(self, issues: List[str]) -> str:
    if not issues:
        return "The sentence is clear and accurate enough for the target move. The Coach can still offer a higher-band version."
    if "missing_required_item" in issues:
        return "The answer is incomplete because this required item is missing."
    if _has_issue(issues, "passive_preposition_error", "collocation_error", "article_determiner_error"):
        return "The meaning is understandable, but the local phrase around 'government' is not natural or accurate enough for higher-level writing."
    if _has_issue(issues, "comparative_quantifier_error"):
        return "The idea is understandable, but 'less people' is not accurate academic English. Use 'fewer people' or, better here, 'fewer workers'."
    if _has_issue(issues, "weak_academic_structure"):
        return "The sentence is understandable but too basic. Replace vague structures with a precise academic noun phrase."
    if _has_issue(issues, "weak_sentence_connection"):
        return "The sentence uses a simple 'and' chain. A stronger academic sentence shows the cause-result relationship more clearly."
    if _has_issue(issues, "preposition_collocation_error", "missing_preposition_error"):
        return "The idea is understandable, but the phrase after 'help parents' is unnatural. Use 'by caring for children' or 'take care of children'."
    if _has_issue(issues, "higher_band_upgrade"):
        return "This is recoverable, but the Coach should push it above basic B1 phrasing toward clearer academic expression."
    return "Focus on making the sentence complete, clear, natural, and precise before increasing difficulty."


def _wc128_how_to_improve(self, issues: List[str], sentence: str, rough_input: Optional[str]) -> str:
    if "missing_required_item" in issues:
        return "Write one complete sentence for this rough idea."
    actions = []
    if _has_issue(issues, "passive_preposition_error", "collocation_error"):
        actions.append("Replace 'covered with government' with 'funded by the government', 'covered by the government', or 'paid for by the government'.")
    if _has_issue(issues, "article_determiner_error"):
        actions.append("Use 'the government', not bare 'government', in this sentence.")
    if _has_issue(issues, "lexical_precision_upgrade"):
        actions.append("Use a more precise phrase such as 'healthcare costs', 'public healthcare services', or 'government-funded healthcare'.")
    if _has_issue(issues, "comparative_quantifier_error"):
        actions.append("Change 'less people' to 'fewer people' or, more naturally here, 'fewer workers'.")
    if _has_issue(issues, "weak_academic_structure"):
        actions.append("Replace 'there are fewer people working' with 'the workforce shrinks' or 'the number of workers declines'.")
    if _has_issue(issues, "weak_sentence_connection"):
        actions.append("Use a cause-result structure: 'When/As many people retire, ..., which can ...'.")
    if _has_issue(issues, "preposition_collocation_error", "unnatural_gerund_phrase", "missing_preposition_error"):
        actions.append("Use 'by caring for children', 'care for children', or 'take care of children'.")
    if _has_issue(issues, "higher_band_upgrade") and not any("academic" in a for a in actions):
        actions.append("Add a precise academic noun and a clear consequence, not only a simple statement.")
    if actions:
        return " ".join(actions)
    if not issues:
        return "The sentence is accurate. Try a stronger academic version to raise the level."
    return "Revise the sentence so it has one clear subject, one correct main verb, natural word combinations, and a clear result."


def _wc128_item_feedback(self, mission: Dict[str, Any], parsed_response: Dict[str, Any], required_items: int) -> List[Dict[str, Any]]:
    items = safe_get(mission, "stimulus.items", []) or []
    feedback: List[Dict[str, Any]] = []
    submitted_by_number = parsed_response.get("submitted_by_number", {}) or {}
    expected_numbers = _expected_item_numbers_from_mission(mission)
    if not expected_numbers:
        expected_numbers = list(range(1, required_items + 1))

    item_by_number: Dict[int, Dict[str, Any]] = {}
    for idx, item in enumerate(items, 1):
        if isinstance(item, dict):
            n = item.get("original_item_number") or item.get("item_number") or idx
            try:
                n = int(n)
            except Exception:
                n = idx
            item_by_number[n] = item

    for fallback_idx, item_number in enumerate(expected_numbers, 1):
        item = item_by_number.get(item_number) or (items[fallback_idx - 1] if fallback_idx - 1 < len(items) and isinstance(items[fallback_idx - 1], dict) else {})
        rough_input = item.get("rough_input")
        entry = submitted_by_number.get(item_number)
        if entry:
            sent = entry.get("text", "")
            strengths, issues = self._sentence_strengths_issues(sent, rough_input)
            quality_level = self._sentence_quality_level(sent, issues)
            layers = self._feedback_layers(sent, rough_input, strengths, issues)
            minimal = layers["correctness_fix"]["minimal_corrected_version"]
            upgraded = layers["higher_band_upgrade"]["upgraded_academic_version"]
            # v1.2.18: real user report -- a sentence with no correctness or
            # naturalness problem, only an available academic upgrade, was
            # labeled "Needs revision" and given the generic catch-all text
            # "This is recoverable, but the Coach should push it above basic
            # B1 phrasing..." from _line_explanation's hardcoded per-topic
            # pattern chain (a chain built against one specific practice
            # essay's vocabulary -- "help parents"/"caring for children",
            # "less people"/"fewer workers", etc. -- that has no branch for
            # a generic sentence, so it falls through to this vague default
            # every time). Meanwhile the ACTUAL explanation of what changed
            # and why was already sitting right next to it, unused:
            # layers["higher_band_upgrade"]["explanation"]/"why_better",
            # which is either genuinely LLM-generated per sentence (when
            # UpgradeGeneratorConfig is enabled) or the engine's own
            # non-essay-specific template -- either way, real coaching
            # content instead of a generic filler line. When the upgrade is
            # the ONLY thing this item needs, prefer that real explanation.
            upgrade_only = (
                not layers["correctness_fix"]["needed"]
                and not layers["naturalness_fix"]["needed"]
                and layers["higher_band_upgrade"]["needed"]
            )
            if upgrade_only:
                hbu = layers["higher_band_upgrade"]
                explanation_text = hbu.get("explanation") or "Your sentence is correct. It can be upgraded to sound more academic."
                how_to_improve_text = hbu.get("why_better") or "Compare your sentence with the suggested revision to see what changed and why it reads as more academic."
            else:
                explanation_text = self._line_explanation(issues)
                how_to_improve_text = self._how_to_improve(issues, sent, rough_input)
            feedback.append({
                "item_number": item_number,
                "rough_input": rough_input,
                "student_sentence": sent,
                "status": "submitted",
                "explicit_number_used": bool(entry.get("explicit_number")),
                "assignment_reason": entry.get("assignment_reason"),
                "strengths": strengths,
                "issues": issues,
                "sentence_quality_level": quality_level,
                "local_feedback_split": layers,
                "minimal_corrected_version": minimal,
                "upgraded_academic_version": upgraded,
                "suggested_revision": upgraded,
                "is_acceptable_for_target_move": quality_level in {"strong_for_current_move", "basic_functional"},
                "needs_local_fix": quality_level == "needs_local_fix_then_upgrade",
                "needs_higher_band_upgrade": layers["higher_band_upgrade"]["needed"],
                "correct_but_upgrade_optional": upgrade_only,
                "explanation": explanation_text,
                "how_to_improve": how_to_improve_text,
            })
        else:
            model = self._upgraded_revision("", rough_input, ["missing_required_item"])
            feedback.append({
                "item_number": item_number,
                "rough_input": rough_input,
                "student_sentence": None,
                "status": "missing",
                "strengths": [],
                "issues": ["missing_required_item"],
                "is_acceptable_for_target_move": False,
                "minimal_corrected_version": self._model_from_rough_input(rough_input),
                "upgraded_academic_version": model,
                "suggested_revision": model,
                "explanation": "This item was not submitted, so the Coach cannot evaluate the target skill for this part of the mission.",
                "how_to_improve": "Write one complete sentence for this rough idea.",
            })

    for entry in parsed_response.get("overflow_items", []) or []:
        sent = entry.get("text", "")
        strengths, issues = self._sentence_strengths_issues(sent, None)
        issues = ["numbering_or_extra_item_issue"] + issues
        layers = self._feedback_layers(sent, None, strengths, issues)
        feedback.append({
            "item_number": entry.get("item_number"),
            "rough_input": None,
            "student_sentence": sent,
            "status": "extra_or_out_of_range",
            "explicit_number_used": bool(entry.get("explicit_number")),
            "strengths": strengths,
            "issues": issues,
            "local_feedback_split": layers,
            "minimal_corrected_version": layers["correctness_fix"]["minimal_corrected_version"],
            "upgraded_academic_version": layers["higher_band_upgrade"]["upgraded_academic_version"],
            "is_acceptable_for_target_move": False,
            "suggested_revision": layers["higher_band_upgrade"]["upgraded_academic_version"],
            "explanation": "This response has a duplicate, missing, or out-of-range number, so it cannot be matched cleanly to a required item.",
            "how_to_improve": "Use the exact required numbers shown in the mission.",
        })
    return feedback


def _wc128_feedback(self, outcome: str, unit_scores: List[Dict[str, Any]], mission: Dict[str, Any], lines: List[str],
                    required_items: int, completion_gate: Dict[str, Any], item_feedback: List[Dict[str, Any]],
                    mastery_update_allowed: bool) -> Dict[str, Any]:
    # Start from the previous stable behavior but add V1.2.10 feedback quality summaries.
    weakest = sorted(unit_scores, key=lambda x: x["score"])[:2]
    submitted = len(lines)
    missing = [it.get("item_number") for it in item_feedback if it.get("status") == "missing" and isinstance(it.get("item_number"), int)]
    submitted_feedback = [it for it in item_feedback if it.get("status") == "submitted"]
    if outcome == "invalid_empty_response":
        summary = f"No answer was submitted. Please complete all {required_items} items."
        next_action = "submit_required_items"
    elif outcome == "invalid_incomplete_output":
        if submitted_feedback:
            summary = f"You submitted {submitted}/{required_items} required items. I reviewed the submitted sentence(s), but complete all {required_items} items before this mission can count."
        else:
            summary = f"You submitted {submitted}/{required_items} required items. Complete all {required_items} items before this mission can count."
        next_action = "complete_missing_items"
    elif outcome == "pass":
        summary = "Good control of the target move. Review the upgrade suggestions, then repeat once with a new topic before unlocking the next skill."
        next_action = "repeat_or_upgrade_based_on_outcome"
    elif outcome == "partial_pass":
        summary = "Partly successful. Fix the local issues first, then repeat the same move with a little more structure."
        next_action = "repeat_with_scaffold"
    else:
        summary = "The target move is not controlled yet. Fix the local accuracy problems before increasing difficulty."
        next_action = "repeat_easier"

    split_counts = Counter()
    for item in submitted_feedback:
        layers = item.get("local_feedback_split") or {}
        if safe_get(layers, "correctness_fix.needed"):
            split_counts["correctness_fix"] += 1
        if safe_get(layers, "naturalness_fix.needed"):
            split_counts["naturalness_fix"] += 1
        if safe_get(layers, "higher_band_upgrade.needed"):
            split_counts["higher_band_upgrade"] += 1

    student_feedback = {
        "overall_comment": summary,
        "format_feedback": completion_gate.get("message"),
        "feedback_quality_policy": "V1.2.10 separates local correctness fixes, naturalness fixes, and higher-band upgrades.",
        "what_went_well": self._aggregate_strengths(item_feedback),
        "what_to_fix_first": self._prioritize_issues(item_feedback, completion_gate),
        "feedback_layer_counts": dict(split_counts),
        "item_feedback": item_feedback,
        "submitted_items_reviewed": [it.get("item_number") for it in submitted_feedback],
        "missing_items": missing,
        "numbering_feedback": self._numbering_feedback(completion_gate),
        "next_action": next_action,
        "try_again_instruction": self._try_again_instruction(mission, missing, outcome),
    }
    teacher_feedback = {
        "outcome": outcome,
        "mastery_update_allowed": mastery_update_allowed,
        "completion_gate": completion_gate,
        "weakest_observable_units": weakest,
        "feedback_layer_counts": dict(split_counts),
        "interpretation": "Incomplete output is treated as non-mastery evidence in V1.2.10." if not mastery_update_allowed else "Complete attempt can be used as mission-level skill evidence.",
    }
    debug_evaluation = {
        "raw_line_count": submitted,
        "required_items": required_items,
        "observable_unit_scores": unit_scores,
        "hard_gate_applied": not completion_gate.get("mastery_update_allowed", False),
        "submitted_item_numbers": completion_gate.get("submitted_item_numbers", []),
        "missing_item_numbers": completion_gate.get("missing_item_numbers", []),
        "numbering_warnings": completion_gate.get("numbering_warnings", []),
        "local_feedback_schema": LOCAL_FEEDBACK_SCHEMA,
        "score_policy": "mission_score is set to 0.0 when the hard completion gate fails; raw_observable_score_before_gate is kept for debugging.",
    }
    lie_update_decision = {
        "mastery_update_allowed": mastery_update_allowed,
        "reason": "complete_attempt" if mastery_update_allowed else completion_gate.get("status"),
        "emission_type": "performance_evidence" if mastery_update_allowed else "attempt_record_only_not_mastery_evidence",
    }
    return {
        "legacy_feedback": {"summary": summary, "weakest_observable_units": weakest, "next_action": next_action},
        "student_feedback": student_feedback,
        "teacher_feedback": teacher_feedback,
        "debug_evaluation": debug_evaluation,
        "lie_update_decision": lie_update_decision,
    }


def terminal_attempt_feedback_text(result: Dict[str, Any]) -> str:
    """V1.2.10 terminal view: show minimal correction separately from higher-band upgrade."""
    sf = result.get("student_feedback") or {}
    lines = []
    overall = sf.get("overall_comment") or safe_get(result, "feedback.summary")
    if overall:
        lines.append(overall)
    numbering = sf.get("numbering_feedback")
    if numbering:
        lines.append(f"Numbering: {numbering}")
    for item in sf.get("item_feedback") or []:
        if item.get("status") != "submitted":
            continue
        num = item.get("item_number")
        lines.append("")
        lines.append(f"Item {num} feedback:")
        lines.append(f"Your sentence: {item.get('student_sentence')}")
        quality = item.get("sentence_quality_level")
        if quality:
            lines.append("Level: " + str(quality))
        strengths = item.get("strengths") or []
        issues = item.get("issues") or []
        if strengths:
            lines.append("What works: " + "; ".join(str(x) for x in strengths[:5]))
        if issues:
            lines.append("Fix/improve: " + "; ".join(_format_issue_label(x) for x in issues[:6]))
        else:
            lines.append("Fix/improve: no local accuracy problem detected; try the higher-band version to raise the level.")

        layers = item.get("local_feedback_split") or {}
        corr = layers.get("correctness_fix") or {}
        nat = layers.get("naturalness_fix") or {}
        up = layers.get("higher_band_upgrade") or {}
        if corr:
            if corr.get("needed"):
                lines.append("Correctness fix: " + str(corr.get("explanation")))
            if corr.get("minimal_corrected_version"):
                lines.append("Minimal correction: " + str(corr.get("minimal_corrected_version")))
        if nat and nat.get("needed"):
            lines.append("Naturalness fix: " + str(nat.get("explanation")))
        if up and up.get("needed"):
            lines.append("Higher-band upgrade: " + str(up.get("explanation")))
            if up.get("upgraded_academic_version"):
                lines.append("Upgraded version: " + str(up.get("upgraded_academic_version")))
        if item.get("how_to_improve"):
            lines.append("How: " + str(item.get("how_to_improve")))
        if item.get("explanation"):
            lines.append("Why: " + str(item.get("explanation")))
    missing = sf.get("missing_items") or []
    if missing:
        lines.append("")
        lines.append("Missing items: " + ", ".join(map(str, missing)))
        if sf.get("try_again_instruction"):
            lines.append(str(sf.get("try_again_instruction")))
    return "\n".join(lines).strip()


# Install V1.2.10 evaluator overrides on the existing standalone engine classes.
MissionEvaluator._sentence_strengths_issues = _wc128_sentence_strengths_issues
MissionEvaluator._sentence_quality_level = _wc128_sentence_quality_level
MissionEvaluator._minimal_correction = _wc128_minimal_correction
MissionEvaluator._upgraded_revision = _wc128_upgraded_revision
MissionEvaluator._feedback_layers = _wc128_feedback_layers
MissionEvaluator._correctness_explanation = _wc128_correctness_explanation
MissionEvaluator._naturalness_explanation = _wc128_naturalness_explanation
MissionEvaluator._line_explanation = _wc128_line_explanation
MissionEvaluator._how_to_improve = _wc128_how_to_improve
MissionEvaluator._item_feedback = _wc128_item_feedback
MissionEvaluator._feedback = _wc128_feedback



# ---------------------------------------------------------------------------
# V1.2.10 Student-Friendly Feedback Layer Overrides
# ---------------------------------------------------------------------------

STUDENT_FRIENDLY_FEEDBACK_SCHEMA = "WRITING_COACH_STUDENT_FRIENDLY_FEEDBACK_V1_2_10"


def _friendly_label(token: Any) -> str:
    key = _issue_key(token)
    mapping = {
        "passive_preposition_error": "Use the right preposition.",
        "article_determiner_error": "Add the article 'the' where needed.",
        "collocation_error": "Use a natural word combination.",
        "comparative_quantifier_error": "Use 'fewer' with countable plural nouns.",
        "weak_academic_structure": "Make the sentence less spoken and more academic.",
        "weak_sentence_connection": "Show the cause and result more clearly.",
        "preposition_collocation_error": "Use the right preposition.",
        "missing_preposition_error": "Add the missing preposition.",
        "unnatural_gerund_phrase": "Make the phrase more natural.",
        "tradition_preposition_error": "Use 'teach traditions to', not 'teach traditions for'.",
        "generation_article_plural_error": "Use 'the younger generation' or 'younger generations'.",
        "sentence_underdeveloped": "Add a clearer result or detail.",
        "higher_band_upgrade": "Make it stronger for IELTS writing.",
        "lexical_precision_upgrade": "Use more precise academic words.",
        "sentence_too_short_or_underdeveloped": "Write a complete sentence with more detail.",
        "finite_verb_missing_or_unclear": "Add one clear main verb.",
        "subject_missing_or_unclear": "Start with a clear subject.",
    }
    return mapping.get(key, str(token).split(":", 1)[0].replace("_", " ").capitalize() + ".")


def _friendly_strength(token: Any) -> str:
    mapping = {
        "meaning_is_recoverable": "I can understand your idea.",
        "meaning_is_partly_recoverable": "I can partly understand your idea.",
        "finite_verb_present": "Your sentence has a verb.",
        "subject_present": "Your sentence has a clear subject.",
        "idea_matches_prompt_item": "Your idea matches this item.",
        "modal_verb_pattern_ok": "The modal verb pattern is OK.",
        "no_blocked_verb_pattern_detected": "I do not see a major verb-form mistake.",
    }
    return mapping.get(str(token), str(token).replace("_", " ").capitalize() + ".")


def _wc129_sentence_strengths_issues(self, sentence: str, rough_input: Optional[str] = None) -> Tuple[List[str], List[str]]:
    strengths, issues = _wc128_sentence_strengths_issues(self, sentence, rough_input)
    lower = (sentence or "").lower()
    rough = (rough_input or "").lower()

    # Tradition / generation naturalness and local accuracy patterns.
    if ("tradition" in rough or "generation" in rough) or ("tradition" in lower or "generation" in lower):
        if re.search(r"\bteach\s+traditions?\s+for\b", lower):
            issues.append("tradition_preposition_error: use 'teach traditions to younger generations', not 'teach traditions for younger generation'")
        if re.search(r"\bfor\s+younger\s+generation\b", lower):
            issues.append("generation_article_plural_error: use 'the younger generation' or 'younger generations'")
        if re.search(r"\byounger\s+generation\b", lower) and "the younger generation" not in lower and "younger generations" not in lower:
            issues.append("generation_article_plural_error: use 'the younger generation' or 'younger generations'")

    # If a real local issue exists, remove over-general upgrade-only duplicate pressure when possible.
    if _has_issue(issues, "tradition_preposition_error", "generation_article_plural_error") and not _has_issue(issues, "higher_band_upgrade"):
        issues.append("higher_band_upgrade: make the sentence more precise and natural for IELTS writing")
    return strengths, issues


def _wc129_sentence_quality_level(self, sentence: str, issues: List[str]) -> str:
    if _has_issue(issues, "tradition_preposition_error", "generation_article_plural_error"):
        return "needs_small_fix_then_upgrade"
    return _wc128_sentence_quality_level(self, sentence, issues)


def _wc129_minimal_correction(self, sentence: str, rough_input: Optional[str], issues: Optional[List[str]] = None) -> str:
    issues = issues or []
    lower_rough = (rough_input or "").lower()
    if _has_issue(issues, "tradition_preposition_error", "generation_article_plural_error") or "tradition" in lower_rough:
        return "Older people can teach traditions to younger generations."
    return _wc128_minimal_correction(self, sentence, rough_input, issues)


def _wc129_upgraded_revision(self, sentence: str, rough_input: Optional[str], issues: Optional[List[str]] = None) -> str:
    issues = issues or []
    lower_rough = (rough_input or "").lower()
    if _has_issue(issues, "tradition_preposition_error", "generation_article_plural_error") or "tradition" in lower_rough or "younger generation" in lower_rough:
        return "Older people can preserve cultural traditions by passing them on to younger generations."
    return _wc128_upgraded_revision(self, sentence, rough_input, issues)


def _wc129_correctness_explanation(self, issues: List[str]) -> str:
    if _has_issue(issues, "tradition_preposition_error"):
        return "Say 'teach traditions to younger generations', not 'teach traditions for younger generation'."
    if _has_issue(issues, "generation_article_plural_error"):
        return "Use 'the younger generation' or 'younger generations'. Without this, the phrase sounds incomplete."
    return _wc128_correctness_explanation(self, issues)


def _wc129_naturalness_explanation(self, issues: List[str]) -> str:
    if _has_issue(issues, "tradition_preposition_error", "generation_article_plural_error"):
        return "The idea is clear, but the phrase is not natural yet. Use 'pass traditions on to younger generations' for a stronger version."
    return _wc128_naturalness_explanation(self, issues)


def _wc129_line_explanation(self, issues: List[str]) -> str:
    if _has_issue(issues, "tradition_preposition_error", "generation_article_plural_error"):
        return "Your idea is clear, but the phrase after 'teach traditions' needs a small fix. Then you can upgrade the sentence."
    return _wc128_line_explanation(self, issues)


def _wc129_how_to_improve(self, issues: List[str], sentence: str, rough_input: Optional[str]) -> str:
    if _has_issue(issues, "tradition_preposition_error", "generation_article_plural_error"):
        return "First fix the phrase: 'teach traditions to younger generations'. Then make it stronger: 'preserve cultural traditions by passing them on to younger generations'."
    return _wc128_how_to_improve(self, issues, sentence, rough_input)


def _friendly_next_step(issues: List[str], missing: bool = False) -> str:
    if missing:
        return "Write this missing sentence."
    if _has_issue(issues, "finite_verb_missing_or_unclear", "subject_missing_or_unclear", "sentence_too_short_or_underdeveloped"):
        return "Make one complete sentence: subject + verb + full idea."
    if _has_issue(issues, "tradition_preposition_error", "generation_article_plural_error"):
        return "Change 'for younger generation' to 'to younger generations'."
    if _has_issue(issues, "passive_preposition_error", "collocation_error", "article_determiner_error"):
        return "Fix the phrase with 'government'. Use 'by/funded by/paid for by the government'."
    if _has_issue(issues, "comparative_quantifier_error"):
        return "Change 'less people' to 'fewer people' or 'fewer workers'."
    if _has_issue(issues, "preposition_collocation_error", "missing_preposition_error", "unnatural_gerund_phrase"):
        return "Use 'care for children' or 'by caring for children'."
    if _has_issue(issues, "higher_band_upgrade", "weak_academic_structure", "weak_sentence_connection", "lexical_precision_upgrade"):
        return "Use the better version to make the sentence more academic."
    return "Good. Now try the stronger academic version."


def _make_student_friendly_item_feedback(item: Dict[str, Any]) -> Dict[str, Any]:
    status = item.get("status")
    if status == "missing":
        return {
            "level": "missing",
            "main_message": "You did not write this sentence yet.",
            "good": [],
            "fix_first": ["Write one complete sentence for this item."],
            "simple_fix": item.get("minimal_corrected_version"),
            "better_version": item.get("upgraded_academic_version") or item.get("suggested_revision"),
            "why": "The Coach cannot check this part until you write it.",
            "next_step": "Write this missing sentence.",
        }
    issues = item.get("issues") or []
    strengths = item.get("strengths") or []
    level = item.get("sentence_quality_level") or "needs_review"
    simple = item.get("minimal_corrected_version") or item.get("suggested_revision")
    better = item.get("upgraded_academic_version") or item.get("suggested_revision")
    local_problem = _has_issue(
        issues,
        "tradition_preposition_error", "generation_article_plural_error",
        "passive_preposition_error", "article_determiner_error", "collocation_error",
        "comparative_quantifier_error", "preposition_collocation_error", "missing_preposition_error",
        "unnatural_gerund_phrase", "finite_verb_missing_or_unclear", "subject_missing_or_unclear",
    )
    if local_problem:
        main = "Good idea, but fix one language problem first."
    elif _has_issue(issues, "higher_band_upgrade", "weak_academic_structure", "weak_sentence_connection", "lexical_precision_upgrade", "sentence_underdeveloped"):
        main = "Your idea is clear. Now make it stronger."
    else:
        main = "Good sentence. Try the stronger version to improve your IELTS style."
    good_items = []
    for x in strengths:
        label = _friendly_strength(x)
        if label not in good_items:
            good_items.append(label)
    fix_items = []
    for x in issues:
        if _has_issue([x], "higher_band_upgrade"):
            continue
        label = _friendly_label(x)
        if label not in fix_items:
            fix_items.append(label)
    return {
        "schema_version": STUDENT_FRIENDLY_FEEDBACK_SCHEMA,
        "level": level,
        "main_message": main,
        "good": good_items[:4],
        "fix_first": fix_items[:4],
        "simple_fix": simple,
        "better_version": better,
        "why": _wc129_line_explanation(None, issues),
        "next_step": _friendly_next_step(issues),
    }


def _wc129_feedback_layers(self, sentence: str, rough_input: Optional[str], strengths: List[str], issues: List[str]) -> Dict[str, Any]:
    layers = _wc128_feedback_layers(self, sentence, rough_input, strengths, issues)
    layers["schema_version"] = LOCAL_FEEDBACK_SCHEMA
    # Make layer explanations simpler for product display while retaining the raw issue list.
    if layers.get("correctness_fix", {}).get("needed"):
        layers["correctness_fix"]["student_explanation"] = _wc129_correctness_explanation(self, layers["correctness_fix"].get("issues", []))
    if layers.get("naturalness_fix", {}).get("needed"):
        layers["naturalness_fix"]["student_explanation"] = _wc129_naturalness_explanation(self, layers["naturalness_fix"].get("issues", []))
    if layers.get("higher_band_upgrade", {}).get("needed"):
        layers["higher_band_upgrade"]["student_explanation"] = "This version sounds clearer and more academic."
    return layers


def _wc129_item_feedback(self, mission: Dict[str, Any], parsed_response: Dict[str, Any], required_items: int) -> List[Dict[str, Any]]:
    fb = _wc128_item_feedback(self, mission, parsed_response, required_items)
    for item in fb:
        item["student_friendly_feedback"] = _make_student_friendly_item_feedback(item)
    return fb


def _wc129_feedback(self, outcome: str, unit_scores: List[Dict[str, Any]], mission: Dict[str, Any], lines: List[str],
                    required_items: int, completion_gate: Dict[str, Any], item_feedback: List[Dict[str, Any]],
                    mastery_update_allowed: bool) -> Dict[str, Any]:
    bundle = _wc128_feedback(self, outcome, unit_scores, mission, lines, required_items, completion_gate, item_feedback, mastery_update_allowed)
    sf = bundle.get("student_feedback") or {}
    submitted = len([it for it in item_feedback if it.get("status") == "submitted"])
    missing = sf.get("missing_items") or []
    if outcome == "invalid_incomplete_output":
        sf["overall_comment"] = f"Good start. You wrote {submitted}/{required_items} sentence(s). I checked what you wrote, but the mission counts only after all {required_items} sentences are finished."
    elif outcome == "pass":
        sf["overall_comment"] = "Good work. Your sentences are complete. Read the better versions to make them stronger for IELTS writing."
    elif outcome == "partial_pass":
        sf["overall_comment"] = "Partly good. Fix the first language problems, then try again."
    elif outcome == "fail":
        sf["overall_comment"] = "This needs more work. Start with the simple fixes first."
    sf["feedback_quality_policy"] = "V1.2.10 uses simple student wording plus hidden teacher/debug diagnostics."
    sf["student_level_mode"] = "A2_B1_friendly"
    sf["simple_summary"] = sf["overall_comment"]
    if missing:
        sf["try_again_instruction"] = "Finish the missing sentence(s): " + ", ".join(map(str, missing)) + ". Then submit all sentences together."
    bundle["student_feedback"] = sf
    tf = bundle.get("teacher_feedback") or {}
    tf["student_friendly_layer_present"] = True
    tf["student_level_mode"] = "A2_B1_friendly"
    bundle["teacher_feedback"] = tf
    return bundle


def terminal_attempt_feedback_text(result: Dict[str, Any]) -> str:
    """V1.2.10 terminal view: student-friendly first, diagnostics hidden in JSON."""
    sf = result.get("student_feedback") or {}
    lines: List[str] = []
    overall = sf.get("simple_summary") or sf.get("overall_comment") or safe_get(result, "feedback.summary")
    if overall:
        lines.append(str(overall))
    numbering = sf.get("numbering_feedback")
    if numbering:
        lines.append("Numbering: " + str(numbering))

    for item in sf.get("item_feedback") or []:
        if item.get("status") != "submitted":
            continue
        num = item.get("item_number")
        friendly = item.get("student_friendly_feedback") or _make_student_friendly_item_feedback(item)
        lines.append("")
        lines.append(f"Item {num}")
        lines.append("Your sentence: " + str(item.get("student_sentence")))
        if friendly.get("main_message"):
            lines.append(str(friendly.get("main_message")))
        good = friendly.get("good") or []
        if good:
            lines.append("Good: " + " ".join(str(x) for x in good[:3]))
        fix = friendly.get("fix_first") or []
        if fix:
            lines.append("Fix first: " + " ".join(str(x) for x in fix[:3]))
        if friendly.get("simple_fix"):
            lines.append("Simple fix: " + str(friendly.get("simple_fix")))
        if friendly.get("better_version"):
            lines.append("Better IELTS version: " + str(friendly.get("better_version")))
        if friendly.get("why"):
            lines.append("Why: " + str(friendly.get("why")))
        if friendly.get("next_step"):
            lines.append("Next: " + str(friendly.get("next_step")))

    missing = sf.get("missing_items") or []
    if missing:
        lines.append("")
        lines.append("Missing items: " + ", ".join(map(str, missing)))
        if sf.get("try_again_instruction"):
            lines.append(str(sf.get("try_again_instruction")))
    return "\n".join(lines).strip()


# Install V1.2.10 student-friendly overrides.
MissionEvaluator._sentence_strengths_issues = _wc129_sentence_strengths_issues
MissionEvaluator._sentence_quality_level = _wc129_sentence_quality_level
MissionEvaluator._minimal_correction = _wc129_minimal_correction
MissionEvaluator._upgraded_revision = _wc129_upgraded_revision
MissionEvaluator._feedback_layers = _wc129_feedback_layers
MissionEvaluator._correctness_explanation = _wc129_correctness_explanation
MissionEvaluator._naturalness_explanation = _wc129_naturalness_explanation
MissionEvaluator._line_explanation = _wc129_line_explanation
MissionEvaluator._how_to_improve = _wc129_how_to_improve
MissionEvaluator._item_feedback = _wc129_item_feedback
MissionEvaluator._feedback = _wc129_feedback



# ---------------------------------------------------------------------------
# V1.2.10 Student-Friendly Diagnostic Feedback Overrides
# ---------------------------------------------------------------------------

STUDENT_FRIENDLY_FEEDBACK_SCHEMA = "WRITING_COACH_STUDENT_FRIENDLY_DIAGNOSTIC_FEEDBACK_V1_2_10"

_UNCOUNTABLE_HINTS = {
    "advices": ("advice", "Advice is uncountable in English, so do not add -s."),
    "informations": ("information", "Information is uncountable in English, so do not add -s."),
    "knowledges": ("knowledge", "Knowledge is uncountable in English, so do not add -s."),
    "homeworks": ("homework", "Homework is uncountable in English, so do not add -s."),
    "researches": ("research", "Research is usually uncountable in academic English, so do not add -s."),
}


def _wc1210_friendly_label(token: Any) -> str:
    key = _issue_key(token)
    mapping = {
        "uncountable_advice_error": "Use 'advice', not 'advices'.",
        "uncountable_experience_error": "Use 'experience' for life knowledge, not 'experiences'.",
        "incomplete_parallel_structure": "The last part is incomplete. After 'give/share', use nouns, or make a new clause.",
        "weak_verb_choice": "Use a more natural verb: 'share experience' or 'offer advice'.",
        "passive_preposition_error": "Use the right preposition.",
        "article_determiner_error": "Add 'the' where needed.",
        "collocation_error": "Use a natural word combination.",
        "comparative_quantifier_error": "Use 'fewer' with countable plural nouns.",
        "weak_academic_structure": "Make the sentence less spoken and more academic.",
        "weak_sentence_connection": "Show the cause and result more clearly.",
        "preposition_collocation_error": "Use the right preposition.",
        "missing_preposition_error": "Add the missing preposition.",
        "unnatural_gerund_phrase": "Make the phrase more natural.",
        "tradition_preposition_error": "Use 'teach traditions to', not 'teach traditions for'.",
        "generation_article_plural_error": "Use 'the younger generation' or 'younger generations'.",
        "sentence_underdeveloped": "Add one clear result or detail.",
        "higher_band_upgrade": "Make it stronger for IELTS writing.",
        "lexical_precision_upgrade": "Use more precise academic words.",
        "sentence_too_short_or_underdeveloped": "Write a complete sentence with more detail.",
        "finite_verb_missing_or_unclear": "Add one clear main verb.",
        "subject_missing_or_unclear": "Start with a clear subject.",
    }
    return mapping.get(key, str(token).split(":", 1)[0].replace("_", " ").capitalize() + ".")

# Replace the global label function used by student-friendly feedback generation.
_friendly_label = _wc1210_friendly_label


def _wc1210_sentence_strengths_issues(self, sentence: str, rough_input: Optional[str] = None) -> Tuple[List[str], List[str]]:
    strengths, issues = _wc129_sentence_strengths_issues(self, sentence, rough_input)
    lower = (sentence or "").lower()
    rough = (rough_input or "").lower()

    # Item 5: experience / advice / useful for society.
    if ("experience" in rough or "advice" in rough or "society" in rough) or any(k in lower for k in ["experience", "experiences", "advice", "advices", "society"]):
        if re.search(r"\badvices\b", lower):
            issues.append("uncountable_advice_error: use 'advice', not 'advices'")
        if re.search(r"\bexperiences\b", lower) and re.search(r"\b(give|share|offer|provide)\b", lower):
            issues.append("uncountable_experience_error: use 'experience' when you mean knowledge from life")
        if re.search(r"\b(give|gives|gave|share|shares|offer|offers)\b.+\band\s+useful\s+for\s+society\b", lower):
            issues.append("incomplete_parallel_structure: 'and useful for society' is not a noun object after the verb")
        if re.search(r"\bgive\s+(experience|experiences|advice|advices)\b", lower):
            issues.append("weak_verb_choice: use 'share experience' or 'offer advice' instead of 'give experiences/advices'")
        if any(k in lower for k in ["experiences", "advices", "and useful for society"]):
            if not _has_issue(issues, "higher_band_upgrade"):
                issues.append("higher_band_upgrade: make the idea clearer and more academic")

    # Keep issue list stable and non-duplicated.
    seen = set()
    cleaned = []
    for issue in issues:
        key = str(issue)
        if key not in seen:
            cleaned.append(issue)
            seen.add(key)
    return strengths, cleaned


def _wc1210_sentence_quality_level(self, sentence: str, issues: List[str]) -> str:
    if _has_issue(issues, "uncountable_advice_error", "uncountable_experience_error", "incomplete_parallel_structure", "weak_verb_choice"):
        return "needs_local_fix_then_upgrade"
    return _wc129_sentence_quality_level(self, sentence, issues)


def _wc1210_minimal_correction(self, sentence: str, rough_input: Optional[str], issues: Optional[List[str]] = None) -> str:
    issues = issues or []
    lower_rough = (rough_input or "").lower()
    if _has_issue(issues, "uncountable_advice_error", "uncountable_experience_error", "incomplete_parallel_structure", "weak_verb_choice") or "experience" in lower_rough or "advice" in lower_rough:
        return "Seniors can share experience and advice that are useful for society."
    return _wc129_minimal_correction(self, sentence, rough_input, issues)


def _wc1210_upgraded_revision(self, sentence: str, rough_input: Optional[str], issues: Optional[List[str]] = None) -> str:
    issues = issues or []
    lower_rough = (rough_input or "").lower()
    if _has_issue(issues, "uncountable_advice_error", "uncountable_experience_error", "incomplete_parallel_structure", "weak_verb_choice") or "experience" in lower_rough or "advice" in lower_rough:
        return "Older people’s experience can benefit society because they often offer practical advice."
    return _wc129_upgraded_revision(self, sentence, rough_input, issues)


def _wc1210_correctness_explanation(self, issues: List[str]) -> str:
    if _has_issue(issues, "uncountable_advice_error", "uncountable_experience_error", "incomplete_parallel_structure"):
        parts = []
        if _has_issue(issues, "uncountable_advice_error"):
            parts.append("Use 'advice', not 'advices'.")
        if _has_issue(issues, "uncountable_experience_error"):
            parts.append("Use 'experience' when you mean life knowledge or skill.")
        if _has_issue(issues, "incomplete_parallel_structure"):
            parts.append("'And useful for society' is incomplete after the verb; use a full noun phrase or a new clause.")
        return " ".join(parts)
    if _has_issue(issues, "weak_verb_choice"):
        return "'Give experience/advice' is understandable but not the best word choice. Use 'share experience' or 'offer advice'."
    return _wc129_correctness_explanation(self, issues)


def _wc1210_naturalness_explanation(self, issues: List[str]) -> str:
    if _has_issue(issues, "weak_verb_choice"):
        return "The idea is clear, but the verb choice sounds unnatural. People usually share experience and offer advice."
    if _has_issue(issues, "uncountable_advice_error", "uncountable_experience_error", "incomplete_parallel_structure"):
        return "After fixing the local grammar, make the sentence smoother by saying how older people's experience helps society."
    return _wc129_naturalness_explanation(self, issues)


def _wc1210_why_sentence_needs_work(issues: List[str]) -> List[str]:
    reasons: List[str] = []
    if _has_issue(issues, "uncountable_advice_error"):
        reasons.append("'Advice' is uncountable. We say 'advice', not 'advices'.")
    if _has_issue(issues, "uncountable_experience_error"):
        reasons.append("Here, 'experience' means life knowledge, so it is usually singular/uncountable.")
    if _has_issue(issues, "incomplete_parallel_structure"):
        reasons.append("'And useful for society' is not complete after 'can give'. You need a noun object, or a full new clause.")
    if _has_issue(issues, "weak_verb_choice"):
        reasons.append("'Give experiences/advice' is not natural here. Use 'share experience' or 'offer advice'.")
    if _has_issue(issues, "tradition_preposition_error"):
        reasons.append("After 'teach traditions', use 'to' for the people who learn them.")
    if _has_issue(issues, "generation_article_plural_error"):
        reasons.append("Say 'younger generations' or 'the younger generation'; without this, the noun phrase sounds incomplete.")
    if _has_issue(issues, "passive_preposition_error", "collocation_error"):
        reasons.append("'Covered with government' is not a natural phrase. Use 'covered by' or 'funded by the government'.")
    if _has_issue(issues, "article_determiner_error"):
        reasons.append("Use 'the government' when you mean the public authority responsible for services.")
    if _has_issue(issues, "comparative_quantifier_error"):
        reasons.append("Use 'fewer' with countable plural nouns such as people or workers.")
    if _has_issue(issues, "preposition_collocation_error", "missing_preposition_error", "unnatural_gerund_phrase"):
        reasons.append("Use a natural care phrase: 'care for children', 'take care of children', or 'by caring for children'.")
    if _has_issue(issues, "weak_academic_structure", "weak_sentence_connection", "higher_band_upgrade", "lexical_precision_upgrade") and not reasons:
        reasons.append("Your idea is understandable, but the sentence is still basic. Make the relationship clearer and more academic.")
    return reasons[:4]


def _wc1210_why_better_version(issues: List[str], better: str) -> List[str]:
    reasons: List[str] = []
    if _has_issue(issues, "uncountable_advice_error", "uncountable_experience_error"):
        reasons.append("It uses the correct nouns: 'experience' and 'advice'.")
    if _has_issue(issues, "incomplete_parallel_structure", "weak_verb_choice"):
        reasons.append("It avoids the incomplete phrase and uses a more natural idea: experience helps people offer advice.")
    if _has_issue(issues, "tradition_preposition_error", "generation_article_plural_error"):
        reasons.append("It uses a natural phrase: 'passing traditions on to younger generations'.")
    if _has_issue(issues, "passive_preposition_error", "collocation_error", "article_determiner_error"):
        reasons.append("It uses a natural government/healthcare phrase and explains the result for public spending.")
    if _has_issue(issues, "comparative_quantifier_error", "weak_academic_structure", "weak_sentence_connection"):
        reasons.append("It shows the cause and result more clearly, which is better for academic writing.")
    if not reasons:
        reasons.append("It is clearer, more natural, and more academic than the first version.")
    return reasons[:3]


def _wc1210_main_message(issues: List[str]) -> str:
    if _has_issue(issues, "uncountable_advice_error", "uncountable_experience_error", "incomplete_parallel_structure", "weak_verb_choice"):
        return "Good idea, but fix the nouns and sentence structure."
    if _has_issue(issues, "tradition_preposition_error", "generation_article_plural_error"):
        return "Good idea, but fix the phrase after 'teach traditions'."
    if _has_issue(issues, "passive_preposition_error", "collocation_error", "article_determiner_error"):
        return "Good idea, but fix the phrase with 'government'."
    if _has_issue(issues, "comparative_quantifier_error"):
        return "Good idea, but fix one grammar word first."
    if _has_issue(issues, "preposition_collocation_error", "missing_preposition_error", "unnatural_gerund_phrase"):
        return "Good idea, but fix the care phrase."
    if _has_issue(issues, "higher_band_upgrade", "weak_academic_structure", "weak_sentence_connection", "lexical_precision_upgrade", "sentence_underdeveloped"):
        return "Your idea is clear. Now make it stronger."
    return "Good sentence. Try the stronger version to improve your IELTS style."


def _make_student_friendly_item_feedback(item: Dict[str, Any]) -> Dict[str, Any]:
    status = item.get("status")
    if status == "missing":
        return {
            "schema_version": STUDENT_FRIENDLY_FEEDBACK_SCHEMA,
            "level": "missing",
            "main_message": "You did not write this sentence yet.",
            "good": [],
            "fix_first": ["Write one complete sentence for this item."],
            "simple_fix": item.get("minimal_corrected_version"),
            "better_version": item.get("upgraded_academic_version") or item.get("suggested_revision"),
            "why_your_sentence_needs_work": ["The Coach cannot check this item until you write it."],
            "why_better_version_is_better": ["The model sentence shows one clear way to express the idea."],
            "next_step": "Write this missing sentence.",
        }
    issues = item.get("issues") or []
    strengths = item.get("strengths") or []
    level = item.get("sentence_quality_level") or "needs_review"
    simple = item.get("minimal_corrected_version") or item.get("suggested_revision")
    better = item.get("upgraded_academic_version") or item.get("suggested_revision")
    good_items: List[str] = []
    for x in strengths:
        label = _friendly_strength(x)
        if label not in good_items:
            good_items.append(label)
    fix_items: List[str] = []
    for x in issues:
        if _has_issue([x], "higher_band_upgrade"):
            continue
        label = _friendly_label(x)
        if label not in fix_items:
            fix_items.append(label)
    return {
        "schema_version": STUDENT_FRIENDLY_FEEDBACK_SCHEMA,
        "level": level,
        "main_message": _wc1210_main_message(issues),
        "good": good_items[:4],
        "fix_first": fix_items[:5],
        "simple_fix": simple,
        "better_version": better,
        "why_your_sentence_needs_work": _wc1210_why_sentence_needs_work(issues),
        "why_better_version_is_better": _wc1210_why_better_version(issues, better),
        "next_step": _friendly_next_step(issues),
    }


def _wc1210_feedback_layers(self, sentence: str, rough_input: Optional[str], strengths: List[str], issues: List[str]) -> Dict[str, Any]:
    layers = _wc129_feedback_layers(self, sentence, rough_input, strengths, issues)
    layers["schema_version"] = "WRITING_COACH_LOCAL_FEEDBACK_SPLIT_V1_2_10"
    layers.setdefault("student_diagnostic_summary", {})
    layers["student_diagnostic_summary"] = {
        "why_sentence_needs_work": _wc1210_why_sentence_needs_work(issues),
        "why_better_version_is_better": _wc1210_why_better_version(issues, layers.get("higher_band_upgrade", {}).get("upgraded_academic_version")),
    }
    return layers


def _wc1210_item_feedback(self, mission: Dict[str, Any], parsed_response: Dict[str, Any], required_items: int) -> List[Dict[str, Any]]:
    fb = _wc128_item_feedback(self, mission, parsed_response, required_items)
    for item in fb:
        item["student_friendly_feedback"] = _make_student_friendly_item_feedback(item)
    return fb


def _wc1210_feedback(self, outcome: str, unit_scores: List[Dict[str, Any]], mission: Dict[str, Any], lines: List[str],
                    required_items: int, completion_gate: Dict[str, Any], item_feedback: List[Dict[str, Any]],
                    mastery_update_allowed: bool) -> Dict[str, Any]:
    bundle = _wc129_feedback(self, outcome, unit_scores, mission, lines, required_items, completion_gate, item_feedback, mastery_update_allowed)
    sf = bundle.get("student_feedback") or {}
    sf["feedback_quality_policy"] = "V1.2.10 uses student-friendly diagnostic feedback: simple wording plus exact local error explanations."
    sf["student_level_mode"] = "A2_B1_friendly_with_diagnostic_reason"
    # Refresh item feedback after wc129 bundle may have copied it.
    refreshed = []
    for item in sf.get("item_feedback") or []:
        item["student_friendly_feedback"] = _make_student_friendly_item_feedback(item)
        refreshed.append(item)
    sf["item_feedback"] = refreshed
    bundle["student_feedback"] = sf
    tf = bundle.get("teacher_feedback") or {}
    tf["student_friendly_diagnostic_layer_present"] = True
    tf["student_level_mode"] = "A2_B1_friendly_with_diagnostic_reason"
    bundle["teacher_feedback"] = tf
    return bundle


def terminal_attempt_feedback_text(result: Dict[str, Any]) -> str:
    """V1.2.10 terminal view: simple but diagnostically useful."""
    sf = result.get("student_feedback") or {}
    lines: List[str] = []
    overall = sf.get("simple_summary") or sf.get("overall_comment") or safe_get(result, "feedback.summary")
    if overall:
        lines.append(str(overall))
    numbering = sf.get("numbering_feedback")
    if numbering:
        lines.append("Numbering: " + str(numbering))

    for item in sf.get("item_feedback") or []:
        if item.get("status") != "submitted":
            continue
        num = item.get("item_number")
        friendly = item.get("student_friendly_feedback") or _make_student_friendly_item_feedback(item)
        lines.append("")
        lines.append(f"Item {num}")
        lines.append("Your sentence: " + str(item.get("student_sentence")))
        if friendly.get("main_message"):
            lines.append(str(friendly.get("main_message")))
        good = friendly.get("good") or []
        if good:
            lines.append("Good: " + " ".join(str(x) for x in good[:3]))
        fix = friendly.get("fix_first") or []
        if fix:
            lines.append("Fix first:")
            for label in fix[:4]:
                lines.append("- " + str(label))
        if friendly.get("simple_fix"):
            lines.append("Simple fix: " + str(friendly.get("simple_fix")))
        if friendly.get("better_version"):
            lines.append("Better IELTS version: " + str(friendly.get("better_version")))
        why_bad = friendly.get("why_your_sentence_needs_work") or []
        if why_bad:
            lines.append("Why your sentence needs work:")
            for reason in why_bad[:3]:
                lines.append("- " + str(reason))
        why_better = friendly.get("why_better_version_is_better") or []
        if why_better:
            lines.append("Why the better version is better:")
            for reason in why_better[:3]:
                lines.append("- " + str(reason))
        if friendly.get("next_step"):
            lines.append("Next: " + str(friendly.get("next_step")))

    missing = sf.get("missing_items") or []
    if missing:
        lines.append("")
        lines.append("Missing items: " + ", ".join(map(str, missing)))
        if sf.get("try_again_instruction"):
            lines.append(str(sf.get("try_again_instruction")))
    return "\n".join(lines).strip()


# Install V1.2.10 student-friendly diagnostic overrides.
MissionEvaluator._sentence_strengths_issues = _wc1210_sentence_strengths_issues
MissionEvaluator._sentence_quality_level = _wc1210_sentence_quality_level
MissionEvaluator._minimal_correction = _wc1210_minimal_correction
MissionEvaluator._upgraded_revision = _wc1210_upgraded_revision
MissionEvaluator._feedback_layers = _wc1210_feedback_layers
MissionEvaluator._correctness_explanation = _wc1210_correctness_explanation
MissionEvaluator._naturalness_explanation = _wc1210_naturalness_explanation
MissionEvaluator._item_feedback = _wc1210_item_feedback
MissionEvaluator._feedback = _wc1210_feedback


# ---------------------------------------------------------------------------
# V1.2.11 — QA-audit patch: scope upgrades to the move's declared skills,
# stop handing out finished answers for unattempted items, surface
# target-skill vs move-primary-skill mismatches.
# See writing_coach_role_qa_audit_v1_2_10.md, requirements R2-R8.
# ---------------------------------------------------------------------------

MISSION_RESULT_SCHEMA_V1_2_11 = "WRITING_COACH_MISSION_RESULT_V1_2_11"
EVALUATOR_VERSION_V1_2_11 = "writing_coach_v1_2_11_scoped_upgrade_evaluator"

# Skills that justify adding clause-level complexity (subordination, cause/effect
# connectors, register shifts) to a "better version". If the selected move does not
# declare any of these (as primary or secondary microskill), the upgrade layer is
# capped to single-clause, lexical/collocation-level improvement instead.
_WC1211_COMPLEX_CLAUSE_SKILLS = {
    "academic_register_control",
    "hedging_control",
    "arg_reasoning_chain_completeness",
    "arg_reason_generation",
    "arg_claim_generation",
    "transition_control",
    "cohesion_control",
    "paragraph_progression",
    "reference_management",
}

# Foundational moves where the primary point is to get one correct, simple sentence
# down -- not to demonstrate complex-sentence formation at the same time.
_WC1211_FOUNDATIONAL_SENTENCE_SKILLS = {
    "simple_sentence_construction",
}

# because/which/when/although/while/since/so that + a second finite verb signal a
# second clause has been added. Gerund/prepositional phrases ("by caring for
# children", "by passing them on to") are NOT flagged: they don't add a finite
# clause, so a sentence using them is still structurally a simple sentence.
_WC1211_CLAUSE_MARKERS = re.compile(
    r"\b(because|although|which|while|whereas|since|so that)\b", re.IGNORECASE
)


def _wc1211_upgrade_scope(mission):
    """Decide how much complexity the higher-band upgrade may add, based on the
    skills the selected move actually declares (R2), not a blanket IELTS pass (R3)."""
    move = mission.get("selected_move") or {}
    primary = move.get("primary_microskill")
    secondary = set(move.get("secondary_microskills") or [])
    declared = ({primary} if primary else set()) | secondary
    allows_complex_clauses = bool(declared & _WC1211_COMPLEX_CLAUSE_SKILLS)
    is_foundational_sentence_move = primary in _WC1211_FOUNDATIONAL_SENTENCE_SKILLS
    in_scope_for_full_upgrade = allows_complex_clauses or not is_foundational_sentence_move
    return {
        "declared_skills": sorted(declared),
        "primary_microskill": primary,
        "allows_complex_clauses": allows_complex_clauses,
        "in_scope_for_full_upgrade": in_scope_for_full_upgrade,
    }


def _wc1211_capped_upgrade(rough_input, fallback):
    """Single-clause, in-scope replacement for the known demo upgrade patterns that
    add subordination beyond a simple_sentence_construction move's declared scope."""
    r = (rough_input or "").lower()
    if "healthcare" in r or "government pays" in r:
        return "An ageing population sharply increases government healthcare spending."
    if "retire" in r or "economy slows" in r:
        return "Mass retirement shrinks the workforce and slows economic growth."
    if "grandparents" in r or "care for children" in r:
        return "Grandparents can support working parents by helping to care for children."
    if "traditions" in r or "younger generation" in r:
        return "Older people can preserve cultural traditions by passing them on to younger generations."
    if "experience" in r or "advice" in r:
        return "Older people's experience benefits society through practical advice."
    return fallback


def _wc1211_upgraded_revision(self, sentence, rough_input, issues=None):
    base = _wc1210_upgraded_revision(self, sentence, rough_input, issues)
    scope = getattr(self, "_wc1211_scope", None)
    self._wc1211_last_capped = False
    self._wc1211_last_preview = None
    if not scope or scope.get("in_scope_for_full_upgrade"):
        return base
    if base and _WC1211_CLAUSE_MARKERS.search(base):
        capped = _wc1211_capped_upgrade(rough_input, base)
        if capped != base:
            self._wc1211_last_capped = True
            self._wc1211_last_preview = base
        return capped
    return base


def _wc1211_feedback_layers(self, sentence, rough_input, strengths, issues):
    layers = _wc1210_feedback_layers(self, sentence, rough_input, strengths, issues)
    scope = getattr(self, "_wc1211_scope", None) or {"in_scope_for_full_upgrade": True}
    layers["upgrade_scope"] = scope
    hbu = layers.get("higher_band_upgrade") or {}
    # _wc1210_feedback_layers already called self._upgraded_revision (patched to
    # _wc1211_upgraded_revision) internally, so hbu["upgraded_academic_version"] is
    # already the capped value if capping applied. Read the flag set during that
    # call instead of re-detecting clause markers on an already-capped string.
    if getattr(self, "_wc1211_last_capped", False):
        capped_version = hbu.get("upgraded_academic_version")
        hbu["explanation"] = (
            "This version stays inside today's mission skill (a correct, natural, single-clause "
            "sentence) instead of adding clause structures this mission does not train."
        )
        hbu["scope_capped"] = True
        hbu["preview_beyond_scope"] = {
            "version": getattr(self, "_wc1211_last_preview", None),
            "note": (
                "This goes beyond today's mission skill scope. Shown as an optional preview for "
                "a later, harder mission -- not required here."
            ),
        }
        layers["higher_band_upgrade"] = hbu
        diag = layers.get("student_diagnostic_summary") or {}
        diag["why_better_version_is_better"] = _wc1210_why_better_version(issues, capped_version)
        layers["student_diagnostic_summary"] = diag
    # R7: naturalness_fix should not silently duplicate higher_band_upgrade text.
    nat = layers.get("naturalness_fix") or {}
    hbu = layers.get("higher_band_upgrade") or {}
    if nat.get("natural_version") and nat.get("natural_version") == hbu.get("upgraded_academic_version"):
        minimal = safe_get(layers, "correctness_fix.minimal_corrected_version")
        if minimal and minimal != hbu.get("upgraded_academic_version"):
            nat["natural_version"] = minimal
            nat["explanation"] = (nat.get("explanation") or "") + " (Shown as the local-naturalness step; see higher_band_upgrade for the fuller rewrite.)"
        else:
            nat["needed"] = False
        layers["naturalness_fix"] = nat
    return layers


_wc1210_make_student_friendly_item_feedback = _make_student_friendly_item_feedback


def _make_student_friendly_item_feedback(item):
    status = item.get("status")
    if status == "missing":
        rough = item.get("rough_input") or "your idea"
        model = item.get("upgraded_academic_version") or item.get("minimal_corrected_version")
        return {
            "schema_version": STUDENT_FRIENDLY_FEEDBACK_SCHEMA,
            "level": "missing",
            "main_message": "You have not written this sentence yet. Try it yourself first.",
            "good": [],
            "fix_first": ["Write one complete sentence for this idea: '" + str(rough) + "'."],
            "simple_fix": None,
            "better_version": None,
            "production_prompt": "Write one complete sentence using this idea: " + str(rough) + ". Use a clear subject and a main verb.",
            "model_sentence_if_stuck": model,
            "why_your_sentence_needs_work": ["The Coach cannot check this item until you write it."],
            "why_better_version_is_better": [],
            "next_step": "Write the sentence yourself first. Only look at 'model_sentence_if_stuck' if you get stuck.",
        }
    result = _wc1210_make_student_friendly_item_feedback(item)
    preview = safe_get(item, "local_feedback_split.higher_band_upgrade.preview_beyond_scope")
    if preview:
        result["beyond_this_mission"] = preview
    return result


def _wc1211_item_feedback(self, mission, parsed_response, required_items):
    # Compute and stash the upgrade scope for this mission before building items,
    # so _upgraded_revision / _feedback_layers (called deep inside _wc128_item_feedback)
    # can read it via self.
    self._wc1211_scope = _wc1211_upgrade_scope(mission)
    fb = _wc128_item_feedback(self, mission, parsed_response, required_items)
    for item in fb:
        item["student_friendly_feedback"] = _make_student_friendly_item_feedback(item)
    return fb


def _wc1211_feedback(self, outcome, unit_scores, mission, lines,
                    required_items, completion_gate, item_feedback,
                    mastery_update_allowed):
    bundle = _wc1210_feedback(self, outcome, unit_scores, mission, lines, required_items, completion_gate, item_feedback, mastery_update_allowed)
    sf = bundle.get("student_feedback") or {}
    refreshed = []
    for item in sf.get("item_feedback") or []:
        item["student_friendly_feedback"] = _make_student_friendly_item_feedback(item)
        refreshed.append(item)
    sf["item_feedback"] = refreshed
    sf["feedback_quality_policy"] = (
        "V1.2.11 scopes higher-band upgrades to the mission's declared move skills, uses a "
        "production prompt instead of a finished model sentence for unattempted items, and "
        "reports the target-skill vs. move-primary-skill relationship explicitly."
    )

    # R8: surface target_skill vs move.primary_microskill mismatch, don't bury it.
    role = mission.get("target_skill_role_in_move") or safe_get(mission, "selected_move.target_skill_role_in_move")
    primary = safe_get(mission, "selected_move.primary_microskill")
    target_name = mission.get("target_skill_name") or mission.get("target_skill_id")
    scope_note = None
    if role and role != "primary_target" and primary and primary != mission.get("target_skill_id"):
        scope_note = (
            "Today's mission move mainly trains '" + str(primary) + "'. '" + str(target_name) + "' is checked as a "
            + str(role).replace("_", " ") + " inside it, not as the main focus -- a pass here is weaker "
            "standalone evidence for '" + str(target_name) + "' than a move whose primary skill is '" + str(target_name) + "'."
        )
        sf["skill_scope_note"] = scope_note
    bundle["student_feedback"] = sf

    tf = bundle.get("teacher_feedback") or {}
    if scope_note:
        tf["skill_scope_note"] = scope_note
    tf["upgrade_scope"] = getattr(self, "_wc1211_scope", None)
    bundle["teacher_feedback"] = tf

    debug = bundle.get("debug_evaluation") or {}
    debug["upgrade_scope"] = getattr(self, "_wc1211_scope", None)
    bundle["debug_evaluation"] = debug

    return bundle


def terminal_attempt_feedback_text(result):
    """V1.2.11 terminal view: same as V1.2.10, plus scope notes and the
    production-prompt / model-sentence-if-stuck split for missing items."""
    sf = result.get("student_feedback") or {}
    lines = []
    overall = sf.get("simple_summary") or sf.get("overall_comment") or safe_get(result, "feedback.summary")
    if overall:
        lines.append(str(overall))
    if sf.get("skill_scope_note"):
        lines.append("Note: " + str(sf.get("skill_scope_note")))
    numbering = sf.get("numbering_feedback")
    if numbering:
        lines.append("Numbering: " + str(numbering))

    for item in sf.get("item_feedback") or []:
        num = item.get("item_number")
        friendly = item.get("student_friendly_feedback") or _make_student_friendly_item_feedback(item)
        if item.get("status") == "missing":
            lines.append("")
            lines.append("Item " + str(num) + " (not written yet)")
            if friendly.get("production_prompt"):
                lines.append("Try this: " + str(friendly.get("production_prompt")))
            if friendly.get("model_sentence_if_stuck"):
                lines.append("If you're stuck: " + str(friendly.get("model_sentence_if_stuck")))
            continue
        if item.get("status") != "submitted":
            continue
        lines.append("")
        lines.append("Item " + str(num))
        lines.append("Your sentence: " + str(item.get("student_sentence")))
        if friendly.get("main_message"):
            lines.append(str(friendly.get("main_message")))
        good = friendly.get("good") or []
        if good:
            lines.append("Good: " + " ".join(str(x) for x in good[:3]))
        fix = friendly.get("fix_first") or []
        if fix:
            lines.append("Fix first:")
            for label in fix[:4]:
                lines.append("- " + str(label))
        if friendly.get("simple_fix"):
            lines.append("Simple fix: " + str(friendly.get("simple_fix")))
        if friendly.get("better_version"):
            lines.append("Better version: " + str(friendly.get("better_version")))
        beyond = friendly.get("beyond_this_mission")
        if beyond and beyond.get("version"):
            lines.append("Beyond this mission (optional preview): " + str(beyond.get("version")))
        why_bad = friendly.get("why_your_sentence_needs_work") or []
        if why_bad:
            lines.append("Why your sentence needs work:")
            for reason in why_bad[:3]:
                lines.append("- " + str(reason))
        why_better = friendly.get("why_better_version_is_better") or []
        if why_better:
            lines.append("Why the better version is better:")
            for reason in why_better[:3]:
                lines.append("- " + str(reason))
        if friendly.get("next_step"):
            lines.append("Next: " + str(friendly.get("next_step")))

    missing = sf.get("missing_items") or []
    if missing:
        lines.append("")
        lines.append("Missing items: " + ", ".join(map(str, missing)))
        if sf.get("try_again_instruction"):
            lines.append(str(sf.get("try_again_instruction")))
    return "\n".join(lines).strip()


_wc126_evaluate = MissionEvaluator.evaluate


def _wc1211_evaluate(self, mission_payload, response_text):
    result = _wc126_evaluate(self, mission_payload, response_text)
    result["schema_version"] = MISSION_RESULT_SCHEMA_V1_2_11
    result["evaluator_version"] = EVALUATOR_VERSION_V1_2_11
    
    return result


# Install V1.2.11 overrides (evaluator-only; mission generation stays V1.2.10).
MissionEvaluator._upgraded_revision = _wc1211_upgraded_revision
MissionEvaluator._feedback_layers = _wc1211_feedback_layers
MissionEvaluator._item_feedback = _wc1211_item_feedback
MissionEvaluator._feedback = _wc1211_feedback
MissionEvaluator.evaluate = _wc1211_evaluate


# ---------------------------------------------------------------------------
# V1.2.12 -- Hint-before-answer coaching methodology, on top of V1.2.11.
#
# The problem V1.2.11 did not fix: even with scoped, level-appropriate
# upgrades, the evaluator still revealed the corrected sentence and the
# upgraded sentence in the same breath as the diagnosis. That is answer
# exposure, not coaching -- the student never has to do the corrective work
# themselves, so the same error has no reason not to recur.
#
# V1.2.12 adds two things:
#  1. Scaffold-aware mission generation: each move bank move can define
#     heavy/medium/light/transfer scaffolding (already authored in the new
#     production move bank, previously unused). The mission now picks a
#     level from the learner's mastery_estimate and stamps whether the model
#     may be shown on a first attempt.
#  2. A hint-before-answer revision ladder in the evaluator: below "heavy"
#     scaffold, a wrong sentence's first evaluation gets a Socratic,
#     rule-pointing hint -- not the corrected sentence. Only on resubmission
#     (or at heavy scaffold) is the correction revealed. The academic
#     upgrade is withheld until the sentence is actually correct, and is
#     then offered as an explicitly separate, optional step -- never bundled
#     with the correctness fix. This requires tracking attempts per item
#     across calls, via an item_revision_ledger threaded through
#     evaluate()/coach_state, not just per-mission state.
# ---------------------------------------------------------------------------

MISSION_RESULT_SCHEMA_V1_2_12 = "WRITING_COACH_MISSION_RESULT_V1_2_12"
EVALUATOR_VERSION_V1_2_12 = "writing_coach_v1_2_12_hint_ladder_evaluator"
MISSION_RESULT_SCHEMA_V1_2_13 = "WRITING_COACH_MISSION_RESULT_V1_2_13"
EVALUATOR_VERSION_V1_2_13 = "writing_coach_v1_2_13_llm_judge_pilot_evaluator"

_WC1212_SCAFFOLD_ORDER = ["heavy", "medium", "light", "transfer"]


def _wc1212_pick_scaffold_level(mastery_estimate):
    """Map mastery estimate to a scaffold level. Low mastery gets the most
    support (model shown immediately); high mastery gets none (pure
    transfer). Thresholds are a deliberate default, not derived from data --
    tune once real attempt history exists."""
    m = mastery_estimate if isinstance(mastery_estimate, (int, float)) else 0.4
    if m < 0.35:
        return "heavy"
    if m < 0.6:
        return "medium"
    if m < 0.85:
        return "light"
    return "transfer"


_wc126_mission_builder_build = MissionBuilder.build


def _wc1212_mission_builder_build(self, skill, move, move_candidates, context):
    mission = _wc126_mission_builder_build(self, skill, move, move_candidates, context)
    scaffolding = move.get("scaffolding") or {}
    level = _wc1212_pick_scaffold_level(getattr(skill, "mastery_estimate", None))
    if level not in scaffolding:
        resolved = None
        for fallback in _WC1212_SCAFFOLD_ORDER:
            if fallback in scaffolding:
                resolved = fallback
                break
        level = resolved
    cfg = scaffolding.get(level) or {}
    mission["scaffold_level"] = level or "not_specified_by_move_bank"
    # Default to showing the model when the move bank doesn't define
    # scaffolding at all, so older/dev move banks keep V1.2.11 behavior.
    mission["show_model_on_first_attempt"] = bool(cfg.get("include_model", True)) if scaffolding else True
    return mission


MissionBuilder.build = _wc1212_mission_builder_build


def _wc1212_hint_for_issues(issues):
    """Socratic, rule-pointing hints -- deliberately do NOT state the fix.
    Compare to _wc1210_why_sentence_needs_work, which states the rule
    directly; that function is still used once the fix is revealed."""
    hints = []

    def add(text):
        if text not in hints:
            hints.append(text)

    if _has_issue(issues, "uncountable_advice_error"):
        add("Look at the word 'advice'. Can you say 'an advice' or 'two advices'? What does that tell you about how to use it here?")
    if _has_issue(issues, "uncountable_experience_error"):
        add("When 'experience' means general life knowledge rather than one event, is it usually countable or uncountable?")
    if _has_issue(issues, "incomplete_parallel_structure"):
        add("Look at the part after 'and'. Is it the same kind of word or phrase as the other items in your list?")
    if _has_issue(issues, "weak_verb_choice"):
        add("Think about what people naturally do with 'advice' and 'experience' -- is 'give' the verb you'd expect here?")
    if _has_issue(issues, "tradition_preposition_error"):
        add("Which preposition usually follows the pattern 'teach [something] ___ [someone]'?")
    if _has_issue(issues, "generation_article_plural_error"):
        add("Does 'generation' need 'the' in front of it here, or should it be plural instead?")
    if _has_issue(issues, "passive_preposition_error", "collocation_error"):
        add("How do we usually say a government pays for something -- which preposition goes with 'covered' or 'funded'?")
    if _has_issue(issues, "article_determiner_error"):
        add("Are you talking about government in general, or one specific government? Does that change whether you need 'the'?")
    if _has_issue(issues, "comparative_quantifier_error"):
        add("Is 'people' countable or uncountable? Which comparative word matches that -- 'less' or 'fewer'?")
    if _has_issue(issues, "preposition_collocation_error", "missing_preposition_error", "unnatural_gerund_phrase"):
        add("What's the natural way to say you look after children -- which preposition goes with 'care'?")
    if _has_issue(issues, "finite_verb_missing_or_unclear"):
        add("Does your sentence have one clear action word -- a main verb?")
    if _has_issue(issues, "subject_missing_or_unclear"):
        add("Who or what is doing the action? Is that clear at the start of your sentence?")
    if not hints:
        add("Read your sentence again. Which part sounds incomplete or not quite natural?")
    return hints[:3]


_wc1211_make_student_friendly_item_feedback = _make_student_friendly_item_feedback


def _wc1212_make_student_friendly_item_feedback(item, mission, ledger):
    """Returns (friendly_feedback_dict, new_ledger_entry_or_None)."""
    status = item.get("status")
    scaffold_level = mission.get("scaffold_level") or "medium"
    show_model_first = bool(mission.get("show_model_on_first_attempt", True))

    if status == "missing":
        friendly = dict(_wc1211_make_student_friendly_item_feedback(item))
        if scaffold_level in {"light", "transfer"}:
            friendly["model_sentence_if_stuck"] = None
            friendly["next_step"] = (
                "Write the sentence yourself. No model is shown at this level -- "
                "use the idea prompt and your own judgement."
            )
        return friendly, None

    if status != "submitted":
        return _wc1211_make_student_friendly_item_feedback(item), None

    issues = item.get("issues") or []
    has_real_issues = any(not _has_issue([i], "higher_band_upgrade") for i in issues)
    mission_id = mission.get("mission_id")
    item_number = item.get("item_number")
    key = f"{mission_id}:{item_number}"
    prior = ledger.get(key) or {"attempts_with_issues": 0, "corrected_confirmed": False}

    full = _wc1211_make_student_friendly_item_feedback(item)

    if not has_real_issues:
        # Already correct. Correction and upgrade are different cognitive
        # jobs -- offer the upgrade as a clearly separate, optional step,
        # never bundled with a correctness fix the student didn't need.
        new_entry = {"attempts_with_issues": prior["attempts_with_issues"], "corrected_confirmed": True}
        friendly = dict(full)
        has_upgrade = bool(friendly.get("better_version"))
        friendly["level"] = "correct_ready_for_upgrade" if has_upgrade else "correct"
        friendly["fix_first"] = []
        friendly["simple_fix"] = None
        friendly["why_your_sentence_needs_work"] = []
        friendly["revision_stage"] = "upgrade_offered" if has_upgrade else "correct_no_upgrade"
        friendly["main_message"] = (
            "Correct. Optional: here's a stronger way to say it." if has_upgrade else "Correct. Well done."
        )
        friendly["next_step"] = (
            "This sentence is correct as written. The 'better_version' below is optional -- try it if you want to push further."
            if has_upgrade else
            "This sentence is correct. Move to the next item."
        )
        return friendly, new_entry

    if show_model_first or prior["attempts_with_issues"] >= 1:
        # Heavy scaffold, or this is a resubmission after at least one hint
        # already given: reveal the correction now, but still hold back the
        # upgrade until the student has actually produced a correct
        # sentence themselves.
        new_entry = {"attempts_with_issues": prior["attempts_with_issues"] + 1, "corrected_confirmed": False}
        friendly = dict(full)
        friendly["better_version"] = None
        friendly["why_better_version_is_better"] = []
        friendly["beyond_this_mission"] = None
        friendly["revision_stage"] = "correction_revealed"
        friendly["next_step"] = (
            f"Fix your sentence using 'simple_fix' as a model, then resubmit item {item_number}. "
            "We'll look at a stronger version once this is correct."
        )
        return friendly, new_entry

    # First time this item has real issues, and the scaffold level withholds
    # the model on a first attempt: hint only, nothing revealed.
    new_entry = {"attempts_with_issues": prior["attempts_with_issues"] + 1, "corrected_confirmed": False}
    friendly = {
        "schema_version": STUDENT_FRIENDLY_FEEDBACK_SCHEMA,
        "level": "needs_self_revision",
        "main_message": "Close, but not correct yet. Try to fix it yourself first.",
        "good": full.get("good") or [],
        "fix_first": [],
        "hints": _wc1212_hint_for_issues(issues),
        "simple_fix": None,
        "better_version": None,
        "why_your_sentence_needs_work": [],
        "why_better_version_is_better": [],
        "revision_stage": "hint_given",
        "next_step": f"Revise item {item_number} yourself using the hints above, then resubmit just this item.",
    }
    return friendly, new_entry


def _wc1212_item_feedback(self, mission, parsed_response, required_items):
    self._wc1211_scope = _wc1211_upgrade_scope(mission)
    ledger = getattr(self, "_wc1212_item_ledger", None) or {}
    fb = _wc128_item_feedback(self, mission, parsed_response, required_items)
    updated_ledger = dict(ledger)
    for item in fb:
        friendly, ledger_entry = _wc1212_make_student_friendly_item_feedback(item, mission, ledger)
        item["student_friendly_feedback"] = friendly
        if ledger_entry is not None:
            key = f"{mission.get('mission_id')}:{item.get('item_number')}"
            updated_ledger[key] = ledger_entry
    self._wc1212_updated_ledger = updated_ledger
    return fb


def _wc1212_feedback(self, outcome, unit_scores, mission, lines,
                    required_items, completion_gate, item_feedback,
                    mastery_update_allowed):
    bundle = _wc1211_feedback(self, outcome, unit_scores, mission, lines, required_items, completion_gate, item_feedback, mastery_update_allowed)
    sf = bundle.get("student_feedback") or {}
    ledger = getattr(self, "_wc1212_item_ledger", None) or {}
    updated_ledger = dict(getattr(self, "_wc1212_updated_ledger", None) or ledger)
    refreshed = []
    for item in sf.get("item_feedback") or []:
        friendly, ledger_entry = _wc1212_make_student_friendly_item_feedback(item, mission, ledger)
        item["student_friendly_feedback"] = friendly
        if ledger_entry is not None:
            key = f"{mission.get('mission_id')}:{item.get('item_number')}"
            updated_ledger[key] = ledger_entry
        refreshed.append(item)
    sf["item_feedback"] = refreshed
    sf["feedback_quality_policy"] = (
        "V1.2.12 uses a hint-before-answer revision ladder: below-heavy scaffold levels get a "
        "rule-pointing hint on the first wrong attempt (no answer shown), the correction is "
        "revealed on resubmission, and the academic upgrade is only offered once the sentence "
        "is actually correct."
    )
    sf["scaffold_level"] = mission.get("scaffold_level")
    bundle["student_feedback"] = sf

    tf = bundle.get("teacher_feedback") or {}
    tf["scaffold_level"] = mission.get("scaffold_level")
    tf["show_model_on_first_attempt"] = mission.get("show_model_on_first_attempt")
    bundle["teacher_feedback"] = tf

    self._wc1212_updated_ledger = updated_ledger
    return bundle


def terminal_attempt_feedback_text(result):
    """V1.2.12 terminal view: same as V1.2.11, plus scaffold level and the
    hint-only rendering for items still in the self-revision stage."""
    sf = result.get("student_feedback") or {}
    lines = []
    overall = sf.get("simple_summary") or sf.get("overall_comment") or safe_get(result, "feedback.summary")
    if overall:
        lines.append(str(overall))
    if sf.get("scaffold_level"):
        lines.append(f"Scaffold level: {sf.get('scaffold_level')}")
    if sf.get("skill_scope_note"):
        lines.append("Note: " + str(sf.get("skill_scope_note")))
    numbering = sf.get("numbering_feedback")
    if numbering:
        lines.append("Numbering: " + str(numbering))

    for item in sf.get("item_feedback") or []:
        num = item.get("item_number")
        friendly = item.get("student_friendly_feedback") or {}
        if item.get("status") == "missing":
            lines.append("")
            lines.append(f"Item {num} (not written yet)")
            if friendly.get("production_prompt"):
                lines.append("Try this: " + str(friendly.get("production_prompt")))
            if friendly.get("model_sentence_if_stuck"):
                lines.append("If you're stuck: " + str(friendly.get("model_sentence_if_stuck")))
            continue
        if item.get("status") != "submitted":
            continue
        lines.append("")
        lines.append(f"Item {num}")
        lines.append("Your sentence: " + str(item.get("student_sentence")))
        if friendly.get("main_message"):
            lines.append(str(friendly.get("main_message")))
        good = friendly.get("good") or []
        if good:
            lines.append("Good: " + " ".join(str(x) for x in good[:3]))

        if friendly.get("revision_stage") == "hint_given":
            lines.append("Hints (figure it out yourself first):")
            for h in friendly.get("hints") or []:
                lines.append("- " + str(h))
            if friendly.get("next_step"):
                lines.append("Next: " + str(friendly.get("next_step")))
            continue

        fix = friendly.get("fix_first") or []
        if fix:
            lines.append("Fix first:")
            for label in fix[:4]:
                lines.append("- " + str(label))
        if friendly.get("simple_fix"):
            lines.append("Simple fix: " + str(friendly.get("simple_fix")))
        if friendly.get("better_version"):
            lines.append("Better version (optional upgrade): " + str(friendly.get("better_version")))
        why_bad = friendly.get("why_your_sentence_needs_work") or []
        if why_bad:
            lines.append("Why your sentence needs work:")
            for reason in why_bad[:3]:
                lines.append("- " + str(reason))
        why_better = friendly.get("why_better_version_is_better") or []
        if why_better:
            lines.append("Why the better version is better:")
            for reason in why_better[:3]:
                lines.append("- " + str(reason))
        if friendly.get("next_step"):
            lines.append("Next: " + str(friendly.get("next_step")))

    missing = sf.get("missing_items") or []
    if missing:
        lines.append("")
        lines.append("Missing items: " + ", ".join(map(str, missing)))
        if sf.get("try_again_instruction"):
            lines.append(str(sf.get("try_again_instruction")))
    return "\n".join(lines).strip()


_wc1211_evaluate = MissionEvaluator.evaluate


def _wc1212_evaluate(self, mission_payload, response_text, item_ledger=None):
    self._wc1212_item_ledger = item_ledger or {}
    result = _wc1211_evaluate(self, mission_payload, response_text)
    result["schema_version"] = MISSION_RESULT_SCHEMA_V1_2_12
    result["evaluator_version"] = EVALUATOR_VERSION_V1_2_12
    result["item_revision_ledger"] = getattr(self, "_wc1212_updated_ledger", None) or dict(self._wc1212_item_ledger)
    return result


# Install V1.2.12 overrides (MissionBuilder.build already patched above).
MissionEvaluator._item_feedback = _wc1212_item_feedback


MissionEvaluator._feedback = _wc1212_feedback
MissionEvaluator.evaluate = _wc1212_evaluate

# ---------------------------------------------------------------------------
# V1.2.13 -- Pilot: LLM-assisted sentence judgment (opt-in).
#
# Two real-usage sessions in a row broke a different regex standing in for
# grammar checking (the "so \w+er" blocked-pattern, then the finite-verb
# fallback). This pilot replaces ONLY the judgment layer -- has_subject,
# has_finite_verb, is_complete_sentence -- with an LLM call, gated behind
# --llm-judge and OFF by default. Correction text, upgrade text, and hints
# stay exactly as V1.2.12 (hardcoded per rough-input pattern); that is a
# separate, larger step, not this one.
#
# If --llm-judge is not passed, or the API key env var is unset, or the API
# call fails for any reason (network, bad response, timeout), the engine
# falls back to the V1.2.12 regex judgment automatically. This preserves the
# "standalone, no hard external dependency" property the base engine was
# built on: the LLM is an optional upgrade, never a requirement to run.
# ---------------------------------------------------------------------------

import os
import urllib.request
import urllib.error

LLM_JUDGE_SCHEMA = "WRITING_COACH_LLM_SENTENCE_JUDGMENT_V1"
DEFAULT_LLM_PROVIDER = "openai"
DEFAULT_LLM_MODEL = "gpt-4o-mini"
DEFAULT_LLM_API_KEY_ENV = "OPENAI_API_KEY"
LLM_JUDGE_TIMEOUT_SECONDS = 12

_LLM_JUDGE_SYSTEM_PROMPT = (
    "You are a strict, literal English grammar checker for a writing-coaching "
    "tool used by language learners. You will be given ONE sentence written by "
    "a learner. Judge it on structural grammar only -- not style, not academic "
    "register, not word choice sophistication. "
    "Respond with ONLY a single JSON object, no other text, with exactly these "
    "keys: "
    '{"has_subject": bool, "has_finite_verb": bool, "is_complete_sentence": bool, '
    '"issues": [string, ...]}. '
    "\"issues\" should be short, specific phrases naming the grammar problem "
    "(for example \"sentence fragment: no main verb\", \"subject-verb agreement\"), "
    "or an empty list if there are no structural issues. "
    "\"is_complete_sentence\" means the text is a full independent clause with "
    "a subject and a finite verb expressing a complete idea. A noun phrase or "
    "sentence fragment is NOT complete even if it is grammatically well-formed "
    "as a phrase."
)


@dataclass
class SentenceJudgment:
    has_subject: bool
    has_finite_verb: bool
    is_complete_sentence: bool
    issues: List[str]
    source: str  # "regex" | "llm" | "llm_fallback_regex"
    raw: Optional[Dict[str, Any]] = None


def _regex_judge_sentence(sentence: str) -> "SentenceJudgment":
    """Wraps the existing V1.2.12 regex heuristics as a judgment provider.
    This is both the default (when --llm-judge is off) and the fallback
    (when the LLM call fails for any reason)."""
    sentence = sentence or ""
    has_verb = MissionEvaluator._has_plausible_finite_verb(sentence)
    lower = sentence.lower()
    has_subject = any(w in lower.split() for w in MissionEvaluator.SUBJECT_HINTS) or bool(re.match(r"^[A-Z]?[a-z]+\s+", sentence))
    is_complete = has_verb and has_subject and len(words(sentence)) >= 4
    issues: List[str] = []
    if not has_verb:
        issues.append("finite_verb_missing_or_unclear")
    if not has_subject:
        issues.append("subject_missing_or_unclear")
    return SentenceJudgment(
        has_subject=has_subject, has_finite_verb=has_verb, is_complete_sentence=is_complete,
        issues=issues, source="regex",
    )


def _llm_judge_sentence(sentence: str, model: str, api_key: str, timeout: int = LLM_JUDGE_TIMEOUT_SECONDS) -> "SentenceJudgment":
    """Calls the OpenAI Chat Completions API via stdlib urllib (no SDK/pip
    dependency). Uses response_format=json_object so the model is constrained
    to return valid JSON directly, without needing to strip markdown fences."""
    body = json.dumps({
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _LLM_JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": f"Sentence: {sentence}"},
        ],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    choices = payload.get("choices") or []
    text = (choices[0].get("message", {}).get("content", "") if choices else "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    parsed = json.loads(text)
    return SentenceJudgment(
        has_subject=bool(parsed.get("has_subject")),
        has_finite_verb=bool(parsed.get("has_finite_verb")),
        is_complete_sentence=bool(parsed.get("is_complete_sentence")),
        issues=[str(x) for x in (parsed.get("issues") or [])],
        source="llm",
        raw=parsed,
    )


class LLMJudgeConfig:
    """Process-wide toggle + cache for the judgment pilot. Set from CLI args
    in main(), or directly in Python for testing without argparse."""
    enabled: bool = False
    model: str = DEFAULT_LLM_MODEL
    api_key_env: str = DEFAULT_LLM_API_KEY_ENV
    cache: Dict[str, "SentenceJudgment"] = {}
    call_log: List[Dict[str, Any]] = []

    @classmethod
    def reset(cls) -> None:
        cls.cache = {}
        cls.call_log = []


def judge_sentence(sentence: str) -> "SentenceJudgment":
    """Single entry point for grammatical judgment of one line. Regex by
    default; LLM if LLMJudgeConfig.enabled and an API key is available, with
    an unconditional fallback to regex on any failure (missing key, network
    error, malformed response)."""
    sentence = sentence or ""
    if not LLMJudgeConfig.enabled:
        return _regex_judge_sentence(sentence)
    cache_key = sentence.strip().lower()
    if cache_key in LLMJudgeConfig.cache:
        return LLMJudgeConfig.cache[cache_key]
    api_key = os.environ.get(LLMJudgeConfig.api_key_env, "")
    if not api_key:
        LLMJudgeConfig.call_log.append({"sentence": sentence, "status": "no_api_key_fallback_to_regex"})
        judgment = _regex_judge_sentence(sentence)
        judgment.source = "llm_fallback_regex"
        LLMJudgeConfig.cache[cache_key] = judgment
        return judgment
    try:
        judgment = _llm_judge_sentence(sentence, LLMJudgeConfig.model, api_key)
        LLMJudgeConfig.call_log.append({"sentence": sentence, "status": "ok", "source": judgment.source})
    except Exception as e:
        judgment = _regex_judge_sentence(sentence)
        judgment.source = "llm_fallback_regex"
        LLMJudgeConfig.call_log.append({
            "sentence": sentence,
            "status": f"llm_error_fallback_to_regex: {type(e).__name__}: {e}",
        })
    LLMJudgeConfig.cache[cache_key] = judgment
    return judgment


_wc1212_score_unit = MissionEvaluator._score_unit


def _wc1213_score_unit(self, uid: str, lines: List[str], text: str, required_items: int, mission: Dict[str, Any]) -> Tuple[float, str]:
    uid = str(uid or "")
    judge_tag = "llm" if LLMJudgeConfig.enabled else "regex"
    if uid in {"finite_verb_present", "main_verb_present"}:
        vals = [1.0 if judge_sentence(line).has_finite_verb else 0.25 for line in lines]
        return (sum(vals) / len(vals) if vals else 0.0), f"Checks for a finite/main verb in each response line. (judge={judge_tag})"
    if uid in {"subject_present", "clear_subject"}:
        vals = [1.0 if judge_sentence(line).has_subject else 0.4 for line in lines]
        return (sum(vals) / len(vals) if vals else 0.0), f"Checks whether each line has an explicit subject or subject-like noun phrase. (judge={judge_tag})"
    if uid in {"complete_meaning", "complete_recoverable_idea"}:
        vals = []
        for line in lines:
            j = judge_sentence(line)
            if j.is_complete_sentence and len(words(line)) >= 6:
                vals.append(1.0)
            elif len(words(line)) >= 4:
                vals.append(0.45)
            else:
                vals.append(0.2)
        return (sum(vals) / len(vals) if vals else 0.0), f"Checks whether each line expresses a recoverable idea. (judge={judge_tag})"
    return _wc1212_score_unit(self, uid, lines, text, required_items, mission)


_wc1212_sentence_strengths_issues = MissionEvaluator._sentence_strengths_issues


def _wc1213_sentence_strengths_issues(self, sentence: str, rough_input: Optional[str] = None) -> Tuple[List[str], List[str]]:
    strengths, issues = _wc1212_sentence_strengths_issues(self, sentence, rough_input)
    if not LLMJudgeConfig.enabled:
        return strengths, issues
    judgment = judge_sentence(sentence)
    # Drop the old regex-derived verb/subject markers so the list reflects one
    # judgment source, not a mix of two that could disagree with each other.
    strengths = [s for s in strengths if s not in {"finite_verb_present", "subject_present"}]
    issues = [i for i in issues if not _has_issue([i], "finite_verb_missing_or_unclear", "subject_missing_or_unclear")]
    if judgment.has_finite_verb:
        strengths.insert(0, "finite_verb_present")
    else:
        issues.insert(0, "finite_verb_missing_or_unclear: add one clear main verb")
    if judgment.has_subject:
        if "subject_present" not in strengths:
            strengths.insert(0, "subject_present")
    else:
        issues.insert(0, "subject_missing_or_unclear: begin with a clear subject")
    for extra in judgment.issues:
        key = "llm_flagged_issue: " + extra
        if key not in issues:
            issues.append(key)
    return strengths, issues


_wc1212_feedback_for_llm_wrap = MissionEvaluator._feedback


def _wc1213_feedback(self, outcome: str, unit_scores: List[Dict[str, Any]], mission: Dict[str, Any], lines: List[str],
                    required_items: int, completion_gate: Dict[str, Any], item_feedback: List[Dict[str, Any]],
                    mastery_update_allowed: bool) -> Dict[str, Any]:
    bundle = _wc1212_feedback_for_llm_wrap(self, outcome, unit_scores, mission, lines, required_items, completion_gate, item_feedback, mastery_update_allowed)
    debug = bundle.get("debug_evaluation") or {}
    debug["llm_judge_enabled"] = LLMJudgeConfig.enabled
    if LLMJudgeConfig.enabled:
        debug["llm_judge_model"] = LLMJudgeConfig.model
        debug["llm_judge_call_log"] = list(LLMJudgeConfig.call_log)
    bundle["debug_evaluation"] = debug
    return bundle


# ---------------------------------------------------------------------------
# V1.2.13 follow-up: the LLM judge can flag a real problem (spelling error,
# missing conjunction, sentence fragment...) via the generic "issues" list,
# but two real runs showed that diagnosis was getting lost downstream:
#   1. It never counted toward correctness_fix.needed, so the sentence was
#      classified "functional_but_needs_upgrade" and explained with
#      "No major local accuracy error was detected" -- directly contradicting
#      the flagged issue sitting right next to it.
#   2. The student-facing bullet for it read "Llm flagged issue." -- the
#      generic humanizer only knows how to titlecase an unmapped issue's
#      prefix, so the actual detail ("spelling error: childeran") never
#      reached the student.
# This section wires "llm_flagged_issue: <detail>" into the same
# classification/explanation/label pipeline every other issue type already
# goes through, so it's treated as something to fix before the optional
# upgrade, and the specific detail is what gets shown.
#
# What this does NOT do: generate an actual corrected sentence targeting the
# specific problem. minimal_corrected_version / simple_fix are still the
# hardcoded, rough-input-keyed templates from V1.2.8-11 -- they were not
# written with knowledge of arbitrary LLM-flagged problems and can't repair
# one (e.g. they won't specifically fix a misspelled word). Making the shown
# correction actually target what the LLM found is a separate, larger step
# (LLM-assisted correction generation, not just judgment) and is explicitly
# out of scope here.
# ---------------------------------------------------------------------------

def _wc1213_llm_issue_detail(token: Any) -> str:
    s = str(token or "")
    return s.split(":", 1)[1].strip() if ":" in s else s.strip()


_wc1210_sentence_quality_level_for_llm_wrap = MissionEvaluator._sentence_quality_level


def _wc1213_sentence_quality_level(self, sentence: str, issues: List[str]) -> str:
    if _has_issue(issues, "llm_flagged_issue"):
        return "needs_local_fix_then_upgrade"
    return _wc1210_sentence_quality_level_for_llm_wrap(self, sentence, issues)


_wc1211_feedback_layers_for_llm_wrap = MissionEvaluator._feedback_layers


def _wc1213_feedback_layers(self, sentence: str, rough_input: Optional[str], strengths: List[str], issues: List[str]) -> Dict[str, Any]:
    layers = _wc1211_feedback_layers_for_llm_wrap(self, sentence, rough_input, strengths, issues)
    llm_issues = [i for i in (issues or []) if _issue_key(i) == "llm_flagged_issue"]
    if llm_issues:
        cf = layers.get("correctness_fix") or {}
        existing = list(cf.get("issues") or [])
        merged = existing + [i for i in llm_issues if i not in existing]
        cf["needed"] = True
        cf["issues"] = merged
        cf["explanation"] = self._correctness_explanation(merged)
        layers["correctness_fix"] = cf
        order = [x for x in (layers.get("recommended_learning_order") or []) if x != "fix_local_accuracy"]
        layers["recommended_learning_order"] = ["fix_local_accuracy"] + order
    return layers


_wc129_line_explanation_for_llm_wrap = MissionEvaluator._line_explanation


def _wc1213_line_explanation(self, issues: List[str]) -> str:
    llm_issues = [i for i in (issues or []) if _issue_key(i) == "llm_flagged_issue"]
    if llm_issues:
        details = "; ".join(_wc1213_llm_issue_detail(i) for i in llm_issues)
        return f"The Coach's language check flagged a specific problem: {details}. Fix that first, then the academic upgrade is optional."
    return _wc129_line_explanation_for_llm_wrap(self, issues)


_wc129_how_to_improve_for_llm_wrap = MissionEvaluator._how_to_improve


def _wc1213_how_to_improve(self, issues: List[str], sentence: str, rough_input: Optional[str]) -> str:
    llm_issues = [i for i in (issues or []) if _issue_key(i) == "llm_flagged_issue"]
    if llm_issues:
        details = " ".join(f"Fix: {_wc1213_llm_issue_detail(i)}." for i in llm_issues)
        return details
    return _wc129_how_to_improve_for_llm_wrap(self, issues, sentence, rough_input)


_wc1210_friendly_label_for_llm_wrap = _friendly_label


def _wc1213_friendly_label(token: Any) -> str:
    key = _issue_key(token)
    if key == "llm_flagged_issue":
        detail = _wc1213_llm_issue_detail(token)
        if detail:
            return detail[0].upper() + detail[1:] + ("" if detail.endswith((".", "!", "?")) else ".")
        return "The Coach flagged a possible grammar issue in this sentence."
    return _wc1210_friendly_label_for_llm_wrap(token)


_wc1212_evaluate_for_llm_wrap = MissionEvaluator.evaluate


def _wc1213_evaluate(self, mission_payload, response_text, item_ledger=None):
    result = _wc1212_evaluate_for_llm_wrap(self, mission_payload, response_text, item_ledger)
    result["schema_version"] = MISSION_RESULT_SCHEMA_V1_2_13
    result["evaluator_version"] = EVALUATOR_VERSION_V1_2_13
    return result


# Install V1.2.13 overrides. The judgment/feedback overrides are opt-in
# no-ops unless LLMJudgeConfig.enabled is set (from --llm-judge in main(),
# or directly for testing). The version-stamp override always applies,
# since it reflects which code ran, not which optional feature fired.
# The llm_flagged_issue classification/label overrides below are also
# no-ops unless an "llm_flagged_issue: ..." entry is actually present in a
# given sentence's issues -- which only happens when --llm-judge is on and
# a real LLM call returned one.
MissionEvaluator._score_unit = _wc1213_score_unit
MissionEvaluator._sentence_strengths_issues = _wc1213_sentence_strengths_issues
MissionEvaluator._feedback = _wc1213_feedback
MissionEvaluator.evaluate = _wc1213_evaluate
MissionEvaluator._sentence_quality_level = _wc1213_sentence_quality_level
MissionEvaluator._feedback_layers = _wc1213_feedback_layers
MissionEvaluator._line_explanation = _wc1213_line_explanation
MissionEvaluator._how_to_improve = _wc1213_how_to_improve
_friendly_label = _wc1213_friendly_label


# ---------------------------------------------------------------------------
# V1.2.14 -- Attempt cap + auto-resolve for the hint-ladder, plus a
# prerequisite-gap signal.
#
# Problem: the V1.2.12 hint-ladder had no terminal state. Once an item had
# one failed attempt, every subsequent resubmission just re-revealed the
# same correction forever -- attempts_with_issues incremented with no
# change in behavior. If a student never produced something the evaluator
# accepted (including because the evaluator itself was wrong -- we proved
# both regex and LLM judgment have real false positives/negatives this
# session), there was no way out of the loop.
#
# Fix: cap attempts_with_issues at _WC1214_MAX_ATTEMPTS_WITH_ISSUES. Once
# hit, stop looping -- show the model sentence outright, mark the item
# resolved, and explicitly do NOT credit it toward mastery
# (mastery_update_allowed semantics are already separate at the mission
# level; this only changes what the student sees for this one item).
#
# Also: when an item caps out AND its issues also include a foundational
# problem (missing subject/verb, not just the move's own target skill),
# that is a signal the real gap may be a prerequisite skill, not the skill
# this mission is training. Surface that as a note, using a small static
# prerequisite table seeded from writing_competency_ontology_v3.json's
# "dependencies" field (not loaded at runtime -- keeps the engine
# standalone; this is a curated snapshot, not a live ontology dependency).
# This does NOT auto-reroute the skill queue -- it only surfaces the signal
# for now. Auto-rerouting is a separate, larger step.
# ---------------------------------------------------------------------------

_WC1214_MAX_ATTEMPTS_WITH_ISSUES = 3

_WC1214_SKILL_PREREQUISITES = {
    "simple_sentence_construction": ["noun_form_control", "agreement_control", "tense_control"],
    "arg_reason_generation": ["arg_claim_generation", "generate_explanations"],
    "arg_claim_generation": ["identify_task_type", "identify_purpose"],
    "arg_claim_specificity": ["arg_claim_generation"],
    "paragraph_planning": ["identify_required_components", "thesis_construction"],
    "topic_sentence_control": ["thesis_construction", "paragraph_planning", "claim_construction"],
    "lexical_precision": ["semantic_compatibility", "topic_vocabulary_control"],
    "transition_control": ["logical_sequencing"],
}

_WC1214_FOUNDATIONAL_ISSUE_PREFIXES = (
    "finite_verb_missing_or_unclear",
    "subject_missing_or_unclear",
    "sentence_too_short_or_underdeveloped",
)


def _wc1214_make_student_friendly_item_feedback(item, mission, ledger):
    """Returns (friendly_feedback_dict, new_ledger_entry_or_None). Wraps the
    V1.2.12 hint-ladder decision function, adding an attempt cap before
    delegating to it -- everything else (hint_given / correction_revealed /
    upgrade_offered) is unchanged."""
    if item.get("status") == "submitted":
        issues = item.get("issues") or []
        has_real_issues = any(not _has_issue([i], "higher_band_upgrade") for i in issues)
        mission_id = mission.get("mission_id")
        item_number = item.get("item_number")
        key = f"{mission_id}:{item_number}"
        prior = ledger.get(key) or {"attempts_with_issues": 0, "corrected_confirmed": False}
        if has_real_issues and prior.get("attempts_with_issues", 0) >= _WC1214_MAX_ATTEMPTS_WITH_ISSUES:
            model = (
                item.get("upgraded_academic_version")
                or item.get("minimal_corrected_version")
                or item.get("suggested_revision")
            )
            new_entry = {
                "attempts_with_issues": prior["attempts_with_issues"] + 1,
                "corrected_confirmed": False,
                "capped_out": True,
            }
            friendly = {
                "schema_version": STUDENT_FRIENDLY_FEEDBACK_SCHEMA,
                "level": "capped_model_shown",
                "main_message": (
                    "You've tried this item several times. Here is a model sentence -- read it, "
                    "then move on. This item will not count as mastered yet, but you can practice "
                    "it again another day."
                ),
                "good": [],
                "fix_first": [],
                "simple_fix": None,
                "better_version": model,
                "why_your_sentence_needs_work": [],
                "why_better_version_is_better": [],
                "next_step": "Move to the next item, or come back to this one in a future session.",
                "beyond_this_mission": None,
                "revision_stage": "capped_model_shown",
            }
            if any(_has_issue([i], *_WC1214_FOUNDATIONAL_ISSUE_PREFIXES) for i in issues):
                primary = safe_get(mission, "selected_move.primary_microskill")
                prereqs = _WC1214_SKILL_PREREQUISITES.get(primary)
                if prereqs:
                    friendly["possible_prerequisite_gap"] = (
                        f"This item also shows a basic sentence problem (missing subject or verb), not just "
                        f"the target skill ({primary}). That can mean the real gap is in an earlier skill -- "
                        f"{', '.join(prereqs)} -- worth practicing before more '{primary}' missions."
                    )
            return friendly, new_entry
    return _wc1212_make_student_friendly_item_feedback(item, mission, ledger)


# NOTE: _item_feedback is NOT the right place to apply the cap. _feedback
# (called afterward, during bundle assembly) unconditionally recomputes and
# overwrites student_friendly_feedback for every item using the V1.2.12
# function by name -- so a cap applied only in _item_feedback gets silently
# stomped by the time the result is returned. The cap has to be applied in
# the same place that recomputation happens: _feedback.

_wc1213_feedback_for_cap_wrap = MissionEvaluator._feedback


def _wc1214_feedback(self, outcome: str, unit_scores: List[Dict[str, Any]], mission: Dict[str, Any], lines: List[str],
                    required_items: int, completion_gate: Dict[str, Any], item_feedback: List[Dict[str, Any]],
                    mastery_update_allowed: bool) -> Dict[str, Any]:
    bundle = _wc1213_feedback_for_cap_wrap(self, outcome, unit_scores, mission, lines, required_items, completion_gate, item_feedback, mastery_update_allowed)
    sf = bundle.get("student_feedback") or {}
    # Use the ORIGINAL incoming ledger (pre-this-turn), not self._wc1212_updated_ledger --
    # that was already written by the V1.2.12 pass inside the wrapped _feedback call above
    # and does not reflect the cap decision. Recomputing from the original prior state and
    # overwriting self._wc1212_updated_ledger here is what makes the cap take effect.
    original_ledger = getattr(self, "_wc1212_item_ledger", None) or {}
    updated_ledger = dict(original_ledger)
    refreshed = []
    for item in sf.get("item_feedback") or []:
        friendly, ledger_entry = _wc1214_make_student_friendly_item_feedback(item, mission, original_ledger)
        item["student_friendly_feedback"] = friendly
        if ledger_entry is not None:
            key = f"{mission.get('mission_id')}:{item.get('item_number')}"
            updated_ledger[key] = ledger_entry
        refreshed.append(item)
    sf["item_feedback"] = refreshed
    bundle["student_feedback"] = sf
    self._wc1212_updated_ledger = updated_ledger
    return bundle


# Install V1.2.14 override. Pure addition: only changes behavior once an
# item has hit the attempt cap; every other case delegates unchanged to
# V1.2.12/13 behavior via the wrapped functions above.
MissionEvaluator._feedback = _wc1214_feedback

MISSION_RESULT_SCHEMA_V1_2_14 = "WRITING_COACH_MISSION_RESULT_V1_2_14"
EVALUATOR_VERSION_V1_2_14 = "writing_coach_v1_2_14_attempt_cap_evaluator"

_wc1213_evaluate_for_cap_wrap = MissionEvaluator.evaluate


def _wc1214_evaluate(self, mission_payload, response_text, item_ledger=None):
    result = _wc1213_evaluate_for_cap_wrap(self, mission_payload, response_text, item_ledger)
    result["schema_version"] = MISSION_RESULT_SCHEMA_V1_2_14
    result["evaluator_version"] = EVALUATOR_VERSION_V1_2_14
    return result


MissionEvaluator.evaluate = _wc1214_evaluate


# ---------------------------------------------------------------------------
# V1.2.15 -- Register/formality judge, separate from the grammar judge.
#
# The grammar judge (V1.2.13) was deliberately scoped to structural grammar
# only -- its own system prompt says "not style, not academic register, not
# word choice sophistication." Real use confirmed the gap this leaves: on
# "With fewer workers we have slower economy," the grammar judge correctly
# caught the missing article and said nothing about "we have" -- a real,
# common IELTS register weakness (first-person framing in formal writing).
#
# Built as a SEPARATE LLM call with its own prompt and its own issue tag
# (llm_register_issue, distinct from llm_flagged_issue) rather than folding
# it into the grammar judge -- same discipline as everything else in this
# engine's LLM work: narrow, single-purpose, independently testable.
#
# Design decision (documented, not hidden): register violations are wired
# through the SAME correctness-tier pipeline as grammar issues (blocks the
# optional upgrade, same as a grammar error would), not treated as an
# upgrade-tier nicety, because IELTS register slips are a real band-limiting
# weakness. That is a pedagogy call, not a fact -- worth revisiting if it
# doesn't match real grading practice.
#
# What it does NOT do: generate the corrected sentence. suggested_reframe is
# a DIRECTION ("rephrase using an impersonal subject"), not a rewritten
# sentence -- the student still does the rewriting. Same Socratic-hint
# discipline as the rest of the hint-ladder.
#
# No deterministic fallback exists for register (unlike grammar, which falls
# back to regex). If disabled, no API key, or the call fails, this silently
# assumes register_appropriate=True and flags nothing -- a false negative
# (missed register issue), never a false positive. Consistent with "never
# invent a signal that wasn't actually checked."
# ---------------------------------------------------------------------------

DEFAULT_REGISTER_MODEL = "gpt-4o-mini"

_LLM_REGISTER_SYSTEM_PROMPT = (
    "You are an academic-register checker for a writing-coaching tool used by "
    "IELTS/English learners (roughly A2-B1 to B2). You will be given ONE "
    "sentence written in response to an academic writing task. Judge ONLY "
    "whether the register is appropriate for formal academic writing -- "
    "nothing else.\n\n"
    "Flag as NOT appropriate:\n"
    "- First- or second-person framing for general claims (\"I think\", "
    "\"we have\", \"you can see\", \"we need\")\n"
    "- Contractions (don't, can't, it's, won't)\n"
    "- Casual sentence openers or connectors (So, And, But, Also, Well at "
    "the start of a sentence)\n"
    "- Colloquial or spoken phrasing (a lot of, get better, kids, stuff)\n"
    "- Vague informal intensifiers (really, very, so + adjective used "
    "casually)\n\n"
    "Do NOT flag:\n"
    "- Grammar errors (checked separately)\n"
    "- Whether the content is specific enough (checked separately)\n"
    "- Simple but still register-appropriate vocabulary -- a short formal "
    "sentence is fine\n\n"
    "Respond with ONLY a single JSON object, no other text: "
    '{"register_appropriate": bool, "issues": [string, ...], '
    '"suggested_reframe": string or null}. '
    "\"issues\" should name the specific informal pattern found (for example "
    "\"first-person framing: 'we have'\"). \"suggested_reframe\" should give "
    "a brief DIRECTION for how to reframe, not a full rewritten sentence -- "
    "for example \"Rephrase using an impersonal subject, such as 'This "
    "results in...' or 'Fewer workers lead to...'\" -- so the student does "
    "the rewriting, not the model."
)


@dataclass
class RegisterJudgment:
    register_appropriate: bool
    issues: List[str]
    suggested_reframe: Optional[str]
    source: str  # "llm" | "llm_fallback_skip" | "disabled"
    raw: Optional[Dict[str, Any]] = None


class RegisterJudgeConfig:
    """Process-wide toggle + cache for the register judge, mirroring
    LLMJudgeConfig but kept fully separate so grammar and register judging
    can be enabled/disabled/tested independently of each other."""
    enabled: bool = False
    model: str = DEFAULT_REGISTER_MODEL
    api_key_env: str = DEFAULT_LLM_API_KEY_ENV
    cache: Dict[str, "RegisterJudgment"] = {}
    call_log: List[Dict[str, Any]] = []

    @classmethod
    def reset(cls) -> None:
        cls.cache = {}
        cls.call_log = []


def _llm_judge_register(sentence: str, model: str, api_key: str, timeout: int = LLM_JUDGE_TIMEOUT_SECONDS) -> "RegisterJudgment":
    body = json.dumps({
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _LLM_REGISTER_SYSTEM_PROMPT},
            {"role": "user", "content": f"Sentence: {sentence}"},
        ],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    choices = payload.get("choices") or []
    text = (choices[0].get("message", {}).get("content", "") if choices else "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    parsed = json.loads(text)
    return RegisterJudgment(
        register_appropriate=bool(parsed.get("register_appropriate", True)),
        issues=[str(x) for x in (parsed.get("issues") or [])],
        suggested_reframe=parsed.get("suggested_reframe"),
        source="llm",
        raw=parsed,
    )


def judge_register(sentence: str) -> "RegisterJudgment":
    sentence = sentence or ""
    if not RegisterJudgeConfig.enabled:
        return RegisterJudgment(register_appropriate=True, issues=[], suggested_reframe=None, source="disabled")
    cache_key = sentence.strip().lower()
    if cache_key in RegisterJudgeConfig.cache:
        return RegisterJudgeConfig.cache[cache_key]
    api_key = os.environ.get(RegisterJudgeConfig.api_key_env, "")
    if not api_key:
        RegisterJudgeConfig.call_log.append({"sentence": sentence, "status": "no_api_key_skip"})
        judgment = RegisterJudgment(register_appropriate=True, issues=[], suggested_reframe=None, source="llm_fallback_skip")
        RegisterJudgeConfig.cache[cache_key] = judgment
        return judgment
    try:
        judgment = _llm_judge_register(sentence, RegisterJudgeConfig.model, api_key)
        RegisterJudgeConfig.call_log.append({"sentence": sentence, "status": "ok"})
    except Exception as e:
        judgment = RegisterJudgment(register_appropriate=True, issues=[], suggested_reframe=None, source="llm_fallback_skip")
        RegisterJudgeConfig.call_log.append({
            "sentence": sentence,
            "status": f"llm_error_skip: {type(e).__name__}: {e}",
        })
    RegisterJudgeConfig.cache[cache_key] = judgment
    return judgment


_wc1214_sentence_strengths_issues_for_register_wrap = MissionEvaluator._sentence_strengths_issues


def _wc1215_sentence_strengths_issues(self, sentence: str, rough_input: Optional[str] = None) -> Tuple[List[str], List[str]]:
    strengths, issues = _wc1214_sentence_strengths_issues_for_register_wrap(self, sentence, rough_input)
    if not RegisterJudgeConfig.enabled:
        return strengths, issues
    reg = judge_register(sentence)
    if not reg.register_appropriate:
        for extra in reg.issues:
            text = extra
            if reg.suggested_reframe:
                text = f"{extra} -- try: {reg.suggested_reframe}"
            key = "llm_register_issue: " + text
            if key not in issues:
                issues.append(key)
    return strengths, issues


_wc1214_sentence_quality_level_for_register_wrap = MissionEvaluator._sentence_quality_level


def _wc1215_sentence_quality_level(self, sentence: str, issues: List[str]) -> str:
    if _has_issue(issues, "llm_register_issue"):
        return "needs_local_fix_then_upgrade"
    return _wc1214_sentence_quality_level_for_register_wrap(self, sentence, issues)


_wc1214_feedback_layers_for_register_wrap = MissionEvaluator._feedback_layers


def _wc1215_feedback_layers(self, sentence: str, rough_input: Optional[str], strengths: List[str], issues: List[str]) -> Dict[str, Any]:
    layers = _wc1214_feedback_layers_for_register_wrap(self, sentence, rough_input, strengths, issues)
    register_issues = [i for i in (issues or []) if _issue_key(i) == "llm_register_issue"]
    if register_issues:
        cf = layers.get("correctness_fix") or {}
        existing = list(cf.get("issues") or [])
        merged = existing + [i for i in register_issues if i not in existing]
        cf["needed"] = True
        cf["issues"] = merged
        cf["explanation"] = self._correctness_explanation(merged)
        layers["correctness_fix"] = cf
        order = [x for x in (layers.get("recommended_learning_order") or []) if x != "fix_local_accuracy"]
        layers["recommended_learning_order"] = ["fix_local_accuracy"] + order
    return layers


_wc1214_line_explanation_for_register_wrap = MissionEvaluator._line_explanation


def _wc1215_line_explanation(self, issues: List[str]) -> str:
    register_issues = [i for i in (issues or []) if _issue_key(i) == "llm_register_issue"]
    if register_issues:
        details = "; ".join(_wc1213_llm_issue_detail(i) for i in register_issues)
        return f"The Coach's register check flagged a formality problem: {details}. Academic writing avoids this -- fix it before the optional upgrade."
    return _wc1214_line_explanation_for_register_wrap(self, issues)


_wc1214_how_to_improve_for_register_wrap = MissionEvaluator._how_to_improve


def _wc1215_how_to_improve(self, issues: List[str], sentence: str, rough_input: Optional[str]) -> str:
    register_issues = [i for i in (issues or []) if _issue_key(i) == "llm_register_issue"]
    if register_issues:
        details = " ".join(f"Fix: {_wc1213_llm_issue_detail(i)}." for i in register_issues)
        return details
    return _wc1214_how_to_improve_for_register_wrap(self, issues, sentence, rough_input)


_wc1214_friendly_label_for_register_wrap = _friendly_label


def _wc1215_friendly_label(token: Any) -> str:
    key = _issue_key(token)
    if key == "llm_register_issue":
        detail = _wc1213_llm_issue_detail(token)
        if detail:
            return detail[0].upper() + detail[1:] + ("" if detail.endswith((".", "!", "?")) else ".")
        return "The Coach flagged a possible register/formality issue in this sentence."
    return _wc1214_friendly_label_for_register_wrap(token)


# _wc1212_hint_for_issues is called by direct module-level name (not via
# self.<method>) inside _wc1212_make_student_friendly_item_feedback, so
# reassigning the module-level name here is what makes the override take
# effect -- same pattern already proven for _friendly_label in V1.2.13.
_wc1212_hint_for_issues_before_register = _wc1212_hint_for_issues


def _wc1215_hint_for_issues(issues):
    hints = _wc1212_hint_for_issues_before_register(issues)
    register_issues = [i for i in (issues or []) if _issue_key(i) == "llm_register_issue"]
    for i in register_issues:
        detail = _wc1213_llm_issue_detail(i)
        hint = (
            f"Your sentence uses informal, conversational phrasing ({detail.split(' -- try:')[0]}). "
            "How could you rephrase it more impersonally, the way academic writing does?"
        )
        if hint not in hints:
            hints.insert(0, hint)
    return hints[:3]


_wc1212_hint_for_issues = _wc1215_hint_for_issues


_wc1214_feedback_for_register_wrap = MissionEvaluator._feedback


def _wc1215_feedback(self, outcome: str, unit_scores: List[Dict[str, Any]], mission: Dict[str, Any], lines: List[str],
                    required_items: int, completion_gate: Dict[str, Any], item_feedback: List[Dict[str, Any]],
                    mastery_update_allowed: bool) -> Dict[str, Any]:
    bundle = _wc1214_feedback_for_register_wrap(self, outcome, unit_scores, mission, lines, required_items, completion_gate, item_feedback, mastery_update_allowed)
    debug = bundle.get("debug_evaluation") or {}
    debug["register_judge_enabled"] = RegisterJudgeConfig.enabled
    if RegisterJudgeConfig.enabled:
        debug["register_judge_model"] = RegisterJudgeConfig.model
        debug["register_judge_call_log"] = list(RegisterJudgeConfig.call_log)
    bundle["debug_evaluation"] = debug
    return bundle


MISSION_RESULT_SCHEMA_V1_2_15 = "WRITING_COACH_MISSION_RESULT_V1_2_15"
EVALUATOR_VERSION_V1_2_15 = "writing_coach_v1_2_15_register_judge_evaluator"

_wc1214_evaluate_for_register_wrap = MissionEvaluator.evaluate


def _wc1215_evaluate(self, mission_payload, response_text, item_ledger=None):
    result = _wc1214_evaluate_for_register_wrap(self, mission_payload, response_text, item_ledger)
    result["schema_version"] = MISSION_RESULT_SCHEMA_V1_2_15
    result["evaluator_version"] = EVALUATOR_VERSION_V1_2_15
    return result


# Install V1.2.15 overrides, all opt-in no-ops unless RegisterJudgeConfig.enabled.
MissionEvaluator._sentence_strengths_issues = _wc1215_sentence_strengths_issues
MissionEvaluator._sentence_quality_level = _wc1215_sentence_quality_level
MissionEvaluator._feedback_layers = _wc1215_feedback_layers
MissionEvaluator._line_explanation = _wc1215_line_explanation
MissionEvaluator._how_to_improve = _wc1215_how_to_improve
_friendly_label = _wc1215_friendly_label
MissionEvaluator._feedback = _wc1215_feedback
MissionEvaluator.evaluate = _wc1215_evaluate


# ---------------------------------------------------------------------------
# V1.2.16 -- LLM-assisted correction generation.
#
# Closes the gap documented since Update 5 (V1.2.13) and restated in the
# V1.2.15 spec: every judge added this session (grammar, register) has been
# able to correctly NAME a problem, but minimal_corrected_version / simple_fix
# were still produced by the V1.2.8-11 hardcoded, rough-input-keyword-matched
# templates -- completely blind to what any judge actually found. Confirmed
# broken on 5 separate real examples across this session (a correlative-
# comparative sentence, "childeran" spelling, "slower economy" missing
# article, and most recently "help families to childcare" wrong preposition
# -- the shown "fix" was the unchanged, still-wrong input every time).
#
# Design constraint, restated because it matters: this is GENERATION, not
# JUDGMENT -- a real departure from the principle held everywhere else in
# this engine ("the LLM produces labels; it never decides pass/fail"). To
# keep it from becoming a second, uncontrolled judge:
#   - The generator is NOT told to find problems. It is given the sentence
#     AND the exact issue text already produced by the existing judges, and
#     told to fix ONLY those, changing as little else as possible.
#   - It cannot touch is_acceptable_for_target_move, sentence_quality_level,
#     scoring, or the issues list itself -- those remain WC's own layer,
#     computed before this ever runs. This function only overwrites the
#     shown correction text.
#   - Only triggers for issue types the old hardcoded templates were never
#     written to handle (llm_flagged_issue, llm_register_issue). Rule-based
#     issues (article_determiner_error, etc.) already have working template
#     repairs -- left untouched, to avoid risking behavior that already works.
#   - If disabled, no API key, or the call fails: fall back to the existing
#     V1.2.8-15 behavior unchanged (the already-known, already-documented
#     gap) rather than fabricating a new, possibly-wrong correction. Same
#     "silent false negative, never a false positive" discipline as the
#     register judge.
#   - Zero behavior change unless --llm-correction-generator is on.
# ---------------------------------------------------------------------------

DEFAULT_CORRECTION_MODEL = "gpt-4o-mini"

_LLM_CORRECTION_SYSTEM_PROMPT = (
    "You are a minimal-correction generator for a writing-coaching tool used by "
    "IELTS/English learners. You will be given ONE student sentence and a list "
    "of specific problems that have ALREADY been identified by other checks. "
    "Your only job is to rewrite the sentence to fix EXACTLY those problems -- "
    "nothing else.\n\n"
    "Rules:\n"
    "- Preserve the student's original meaning, content, and sentence structure "
    "as much as possible. Do not add new ideas, examples, or clauses.\n"
    "- Do not fix anything that was not listed as a problem, even if you notice "
    "something else wrong.\n"
    "- Keep the same register/complexity level as the input unless a listed "
    "problem is specifically about register.\n"
    "- The result must be a single, complete, grammatical sentence.\n\n"
    "Respond with ONLY a single JSON object, no other text: "
    '{"minimal_corrected_version": string}.'
)


@dataclass
class CorrectionGeneration:
    minimal_corrected_version: Optional[str]
    source: str  # "llm" | "llm_fallback_skip" | "disabled"
    raw: Optional[Dict[str, Any]] = None


class CorrectionGeneratorConfig:
    """Process-wide toggle + cache, mirroring LLMJudgeConfig/RegisterJudgeConfig
    but kept fully separate: this is a generation call, not a judgment call,
    and must remain independently testable/disable-able from both judges."""
    enabled: bool = False
    model: str = DEFAULT_CORRECTION_MODEL
    api_key_env: str = DEFAULT_LLM_API_KEY_ENV
    cache: Dict[str, "CorrectionGeneration"] = {}
    call_log: List[Dict[str, Any]] = []

    @classmethod
    def reset(cls) -> None:
        cls.cache = {}
        cls.call_log = []


def _llm_generate_correction(sentence: str, issue_details: List[str], model: str, api_key: str, timeout: int = LLM_JUDGE_TIMEOUT_SECONDS) -> "CorrectionGeneration":
    issues_text = "\n".join(f"- {d}" for d in issue_details)
    body = json.dumps({
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _LLM_CORRECTION_SYSTEM_PROMPT},
            {"role": "user", "content": f"Sentence: {sentence}\n\nProblems to fix:\n{issues_text}"},
        ],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    choices = payload.get("choices") or []
    text = (choices[0].get("message", {}).get("content", "") if choices else "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    parsed = json.loads(text)
    fixed = parsed.get("minimal_corrected_version")
    return CorrectionGeneration(
        minimal_corrected_version=str(fixed).strip() if fixed else None,
        source="llm",
        raw=parsed,
    )


def generate_correction(sentence: str, issue_details: List[str]) -> "CorrectionGeneration":
    sentence = sentence or ""
    if not CorrectionGeneratorConfig.enabled or not issue_details:
        return CorrectionGeneration(minimal_corrected_version=None, source="disabled")
    cache_key = sentence.strip().lower() + "||" + "||".join(sorted(issue_details))
    if cache_key in CorrectionGeneratorConfig.cache:
        return CorrectionGeneratorConfig.cache[cache_key]
    api_key = os.environ.get(CorrectionGeneratorConfig.api_key_env, "")
    if not api_key:
        CorrectionGeneratorConfig.call_log.append({"sentence": sentence, "status": "no_api_key_skip"})
        result = CorrectionGeneration(minimal_corrected_version=None, source="llm_fallback_skip")
        CorrectionGeneratorConfig.cache[cache_key] = result
        return result
    try:
        result = _llm_generate_correction(sentence, issue_details, CorrectionGeneratorConfig.model, api_key)
        CorrectionGeneratorConfig.call_log.append({"sentence": sentence, "status": "ok"})
    except Exception as e:
        result = CorrectionGeneration(minimal_corrected_version=None, source="llm_fallback_skip")
        CorrectionGeneratorConfig.call_log.append({
            "sentence": sentence,
            "status": f"llm_error_skip: {type(e).__name__}: {e}",
        })
    CorrectionGeneratorConfig.cache[cache_key] = result
    return result


_wc1215_feedback_layers_for_correction_wrap = MissionEvaluator._feedback_layers


def _wc1216_feedback_layers(self, sentence: str, rough_input: Optional[str], strengths: List[str], issues: List[str]) -> Dict[str, Any]:
    layers = _wc1215_feedback_layers_for_correction_wrap(self, sentence, rough_input, strengths, issues)
    if not CorrectionGeneratorConfig.enabled:
        return layers
    cf = layers.get("correctness_fix") or {}
    targetable = [i for i in (cf.get("issues") or []) if _issue_key(i) in ("llm_flagged_issue", "llm_register_issue")]
    if not targetable:
        return layers
    detail_texts = [_wc1213_llm_issue_detail(i) for i in targetable]
    result = generate_correction(sentence, detail_texts)
    if result.minimal_corrected_version:
        old_minimal = cf.get("minimal_corrected_version")
        cf["minimal_corrected_version"] = result.minimal_corrected_version
        cf["generated_by"] = "llm_correction_generator"
        layers["correctness_fix"] = cf
        nat = layers.get("naturalness_fix") or {}
        if nat.get("natural_version") == old_minimal:
            nat["natural_version"] = result.minimal_corrected_version
            layers["naturalness_fix"] = nat
    return layers


MISSION_RESULT_SCHEMA_V1_2_16 = "WRITING_COACH_MISSION_RESULT_V1_2_16"
EVALUATOR_VERSION_V1_2_16 = "writing_coach_v1_2_16_correction_generator_evaluator"

_wc1215_evaluate_for_correction_wrap = MissionEvaluator.evaluate


def _wc1216_evaluate(self, mission_payload, response_text, item_ledger=None):
    result = _wc1215_evaluate_for_correction_wrap(self, mission_payload, response_text, item_ledger)
    result["schema_version"] = MISSION_RESULT_SCHEMA_V1_2_16
    result["evaluator_version"] = EVALUATOR_VERSION_V1_2_16
    return result


_wc1215_feedback_for_correction_wrap = MissionEvaluator._feedback


def _wc1216_feedback(self, outcome: str, unit_scores: List[Dict[str, Any]], mission: Dict[str, Any], lines: List[str],
                    required_items: int, completion_gate: Dict[str, Any], item_feedback: List[Dict[str, Any]],
                    mastery_update_allowed: bool) -> Dict[str, Any]:
    bundle = _wc1215_feedback_for_correction_wrap(self, outcome, unit_scores, mission, lines, required_items, completion_gate, item_feedback, mastery_update_allowed)
    debug = bundle.get("debug_evaluation") or {}
    debug["correction_generator_enabled"] = CorrectionGeneratorConfig.enabled
    if CorrectionGeneratorConfig.enabled:
        debug["correction_generator_model"] = CorrectionGeneratorConfig.model
        debug["correction_generator_call_log"] = list(CorrectionGeneratorConfig.call_log)
    bundle["debug_evaluation"] = debug
    return bundle


# Install V1.2.16 overrides, all opt-in no-ops unless CorrectionGeneratorConfig.enabled.
MissionEvaluator._feedback_layers = _wc1216_feedback_layers
MissionEvaluator._feedback = _wc1216_feedback
MissionEvaluator.evaluate = _wc1216_evaluate


# ---------------------------------------------------------------------------
# V1.2.17 -- Genuine, universal academic-upgrade generation.
#
# The user identified this as the actual point of the "upgrade" feature: it's
# the hint that shows a student how to elevate their sentence, which is the
# real coaching value this tool is supposed to deliver.
#
# The problem, confirmed by direct testing before writing this: for the 5
# rough_input topics this demo mission happens to use (healthcare, retire,
# grandparents, traditions, experience), _wc1211_upgraded_revision's
# hardcoded keyword-matched templates produce a real upgrade. For ANY other
# topic, none of the keyword branches match, and it silently falls through
# to self._minimal_correction -- meaning upgraded_academic_version is
# byte-identical to minimal_corrected_version. No genuine academic upgrade
# is generated at all outside those 5 topics. This is the same "sentence-
# specific patterns, not a universal writing coach" problem the user flagged
# earlier this session about a different part of the engine.
#
# This patch replaces that per-topic template, for any item where an upgrade
# is warranted (layers["higher_band_upgrade"]["needed"]), with a real LLM
# rewrite constrained the same way generation is constrained everywhere else
# in this engine:
#   - It is given the (already-corrected, if V1.2.16 ran) sentence and the
#     student's original rough notes, and told to elevate vocabulary/
#     structure/precision of the SAME idea -- not add new content.
#   - It must respect the mission's declared upgrade scope
#     (_wc1211_upgrade_scope): if the move doesn't declare a complex-clause
#     skill, the prompt instructs single-clause only. Because LLM instruction-
#     following isn't guaranteed, a deterministic regex safety net
#     (_WC1211_CLAUSE_MARKERS, already used for the old hardcoded templates)
#     re-checks the output and falls back to the known-safe corrected
#     sentence if the model added a clause anyway -- the LLM output is kept
#     as a labeled "preview_beyond_scope", same UX as V1.2.11 used for this.
#   - If disabled, no key, or the call fails: falls back to the existing
#     V1.2.8-16 template behavior unchanged (topic-hardcoded, but a known,
#     already-documented limitation) -- never a fabricated risky rewrite.
#
# Ordering note, applying a lesson already paid for once in V1.2.14: the
# why_better explanation text can NOT be wired in at the _item_feedback
# level. _wc1212_feedback (the _feedback-level override) recomputes each
# item's student_friendly_feedback a SECOND time from scratch by calling
# _wc1212_make_student_friendly_item_feedback again -- so anything set only
# during _item_feedback gets silently discarded by the time the final bundle
# is built. Verified this concretely before relying on it (see spec). The
# fix: patch why_better_version_is_better at the _feedback level, on
# bundle["student_feedback"]["item_feedback"], after every earlier override
# in the chain has already run.
# ---------------------------------------------------------------------------

DEFAULT_UPGRADE_MODEL = "gpt-4o-mini"

_LLM_UPGRADE_SYSTEM_PROMPT_BASE = (
    "You are an academic-writing upgrade coach for IELTS/English learners "
    "(roughly A2-B1 to B2). You will be given ONE grammatically acceptable "
    "sentence expressing a student's idea, plus the student's original rough "
    "notes it came from. Rewrite the sentence as a stronger, more academic "
    "version of the SAME idea: more precise vocabulary, natural collocations, "
    "a clearer relationship between the parts of the idea, and no vague "
    "filler words.\n\n"
    "Rules:\n"
    "- Do not add new information, examples, or claims beyond the student's "
    "original idea.\n"
    "- Do not change what the sentence is about.\n"
    "- The result must be a single, complete, grammatical, natural-sounding "
    "sentence.\n"
)

_LLM_UPGRADE_SCOPE_SINGLE_CLAUSE = (
    "- IMPORTANT: Keep the result to a single clause. Do not add a second "
    "clause using words like because, although, which, while, whereas, "
    "since, or so that. Gerund/prepositional phrases such as 'by doing X' or "
    "'to do X' are fine and do not count as a second clause.\n"
)

_LLM_UPGRADE_SCOPE_ALLOW_COMPLEX = (
    "- You may use a second clause (for example with because, which, or "
    "while) if it makes the relationship clearer, but only if it stays "
    "natural and grammatically correct.\n"
)

_LLM_UPGRADE_RESPONSE_INSTRUCTION = (
    "\nRespond with ONLY a single JSON object, no other text: "
    '{"upgraded_academic_version": string, "why_better": string}. '
    "\"why_better\" is ONE short, encouraging sentence aimed at the student "
    "explaining what changed and why it sounds more academic."
)


@dataclass
class UpgradeGeneration:
    upgraded_academic_version: Optional[str]
    why_better: Optional[str]
    source: str  # "llm" | "llm_fallback_skip" | "disabled"
    raw: Optional[Dict[str, Any]] = None


class UpgradeGeneratorConfig:
    """Process-wide toggle + cache for genuine (non-topic-hardcoded) academic
    upgrade generation. Kept separate from CorrectionGeneratorConfig -- fixing
    a grammar error and elevating an already-correct sentence's style are
    different jobs, and each should be independently enable/disable/testable,
    same reasoning as splitting the grammar and register judges."""
    enabled: bool = False
    model: str = DEFAULT_UPGRADE_MODEL
    api_key_env: str = DEFAULT_LLM_API_KEY_ENV
    cache: Dict[str, "UpgradeGeneration"] = {}
    call_log: List[Dict[str, Any]] = []

    @classmethod
    def reset(cls) -> None:
        cls.cache = {}
        cls.call_log = []


def _llm_generate_upgrade(sentence: str, rough_input: Optional[str], allows_complex_clauses: bool, model: str, api_key: str, timeout: int = LLM_JUDGE_TIMEOUT_SECONDS) -> "UpgradeGeneration":
    scope_text = _LLM_UPGRADE_SCOPE_ALLOW_COMPLEX if allows_complex_clauses else _LLM_UPGRADE_SCOPE_SINGLE_CLAUSE
    system_prompt = _LLM_UPGRADE_SYSTEM_PROMPT_BASE + scope_text + _LLM_UPGRADE_RESPONSE_INSTRUCTION
    user_content = f"Sentence: {sentence}"
    if rough_input:
        user_content += f"\nOriginal rough notes: {rough_input}"
    body = json.dumps({
        "model": model,
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    choices = payload.get("choices") or []
    text = (choices[0].get("message", {}).get("content", "") if choices else "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    parsed = json.loads(text)
    upgraded = parsed.get("upgraded_academic_version")
    return UpgradeGeneration(
        upgraded_academic_version=str(upgraded).strip() if upgraded else None,
        why_better=parsed.get("why_better"),
        source="llm",
        raw=parsed,
    )


def generate_upgrade(sentence: str, rough_input: Optional[str], scope: Optional[Dict[str, Any]]) -> "UpgradeGeneration":
    sentence = sentence or ""
    if not UpgradeGeneratorConfig.enabled or not sentence.strip():
        return UpgradeGeneration(upgraded_academic_version=None, why_better=None, source="disabled")
    allows_complex_clauses = bool((scope or {}).get("in_scope_for_full_upgrade", True))
    cache_key = sentence.strip().lower() + "||" + str(allows_complex_clauses)
    if cache_key in UpgradeGeneratorConfig.cache:
        return UpgradeGeneratorConfig.cache[cache_key]
    api_key = os.environ.get(UpgradeGeneratorConfig.api_key_env, "")
    if not api_key:
        UpgradeGeneratorConfig.call_log.append({"sentence": sentence, "status": "no_api_key_skip"})
        result = UpgradeGeneration(upgraded_academic_version=None, why_better=None, source="llm_fallback_skip")
        UpgradeGeneratorConfig.cache[cache_key] = result
        return result
    try:
        result = _llm_generate_upgrade(sentence, rough_input, allows_complex_clauses, UpgradeGeneratorConfig.model, api_key)
        UpgradeGeneratorConfig.call_log.append({"sentence": sentence, "status": "ok"})
    except Exception as e:
        result = UpgradeGeneration(upgraded_academic_version=None, why_better=None, source="llm_fallback_skip")
        UpgradeGeneratorConfig.call_log.append({
            "sentence": sentence,
            "status": f"llm_error_skip: {type(e).__name__}: {e}",
        })
    UpgradeGeneratorConfig.cache[cache_key] = result
    return result


_wc1216_feedback_layers_for_upgrade_wrap = MissionEvaluator._feedback_layers


def _wc1217_feedback_layers(self, sentence: str, rough_input: Optional[str], strengths: List[str], issues: List[str]) -> Dict[str, Any]:
    layers = _wc1216_feedback_layers_for_upgrade_wrap(self, sentence, rough_input, strengths, issues)
    if not UpgradeGeneratorConfig.enabled:
        return layers
    hbu = layers.get("higher_band_upgrade") or {}
    if not hbu.get("needed"):
        return layers
    base_sentence = safe_get(layers, "correctness_fix.minimal_corrected_version") or sentence
    scope = getattr(self, "_wc1211_scope", None) or {"in_scope_for_full_upgrade": True}
    result = generate_upgrade(base_sentence, rough_input, scope)
    if not result.upgraded_academic_version:
        return layers
    final_version = result.upgraded_academic_version
    scope_capped = False
    preview = None
    if not scope.get("in_scope_for_full_upgrade") and _WC1211_CLAUSE_MARKERS.search(final_version):
        # Safety net: the model was told to stay single-clause but didn't.
        # Don't trust an out-of-scope rewrite -- fall back to the known-safe
        # corrected sentence, and keep the LLM's version as a labeled
        # preview, same UX V1.2.11 already uses for the hardcoded templates.
        preview = final_version
        final_version = base_sentence
        scope_capped = True
    hbu["upgraded_academic_version"] = final_version
    hbu["generated_by"] = "llm_upgrade_generator"
    if result.why_better:
        hbu["why_better"] = result.why_better
    if scope_capped:
        hbu["scope_capped"] = True
        hbu["preview_beyond_scope"] = {
            "version": preview,
            "note": (
                "This goes beyond today's mission skill scope. Shown as an "
                "optional preview for a later, harder mission -- not "
                "required here."
            ),
        }
    else:
        hbu.pop("scope_capped", None)
        hbu.pop("preview_beyond_scope", None)
    layers["higher_band_upgrade"] = hbu
    return layers


_wc1216_feedback_for_upgrade_wrap = MissionEvaluator._feedback


def _wc1217_feedback(self, outcome: str, unit_scores: List[Dict[str, Any]], mission: Dict[str, Any], lines: List[str],
                    required_items: int, completion_gate: Dict[str, Any], item_feedback: List[Dict[str, Any]],
                    mastery_update_allowed: bool) -> Dict[str, Any]:
    bundle = _wc1216_feedback_for_upgrade_wrap(self, outcome, unit_scores, mission, lines, required_items, completion_gate, item_feedback, mastery_update_allowed)
    # Must run AFTER the whole _feedback chain, not during _item_feedback:
    # _wc1212_feedback re-derives student_friendly_feedback a second time
    # from item.get("upgraded_academic_version") (which is safe -- a stable
    # item field, unaffected) but recomputes why_better_version_is_better via
    # a static keyword-matched function each time, discarding anything set
    # earlier. Patching here, on the final bundle, is the only point that
    # survives.
    sf = bundle.get("student_feedback") or {}
    for item in sf.get("item_feedback") or []:
        hbu = safe_get(item, "local_feedback_split.higher_band_upgrade") or {}
        why_better = hbu.get("why_better")
        if hbu.get("generated_by") == "llm_upgrade_generator" and why_better:
            sff = item.get("student_friendly_feedback") or {}
            if sff.get("better_version"):
                sff["why_better_version_is_better"] = [why_better]
                item["student_friendly_feedback"] = sff
    bundle["student_feedback"] = sf
    debug = bundle.get("debug_evaluation") or {}
    debug["upgrade_generator_enabled"] = UpgradeGeneratorConfig.enabled
    if UpgradeGeneratorConfig.enabled:
        debug["upgrade_generator_model"] = UpgradeGeneratorConfig.model
        debug["upgrade_generator_call_log"] = list(UpgradeGeneratorConfig.call_log)
    bundle["debug_evaluation"] = debug
    return bundle


MISSION_RESULT_SCHEMA_V1_2_17 = "WRITING_COACH_MISSION_RESULT_V1_2_17"
EVALUATOR_VERSION_V1_2_17 = "writing_coach_v1_2_17_upgrade_generator_evaluator"

_wc1216_evaluate_for_upgrade_wrap = MissionEvaluator.evaluate


def _wc1217_evaluate(self, mission_payload, response_text, item_ledger=None):
    result = _wc1216_evaluate_for_upgrade_wrap(self, mission_payload, response_text, item_ledger)
    result["schema_version"] = MISSION_RESULT_SCHEMA_V1_2_17
    result["evaluator_version"] = EVALUATOR_VERSION_V1_2_17
    return result


# Install V1.2.17 overrides, all opt-in no-ops unless UpgradeGeneratorConfig.enabled.
MissionEvaluator._feedback_layers = _wc1217_feedback_layers
MissionEvaluator._feedback = _wc1217_feedback
MissionEvaluator.evaluate = _wc1217_evaluate


# ============================================================================
# V1.2.18 addition (Gold pipeline integration): a LanguageTool grammar-
# correctness layer on top of the existing regex/LLM structural judge, plus
# a mission-response-level LLM review of reasoning/clarity/relevance/
# argumentation with improvement suggestions. Both are separate, opt-in
# layers following the same pattern as V1.2.13-17 above -- zero behavior
# change unless --lt-judge / --llm-response-quality are passed.
#
# Why these are split from the existing four LLM pilots: LLMJudgeConfig
# judges sentence structure (subject/verb/completeness) one line at a time;
# LanguageTool is a different tool for a different job -- real grammar-rule
# correctness (subject-verb agreement, tense, article use). And none of the
# existing four pilots look at the submitted response as a whole, which is
# what reasoning/clarity/relevance/argumentation review requires.
# ============================================================================

DEFAULT_RESPONSE_QUALITY_MODEL = "gpt-4o-mini"

_LANGUAGETOOL_INSTANCE = None
_LANGUAGETOOL_INIT_FAILED = False
_LANGUAGETOOL_IGNORED_CATEGORIES = {"TYPOGRAPHY", "STYLE", "CASING", "PUNCTUATION"}


class LTJudgeConfig:
    """Process-wide toggle for LanguageTool-based grammar-correctness
    checking of student mission response sentences. Supplements (does not
    replace) the existing regex/LLM structural judge -- LanguageTool adds
    real grammar-rule errors that neither the regex heuristic nor the
    structural LLM judge check for."""
    enabled: bool = False
    language: str = "en-US"
    call_log: List[Dict[str, Any]] = []

    @classmethod
    def reset(cls) -> None:
        cls.call_log = []


def _get_language_tool():
    """Lazily starts one LanguageTool instance per process and reuses it --
    starting LanguageTool per-sentence would be far too slow. Any import/
    start failure is cached so we don't retry on every sentence;
    judge_sentence() simply falls back to whatever base judgment (regex or
    LLM) already ran."""
    global _LANGUAGETOOL_INSTANCE, _LANGUAGETOOL_INIT_FAILED
    if _LANGUAGETOOL_INSTANCE is not None:
        return _LANGUAGETOOL_INSTANCE
    if _LANGUAGETOOL_INIT_FAILED:
        return None
    try:
        import language_tool_python
        _LANGUAGETOOL_INSTANCE = language_tool_python.LanguageTool(LTJudgeConfig.language)
        return _LANGUAGETOOL_INSTANCE
    except Exception:
        _LANGUAGETOOL_INIT_FAILED = True
        return None


def _languagetool_check(sentence: str) -> Optional[List[str]]:
    """Runs LanguageTool on one sentence and returns filtered grammar-issue
    strings, or None if LanguageTool is unavailable or errors (callers
    should treat None as "no signal available", not "sentence is clean").
    Shared by both integration points: the observable_unit-scoring path
    (_apply_languagetool_layer, via judge_sentence) and the student-facing
    item-feedback path (_sentence_strengths_issues override below)."""
    tool = _get_language_tool()
    if tool is None:
        LTJudgeConfig.call_log.append({"sentence": sentence, "status": "languagetool_unavailable"})
        return None
    try:
        matches = tool.check(sentence)
    except Exception as e:
        LTJudgeConfig.call_log.append({"sentence": sentence, "status": f"languagetool_error: {type(e).__name__}: {e}"})
        return None
    lt_issues = [
        f"languagetool[{getattr(m, 'ruleId', '')}]: {getattr(m, 'message', '')}"
        for m in matches
        if (getattr(m, "category", "") or "").upper() not in _LANGUAGETOOL_IGNORED_CATEGORIES
    ]
    LTJudgeConfig.call_log.append({"sentence": sentence, "status": "ok", "issue_count": len(lt_issues)})
    return lt_issues


def _apply_languagetool_layer(sentence: str, judgment: "SentenceJudgment") -> "SentenceJudgment":
    lt_issues = _languagetool_check(sentence)
    if not lt_issues:
        return judgment
    combined_issues = list(judgment.issues) + [i for i in lt_issues if i not in judgment.issues]
    return SentenceJudgment(
        has_subject=judgment.has_subject,
        has_finite_verb=judgment.has_finite_verb,
        is_complete_sentence=judgment.is_complete_sentence and not lt_issues,
        issues=combined_issues,
        source=f"{judgment.source}+languagetool",
        raw=judgment.raw,
    )


_wc1218_judge_sentence_base = judge_sentence


def judge_sentence(sentence: str) -> "SentenceJudgment":
    """V1.2.18: layers a LanguageTool grammar-correctness pass on top of
    whichever structural judge (regex or LLM, per LLMJudgeConfig) already
    ran. No-op unless --lt-judge is passed, so existing behavior is
    unchanged by default."""
    judgment = _wc1218_judge_sentence_base(sentence)
    if not LTJudgeConfig.enabled:
        return judgment
    return _apply_languagetool_layer(sentence, judgment)


_LLM_RESPONSE_QUALITY_SYSTEM_PROMPT = (
    "You are an IELTS/academic writing coach reviewing a student's short "
    "practice response to a writing mission. Judge the response AS A WHOLE "
    "(not sentence-by-sentence) on four dimensions, each rated 1-5 (5 = "
    "strong): reasoning_quality (does the response show sound, logical "
    "thinking), clarity (is the point easy to follow), relevance (does it "
    "actually address the mission's prompt/task), and argumentation_quality "
    "(is any claim supported, not just asserted). Also return 1-3 short, "
    "specific improvement_suggestions the student can act on, and 0-2 "
    "strengths worth naming. Respond with ONLY a JSON object: "
    "{\"reasoning_quality\": <1-5 int>, \"clarity\": <1-5 int>, "
    "\"relevance\": <1-5 int>, \"argumentation_quality\": <1-5 int>, "
    "\"strengths\": [<string>, ...], \"improvement_suggestions\": "
    "[<string>, ...]}. Be concise and specific to what was actually "
    "written -- do not give generic advice."
)


class ResponseQualityConfig:
    """Process-wide toggle for a mission-response-level LLM review of
    reasoning/clarity/relevance/argumentation, plus improvement
    suggestions. Separate from LLMJudgeConfig (per-sentence structural
    judge) and RegisterJudgeConfig (formality) -- this looks at the whole
    submitted response together, since reasoning and argumentation are
    properties of the response as a whole, not of any single sentence."""
    enabled: bool = False
    model: str = DEFAULT_RESPONSE_QUALITY_MODEL
    api_key_env: str = DEFAULT_LLM_API_KEY_ENV
    cache: Dict[str, Dict[str, Any]] = {}
    call_log: List[Dict[str, Any]] = []

    @classmethod
    def reset(cls) -> None:
        cls.cache = {}
        cls.call_log = []


def _llm_review_response_quality(response_text: str, mission_context: str, model: str, api_key: str, timeout: int = LLM_JUDGE_TIMEOUT_SECONDS) -> Dict[str, Any]:
    user_content = f"Mission task: {mission_context}\n\nStudent's response:\n{response_text}"
    body = json.dumps({
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _LLM_RESPONSE_QUALITY_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    choices = payload.get("choices") or []
    text = (choices[0].get("message", {}).get("content", "") if choices else "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    return json.loads(text)


def review_response_quality(response_text: str, mission: Dict[str, Any]) -> Dict[str, Any]:
    """Single entry point. Returns a disabled/no-op stub unless
    --llm-response-quality is passed and an API key is available; falls
    back to the same stub on any call failure so a flaky LLM call never
    breaks mission evaluation."""
    stub = {
        "enabled": False,
        "reasoning_quality": None,
        "clarity": None,
        "relevance": None,
        "argumentation_quality": None,
        "strengths": [],
        "improvement_suggestions": [],
        "source": "disabled",
    }
    if not ResponseQualityConfig.enabled:
        return stub
    cache_key = response_text.strip().lower()
    if cache_key in ResponseQualityConfig.cache:
        return ResponseQualityConfig.cache[cache_key]
    api_key = os.environ.get(ResponseQualityConfig.api_key_env, "")
    mission_context = safe_get(mission, "task_prompt") or safe_get(mission, "title") or safe_get(mission, "target_skill_name") or ""
    if not api_key:
        ResponseQualityConfig.call_log.append({"status": "no_api_key_skip"})
        result = dict(stub)
        result["source"] = "llm_fallback_skip_no_api_key"
        ResponseQualityConfig.cache[cache_key] = result
        return result
    try:
        parsed = _llm_review_response_quality(response_text, mission_context, ResponseQualityConfig.model, api_key)
        result = {
            "enabled": True,
            "reasoning_quality": parsed.get("reasoning_quality"),
            "clarity": parsed.get("clarity"),
            "relevance": parsed.get("relevance"),
            "argumentation_quality": parsed.get("argumentation_quality"),
            "strengths": [str(x) for x in (parsed.get("strengths") or [])],
            "improvement_suggestions": [str(x) for x in (parsed.get("improvement_suggestions") or [])],
            "source": "llm",
            "model": ResponseQualityConfig.model,
        }
        ResponseQualityConfig.call_log.append({"status": "ok"})
    except Exception as e:
        result = dict(stub)
        result["source"] = "llm_fallback_skip_error"
        ResponseQualityConfig.call_log.append({"status": f"llm_error_skip: {type(e).__name__}: {e}"})
    ResponseQualityConfig.cache[cache_key] = result
    return result


MISSION_RESULT_SCHEMA_V1_2_18 = "WRITING_COACH_MISSION_RESULT_V1_2_18"
EVALUATOR_VERSION_V1_2_18 = "writing_coach_v1_2_18_lt_grammar_and_response_quality"

_wc1218_evaluate_for_response_quality_wrap = MissionEvaluator.evaluate


def _wc1218_evaluate(self, mission_payload, response_text, item_ledger=None):
    result = _wc1218_evaluate_for_response_quality_wrap(self, mission_payload, response_text, item_ledger)
    mission, _ = extract_mission_for_cli(mission_payload)
    result["response_quality_review"] = review_response_quality(response_text, mission or {})
    debug = result.get("debug_evaluation") or {}
    debug["lt_judge_enabled"] = LTJudgeConfig.enabled
    if LTJudgeConfig.enabled:
        debug["lt_judge_call_log"] = list(LTJudgeConfig.call_log)
    debug["response_quality_enabled"] = ResponseQualityConfig.enabled
    result["debug_evaluation"] = debug
    result["schema_version"] = MISSION_RESULT_SCHEMA_V1_2_18
    result["evaluator_version"] = EVALUATOR_VERSION_V1_2_18
    return result


# V1.2.18 fix (found via a real run against an Article Control mission):
# _apply_languagetool_layer only ever ran through judge_sentence(), which
# _score_unit only calls for a narrow set of observable_unit tags
# (finite_verb_present/subject_present/complete_meaning-style units). Most
# move-bank missions -- Article Control included -- use different unit tags
# (article_choice_accurate, noun_phrase_complete, ...) that never call
# judge_sentence() at all, so --lt-judge silently never fired for them: a
# real run showed lt_judge_call_log=[] despite lt_judge_enabled=true. This
# second integration point hooks LanguageTool into _sentence_strengths_issues
# instead, which unconditionally runs for every submitted item regardless of
# mission/unit type, so grammar checking now actually applies to any
# response, not just certain move types.
_wc1218_sentence_strengths_issues_for_lt_wrap = MissionEvaluator._sentence_strengths_issues


def _wc1218_sentence_strengths_issues(self, sentence: str, rough_input: Optional[str] = None) -> Tuple[List[str], List[str]]:
    strengths, issues = _wc1218_sentence_strengths_issues_for_lt_wrap(self, sentence, rough_input)
    if not LTJudgeConfig.enabled:
        return strengths, issues
    lt_issues = _languagetool_check(sentence)
    if lt_issues:
        for i in lt_issues:
            if i not in issues:
                issues.append(i)
    return strengths, issues


# Install V1.2.18 overrides -- all opt-in no-ops unless LTJudgeConfig.enabled
# / ResponseQualityConfig.enabled.
MissionEvaluator._sentence_strengths_issues = _wc1218_sentence_strengths_issues
MissionEvaluator.evaluate = _wc1218_evaluate


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="VA/ST.ELLA Writing Coach V1.2.17 Proactive Adaptive Planner with Attempt CLI (opt-in universal LLM academic-upgrade generator over V1.2.16)")
    p.add_argument("--move-bank", help="External Micro-Writing Move Bank JSON file.")
    p.add_argument("--mode", choices=["auto", "after_essay", "daily", "weekly_review"], default="auto", help="Run mode. auto uses daily if --coach-state is supplied without essay inputs; otherwise after_essay.")
    p.add_argument("--coach-state", help="Writing Coach state JSON from a previous V1.2 output; used for proactive daily continuation.")
    p.add_argument("--last-mission-result", help="Latest mission-result JSON; optional but recommended for daily continuation.")
    p.add_argument("--plan-horizon-days", type=int, default=7, help="Rolling adaptive plan horizon; default 7 days.")
    p.add_argument("--state-output", help="Optional path to save coach_state_export separately.")
    p.add_argument("--evaluator")
    p.add_argument("--intake")
    p.add_argument("--detector")
    p.add_argument("--errormap")
    p.add_argument("--scorer")
    p.add_argument("--verifier")
    p.add_argument("--adjudicated")
    p.add_argument("--scorer-metrics")
    p.add_argument("--score-contract")
    p.add_argument("--priority")
    p.add_argument("--directive")
    p.add_argument("--feedback")
    p.add_argument("--feedback-report")
    p.add_argument("--ontology")
    p.add_argument("--clusters")
    p.add_argument("--evaluate-mission", help="Existing Writing Coach output JSON to evaluate a response from file/text.")
    p.add_argument("--show-mission", help="Print a generated Writing Coach mission to the terminal and exit.")
    p.add_argument("--attempt-mission", help="Open a generated Writing Coach mission, collect/accept the student's response, evaluate it, and optionally update coach state.")
    p.add_argument("--interactive", action="store_true", help="With --attempt-mission, let the student type the answer in the terminal.")
    p.add_argument("--student-response", help="Text file containing student's response to the mission.")
    p.add_argument("--student-response-text", help="Student response as a direct command-line string.")
    p.add_argument("--llm-judge", action="store_true", help="V1.2.13 pilot: use an LLM call to judge sentence subject/verb/completeness instead of regex. Off by default; falls back to regex on any failure.")
    p.add_argument("--llm-model", default=DEFAULT_LLM_MODEL, help="Model name for --llm-judge calls.")
    p.add_argument("--llm-api-key-env", default=DEFAULT_LLM_API_KEY_ENV, help="Environment variable name holding the OpenAI API key for --llm-judge.")
    p.add_argument("--llm-register-judge", action="store_true", help="V1.2.15 pilot: use a separate LLM call to judge academic register/formality (first-person framing, contractions, casual connectors). Off by default; silently flags nothing if disabled or the call fails.")
    p.add_argument("--llm-register-model", default=DEFAULT_REGISTER_MODEL, help="Model name for --llm-register-judge calls.")
    p.add_argument("--llm-register-api-key-env", default=DEFAULT_LLM_API_KEY_ENV, help="Environment variable name holding the OpenAI API key for --llm-register-judge.")
    p.add_argument("--llm-correction-generator", action="store_true", help="V1.2.16 pilot: use an LLM call to generate minimal_corrected_version/simple_fix that actually targets whatever the grammar/register judge flagged, instead of the old hardcoded per-topic templates. Off by default; falls back to V1.2.8-15 template behavior on any failure.")
    p.add_argument("--llm-correction-model", default=DEFAULT_CORRECTION_MODEL, help="Model name for --llm-correction-generator calls.")
    p.add_argument("--llm-correction-api-key-env", default=DEFAULT_LLM_API_KEY_ENV, help="Environment variable name holding the OpenAI API key for --llm-correction-generator.")
    p.add_argument("--llm-upgrade-generator", action="store_true", help="V1.2.17 pilot: use an LLM call to generate a genuine, universal academic upgrade (upgraded_academic_version) instead of the old per-topic hardcoded templates, which only worked for 5 built-in demo topics. Off by default; falls back to V1.2.8-16 template behavior on any failure.")
    p.add_argument("--llm-upgrade-model", default=DEFAULT_UPGRADE_MODEL, help="Model name for --llm-upgrade-generator calls.")
    p.add_argument("--llm-upgrade-api-key-env", default=DEFAULT_LLM_API_KEY_ENV, help="Environment variable name holding the OpenAI API key for --llm-upgrade-generator.")
    p.add_argument("--lt-judge", action="store_true", help="V1.2.18: use LanguageTool to add real grammar-rule correctness checking (subject-verb agreement, tense, articles, etc.) on top of the existing regex/LLM structural judge. Off by default; a no-op if LanguageTool isn't installed/reachable.")
    p.add_argument("--lt-language", default="en-US", help="LanguageTool language code for --lt-judge.")
    p.add_argument("--llm-response-quality", action="store_true", help="V1.2.18: use an LLM call to review the whole submitted response (not sentence-by-sentence) for reasoning, clarity, relevance to the prompt, and argumentation quality, plus improvement suggestions. Off by default.")
    p.add_argument("--llm-response-quality-model", default=DEFAULT_RESPONSE_QUALITY_MODEL, help="Model name for --llm-response-quality calls.")
    p.add_argument("--llm-response-quality-api-key-env", default=DEFAULT_LLM_API_KEY_ENV, help="Environment variable name holding the OpenAI API key for --llm-response-quality.")
    p.add_argument("--output", help="Output JSON path. Required except with --show-mission.")
    p.add_argument("--pretty", action="store_true")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    LLMJudgeConfig.enabled = bool(getattr(args, "llm_judge", False))
    LLMJudgeConfig.model = getattr(args, "llm_model", DEFAULT_LLM_MODEL)
    LLMJudgeConfig.api_key_env = getattr(args, "llm_api_key_env", DEFAULT_LLM_API_KEY_ENV)
    LLMJudgeConfig.reset()
    RegisterJudgeConfig.enabled = bool(getattr(args, "llm_register_judge", False))
    RegisterJudgeConfig.model = getattr(args, "llm_register_model", DEFAULT_REGISTER_MODEL)
    RegisterJudgeConfig.api_key_env = getattr(args, "llm_register_api_key_env", DEFAULT_LLM_API_KEY_ENV)
    RegisterJudgeConfig.reset()
    CorrectionGeneratorConfig.enabled = bool(getattr(args, "llm_correction_generator", False))
    CorrectionGeneratorConfig.model = getattr(args, "llm_correction_model", DEFAULT_CORRECTION_MODEL)
    CorrectionGeneratorConfig.api_key_env = getattr(args, "llm_correction_api_key_env", DEFAULT_LLM_API_KEY_ENV)
    CorrectionGeneratorConfig.reset()
    UpgradeGeneratorConfig.enabled = bool(getattr(args, "llm_upgrade_generator", False))
    UpgradeGeneratorConfig.model = getattr(args, "llm_upgrade_model", DEFAULT_UPGRADE_MODEL)
    UpgradeGeneratorConfig.api_key_env = getattr(args, "llm_upgrade_api_key_env", DEFAULT_LLM_API_KEY_ENV)
    UpgradeGeneratorConfig.reset()
    LTJudgeConfig.enabled = bool(getattr(args, "lt_judge", False))
    LTJudgeConfig.language = getattr(args, "lt_language", "en-US")
    LTJudgeConfig.reset()
    ResponseQualityConfig.enabled = bool(getattr(args, "llm_response_quality", False))
    ResponseQualityConfig.model = getattr(args, "llm_response_quality_model", DEFAULT_RESPONSE_QUALITY_MODEL)
    ResponseQualityConfig.api_key_env = getattr(args, "llm_response_quality_api_key_env", DEFAULT_LLM_API_KEY_ENV)
    ResponseQualityConfig.reset()
    try:
        if args.show_mission:
            mission_payload = load_json(args.show_mission, required=True)
            assert_mission_payload_for_cli(mission_payload, args.show_mission)
            print(terminal_mission_text(mission_payload))
            return 0

        if args.attempt_mission:
            mission_payload = load_json(args.attempt_mission, required=True)
            assert_mission_payload_for_cli(mission_payload, args.attempt_mission)
            print(terminal_mission_text(mission_payload))
            if args.interactive:
                response_text = read_interactive_response()
            elif args.student_response_text:
                response_text = args.student_response_text
            elif args.student_response:
                response_text = Path(args.student_response).read_text(encoding="utf-8")
            else:
                raise ValueError("Use --interactive, --student-response, or --student-response-text with --attempt-mission.")
            # V1.2.12: load the item_revision_ledger (if any) from --coach-state
            # or the mission's embedded coach_state_export, so a resubmission of
            # the same mission_id/item_number is recognized as a retry and the
            # hint-before-answer ladder can progress instead of restarting.
            state_for_ledger = load_json(args.coach_state, {}) if args.coach_state else (mission_payload.get("coach_state_export") or {})
            item_ledger = (state_for_ledger or {}).get("item_revision_ledger", {})
            result = MissionEvaluator().evaluate(mission_payload, response_text, item_ledger=item_ledger)
            if not args.output:
                raise ValueError("--output is required with --attempt-mission so the mission result can be saved.")
            write_json(args.output, result, args.pretty)
            print(f"\nResult: {result.get('outcome')} | score={result.get('mission_score')} | confidence={result.get('confidence')}\n")
            print(terminal_attempt_feedback_text(result))
            # Update coach state if requested. If --coach-state is not supplied, use the state embedded in the mission output.
            if args.state_output:
                state = load_json(args.coach_state, {}) if args.coach_state else (mission_payload.get("coach_state_export") or {})
                if not state:
                    raise ValueError("--state-output was provided, but no --coach-state or embedded coach_state_export was available.")
                updated_state = normalize_coach_state_contract(update_coach_state_with_result(state, result))
                updated_state["item_revision_ledger"] = result.get("item_revision_ledger", item_ledger)
                write_json(args.state_output, updated_state, args.pretty)
                print(f"Updated coach state written to: {args.state_output}")
            return 0

        if args.evaluate_mission:
            mission_payload = load_json(args.evaluate_mission, required=True)
            assert_mission_payload_for_cli(mission_payload, args.evaluate_mission)
            if args.student_response_text:
                response_text = args.student_response_text
            elif args.student_response:
                response_text = Path(args.student_response).read_text(encoding="utf-8")
            else:
                raise ValueError("--student-response or --student-response-text is required with --evaluate-mission")
            result = MissionEvaluator().evaluate(mission_payload, response_text)
            if not args.output:
                raise ValueError("--output is required with --evaluate-mission")
            write_json(args.output, result, args.pretty)
            return 0

        if not args.output:
            raise ValueError("--output is required for generation/daily/weekly modes. Use --show-mission to only print a mission.")

        if not args.move_bank:
            raise ValueError("--move-bank is required. The Micro-Writing Move Bank is external and is not embedded in this engine.")
        inputs = {
            "evaluator": load_json(args.evaluator, {}) if args.evaluator else {},
            "intake": load_json(args.intake, {}) if args.intake else {},
            "detector": load_json(args.detector, {}) if args.detector else {},
            "errormap": load_json(args.errormap, {}) if args.errormap else {},
            "scorer": load_json(args.scorer, {}) if args.scorer else {},
            "verifier": load_json(args.verifier, {}) if args.verifier else {},
            "adjudicated": load_json(args.adjudicated, {}) if args.adjudicated else {},
            "scorer_metrics": load_json(args.scorer_metrics, {}) if args.scorer_metrics else {},
            "score_contract": load_json(args.score_contract, {}) if args.score_contract else {},
            "priority": load_json(args.priority, {}) if args.priority else {},
            "directive": load_json(args.directive, {}) if args.directive else {},
            "feedback": load_json(args.feedback, {}) if args.feedback else {},
            "feedback_report": load_json(args.feedback_report, {}) if args.feedback_report else {},
            "ontology": load_json(args.ontology, {}) if args.ontology else {},
            "clusters": load_json(args.clusters, {}) if args.clusters else {},
        }
        move_bank = MoveBank.from_path(args.move_bank)
        resources = OntologyResources(inputs.get("ontology") or {}, inputs.get("clusters") or {})
        engine = ProactiveAdaptiveWritingCoach(move_bank, resources)
        coach_state = load_json(args.coach_state, {}) if args.coach_state else {}
        last_result = load_json(args.last_mission_result, {}) if args.last_mission_result else {}
        has_essay_inputs = any([args.evaluator, args.detector, args.errormap, args.priority, args.directive, args.feedback, args.feedback_report])
        mode = args.mode
        if mode == "auto":
            mode = "daily" if args.coach_state and not has_essay_inputs else "after_essay"
        if mode == "weekly_review":
            if not coach_state:
                raise ValueError("--coach-state is required for --mode weekly_review")
            output = engine.weekly_review(coach_state, last_result)
        elif mode == "daily":
            if not coach_state:
                raise ValueError("--coach-state is required for --mode daily")
            output = engine.generate_daily(coach_state, last_result, inputs, args.plan_horizon_days)
        else:
            output = engine.generate(inputs, coach_state, last_result, args.plan_horizon_days)
        write_json(args.output, output, args.pretty)
        if args.state_output and isinstance(output, dict) and output.get("coach_state_export"):
            write_json(args.state_output, normalize_coach_state_contract(output["coach_state_export"]), args.pretty)
        return 0
    except Exception as e:
        sys.stderr.write(f"ERROR: {e}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
