#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VA / ST.ELLA — Gold Full Pipeline Runner v1.0
=============================================

Standalone orchestration layer for the Gold subscription pipeline.

Design boundary
---------------
This runner does NOT replace the Premium pipeline. It wraps it.

Premium remains responsible for:
  - Detector / ErrorMap
  - Premium Scorer
  - Verifier
  - Automated Adjudicator
  - Final Score Contract
  - Premium Feedback
  - Premium Practice output

Gold adds:
  - Evaluator / WKE execution
  - Gold Evidence Fusion
  - Gold Feedback / Writing Capacity profile
  - LRET lexical training session, with deterministic fallback
  - Writing Coach execution or deterministic mission fallback
  - Gold Practice directive enrichment
  - Gold LIE learner-profile update
  - Gold Service Router
  - Gold QA report

No essay-specific rules are used. All routing is evidence-family, skill, status,
and confidence based.

Typical use
-----------
Run Premium + Gold in one command:

  python gold_full_pipeline_runner_v1.py \
    --input submission.json \
    --premium-runner pipeline_runner_v14j_revision_safe.py \
    --evaluator-runner va_premium_evaluator_v7_3b_wke_v7_3b_3.py \
    --output-root gold_sessions \
    --pretty

Run Gold on an existing Premium session:

  python gold_full_pipeline_runner_v1.py \
    --input submission.json \
    --premium-session-dir full_premium_v1/sessions/session_001 \
    --evaluator-runner va_premium_evaluator_v7_3b_wke_v7_3b_3.py \
    --output-root gold_sessions \
    --pretty

Optional Writing Coach:

  python gold_full_pipeline_runner_v1.py ... \
    --writing-coach-runner writing_coach_v1_2_3_cli.py \
    --move-bank micro_writing_move_bank_simple_v1.json \
    --ontology writing_competency_ontology_v3.json \
    --clusters VA_microskill_clustering_v3.json

Optional revision hook:
Gold revision is normally a second user action, because the learner must submit
an edited essay. This runner writes a revision_launch_packet.json showing the
exact original-session context needed by the revision runner.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ENGINE_ID = "VA_STELLA_GOLD_FULL_PIPELINE_RUNNER"
ENGINE_VERSION = "1.0.0"
SCHEMA_VERSION = "GOLD_FULL_PIPELINE_OUTPUT_V1"

REQUIRED_PREMIUM_FILES = {
    "intake": "00_intake_assessment.json",
    "detector": "01_detector_output.json",
    "errormap": "01b_errormap_v3.json",
    "scorer": "02a_premium_scorer_v1_4_1_output.json",
    "verifier": "02b_premium_verifier_v1_4_3_output.json",
    "adjudicator": "02c_final_adjudicated_v1_2.json",
    "score_contract": "02d_final_score_contract.json",
    "priority": "03_pe_output.json",
    "directive": "04_directive_v2.json",
    "feedback_engine": "05_fe_output.json",
    "feedback_report": "06_feedback_report_v6c.json",
}

OPTIONAL_PREMIUM_FILES = {
    "practice_session": "07b_practice_session.json",
    "practice_results": "07c_practice_results.json",
    "practice_debrief": "07f_practice_debrief_v2.json",
    "lie_profile": "08_learning_profile.json",
    "observed_activity": "08_observed_activity.json",
    "study_plan": "09_study_plan_v1.json",
    "qa_report": "QA_report.json",
}

GOLD_ARTIFACTS = {
    "evaluator_request": "07_evaluator_request.json",
    "evaluator_output": "07_evaluator_output.json",
    "evidence_fusion": "07b_gold_evidence_fusion.json",
    "gold_feedback": "07c_gold_feedback_report.json",
    "lret_session": "07d_lret_session.json",
    "writing_coach": "07e_writing_coach_output.json",
    "writing_coach_state": "07e_writing_coach_state.json",
    "gold_practice_directive": "07f_gold_practice_directive.json",
    "learner_profile": "08_gold_learner_profile.json",
    "skills_progress": "08b_gold_skills_progress_report.json",
    "learning_roadmap": "08c_gold_learning_roadmap.json",
    "service_routing": "08d_gold_service_routing.json",
    "progress_snapshot": "09_gold_progress_snapshot.json",
    "revision_launch": "revision_launch_packet.json",
    "qa": "QA_gold_report.json",
    "manifest": "gold_run_manifest.json",
}

CRITERION_NAMES = {
    "TR": "Task Response",
    "CC": "Coherence & Cohesion",
    "LR": "Lexical Resource",
    "GRA": "Grammar Range & Accuracy",
    "task_response": "Task Response",
    "coherence_cohesion": "Coherence & Cohesion",
    "lexical_resource": "Lexical Resource",
    "grammatical_range_accuracy": "Grammar Range & Accuracy",
    "grammar": "Grammar Range & Accuracy",
}

FAMILY_TO_CAPACITY = {
    "ARTICLE_DETERMINER": "sentence_control",
    "NOUN_NUMBER_COUNTABILITY": "sentence_control",
    "SUBJECT_VERB_AGREEMENT": "sentence_control",
    "VERB_FORM": "sentence_control",
    "VERB_TENSE": "sentence_control",
    "VERB_PATTERN": "sentence_control",
    "CLAUSE_STRUCTURE": "sentence_control",
    "FRAGMENT": "sentence_control",
    "RUN_ON": "sentence_control",
    "CONDITIONAL_STRUCTURE": "sentence_control",
    "COMPARATIVE_FORM": "sentence_control",
    "WORD_FORM": "lexical_precision",
    "COLLOCATION": "lexical_precision",
    "WORD_CHOICE": "lexical_precision",
    "LEXICAL_PRECISION": "lexical_precision",
    "SEMANTIC_COMBINATION": "lexical_precision",
    "SPELLING": "lexical_precision",
    "REPETITION": "lexical_precision",
    "REGISTER": "style_control",
    "TRANSITION": "cohesion_control",
    "MISSING_TRANSITION": "cohesion_control",
    "REFERENCE_COHESION": "cohesion_control",
    "REFERENCE_BREAK": "cohesion_control",
    "TOPIC_CONTINUITY": "cohesion_control",
    "LOGICAL_PROGRESSION": "paragraph_logic",
    "PARAGRAPH_STRUCTURE": "paragraph_logic",
    "CHAIN_BREAK": "paragraph_logic",
    "EXAMPLE_INTEGRATION": "argument_development",
    "WEAK_EXAMPLE": "argument_development",
    "UNSUPPORTED_CLAIM": "argument_development",
    "INCOMPLETE_ARGUMENT": "argument_development",
    "CLAIM_SUPPORT_LINK": "argument_development",
    "REASONING_CHAIN": "argument_development",
    "OVERGENERALIZATION": "argument_development",
    "POSITION_CLARITY": "task_response_control",
    "PROMPT_COVERAGE": "task_response_control",
    "PROMPT_RELEVANCE": "task_response_control",
    "TASK_COMPLETENESS": "task_response_control",
    "POSITION_RESPONSE": "task_response_control",
}

CAPACITY_LABELS = {
    "sentence_control": "Sentence control",
    "lexical_precision": "Lexical precision",
    "argument_development": "Argument development",
    "paragraph_logic": "Paragraph logic",
    "cohesion_control": "Cohesion control",
    "task_response_control": "Task response control",
    "style_control": "Academic style control",
}

CAPACITY_TO_SERVICE = {
    "sentence_control": "writing_coach",
    "lexical_precision": "lret",
    "argument_development": "writing_coach",
    "paragraph_logic": "essay_revision",
    "cohesion_control": "practice",
    "task_response_control": "writing_coach",
    "style_control": "practice",
}

CAPACITY_TO_WEEK_PLAN = {
    "sentence_control": [
        "controlled clause construction",
        "verb pattern repair",
        "sentence-combining with clear subjects and verbs",
        "short transfer paragraph",
    ],
    "lexical_precision": [
        "repair unnatural lexical units",
        "collocation and word-form practice",
        "enhance weak noun/verb phrases",
        "reuse improved phrases in a new paragraph",
    ],
    "argument_development": [
        "claim + reason control",
        "specific example building",
        "explanation chain development",
        "short argument paragraph",
    ],
    "paragraph_logic": [
        "topic sentence control",
        "idea grouping",
        "paragraph-level revision",
        "full paragraph rewrite",
    ],
    "cohesion_control": [
        "reference clarity",
        "logical transition choices",
        "sentence-to-sentence flow",
        "short cohesive paragraph",
    ],
    "task_response_control": [
        "prompt-part identification",
        "position control",
        "coverage check",
        "full essay plan before writing",
    ],
    "style_control": [
        "academic tone",
        "hedging and precision",
        "formal phrase replacement",
        "reader-friendly final paragraph",
    ],
}

MEANINGLESS_LEXICAL_PATTERNS = [
    re.compile(r"^(it|this|that|there|they|we|you|he|she)\s+(is|are|was|were|has|have|had|can|could|will|would|should)$", re.I),
    re.compile(r"^(in|on|at|for|with|to|from|of|by|about|as)\b", re.I),
    re.compile(r"\b(the|a|an|of|to|for|with|and|or|but)$", re.I),
    re.compile(r"^(the|a|an|and|or|but|so|because)$", re.I),
]

WEAK_WORDS = {
    "good", "bad", "big", "small", "things", "thing", "stuff", "ways", "way",
    "important", "nice", "many ways", "a lot", "very good", "very bad",
}


# ---------------------------------------------------------------------------
# Generic utilities
# ---------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(prefix: str, *parts: Any, n: int = 12) -> str:
    raw = "|".join(str(p) for p in parts)
    digest = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:n]
    return f"{prefix}_{digest}"


