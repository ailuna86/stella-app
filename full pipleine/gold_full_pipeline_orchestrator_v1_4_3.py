#!/usr/bin/env python3
"""
VA / ST.ELLA Gold Full Pipeline Orchestrator v1.4.3
==================================================

ORCHESTRATION-ONLY DESIGN
-------------------------
This file coordinates independent engines. It does not implement Detector,
Scorer, Verifier, Adjudicator, Feedback, Evaluator/WKE, LRET, Writing Coach,
Practice, Revision, or Learning Intelligence logic.

It performs only:
- input normalization into a single essay submission
- Gold session folder creation
- optional subprocess execution of configured external engines
- optional copying of precomputed artifacts for QA/development runs
- artifact presence/JSON validation
- metadata-level evidence-fusion manifest
- final run manifest and QA report

No essay-specific patterns. No lexical upgrade lists. No collocation banks.
No LRET labels. No Writing Coach task generation. No previous-version imports.

Engine commands are supplied via a JSON config. Each command may be either a
string or a list of arguments. Template variables are expanded, for example:

{
  "detector": ["python", "detector_cli.py", "--input", "{submission}", "--output", "{detector}", "--pretty"],
  "errormap": ["python", "detector_to_errormap_v3_standalone.py", "--input", "{detector}", "--output", "{errormap}", "--pretty"]
}

Typical usage:
python gold_full_pipeline_orchestrator_v1_4_3.py --input submission.json --engine-config gold_engine_commands.json --output-root gold_sessions --pretty

For development, you can copy existing artifacts instead of running engines:
python gold_full_pipeline_orchestrator_v1_4_3.py --input submission.json --copy-from-session previous_session_dir --output-root gold_sessions --pretty
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

ENGINE_ID = "VA_STELLA_GOLD_ORCHESTRATOR"
ENGINE_VERSION = "1.4.3-quality-gated-orchestration-only-standalone"
SCHEMA_VERSION = "GOLD_ORCHESTRATOR_MANIFEST_V1_4"

ARTIFACTS: Dict[str, str] = {
    "submission": "00_submission.json",
    "intake": "00_intake_assessment.json",
    "detector": "01_detector_output.json",
    "errormap": "01b_errormap_v3.json",
    "detector_for_evaluator": "01c_detector_for_evaluator.json",
    "metric_profile": "02_metric_profile.json",
    "scorer": "02a_premium_scorer_v1_4_1_output.json",
    "verifier": "02b_premium_verifier_v1_4_3_output.json",
    "adjudicator": "02c_final_adjudicated_v1_2.json",
    "score_contract": "02d_final_score_contract.json",
    "priority": "03_pe_output.json",
    "priority_normalized": "03b_priority_normalized_v1_4_3.json",
    "directive": "04_directive_v2.json",
    "feedback_engine": "05_fe_output.json",
    "feedback_report": "06_feedback_report_v6c.json",
    "evaluator": "07_evaluator_output.json",
    "evidence_fusion": "07b_gold_evidence_fusion.json",
    "lret_session": "07d_lret_session.json",
    "writing_coach": "07e_writing_coach_output.json",
    "practice_session": "07f_gold_practice_session.json",
    "learner_profile": "08_gold_learner_profile.json",
    "skills_progress": "08b_gold_skills_progress_report.json",
    "learning_roadmap": "08c_gold_learning_roadmap.json",
    "service_routing": "08d_gold_service_routing.json",
    "progress_snapshot": "09_gold_progress_snapshot.json",
    "revision_workspace": "10_revision_workspace.json",
    "revision_launch_packet": "revision_launch_packet.json",
    "qa_report": "QA_gold_report.json",
    "manifest": "gold_run_manifest.json",
}

# Stage order is orchestration order only. Stages are skipped unless a command is
# configured or an artifact is copied from --copy-from-session.
STAGE_ORDER: List[str] = [
    "intake",
    "detector",
    "errormap",
    "detector_for_evaluator",
    "metric_profile",
    "scorer",
    "verifier",
    "adjudicator",
    "score_contract",
    "priority",
    "priority_normalized",
    "directive",
    "feedback_engine",
    "feedback_report",
    "evaluator",
    "lret_session",
    "writing_coach",
    "practice_session",
    "learner_profile",
    "skills_progress",
    "learning_roadmap",
    "service_routing",
    "progress_snapshot",
    "revision_workspace",
    "revision_launch_packet",
    "evidence_fusion",
]

# Minimal product-critical artifacts for a complete Gold report. Development
# runs may be partial unless --strict is used.
REQUIRED_FOR_COMPLETE_GOLD: List[str] = [
    "submission",
    "detector",
    "errormap",
    "detector_for_evaluator",
    "scorer",
    "verifier",
    "adjudicator",
    "score_contract",
    "priority",
    "priority_normalized",
    "directive",
    "feedback_engine",
    "feedback_report",
    "evaluator",
    "evidence_fusion",
    "lret_session",
    "writing_coach",
    "practice_session",
    "learner_profile",
    "service_routing",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Union[str, Path], required: bool = True) -> Any:
    p = Path(path)
    if not p.exists():
        if required:
            raise FileNotFoundError(str(p))
        return None
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Union[str, Path], data: Any, pretty: bool = False) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2 if pretty else None)
        f.write("\n")


def stable_safe_id(text: str, fallback: str) -> str:
    s = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in str(text or "").strip())
    s = s.strip("_")
    return s or fallback


def normalize_submission(raw: Any, essay_index: int = 0) -> Dict[str, Any]:
    """Accept either top-level essay JSON or batch JSON with essays[]."""
    if isinstance(raw, dict) and isinstance(raw.get("essays"), list):
        essays = raw.get("essays") or []
        if not essays:
            raise ValueError("Input contains essays=[], but no essay submission.")
        if essay_index < 0 or essay_index >= len(essays):
            raise IndexError(f"essay_index {essay_index} out of range for {len(essays)} essays")
        rec = dict(essays[essay_index] or {})
    elif isinstance(raw, dict):
        rec = dict(raw)
    else:
        raise ValueError("Input JSON must be an object or an object with essays[].")

    essay_text = str(rec.get("essay_text") or rec.get("text") or "").strip()
    prompt_text = str(rec.get("prompt_text") or rec.get("prompt") or "").strip()
    if not essay_text:
        raise ValueError("Submission JSON must contain non-empty essay_text.")
    if not prompt_text:
        raise ValueError("Submission JSON must contain non-empty prompt_text.")

    essay_id = stable_safe_id(rec.get("essay_id") or rec.get("submission_id"), "essay_001")
    student_id = stable_safe_id(rec.get("student_id") or rec.get("learner_id"), "student_unknown")
    task_type = str(rec.get("task_type") or "WT2").strip() or "WT2"

    out = {
        "schema_version": "GOLD_SUBMISSION_NORMALIZED_V1_4",
        "essay_id": essay_id,
        "student_id": student_id,
        "task_type": task_type,
        "prompt_text": prompt_text,
        "essay_text": essay_text,
        "topic_keywords": rec.get("topic_keywords", []),
        "source_metadata": {
            k: v for k, v in rec.items()
            if k not in {"essay_text", "text", "prompt_text", "prompt"}
        },
    }
    return out


def make_session_dir(output_root: Path, student_id: str, essay_id: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_id = f"gold_{stamp}_{essay_id}_{uuid.uuid4().hex[:8]}"
    return output_root / student_id / session_id


def template_value(value: Any, mapping: Dict[str, str]) -> Any:
    if isinstance(value, str):
        try:
            return value.format(**mapping)
        except KeyError as e:
            raise KeyError(f"Unknown template variable {e} in command value: {value}")
    if isinstance(value, list):
        return [template_value(v, mapping) for v in value]
    if isinstance(value, dict):
        return {k: template_value(v, mapping) for k, v in value.items()}
    return value


def is_probable_local_script(token: str) -> bool:
    """Return True only for tokens that look like executable project scripts.

    This is orchestration support, not targeted engine logic. It prevents a
    relative script name such as detector_cli.py from being looked up in the
    generated session folder or another accidental working directory.
    """
    if not isinstance(token, str) or not token.strip():
        return False
    t = token.strip().strip('"')
    if t.startswith("{") and t.endswith("}"):
        return False
    if t.startswith("-"):
        return False
    suffix = Path(t).suffix.lower()
    return suffix in {".py", ".pyw", ".cmd", ".bat", ".ps1"}


def resolve_command_paths(command: Union[str, List[str]], config_dir: Path, orchestrator_dir: Path) -> Union[str, List[str]]:
    """Resolve relative engine script paths while preserving artifact paths.

    Rules:
    - Shell-string commands are left unchanged and executed with cwd=config_dir.
    - List commands are safer; relative script tokens are expanded to absolute
      paths if they exist next to the config file or next to this orchestrator.
    - Ordinary arguments and artifact paths are not rewritten.
    """
    if isinstance(command, str):
        return command
    resolved: List[str] = []
    for idx, raw in enumerate(command):
        token = str(raw)
        cleaned = token.strip().strip('"')
        if is_probable_local_script(cleaned) and not Path(cleaned).is_absolute():
            candidates = [config_dir / cleaned, orchestrator_dir / cleaned, Path.cwd() / cleaned]
            found = next((c.resolve() for c in candidates if c.exists()), None)
            if found is not None:
                resolved.append(str(found))
            else:
                # Preserve the original token so subprocess reports the real missing script.
                resolved.append(token)
        else:
            resolved.append(token)
    return resolved


@dataclass
class StageResult:
    stage: str
    status: str
    output_path: str
    command: Optional[Union[str, List[str]]] = None
    returncode: Optional[int] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None
    copied_from: Optional[str] = None
    stdout_log: Optional[str] = None
    stderr_log: Optional[str] = None


def run_command(stage: str, command: Union[str, List[str]], cwd: Path, stdout_log: Path, stderr_log: Path) -> Tuple[int, Optional[str]]:
    stdout_log.parent.mkdir(parents=True, exist_ok=True)
    stderr_log.parent.mkdir(parents=True, exist_ok=True)
    with stdout_log.open("w", encoding="utf-8") as out, stderr_log.open("w", encoding="utf-8") as err:
        if isinstance(command, str):
            proc = subprocess.run(command, cwd=str(cwd), stdout=out, stderr=err, shell=True)
        else:
            proc = subprocess.run([str(x) for x in command], cwd=str(cwd), stdout=out, stderr=err, shell=False)
    if proc.returncode != 0:
        try:
            msg = stderr_log.read_text(encoding="utf-8")[-2000:]
        except Exception:
            msg = f"Stage {stage} failed with return code {proc.returncode}."
        return proc.returncode, msg
    return proc.returncode, None


def copy_artifact_if_available(stage: str, copy_from: Optional[Path], dest: Path) -> Optional[str]:
    if not copy_from:
        return None
    src = copy_from / ARTIFACTS[stage]
    if src.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return str(src)
    return None


def json_status(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"exists": False, "valid_json": False}
    try:
        obj = read_json(path)
        schema = obj.get("schema_version") if isinstance(obj, dict) else None
        return {"exists": True, "valid_json": True, "schema_version": schema}
    except Exception as e:
        return {"exists": True, "valid_json": False, "error": str(e)}


def build_evidence_fusion(paths: Dict[str, Path], pretty: bool = False) -> Dict[str, Any]:
    """Metadata-level fusion only. Does not classify, score, or teach."""
    def maybe(path_key: str) -> Optional[Any]:
        p = paths.get(path_key)
        if p and p.exists():
            try:
                return read_json(p)
            except Exception:
                return None
        return None

    score_contract = maybe("score_contract") or {}
    errormap = maybe("errormap") or {}
    evaluator = maybe("evaluator") or {}
    lret = maybe("lret_session") or {}
    coach = maybe("writing_coach") or {}
    practice = maybe("practice_session") or {}

    fusion = {
        "schema_version": "GOLD_EVIDENCE_FUSION_METADATA_V1_4",
        "created_at": now_iso(),
        "boundary": "Metadata-level orchestration record only; targeted engines own scoring, detection, LRET classification, coaching, practice, revision, and learner-model logic.",
        "performance_evidence": {
            "source_artifact": ARTIFACTS["score_contract"],
            "present": bool(score_contract),
            "score_status": score_contract.get("score_status") if isinstance(score_contract, dict) else None,
            "score_confidence": score_contract.get("score_confidence") if isinstance(score_contract, dict) else None,
            "progress_tracking_allowed": score_contract.get("progress_tracking_allowed") if isinstance(score_contract, dict) else None,
            "lie_update_allowed": score_contract.get("lie_update_allowed") if isinstance(score_contract, dict) else None,
        },
        "error_pattern_evidence": {
            "source_artifact": ARTIFACTS["errormap"],
            "present": bool(errormap),
            "error_count": len(errormap.get("errors", [])) if isinstance(errormap, dict) else None,
            "counts_present": isinstance(errormap, dict) and isinstance(errormap.get("counts"), dict),
        },
        "writing_capacity_evidence": {
            "source_artifact": ARTIFACTS["evaluator"],
            "present": bool(evaluator),
            "profile_present": isinstance(evaluator, dict) and "writing_skill_profile" in evaluator,
            "consumer_payloads_present": isinstance(evaluator, dict) and "consumer_payloads" in evaluator,
        },
        "service_outputs": {
            "lret_present": bool(lret),
            "writing_coach_present": bool(coach),
            "practice_present": bool(practice),
        },
    }
    write_json(paths["evidence_fusion"], fusion, pretty=pretty)
    return fusion


def validate_boundaries(paths: Dict[str, Path]) -> List[Dict[str, str]]:
    """Non-invasive checks for orchestration boundaries."""
    issues: List[Dict[str, str]] = []

    # Orchestrator output should not need to inspect LRET labels, but we check
    # only that evidence_fusion did not invent them.
    ef_path = paths.get("evidence_fusion")
    if ef_path and ef_path.exists():
        text = ef_path.read_text(encoding="utf-8", errors="replace").lower()
        forbidden_fragments = [
            "general_single_word_upgrades", "academic_single_word_upgrades",
            "collocation_keep", "collocation_fix", "collocation_enhance",
            "suggestions_academic", "suggestions_general",
        ]
        for frag in forbidden_fragments:
            if frag in text:
                issues.append({"severity": "high", "artifact": "evidence_fusion", "issue": f"forbidden_engine_logic_fragment:{frag}"})

    # Score contract should be authoritative for released score if present.
    sc = paths.get("score_contract")
    if sc and sc.exists():
        try:
            obj = read_json(sc)
            if isinstance(obj, dict) and "released_score" not in obj and "final_score_profile" not in obj:
                issues.append({"severity": "medium", "artifact": "score_contract", "issue": "score_contract_missing_released_score_or_final_score_profile"})
        except Exception as e:
            issues.append({"severity": "high", "artifact": "score_contract", "issue": f"invalid_json:{e}"})

    return issues


def _safe_obj(paths: Dict[str, Path], key: str) -> Any:
    p = paths.get(key)
    if not p or not p.exists():
        return None
    try:
        return read_json(p, required=False)
    except Exception:
        return None


def _detector_row_count(obj: Any) -> int:
    if not isinstance(obj, dict):
        return 0
    root = obj
    if isinstance(obj.get("results"), list) and obj["results"]:
        root = obj["results"][0] if isinstance(obj["results"][0], dict) else {}
    total = 0
    for key in ("diagnostic_rows", "student_rows", "survived_candidates", "validated_rows", "rows", "errors"):
        value = root.get(key) if isinstance(root, dict) else None
        if isinstance(value, list):
            total += len(value)
    payload = root.get("evaluator_payload") if isinstance(root, dict) else None
    if isinstance(payload, dict) and isinstance(payload.get("all_detector_evidence"), list):
        total += len(payload["all_detector_evidence"])
    return total


def _evaluator_detector_row_count(obj: Any) -> int:
    if not isinstance(obj, dict):
        return 0
    return int(((obj.get("input_summary") or {}).get("detector_row_count")) or 0)


def _focus_areas(obj: Any) -> List[Dict[str, Any]]:
    if not isinstance(obj, dict):
        return []
    focus = obj.get("focus_areas")
    if isinstance(focus, list):
        return [x for x in focus if isinstance(x, dict)]
    primary = obj.get("primary_focus")
    return [primary] if isinstance(primary, dict) else []


def _has_unknown_skill(focus: List[Dict[str, Any]]) -> bool:
    for item in focus:
        values = [item.get("skill"), item.get("skill_tag"), item.get("skill_id"), item.get("student_label"), item.get("criterion"), item.get("rubric")]
        if any(str(v or "").upper().startswith("UNKNOWN") for v in values):
            return True
    return False


def validate_quality_gates(paths: Dict[str, Path]) -> List[Dict[str, str]]:
    """Quality gates for v1.4.3.

    These checks are still orchestration-level checks: they do not score,
    detect, teach, or classify. They only verify that downstream artifacts are
    usable and that adapters have repaired known contract mismatches.
    """
    issues: List[Dict[str, str]] = []
    detector = _safe_obj(paths, "detector")
    detector_eval = _safe_obj(paths, "detector_for_evaluator")
    evaluator = _safe_obj(paths, "evaluator")
    priority_norm = _safe_obj(paths, "priority_normalized")
    directive = _safe_obj(paths, "directive")
    practice = _safe_obj(paths, "practice_session")
    evidence_fusion = _safe_obj(paths, "evidence_fusion")
    service_routing = _safe_obj(paths, "service_routing")

    source_detector_rows = _detector_row_count(detector)
    bridge_detector_rows = _detector_row_count(detector_eval)
    evaluator_rows = _evaluator_detector_row_count(evaluator)
    if source_detector_rows > 0 and bridge_detector_rows <= 0:
        issues.append({"severity": "high", "artifact": "detector_for_evaluator", "issue": "detector_rows_not_exported_for_evaluator"})
    if source_detector_rows > 0 and evaluator_rows <= 0:
        issues.append({"severity": "high", "artifact": "evaluator", "issue": "evaluator_detector_row_count_zero_despite_detector_evidence"})

    focus = _focus_areas(priority_norm)
    if not focus:
        issues.append({"severity": "high", "artifact": "priority_normalized", "issue": "no_focus_areas"})
    elif _has_unknown_skill(focus):
        issues.append({"severity": "high", "artifact": "priority_normalized", "issue": "unknown_skill_remaining_after_normalization"})

    d_primary = directive.get("primary_focus") if isinstance(directive, dict) else None
    gld = directive.get("gold_learning_directive") if isinstance(directive, dict) else {}
    if not isinstance(d_primary, dict):
        issues.append({"severity": "high", "artifact": "directive", "issue": "primary_focus_missing"})
    if not isinstance(gld, dict) or not gld.get("recommended_service") or not gld.get("next_best_capacity_domain"):
        issues.append({"severity": "high", "artifact": "directive", "issue": "routing_fields_missing"})

    p_primary = practice.get("primary_focus") if isinstance(practice, dict) else None
    p_count = int((practice.get("exercise_count") if isinstance(practice, dict) else 0) or 0)
    if not isinstance(p_primary, dict):
        issues.append({"severity": "high", "artifact": "practice_session", "issue": "primary_focus_missing"})
    if p_count <= 0:
        issues.append({"severity": "high", "artifact": "practice_session", "issue": "no_exercises"})

    if isinstance(evidence_fusion, dict):
        services = evidence_fusion.get("service_outputs") or {}
        for service_key in ("lret_present", "writing_coach_present", "practice_present"):
            if services.get(service_key) is not True:
                issues.append({"severity": "medium", "artifact": "evidence_fusion", "issue": f"{service_key}_false_after_full_run"})

    if isinstance(service_routing, dict):
        if not (service_routing.get("next_best_service") or service_routing.get("primary_next_service")):
            issues.append({"severity": "medium", "artifact": "service_routing", "issue": "next_best_service_missing"})
    return issues


def load_engine_config(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    obj = read_json(path)
    if not isinstance(obj, dict):
        raise ValueError("Engine config must be a JSON object.")
    return obj.get("commands", obj)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Gold full pipeline orchestrator v1.4.3 — standalone orchestration only with downstream quality gates.")
    ap.add_argument("--input", required=True, help="Submission JSON, either one essay or {essays:[...]}.")
    ap.add_argument("--essay-index", type=int, default=0, help="Essay index when input has essays[].")
    ap.add_argument("--output-root", default="gold_sessions", help="Root folder for Gold sessions.")
    ap.add_argument("--engine-config", help="JSON file with stage command templates.")
    ap.add_argument("--copy-from-session", help="Existing session folder to copy artifacts from for QA/development.")
    ap.add_argument("--strict", action="store_true", help="Exit non-zero unless all product-critical Gold artifacts are present and valid JSON.")
    ap.add_argument("--continue-on-error", action="store_true", help="Continue running later stages after a configured command fails.")
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)

    input_path = Path(args.input).resolve()
    output_root = Path(args.output_root).resolve()
    copy_from = Path(args.copy_from_session).resolve() if args.copy_from_session else None
    raw = read_json(input_path)
    submission = normalize_submission(raw, essay_index=args.essay_index)

    session_dir = make_session_dir(output_root, submission["student_id"], submission["essay_id"])
    session_dir.mkdir(parents=True, exist_ok=True)

    paths = {key: session_dir / filename for key, filename in ARTIFACTS.items()}
    write_json(paths["submission"], submission, pretty=args.pretty)

    # Template mapping. Users can reference any artifact key by name.
    template_map = {key: str(path) for key, path in paths.items()}
    template_map.update({
        "session_dir": str(session_dir),
        "output_root": str(output_root),
        "input": str(input_path),
        "essay_id": submission["essay_id"],
        "student_id": submission["student_id"],
        "task_type": submission["task_type"],
        "python": sys.executable,
    })

    commands = load_engine_config(args.engine_config)
    # Relative engine script paths in the config are resolved from the config file folder.
    # This keeps the session folder clean and prevents commands like "detector_cli.py"
    # from being searched inside the generated session directory.
    if args.engine_config:
        engine_working_dir = Path(args.engine_config).resolve().parent
    else:
        engine_working_dir = Path.cwd().resolve()
    orchestrator_dir = Path(__file__).resolve().parent
    template_map.update({
        "project_root": str(engine_working_dir),
        "config_dir": str(engine_working_dir),
        "engine_working_dir": str(engine_working_dir),
        "orchestrator_dir": str(orchestrator_dir),
    })
    stage_results: List[StageResult] = []

    # Copy artifacts first if requested. Commands can still overwrite copied artifacts.
    for stage in STAGE_ORDER:
        dest = paths[stage]
        copied = copy_artifact_if_available(stage, copy_from, dest)
        if copied:
            stage_results.append(StageResult(stage=stage, status="copied", output_path=str(dest), copied_from=copied))

    for stage in STAGE_ORDER:
        # evidence_fusion can be built internally because it is metadata only.
        if stage == "evidence_fusion" and stage not in commands:
            started = now_iso()
            try:
                build_evidence_fusion(paths, pretty=args.pretty)
                stage_results.append(StageResult(stage=stage, status="built_metadata", output_path=str(paths[stage]), started_at=started, finished_at=now_iso()))
            except Exception as e:
                stage_results.append(StageResult(stage=stage, status="failed", output_path=str(paths[stage]), started_at=started, finished_at=now_iso(), error=str(e)))
                if not args.continue_on_error:
                    break
            continue

        if stage not in commands:
            # Already copied? If not, mark skipped.
            if not paths[stage].exists():
                stage_results.append(StageResult(stage=stage, status="skipped_no_command", output_path=str(paths[stage])))
            continue

        command = template_value(commands[stage], template_map)
        command = resolve_command_paths(command, config_dir=engine_working_dir, orchestrator_dir=orchestrator_dir)
        started = now_iso()
        stdout_log = session_dir / "logs" / f"{stage}_stdout.log"
        stderr_log = session_dir / "logs" / f"{stage}_stderr.log"
        try:
            rc, err = run_command(stage, command, cwd=engine_working_dir, stdout_log=stdout_log, stderr_log=stderr_log)
            status = "ok" if rc == 0 else "failed"
            stage_results.append(StageResult(
                stage=stage,
                status=status,
                output_path=str(paths[stage]),
                command=command,
                returncode=rc,
                started_at=started,
                finished_at=now_iso(),
                error=err,
                stdout_log=str(stdout_log),
                stderr_log=str(stderr_log),
            ))
            if rc != 0 and not args.continue_on_error:
                break
        except Exception as e:
            stage_results.append(StageResult(stage=stage, status="failed", output_path=str(paths[stage]), command=command, started_at=started, finished_at=now_iso(), error=str(e)))
            if not args.continue_on_error:
                break

    artifact_status = {key: json_status(path) for key, path in paths.items() if key not in {"manifest", "qa_report"}}
    boundary_issues = validate_boundaries(paths)
    quality_gate_issues = validate_quality_gates(paths)
    missing_required = [k for k in REQUIRED_FOR_COMPLETE_GOLD if not artifact_status.get(k, {}).get("exists")]
    invalid_required = [k for k in REQUIRED_FOR_COMPLETE_GOLD if artifact_status.get(k, {}).get("exists") and not artifact_status.get(k, {}).get("valid_json")]

    qa_status = "passed" if not missing_required and not invalid_required and not boundary_issues and not quality_gate_issues else "needs_attention"
    if args.strict and qa_status != "passed":
        exit_code = 2
    else:
        exit_code = 0

    qa = {
        "schema_version": "GOLD_QA_REPORT_V1_4_3",
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "qa_status": qa_status,
        "strict_mode": bool(args.strict),
        "missing_required_artifacts": missing_required,
        "invalid_required_artifacts": invalid_required,
        "boundary_issues": boundary_issues,
        "quality_gate_issues": quality_gate_issues,
        "artifact_status": artifact_status,
    }
    write_json(paths["qa_report"], qa, pretty=args.pretty)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "input_path": str(input_path),
        "session_dir": str(session_dir),
        "student_id": submission["student_id"],
        "essay_id": submission["essay_id"],
        "orchestration_boundary": "This orchestrator does not implement targeted engine logic. It only coordinates external engines and validates artifacts.",
        "engine_config_path": str(Path(args.engine_config).resolve()) if args.engine_config else None,
        "engine_working_dir": str(engine_working_dir),
        "orchestrator_dir": str(orchestrator_dir),
        "copy_from_session": str(copy_from) if copy_from else None,
        "qa_status": qa_status,
        "artifacts": {key: str(path) for key, path in paths.items()},
        "stage_results": [asdict(r) for r in stage_results],
    }
    write_json(paths["manifest"], manifest, pretty=args.pretty)

    print(json.dumps({"qa_status": qa_status, "session_dir": str(session_dir), "manifest": str(paths["manifest"]), "qa_report": str(paths["qa_report"])}, ensure_ascii=False, indent=2 if args.pretty else None))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
