#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VA / ST.ELLA — Writing Coach V1.2.7 Proactive Adaptive Planner with Attempt CLI
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
python writing_coach_v1_2_7_freeze_candidate.py \
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
  --output writing_coach_v1_2_7_output.json \
  --pretty

Evaluate a student response to a generated mission:
python writing_coach_v1_2_7_freeze_candidate.py \
  --evaluate-mission writing_coach_v1_2_7_output.json \
  --student-response student_response.txt \
  --output writing_coach_v1_2_7_mission_result.json \
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
ENGINE_VERSION = "writing_coach_v1.2.7_proactive_adaptive_cli"
OUTPUT_SCHEMA = "WRITING_COACH_OUTPUT_V1_2_7_PROACTIVE_ADAPTIVE_CLI"
MISSION_RESULT_SCHEMA = "WRITING_COACH_MISSION_RESULT_V1_2_7"

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

    V1.2.7 distinguishes STRUCTURAL validity from MASTERY validity.
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

    V1.2.7: incomplete/invalid attempts may be stored for behavior history, but
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

    V1.2.7 deliberately rejects mission-result/state files before the user is
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
            "for example writing_coach_v1_2_7_output.json or a daily output JSON."
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

    V1.2.7 keeps explicit numbering and improves unnumbered matching.
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


STATE_SCHEMA = "WRITING_COACH_STATE_V1_2_7"


def normalize_coach_state_contract(state: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize every state output path to the V1.2.7 contract."""
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
            "mission_version": "writing_coach_v1_2_7_move_mission",
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
            f"Selected by move-based Writing Coach V1.2.7. skill={skill.skill_id}; "
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

    V1.2.7 adds a hard completion gate and a student-facing Attempt Feedback
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
            return score, f"Detected {len(lines)} item(s); required {required_items}. This is a hard completion gate in V1.2.7."
        if uid in {"subject_present", "clear_subject"}:
            vals = [1.0 if any(w in line.lower().split() for w in self.SUBJECT_HINTS) or re.match(r"^[A-Z]?[a-z]+\s+", line) else 0.4 for line in lines]
            return (sum(vals) / len(vals) if vals else 0.0), "Checks whether each line has an explicit subject or subject-like noun phrase."
        if uid in {"finite_verb_present", "main_verb_present"}:
            vals = [1.0 if any(v in words(line) for v in self.FINITE_VERBS) or re.search(r"\b\w+(?:s|ed)\b", line.lower()) else 0.25 for line in lines]
            return (sum(vals) / len(vals) if vals else 0.0), "Checks for a finite/main verb in each response line."
        if uid in {"verb_form_correct", "pattern_accuracy"}:
            bad_patterns = [r"\bhas to spent\b", r"\bhave to spent\b", r"\bcan gives\b", r"\bthis make\b", r"\bthey helps\b", r"\bmore \w+er\b", r"\bso \w+er\b"]
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

        if any(v in ws for v in self.FINITE_VERBS) or re.search(r"\b\w+(?:s|ed)\b", lower):
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
            "interpretation": "Incomplete output is treated as non-mastery evidence in V1.2.7." if not mastery_update_allowed else "Complete attempt can be used as mission-level skill evidence.",
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

    def _aggregate_strengths(self, item_feedback: List[Dict[str, Any]]) -> List[str]:
        c = Counter()
        for item in item_feedback:
            for s in item.get("strengths") or []:
                c[s] += 1
        return [k for k, _ in c.most_common(4)]

    def _prioritize_issues(self, item_feedback: List[Dict[str, Any]], completion_gate: Dict[str, Any]) -> List[str]:
        c = Counter()
        for item in item_feedback:
            if item.get("status") == "submitted":
                for issue in item.get("issues") or []:
                    c[str(issue).split(":")[0]] += 1
        submitted_issues = [k for k, _ in c.most_common(3)]
        if not completion_gate.get("mastery_update_allowed", False):
            return ["complete_all_required_items"] + submitted_issues
        return submitted_issues or ["continue_same_move_with_new_topic"]

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
            "contract_compatibility": "WRITING_COACH_OUTPUT_V1_2_7",
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
                "selection_policy": "move_based_dependency_aware_next_best_microskill_v1_2_7",
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

    def _micro_lesson(self, skill: SkillCandidate, move: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "skill_id": skill.skill_id,
            "title": f"Mini-lesson: {move.get('move_name')}",
            "teaching_point": move.get("teaching_point") or move.get("student_goal") or f"Practise {skill.skill_name} in a short writing move.",
            "do": move.get("do") or [],
            "avoid": move.get("avoid") or [],
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
        if mission.get("mission_version") and "v1_2_7" not in str(mission.get("mission_version")):
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

    V1.2.7 selects a skill, generates today's move mission, and plans the proactive cycle:
    - V1.2 introduced proactive planning;
    - V1.2.7 is the freeze-candidate consistency/safety patch;
    - after-essay prescription starts/refreshed an active cycle;
    - daily continuation can run from saved coach state + latest mission result;
    - output includes rolling 7-day adaptive plan, next-run trigger, home card, and state export.
    """

    def generate(self, inputs: Dict[str, Any], coach_state: Optional[Dict[str, Any]] = None,
                 last_result: Optional[Dict[str, Any]] = None, plan_horizon_days: int = 7) -> Dict[str, Any]:
        base = super().generate(inputs)
        base["schema_version"] = OUTPUT_SCHEMA
        base["contract_compatibility"] = "WRITING_COACH_OUTPUT_V1_2_7"
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
            "version_labels_v1_2_7": (
                base.get("contract_compatibility") == "WRITING_COACH_OUTPUT_V1_2_7"
                and "v1_2_7" in str(mission.get("mission_version", ""))
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
            # V1.2.7: incomplete/invalid attempts are not upgrades. Reassign
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
            "selection_policy": "proactive_daily_continuation_v1_2_7",
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
            "contract_compatibility": "WRITING_COACH_OUTPUT_V1_2_7",
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
        # V1.2.7 freeze-candidate semantic QA for daily continuation.
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
            "version_labels_v1_2_7": output.get("contract_compatibility") == "WRITING_COACH_OUTPUT_V1_2_7" and "v1_2_7" in str(mission.get("mission_version", "")),
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
# V1.2.7 Adaptive Mission Variant Generator + Focused Retry Overrides
# ---------------------------------------------------------------------------

ADAPTIVE_VARIANT_SCHEMA = "WRITING_COACH_ADAPTIVE_VARIANT_V1_2_7"


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

    V1.2.7 supports focused retry missions where expected item numbers may be
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
            note = f"Detected {submitted_items} expected item(s); required {required_items}. Expected numbers: {expected_numbers}. This is a hard completion gate in V1.2.7."
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
    mission["mission_version"] = "writing_coach_v1_2_7_move_mission"
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


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="VA/ST.ELLA Writing Coach V1.2.7 Proactive Adaptive Planner with Attempt CLI")
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
    p.add_argument("--output", help="Output JSON path. Required except with --show-mission.")
    p.add_argument("--pretty", action="store_true")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
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
            result = MissionEvaluator().evaluate(mission_payload, response_text)
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