def clamp(x: Any, lo: float = 0.0, hi: float = 1.0, default: float = 0.0) -> float:
    try:
        f = float(x)
    except Exception:
        return default
    if math.isnan(f):
        return default
    return max(lo, min(hi, f))


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        f = float(value)
        if math.isnan(f):
            return default
        return f
    except Exception:
        return default


def read_json(path: Path, default: Any = None, required: bool = False) -> Any:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        if required:
            raise
        return copy.deepcopy(default)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc


def write_json(path: Path, payload: Any, pretty: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2 if pretty else None)
    return path


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def deep_get(obj: Any, path: Sequence[Any], default: Any = None) -> Any:
    cur = obj
    for key in path:
        if isinstance(cur, dict):
            cur = cur.get(key)
        elif isinstance(cur, list) and isinstance(key, int) and 0 <= key < len(cur):
            cur = cur[key]
        else:
            return default
        if cur is None:
            return default
    return cur


def first_non_empty(*values: Any, default: Any = None) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, dict)) and not value:
            continue
        return value
    return default


def list_session_dirs(sessions_dir: Path) -> List[Path]:
    if not sessions_dir.exists():
        return []
    return sorted(
        [p for p in sessions_dir.iterdir() if p.is_dir() and p.name.startswith("session_")],
        key=lambda p: p.name,
    )


def detect_new_session(before: Sequence[Path], after: Sequence[Path]) -> Optional[Path]:
    before_set = {p.resolve() for p in before}
    new = [p for p in after if p.resolve() not in before_set]
    if new:
        return sorted(new, key=lambda p: p.name)[-1]
    return sorted(after, key=lambda p: p.name)[-1] if after else None


def shell_join(cmd: Sequence[str]) -> str:
    return " ".join(f'"{x}"' if " " in str(x) else str(x) for x in cmd)


def run_command(cmd: List[str], cwd: Optional[Path], stdout_path: Path, stderr_path: Path, label: str) -> subprocess.CompletedProcess:
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    with stdout_path.open("w", encoding="utf-8") as out_f, stderr_path.open("w", encoding="utf-8") as err_f:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=out_f,
            stderr=err_f,
            env=env,
        )
    if proc.returncode != 0:
        tail = ""
        if stderr_path.exists():
            tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        raise RuntimeError(
            f"{label} failed with exit code {proc.returncode}.\n"
            f"Command: {shell_join(cmd)}\n"
            f"Stdout: {stdout_path}\n"
            f"Stderr: {stderr_path}\n\n"
            f"Last stderr lines:\n{tail}"
        )
    return proc


def iter_dicts(obj: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from iter_dicts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from iter_dicts(v)


def sentence_split(text: str) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", text) if s.strip()]


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text or ""))


# ---------------------------------------------------------------------------
# Session loading
# ---------------------------------------------------------------------------


def load_submission(path: Path) -> Dict[str, Any]:
    submission = read_json(path, required=True)
    if not isinstance(submission, dict):
        raise ValueError("Submission JSON must be an object.")
    if not clean_text(submission.get("essay_text")):
        raise ValueError("Submission JSON must contain non-empty essay_text.")
    submission.setdefault("student_id", "anonymous")
    submission.setdefault("essay_id", str(uuid.uuid4()))
    submission.setdefault("task_type", "WT2")
    submission.setdefault("prompt_text", "")
    submission.setdefault("topic", "Submitted Essay")
    return submission


def load_premium_session(session_dir: Path) -> Dict[str, Any]:
    session: Dict[str, Any] = {"session_dir": str(session_dir)}
    for key, name in REQUIRED_PREMIUM_FILES.items():
        p = session_dir / name
        session[key] = read_json(p, default={}, required=False)
        session[f"{key}_path"] = str(p)
    for key, name in OPTIONAL_PREMIUM_FILES.items():
        p = session_dir / name
        if p.exists():
            session[key] = read_json(p, default={}, required=False)
            session[f"{key}_path"] = str(p)
    meta_path = session_dir / "session_meta.json"
    if meta_path.exists():
        session["session_meta"] = read_json(meta_path, default={})
    return session


def premium_session_missing_files(session_dir: Path) -> List[str]:
    return [name for name in REQUIRED_PREMIUM_FILES.values() if not (session_dir / name).exists()]


# ---------------------------------------------------------------------------
# Premium and Evaluator execution
# ---------------------------------------------------------------------------


def run_premium_pipeline(
    python_exe: str,
    premium_runner: Path,
    submission_path: Path,
    gold_dir: Path,
) -> Path:
    if not premium_runner.exists():
        raise FileNotFoundError(f"Premium runner not found: {premium_runner}")
    pipeline_dir = premium_runner.resolve().parent
    sessions_dir = pipeline_dir / "sessions"
    before = list_session_dirs(sessions_dir)
    cmd = [python_exe, str(premium_runner), "--input", str(submission_path)]
    run_command(
        cmd=cmd,
        cwd=pipeline_dir,
        stdout_path=gold_dir / "premium_pipeline_stdout.log",
        stderr_path=gold_dir / "premium_pipeline_stderr.log",
        label="Premium pipeline",
    )
    after = list_session_dirs(sessions_dir)
    session_dir = detect_new_session(before, after)
    if session_dir is None:
        raise RuntimeError("Premium pipeline completed but no session directory was detected.")
    return session_dir


def build_evaluator_request(
    submission: Dict[str, Any],
    premium_session: Dict[str, Any],
    prior_profile: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "student_id": str(submission.get("student_id") or "anonymous"),
        "essay_id": str(submission.get("essay_id") or "unknown"),
        "submission_id": first_non_empty(
            deep_get(premium_session.get("session_meta"), ["submission_id"]),
            stable_id("submission", submission.get("student_id"), submission.get("essay_id")),
        ),
        "prompt_text": submission.get("prompt_text", ""),
        "essay_text": submission.get("essay_text", ""),
        "detector_output": premium_session.get("detector") or None,
        "scorer_output": premium_session.get("score_contract") or premium_session.get("scorer") or None,
        "learner_history": prior_profile or {},
        "use_llm": False,
    }


def run_evaluator(
    python_exe: str,
    evaluator_runner: Path,
    request_path: Path,
    output_path: Path,
    gold_dir: Path,
    pretty: bool,
    no_llm: bool,
) -> Dict[str, Any]:
    if not evaluator_runner.exists():
        raise FileNotFoundError(f"Evaluator runner not found: {evaluator_runner}")
    cmd = [python_exe, str(evaluator_runner), "--input", str(request_path), "--output", str(output_path)]
    if pretty:
        cmd.append("--pretty")
    if no_llm:
        cmd.append("--no-llm")
    run_command(
        cmd=cmd,
        cwd=evaluator_runner.resolve().parent,
        stdout_path=gold_dir / "evaluator_stdout.log",
        stderr_path=gold_dir / "evaluator_stderr.log",
        label="Evaluator/WKE",
    )
    return read_json(output_path, default={})


# ---------------------------------------------------------------------------
# Evidence extraction
# ---------------------------------------------------------------------------


def normalize_family(row: Dict[str, Any]) -> Optional[str]:
    candidates = [
        row.get("error_family"), row.get("family"), row.get("issue_family"),
        row.get("rule_family"), row.get("category_family"), row.get("candidate_issue_family"),
    ]
    codes = row.get("candidate_issue_codes")
    if isinstance(codes, list) and codes:
        for c in codes:
            c = str(c or "")
            if c.startswith("G_") or c.startswith("L_") or c.startswith("A_") or c.startswith("C_") or c.startswith("TR_"):
                # Prefer the explicit normalized family elsewhere, but keep code fallback.
                candidates.append(c)
    for c in candidates:
        if not c:
            continue
        s = str(c).strip()
        if not s:
            continue
        s = s.replace("G_", "", 1) if s.startswith("G_") else s
        s = s.replace("L_", "", 1) if s.startswith("L_") else s
        s = s.replace("A_", "", 1) if s.startswith("A_") else s
        s = s.replace("C_", "", 1) if s.startswith("C_") else s
        s = s.replace("TR_", "", 1) if s.startswith("TR_") else s
        return re.sub(r"[^A-Z0-9]+", "_", s.upper()).strip("_")
    return None


def normalize_category(row: Dict[str, Any], family: Optional[str] = None) -> str:
    for key in ("category", "rubric", "criterion", "ielts_criterion"):
        if row.get(key):
            return str(row[key]).strip().lower()
    if family in FAMILY_TO_CAPACITY:
        cap = FAMILY_TO_CAPACITY[family]
        if cap == "sentence_control":
            return "grammar"
        if cap == "lexical_precision":
            return "lexical_resource"
        if cap in {"cohesion_control", "paragraph_logic"}:
            return "cohesion"
        if cap == "argument_development":
            return "argumentation"
        if cap == "task_response_control":
            return "task_response"
    return "unknown"


