#!/usr/bin/env python3
"""
premium_metric_profiler_v1.py
================================

Detector-side LLM metric profiler for VA/ST.ELLA Premium scoring.

Purpose
-------
The Premium detector is LLM-based and has access to raw essay text, prompt,
segmentation, Layer 0 idea map, Layer 0.5 semantic recoverability, strengths,
and diagnostic rows. This module enriches detector outputs with a structured
`premium_metric_profile` and maps that profile into the scorer's named metric
profile fields.

The scorer can then remain deterministic: metrics -> composites -> thresholds
-> caps -> bands.

Cost control
------------
- Runs once per essay and caches results by prompt+essay hash.
- Can run in --no-llm mode for smoke tests and calibration plumbing.
- Never asks the LLM for a final IELTS band. It asks for bounded metrics only.

Input
-----
Single DETECTOR_OUTPUT_V1.1 dict or batch wrapper with top-level results[].

Output
------
Same structure enriched with:
- premium_metric_profile
- scorer_payload.premium_metric_profile
- scorer_payload.premium_metric_profile_mapped_metrics
- detector_metric_profile fields mapped from profiler metrics

Usage
-----
python premium_metric_profiler_v1.py \
  --input detector_batch.json \
  --output detector_batch_profiled.json \
  --cache-dir premium_metric_cache \
  --pretty

No-token smoke test:
python premium_metric_profiler_v1.py -i detector_batch.json -o profiled.json --no-llm --pretty
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROFILE_VERSION = "premium_metric_profile_v1"
DEFAULT_MODEL = os.environ.get("PREMIUM_METRIC_PROFILER_MODEL", os.environ.get("VIP_CHEAP_MODEL", "gpt-4o-mini"))

RUBRICS = ("task_response", "coherence_cohesion", "lexical_resource", "grammar")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clamp01(x: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(x)))
    except Exception:
        return default


def safe_div(a: float, b: float) -> float:
    return float(a) / float(b) if b else 0.0


def nested_get(d: Any, *keys: str, default: Any = None) -> Any:
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


def nested_set(d: Dict[str, Any], path: Tuple[str, ...], value: Any) -> None:
    cur = d
    for k in path[:-1]:
        if not isinstance(cur.get(k), dict):
            cur[k] = {}
        cur = cur[k]
    cur[path[-1]] = value


def words(text: str) -> List[str]:
    return re.findall(r"[A-Za-z][A-Za-z'\-]*", text or "")


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def content_hash(prompt: str, essay: str) -> str:
    raw = json.dumps({"prompt": prompt or "", "essay": essay or ""}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:24]


def extract_text_prompt(payload: Dict[str, Any]) -> Tuple[str, str]:
    essay = (
        nested_get(payload, "intake_record", "essay_text")
        or nested_get(payload, "intake_record", "raw_text")
        or nested_get(payload, "intake", "essay_text")
        or nested_get(payload, "scorer_payload", "essay_text")
        or payload.get("essay_text")
        or payload.get("text")
        or ""
    )
    prompt = (
        nested_get(payload, "intake_record", "prompt_text")
        or nested_get(payload, "intake", "prompt_text")
        or nested_get(payload, "scorer_payload", "prompt_text")
        or payload.get("prompt_text")
        or payload.get("prompt")
        or ""
    )
    return str(essay or ""), str(prompt or "")


def extract_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = nested_get(payload, "scorer_payload", "chargeable_detector_rows")
    if isinstance(rows, list):
        return rows
    rows = payload.get("student_rows")
    if isinstance(rows, list):
        return rows
    return []


def extract_metadata(payload: Dict[str, Any], essay_text: str = "") -> Dict[str, int]:
    candidates = [
        nested_get(payload, "scorer_payload", "metadata"),
        nested_get(payload, "detector_metric_profile", "shared"),
        payload.get("generated_metadata"),
        nested_get(payload, "headline", "summary"),
    ]
    meta: Dict[str, int] = {}
    aliases = {
        "word_count": ("word_count", "n_words"),
        "sentence_count": ("sentence_count", "n_sentences"),
        "paragraph_count": ("paragraph_count", "n_paragraphs", "validated_paragraph_count"),
    }
    for c in candidates:
        if not isinstance(c, dict):
            continue
        for out_k, ks in aliases.items():
            if out_k in meta:
                continue
            for k in ks:
                if c.get(k) is not None:
                    try:
                        meta[out_k] = int(c[k])
                        break
                    except Exception:
                        pass
    if essay_text:
        meta.setdefault("word_count", len(words(essay_text)))
        rough_sent = re.findall(r"[^.!?]+(?:[.!?]+|$)", essay_text)
        meta.setdefault("sentence_count", max(1, len([s for s in rough_sent if s.strip()])))
        meta.setdefault("paragraph_count", max(1, len([p for p in re.split(r"\n\s*\n", essay_text) if p.strip()])))
    meta.setdefault("word_count", 250)
    meta.setdefault("sentence_count", 16)
    meta.setdefault("paragraph_count", 4)
    return meta


def count_rows_by(rows: List[Dict[str, Any]], field: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in rows:
        v = str(r.get(field) or r.get("primary_" + field) or "unknown").lower()
        out[v] = out.get(v, 0) + 1
    return out


def row_density(rows: List[Dict[str, Any]], rubric: str, wc: int) -> float:
    total = 0.0
    for r in rows:
        rr = str(r.get("rubric") or r.get("primary_rubric") or "").lower()
        if rr == rubric:
            try:
                total += float(r.get("score_charge_weight", r.get("score_weight", 1.0)) or 1.0)
            except Exception:
                total += 1.0
    return total / max(1, wc) * 100.0


def default_profile() -> Dict[str, Any]:
    return {
        "version": PROFILE_VERSION,
        "source": "uninitialized",
        "created_at": now_iso(),
        "task_response": {
            "prompt_part_coverage": 0.55,
            "position_clarity": 0.55,
            "position_consistency": 0.62,
            "relevance_ratio": 0.58,
            "idea_development_depth": 0.55,
            "support_specificity": 0.52,
            "example_integration": 0.50,
            "conclusion_alignment": 0.55,
            "irrelevance_or_repetition_risk": 0.25,
            "confidence": 0.50,
        },
        "coherence_cohesion": {
            "global_progression": 0.55,
            "paragraph_role_clarity": 0.55,
            "paragraph_balance": 0.55,
            "topic_sentence_control": 0.50,
            "intra_paragraph_sequencing": 0.55,
            "inter_paragraph_linking": 0.52,
            "reference_clarity": 0.58,
            "cohesion_naturalness": 0.55,
            "mechanical_cohesion_risk": 0.35,
            "confidence": 0.50,
        },
        "lexical_resource": {
            "lexical_range": 0.55,
            "topic_vocabulary_precision": 0.55,
            "word_choice_precision": 0.55,
            "collocation_naturalness": 0.52,
            "semantic_combination_control": 0.52,
            "register_appropriacy": 0.58,
            "phrase_naturalness": 0.52,
            "lexical_sophistication": 0.50,
            "multiword_density": 0.35,
            "repetition_or_simplification_risk": 0.30,
            "word_formation_control": 0.65,
            "spelling_control": 0.65,
            "high_band_lexical_evidence": 0.35,
            "confidence": 0.50,
        },
        "grammar": {
            "sentence_control_stability": 0.55,
            "simple_sentence_control": 0.62,
            "compound_sentence_control": 0.55,
            "complex_structure_success": 0.48,
            "grammar_range_control": 0.50,
            "error_free_sentence_quality": 0.50,
            "malformed_clause_risk": 0.35,
            "communicative_effect_of_errors": 0.58,
            "punctuation_control": 0.70,
            "severe_grammar_error_density": 0.20,
            "overall_grammar_error_density_per_100w": 0.35,
            "confidence": 0.50,
        },
        "shared": {
            "semantic_recoverability": 0.60,
            "proposition_stability": 0.60,
            "local_language_damage": 0.35,
            "discourse_evaluability": 0.60,
            "high_band_readiness": 0.35,
            "weak_writing_probability": 0.45,
            "scorer_confidence_recommendation": 0.55,
            "confidence": 0.50,
        },
        "evidence": {
            "summary": "fallback profile not yet populated by LLM",
            "strongest_positive_evidence": [],
            "main_risks": [],
        },
        "audit": {
            "final_band_generated": False,
            "allowed_to_set_final_band": False,
        },
    }


def heuristic_profile(payload: Dict[str, Any], essay_text: str, prompt: str) -> Dict[str, Any]:
    """No-token fallback. Conservative, useful for smoke tests and pipeline validation."""
    p = default_profile()
    rows = extract_rows(payload)
    meta = extract_metadata(payload, essay_text)
    wc, sc, pc = meta["word_count"], meta["sentence_count"], meta["paragraph_count"]
    dmp = payload.get("detector_metric_profile") or {}
    shared = dmp.get("shared") or {}
    sem_rec = clamp01(shared.get("semantic_recoverability"), 0.60)
    adr = clamp01(shared.get("affected_discourse_ratio"), 0.30)
    grammar_density = row_density(rows, "grammar", wc)
    lr_density = row_density(rows, "lexical_resource", wc)
    tr_rows = count_rows_by(rows, "rubric").get("task_response", 0)
    cc_rows = count_rows_by(rows, "rubric").get("coherence_cohesion", 0)
    gra_rows = count_rows_by(rows, "rubric").get("grammar", 0)
    lr_rows = count_rows_by(rows, "rubric").get("lexical_resource", 0)

    enough_length = clamp01((wc - 120) / 140.0)
    para_quality = 0.25 if pc <= 1 else 0.55 if pc == 2 else 0.75 if pc == 3 else 0.82
    sent_control = clamp01(1.0 - min(1.0, grammar_density / 5.0) * 0.55 - adr * 0.25)
    lr_control = clamp01(1.0 - min(1.0, lr_density / 4.5) * 0.55)
    support_markers = len(re.findall(r"\b(for example|for instance|such as|because|therefore|as a result|this means|research|study|evidence)\b", essay_text, re.I))
    conclusion_markers = len(re.findall(r"\b(in conclusion|to conclude|overall|to sum up|in summary)\b", essay_text, re.I))
    stance_markers = len(re.findall(r"\b(i believe|i think|in my opinion|i agree|i disagree|it is clear|should|must)\b", essay_text, re.I))
    discourse_markers = len(re.findall(r"\b(however|therefore|moreover|furthermore|on the other hand|firstly|secondly|finally|in addition)\b", essay_text, re.I))

    p["source"] = "heuristic_no_llm_fallback"
    p["task_response"].update({
        "prompt_part_coverage": clamp01((0.45 + enough_length * 0.20 + (0.15 if prompt else 0.0)) - tr_rows * 0.04),
        "position_clarity": clamp01(0.45 + min(0.25, stance_markers * 0.08)),
        "position_consistency": clamp01(0.65 - tr_rows * 0.03),
        "relevance_ratio": clamp01(0.55 + (0.10 if prompt else 0.0) - tr_rows * 0.05),
        "idea_development_depth": clamp01(0.35 + enough_length * 0.25 + min(0.20, support_markers * 0.06)),
        "support_specificity": clamp01(0.30 + min(0.35, support_markers * 0.08)),
        "example_integration": clamp01(0.30 + min(0.30, support_markers * 0.08)),
        "conclusion_alignment": 0.75 if conclusion_markers else 0.35,
        "irrelevance_or_repetition_risk": clamp01(0.25 + tr_rows * 0.08),
        "confidence": 0.46,
    })
    p["coherence_cohesion"].update({
        "global_progression": clamp01(0.38 + para_quality * 0.25 + min(0.16, discourse_markers * 0.03) - adr * 0.20 - cc_rows * 0.04),
        "paragraph_role_clarity": para_quality,
        "paragraph_balance": para_quality,
        "topic_sentence_control": clamp01(para_quality - 0.05),
        "intra_paragraph_sequencing": clamp01(0.45 + sem_rec * 0.20 - adr * 0.20),
        "inter_paragraph_linking": clamp01(0.40 + min(0.25, discourse_markers * 0.05) - (0.15 if pc <= 1 else 0.0)),
        "reference_clarity": clamp01(0.58 + sem_rec * 0.15 - adr * 0.25),
        "cohesion_naturalness": clamp01(0.52 + min(0.20, discourse_markers * 0.03) - adr * 0.20),
        "mechanical_cohesion_risk": clamp01(0.20 + max(0, discourse_markers - 6) * 0.08 + (0.15 if pc <= 1 and discourse_markers >= 4 else 0)),
        "confidence": 0.46,
    })
    p["lexical_resource"].update({
        "lexical_range": clamp01(0.48 + enough_length * 0.16 + lr_control * 0.18),
        "topic_vocabulary_precision": clamp01(0.48 + lr_control * 0.20),
        "word_choice_precision": clamp01(0.45 + lr_control * 0.28 - lr_rows * 0.02),
        "collocation_naturalness": clamp01(0.42 + lr_control * 0.28 - lr_rows * 0.02),
        "semantic_combination_control": clamp01(0.42 + lr_control * 0.28 - lr_rows * 0.02),
        "register_appropriacy": clamp01(0.55 + lr_control * 0.16),
        "phrase_naturalness": clamp01(0.42 + lr_control * 0.30),
        "lexical_sophistication": clamp01(0.42 + enough_length * 0.15 + lr_control * 0.18),
        "multiword_density": clamp01(0.25 + min(0.25, support_markers * 0.03)),
        "repetition_or_simplification_risk": clamp01(0.25 + lr_rows * 0.03),
        "word_formation_control": lr_control,
        "spelling_control": clamp01(1.0 - min(1.0, lr_density / 4.0) * 0.65),
        "high_band_lexical_evidence": clamp01(0.25 + lr_control * 0.35),
        "confidence": 0.46,
    })
    p["grammar"].update({
        "sentence_control_stability": sent_control,
        "simple_sentence_control": clamp01(0.55 + sent_control * 0.25),
        "compound_sentence_control": clamp01(0.45 + sent_control * 0.25),
        "complex_structure_success": clamp01(0.35 + sent_control * 0.25 - gra_rows * 0.02),
        "grammar_range_control": clamp01(0.35 + min(0.30, sc / 12.0) + sent_control * 0.20),
        "error_free_sentence_quality": sent_control,
        "malformed_clause_risk": clamp01(1.0 - sent_control),
        "communicative_effect_of_errors": clamp01(0.45 + sent_control * 0.35),
        "punctuation_control": clamp01(0.65 + sent_control * 0.20),
        "severe_grammar_error_density": clamp01(grammar_density / 5.0),
        "overall_grammar_error_density_per_100w": clamp01(grammar_density / 6.0),
        "confidence": 0.46,
    })
    weak_prob = clamp01(0.25 + (1 - sem_rec) * 0.25 + (1 - sent_control) * 0.25 + (0.18 if pc <= 1 and wc >= 180 else 0.0) + (0.12 if lr_density > 2 else 0.0))
    high_ready = clamp01((p["task_response"]["idea_development_depth"] + p["coherence_cohesion"]["global_progression"] + p["lexical_resource"]["phrase_naturalness"] + p["grammar"]["sentence_control_stability"]) / 4.0 - weak_prob * 0.20)
    p["shared"].update({
        "semantic_recoverability": sem_rec,
        "proposition_stability": clamp01(sem_rec + 0.05),
        "local_language_damage": clamp01(1 - sent_control),
        "discourse_evaluability": clamp01(sem_rec - adr * 0.25),
        "high_band_readiness": high_ready,
        "weak_writing_probability": weak_prob,
        "scorer_confidence_recommendation": 0.55,
        "confidence": 0.46,
    })
    p["evidence"] = {
        "summary": "Heuristic fallback profile. Use for smoke tests only; run with LLM for production calibration.",
        "strongest_positive_evidence": [],
        "main_risks": ["heuristic_profile_not_llm"],
    }
    p["audit"] = {"final_band_generated": False, "allowed_to_set_final_band": False, "llm_used": False}
    return validate_profile(p)


def build_llm_prompt(payload: Dict[str, Any], essay_text: str, prompt_text: str) -> str:
    meta = extract_metadata(payload, essay_text)
    rows = extract_rows(payload)
    row_sample = []
    for r in rows[:30]:
        row_sample.append({
            "rubric": r.get("rubric") or r.get("primary_rubric"),
            "family": r.get("family") or r.get("primary_family"),
            "severity": r.get("severity"),
            "quote": str(r.get("quote") or "")[:120],
            "explanation": str(r.get("explanation") or r.get("problem_statement") or "")[:200],
        })
    layer0 = payload.get("layer0_idea_map") or nested_get(payload, "scorer_payload", "layer0_idea_map") or {}
    semantic = payload.get("layer0_5_semantic_recoverability") or nested_get(payload, "scorer_payload", "semantic_recoverability_profile") or {}
    strengths = nested_get(payload, "evaluator_payload", "strengths_profile") or {}
    task_profile = payload.get("task_profile") or nested_get(payload, "scorer_payload", "task_profile") or {}
    context = {
        "metadata": meta,
        "task_profile": task_profile,
        "semantic_summary": semantic.get("semantic_summary") if isinstance(semantic, dict) else {},
        "layer0_summary_keys": list(layer0.keys())[:20] if isinstance(layer0, dict) else [],
        "strengths_summary": strengths,
        "row_counts_by_rubric": count_rows_by(rows, "rubric"),
        "row_counts_by_family": count_rows_by(rows, "family"),
        "row_sample": row_sample,
    }
    essay_trim = essay_text[:7000]
    return f"""
