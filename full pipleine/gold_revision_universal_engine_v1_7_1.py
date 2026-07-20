#!/usr/bin/env python3
"""
VA Gold Essay Revision Universal Engine V1.4

Upstream-only revision workspace composer.
- Does NOT evaluate grammar independently.
- Does NOT classify errors.
- Does NOT use essay-specific topic patterns.
- Builds green/yellow/red from upstream Evaluator sentence-control + Detector/ErrorMap evidence.

Compatibility note:
If Evaluator V7.6 sentence-control payload is missing, V1.3 blocks green by default and
marks such sentences as yellow/needs-upstream-signal unless Detector/ErrorMap already supplies red/yellow evidence.
"""
from __future__ import annotations

import argparse
import copy
import html
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ENGINE_ID = "VA_GOLD_REVISION_UNIVERSAL_ENGINE"
ENGINE_VERSION = "1.7.1-student-safe-prewrite-guidance-stabilized"
SCHEMA_VERSION = "GOLD_REVISION_WORKSPACE_V1_7_1"

STATUS_RANK = {"green": 0, "unknown": 1, "yellow": 2, "red": 3}
DISPLAY_FOR_STATUS = {"green": "Keep", "yellow": "Improve", "red": "Rewrite", "unknown": "Check"}
EMOJI = {"green": "🟢", "yellow": "🟡", "red": "🔴", "unknown": "🟡"}

CRITERION_LABELS = {
    "TR": "Answer / ideas",
    "CC": "Flow",
    "LR": "Vocabulary",
    "GRA": "Grammar",
    "task_response": "Answer / ideas",
    "coherence_cohesion": "Flow",
    "cohesion_coherence": "Flow",
    "lexical_resource": "Vocabulary",
    "grammatical_range_accuracy": "Grammar",
    "grammar": "Grammar",
}
CRITERION_TAGS = {
    "task_response": "TR",
    "coherence_cohesion": "CC",
    "cohesion_coherence": "CC",
    "lexical_resource": "LR",
    "grammatical_range_accuracy": "GRA",
    "grammar": "GRA",
}

LOCAL_ERROR_FAMILY_LABELS = {
    "ARTICLE_DETERMINER": "article / noun form",
    "NOUN_NUMBER_COUNTABILITY": "noun number",
    "SUBJECT_VERB_AGREEMENT": "subject–verb control",
    "VERB_FORM": "verb form",
    "VERB_TENSE": "verb tense",
    "VERB_PATTERN": "verb pattern",
    "CLAUSE_STRUCTURE": "clause structure",
    "COMPARATIVE_FORM": "comparison form",
    "WORD_FORM": "word form",
    "COLLOCATION": "natural phrase",
    "LEXICAL_PRECISION": "word precision",
    "WORD_CHOICE": "word choice",
    "SPELLING": "spelling",
    "PREPOSITION_PATTERN": "preposition pattern",
    "GRAMMAR_PUNCTUATION": "punctuation",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Optional[Path]) -> Optional[Dict[str, Any]]:
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def write_json(path: Path, obj: Any, pretty: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2 if pretty else None)


def first_existing(paths: List[Path]) -> Optional[Path]:
    for p in paths:
        if p and p.exists():
            return p
    return None


def norm_status(value: Any) -> str:
    if value is None:
        return "unknown"
    v = str(value).strip().lower().replace(" ", "_")
    if v in {"green", "keep", "controlled", "full", "good", "clear", "ok", "pass"}:
        return "green"
    if v in {"yellow", "improve", "minor_instability", "partial", "limited", "needs_minor_repair", "repairable", "medium"}:
        return "yellow"
    if v in {"red", "rewrite", "unstable", "broken", "low", "blocked", "high", "needs_rewrite"}:
        return "red"
    if "red" in v or "rewrite" in v or "broken" in v or "blocked" in v or "unstable" in v:
        return "red"
    if "yellow" in v or "minor" in v or "partial" in v or "limited" in v or "repair" in v:
        return "yellow"
    if "green" in v or "controlled" in v or "full" in v:
        return "green"
    return "unknown"


def max_status(statuses: List[str]) -> str:
    if not statuses:
        return "unknown"
    return max((norm_status(s) for s in statuses), key=lambda s: STATUS_RANK.get(s, 1))


def criterion_tag(raw: Any) -> str:
    if raw is None:
        return ""
    r = str(raw).strip()
    if r in {"TR", "CC", "LR", "GRA"}:
        return r
    return CRITERION_TAGS.get(r, CRITERION_TAGS.get(r.lower(), r.upper() if len(r) <= 4 else ""))


def criterion_label_from_tag(tag: str) -> str:
    return CRITERION_LABELS.get(tag, CRITERION_LABELS.get(tag.lower(), tag))