def extract_error_rows(detector: Dict[str, Any], errormap: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    for source_name, obj in (("detector", detector), ("errormap", errormap)):
        for d in iter_dicts(obj):
            family = normalize_family(d)
            quote = first_non_empty(
                d.get("surface_quote"), d.get("span_text"), d.get("expanded_quote"),
                d.get("quote"), d.get("text"), default=""
            )
            # Avoid treating global metadata dictionaries as error rows.
            has_error_shape = bool(family or d.get("candidate_issue_codes") or d.get("rationale") or d.get("severity"))
            if not has_error_shape:
                continue
            if not family and not quote:
                continue
            row_id = first_non_empty(
                d.get("detection_id"), d.get("error_id"), d.get("cluster_id"),
                stable_id("err", source_name, family, quote, d.get("sentence_index"), d.get("start")),
            )
            if row_id in seen:
                continue
            seen.add(row_id)
            confidence = safe_float(d.get("confidence"), default=None)
            rows.append({
                "id": row_id,
                "source": source_name,
                "family": family or "UNKNOWN",
                "category": normalize_category(d, family),
                "quote": clean_text(quote)[:240],
                "rationale": clean_text(d.get("rationale") or d.get("message") or d.get("explanation"))[:500],
                "severity": str(d.get("severity") or "unknown"),
                "confidence": confidence,
                "sentence_index": d.get("sentence_index"),
                "paragraph_index": d.get("paragraph_index"),
                "start": d.get("start"),
                "end": d.get("end"),
            })
    return rows


def extract_score_summary(score_contract: Dict[str, Any]) -> Dict[str, Any]:
    final = score_contract.get("final_score") if isinstance(score_contract.get("final_score"), dict) else score_contract
    criteria = first_non_empty(
        final.get("criteria_bands") if isinstance(final, dict) else None,
        score_contract.get("criteria_bands"),
        score_contract.get("criterion_bands"),
        default={},
    )
    overall = first_non_empty(
        final.get("overall_band") if isinstance(final, dict) else None,
        score_contract.get("overall_band"),
        score_contract.get("band"),
        default=None,
    )
    return {
        "overall_band": overall,
        "criteria_bands": criteria if isinstance(criteria, dict) else {},
        "score_confidence": score_contract.get("score_confidence", "unknown"),
        "student_score_release": bool(score_contract.get("student_score_release", True)),
        "progress_tracking_allowed": bool(score_contract.get("progress_tracking_allowed", False)),
        "lie_update_allowed": bool(score_contract.get("lie_update_allowed", False)),
        "adjudication_status": score_contract.get("adjudication_status", "unknown"),
        "score_audit": score_contract.get("score_audit", {}),
    }


def extract_evaluator_observations(evaluator: Dict[str, Any]) -> List[Dict[str, Any]]:
    obs = evaluator.get("skill_observation_profile")
    if isinstance(obs, list):
        return [o for o in obs if isinstance(o, dict)]
    obs = deep_get(evaluator, ["writing_skill_profile", "observations"], [])
    if isinstance(obs, list):
        return [o for o in obs if isinstance(o, dict)]
    return []


def observation_skill_id(o: Dict[str, Any]) -> str:
    return str(first_non_empty(o.get("skill_id"), o.get("id"), o.get("skill"), default="unknown_skill"))


def observation_skill_name(o: Dict[str, Any]) -> str:
    return str(first_non_empty(o.get("skill_name"), o.get("name"), o.get("skill"), observation_skill_id(o)))


def observation_domain(o: Dict[str, Any]) -> str:
    return str(first_non_empty(o.get("domain"), o.get("dimension"), o.get("bucket"), default="unknown"))


def observation_priority(o: Dict[str, Any]) -> float:
    return clamp(first_non_empty(o.get("priority_index"), o.get("priority"), o.get("gap_score"), default=0.0))


def map_observation_to_capacity(o: Dict[str, Any]) -> str:
    sid = observation_skill_id(o).lower()
    domain = observation_domain(o).lower()
    name = observation_skill_name(o).lower()
    text = f"{sid} {domain} {name}"
    if any(k in text for k in ["article", "agreement", "verb", "clause", "sentence", "grammar", "comparison", "preposition"]):
        return "sentence_control"
    if any(k in text for k in ["lexical", "vocab", "collocation", "word", "phrase", "precision", "style"]):
        return "lexical_precision"
    if any(k in text for k in ["argument", "claim", "support", "reason", "example", "rebuttal", "counter"]):
        return "argument_development"
    if any(k in text for k in ["paragraph", "organization", "topic_sentence", "progression"]):
        return "paragraph_logic"
    if any(k in text for k in ["cohesion", "reference", "transition", "flow", "connector"]):
        return "cohesion_control"
    if any(k in text for k in ["task", "prompt", "position", "response"]):
        return "task_response_control"
    return "argument_development"


def build_capacity_profile(error_rows: List[Dict[str, Any]], observations: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_capacity_errors: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in error_rows:
        cap = FAMILY_TO_CAPACITY.get(row.get("family"), None)
        if cap:
            by_capacity_errors[cap].append(row)

    by_capacity_obs: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for o in observations:
        by_capacity_obs[map_observation_to_capacity(o)].append(o)

    profile: Dict[str, Any] = {}
    all_caps = list(CAPACITY_LABELS.keys())
    for cap in all_caps:
        errors = by_capacity_errors.get(cap, [])
        obs = by_capacity_obs.get(cap, [])
        error_n = len(errors)
        high_priority = max([observation_priority(o) for o in obs] + [0.0])
        status_counts = Counter(str(o.get("status") or "unknown") for o in obs)
        strength_count = sum(1 for o in obs if str(o.get("skill_signal")) == "current_strength" or str(o.get("status")) == "observed")
        target_count = sum(1 for o in obs if str(o.get("skill_signal")) in {"development_target", "monitor"} or observation_priority(o) > 0.15)

        if error_n >= 8 or high_priority >= 0.70 or status_counts.get("not_observed", 0) >= 4:
            level = "weak"
            learning_status = "active_bottleneck"
        elif error_n >= 4 or high_priority >= 0.40 or target_count >= 2:
            level = "developing"
            learning_status = "needs_targeted_training"
        elif error_n >= 1 or high_priority >= 0.15:
            level = "emerging"
            learning_status = "monitor"
        elif strength_count >= 2:
            level = "functional"
            learning_status = "current_strength"
        else:
            level = "not_enough_evidence"
            learning_status = "observe_more"

        evidence = []
        for row in errors[:8]:
            evidence.append({
                "type": "error_pattern",
                "family": row.get("family"),
                "quote": row.get("quote"),
                "confidence": row.get("confidence"),
            })
        for o in sorted(obs, key=observation_priority, reverse=True)[:6]:
            evidence.append({
                "type": "skill_observation",
                "skill_id": observation_skill_id(o),
                "skill_name": observation_skill_name(o),
                "status": o.get("status"),
                "skill_signal": o.get("skill_signal"),
                "priority_index": observation_priority(o),
                "confidence": o.get("diagnostic_confidence"),
            })

        profile[cap] = {
            "label": CAPACITY_LABELS[cap],
            "level": level,
            "learning_status": learning_status,
            "error_count": error_n,
            "skill_observation_count": len(obs),
            "highest_priority_index": round(high_priority, 3),
            "status_counts": dict(status_counts),
            "evidence": evidence,
        }
    return profile


def choose_main_bottleneck(capacity_profile: Dict[str, Any]) -> Dict[str, Any]:
    def score_item(item: Tuple[str, Dict[str, Any]]) -> Tuple[float, int, str]:
        cap, data = item
        level_weight = {
            "weak": 4.0,
            "developing": 3.0,
            "emerging": 1.8,
            "functional": 0.5,
            "not_enough_evidence": 0.0,
        }.get(data.get("level"), 0.0)
        # Sentence control comes first when it blocks recoverability.
        foundational = 0.5 if cap == "sentence_control" else 0.2 if cap in {"lexical_precision", "argument_development"} else 0.0
        return (level_weight + foundational + float(data.get("highest_priority_index", 0.0)), int(data.get("error_count", 0)), cap)

    if not capacity_profile:
        return {"capacity": "unknown", "skill_name": "Unknown", "reason": "No learning evidence was available."}
    cap, data = sorted(capacity_profile.items(), key=score_item, reverse=True)[0]
    evidence_bits = []
    if data.get("error_count"):
        evidence_bits.append(f"{data.get('error_count')} related error signal(s)")
    if data.get("highest_priority_index", 0.0) > 0:
        evidence_bits.append(f"priority index {data.get('highest_priority_index')}")
    reason = "; ".join(evidence_bits) if evidence_bits else "This is the clearest available learning target."
    root = "local language control" if cap in {"sentence_control", "lexical_precision"} else "discourse/idea execution"
    secondary = []
    if cap == "sentence_control":
        secondary = ["lower semantic recoverability", "less reliable argument evaluation", "weaker revision control"]
    elif cap == "lexical_precision":
        secondary = ["less natural expression", "lower clarity", "weaker academic style"]
    elif cap == "argument_development":
        secondary = ["underdeveloped support", "weaker paragraph persuasiveness"]
    elif cap == "paragraph_logic":
        secondary = ["unclear progression", "weaker reader guidance"]
    return {
        "capacity": cap,
        "skill_id": stable_id("gold_skill", cap),
        "skill_name": CAPACITY_LABELS.get(cap, cap),
        "reason": reason,
        "root_cause": root,
        "secondary_effects": secondary,
        "recommended_service": CAPACITY_TO_SERVICE.get(cap, "practice"),
    }


def choose_next_best_skill(capacity_profile: Dict[str, Any], observations: List[Dict[str, Any]], bottleneck: Dict[str, Any]) -> Dict[str, Any]:
    target_cap = bottleneck.get("capacity") or "sentence_control"
    cap_obs = [o for o in observations if map_observation_to_capacity(o) == target_cap]
    cap_obs = sorted(cap_obs, key=observation_priority, reverse=True)
    if cap_obs and observation_priority(cap_obs[0]) > 0:
        top = cap_obs[0]
        return {
            "skill_id": observation_skill_id(top),
            "skill_name": observation_skill_name(top),
            "capacity": target_cap,
            "why_now": f"It is the highest-priority observable skill inside the current bottleneck ({CAPACITY_LABELS.get(target_cap, target_cap)}).",
            "recommended_service": CAPACITY_TO_SERVICE.get(target_cap, "practice"),
            "priority_index": observation_priority(top),
        }
    return {
        "skill_id": stable_id("gold_skill", target_cap),
        "skill_name": CAPACITY_LABELS.get(target_cap, target_cap),
        "capacity": target_cap,
        "why_now": "This capacity is the current bottleneck based on combined Premium and Evaluator evidence.",
        "recommended_service": CAPACITY_TO_SERVICE.get(target_cap, "practice"),
        "priority_index": capacity_profile.get(target_cap, {}).get("highest_priority_index", 0.0),
    }


# ---------------------------------------------------------------------------
# Gold evidence fusion and feedback
# ---------------------------------------------------------------------------


def build_gold_evidence_fusion(
    submission: Dict[str, Any],
    premium_session_dir: Path,
    premium_session: Dict[str, Any],
    evaluator: Dict[str, Any],
    prior_profile: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    score_summary = extract_score_summary(premium_session.get("score_contract", {}))
    error_rows = extract_error_rows(premium_session.get("detector", {}), premium_session.get("errormap", {}))
    observations = extract_evaluator_observations(evaluator)
    capacity_profile = build_capacity_profile(error_rows, observations)
    bottleneck = choose_main_bottleneck(capacity_profile)
    next_skill = choose_next_best_skill(capacity_profile, observations, bottleneck)
    error_counts_by_family = Counter(row.get("family") for row in error_rows)
    error_counts_by_category = Counter(row.get("category") for row in error_rows)
    consumer = evaluator.get("consumer_payloads") if isinstance(evaluator.get("consumer_payloads"), dict) else {}

    fusion = {
        "schema_version": "GOLD_EVIDENCE_FUSION_V1",
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "identity": {
            "student_id": str(submission.get("student_id") or "anonymous"),
            "essay_id": str(submission.get("essay_id") or "unknown"),
            "task_type": submission.get("task_type", "WT2"),
            "topic": submission.get("topic", "Submitted Essay"),
            "premium_session_dir": str(premium_session_dir),
        },
        "performance_evidence": {
            "source": "final_score_contract",
            **score_summary,
            "stable_for_trend": bool(score_summary.get("progress_tracking_allowed")),
        },
        "error_pattern_evidence": {
            "source": "detector_errormap",
            "chargeable_errors": error_rows[:300],
            "error_count": len(error_rows),
            "counts_by_family": dict(error_counts_by_family),
            "counts_by_category": dict(error_counts_by_category),
            "top_families": [
                {"family": fam, "count": count, "capacity": FAMILY_TO_CAPACITY.get(fam, "unknown")}
                for fam, count in error_counts_by_family.most_common(12)
            ],
        },
        "skill_capacity_evidence": {
            "source": "evaluator_wke",
            "capacity_profile": capacity_profile,
            "skill_observations": observations[:250],
            "observation_count": len(observations),
            "current_strengths": [
                o for o in observations
                if str(o.get("skill_signal")) == "current_strength" or str(o.get("status")) == "observed"
            ][:30],
            "development_targets": sorted(
                [o for o in observations if observation_priority(o) > 0 or str(o.get("skill_signal")) in {"development_target", "monitor"}],
                key=observation_priority,
                reverse=True,
            )[:50],
            "requires_practice_evidence": [o for o in observations if str(o.get("status")) == "requires_practice_evidence"][:40],
            "requires_revision_evidence": [o for o in observations if str(o.get("status")) == "requires_revision_evidence"][:40],
            "consumer_payloads_present": sorted(list(consumer.keys())),
        },
        "learning_behavior_evidence": {
            "source": "practice_writing_coach_lret_revision",
            "prior_profile_loaded": bool(prior_profile),
            "prior_profile_version": (prior_profile or {}).get("profile_version"),
            "prior_mission_count": len(deep_get(prior_profile or {}, ["writing_coach_profile", "mission_history"], []) or []),
            "prior_lret_units": len(deep_get(prior_profile or {}, ["lexical_profile", "fix_units"], []) or []),
        },
        "main_learning_bottleneck": bottleneck,
        "next_best_skill": next_skill,
        "gold_learning_directive": {
            "next_best_skill": next_skill,
            "blocked_by": [bottleneck.get("capacity")],
            "recommended_services": list(dict.fromkeys([
                next_skill.get("recommended_service"),
                CAPACITY_TO_SERVICE.get(bottleneck.get("capacity"), "practice"),
                "practice",
            ])),
            "learning_update_allowed": True,
            "mastery_update_allowed": False,
            "reason": "Gold essay observations update learning evidence; stable mastery requires repeated essay/practice/revision/coach evidence.",
        },
    }
    return fusion


def build_gold_feedback(fusion: Dict[str, Any]) -> Dict[str, Any]:
    perf = fusion.get("performance_evidence", {})
    capacity = deep_get(fusion, ["skill_capacity_evidence", "capacity_profile"], {}) or {}
    bottleneck = fusion.get("main_learning_bottleneck", {})
    next_skill = fusion.get("next_best_skill", {})
    strengths = []
    emerging = []
    not_stable = []
    for cap, data in capacity.items():
        row = {
            "capacity": cap,
            "label": data.get("label", cap),
            "level": data.get("level"),
            "evidence_count": len(data.get("evidence", [])),
        }
        if data.get("level") == "functional":
            strengths.append(row)
        elif data.get("level") in {"emerging", "developing"}:
            emerging.append(row)
        elif data.get("level") == "not_enough_evidence":
            not_stable.append(row)

    target_cap = next_skill.get("capacity") or bottleneck.get("capacity") or "sentence_control"
    week_focus = CAPACITY_TO_WEEK_PLAN.get(target_cap, CAPACITY_TO_WEEK_PLAN["sentence_control"])
    learning_plan = [
        {"week": idx + 1, "focus": focus, "service": CAPACITY_TO_SERVICE.get(target_cap, "practice")}
        for idx, focus in enumerate(week_focus)
    ]
    return {
        "schema_version": "GOLD_FEEDBACK_REPORT_V1",
        "engine_id": "GOLD_FEEDBACK_LAYER",
        "created_at": now_iso(),
        "identity": fusion.get("identity", {}),
        "performance_summary": {
            "overall_band": perf.get("overall_band"),
            "criteria_bands": perf.get("criteria_bands", {}),
            "score_confidence": perf.get("score_confidence", "unknown"),
            "progress_tracking_allowed": perf.get("progress_tracking_allowed", False),
        },
        "writing_capacity_profile": capacity,
        "main_learning_bottleneck": bottleneck,
        "strength_profile": {
            "safe_strengths": strengths,
            "emerging_strengths": emerging,
            "not_yet_stable": not_stable,
        },
        "next_best_skill": next_skill,
        "learning_plan": learning_plan,
        "student_summary": {
            "result": f"Your current estimated score is {perf.get('overall_band', 'not available')}.",
            "main_bottleneck": (
                f"Your main learning bottleneck is {bottleneck.get('skill_name', 'the next writing skill')} "
                f"because {bottleneck.get('reason', 'this is the strongest evidence from the essay.')}"
            ),
            "today_action": (
                f"Work on {next_skill.get('skill_name', 'the next skill')} through "
                f"{next_skill.get('recommended_service', 'practice')} first."
            ),
        },
    }


# ---------------------------------------------------------------------------
# LRET deterministic fallback
# ---------------------------------------------------------------------------


def lexical_unit_text(unit: Dict[str, Any]) -> str:
    return clean_text(first_non_empty(unit.get("text"), unit.get("unit"), unit.get("span_text"), unit.get("surface"), default=""))


def meaningful_lexical_unit(text: str) -> bool:
    t = clean_text(text)
    if not t:
        return False
    words = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", t)
    if len(words) == 0:
        return False
    if len(words) == 1 and len(words[0]) < 5:
        return False
    for pat in MEANINGLESS_LEXICAL_PATTERNS:
        if pat.search(t):
            return False
    # Reject mostly function-word phrases.
    functionish = {"the", "a", "an", "of", "to", "for", "with", "and", "or", "but", "is", "are", "was", "were", "it", "there"}
    if words and sum(1 for w in words if w.lower() in functionish) / len(words) > 0.66:
        return False
    return True


def build_lret_session(fusion: Dict[str, Any], evaluator: Dict[str, Any]) -> Dict[str, Any]:
    lret_payload = deep_get(evaluator, ["consumer_payloads", "lret_payload"], {}) or {}
    units = lret_payload.get("lexical_units_for_lret") or deep_get(evaluator, ["lexical_unit_profile", "lexical_units_for_lret"], []) or []
    fix_candidates = lret_payload.get("fix_candidates") or []
    fix_texts = {clean_text(c.get("span_text") or c.get("text") or c.get("quote")).lower() for c in fix_candidates if isinstance(c, dict)}
    fix_families = {str(c.get("error_family") or c.get("family") or "").upper() for c in fix_candidates if isinstance(c, dict)}

    keep: List[Dict[str, Any]] = []
    enhance: List[Dict[str, Any]] = []
    fix: List[Dict[str, Any]] = []
    avoid: List[Dict[str, Any]] = []
    seen: set = set()

    for unit in units:
        if not isinstance(unit, dict):
            unit = {"text": str(unit)}
        text = lexical_unit_text(unit)
        key = text.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        if not meaningful_lexical_unit(text):
            avoid.append({"unit": text, "label": "AVOID", "reason": "low learning value / fragment", "source": "lret_fallback"})
            continue
        family = str(unit.get("error_family") or unit.get("family") or "").upper()
        base = {
            "unit": text,
            "source_unit": unit,
            "source": "lret_fallback_classifier",
            "lret_boundary": "classification_done_by_lret_not_evaluator",
        }
        if key in fix_texts or family in fix_families or any(f in family for f in ["COLLOCATION", "WORD_CHOICE", "WORD_FORM", "SEMANTIC", "SPELLING"]):
            row = {**base, "label": "FIX", "reason": "linked to lexical error/fix evidence"}
            fix.append(row)
        elif key in WEAK_WORDS or any(w in key.split() for w in WEAK_WORDS):
            row = {**base, "label": "ENHANCE", "reason": "meaningful but could be more precise or academic"}
            enhance.append(row)
        elif len(text.split()) >= 2:
            row = {**base, "label": "KEEP", "reason": "meaningful reusable phrase with no direct fix signal"}
            keep.append(row)
        else:
            enhance.append({**base, "label": "ENHANCE", "reason": "meaningful single word; check precision in context"})

    # Add detector-derived lexical fix candidates that were not in evaluator lexical unit list.
    for c in fix_candidates:
        if not isinstance(c, dict):
            continue
        text = clean_text(c.get("span_text") or c.get("text") or c.get("quote"))
        if text and meaningful_lexical_unit(text) and text.lower() not in seen:
            seen.add(text.lower())
            fix.append({
                "unit": text,
                "label": "FIX",
                "reason": "detector/error-map lexical fix candidate",
                "source_candidate": c,
                "source": "lret_fallback_classifier",
            })

    return {
        "schema_version": "GOLD_LRET_SESSION_V1",
        "engine_id": "GOLD_LRET_FALLBACK_CLASSIFIER",
        "created_at": now_iso(),
        "identity": fusion.get("identity", {}),
        "classification_policy": {
            "evaluator_extracts_units_only": True,
            "lret_owns_keep_fix_enhance_labels": True,
            "meaningless_fragments_excluded": True,
        },
        "keep_candidates": keep[:60],
        "enhance_candidates": enhance[:80],
        "fix_candidates": fix[:80],
        "avoid_candidates": avoid[:80],
        "summary": {
            "keep": len(keep),
            "enhance": len(enhance),
            "fix": len(fix),
            "avoid": len(avoid),
        },
        "lie_event": {
            "event_type": "lret_session_generated",
            "units_available": len(keep) + len(enhance) + len(fix),
            "mastery_update_allowed": False,
            "reason": "Session generation is prescription evidence only; mastery requires learner action/reuse.",
        },
    }


# ---------------------------------------------------------------------------
# Writing Coach fallback and optional execution
# ---------------------------------------------------------------------------


def build_writing_coach_fallback(fusion: Dict[str, Any], gold_feedback: Dict[str, Any]) -> Dict[str, Any]:
    next_skill = fusion.get("next_best_skill", {})
    cap = next_skill.get("capacity") or "sentence_control"
    mission_id = stable_id("wc_mission", fusion.get("identity", {}).get("student_id"), fusion.get("identity", {}).get("essay_id"), next_skill.get("skill_id"))
    move_by_cap = {
        "sentence_control": "Write two clear cause-effect sentences with one subject and one main verb in each clause.",
        "lexical_precision": "Replace three weak phrases with more precise academic phrases and reuse one in a new sentence.",
        "argument_development": "Write one claim, one reason, and one specific example as a mini-argument.",
        "paragraph_logic": "Write a topic sentence and two supporting sentences that stay on the same idea.",
        "cohesion_control": "Connect three sentences with clear reference words and one logical transition.",
        "task_response_control": "Write a one-sentence position and list the exact parts of the question it answers.",
        "style_control": "Rewrite two informal sentences in a more academic tone.",
    }
    return {
        "schema_version": "WRITING_COACH_OUTPUT_V1_2_3_COMPAT_GOLD_FALLBACK",
        "engine_id": "GOLD_WRITING_COACH_FALLBACK",
        "created_at": now_iso(),
        "identity": fusion.get("identity", {}),
        "run_status": "fallback_generated_no_external_move_bank",
        "today_mission": {
            "mission_id": mission_id,
            "title": f"Gold Writing Move: {next_skill.get('skill_name', CAPACITY_LABELS.get(cap, cap))}",
            "target_skill_id": next_skill.get("skill_id"),
            "target_skill_name": next_skill.get("skill_name"),
            "capacity": cap,
            "student_goal": move_by_cap.get(cap, move_by_cap["sentence_control"]),
            "timebox_minutes": 10,
            "success_checklist": [
                "The answer is complete, not a fragment.",
                "The meaning is clear without explanation from the teacher.",
                "The target skill is visible in the response.",
            ],
            "mastery_policy": {
                "assignment_updates_mastery": False,
                "mastery_requires_valid_attempt": True,
            },
        },
        "coach_state_export": {
            "active_skill_id": next_skill.get("skill_id"),
            "active_skill_name": next_skill.get("skill_name"),
            "active_capacity": cap,
            "active_coaching_cycle": {
                "status": "active",
                "mission_id": mission_id,
                "started_at": now_iso(),
            },
        },
        "lie_event": {
            "event_type": "writing_coach_mission_assigned",
            "mission_id": mission_id,
            "skill_id": next_skill.get("skill_id"),
            "mastery_update_allowed": False,
        },
    }


def run_writing_coach_optional(
    python_exe: str,
    runner: Optional[Path],
    move_bank: Optional[Path],
    ontology: Optional[Path],
    clusters: Optional[Path],
    coach_state: Optional[Path],
    last_mission_result: Optional[Path],
    premium_session: Dict[str, Any],
    evaluator_path: Path,
    gold_dir: Path,
    pretty: bool,
    fusion: Dict[str, Any],
    gold_feedback: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    output_path = gold_dir / GOLD_ARTIFACTS["writing_coach"]
    state_path = gold_dir / GOLD_ARTIFACTS["writing_coach_state"]
    if runner and runner.exists() and move_bank and move_bank.exists():
        cmd = [
            python_exe, str(runner),
            "--mode", "after_essay",
            "--move-bank", str(move_bank),
            "--evaluator", str(evaluator_path),
            "--intake", premium_session.get("intake_path", ""),
            "--detector", premium_session.get("detector_path", ""),
            "--errormap", premium_session.get("errormap_path", ""),
            "--score-contract", premium_session.get("score_contract_path", ""),
            "--priority", premium_session.get("priority_path", ""),
            "--directive", premium_session.get("directive_path", ""),
            "--feedback", premium_session.get("feedback_engine_path", ""),
            "--feedback-report", premium_session.get("feedback_report_path", ""),
            "--output", str(output_path),
            "--state-output", str(state_path),
        ]
        if ontology and ontology.exists():
            cmd += ["--ontology", str(ontology)]
        if clusters and clusters.exists():
            cmd += ["--clusters", str(clusters)]
        if coach_state and coach_state.exists():
            cmd += ["--coach-state", str(coach_state)]
        if last_mission_result and last_mission_result.exists():
            cmd += ["--last-mission-result", str(last_mission_result)]
        if pretty:
            cmd.append("--pretty")
        try:
            run_command(
                cmd=cmd,
                cwd=runner.resolve().parent,
                stdout_path=gold_dir / "writing_coach_stdout.log",
                stderr_path=gold_dir / "writing_coach_stderr.log",
                label="Writing Coach",
            )
            wc = read_json(output_path, default={})
            state = read_json(state_path, default=wc.get("coach_state_export", {}))
            return wc, state
        except Exception as exc:
            fallback = build_writing_coach_fallback(fusion, gold_feedback)
            fallback["external_runner_error"] = str(exc)
            write_json(output_path, fallback, pretty)
            write_json(state_path, fallback.get("coach_state_export", {}), pretty)
            return fallback, fallback.get("coach_state_export", {})

    fallback = build_writing_coach_fallback(fusion, gold_feedback)
    missing = []
    if not runner:
        missing.append("writing_coach_runner_not_provided")
    elif not runner.exists():
        missing.append(f"writing_coach_runner_missing:{runner}")
    if not move_bank:
        missing.append("move_bank_not_provided")
    elif not move_bank.exists():
        missing.append(f"move_bank_missing:{move_bank}")
    fallback["fallback_reasons"] = missing
    write_json(output_path, fallback, pretty)
    write_json(state_path, fallback.get("coach_state_export", {}), pretty)
    return fallback, fallback.get("coach_state_export", {})


# ---------------------------------------------------------------------------
# Practice directive, LIE update, service router
# ---------------------------------------------------------------------------


def build_gold_practice_directive(fusion: Dict[str, Any], lret_session: Dict[str, Any], writing_coach: Dict[str, Any]) -> Dict[str, Any]:
    next_skill = fusion.get("next_best_skill", {})
    bottleneck = fusion.get("main_learning_bottleneck", {})
    cap = next_skill.get("capacity") or bottleneck.get("capacity") or "sentence_control"
    lret_focus = []
    for key in ("fix_candidates", "enhance_candidates"):
        for row in lret_session.get(key, [])[:5]:
            lret_focus.append({"unit": row.get("unit"), "label": row.get("label"), "reason": row.get("reason")})
    return {
        "schema_version": "GOLD_PRACTICE_DIRECTIVE_V1",
        "created_at": now_iso(),
        "identity": fusion.get("identity", {}),
        "priority_hierarchy": [
            "recoverability_and_sentence_control",
            "recurring_root_cause_error_families",
            "evaluator_development_targets",
            "learner_history_recurring_weaknesses",
            "writing_coach_active_skill",
            "lret_lexical_targets",
            "ielts_criterion_target",
            "maintenance_review",
        ],
        "primary_capacity": cap,
        "next_best_skill": next_skill,
        "recommended_exercise_families": [
            fam for fam, count in [
                (x.get("family"), x.get("count")) for x in deep_get(fusion, ["error_pattern_evidence", "top_families"], [])
            ] if fam
        ][:8],
        "lret_focus_units": lret_focus,
        "writing_coach_mission": deep_get(writing_coach, ["today_mission"], {}),
        "mastery_update_policy": {
            "practice_generation_updates_mastery": False,
            "practice_result_required": True,
        },
    }


def default_profile(student_id: str) -> Dict[str, Any]:
    return {
        "student_id": student_id,
        "profile_version": "gold_lie_v1",
        "created_at": now_iso(),
        "last_updated_at": now_iso(),
        "performance_profile": {
            "latest_overall_band": None,
            "stable_band_trend": [],
            "criterion_trends": {},
            "score_confidence_history": [],
        },
        "error_pattern_profile": {
            "recurring_families": [],
            "family_counts_total": {},
            "resolved_families": [],
            "new_families": [],
            "root_cause_chains": [],
        },
        "skill_capacity_profile": {},
        "microskill_mastery": {},
        "lexical_profile": {
            "keep_units": [],
            "enhance_units": [],
            "fix_units": [],
            "reused_successfully": [],
            "recurring_lexical_errors": [],
        },
        "revision_profile": {
            "revision_count": 0,
            "valid_revision_count": 0,
            "average_error_reduction": None,
            "common_revision_failures": [],
            "best_revision_skills": [],
        },
        "practice_profile": {
            "attempts": [],
            "accuracy_by_skill": {},
            "review_due": [],
            "blocked_skills": [],
        },
        "writing_coach_profile": {
            "active_cycle": {},
            "mission_history": [],
            "functional_skills": [],
            "stable_skills": [],
        },
        "session_history": [],
        "next_best_action": {},
    }


def append_unique_units(existing: List[Dict[str, Any]], new_rows: List[Dict[str, Any]], limit: int = 300) -> List[Dict[str, Any]]:
    by_key: Dict[str, Dict[str, Any]] = {}
    for row in existing or []:
        if not isinstance(row, dict):
            continue
        key = clean_text(row.get("unit") or row.get("text")).lower()
        if key:
            by_key[key] = row
    for row in new_rows or []:
        if not isinstance(row, dict):
            continue
        key = clean_text(row.get("unit") or row.get("text")).lower()
        if key and key not in by_key:
            by_key[key] = {
                "unit": row.get("unit") or row.get("text"),
                "first_seen_at": now_iso(),
                "latest_label": row.get("label"),
                "source": row.get("source"),
            }
        elif key:
            by_key[key]["latest_seen_at"] = now_iso()
            by_key[key]["latest_label"] = row.get("label", by_key[key].get("latest_label"))
    return list(by_key.values())[-limit:]


def update_gold_lie_profile(
    prior_profile: Optional[Dict[str, Any]],
    fusion: Dict[str, Any],
    gold_feedback: Dict[str, Any],
    lret_session: Dict[str, Any],
    writing_coach: Dict[str, Any],
    practice_directive: Dict[str, Any],
) -> Dict[str, Any]:
    student_id = deep_get(fusion, ["identity", "student_id"], "anonymous")
    profile = copy.deepcopy(prior_profile) if isinstance(prior_profile, dict) and prior_profile else default_profile(student_id)
    profile.setdefault("student_id", student_id)
    profile.setdefault("profile_version", "gold_lie_v1")
    profile["last_updated_at"] = now_iso()

    perf = fusion.get("performance_evidence", {})
    profile.setdefault("performance_profile", {})
    profile["performance_profile"]["latest_overall_band"] = perf.get("overall_band")
    profile["performance_profile"].setdefault("score_confidence_history", []).append({
        "essay_id": deep_get(fusion, ["identity", "essay_id"]),
        "overall_band": perf.get("overall_band"),
        "score_confidence": perf.get("score_confidence"),
        "progress_tracking_allowed": perf.get("progress_tracking_allowed"),
        "created_at": now_iso(),
    })
    profile["performance_profile"]["score_confidence_history"] = profile["performance_profile"]["score_confidence_history"][-100:]
    if perf.get("progress_tracking_allowed"):
        profile["performance_profile"].setdefault("stable_band_trend", []).append({
            "essay_id": deep_get(fusion, ["identity", "essay_id"]),
            "overall_band": perf.get("overall_band"),
            "criteria_bands": perf.get("criteria_bands", {}),
            "created_at": now_iso(),
        })
        profile["performance_profile"]["stable_band_trend"] = profile["performance_profile"]["stable_band_trend"][-100:]

    # Error pattern profile.
    epp = profile.setdefault("error_pattern_profile", {})
    total_counts = Counter(epp.get("family_counts_total", {}))
    current_counts = Counter(deep_get(fusion, ["error_pattern_evidence", "counts_by_family"], {}) or {})
    total_counts.update(current_counts)
    epp["family_counts_total"] = dict(total_counts)
    epp["new_families"] = [fam for fam in current_counts if fam not in set((epp.get("family_counts_total") or {}).keys())]
    epp["recurring_families"] = [
        {"family": fam, "total_count": count, "capacity": FAMILY_TO_CAPACITY.get(fam, "unknown")}
        for fam, count in total_counts.most_common(20)
        if count >= 2
    ]

    # Skill capacity snapshots.
    profile["skill_capacity_profile"] = gold_feedback.get("writing_capacity_profile", {})
    mastery = profile.setdefault("microskill_mastery", {})
    for o in deep_get(fusion, ["skill_capacity_evidence", "skill_observations"], []) or []:
        sid = observation_skill_id(o)
        if not sid or sid == "unknown_skill":
            continue
        row = mastery.setdefault(sid, {
            "skill_id": sid,
            "skill_name": observation_skill_name(o),
            "status": "building",
            "evidence_count": 0,
            "last_sources": [],
            "recent_scores": [],
            "stability": 0.0,
            "confidence": 0.0,
        })
        row["evidence_count"] = int(row.get("evidence_count", 0)) + 1
        row["skill_name"] = observation_skill_name(o)
        row["last_sources"] = list(dict.fromkeys((row.get("last_sources") or []) + ["essay_evaluator"]))[-8:]
        conf = safe_float(o.get("diagnostic_confidence"), 0.0) or 0.0
        row["confidence"] = round(max(float(row.get("confidence") or 0.0), conf), 3)
        # Essay observation alone cannot mark stable. It can show functional/current strength only cautiously.
        if row["evidence_count"] >= 3 and str(o.get("status")) == "observed" and conf >= 0.65:
            row["status"] = "functional"
        elif observation_priority(o) > 0.2:
            row["status"] = "building"

    # LRET profile.
    lex = profile.setdefault("lexical_profile", {})
    lex["keep_units"] = append_unique_units(lex.get("keep_units", []), lret_session.get("keep_candidates", []), limit=300)
    lex["enhance_units"] = append_unique_units(lex.get("enhance_units", []), lret_session.get("enhance_candidates", []), limit=300)
    lex["fix_units"] = append_unique_units(lex.get("fix_units", []), lret_session.get("fix_candidates", []), limit=300)

    # Writing Coach state: assignment only, no mastery.
    wc_profile = profile.setdefault("writing_coach_profile", {})
    mission = writing_coach.get("today_mission") or writing_coach.get("mission") or {}
    if mission:
        wc_profile["active_cycle"] = {
            "mission_id": mission.get("mission_id"),
            "skill_id": mission.get("target_skill_id") or deep_get(fusion, ["next_best_skill", "skill_id"]),
            "skill_name": mission.get("target_skill_name") or deep_get(fusion, ["next_best_skill", "skill_name"]),
            "capacity": mission.get("capacity") or deep_get(fusion, ["next_best_skill", "capacity"]),
            "status": "assigned",
            "mastery_update_allowed": False,
            "assigned_at": now_iso(),
        }

    profile["next_best_action"] = {
        "service": deep_get(fusion, ["next_best_skill", "recommended_service"], "practice"),
        "skill_id": deep_get(fusion, ["next_best_skill", "skill_id"]),
        "skill_name": deep_get(fusion, ["next_best_skill", "skill_name"]),
        "reason": deep_get(fusion, ["next_best_skill", "why_now"]),
    }

    profile.setdefault("session_history", []).append({
        "essay_id": deep_get(fusion, ["identity", "essay_id"]),
        "premium_session_dir": deep_get(fusion, ["identity", "premium_session_dir"]),
        "overall_band": perf.get("overall_band"),
        "score_confidence": perf.get("score_confidence"),
        "main_bottleneck": fusion.get("main_learning_bottleneck"),
        "next_best_skill": fusion.get("next_best_skill"),
        "created_at": now_iso(),
    })
    profile["session_history"] = profile["session_history"][-100:]
    return profile


def build_skills_progress_report(profile: Dict[str, Any]) -> Dict[str, Any]:
    mastery = profile.get("microskill_mastery", {}) or {}
    status_counts = Counter(row.get("status", "unknown") for row in mastery.values() if isinstance(row, dict))
    sorted_skills = sorted(
        [row for row in mastery.values() if isinstance(row, dict)],
        key=lambda r: (r.get("status") != "building", -(r.get("evidence_count") or 0), -(r.get("confidence") or 0.0)),
    )
    return {
        "schema_version": "GOLD_SKILLS_PROGRESS_REPORT_V1",
        "created_at": now_iso(),
        "student_id": profile.get("student_id"),
        "status_counts": dict(status_counts),
        "active_development_skills": [r for r in sorted_skills if r.get("status") == "building"][:20],
        "functional_skills": [r for r in sorted_skills if r.get("status") == "functional"][:20],
        "stable_skills": [r for r in sorted_skills if r.get("status") == "stable"][:20],
        "note": "Essay observations can support functional status after repeated evidence; stable mastery requires repeated cross-source evidence.",
    }


def build_learning_roadmap(gold_feedback: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
    next_action = profile.get("next_best_action", {})
    return {
        "schema_version": "GOLD_LEARNING_ROADMAP_V1",
        "created_at": now_iso(),
        "student_id": profile.get("student_id"),
        "next_best_action": next_action,
        "four_week_plan": gold_feedback.get("learning_plan", []),
        "maintenance": {
            "review_due": deep_get(profile, ["practice_profile", "review_due"], []),
            "lexical_units_to_reuse": (deep_get(profile, ["lexical_profile", "enhance_units"], []) or [])[:10],
        },
    }


def build_service_router(fusion: Dict[str, Any], lret_session: Dict[str, Any], writing_coach: Dict[str, Any]) -> Dict[str, Any]:
    next_skill = fusion.get("next_best_skill", {})
    cap = next_skill.get("capacity") or "sentence_control"
    primary = next_skill.get("recommended_service") or CAPACITY_TO_SERVICE.get(cap, "practice")
    # If LRET has no usable targets, avoid routing primary to LRET.
    if primary == "lret" and not (lret_session.get("fix_candidates") or lret_session.get("enhance_candidates")):
        primary = "practice"
    secondary = [s for s in ["writing_coach", "lret", "practice", "essay_revision"] if s != primary]
    not_now = []
    reason = next_skill.get("why_now", "This is the strongest available learning target.")
    if cap == "sentence_control":
        not_now.append("full_essay_revision_until_sentence_targets_are_clear")
    return {
        "schema_version": "GOLD_SERVICE_ROUTING_V1",
        "created_at": now_iso(),
        "identity": fusion.get("identity", {}),
        "primary_next_service": primary,
        "secondary_services": secondary[:3],
        "not_recommended_now": not_now,
        "reason": reason,
        "next_best_action": {
            "type": f"{primary}_task",
            "skill_id": next_skill.get("skill_id"),
            "skill_name": next_skill.get("skill_name"),
            "capacity": cap,
            "estimated_minutes": 10 if primary in {"writing_coach", "lret", "practice"} else 30,
        },
        "service_payload_refs": {
            "writing_coach_mission_id": deep_get(writing_coach, ["today_mission", "mission_id"]),
            "lret_fix_count": len(lret_session.get("fix_candidates", [])),
            "lret_enhance_count": len(lret_session.get("enhance_candidates", [])),
        },
    }


def build_progress_snapshot(profile: Dict[str, Any], fusion: Dict[str, Any]) -> Dict[str, Any]:
    perf = fusion.get("performance_evidence", {})
    return {
        "schema_version": "GOLD_PROGRESS_SNAPSHOT_V1",
        "created_at": now_iso(),
        "student_id": profile.get("student_id"),
        "latest_overall_band": perf.get("overall_band"),
        "score_confidence": perf.get("score_confidence"),
        "stable_score_points": len(deep_get(profile, ["performance_profile", "stable_band_trend"], []) or []),
        "tracked_microskills": len(profile.get("microskill_mastery", {}) or {}),
        "active_capacity_bottleneck": deep_get(fusion, ["main_learning_bottleneck", "capacity"]),
        "next_best_action": profile.get("next_best_action", {}),
    }


def build_revision_launch_packet(
    submission: Dict[str, Any],
    premium_session_dir: Path,
    evaluator_path: Path,
    gold_dir: Path,
    args: argparse.Namespace,
) -> Dict[str, Any]:
    return {
        "schema_version": "GOLD_REVISION_LAUNCH_PACKET_V1",
        "created_at": now_iso(),
        "status": "ready_when_student_submits_revised_essay",
        "identity": {
            "student_id": submission.get("student_id"),
            "essay_id": submission.get("essay_id"),
            "task_type": submission.get("task_type", "WT2"),
        },
        "original_session_dir": str(premium_session_dir),
        "original_evaluator_output": str(evaluator_path),
        "suggested_work_dir": str(gold_dir / "revision_work_dir"),
        "runner_expected": str(args.revision_runner) if args.revision_runner else None,
        "required_when_running_revision": [
            "--pipeline-runner",
            "--revision-engine",
            "--original-session-dir",
            "--work-dir",
            "--revised-essay-file or interactive paste",
            "--evaluator-runner",
        ],
        "note": "Revision is a second-stage learner action. This packet preserves the original Gold context for the revision runner.",
    }


# ---------------------------------------------------------------------------
# QA
# ---------------------------------------------------------------------------


def evaluator_has_forbidden_score_fields(evaluator: Dict[str, Any]) -> List[str]:
    forbidden = {"performance_score", "performance_band", "ielts_band", "criterion_score", "overall_band"}
    found = set()
    for d in iter_dicts(evaluator):
        for key in d.keys():
            if key in forbidden:
                found.add(key)
    return sorted(found)


def evaluator_has_lret_labels(evaluator: Dict[str, Any]) -> bool:
    bad = {"KEEP", "FIX", "ENHANCE", "AVOID"}
    for unit in deep_get(evaluator, ["lexical_unit_profile", "lexical_units_for_lret"], []) or []:
        if isinstance(unit, dict) and str(unit.get("label") or unit.get("lret_label") or "").upper() in bad:
            return True
    for unit in deep_get(evaluator, ["consumer_payloads", "lret_payload", "lexical_units_for_lret"], []) or []:
        if isinstance(unit, dict) and str(unit.get("label") or unit.get("lret_label") or "").upper() in bad:
            return True
    return False


def build_qa_report(
    premium_session_dir: Path,
    premium_session: Dict[str, Any],
    evaluator: Dict[str, Any],
    fusion: Dict[str, Any],
    gold_feedback: Dict[str, Any],
    lret_session: Dict[str, Any],
    writing_coach: Dict[str, Any],
    profile: Dict[str, Any],
) -> Dict[str, Any]:
    missing = premium_session_missing_files(premium_session_dir)
    forbidden_score_fields = evaluator_has_forbidden_score_fields(evaluator)
    evaluator_lret_leak = evaluator_has_lret_labels(evaluator)
    lret_meaningless_kept = []
    for key in ("keep_candidates", "enhance_candidates", "fix_candidates"):
        for row in lret_session.get(key, []):
            unit = clean_text(row.get("unit"))
            if unit and not meaningful_lexical_unit(unit):
                lret_meaningless_kept.append(unit)
    wc_mission = writing_coach.get("today_mission") or {}
    checks = [
        ("premium_pipeline_completed", not missing, f"missing={missing}"),
        ("final_score_contract_present", bool(premium_session.get("score_contract")), ""),
        ("evaluator_present", bool(evaluator), ""),
        ("evaluator_has_consumer_payloads", bool(evaluator.get("consumer_payloads")), ""),
        ("gold_evidence_fusion_present", bool(fusion), ""),
        ("gold_feedback_present", bool(gold_feedback), ""),
        ("lret_ran", bool(lret_session), ""),
        ("writing_coach_ran_or_fallback", bool(writing_coach), ""),
        ("lie_profile_updated", bool(profile), ""),
        ("no_evaluator_score_leak", not forbidden_score_fields, f"found={forbidden_score_fields}"),
        ("no_lret_label_leak_from_evaluator", not evaluator_lret_leak, ""),
        ("no_meaningless_lret_units_kept", not lret_meaningless_kept, f"examples={lret_meaningless_kept[:10]}"),
        ("no_mastery_update_from_mission_assignment", deep_get(writing_coach, ["lie_event", "mastery_update_allowed"], False) is False, ""),
        ("gold_feedback_separates_score_and_capacity", bool(gold_feedback.get("performance_summary")) and bool(gold_feedback.get("writing_capacity_profile")), ""),
        ("service_next_skill_present", bool(fusion.get("next_best_skill", {}).get("skill_id")), ""),
    ]
    qa_items = [{"label": label, "ok": bool(ok), "detail": detail} for label, ok, detail in checks]
    status = "passed" if all(item["ok"] for item in qa_items) else "failed"
    return {
        "schema_version": "QA_GOLD_REPORT_V1",
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "status": status,
        "items": qa_items,
        "critical_failures": [item for item in qa_items if not item["ok"]],
        "summary": {
            "passed": sum(1 for item in qa_items if item["ok"]),
            "failed": sum(1 for item in qa_items if not item["ok"]),
            "writing_coach_mission_id": wc_mission.get("mission_id"),
        },
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


@dataclass
class GoldRunResult:
    gold_dir: str
    premium_session_dir: str
    artifacts: Dict[str, str]
    qa_status: str
    next_best_action: Dict[str, Any]


class GoldPipelineRunner:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.pretty = bool(args.pretty)
        self.python_exe = args.python or sys.executable
        self.input_path = Path(args.input).resolve()
        self.submission = load_submission(self.input_path)
        self.output_root = Path(args.output_root).resolve()
        self.student_id = str(self.submission.get("student_id") or "anonymous")
        self.essay_id = str(self.submission.get("essay_id") or stable_id("essay", self.input_path))
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        explicit = Path(args.gold_dir).resolve() if args.gold_dir else None
        self.gold_dir = explicit or (self.output_root / self.student_id / f"gold_{stamp}_{self.essay_id}")
        self.gold_dir.mkdir(parents=True, exist_ok=True)
        self.profile_path = Path(args.gold_profile).resolve() if args.gold_profile else (self.output_root / "learner_profiles" / f"{self.student_id}_gold_profile.json")
        self.artifacts: Dict[str, str] = {}

    def artifact(self, key: str) -> Path:
        return self.gold_dir / GOLD_ARTIFACTS[key]

    def save_artifact(self, key: str, payload: Any) -> Path:
        path = self.artifact(key)
        write_json(path, payload, self.pretty)
        self.artifacts[key] = str(path)
        return path

    def resolve_premium_session(self) -> Path:
        if self.args.premium_session_dir:
            session_dir = Path(self.args.premium_session_dir).resolve()
            if not session_dir.exists():
                raise FileNotFoundError(f"Premium session directory not found: {session_dir}")
            return session_dir
        if not self.args.premium_runner:
            raise ValueError("Provide either --premium-session-dir or --premium-runner.")
        return run_premium_pipeline(
            python_exe=self.python_exe,
            premium_runner=Path(self.args.premium_runner).resolve(),
            submission_path=self.input_path,
            gold_dir=self.gold_dir,
        )

    def load_prior_profile(self) -> Dict[str, Any]:
        return read_json(self.profile_path, default=default_profile(self.student_id), required=False)

    def run(self) -> GoldRunResult:
        start = now_iso()
        # Preserve submission inside Gold folder.
        shutil.copyfile(self.input_path, self.gold_dir / "00_submission.json")
        self.artifacts["submission_copy"] = str(self.gold_dir / "00_submission.json")

        prior_profile = self.load_prior_profile()
        premium_session_dir = self.resolve_premium_session()
        premium_session = load_premium_session(premium_session_dir)

        # Evaluator/WKE.
        evaluator_output_path = self.artifact("evaluator_output")
        evaluator_request_path = self.artifact("evaluator_request")
        if self.args.evaluator_output:
            evaluator = read_json(Path(self.args.evaluator_output).resolve(), default={}, required=True)
            write_json(evaluator_output_path, evaluator, self.pretty)
            self.artifacts["evaluator_output"] = str(evaluator_output_path)
        elif evaluator_output_path.exists() and not self.args.force_evaluator:
            evaluator = read_json(evaluator_output_path, default={})
        else:
            if not self.args.evaluator_runner:
                raise ValueError("Gold requires Evaluator/WKE. Provide --evaluator-runner or --evaluator-output.")
            evaluator_request = build_evaluator_request(self.submission, premium_session, prior_profile)
            # no-llm flag overrides request to deterministic mode. If caller wants LLM, pass --evaluator-use-llm.
            evaluator_request["use_llm"] = bool(self.args.evaluator_use_llm)
            write_json(evaluator_request_path, evaluator_request, self.pretty)
            self.artifacts["evaluator_request"] = str(evaluator_request_path)
            evaluator = run_evaluator(
                python_exe=self.python_exe,
                evaluator_runner=Path(self.args.evaluator_runner).resolve(),
                request_path=evaluator_request_path,
                output_path=evaluator_output_path,
                gold_dir=self.gold_dir,
                pretty=self.pretty,
                no_llm=not bool(self.args.evaluator_use_llm),
            )
            self.artifacts["evaluator_output"] = str(evaluator_output_path)

        fusion = build_gold_evidence_fusion(self.submission, premium_session_dir, premium_session, evaluator, prior_profile)
        self.save_artifact("evidence_fusion", fusion)

        gold_feedback = build_gold_feedback(fusion)
        self.save_artifact("gold_feedback", gold_feedback)

        lret_session = build_lret_session(fusion, evaluator)
        self.save_artifact("lret_session", lret_session)

        wc, wc_state = run_writing_coach_optional(
            python_exe=self.python_exe,
            runner=Path(self.args.writing_coach_runner).resolve() if self.args.writing_coach_runner else None,
            move_bank=Path(self.args.move_bank).resolve() if self.args.move_bank else None,
            ontology=Path(self.args.ontology).resolve() if self.args.ontology else None,
            clusters=Path(self.args.clusters).resolve() if self.args.clusters else None,
            coach_state=Path(self.args.coach_state).resolve() if self.args.coach_state else None,
            last_mission_result=Path(self.args.last_mission_result).resolve() if self.args.last_mission_result else None,
            premium_session=premium_session,
            evaluator_path=evaluator_output_path,
            gold_dir=self.gold_dir,
            pretty=self.pretty,
            fusion=fusion,
            gold_feedback=gold_feedback,
        )
        self.artifacts["writing_coach"] = str(self.artifact("writing_coach"))
        self.artifacts["writing_coach_state"] = str(self.artifact("writing_coach_state"))

        practice_directive = build_gold_practice_directive(fusion, lret_session, wc)
        self.save_artifact("gold_practice_directive", practice_directive)

        profile = update_gold_lie_profile(prior_profile, fusion, gold_feedback, lret_session, wc, practice_directive)
        self.save_artifact("learner_profile", profile)
        write_json(self.profile_path, profile, self.pretty)
        self.artifacts["persistent_learner_profile"] = str(self.profile_path)

        skills_progress = build_skills_progress_report(profile)
        self.save_artifact("skills_progress", skills_progress)

        roadmap = build_learning_roadmap(gold_feedback, profile)
        self.save_artifact("learning_roadmap", roadmap)

        service_router = build_service_router(fusion, lret_session, wc)
        self.save_artifact("service_routing", service_router)

        progress_snapshot = build_progress_snapshot(profile, fusion)
        self.save_artifact("progress_snapshot", progress_snapshot)

        revision_launch = build_revision_launch_packet(self.submission, premium_session_dir, evaluator_output_path, self.gold_dir, self.args)
        self.save_artifact("revision_launch", revision_launch)

        qa = build_qa_report(premium_session_dir, premium_session, evaluator, fusion, gold_feedback, lret_session, wc, profile)
        self.save_artifact("qa", qa)

        manifest = {
            "schema_version": "GOLD_RUN_MANIFEST_V1",
            "engine_id": ENGINE_ID,
            "engine_version": ENGINE_VERSION,
            "started_at": start,
            "finished_at": now_iso(),
            "gold_dir": str(self.gold_dir),
            "premium_session_dir": str(premium_session_dir),
            "student_id": self.student_id,
            "essay_id": self.essay_id,
            "artifacts": self.artifacts,
            "qa_status": qa.get("status"),
            "next_best_action": service_router.get("next_best_action"),
        }
        self.save_artifact("manifest", manifest)

        return GoldRunResult(
            gold_dir=str(self.gold_dir),
            premium_session_dir=str(premium_session_dir),
            artifacts=self.artifacts,
            qa_status=qa.get("status", "unknown"),
            next_best_action=service_router.get("next_best_action", {}),
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="VA/ST.ELLA Gold Full Pipeline Runner v1.0")
    p.add_argument("--input", required=True, help="Submission JSON: student_id, essay_id, essay_text, prompt_text, task_type, topic.")
    p.add_argument("--output-root", default="gold_sessions", help="Root folder for Gold outputs and learner profiles.")
    p.add_argument("--gold-dir", help="Exact Gold output folder. Overrides --output-root session naming.")
    p.add_argument("--gold-profile", help="Persistent Gold learner profile path. Default: <output-root>/learner_profiles/<student_id>_gold_profile.json")

    p.add_argument("--premium-runner", help="Path to pipeline_runner_v14j_revision_safe.py. Required unless --premium-session-dir is provided.")
    p.add_argument("--premium-session-dir", help="Existing Premium session folder. If provided, Premium is not run.")

    p.add_argument("--evaluator-runner", help="Path to va_premium_evaluator_v7_3b_wke_v7_3b_3.py.")
    p.add_argument("--evaluator-output", help="Existing Evaluator/WKE output JSON. If provided, Evaluator is not run.")
    p.add_argument("--evaluator-use-llm", action="store_true", help="Allow Evaluator LLM refinement if the evaluator supports it and API key is available.")
    p.add_argument("--force-evaluator", action="store_true", help="Re-run evaluator even if local evaluator output exists.")

    p.add_argument("--writing-coach-runner", help="Optional path to writing_coach_v1_2_3_cli.py.")
    p.add_argument("--move-bank", help="Required by external Writing Coach. If omitted, Gold creates a deterministic fallback mission.")
    p.add_argument("--ontology", help="Optional writing competency ontology JSON for Writing Coach.")
    p.add_argument("--clusters", help="Optional microskill clustering JSON for Writing Coach.")
    p.add_argument("--coach-state", help="Optional prior Writing Coach state JSON.")
    p.add_argument("--last-mission-result", help="Optional last Writing Coach mission result JSON.")

    p.add_argument("--revision-runner", help="Optional path to essay_revision_full_pipeline_runner_v4_7_1.py. This runner writes a launch packet; revision requires a revised essay.")

    p.add_argument("--python", default=sys.executable, help="Python executable for subprocess engines.")
    p.add_argument("--pretty", action="store_true", help="Pretty-print JSON outputs.")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        runner = GoldPipelineRunner(args)
        result = runner.run()
        print("\nGOLD PIPELINE COMPLETE")
        print("Gold folder:       ", result.gold_dir)
        print("Premium session:   ", result.premium_session_dir)
        print("QA status:         ", result.qa_status)
        nba = result.next_best_action or {}
        print("Next best action:  ", nba.get("type"), "|", nba.get("skill_name"))
        print("Manifest:          ", result.artifacts.get("manifest"))
        return 0 if result.qa_status == "passed" else 2
    except Exception as exc:
        print("\n[GOLD PIPELINE ERROR]", str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