You are the VA Premium Metric Profiler for IELTS Writing Task 2.

Task: produce calibrated 0..1 metrics only. Do NOT produce final IELTS bands.
Use the prompt, essay text, detector rows, Layer 0/0.5 summaries, and strengths.
Be strict: do not treat academic-looking vocabulary or absence of local errors as high-band proof.
If language is malformed, lower discourse evaluability and restrict TR/CC confidence.

Return one valid JSON object with exactly these top-level keys:
version, source, task_response, coherence_cohesion, lexical_resource, grammar, shared, evidence, audit.
All metric values must be numbers from 0 to 1. Higher = better except risk fields explicitly ending in _risk, _damage, _probability, _density.
Do not include markdown.

Metric schema:
{json.dumps(default_profile(), ensure_ascii=False, indent=2)}

Prompt:
{prompt_text or '[missing]'}

Essay:
{essay_trim}

Existing detector context:
{json.dumps(context, ensure_ascii=False, indent=2)[:9000]}
""".strip()


def call_openai_json(prompt: str, model: str = DEFAULT_MODEL, timeout: int = 90) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    audit = {"provider": "openai", "model": model, "used": False, "error": None, "latency_ms": None}
    if not api_key:
        audit["error"] = "OPENAI_API_KEY_missing"
        return None, audit
    try:
        from openai import OpenAI  # type: ignore
    except Exception as e:
        audit["error"] = f"openai_import_failed: {e}"
        return None, audit
    t0 = time.perf_counter()
    try:
        client = OpenAI(api_key=api_key, timeout=timeout)
        resp = client.chat.completions.create(
            model=model,
            temperature=0,
            max_tokens=2200,
            messages=[
                {"role": "system", "content": "You output strict JSON only. You are a calibrated IELTS metric profiler, not a band scorer."},
                {"role": "user", "content": prompt},
            ],
        )
        content = resp.choices[0].message.content or ""
        audit["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        audit["used"] = True
        data = extract_json(content)
        if not isinstance(data, dict):
            audit["error"] = "json_parse_failed"
            return None, audit
        return data, audit
    except Exception as e:
        audit["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        audit["error"] = str(e)[:500]
        return None, audit


def extract_json(text: str) -> Any:
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except Exception:
        m = re.search(r"(\{.*\})", t, flags=re.S)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                return None
    return None


# Allowed metric keys are taken from the default profile. Unknown keys are ignored.
def validate_profile(raw: Dict[str, Any]) -> Dict[str, Any]:
    base = default_profile()
    out = copy.deepcopy(base)
    if isinstance(raw, dict):
        out["version"] = str(raw.get("version") or PROFILE_VERSION)
        out["source"] = str(raw.get("source") or out["source"])
        out["created_at"] = str(raw.get("created_at") or now_iso())
        for section in ("task_response", "coherence_cohesion", "lexical_resource", "grammar", "shared"):
            if isinstance(raw.get(section), dict):
                for k in out[section].keys():
                    if k in raw[section]:
                        out[section][k] = clamp01(raw[section][k], out[section][k])
        if isinstance(raw.get("evidence"), dict):
            out["evidence"].update(raw["evidence"])
        if isinstance(raw.get("audit"), dict):
            out["audit"].update(raw["audit"])
    out["version"] = PROFILE_VERSION
    out["audit"]["final_band_generated"] = False
    out["audit"]["allowed_to_set_final_band"] = False
    return out


def profile_to_dmp(profile: Dict[str, Any], metadata: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    tr = profile["task_response"]
    cc = profile["coherence_cohesion"]
    lr = profile["lexical_resource"]
    gr = profile["grammar"]
    sh = profile["shared"]
    metadata = metadata or {}
    grammar_damage_index = clamp01(gr.get("malformed_clause_risk", 0.35) * 0.45 + sh.get("local_language_damage", 0.35) * 0.35 + gr.get("overall_grammar_error_density_per_100w", 0.35) * 0.20)
    lexical_damage_index = clamp01((1 - lr.get("word_choice_precision", 0.55)) * 0.30 + (1 - lr.get("phrase_naturalness", 0.52)) * 0.35 + lr.get("repetition_or_simplification_risk", 0.30) * 0.20 + (1 - lr.get("collocation_naturalness", 0.52)) * 0.15)
    mapped = {
        "task_response": {
            "TR1_prompt_part_coverage": tr["prompt_part_coverage"],
            "TR2_position_clarity": tr["position_clarity"],
            "TR3_position_consistency": tr["position_consistency"],
            "TR4_relevance_ratio": tr["relevance_ratio"],
            "TR5_idea_extension_depth": tr["idea_development_depth"],
            "TR6_support_quality": tr["support_specificity"],
            "TR7_conclusion_alignment": tr["conclusion_alignment"],
            "TR8_irrelevant_or_repetitive_content_rate": tr["irrelevance_or_repetition_risk"],
        },
        "coherence_cohesion": {
            "CC1_global_logical_progression": cc["global_progression"],
            "CC2_paragraph_topic_unity": cc["paragraph_role_clarity"],
            "CC3_paragraphing_appropriacy": cc["paragraph_balance"],
            "CC4_intra_paragraph_sequencing": cc["intra_paragraph_sequencing"],
            "CC5_inter_paragraph_transition_quality": cc["inter_paragraph_linking"],
            "CC6_reference_substitution_clarity": cc["reference_clarity"],
            "CC7_cohesive_device_appropriacy": cc["cohesion_naturalness"],
            "CC8_cohesive_device_overuse_mechanicality": cc["mechanical_cohesion_risk"],
        },
        "lexical_resource": {
            "LR1_lexical_range": lr["lexical_range"],
            "LR2_topic_vocabulary_adequacy": lr["topic_vocabulary_precision"],
            "LR3_word_choice_precision": lr["word_choice_precision"],
            "LR4_collocation_control": lr["collocation_naturalness"],
            "LR5_lexical_appropriacy_register": lr["register_appropriacy"],
            "LR6_repetition_simplification_rate": lr["repetition_or_simplification_risk"],
            "LR7_word_formation_accuracy": lr["word_formation_control"],
            "LR8_spelling_impact": lr["spelling_control"],
            "LR9_semantic_phrase_naturalness": lr["phrase_naturalness"],
            "LR10_lexical_sophistication_index": lr["lexical_sophistication"],
            "LR11_dynamic_multiword_density": lr["multiword_density"],
            "ocd_positive_hits": int(round(lr["multiword_density"] * 6)),
        },
        "grammar": {
            "GRA1_structure_range": gr["grammar_range_control"],
            "GRA2_simple_sentence_accuracy": gr["simple_sentence_control"],
            "GRA3_compound_sentence_accuracy": gr["compound_sentence_control"],
            "GRA4_complex_sentence_accuracy": gr["complex_structure_success"],
            "GRA5_severe_grammar_error_density": gr["severe_grammar_error_density"],
            "GRA6_overall_grammar_error_density_per_100w": gr["overall_grammar_error_density_per_100w"],
            "GRA7_punctuation_accuracy": gr["punctuation_control"],
            "GRA8_malformed_sentence_ratio": gr["malformed_clause_risk"],
            "GRA9_communicative_effect_of_errors": gr["communicative_effect_of_errors"],
        },
        "shared": {
            "semantic_recoverability": sh["semantic_recoverability"],
            "proposition_stability": sh["proposition_stability"],
            "affected_discourse_ratio": clamp01(1.0 - sh["discourse_evaluability"]),
            "support_depth": tr["support_specificity"],
            "grammar_damage_index": grammar_damage_index,
            "lexical_damage_index": lexical_damage_index,
            "cohesion_mechanicality": cc["mechanical_cohesion_risk"],
            "word_count": metadata.get("word_count"),
            "sentence_count": metadata.get("sentence_count"),
            "paragraph_count": metadata.get("paragraph_count"),
        },
        "profile_quality": {
            "weak_writing_probability": sh["weak_writing_probability"],
            "high_band_readiness": sh["high_band_readiness"],
            "profile_confidence": sh["confidence"],
        },
    }
    return mapped


def merge_mapped_dmp(payload: Dict[str, Any], mapped: Dict[str, Any], overwrite: bool = True) -> None:
    dmp = payload.setdefault("detector_metric_profile", {})
    for section in ("task_response", "coherence_cohesion", "lexical_resource", "grammar", "shared"):
        sec = dmp.setdefault(section, {})
        for k, v in mapped.get(section, {}).items():
            if v is None:
                continue
            if overwrite or sec.get(k) is None:
                sec[k] = v
    dmp.setdefault("premium_metric_profile_mapping", {})
    dmp["premium_metric_profile_mapping"] = {
        "version": PROFILE_VERSION,
        "mapped_at": now_iso(),
        "overwrite_existing_detector_metrics": overwrite,
    }


def enrich_one(payload: Dict[str, Any], *, use_llm: bool = True, cache_dir: Optional[Path] = None, model: str = DEFAULT_MODEL, overwrite_dmp: bool = True) -> Dict[str, Any]:
    out = copy.deepcopy(payload)
    essay, prompt = extract_text_prompt(out)
    meta = extract_metadata(out, essay)
    h = content_hash(prompt, essay)
    cache_path = cache_dir / f"{h}.json" if cache_dir else None
    profile: Optional[Dict[str, Any]] = None
    cache_hit = False
    llm_audit: Dict[str, Any] = {"used": False, "cache_hit": False}
    if cache_path and cache_path.exists():
        try:
            profile = validate_profile(json.loads(cache_path.read_text(encoding="utf-8")))
            cache_hit = True
        except Exception:
            profile = None
    if profile is None:
        if use_llm and essay.strip():
            raw, llm_audit = call_openai_json(build_llm_prompt(out, essay, prompt), model=model)
            if raw is not None:
                profile = validate_profile(raw)
                profile["source"] = "detector_llm_metric_profiler"
                profile["audit"]["llm_used"] = True
                profile["audit"]["llm_audit"] = llm_audit
        if profile is None:
            profile = heuristic_profile(out, essay, prompt)
            profile["audit"]["llm_audit"] = llm_audit
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        profile["audit"]["cache_hit"] = True
    mapped = profile_to_dmp(profile, meta)
    out["premium_metric_profile"] = profile
    scorer_payload = out.setdefault("scorer_payload", {})
    scorer_payload["premium_metric_profile"] = profile
    scorer_payload["premium_metric_profile_mapped_metrics"] = mapped
    # Also add lr_positive_signals fallback from profiler to scorer_payload if detector lacks it.
    scorer_payload.setdefault("lr_positive_signals", {})
    scorer_payload["lr_positive_signals"].setdefault("ocd_positive_hits", mapped["lexical_resource"].get("ocd_positive_hits"))
    scorer_payload["lr_positive_signals"].setdefault("LR11_dynamic_multiword_density", mapped["lexical_resource"].get("LR11_dynamic_multiword_density"))
    merge_mapped_dmp(out, mapped, overwrite=overwrite_dmp)
    out.setdefault("qa", {})["premium_metric_profiler_v1"] = {
        "profile_version": PROFILE_VERSION,
        "cache_hit": cache_hit,
        "llm_used": bool(profile.get("audit", {}).get("llm_used")),
        "text_hash": h,
        "mapped_to_detector_metric_profile": True,
        "final_band_generated": False,
    }
    return out


def enrich_payload(payload: Dict[str, Any], *, use_llm: bool = True, cache_dir: Optional[Path] = None, model: str = DEFAULT_MODEL, overwrite_dmp: bool = True) -> Dict[str, Any]:
    if isinstance(payload.get("results"), list):
        out = copy.deepcopy(payload)
        out["results"] = [enrich_one(x, use_llm=use_llm, cache_dir=cache_dir, model=model, overwrite_dmp=overwrite_dmp) for x in payload["results"]]
        out.setdefault("system", {})["premium_metric_profiler_v1"] = {
            "profile_version": PROFILE_VERSION,
            "n_profiled": len(out["results"]),
            "llm_enabled": use_llm,
            "model": model if use_llm else None,
        }
        return out
    return enrich_one(payload, use_llm=use_llm, cache_dir=cache_dir, model=model, overwrite_dmp=overwrite_dmp)


def main() -> None:
    ap = argparse.ArgumentParser(description="Enrich detector output with Premium Metric Profile v1.")
    ap.add_argument("-i", "--input", required=True)
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--cache-dir", default=None)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--no-llm", action="store_true", help="Use heuristic fallback only; no token cost.")
    ap.add_argument("--no-overwrite-dmp", action="store_true", help="Do not overwrite existing detector_metric_profile values.")
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    cache_dir = Path(args.cache_dir) if args.cache_dir else None
    out = enrich_payload(payload, use_llm=not args.no_llm, cache_dir=cache_dir, model=args.model, overwrite_dmp=not args.no_overwrite_dmp)
    Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2 if args.pretty else None), encoding="utf-8")


if __name__ == "__main__":
    main()
