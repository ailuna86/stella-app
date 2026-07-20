#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VA / ST.ELLA Gold Essay Revision Full-Pipeline Runner V4.7.1
========================================================

Implements the Gold Revision Workspace / ER V1.7.1 workflow:

  1. Load original full-pipeline session.
  2. Run Revision Workspace Builder BEFORE asking for the revised essay.
  3. Show the learner an annotated dual-pane revision workspace.
  4. Collect revised essay from file or interactive paste.
  5. Run Revision Submission Intake Gate.
  6. If valid, run the existing full premium pipeline on the revised essay.
  7. Build a wrapper revision_request JSON.
  8. Run the comparator and render an A2-B1 friendly student report.

Important
---------
This runner does not replace Detector/Scorer/Verifier/Adjudicator/Feedback.
It orchestrates them, normalises pasted revised text, and prevents incomplete
or malformed replacement fragments from being treated as normal Gold revisions.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

REQUIRED_SESSION_FILES = {
    "detector_output": "01_detector_output.json",
    "errormap": "01b_errormap_v3.json",
    "scorer": "02a_premium_scorer_v1_4_1_output.json",
    "verifier": "02b_premium_verifier_v1_4_3_output.json",
    "adjudicator": "02c_final_adjudicated_v1_2.json",
    "final_score": "02d_final_score_contract.json",
    "feedback": "06_feedback_report_v6c.json",
}

OPTIONAL_SESSION_FILES = {
    "pe_output": "03_pe_output.json",
    "directive": "04_directive_v2.json",
    "scorer_metrics": "02c_scorer_metrics.json",
}

EVALUATOR_SESSION_FILE = "07_evaluator_output.json"


def read_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any, pretty: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2 if pretty else None)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def deep_get(obj: Any, path: Iterable[Any], default: Any = None) -> Any:
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


def first_result(detector_output: Dict[str, Any]) -> Dict[str, Any]:
    results = detector_output.get("results")
    if isinstance(results, list) and results:
        return results[0]
    return detector_output


def words(text: str) -> List[str]:
    return re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text or "")


def word_count(text: str) -> int:
    return len(words(text))


def split_paragraphs(text: str) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    return [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]