def clean_text(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


TECHNICAL_TO_STUDENT_TEXT = [
    (r"No upstream sentence-control confirmation was provided, so this sentence should not be marked as Keep yet\.",
     "Check this sentence before keeping it. It has not been marked as safe to keep yet."),
    (r"Upstream engines flagged a ([^.]+?) problem\. Fix this before keeping the sentence\.",
     r"This sentence has a \1 issue to fix before you keep it."),
    (r"This sentence has (\d+) upstream local-control signals\. Rewrite or revise it carefully; do not mark it as keep\.",
     r"This sentence has \1 highlighted language problems. Rewrite or revise it carefully before keeping it."),
    (r"Keep this sentence only because upstream language and function control allow it\.",
     "Keep this sentence. It is clear enough for this revision step."),
    (r"Upstream paragraph-function control is missing; paragraph cannot be marked green yet\.",
     "Check the paragraph role before keeping this paragraph."),
    (r"Paragraph role is shown for layout, but upstream paragraph-function control is missing\. ",
     ""),
    (r"Evaluator sentence/paragraph control payload is incomplete\. ER has blocked unsafe green labels\.",
     "Some sentences need checking before they can be treated as Keep."),
    (r"(\d+) sentence\(s\) do not have evaluator sentence-control confirmation, so they are not treated as Keep\.",
     r"Check \1 sentence(s) before keeping them."),
    (r"Fixed-phrase lookup: .*?Correct: .*",
     "This phrase is not natural here. Rewrite it with one clear pattern."),
    (r"Should be [\'‘\"].*?[\'’\"].*",
     "This phrase is unnatural. Rewrite it in simpler natural English."),
    (r"The correct phrase is .*",
     "The phrase needs grammar or word-form repair."),
    (r"spaCy dep-parse: .*",
     "Check the subject and verb form."),
    (r"Confirm language-control signals",
     "Check sentences before keeping"),
    (r"Improve/check yellow areas",
     "Improve yellow areas"),
    (r"language and paragraph function",
     "language and paragraph role"),
    (r"Upstream local-control issue\.",
     "Language issue."),
]

TECHNICAL_WORDS_FOR_STUDENT = [
    "upstream", "evaluator", "detector", "errormap", "payload", "schema", "engine", "ER V1.4", "ER V1.3",
    "sentence-control", "paragraph-function", "local-control", "green blocked", "confidence gate", "row id", "machine payload",
]


def student_safe_text(text: Any) -> str:
    """Convert internal/debug wording into learner-facing text for Markdown/HTML only."""
    out = clean_text(text)
    if not out:
        return out
    for pattern, repl in TECHNICAL_TO_STUDENT_TEXT:
        out = re.sub(pattern, repl, out, flags=re.IGNORECASE)
    # Last-resort cleanup if a technical word slips through.
    lowered = out.lower()
    if any(w.lower() in lowered for w in TECHNICAL_WORDS_FOR_STUDENT):
        out = out.replace("upstream engines", "the app")
        out = out.replace("Upstream engines", "The app")
        out = out.replace("upstream", "app")
        out = out.replace("Upstream", "App")
        out = out.replace("evaluator sentence-control confirmation", "sentence check")
        out = out.replace("sentence-control confirmation", "sentence check")
        out = out.replace("paragraph-function control", "paragraph role check")
        out = out.replace("local-control signals", "highlighted language problems")
        out = out.replace("payload", "check")
        out = out.replace("ER V1.4", "the app")
        out = out.replace("ER V1.3", "the app")
    return clean_text(out)


def add_student_safe_view(ws: Dict[str, Any]) -> Dict[str, Any]:
    """Attach public-text fields while preserving full internal payload for debug JSON."""
    ws["student_visible_policy"] = {
        "hide_internal_terms": True,
        "student_facing_outputs": ["markdown", "html", "student_view"],
        "debug_terms_kept_only_in_json": True,
    }
    for w in ws.get("revision_waves", []) or []:
        w["student_title"] = student_safe_text(w.get("title"))
        w["student_text"] = student_safe_text(w.get("text"))
    for p in ws.get("annotated_essay", {}).get("paragraphs", []) or []:
        p["paragraph_hint_public"] = student_safe_text(p.get("paragraph_hint"))
        for a in p.get("function_alerts", []) or []:
            a["text_public"] = student_safe_text(a.get("text"))
        for snt in p.get("sentences", []) or []:
            snt["student_hint_public"] = student_safe_text(snt.get("student_hint"))
            for sp in snt.get("span_annotations", []) or []:
                sp["hint_public"] = student_safe_text(sp.get("hint"))
            for fa in snt.get("function_alerts", []) or []:
                fa["text_public"] = student_safe_text(fa.get("text"))
    return ws


def load_detector_result(detector: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not detector:
        return None
    if isinstance(detector.get("results"), list) and detector["results"]:
        return detector["results"][0]
    return detector


def extract_segmentation(detector_result: Optional[Dict[str, Any]], fallback_text: str = "") -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str, List[str]]:
    flags: List[str] = []
    if detector_result:
        seg = detector_result.get("segmentation") or {}
        paragraphs = copy.deepcopy(seg.get("paragraphs") or [])
        sentences = copy.deepcopy(seg.get("sentences") or [])
        essay_text = ""
        intake = detector_result.get("intake_record") or {}
        essay_text = intake.get("essay_text") or intake.get("raw_text") or fallback_text or ""
        if paragraphs and sentences:
            # Ensure one-based public indices exist.
            for p_i, p in enumerate(paragraphs, start=1):
                p.setdefault("paragraph_index", p_i)
                p.setdefault("role", p.get("paragraph_role") or _layout_role(p_i, len(paragraphs)))
            for s_i, s in enumerate(sentences, start=1):
                s.setdefault("global_sentence_index", s.get("sentence_index") or s_i)
                s.setdefault("sentence_index", s.get("global_sentence_index") or s_i)
                s.setdefault("text", clean_text(s.get("text")))
            return paragraphs, sentences, essay_text, flags
    # Fallback segmentation only for layout; not used as quality evaluation.
    flags.append("layout_fallback_segmentation_used")
    essay_text = fallback_text or ""
    raw_paras = [p.strip() for p in re.split(r"\n\s*\n", essay_text) if p.strip()]
    paragraphs = []
    sentences = []
    sent_no = 1
    offset = 0
    for p_no, p_text in enumerate(raw_paras, start=1):
        role = _layout_role(p_no, len(raw_paras))
        paragraphs.append({"paragraph_index": p_no, "text": p_text, "role": role})
        parts = [x.strip() for x in re.split(r"(?<=[.!?])\s+", p_text) if x.strip()]
        for local_i, s_text in enumerate(parts, start=1):
            sentences.append({"sentence_index": sent_no, "global_sentence_index": sent_no, "paragraph_index": p_no, "text": s_text})
            sent_no += 1
    return paragraphs, sentences, essay_text, flags


def _layout_role(p_no: int, total: int) -> str:
    if p_no == 1:
        return "introduction"
    if p_no == total and total > 1:
        return "conclusion"
    return "body"


def extract_score_summary(score: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not score:
        return {"overall_band": None, "criteria_bands": {}, "score_confidence": "missing"}
    final = score.get("final_score") or score.get("score_movement", {}).get("revised") or {}
    criteria = final.get("criteria") or final.get("criteria_bands") or score.get("criteria_bands") or {}
    return {
        "overall_band": final.get("overall_band") or final.get("overall") or score.get("overall_band"),
        "criteria_bands": criteria,
        "score_confidence": score.get("score_confidence") or final.get("confidence") or "available",
    }


def extract_errormap_rows(errormap: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not errormap:
        return []
    rows = errormap.get("errors") or errormap.get("rows") or []
    out = []
    for i, e in enumerate(rows):
        loc = e.get("location") or {}
        sent_idx = loc.get("sentence_index") or e.get("sentence_index")
        para_idx = loc.get("paragraph_index") or e.get("paragraph_index")
        try:
            sent_idx = int(sent_idx)
        except Exception:
            sent_idx = None
        try:
            para_idx = int(para_idx)
        except Exception:
            para_idx = None
        criterion = e.get("criterion") or e.get("category") or e.get("rubric")
        tag = criterion_tag(criterion)
        family = e.get("error_type") or e.get("family") or e.get("issue_family") or e.get("family_candidate") or "LOCAL_CONTROL"
        quote = loc.get("excerpt") or e.get("quote") or e.get("surface_quote") or ""
        sentence_text = loc.get("sentence") or e.get("sentence") or e.get("local_quote") or ""
        severity = (e.get("severity") or "moderate").lower()
        out.append({
            "error_id": e.get("error_id") or e.get("row_id") or f"row_{i+1}",
            "sentence_index": sent_idx,
            "paragraph_index": para_idx,
            "criterion_tag": tag,
            "criterion_label": criterion_label_from_tag(tag) if tag else "Language",
            "family": family,
            "family_label": LOCAL_ERROR_FAMILY_LABELS.get(str(family), str(family).replace("_", " ").title()),
            "quote": clean_text(quote),
            "sentence": clean_text(sentence_text),
            "severity": severity,
            # v1.7.2 fix: the real errormap schema (01b_errormap_v3.json)
            # carries its per-row explanation as `student_message`, with
            # `suggested_revision` giving the correction pattern -- neither
            # `explanation` nor `rationale` exist anywhere in the real data,
            # so this always silently fell back to the generic placeholder
            # below. Confirmed via direct inspection of a real errormap row:
            # student_message = "After have/has/had to, use base verb, not
            # past/participle/ing.", suggested_revision = "have/has/had to +
            # base verb" -- both real, specific, and previously unused here.
            "explanation": clean_text(
                e.get("student_message") or e.get("explanation") or e.get("rationale") or ""
            ),
            "suggested_revision": clean_text(e.get("suggested_revision") or ""),
            "char_start": loc.get("char_start") or e.get("start"),
            "char_end": loc.get("char_end") or e.get("end"),
            "source": "errormap",
        })
    return out


def _get_path(obj: Dict[str, Any], path: List[str]) -> Any:
    cur: Any = obj
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _find_first_list(obj: Dict[str, Any], candidate_paths: List[List[str]]) -> Optional[List[Any]]:
    for path in candidate_paths:
        val = _get_path(obj, path)
        if isinstance(val, list):
            return val
        if isinstance(val, dict) and isinstance(val.get("sentences"), list):
            return val.get("sentences")
        if isinstance(val, dict) and isinstance(val.get("paragraphs"), list):
            return val.get("paragraphs")
    return None


def extract_evaluator_control(evaluator: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract future V7.6 control payload; tolerate likely key variants."""
    result = {
        "available": bool(evaluator),
        "source_engine": None,
        "source_version": None,
        "sentence_control_available": False,
        "paragraph_function_available": False,
        "essay_function_available": False,
        "sentence_control_by_index": {},
        "paragraph_function_by_index": {},
        "example_quality_by_sentence": defaultdict(list),
        "qa": {},
        "raw_payload_present": False,
    }
    if not evaluator:
        return result
    meta = evaluator.get("metadata") or {}
    result["source_engine"] = meta.get("engine_id") or evaluator.get("engine_id")
    result["source_version"] = meta.get("engine_version") or evaluator.get("engine_version")
    cp = evaluator.get("consumer_payloads") or {}
    payload = (
        cp.get("essay_revision_control_payload")
        or cp.get("essay_function_payload")
        or cp.get("essay_revision_payload")
        or evaluator.get("essay_revision_control_payload")
        or {}
    )
    result["raw_payload_present"] = bool(payload)
    result["qa"] = payload.get("qa") or evaluator.get("qa") or {}

    sent_list = _find_first_list(payload, [
        ["sentence_control"],
        ["sentence_control_payload"],
        ["sentence_revision_signals"],
        ["sentences"],
    ])
    if sent_list:
        result["sentence_control_available"] = True
        for item in sent_list:
            if not isinstance(item, dict):
                continue
            idx = item.get("sentence_index") or item.get("global_sentence_index") or item.get("sentence_number")
            try:
                idx = int(idx)
            except Exception:
                continue
            # Accept both 0-based and 1-based by storing both if needed. Public engine uses 1-based.
            if idx == 0:
                idx = 1
            result["sentence_control_by_index"][idx] = item
            if idx + 1 not in result["sentence_control_by_index"] and item.get("index_base") == 0:
                result["sentence_control_by_index"][idx + 1] = item

    para_list = _find_first_list(payload, [
        ["paragraph_function"],
        ["paragraph_function_payload"],
        ["paragraphs"],
    ])
    if para_list:
        result["paragraph_function_available"] = True
        for item in para_list:
            if not isinstance(item, dict):
                continue
            idx = item.get("paragraph_index") or item.get("paragraph_number")
            try:
                idx = int(idx)
            except Exception:
                continue
            if idx == 0:
                idx = 1
            result["paragraph_function_by_index"][idx] = item
            if idx + 1 not in result["paragraph_function_by_index"] and item.get("index_base") == 0:
                result["paragraph_function_by_index"][idx + 1] = item

    examples = payload.get("example_quality") or payload.get("example_quality_alerts") or []
    if isinstance(examples, list):
        for ex in examples:
            if not isinstance(ex, dict):
                continue
            idx = ex.get("sentence_index")
            try:
                idx = int(idx)
            except Exception:
                continue
            if idx == 0:
                idx = 1
            result["example_quality_by_sentence"][idx].append(ex)
    result["essay_function_available"] = bool(payload.get("essay_function"))
    return result


def row_pressure(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "green"
    if len(rows) >= 2:
        return "red"
    sev = (rows[0].get("severity") or "moderate").lower()
    if sev in {"high", "severe", "critical"}:
        return "red"
    return "yellow"


def evaluator_sentence_status(signal: Optional[Dict[str, Any]]) -> Tuple[str, str, str, str, List[str]]:
    if not signal:
        return "unknown", "unknown", "unknown", "unknown", []
    language = norm_status(
        signal.get("language_control_status")
        or signal.get("grammar_control_status")
        or signal.get("revision_status_recommendation")
    )
    grammar = norm_status(signal.get("grammar_control_status"))
    lexical = norm_status(signal.get("lexical_control_status"))
    # v1.7.2 fix: real evaluator output (essay_revision_control_payload.
    # sentence_control[].semantic_recoverability, values like "full") uses
    # this key, not `semantic_recoverability_status`/`semantic_status` --
    # neither of which exist anywhere in real data. Confirmed by direct
    # inspection: every sentence-control row read this as "unknown" before,
    # silently defeating the semantic-recoverability signal entirely.
    semantic = norm_status(
        signal.get("semantic_recoverability")
        or signal.get("semantic_recoverability_status")
        or signal.get("semantic_status")
    )
    function = norm_status(signal.get("function_status") or signal.get("sentence_function_status"))
    statuses = [s for s in [language, grammar, lexical, semantic] if s != "unknown"]
    if statuses:
        language = max_status(statuses)
    observations = []
    for key in ("grammar_control_observations", "lexical_control_observations", "observations", "alerts"):
        vals = signal.get(key) or []
        if isinstance(vals, list):
            for v in vals:
                if isinstance(v, dict):
                    observations.append(clean_text(v.get("explanation") or v.get("student_hint") or v.get("observation_type")))
                else:
                    observations.append(clean_text(v))
    return language, grammar, lexical, function, [o for o in observations if o]


def paragraph_function_status(signal: Optional[Dict[str, Any]]) -> Tuple[str, List[str], List[str], List[str]]:
    if not signal:
        return "unknown", [], [], []
    status = norm_status(signal.get("paragraph_function_status") or signal.get("paragraph_status") or signal.get("paragraph_revision_status_recommendation"))
    strengths = signal.get("role_strengths") or signal.get("function_strengths") or []
    gaps = signal.get("role_gaps") or signal.get("function_alerts") or signal.get("alerts") or []
    actions = signal.get("revision_actions") or signal.get("revision_instructions") or []
    def to_texts(items: Any) -> List[str]:
        out = []
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict):
                    txt = it.get("text") or it.get("student_hint") or it.get("message")
                else:
                    txt = it
                if txt:
                    out.append(clean_text(txt))
        return out
    return status, to_texts(strengths), to_texts(gaps), to_texts(actions)


def universal_role_instruction(role: str) -> str:
    role = (role or "").lower()
    if role == "introduction":
        return "For an introduction, use only task framing, position, and main-argument preview. Do not add examples here."
    if role == "conclusion":
        return "For a conclusion, restate the final answer and summarize the main reasons. Do not introduce a new example."
    if role == "body":
        return "For a body paragraph, use one clear main idea, support it, and explain how the support proves the point."
    return "Check the paragraph role using the upstream evaluator output."


def build_workspace(
    detector: Optional[Dict[str, Any]],
    errormap: Optional[Dict[str, Any]],
    score: Optional[Dict[str, Any]],
    evaluator: Optional[Dict[str, Any]],
    *,
    compatibility_mode: bool = True,
) -> Dict[str, Any]:
    detector_result = load_detector_result(detector)
    fallback_text = ""
    if detector_result:
        fallback_text = (detector_result.get("intake_record") or {}).get("essay_text") or ""
    paragraphs, sentences, essay_text, seg_flags = extract_segmentation(detector_result, fallback_text)
    err_rows = extract_errormap_rows(errormap)
    rows_by_sentence: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    rows_by_para: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for row in err_rows:
        if row.get("sentence_index") is not None:
            rows_by_sentence[int(row["sentence_index"])].append(row)
        if row.get("paragraph_index") is not None:
            rows_by_para[int(row["paragraph_index"])].append(row)
    ev = extract_evaluator_control(evaluator)
    score_summary = extract_score_summary(score)

    sentence_objs_by_para: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    status_counts = Counter()
    unknown_control_count = 0
    green_blocked_by_missing_signal = 0
    green_blocked_by_detector = 0

    # v1.7.2: paragraph role needs to be known while building SENTENCE hints
    # (not just paragraph hints), so a poor-function-fit sentence can name its
    # actual paragraph role instead of a generic note. Computed once, ahead of
    # the sentence loop, using the identical role-resolution logic the
    # paragraph loop below uses -- kept in sync deliberately, not duplicated
    # by accident.
    role_by_para_idx: Dict[int, str] = {}
    for _p_no, _p in enumerate(paragraphs, start=1):
        _p_idx = int(_p.get("paragraph_index") or _p_no)
        role_by_para_idx[_p_idx] = (
            _p.get("role") or _p.get("paragraph_role") or _layout_role(_p_no, len(paragraphs))
        ).lower()

    # v1.7.2 fix: real evaluator output has no per-sentence function_status
    # field at all -- role/function fit is only assessed at the paragraph
    # level (essay_revision_control_payload.paragraph_function[]). Every
    # sentence's function_status was silently "unknown" before, which meant
    # function fit never contributed to a sentence's red/yellow/green status
    # or its hint, even though the signal exists one level up. Precompute it
    # here so a sentence can inherit its paragraph's function-fit status when
    # (as in all real data seen so far) it has no signal of its own.
    para_func_status_by_idx: Dict[int, str] = {}
    para_func_gap_by_idx: Dict[int, str] = {}
    for _p_idx2, _p_sig in ev["paragraph_function_by_index"].items():
        _status, _s2, _g2, _a2 = paragraph_function_status(_p_sig)
        para_func_status_by_idx[_p_idx2] = _status
        # Prefer the specific gap text (e.g. "Add a clear sentence that
        # gives your position or opinion.") over the generic role
        # instruction -- the same evaluator payload already carries this,
        # it just wasn't reused at the sentence-hint layer before.
        if _g2:
            para_func_gap_by_idx[_p_idx2] = _g2[0]

    for s in sentences:
        s_idx = int(s.get("global_sentence_index") or s.get("sentence_index"))
        p_idx = int(s.get("paragraph_index") or 1)
        text = clean_text(s.get("text"))
        local_rows = rows_by_sentence.get(s_idx, [])
        ev_sig = ev["sentence_control_by_index"].get(s_idx)
        ev_language, ev_grammar, ev_lexical, ev_function, ev_observations = evaluator_sentence_status(ev_sig)
        detector_pressure = row_pressure(local_rows)
        # Missing evaluator sentence-control must block green.
        missing_control = not bool(ev_sig)
        if missing_control:
            unknown_control_count += 1
        language_status = max_status([ev_language, detector_pressure])
        if missing_control and detector_pressure == "green":
            language_status = "unknown"
            green_blocked_by_missing_signal += 1
        if detector_pressure != "green":
            green_blocked_by_detector += 1
        function_status = ev_function if ev_function != "unknown" else para_func_status_by_idx.get(p_idx, "unknown")
        semantic_status = norm_status(
            (ev_sig.get("semantic_recoverability") or ev_sig.get("semantic_recoverability_status"))
            if isinstance(ev_sig, dict) else None
        )
        final_status = max_status([language_status, function_status, semantic_status])
        if final_status == "unknown":
            final_status = "yellow"
        # Green gate: all upstream controls must confirm green and no detector rows.
        green_gate_ok = (
            ev_sig is not None
            and language_status == "green"
            and (function_status in {"green", "unknown"})
            and (semantic_status in {"green", "unknown"})
            and detector_pressure == "green"
        )
        if not green_gate_ok and final_status == "green":
            final_status = "yellow"

        tags = []
        for row in local_rows:
            if row.get("criterion_tag"):
                tags.append(row["criterion_tag"])
        if function_status in {"yellow", "red"}:
            tags.append("TR")
        tags = sorted(set(t for t in tags if t))
        labels = [criterion_label_from_tag(t) for t in tags] or (["Language control"] if local_rows else ["Check"])

        span_annotations = []
        for row in local_rows:
            span_annotations.append({
                "span_text": row.get("quote") or "",
                "criterion_tag": row.get("criterion_tag") or "",
                "criterion_label": row.get("criterion_label") or "Language",
                "upstream_family": row.get("family"),
                "learner_label": row.get("family_label"),
                "hint": row.get("explanation") or "Check this part.",
                "severity": row.get("severity") or "moderate",
                "evidence_id": row.get("error_id"),
                "source": row.get("source"),
                "pre_revision_correction_visible": False,
            })

        # v1.7.2: hint content is now driven by which upstream signal is
        # actually the problem, not by error-presence alone (direct request:
        # sentence suggestions should weight the evaluator's/detector's
        # recoverability and function-fit measurements, not just whether an
        # errormap row exists). Priority order, most severe/most useful
        # first:
        #   1. semantic_status == red: a reader cannot recover the sentence's
        #      meaning as written. This is a rewrite-for-clarity problem, not
        #      a wording-level fix -- even if a specific error also exists,
        #      lead with the comprehension problem, since fixing one token
        #      won't fix an unrecoverable sentence.
        #   2. a specific errormap hit exists and recoverability is fine:
        #      today's targeted correction, but now surfaced using the real
        #      per-row explanation text (already computed into
        #      span_annotations[].hint above but never previously reused
        #      here -- this fell into the same "real text existed but a
        #      generic template was shown instead" bug already fixed once
        #      this session in Writing Coach).
        #   3. language/errormap and recoverability are both fine, but
        #      function/role fit is not: the sentence is clear but isn't
        #      doing the job its paragraph role needs (e.g. a body-paragraph
        #      sentence that reads fine but never actually supports the
        #      point). Names the real paragraph role via
        #      universal_role_instruction(), not a generic note.
        #   4. semantic_status == yellow with no harder signal: a softer
        #      clarity nudge, distinct from a grammar note.
        #   5-7: existing fallbacks, unchanged.
        if semantic_status == "red":
            base = "A reader can't reliably follow this sentence's meaning as written -- rewrite it for clarity; a smaller wording fix alone won't solve this."
            if local_rows:
                detail = clean_text(local_rows[0].get("explanation") or "")
                student_hint = f"{base} Related issue to fold into the rewrite: {detail}" if detail else base
            else:
                student_hint = base
        elif local_rows:
            if len(local_rows) >= 2:
                details = [clean_text(r.get("explanation")) for r in local_rows[:2] if clean_text(r.get("explanation"))]
                if details:
                    student_hint = f"This sentence has {len(local_rows)} language-control issues. Start here: {' Also: '.join(details)}"
                else:
                    student_hint = f"This sentence has {len(local_rows)} upstream local-control signals. Rewrite or revise it carefully; do not mark it as keep."
            else:
                row = local_rows[0]
                detail = clean_text(row.get("explanation") or "")
                pattern = clean_text(row.get("suggested_revision") or "").rstrip(".")
                if detail and pattern:
                    student_hint = f"{detail} Pattern to use: {pattern}."
                elif detail:
                    student_hint = detail
                else:
                    student_hint = f"Upstream engines flagged a {row.get('family_label', 'language-control')} problem. Fix this before keeping the sentence."
        elif function_status in {"yellow", "red"}:
            role_here = role_by_para_idx.get(p_idx, "")
            role_label = "body paragraph" if role_here == "body" else (role_here or "paragraph")
            article = "an" if role_label[:1] in "aeiou" else "a"
            # v1.7.3: prefer the real per-SENTENCE function-fit note (added
            # to the Evaluator this session -- sentence_function_note,
            # confirmed to correctly identify e.g. the specific closing
            # sentence of an introduction that never states a position, not
            # just "this paragraph has a problem somewhere") over the
            # paragraph-wide gap text, over the generic role instruction.
            # This is what lets two sentences in the same weak paragraph get
            # two different, individually accurate notes instead of both
            # repeating the same paragraph-level line.
            sent_note = clean_text((ev_sig or {}).get("sentence_function_note") or "") if isinstance(ev_sig, dict) else ""
            gap_text = para_func_gap_by_idx.get(p_idx, "")
            guidance = sent_note or gap_text or universal_role_instruction(role_here)
            student_hint = f"This sentence reads clearly, but it isn't doing the job {article} {role_label} needs here. {guidance}"
        elif semantic_status == "yellow":
            student_hint = "This sentence is understandable, but a reader has to work harder than they should to follow it -- consider rephrasing for more direct, unambiguous meaning."
        elif ev_observations:
            student_hint = ev_observations[0]
        elif missing_control:
            student_hint = "No upstream sentence-control confirmation was provided, so this sentence should not be marked as Keep yet."
        elif final_status != "green":
            # Real-world bug (reported by an actual student): no local
            # errormap row and no evaluator observation text were attached
            # to this sentence, but its own computed final_status is
            # yellow/red -- falling through to the "keep, it's fine"
            # message below directly contradicts the status badge shown
            # right next to it. Give an honest, still-actionable hint
            # instead of a false all-clear.
            student_hint = "This sentence needs another look -- re-read it for clarity, grammar, and whether it directly supports your point, even though no single error was pinpointed."
        else:
            student_hint = "Keep this sentence only because upstream language and function control allow it."

        function_alerts = []
        for ex in ev["example_quality_by_sentence"].get(s_idx, []):
            function_alerts.append({
                "level": norm_status(ex.get("example_quality_status") or ex.get("level") or "yellow"),
                "alert_type": ex.get("alert_type") or "example_quality",
                "text": clean_text(ex.get("student_hint") or ex.get("text") or "Improve the example quality."),
                "source": "evaluator_example_quality",
            })

        sent_obj = {
            "sentence_index": s_idx,
            "sentence_number": s_idx,
            "paragraph_index": p_idx,
            "text": text,
            "status": final_status,
            "status_label": DISPLAY_FOR_STATUS.get(final_status, "Check"),
            "language_status": language_status if language_status != "unknown" else "yellow_unknown_upstream",
            "function_status": function_status,
            "semantic_status": semantic_status,
            "detector_pressure_status": detector_pressure,
            "criterion_tags": tags,
            "criterion_labels": labels,
            "student_hint": student_hint,
            "span_annotations": span_annotations,
            "function_alerts": function_alerts,
            "evidence_sources": {
                "errormap_row_ids": [r.get("error_id") for r in local_rows],
                "evaluator_sentence_control_present": bool(ev_sig),
                "evaluator_sentence_control_source": "essay_revision_control_payload" if ev_sig else None,
            },
            "pre_revision_model_answer_allowed": False,
        }
        sentence_objs_by_para[p_idx].append(sent_obj)
        status_counts[final_status] += 1

    para_objs = []
    for p_no, p in enumerate(paragraphs, start=1):
        p_idx = int(p.get("paragraph_index") or p_no)
        role = (p.get("role") or p.get("paragraph_role") or _layout_role(p_no, len(paragraphs))).lower()
        p_sig = ev["paragraph_function_by_index"].get(p_idx)
        p_func_status, strengths, gaps, actions = paragraph_function_status(p_sig)
        # v1.4.13 Gold pipeline fix (stress-test Problem 9c): upstream
        # evaluator-sourced paragraph gaps are role-agnostic and can directly
        # contradict this engine's own role-based paragraph_hint -- e.g. the
        # introduction's paragraph_hint says "Do not add examples" (correct:
        # intros should only frame the task, state the position, and preview
        # arguments) while an upstream gap simultaneously says "Add a
        # specific example to support your point." for the SAME paragraph
        # (reproduced identically on both the weak and strong stress-test
        # essays). Filter out any introduction/conclusion gap that asks the
        # student to add/include a new example -- that structurally
        # conflicts with this engine's own guidance for those paragraph
        # types. Gaps asking to remove/adjust an existing example (e.g. "too
        # personal, use a wider example") are left alone: those reinforce,
        # rather than contradict, "do not add examples".
        role_conflict_gaps_suppressed: List[str] = []
        if role in {"introduction", "conclusion"}:
            kept_gaps: List[str] = []
            for g in gaps:
                low = (g or "").lower()
                removal_or_adjust_signal = bool(re.search(r"\bremove\b|\btoo personal\b|\badjust\b|\bwider\b", low))
                adds_new_example = "example" in low and not removal_or_adjust_signal
                if adds_new_example:
                    role_conflict_gaps_suppressed.append(g)
                else:
                    kept_gaps.append(g)
            gaps = kept_gaps
        local_sentences = sentence_objs_by_para.get(p_idx, [])
        sentence_status = max_status([s["status"] for s in local_sentences])
        if not p_sig:
            p_func_status = "unknown"
            gaps.append("Upstream paragraph-function control is missing; paragraph cannot be marked green yet.")
            actions.append(universal_role_instruction(role))
        paragraph_status = max_status([p_func_status, sentence_status])
        if paragraph_status == "unknown":
            paragraph_status = "yellow"
        # Missing paragraph function blocks green.
        if not p_sig and paragraph_status == "green":
            paragraph_status = "yellow"
        para_rows = rows_by_para.get(p_idx, [])
        para_tags = set()
        for r in para_rows:
            if r.get("criterion_tag"):
                para_tags.add(r["criterion_tag"])
        if p_func_status in {"yellow", "red", "unknown"}:
            para_tags.add("TR" if role in {"introduction", "conclusion"} else "CC")
        para_tags = sorted(para_tags)
        if not strengths and p_sig:
            strengths = ["Upstream evaluator found usable paragraph evidence."]
        paragraph_hint = _paragraph_hint(role, p_func_status, paragraph_status, bool(p_sig))
        para_objs.append({
            "paragraph_index": p_idx,
            "paragraph_number": p_idx,
            "paragraph_role": role,
            "paragraph_status": paragraph_status,
            "paragraph_status_label": DISPLAY_FOR_STATUS.get(paragraph_status, "Check"),
            "paragraph_function_status": p_func_status,
            "paragraph_function_label": DISPLAY_FOR_STATUS.get(p_func_status, "Check"),
            "criterion_tags": para_tags,
            "criterion_labels": [criterion_label_from_tag(t) for t in para_tags],
            "paragraph_hint": paragraph_hint,
            "function_strengths": strengths,
            "function_alerts": [{"level": "yellow" if p_func_status == "unknown" else p_func_status, "text": g, "source": "evaluator_or_missing_signal"} for g in gaps],
            "revision_instructions": actions or [universal_role_instruction(role)],
            "text": p.get("text") or "\n".join(s["text"] for s in local_sentences),
            "sentences": local_sentences,
            "evidence_sources": {
                "evaluator_paragraph_function_present": bool(p_sig),
                "errormap_row_count": len(para_rows),
                "role_conflict_gaps_suppressed": role_conflict_gaps_suppressed,
            }
        })

    revision_waves = build_revision_waves(para_objs, unknown_control_count)
    source_summary = {
        "score_summary": score_summary,
        "original_word_count": len(re.findall(r"\b\w+\b", essay_text)),
        "original_paragraph_count": len(paragraphs),
        "original_sentence_count": len(sentences),
        "displayed_sentence_status_counts": dict(status_counts),
        "displayed_error_family_counts": dict(Counter(r.get("family") for r in err_rows if r.get("family"))),
        "displayed_criterion_counts": dict(Counter(r.get("criterion_tag") for r in err_rows if r.get("criterion_tag"))),
        "evaluator_required": True,
        "evaluator_status": {
            "available": ev["available"],
            "source_engine": ev["source_engine"],
            "source_version": ev["source_version"],
            "sentence_control_available": ev["sentence_control_available"],
            "paragraph_function_available": ev["paragraph_function_available"],
            "essay_function_available": ev["essay_function_available"],
        },
    }
    integrity_status = "complete" if ev["sentence_control_available"] and ev["paragraph_function_available"] else "incomplete"
    qa_flags = list(seg_flags)
    if not ev["sentence_control_available"]:
        qa_flags.append("evaluator_sentence_control_missing_green_blocked")
    if not ev["paragraph_function_available"]:
        qa_flags.append("evaluator_paragraph_function_missing_green_blocked")
    if green_blocked_by_detector:
        qa_flags.append("detector_rows_blocked_green")
    workspace = {
        "schema_version": SCHEMA_VERSION,
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "revision_mode": "upstream_only_annotated_dual_pane_workspace",
        "upstream_signal_integrity": {
            "status": integrity_status,
            "evaluator_sentence_control_available": ev["sentence_control_available"],
            "evaluator_paragraph_function_available": ev["paragraph_function_available"],
            "missing_sentence_control_count": unknown_control_count,
            "green_blocked_by_missing_evaluator_signal": green_blocked_by_missing_signal,
            "green_blocked_by_detector_rows": green_blocked_by_detector,
            "message": "ER V1.7.1 does not mark green unless upstream language/function control permits it.",
        },
        "source_summary": source_summary,
        "ui_contract": {
            "layout": "left_annotated_essay_right_revision_editor",
            "default_explanations_collapsed": True,
            "copy_clean_text_available": True,
            "controls": [
                "show_all_highlights",
                "show_only_red",
                "show_yellow_and_red",
                "show_only_yellow",
                "show_hide_hints",
                "copy_clean_essay_to_editor",
                "submit_revised_essay",
            ],
            "color_legend": {
                "green": "Keep: upstream engines confirm good enough language and useful IELTS function.",
                "yellow": "Improve/check: upstream engines found repair need or sentence-control confirmation is missing.",
                "red": "Rewrite: upstream engines show low control, multiple local problems, or paragraph-function pressure.",
            },
        },
        "annotated_essay": {"paragraphs": para_objs},
        "revision_waves": revision_waves,
        "copyable_clean_text": essay_text,
        "revision_editor_seed": essay_text,
        "overall_revision_hints": build_overall_hints(para_objs, status_counts, integrity_status),
        "student_checklist": [
            "I fixed red sentences first.",
            "I checked every yellow sentence before keeping it.",
            "I did not treat a sentence as green unless the app marked it green.",
            "I kept paragraph breaks.",
            "I checked the introduction: no example, only task framing, position, and main arguments.",
            "I checked the conclusion: final answer and main reasons, no new example.",
            "I checked spelling and grammar after rewriting.",
        ],
        "pre_revision_correction_policy": {
            "full_corrected_sentence_visible": False,
            "direct_replacement_phrase_visible": False,
            "model_answer_visible": False,
            "reason": "The learner should self-correct before seeing a model version.",
        },
        "post_revision_ai_model_policy": {
            "available_after_self_revision": True,
            "pre_revision_model_answer_allowed": False,
            "recommended_display": "paragraph_first_revised_vs_ai_then_collapsible_sentence_details",
            "full_model_essay_default": False,
        },
        "machine_payload": {
            "errormap_rows_used": err_rows,
            "evaluator_control_summary": source_summary["evaluator_status"],
            "composition_policy": "upstream_only_no_hidden_evaluation",
        },
        "qa": {
            "status": "pass" if detector_result and errormap is not None else "warning",
            "flags": qa_flags,
            "no_pre_revision_corrections": True,
            "no_essay_specific_patterns": True,
            "upstream_only_mode": True,
        },
    }
    return workspace


def _paragraph_hint(role: str, function_status: str, paragraph_status: str, has_function_signal: bool) -> str:
    if not has_function_signal:
        return f"Paragraph role is shown for layout, but upstream paragraph-function control is missing. {universal_role_instruction(role)}"
    if role == "introduction":
        return "Use the introduction to frame the task, state the position, and preview the main arguments. Do not add examples."
    if role == "conclusion":
        return "Use the conclusion to restate the final answer and summarize the main reasons. Do not add a new example."
    if role == "body":
        return "Use one clear main idea, then support and explain it. Rewrite red sentences before adding new ideas."
    return "Revise according to the upstream paragraph-function signals."


def build_revision_waves(paragraphs: List[Dict[str, Any]], unknown_control_count: int) -> List[Dict[str, Any]]:
    waves = []
    red_ps = [p["paragraph_number"] for p in paragraphs if p.get("paragraph_status") == "red"]
    yellow_ps = [p["paragraph_number"] for p in paragraphs if p.get("paragraph_status") == "yellow"]
    if unknown_control_count:
        waves.append({
            "level": "yellow",
            "title": "Wave 1 — Confirm language-control signals",
            "text": f"{unknown_control_count} sentence(s) do not have evaluator sentence-control confirmation, so they are not treated as Keep.",
        })
    if red_ps:
        waves.append({
            "level": "red",
            "title": "Wave 2 — Rewrite red areas",
            "text": f"Paragraph(s) {', '.join(map(str, red_ps))}: rewrite red sentences first using short clear sentences.",
        })
    if yellow_ps:
        waves.append({
            "level": "yellow",
            "title": "Wave 3 — Improve/check yellow areas",
            "text": f"Paragraph(s) {', '.join(map(str, yellow_ps))}: check language and paragraph function before keeping these sentences.",
        })
    waves.append({
        "level": "yellow",
        "title": "Wave 4 — Final language pass",
        "text": "After rewriting, check only changed sentences for grammar, vocabulary, spelling, and paragraph flow.",
    })
    return waves


def build_overall_hints(paragraphs: List[Dict[str, Any]], counts: Counter, integrity: str) -> Dict[str, List[Dict[str, Any]]]:
    hints: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    if integrity != "complete":
        hints["upstream_status"].append({"level": "yellow", "text": "Evaluator sentence/paragraph control payload is incomplete. ER has blocked unsafe green labels."})
    if counts.get("red", 0):
        hints["language_repair"].append({"level": "red", "text": f"Rewrite {counts.get('red')} red sentence(s) before improving style or adding ideas."})
    if counts.get("yellow", 0):
        hints["language_repair"].append({"level": "yellow", "text": f"Check {counts.get('yellow')} yellow sentence(s); they are not safe Keep sentences yet."})
    for p in paragraphs:
        role = p.get("paragraph_role")
        if role in {"introduction", "conclusion"} and p.get("paragraph_status") != "green":
            hints["paragraph_function"].append({"level": p.get("paragraph_status"), "text": f"Paragraph {p.get('paragraph_number')} ({role}): {p.get('paragraph_hint_public') or p.get('paragraph_hint')}"})
    return dict(hints)




def infer_task_family_for_guidance(detector: Dict[str, Any]) -> str:
    text_parts = []
    try:
        res = first_result(detector)
        text_parts.append(str(deep_get(res, ["task_profile", "task_type"], "")))
        text_parts.append(str(deep_get(res, ["intake_record", "prompt_text"], "")))
        text_parts.append(str(deep_get(res, ["intake_record", "essay_text"], ""))[:1000])
    except Exception:
        pass
    t = " ".join(text_parts).lower()
    if any(x in t for x in ["advantage", "disadvantage", "outweigh", "benefit", "drawback"]):
        return "advantages_disadvantages"
    if any(x in t for x in ["problem", "solution", "solutions", "solve"]):
        return "problem_solution"
    if any(x in t for x in ["cause", "effect", "causes", "effects"]):
        return "causes_effects"
    if any(x in t for x in ["agree", "disagree", "opinion", "to what extent"]):
        return "opinion"
    if any(x in t for x in ["discuss both", "both views"]):
        return "discussion"
    return "generic_wt2"


def build_prewrite_guidance(task_family: str) -> Dict[str, Any]:
    """Student-facing planning guidance usable before initial writing and before revision."""
    common_formula = [
        "Topic sentence: say the paragraph's main idea.",
        "Argument/reason: explain why the idea matters.",
        "Development: add cause, effect, comparison, or consequence.",
        "Specific example: place/actor + action/situation + result.",
        "Link back: connect the example to the question or your position.",
    ]
    if task_family == "problem_solution":
        structures = [
            {
                "name": "Problems first, solutions second",
                "paragraphs": [
                    "Introduction: introduce the issue and preview the main problem and solution areas.",
                    "Body 1: problems only — explain one or two connected problems with a specific example.",
                    "Body 2: solutions only — explain matching solutions and how they solve the problems.",
                    "Conclusion: summarize the problem-solution logic. Do not add a new solution.",
                ],
            },
            {
                "name": "Problem-solution pairs",
                "paragraphs": [
                    "Introduction: introduce the issue and preview two problem-solution pairs.",
                    "Body 1: problem 1 + solution 1 — connect them directly.",
                    "Body 2: problem 2 + solution 2 — connect them directly.",
                    "Conclusion: summarize both pairs. Do not add a new solution.",
                ],
            },
        ]
    elif task_family == "advantages_disadvantages":
        structures = [{
            "name": "Advantages/disadvantages structure",
            "paragraphs": [
                "Introduction: frame the task, give your position, and preview main arguments.",
                "Body 1: one side only, usually the weaker side if you have an outweigh position.",
                "Body 2: the other side only, usually the side you support more strongly.",
                "Conclusion: summarize both sides and restate the final position. No new idea.",
            ],
        }]
    else:
        structures = [{
            "name": "Universal IELTS Task 2 structure",
            "paragraphs": [
                "Introduction: task frame, clear answer/position, preview main ideas.",
                "Body 1: one main idea, explanation, specific example, link.",
                "Body 2: one main idea, explanation, specific example, link.",
                "Conclusion: summary + final answer. No new example or idea.",
            ],
        }]
    return {
        "title": "Before you write or revise",
        "word_plan": {
            "minimum_words": 250,
            "recommended_range": "260-290 words",
            "paragraph_targets": {
                "introduction": "35-45 words",
                "body_1": "80-100 words",
                "body_2": "80-100 words",
                "conclusion": "35-45 words",
            },
        },
        "structure_options": structures,
        "body_paragraph_formula": common_formula,
        "example_quality": {
            "strong_example_rule": "Use a specific place/actor/programme/situation, show what happened, and link it to the argument.",
            "weak_example_rule": "Avoid private family anecdotes or very general examples as the main evidence.",
        },
    }


def attach_prewrite_guidance(ws: Dict[str, Any], detector: Dict[str, Any]) -> Dict[str, Any]:
    ws["prewriting_guidance"] = build_prewrite_guidance(infer_task_family_for_guidance(detector))
    return ws


def render_prewrite_md(ws: Dict[str, Any]) -> List[str]:
    g = ws.get("prewriting_guidance") or {}
    if not g:
        return []
    lines = ["## Before you write or revise", ""]
    wp = g.get("word_plan") or {}
    lines.append(f"Aim for at least **{wp.get('minimum_words', 250)} words**. A good practice target is **{wp.get('recommended_range', '260-290 words')}**.")
    lines.append("")
    lines.append("### Essay structure options")
    for opt in g.get("structure_options", []):
        lines.append(f"- **{opt.get('name')}**")
        for step in opt.get("paragraphs", []):
            lines.append(f"  - {step}")
    lines.append("")
    lines.append("### Body paragraph formula")
    for step in g.get("body_paragraph_formula", []):
        lines.append(f"- {step}")
    ex = g.get("example_quality", {})
    if ex:
        lines.append("")
        lines.append("### Example quality")
        lines.append(f"- Strong example: {ex.get('strong_example_rule')}")
        lines.append(f"- Weak example: {ex.get('weak_example_rule')}")
    lines.append("")
    return lines


def render_prewrite_html(ws: Dict[str, Any]) -> str:
    g = ws.get("prewriting_guidance") or {}
    if not g:
        return ""
    def esc(s: Any) -> str:
        return html.escape(str(s or ""))
    wp = g.get("word_plan") or {}
    opts = []
    for opt in g.get("structure_options", []):
        steps = "".join(f"<li>{esc(x)}</li>" for x in opt.get("paragraphs", []))
        opts.append(f"<details><summary>{esc(opt.get('name'))}</summary><ul>{steps}</ul></details>")
    formula = "".join(f"<li>{esc(x)}</li>" for x in g.get("body_paragraph_formula", []))
    ex = g.get("example_quality", {})
    return f"""
<div class='plan'><h3>Before you write or revise</h3>
<p>Aim for at least <b>{esc(wp.get('minimum_words', 250))} words</b>. Good practice target: <b>{esc(wp.get('recommended_range', '260-290 words'))}</b>.</p>
<h4>Essay structure options</h4>{''.join(opts)}
<h4>Body paragraph formula</h4><ul>{formula}</ul>
<p><b>Strong examples:</b> {esc(ex.get('strong_example_rule'))}</p>
<p><b>Avoid:</b> {esc(ex.get('weak_example_rule'))}</p></div>
"""

def render_markdown(ws: Dict[str, Any]) -> str:
    lines = []
    lines.append("# Essay Revision Workspace")
    lines.append("")
    lines.append("Use the colors to decide what to keep, improve, or rewrite before submitting your revised essay.")
    lines.append("")
    lines.extend(render_prewrite_md(ws))
    lines.append("## Revision plan")
    for w in ws.get("revision_waves", []):
        lines.append(f"- {EMOJI.get(w.get('level'), '🟡')} **{w.get('student_title') or w.get('title')}**: {w.get('student_text') or w.get('text')}")
    lines.append("")
    lines.append("## Annotated original essay")
    for p in ws.get("annotated_essay", {}).get("paragraphs", []):
        lines.append(f"\n### {EMOJI.get(p.get('paragraph_status'), '🟡')} Paragraph {p.get('paragraph_number')} — {p.get('paragraph_role', '').title()}")
        lines.append(f"_{p.get('paragraph_hint_public') or p.get('paragraph_hint')}_")
        for alert in p.get("function_alerts", []):
            lines.append(f"- {EMOJI.get(alert.get('level'), '🟡')} {alert.get('text_public') or alert.get('text')}")
        for s in p.get("sentences", []):
            labs = ", ".join(s.get("criterion_labels") or [])
            lines.append(f"\n{EMOJI.get(s.get('status'), '🟡')} **{s.get('status_label')}** ({labs}): {s.get('text')}")
            lines.append(f"  - Hint: {s.get('student_hint_public') or s.get('student_hint')}")
            for sp in s.get("span_annotations", []):
                quote = sp.get("span_text") or "whole sentence"
                lines.append(f"  - Focus: `{quote}` — {sp.get('hint_public') or sp.get('hint')}")
            for fa in s.get("function_alerts", []):
                lines.append(f"  - Function note: {fa.get('text_public') or fa.get('text')}")
    lines.append("\n## Revision editor seed")
    lines.append("```text")
    lines.append(ws.get("revision_editor_seed") or "")
    lines.append("```")
    return "\n".join(lines)


def render_html(ws: Dict[str, Any]) -> str:
    def e(s: Any) -> str:
        return html.escape(str(s or ""))
    paras_html = []
    for p in ws.get("annotated_essay", {}).get("paragraphs", []):
        p_status = p.get("paragraph_status", "yellow")
        sent_html = []
        for s in p.get("sentences", []):
            st = s.get("status", "yellow")
            spans = "".join(f"<li><b>{e(sp.get('span_text') or 'whole sentence')}</b>: {e(sp.get('hint_public') or sp.get('hint'))}</li>" for sp in s.get("span_annotations", []))
            if spans:
                spans = f"<ul class='spans'>{spans}</ul>"
            sent_html.append(f"""
            <div class='sentence {e(st)}' data-status='{e(st)}'>
              <div class='badge'>{EMOJI.get(st, '🟡')} {e(s.get('status_label'))}</div>
              <div class='sent-text'>{e(s.get('text'))}</div>
              <details><summary>Hint</summary><p>{e(s.get('student_hint_public') or s.get('student_hint'))}</p>{spans}</details>
            </div>
            """)
        alerts = "".join(f"<li>{e(a.get('text_public') or a.get('text'))}</li>" for a in p.get("function_alerts", []))
        paras_html.append(f"""
        <section class='paragraph {e(p_status)}' data-status='{e(p_status)}'>
          <h3>{EMOJI.get(p_status, '🟡')} Paragraph {e(p.get('paragraph_number'))} — {e(str(p.get('paragraph_role','')).title())}</h3>
          <p class='hint'>{e(p.get('paragraph_hint_public') or p.get('paragraph_hint'))}</p>
          {('<ul class="alerts">'+alerts+'</ul>') if alerts else ''}
          {''.join(sent_html)}
        </section>
        """)
    waves = "".join(f"<li class='{e(w.get('level'))}'>{EMOJI.get(w.get('level'), '🟡')} <b>{e(w.get('student_title') or w.get('title'))}</b>: {e(w.get('student_text') or w.get('text'))}</li>" for w in ws.get("revision_waves", []))
    clean = e(ws.get("revision_editor_seed") or "")
    return f"""<!doctype html>
<html><head><meta charset='utf-8'><title>Essay Revision Workspace</title>
<style>
body {{ margin:0; font-family:Arial, sans-serif; background:#f7f7f7; color:#222; }}
.header {{ background:#202124; color:#fff; padding:14px 20px; }}
.toolbar {{ background:#fff; border-bottom:1px solid #ddd; padding:10px 20px; position:sticky; top:0; z-index:5; }}
button {{ margin:3px; padding:8px 10px; border:1px solid #bbb; border-radius:8px; background:#fff; cursor:pointer; }}
.workspace {{ display:grid; grid-template-columns: minmax(360px, 1fr) minmax(360px, 1fr); gap:14px; padding:14px; }}
.panel {{ background:#fff; border:1px solid #ddd; border-radius:14px; padding:14px; min-height:75vh; }}
.plan {{ margin:14px; background:#fff; border:1px solid #ddd; border-radius:14px; padding:12px; }}
.paragraph {{ border:1px solid #ddd; border-radius:12px; padding:12px; margin:12px 0; }}
.paragraph.red {{ border-left:7px solid #d93025; }}
.paragraph.yellow {{ border-left:7px solid #f9ab00; }}
.paragraph.green {{ border-left:7px solid #188038; }}
.sentence {{ border:1px solid #ddd; border-radius:10px; padding:10px; margin:8px 0; background:#fafafa; }}
.sentence.red {{ background:#fdecea; }}
.sentence.yellow {{ background:#fff8e1; }}
.sentence.green {{ background:#e6f4ea; }}
.badge {{ font-weight:bold; margin-bottom:5px; }}
.hint {{ color:#444; }}
textarea {{ width:100%; min-height:68vh; box-sizing:border-box; border:1px solid #bbb; border-radius:12px; padding:12px; font-size:15px; line-height:1.45; }}
.small {{ color:#666; font-size:13px; }}
@media(max-width:900px) {{ .workspace {{ grid-template-columns:1fr; }} }}
</style>
<script>
function setFilter(mode) {{
  document.querySelectorAll('.sentence').forEach(el => {{
    const st = el.dataset.status;
    let show = mode==='all' || (mode==='red' && st==='red') || (mode==='yr' && (st==='yellow'||st==='red')) || (mode==='yellow' && st==='yellow');
    el.style.display = show ? '' : 'none';
  }});
  document.querySelectorAll('.paragraph').forEach(p => {{
    const visibleSentences = Array.from(p.querySelectorAll('.sentence')).some(s => s.style.display !== 'none');
    const pst = p.dataset.status;
    let showPara = mode==='all' || visibleSentences || (mode==='red' && pst==='red') || (mode==='yr' && (pst==='yellow'||pst==='red')) || (mode==='yellow' && pst==='yellow');
    p.style.display = showPara ? '' : 'none';
  }});
}}
function copyClean() {{
  const seed = document.getElementById('seed').textContent;
  document.getElementById('editor').value = seed;
}}
</script>
</head><body>
<div class='header'><h2>Essay Revision Workspace</h2><div class='small'>Hints only · no model answer before your own revision</div></div>
<div class='toolbar'>
  <button onclick="setFilter('all')">Show all</button>
  <button onclick="setFilter('red')">Only red</button>
  <button onclick="setFilter('yr')">Yellow + red</button>
  <button onclick="setFilter('yellow')">Only yellow</button>
  <button onclick="copyClean()">Copy clean essay to editor</button>
</div>
{render_prewrite_html(ws)}
<div class='plan'><h3>Revision plan</h3><ul>{waves}</ul><p class='small'>Start with red sentences, then improve yellow sentences. Keep green sentences unless you have a clear reason to change them.</p></div>
<div class='workspace'>
  <div class='panel'><h2>Annotated original</h2>{''.join(paras_html)}</div>
  <div class='panel'><h2>Your revision</h2><p class='small'>Write your own corrected essay here. Hints only; no model answer before self-correction.</p><textarea id='editor'></textarea><pre id='seed' style='display:none'>{clean}</pre></div>
</div>
</body></html>"""


def resolve_paths(args: argparse.Namespace) -> Dict[str, Optional[Path]]:
    session = Path(args.session_dir).resolve() if args.session_dir else None
    def opt(name: str, defaults: List[str]) -> Optional[Path]:
        raw = getattr(args, name)
        if raw:
            return Path(raw).resolve()
        if session:
            return first_existing([session / d for d in defaults])
        return None
    return {
        "detector": opt("detector_output", ["01_detector_output.json", "detector_output.json"]),
        "errormap": opt("errormap_output", ["01b_errormap_v3.json", "errormap.json"]),
        "score": opt("score_contract", ["02d_final_score_contract.json", "final_score_contract.json"]),
        "evaluator": opt("evaluator_output", ["07_evaluator_output.json", "evaluator_output.json"]),
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build student-safe Gold Essay Revision Workspace V1.7.1 from upstream outputs only.")
    ap.add_argument("--session-dir")
    ap.add_argument("--detector-output")
    ap.add_argument("--errormap-output")
    ap.add_argument("--score-contract")
    ap.add_argument("--evaluator-output")
    ap.add_argument("--output", required=True)
    ap.add_argument("--markdown")
    ap.add_argument("--html")
    ap.add_argument("--task-type", default="")
    ap.add_argument("--prompt-text", default="")
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)

    paths = resolve_paths(args)
    detector = read_json(paths["detector"])
    errormap = read_json(paths["errormap"])
    score = read_json(paths["score"])
    evaluator = read_json(paths["evaluator"])
    ws = build_workspace(detector, errormap, score, evaluator)
    attach_prewrite_guidance(ws, detector)
    add_student_safe_view(ws)
    ws["run_inputs"] = {k: str(v) if v else None for k, v in paths.items()}
    out = Path(args.output).resolve()
    write_json(out, ws, pretty=args.pretty)
    if args.markdown:
        Path(args.markdown).resolve().write_text(render_markdown(ws), encoding="utf-8")
    if args.html:
        Path(args.html).resolve().write_text(render_html(ws), encoding="utf-8")
    print(json.dumps({"status": "ok", "output": str(out), "schema_version": SCHEMA_VERSION}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