def split_sentences(text: str) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    return [p.strip() for p in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", text) if p.strip()]


def token_set(text: str) -> set:
    return {w.lower() for w in words(text) if len(w) >= 4}


def similarity_ratio(a: str, b: str) -> float:
    sa, sb = token_set(a), token_set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / max(len(sa | sb), 1)


def shell_join(cmd: List[str]) -> str:
    return " ".join(f'"{x}"' if " " in str(x) else str(x) for x in cmd)


def normalise_revised_input(text: str) -> str:
    """Normalise pasted/file revised essay before validation and pipeline run.

    Fixes common Windows/PowerShell/UI paste issues:
    - wrapping quotes around the whole essay;
    - literal \n text instead of real paragraph breaks;
    - END/end sentinel accidentally included at the end;
    - repeated blank lines/spaces.
    """
    t = text or ""
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    t = t.strip()
    if (len(t) >= 2) and ((t[0] == t[-1] == '"') or (t[0] == t[-1] == "'")):
        t = t[1:-1].strip()
    # Convert escaped newlines only when they look like pasted literal escapes.
    if "\\n" in t:
        t = t.replace("\\r\\n", "\n").replace("\\n", "\n")
    # Some paste surfaces leave a literal leading n at the start of a paragraph after escaped newlines.
    # Example: nAnother problem... / nOn the other hand...
    t = re.sub(r"(?m)^n(?=[A-Z])", "", t)
    t = re.sub(r"(?<=[.!?])\s+n(?=[A-Z])", "\n\n", t)
    # Remove END/end sentinel if included as its own final line.
    lines = [ln.rstrip() for ln in t.split("\n")]
    while lines and re.fullmatch(r"(?i)end", lines[-1].strip()):
        lines.pop()
    t = "\n".join(lines).strip()
    # Remove accidental opening/closing quotes after sentinel removal.
    if (len(t) >= 2) and ((t[0] == t[-1] == '"') or (t[0] == t[-1] == "'")):
        t = t[1:-1].strip()
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def clean_work_dir_for_new_run(work_dir: Path) -> None:
    """Remove stale stage-B artifacts so old pipeline/comparator output is not reused."""
    stale_names = [
        "revised_pipeline_stdout.log", "revised_pipeline_stderr.log",
        "revision_comparator_stdout.log", "revision_comparator_stderr.log",
        "revision_comparator_command.txt", "revision_output.json", "revision_report.md",
        "revision_request_v2_3.json", "revision_workspace.json", "revision_workspace.md", "revision_workspace.html",
        "revision_student_report.json", "revision_student_report.md", "revision_student_report_stdout.log", "revision_student_report_stderr.log",
        "revision_run_manifest.json",
        "revision_ai_comparison.json", "revision_ai_comparison.md", "revision_ai_comparison.html",
        "revision_ai_comparison_v1_4.json", "revision_ai_comparison_v1_4.md", "revision_ai_comparison_v1_4.html",
        "revision_ai_comparison_v1_7.json", "revision_ai_comparison_v1_7.md", "revision_ai_comparison_v1_7.html",
        "revision_ai_comparison_v1_7_1.json", "revision_ai_comparison_v1_7_1.md", "revision_ai_comparison_v1_7_1.html",
        "revision_ai_comparison_stdout.log", "revision_ai_comparison_stderr.log",
        "revised_submission_for_full_pipeline.json", "stage_b_not_run_marker.json",
    ]
    work_dir.mkdir(parents=True, exist_ok=True)
    for name in stale_names:
        p = work_dir / name
        if p.exists():
            try:
                p.unlink()
            except OSError:
                marker = work_dir / f"STALE_{name}.txt"
                marker.write_text(f"Could not delete stale artifact: {p}\n", encoding="utf-8")


def mark_stage_b_not_run(work_dir: Path, reason: str) -> None:
    marker = {
        "status": "stage_b_not_run",
        "reason": reason,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "stale_outputs_removed": True,
    }
    write_json(work_dir / "stage_b_not_run_marker.json", marker, pretty=True)


def validate_session_files(session_dir: Path, label: str) -> None:
    missing = []
    for filename in REQUIRED_SESSION_FILES.values():
        if not (session_dir / filename).exists():
            missing.append(filename)
    if missing:
        msg = "\n".join(f"  - {m}" for m in missing)
        raise FileNotFoundError(f"{label} session is missing required files:\n{msg}\nFolder: {session_dir}")


def list_session_dirs(sessions_dir: Path) -> List[Path]:
    if not sessions_dir.exists():
        return []
    return sorted(
        [p for p in sessions_dir.iterdir() if p.is_dir() and p.name.startswith("session_")],
        key=lambda p: (p.name, p.stat().st_mtime),
    )


def detect_new_session(before: List[Path], after: List[Path]) -> Optional[Path]:
    before_set = {p.resolve() for p in before}
    new_dirs = [p for p in after if p.resolve() not in before_set]
    if new_dirs:
        return sorted(new_dirs, key=lambda p: p.stat().st_mtime)[-1]
    if after:
        return sorted(after, key=lambda p: p.stat().st_mtime)[-1]
    return None


def extract_context_from_session(session_dir: Path) -> Dict[str, Any]:
    det = read_json(session_dir / "01_detector_output.json") if (session_dir / "01_detector_output.json").exists() else {}
    final = read_json(session_dir / "02d_final_score_contract.json") if (session_dir / "02d_final_score_contract.json").exists() else {}
    meta = read_json(session_dir / "session_meta.json") if (session_dir / "session_meta.json").exists() else {}
    rec = first_result(det)
    intake = rec.get("intake_record") or {}
    identity = rec.get("identity") or {}
    essay_text = intake.get("essay_text") or intake.get("raw_text") or ""
    return {
        "student_id": identity.get("student_id") or det.get("student_id") or final.get("student_id") or meta.get("student_id"),
        "essay_id": str(identity.get("essay_id") or final.get("essay_id") or meta.get("essay_id") or "1"),
        "submission_id": identity.get("submission_id") or meta.get("submission_id"),
        "essay_text": essay_text,
        "prompt_text": intake.get("prompt_text") or meta.get("prompt_text") or "",
        "task_type": deep_get(rec, ["task_profile", "task_type"], None) or final.get("task_type") or meta.get("task_type") or "WT2",
        "topic": meta.get("essay_topic") or meta.get("topic") or "Revised Essay",
        "word_count": word_count(essay_text),
        "paragraph_count": len(split_paragraphs(essay_text)),
    }


def collect_revised_essay_interactively() -> str:
    print("\nPaste the FULL revised essay below.")
    print("When finished, type END on a new line and press Enter.")
    print("-" * 60)
    lines: List[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if re.fullmatch(r"(?i)end", line.strip()):
            break
        lines.append(line)
    text = "\n".join(lines).strip()
    if not text:
        raise ValueError("No revised essay text was provided.")
    return text


def read_revised_essay(args: argparse.Namespace) -> str:
    if args.revised_essay_file:
        p = Path(args.revised_essay_file)
        if not p.exists():
            raise FileNotFoundError(f"Revised essay file not found: {p}")
        text = p.read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError(f"Revised essay file is empty: {p}")
        return normalise_revised_input(text)
    return normalise_revised_input(collect_revised_essay_interactively())



def session_evaluator_path(session_dir: Path) -> Path:
    return session_dir / EVALUATOR_SESSION_FILE


def run_evaluator_for_session(
    python_exe: str,
    evaluator_runner: Path,
    session_dir: Path,
    output_path: Path,
    student_id: str,
    essay_id: str,
    prompt_text: str,
    no_llm: bool = False,
    pretty: bool = True,
) -> Path:
    """Run WKE/Evaluator for a full-pipeline session and return output_path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    detector_path = session_dir / REQUIRED_SESSION_FILES["detector_output"]
    scorer_path = session_dir / REQUIRED_SESSION_FILES["scorer"]
    detector = read_json(detector_path)
    essay_text = (
        deep_get(detector, ["results", 0, "intake_record", "essay_text"], "")
        or deep_get(detector, ["results", 0, "intake_record", "raw_text"], "")
    )
    if not essay_text:
        raise ValueError(f"Cannot run evaluator: essay_text not found in {detector_path}")

    request = {
        "student_id": student_id,
        "essay_id": essay_id,
        "prompt_text": prompt_text,
        "essay_text": essay_text,
        "detector_output_path": str(detector_path),
        "scorer_output_path": str(scorer_path),
        "use_llm": not no_llm,
        "max_llm_skills": 30,
    }
    req_path = output_path.with_suffix(".request.json")
    write_json(req_path, request, pretty=True)

    cmd = [python_exe, str(evaluator_runner), "--input", str(req_path), "--output", str(output_path)]
    if pretty:
        cmd.append("--pretty")
    if no_llm:
        cmd.append("--no-llm")

    stdout_path = output_path.with_suffix(".stdout.log")
    stderr_path = output_path.with_suffix(".stderr.log")
    print("\nRunning Evaluator/WKE...")
    print("Command:", shell_join(cmd))
    with open(stdout_path, "w", encoding="utf-8") as out_f, open(stderr_path, "w", encoding="utf-8") as err_f:
        proc = subprocess.run(
            cmd,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=out_f,
            stderr=err_f,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        )
    if proc.returncode != 0:
        tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:] if stderr_path.exists() else ""
        raise RuntimeError(f"Evaluator/WKE failed with exit code {proc.returncode}.\n{tail}")

    # V4.0: make WKE a normal session artifact as well as a work-dir artifact.
    # This lets future revision runs reuse the evaluator output from the session folder.
    session_out = session_evaluator_path(session_dir)
    try:
        if output_path.resolve() != session_out.resolve():
            write_json(session_out, read_json(output_path), pretty=True)
            print(f"Evaluator/WKE session artifact saved: {session_out}")
    except Exception as copy_exc:
        print(f"[WARN] Evaluator/WKE output was created but could not be copied to the session folder: {copy_exc}")
    return output_path


def resolve_required_evaluator_output(
    label: str,
    python_exe: str,
    session_dir: Path,
    provided_path: Optional[Path],
    evaluator_runner: Optional[Path],
    work_dir: Path,
    student_id: str,
    essay_id: str,
    prompt_text: str,
    no_llm: bool,
    allow_missing: bool = False,
) -> Optional[Path]:
    """Gold revision requires evaluator output. Development override is explicit."""
    if provided_path:
        if not provided_path.exists():
            raise FileNotFoundError(f"{label} evaluator output not found: {provided_path}")
        return provided_path

    in_session = session_evaluator_path(session_dir)
    if in_session.exists():
        return in_session

    if evaluator_runner:
        out = work_dir / f"{label.lower()}_evaluator_output.json"
        return run_evaluator_for_session(
            python_exe=python_exe,
            evaluator_runner=evaluator_runner,
            session_dir=session_dir,
            output_path=out,
            student_id=student_id,
            essay_id=essay_id,
            prompt_text=prompt_text,
            no_llm=no_llm,
            pretty=True,
        )

    if allow_missing:
        print(f"[WARN] {label} evaluator output missing; continuing only because --allow-missing-evaluator was set.")
        return None

    raise RuntimeError(
        f"{label} Evaluator/WKE output is required for Gold Essay Revision V4.0. "
        f"Provide --{label.lower()}-evaluator-output, put {EVALUATOR_SESSION_FILE} in the session folder, "
        f"or pass --evaluator-runner so this runner can generate it."
    )


def run_workspace_builder(
    python_exe: str,
    workspace_builder: Path,
    original_session_dir: Path,
    work_dir: Path,
    pretty: bool,
    evaluator_output: Optional[Path] = None,
    task_type: str = "",
    prompt_text: str = "",
) -> Dict[str, Any]:
    out_json = work_dir / "revision_workspace.json"
    out_md = work_dir / "revision_workspace.md"
    out_html = work_dir / "revision_workspace.html"
    cmd = [
        python_exe,
        str(workspace_builder),
        "--session-dir", str(original_session_dir),
        "--output", str(out_json),
        "--markdown", str(out_md),
        "--html", str(out_html),
    ]
    if evaluator_output:
        cmd.extend(["--evaluator-output", str(evaluator_output)])
    else:
        raise RuntimeError("Stage A requires original evaluator output in V4.7.")
    if task_type:
        cmd.extend(["--task-type", task_type])
    if prompt_text:
        cmd.extend(["--prompt-text", prompt_text])
    if pretty:
        cmd.append("--pretty")
    stdout_path = work_dir / "revision_workspace_builder_stdout.log"
    stderr_path = work_dir / "revision_workspace_builder_stderr.log"
    print("\nBuilding Stage A revision workspace...")
    print("Command:", shell_join(cmd))
    with open(stdout_path, "w", encoding="utf-8") as out_f, open(stderr_path, "w", encoding="utf-8") as err_f:
        proc = subprocess.run(
            cmd,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=out_f,
            stderr=err_f,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        )
    if proc.returncode != 0:
        tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:] if stderr_path.exists() else ""
        raise RuntimeError(f"Revision Workspace Builder failed with exit code {proc.returncode}.\n{tail}")
    task = read_json(out_json)
    print("\n" + "=" * 70)
    print("STAGE A — REVISION WORKSPACE")
    print("=" * 70)
    print(out_md.read_text(encoding="utf-8")[:12000])
    print("=" * 70)
    return task


def render_invalid_revision_report(gate: Dict[str, Any], task: Dict[str, Any]) -> str:
    lines = ["# Revision intake result", ""]
    lines.append("This revision is not ready for full Gold checking yet.")
    lines.append("")
    lines.append("## Why the system stopped")
    lines.append("")
    for r in gate.get("reasons") or []:
        lines.append(f"- {r}")
    lines.append("")
    lines.append("## Quick facts")
    lines.append("")
    lines.append(f"- Original essay: **{gate.get('original_word_count')} words**, **{gate.get('original_paragraph_count')} paragraph(s)**")
    lines.append(f"- Revised submission: **{gate.get('revised_word_count')} words**, **{gate.get('revised_paragraph_count')} paragraph(s)**")
    if gate.get("normalisation_applied"):
        lines.append("- Input was normalised before checking: quotes/END markers/literal \n were cleaned where needed.")
    lines.append("")
    req = task.get("submission_requirements") or deep_get(task, ["revision_workspace_packet", "minimum_submission_requirements"], {}) or {}
    lines.append("## What to do now")
    lines.append("")
    lines.append("Open **revision_workspace.md**, follow the highlighted paragraph/sentence notes, and submit the full essay again.")
    lines.append(f"Minimum: **{req.get('minimum_revised_word_count', 150)} words** and **{req.get('minimum_paragraphs', 3)} paragraphs**.")
    if req.get("recommended_task2_word_count"):
        lines.append(f"Best IELTS Task 2 target: about **{req.get('recommended_task2_word_count')}+ words** with real paragraph breaks.")
    lines.append("")
    lines.append("The full task is not repeated here to avoid noise. It is saved separately as **revision_workspace.md**.")
    return "\n".join(lines).strip() + "\n"


def validate_revision_intake(original_text: str, revised_text: str, task: Dict[str, Any]) -> Dict[str, Any]:
    req = task.get("submission_requirements") or deep_get(task, ["revision_workspace_packet", "minimum_submission_requirements"], {}) or {}
    original_wc = word_count(original_text)
    revised_wc = word_count(revised_text)
    original_paras = split_paragraphs(original_text)
    revised_paras = split_paragraphs(revised_text)
    revised_sents = split_sentences(revised_text)
    wc_ratio = revised_wc / original_wc if original_wc else None
    sim = similarity_ratio(original_text, revised_text)

    min_words = int(req.get("minimum_revised_word_count") or max(150, int(original_wc * 0.60) if original_wc else 150))
    min_paras = int(req.get("minimum_paragraphs") or 3)
    reasons: List[str] = []

    if revised_wc < 75:
        reasons.append("The revised submission is ultra-short. It looks like a fragment, not a full essay revision.")
    if revised_wc < min_words:
        reasons.append(f"The revised submission has {revised_wc} words, below the required minimum of {min_words} words.")
    if original_wc and revised_wc < 0.60 * original_wc:
        reasons.append("The revised submission is less than 60% of the original essay length, so it may be a replacement fragment.")
    if len(revised_paras) < min_paras:
        reasons.append(f"The revised submission has {len(revised_paras)} paragraph(s), below the required minimum of {min_paras}.")
    if len(revised_sents) < 5:
        reasons.append("The revised submission has too few sentences for a complete Task 2 revision.")
    # Similarity is not a hard invalid condition by itself, but flag it.
    if sim < 0.08 and revised_wc >= min_words:
        reasons.append("The revised essay shares very little topic vocabulary with the original; this may need a new full evaluation rather than revision comparison.")

    status = "valid_revision_attempt"
    if reasons:
        status = "invalid_incomplete_revision"
    return {
        "schema_version": "REVISION_SUBMISSION_INTAKE_GATE_V1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "valid": not reasons,
        "original_word_count": original_wc,
        "revised_word_count": revised_wc,
        "word_count_ratio": round(wc_ratio, 4) if wc_ratio is not None else None,
        "original_paragraph_count": len(original_paras),
        "revised_paragraph_count": len(revised_paras),
        "revised_sentence_count": len(revised_sents),
        "topic_similarity_token_jaccard": round(sim, 4),
        "minimum_revised_word_count": min_words,
        "minimum_paragraphs": min_paras,
        "reasons": reasons,
    }


def run_full_pipeline(
    python_exe: str,
    pipeline_runner: Path,
    submission_json: Path,
    work_dir: Path,
) -> Tuple[int, Path, Path]:
    pipeline_dir = pipeline_runner.resolve().parent
    sessions_dir = pipeline_dir / "sessions"
    before = list_session_dirs(sessions_dir)
    cmd = [python_exe, str(pipeline_runner), "--input", str(submission_json)]
    stdout_path = work_dir / "revised_pipeline_stdout.log"
    stderr_path = work_dir / "revised_pipeline_stderr.log"
    print("\nRunning full pipeline on revised essay...")
    print("Command:", shell_join(cmd))
    print("Stdout log:", stdout_path)
    print("Stderr log:", stderr_path)
    with open(stdout_path, "w", encoding="utf-8") as out_f, open(stderr_path, "w", encoding="utf-8") as err_f:
        proc = subprocess.run(
            cmd,
            cwd=str(pipeline_dir),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=out_f,
            stderr=err_f,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        )
    after = list_session_dirs(sessions_dir)
    revised_session = detect_new_session(before, after)
    if proc.returncode != 0:
        tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:] if stderr_path.exists() else ""
        raise RuntimeError(
            f"Full pipeline failed with exit code {proc.returncode}.\n"
            f"Check logs:\n  {stdout_path}\n  {stderr_path}\n\nLast stderr lines:\n{tail}"
        )
    if revised_session is None:
        raise RuntimeError("Full pipeline finished, but no session folder was detected.")
    return proc.returncode, revised_session, sessions_dir


def load_session_payload(session_dir: Path, evaluator_output_path: Optional[Path] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    for key, filename in REQUIRED_SESSION_FILES.items():
        payload[key] = read_json(session_dir / filename)
    for key, filename in OPTIONAL_SESSION_FILES.items():
        p = session_dir / filename
        if p.exists():
            payload[key] = read_json(p)
    evaluator_output = None
    if evaluator_output_path and evaluator_output_path.exists():
        evaluator_output = read_json(evaluator_output_path)
    elif session_evaluator_path(session_dir).exists():
        evaluator_output = read_json(session_evaluator_path(session_dir))
    # Normalize names expected by comparator request.
    return {
        "essay_text": deep_get(payload.get("detector_output"), ["results", 0, "intake_record", "essay_text"], "")
                      or deep_get(payload.get("detector_output"), ["results", 0, "intake_record", "raw_text"], ""),
        "detector_output": payload.get("detector_output"),
        "error_map": payload.get("errormap"),
        "scorer_output": payload.get("scorer"),
        "verifier_output": payload.get("verifier"),
        "adjudicator_output": payload.get("adjudicator"),
        "final_score_contract": payload.get("final_score"),
        "feedback_report": payload.get("feedback"),
        "priority_engine_output": payload.get("pe_output"),
        "directive_output": payload.get("directive"),
        "scorer_metrics": payload.get("scorer_metrics"),
        "evaluator_output": evaluator_output,
    }


def build_revision_request(
    original_session_dir: Path,
    revised_session_dir: Path,
    task: Dict[str, Any],
    student_id: str,
    essay_id: str,
    task_type: str,
    prompt_text: str,
    original_evaluator_output: Optional[Path] = None,
    revised_evaluator_output: Optional[Path] = None,
) -> Dict[str, Any]:
    return {
        "schema_version": "GOLD_ESSAY_REVISION_INPUT_V2_5_WKE_REQUIRED",
        "identity": {
            "student_id": student_id,
            "essay_id": essay_id,
            "original_submission_id": deep_get(task, ["identity", "submission_id"], "original"),
            "revised_submission_id": "revised",
            "revision_attempt_no": deep_get(task, ["identity", "revision_attempt_no"], 1),
        },
        "subscription": {"tier": "gold"},
        "prompt": {
            "task_type": task_type,
            "prompt_text": prompt_text,
        },
        "revision_workspace": task,
        "original": load_session_payload(original_session_dir, original_evaluator_output),
        "revised": load_session_payload(revised_session_dir, revised_evaluator_output),
        "config": {
            "allow_non_gold_for_testing": False,
            "stage_a_workspace_builder_used": True,
            "revision_workspace_schema": task.get("schema_version"),
            "evaluator_required": True,
            "wke_pre_post_comparison_available": bool(original_evaluator_output and revised_evaluator_output),
            "show_full_gold_feedback": True,
        },
    }


def run_comparator_with_input(
    python_exe: str,
    revision_engine: Path,
    request_path: Path,
    work_dir: Path,
    pretty: bool,
) -> None:
    out_json = work_dir / "revision_output.json"
    out_md = work_dir / "revision_report.md"
    cmd = [
        python_exe,
        str(revision_engine),
        "--input", str(request_path),
        "--output", str(out_json),
        "--markdown", str(out_md),
    ]
    if pretty:
        cmd.append("--pretty")
    stdout_path = work_dir / "revision_comparator_stdout.log"
    stderr_path = work_dir / "revision_comparator_stderr.log"
    write_text(work_dir / "revision_comparator_command.txt", shell_join(cmd))
    print("\nRunning Gold revision comparator...")
    print("Command:", shell_join(cmd))
    print("Stdout log:", stdout_path)
    print("Stderr log:", stderr_path)
    with open(stdout_path, "w", encoding="utf-8") as out_f, open(stderr_path, "w", encoding="utf-8") as err_f:
        proc = subprocess.run(
            cmd,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=out_f,
            stderr=err_f,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        )
    if proc.returncode != 0:
        tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:] if stderr_path.exists() else ""
        raise RuntimeError(
            f"Revision comparator failed with exit code {proc.returncode}.\n"
            f"Check logs:\n  {stdout_path}\n  {stderr_path}\n\nLast stderr lines:\n{tail}"
        )



def run_student_report_renderer(
    python_exe: str,
    renderer: Path,
    work_dir: Path,
    pretty: bool,
    ai_comparison_path: Optional[Path] = None,
) -> None:
    revision_output = work_dir / "revision_output.json"
    workspace = work_dir / "revision_workspace.json"
    out_json = work_dir / "revision_student_report.json"
    out_md = work_dir / "revision_student_report.md"
    cmd = [
        python_exe,
        str(renderer),
        "--revision-output", str(revision_output),
        "--workspace", str(workspace),
        "--output", str(out_json),
        "--markdown", str(out_md),
    ]
    if ai_comparison_path and ai_comparison_path.exists():
        cmd.extend(["--ai-comparison", str(ai_comparison_path)])
    if pretty:
        cmd.append("--pretty")
    stdout_path = work_dir / "revision_student_report_stdout.log"
    stderr_path = work_dir / "revision_student_report_stderr.log"
    print("\nRendering A2-B1 friendly student revision report...")
    print("Command:", shell_join(cmd))
    with open(stdout_path, "w", encoding="utf-8") as out_f, open(stderr_path, "w", encoding="utf-8") as err_f:
        proc = subprocess.run(
            cmd,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=out_f,
            stderr=err_f,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        )
    if proc.returncode != 0:
        tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:] if stderr_path.exists() else ""
        raise RuntimeError(f"Student report renderer failed with exit code {proc.returncode}.\n{tail}")



def run_ai_comparison_generator(
    python_exe: str,
    generator: Path,
    work_dir: Path,
    pretty: bool,
    use_llm: bool,
    model: Optional[str] = None,
    limit: int = 5,
    max_attempts: int = 4,
    allow_schema_fallback: bool = True,
) -> bool:
    """Generate optional post-self-correction AI sentence comparison artifacts.

    This is deliberately after Stage B/comparator. It never creates a full model essay by default.
    It is optional in the UI, but V4.7.1 generates the artifact so the feature is visible/testable.
    """
    revision_request = work_dir / "revision_request_v2_4.json"
    revision_output = work_dir / "revision_output.json"
    workspace = work_dir / "revision_workspace.json"
    # V4.7: versioned primary outputs prevent legacy V1 sentence-comparison files from
    # being confused with the paragraph-first IELTS-structure model.
    out_json = work_dir / "revision_ai_comparison_v1_7_1.json"
    out_md = work_dir / "revision_ai_comparison_v1_7_1.md"
    out_html = work_dir / "revision_ai_comparison_v1_7_1.html"
    cmd = [
        python_exe,
        str(generator),
        "--revision-request", str(revision_request),
        "--revision-output", str(revision_output),
        "--workspace", str(workspace),
        "--output", str(out_json),
        "--markdown", str(out_md),
        "--html", str(out_html),
        "--limit", str(limit),
        "--max-ai-attempts", str(max_attempts),
    ]
    if not allow_schema_fallback:
        cmd.append("--disable-schema-fallback")
    if not use_llm:
        cmd.append("--no-llm")
    if model:
        cmd.extend(["--model", model])
    if pretty:
        cmd.append("--pretty")
    stdout_path = work_dir / "revision_ai_comparison_stdout.log"
    stderr_path = work_dir / "revision_ai_comparison_stderr.log"
    print("\nGenerating optional post-revision AI IELTS structure comparison...")
    print("Command:", shell_join(cmd))
    with open(stdout_path, "w", encoding="utf-8") as out_f, open(stderr_path, "w", encoding="utf-8") as err_f:
        proc = subprocess.run(
            cmd,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=out_f,
            stderr=err_f,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        )
    if proc.returncode != 0:
        tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-3000:] if stderr_path.exists() else ""
        print(f"[WARN] AI comparison generator failed with exit code {proc.returncode}.\n{tail}")
        return False
    return True

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Gold Essay Revision V4.7.1 runner with required Evaluator/WKE for original and revised essays")
    parser.add_argument("--pipeline-runner", required=True, help="Path to pipeline_runner_v14j_revision_safe.py or your full pipeline runner")
    parser.add_argument("--revision-engine", required=True, help="Path to essay_revision_engine_v2_4_gold.py comparator")
    parser.add_argument("--workspace-builder", help="Path to gold_revision_universal_engine_v1_7_1.py. Defaults to same folder as this runner.")
    parser.add_argument("--student-report-renderer", help="Path to gold_revision_student_report_renderer_v1_4_1.py. Defaults to same folder as this runner.")
    parser.add_argument("--ai-comparison-generator", help="Path to gold_revision_ai_comparison_generator_v1_7_1.py. Defaults to same folder as this runner.")
    parser.add_argument("--evaluator-runner", help="Path to va_premium_evaluator_v7_3b_wke_v7_3b_3.py. Used to generate missing original/revised WKE outputs.")
    parser.add_argument("--original-evaluator-output", help="Original WKE/Evaluator output JSON. Required unless 07_evaluator_output.json exists or --evaluator-runner is supplied.")
    parser.add_argument("--revised-evaluator-output", help="Revised WKE/Evaluator output JSON. Required for Stage B unless 07_evaluator_output.json exists or --evaluator-runner is supplied.")
    parser.add_argument("--original-session-dir", required=True, help="Original full-pipeline session folder")
    parser.add_argument("--work-dir", required=True, help="Working/output folder")
    parser.add_argument("--revised-essay-file", help="Optional .txt file with the student's revised essay. If omitted, paste interactively.")
    parser.add_argument("--revised-session-dir", help="Optional existing revised full-pipeline session folder. If provided, skip running pipeline.")
    parser.add_argument("--student-id", help="Student ID. Defaults to original session value if available.")
    parser.add_argument("--essay-id", help="Essay ID. Defaults to original session value if available.")
    parser.add_argument("--task-type", help="Task type. Defaults to original session value or WT2.")
    parser.add_argument("--prompt-text", help="Prompt text. Defaults to original session detector prompt_text if available.")
    parser.add_argument("--topic", default="Revised Essay", help="Topic label for revised pipeline submission")
    parser.add_argument("--expected-band", help="Optional expected_band field for old pipeline compatibility")
    parser.add_argument("--python", default=sys.executable, help="Python executable. Default: current Python")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON outputs")
    parser.add_argument("--force-pipeline-on-invalid", action="store_true", help="Run full pipeline even if intake gate marks the revision incomplete")
    parser.add_argument("--skip-stage-a-display", action="store_true", help="Build revision workspace but do not print the Markdown to console")
    parser.add_argument("--no-evaluator-llm", action="store_true", help="Run evaluator without LLM when --evaluator-runner is used")
    parser.add_argument("--allow-missing-evaluator", action="store_true", help="Developer/testing override only. Product Gold mode requires original and revised evaluator outputs.")
    parser.add_argument("--skip-ai-comparison", action="store_true", help="Do not generate optional post-revision AI comparison artifacts.")
    parser.add_argument("--no-ai-comparison-llm", action="store_true", help="Create deterministic schema-based AI comparison without calling an LLM.")
    parser.add_argument("--ai-comparison-model", help="Model name for post-revision sentence comparison generator.")
    parser.add_argument("--ai-comparison-limit", type=int, default=5, help="Maximum paragraph-comparison items to generate.")
    parser.add_argument("--ai-comparison-max-attempts", type=int, default=4, help="Maximum AI generation/repair attempts before schema fallback.")
    parser.add_argument("--disable-ai-schema-fallback", action="store_true", help="Disable schema-based fallback model if LLM validation fails.")
    args = parser.parse_args(argv)

    pipeline_runner = Path(args.pipeline_runner).resolve()
    revision_engine = Path(args.revision_engine).resolve()
    original_session_dir = Path(args.original_session_dir).resolve()
    work_dir = Path(args.work_dir).resolve()
    workspace_builder = Path(args.workspace_builder).resolve() if args.workspace_builder else (Path(__file__).resolve().parent / "gold_revision_universal_engine_v1_7_1.py")
    student_report_renderer = Path(args.student_report_renderer).resolve() if args.student_report_renderer else (Path(__file__).resolve().parent / "gold_revision_student_report_renderer_v1_4_1.py")
    evaluator_runner = Path(args.evaluator_runner).resolve() if args.evaluator_runner else None
    ai_comparison_generator = Path(args.ai_comparison_generator).resolve() if args.ai_comparison_generator else (Path(__file__).resolve().parent / "gold_revision_ai_comparison_generator_v1_7_1.py")
    work_dir.mkdir(parents=True, exist_ok=True)
    clean_work_dir_for_new_run(work_dir)

    if not pipeline_runner.exists():
        raise FileNotFoundError(f"Pipeline runner not found: {pipeline_runner}")
    if not revision_engine.exists():
        raise FileNotFoundError(f"Revision engine not found: {revision_engine}")
    if not workspace_builder.exists():
        raise FileNotFoundError(f"Revision Workspace Builder not found: {workspace_builder}")
    if not student_report_renderer.exists():
        raise FileNotFoundError(f"Student report renderer not found: {student_report_renderer}")
    if evaluator_runner and not evaluator_runner.exists():
        raise FileNotFoundError(f"Evaluator/WKE runner not found: {evaluator_runner}")
    if not args.skip_ai_comparison and not ai_comparison_generator.exists():
        raise FileNotFoundError(f"AI comparison generator not found: {ai_comparison_generator}")
    if not original_session_dir.exists():
        raise FileNotFoundError(f"Original session folder not found: {original_session_dir}")
    validate_session_files(original_session_dir, "Original")

    original_context = extract_context_from_session(original_session_dir)
    student_id = args.student_id or str(original_context.get("student_id") or "student_demo_001")
    essay_id = args.essay_id or str(original_context.get("essay_id") or "1")
    task_type = args.task_type or str(original_context.get("task_type") or "WT2")
    prompt_text = args.prompt_text if args.prompt_text is not None else str(original_context.get("prompt_text") or "")
    write_json(work_dir / "original_context_extracted.json", original_context, pretty=True)

    # V4.0: required original Evaluator/WKE output is resolved/generated BEFORE Stage A.
    # In V3.3 this step existed as a helper but was accidentally not called before
    # run_workspace_builder(), so --evaluator-runner was ignored for the original essay.
    original_evaluator_output = resolve_required_evaluator_output(
        label="Original",
        python_exe=args.python,
        session_dir=original_session_dir,
        provided_path=Path(args.original_evaluator_output).resolve() if args.original_evaluator_output else None,
        evaluator_runner=evaluator_runner,
        work_dir=work_dir,
        student_id=student_id,
        essay_id=essay_id,
        prompt_text=prompt_text,
        no_llm=args.no_evaluator_llm,
        allow_missing=args.allow_missing_evaluator,
    )

    # Stage A: revision workspace builder.
    task = run_workspace_builder(
        python_exe=args.python,
        workspace_builder=workspace_builder,
        original_session_dir=original_session_dir,
        work_dir=work_dir,
        pretty=args.pretty,
        evaluator_output=original_evaluator_output,
        task_type=task_type,
        prompt_text=prompt_text,
    )
    if args.skip_stage_a_display:
        # The task was already printed by run_workspace_builder; this flag exists for future UI wrappers.
        pass

    # Stage B intake and full-pipeline.
    if args.revised_session_dir:
        revised_session_dir = Path(args.revised_session_dir).resolve()
        if not revised_session_dir.exists():
            raise FileNotFoundError(f"Revised session folder not found: {revised_session_dir}")
        validate_session_files(revised_session_dir, "Revised")
        print("\nUsing existing revised session folder:", revised_session_dir)
    else:
        raw_revised_essay = read_revised_essay(args)
        revised_essay = normalise_revised_input(raw_revised_essay)
        revised_essay_path = work_dir / "revised_essay.txt"
        write_text(revised_essay_path, revised_essay)

        gate = validate_revision_intake(str(original_context.get("essay_text") or ""), revised_essay, task)
        gate["normalisation_applied"] = (raw_revised_essay != revised_essay)
        write_json(work_dir / "revision_intake_gate_result.json", gate, pretty=True)
        if not gate["valid"] and not args.force_pipeline_on_invalid:
            report = render_invalid_revision_report(gate, task)
            write_text(work_dir / "revision_resubmission_instruction.md", report)
            mark_stage_b_not_run(work_dir, "revision_intake_gate_invalid")
            print("\n" + "=" * 70)
            print("REVISION INTAKE GATE: INCOMPLETE REVISION")
            print("=" * 70)
            print(report[:12000])
            print("=" * 70)
            print("\nFull pipeline was NOT run. Revise the full essay and run again.")
            print("Saved:", work_dir / "revision_intake_gate_result.json")
            print("Saved:", work_dir / "revision_resubmission_instruction.md")
            return 0

        submission = {
            "essay_id": essay_id,
            "student_id": student_id,
            "essay_text": revised_essay,
            "prompt_text": prompt_text,
            "task_type": task_type,
            "topic": args.topic,
            "created_for": "gold_essay_revision_full_pipeline_runner_v4_7_1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "revision_workspace_id": deep_get(task, ["identity", "submission_id"], None),
        }
        if args.expected_band is not None:
            try:
                submission["expected_band"] = float(args.expected_band)
            except ValueError:
                submission["expected_band"] = args.expected_band
        submission_path = work_dir / "revised_submission_for_full_pipeline.json"
        write_json(submission_path, submission, pretty=True)
        print("\nRevised submission JSON:", submission_path)

        _, revised_session_dir, _ = run_full_pipeline(
            python_exe=args.python,
            pipeline_runner=pipeline_runner,
            submission_json=submission_path,
            work_dir=work_dir,
        )
        print("\nRevised full-pipeline session detected:", revised_session_dir)
        validate_session_files(revised_session_dir, "Revised")

    # Required revised Evaluator/WKE output for Stage B WKM-aware comparison.
    revised_evaluator_output = resolve_required_evaluator_output(
        label="Revised",
        python_exe=args.python,
        session_dir=revised_session_dir,
        provided_path=Path(args.revised_evaluator_output).resolve() if args.revised_evaluator_output else None,
        evaluator_runner=evaluator_runner,
        work_dir=work_dir,
        student_id=student_id,
        essay_id=essay_id,
        prompt_text=prompt_text,
        no_llm=args.no_evaluator_llm,
        allow_missing=args.allow_missing_evaluator,
    )

    request = build_revision_request(
        original_session_dir=original_session_dir,
        revised_session_dir=revised_session_dir,
        task=task,
        student_id=student_id,
        essay_id=essay_id,
        task_type=task_type,
        prompt_text=prompt_text,
        original_evaluator_output=original_evaluator_output,
        revised_evaluator_output=revised_evaluator_output,
    )
    request_path = work_dir / "revision_request_v2_4.json"
    write_json(request_path, request, pretty=True)

    run_comparator_with_input(
        python_exe=args.python,
        revision_engine=revision_engine,
        request_path=request_path,
        work_dir=work_dir,
        pretty=args.pretty,
    )

    ai_comparison_generated = False
    ai_comparison_path = work_dir / "revision_ai_comparison_v1_7_1.json"
    if not args.skip_ai_comparison:
        ai_comparison_generated = run_ai_comparison_generator(
            python_exe=args.python,
            generator=ai_comparison_generator,
            work_dir=work_dir,
            pretty=args.pretty,
            use_llm=not args.no_ai_comparison_llm,
            model=args.ai_comparison_model,
            limit=args.ai_comparison_limit,
            max_attempts=args.ai_comparison_max_attempts,
            allow_schema_fallback=not args.disable_ai_schema_fallback,
        )

    # V4.7: render the student report AFTER AI comparison so availability messages are synchronized.
    run_student_report_renderer(
        python_exe=args.python,
        renderer=student_report_renderer,
        work_dir=work_dir,
        pretty=args.pretty,
        ai_comparison_path=ai_comparison_path if ai_comparison_generated else None,
    )

    ai_artifact = {}
    if ai_comparison_generated and ai_comparison_path.exists():
        try:
            ai_artifact = read_json(ai_comparison_path)
        except Exception:
            ai_artifact = {}
    ai_model_available = bool(ai_artifact.get("model_available_to_student"))
    ai_generation_status = ai_artifact.get("generation_status")
    ai_generation_strategy = ai_artifact.get("generation_strategy")
    ai_schema_version = ai_artifact.get("schema_version")
    ai_engine_version = ai_artifact.get("engine_version")
    ai_qa_status = deep_get(ai_artifact, ["qa", "status"], None) if ai_artifact else None
    ai_versioned_json = work_dir / "revision_ai_comparison_v1_7_1.json"
    ai_versioned_md = work_dir / "revision_ai_comparison_v1_7_1.md"
    ai_versioned_html = work_dir / "revision_ai_comparison_v1_7_1.html"
    ai_artifact_integrity = {
        "schema_version": ai_schema_version,
        "engine_version": ai_engine_version,
        "qa_status": ai_qa_status,
        "versioned_json_exists": ai_versioned_json.exists(),
        "versioned_markdown_exists": ai_versioned_md.exists(),
        "versioned_html_exists": ai_versioned_html.exists(),
        "student_visible_model_available": ai_model_available,
    }

    manifest = {
        "runner": "essay_revision_full_pipeline_runner_v4_7_1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "student_id": student_id,
        "essay_id": essay_id,
        "original_session_dir": str(original_session_dir),
        "revised_session_dir": str(revised_session_dir),
        "work_dir": str(work_dir),
        "revision_workspace_json": str(work_dir / "revision_workspace.json"),
        "revision_workspace_markdown": str(work_dir / "revision_workspace.md"),
        "revision_workspace_html": str(work_dir / "revision_workspace.html"),
        "revision_student_report_json": str(work_dir / "revision_student_report.json"),
        "revision_student_report_markdown": str(work_dir / "revision_student_report.md"),
        "revision_request_json": str(request_path),
        "revision_output_json": str(work_dir / "revision_output.json"),
        "revision_report_markdown": str(work_dir / "revision_report.md"),
        "ai_comparison_artifact_created": ai_comparison_generated,
        "ai_comparison_generated": ai_comparison_generated,
        "ai_model_available_to_student": ai_model_available,
        "ai_model_generation_status": ai_generation_status,
        "ai_model_generation_strategy": ai_generation_strategy,
        "active_ai_comparison_version": "v1_7_1",
        "ui_should_use_ai_comparison_json": str(ai_versioned_json) if ai_comparison_generated else None,
        "ui_should_use_ai_comparison_markdown": str(ai_versioned_md) if ai_comparison_generated else None,
        "ui_should_use_ai_comparison_html": str(ai_versioned_html) if ai_comparison_generated else None,
        "revision_ai_comparison_json": str(ai_versioned_json) if ai_comparison_generated else None,
        "revision_ai_comparison_markdown": str(ai_versioned_md) if ai_comparison_generated else None,
        "revision_ai_comparison_html": str(ai_versioned_html) if ai_comparison_generated else None,
        "ai_comparison_artifact_integrity": ai_artifact_integrity,
        "legacy_ai_comparison_files_ignored_by_manifest": True,
        "ai_comparison_versioned_outputs": True,
    }
    write_json(work_dir / "revision_run_manifest.json", manifest, pretty=True)

    print("\nDONE.")
    print("Workspace MD:     ", work_dir / "revision_workspace.md")
    print("Workspace HTML:   ", work_dir / "revision_workspace.html")
    print("Technical JSON:   ", work_dir / "revision_output.json")
    print("Technical report: ", work_dir / "revision_report.md")
    print("Student report:   ", work_dir / "revision_student_report.md")
    if not args.skip_ai_comparison:
        print("AI comparison:    ", work_dir / "revision_ai_comparison_v1_7_1.md")
    print("Manifest:        ", work_dir / "revision_run_manifest.json")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("\n[ERROR]", exc, file=sys.stderr)
        raise
