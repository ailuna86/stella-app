#!/usr/bin/env python3
"""
lret_v2_reclassification_pipeline_v1_0.py
============================================

Implements LRET_v2_Spec.docx §4 + Addendum A (§A3) + Addendum B (§B1, §B3,
§B4, §B5): a four-pass pipeline -- FILTER -> CLASSIFY -> SUGGEST -> VERIFY --
that turns a list of raw candidate lexical units (assumed already extracted
upstream -- extraction itself is NOT rebuilt here, see scope note below) into
FIX/ENHANCE/CLARIFY/KEEP classifications with verified suggestions.

SCOPE NOTE: this does not reimplement the ~11,000-line extraction/registry
machinery in lret_engine_v1_12_0_meaning_sensitive_detector_families.py. That
file's own real output already shows FIX units carrying
`"source": "detector_validated_fix_candidate"` -- i.e. the Detector and LRET
are already two separate systems with a defined hand-off, exactly the
"connect the plumbing, don't rebuild" pattern this project has repeatedly
found (see Addendum B §B3 and the original spec's §5.3 finding about
priority_input_builder). This pipeline is the NEW post-extraction layer the
addenda actually asked for: given candidate units (with their detector row,
if any, already attached the way the real engine already attaches it), run
them through four explicit passes instead of the more conflated classify-
and-generate step the original engine uses today.

A REAL, VERIFIED CORRECTION MADE WHILE BUILDING THIS (not hidden): Addendum
B §B3 claimed `detector_to_errormap_v4.py` already fixes LRET's "modey" ->
wrong-suggestion bug via its `_AUDIT_RE` regex
(`Spelling error for '([^']+)'`). Testing that regex against the actual
arbitration_reasons strings in the real LRET sample
(`lret_v1_12_0_smoke_output_with_detector.json`) shows it does NOT match:
the real strings read "Spelling error; 'modey' should be 'money'." (a
"should be" template), not "Spelling error for 'money'." (the "for"
template `detector_to_errormap_v4.py`'s regex expects). Zero of three real
strings matched when tested directly. The underlying idea (prefer the
audit-derived word over repair_hypothesis) is still correct and is what
`extract_spelling_correction()` below implements -- but with a regex
verified against the real strings, trying the "should be" template first
and the "for" template as a fallback (in case an older engine version
produces that phrasing), rather than reusing the untested one as-is.

CLI:
    --candidates PATH        (JSON list of raw candidate units, see
                              CandidateUnit shape in the module docstring
                              below)
    --classify-model STR     (default: mid-tier model, see MODEL_TIERS)
    --suggest-model STR      (default: cheap-tier model)
    --verify-model STR       (default: strong-tier model)
    --use-llm                (flag; default off -- same fail-safe-never-
                              fabricate discipline as Vocabulary Coach)
    --output PATH

CandidateUnit input shape (one dict per candidate, list in the input file):
    {
      "unit_text": str,
      "context": str,                    # full sentence
      "source_sentence_index": int,
      "source_paragraph_index": int,
      "family_hint": str | null,          # e.g. "SPELLING" if known upstream
      "detector_row": {                   # optional, only for detector-sourced candidates
        "repair_hypothesis": str | null,
        "arbitration_reasons": [str, ...],
        "detector_confidence": float
      }
    }
"""
import argparse
import json
import os
import re
import sys

ENGINE_VERSION = "lret-v2-reclassification-pipeline-v1.0"

MODEL_TIERS = {
    "classify": os.environ.get("LRET_V2_CLASSIFY_MODEL", "gpt-5-mini"),
    "suggest": os.environ.get("LRET_V2_SUGGEST_MODEL", "gpt-5-nano"),
    "verify": os.environ.get("LRET_V2_VERIFY_MODEL", "gpt-5"),
}
# Per Addendum B §B4: classify runs on every surfaced candidate (highest
# volume of the three passes), suggest only on FIX/ENHANCE candidates,
# verify only on what suggest produced (lowest volume) -- a mid-tier model
# for classify, cheap for suggest, strong for verify, not a flat two-tier
# split, pending real per-essay cost measurement against a real batch
# (explicitly flagged as not yet done -- see the addendum for this build).

DEMOTION_LADDER = {"FIX": "ENHANCE", "ENHANCE": "CLARIFY"}
# Per Addendum B §B5: a flagged-wrong item never demotes into KEEP -- KEEP
# is the one tier that tells a student "copy this." CLARIFY is the floor
# for demotion; anything worse than CLARIFY-worthy gets suppressed outright.

# ---------------------------------------------------------------------------
# Pass 0 -- FILTER (formalizes the real engine's existing "noise_filter"
# stage, confirmed real via qa.source_audit.dropped_units in the sample
# output, e.g. {"unit": "a lot", "reason": "edge function word with no
# redeeming lexical signal", "stage": "noise_filter"})
# ---------------------------------------------------------------------------

EDGE_FUNCTION_WORDS = {
    "a", "an", "the", "a lot", "also", "some", "more", "one", "problem",
    "one problem", "money", "more money", "new", "more new", "medical",
    "more medical", "companies", "some companies", "and", "so", "for",
    "that", "this", "these", "those", "it", "they", "them", "he", "she",
}


def is_valid_candidate(unit):
    """Real, deterministic Pass 0 validity check -- no LLM call needed.
    Returns (is_valid, reasons_if_invalid)."""
    text = (unit.get("unit_text") or "").strip()
    reasons = []
    if not text:
        reasons.append("empty_unit_text")
        return False, reasons
    words = text.lower().split()
    if text.lower() in EDGE_FUNCTION_WORDS or all(w in EDGE_FUNCTION_WORDS for w in words):
        reasons.append("edge_function_word_no_lexical_content")
    if len(words) > 8:
        reasons.append("candidate_span_too_long_likely_extraction_artifact")
    return (len(reasons) == 0), reasons


def dedup_candidates(units):
    """Drops a candidate whose (sentence_index, normalized text) exactly
    matches one already kept -- mirrors the real engine's dedup_role field
    (confirmed real value "independent_keep" on every keep_unit in the
    sample), formalized here as an explicit Pass 0 step rather than left
    implicit."""
    seen = set()
    out = []
    for u in units:
        key = (u.get("source_sentence_index"), (u.get("unit_text") or "").strip().lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(u)
    return out


def run_filter_pass(raw_candidates):
    deduped = dedup_candidates(raw_candidates)
    valid, dropped = [], []
    for u in deduped:
        ok, reasons = is_valid_candidate(u)
        if ok:
            valid.append(u)
        else:
            dropped.append({"unit_text": u.get("unit_text"), "reasons": reasons, "stage": "noise_filter"})
    return valid, dropped


# ---------------------------------------------------------------------------
# Real, corrected fix for Addendum B §B3's finding: prefer the audit-derived
# correction over the Detector's own context-blind repair_hypothesis, for
# SPELLING-family candidates with an attached detector_row.
# ---------------------------------------------------------------------------

_AUDIT_SHOULD_BE_RE = re.compile(
    r"stage7_v12_audit_(?:confirm|possible_fp):.*?should be '([^']+)'",
    re.IGNORECASE,
)
# Fallback pattern matching detector_to_errormap_v4.py's documented (but,
# per this file's own header, unverified-against-real-data) template, kept
# as a secondary attempt in case a different engine version ever produces
# that phrasing instead.
_AUDIT_FOR_RE = re.compile(
    r"stage7_v12_audit_(?:confirm|possible_fp):Spelling error for '([^']+)'",
    re.IGNORECASE,
)


def extract_spelling_correction(detector_row):
    """Priority 1: audit-string extraction (has real sentence context,
    confirmed correct on all observed real cases: modey->money,
    goverment->government, contries->countries, and generalizes to
    WORD_FORM's "issued"->"issue"). Priority 2: repair_hypothesis fallback
    (context-blind, sometimes wrong -- confirmed real failure mode).
    Priority 3: None (never assert an unverified guess)."""
    if not detector_row:
        return None, "no_detector_row"
    reasons = detector_row.get("arbitration_reasons") or []
    for reason in reasons:
        m = _AUDIT_SHOULD_BE_RE.search(str(reason))
        if m:
            return m.group(1), "audit_should_be_pattern"
        m = _AUDIT_FOR_RE.search(str(reason))
        if m:
            return m.group(1), "audit_for_pattern"
    rh = detector_row.get("repair_hypothesis")
    if rh:
        return rh, "repair_hypothesis_fallback_unverified"
    return None, "no_correction_available"


# ---------------------------------------------------------------------------
# LLM call helper -- mirrors vocab_coach_engine_v1_0_0.py's _call_llm_judge /
# vocab_coach_response_grader_v1_0.py's _call_llm_judge fail-safe contract:
# only attempts a call if OPENAI_API_KEY is present, returns None (never a
# fabricated result) on any error. No key is present in this sandbox
# (checked directly, same as the Vocabulary Coach engines) -- every real run
# here uses the documented fail-safe path, not a simulated one.
# ---------------------------------------------------------------------------

def _call_llm(prompt, model):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        import openai  # type: ignore
        client = openai.OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=600,
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as exc:
        print(f"[lret_v2_pipeline] LLM call failed on model={model}: {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Pass 1 -- CLASSIFY
# ---------------------------------------------------------------------------

def build_classify_prompt(unit):
    return f"""Classify this candidate lexical unit from a student's IELTS essay into exactly one of FIX, ENHANCE, CLARIFY, or KEEP.
FIX = something is actually wrong. ENHANCE = correct but could be stronger. CLARIFY = too vague to judge yet (ask the student to clarify/rewrite). KEEP = already working well.

Unit: "{unit.get('unit_text')}"
Sentence context: "{unit.get('context')}"

Return ONLY this JSON: {{"class_label": "FIX|ENHANCE|CLARIFY|KEEP", "family": "one short family tag", "confidence": 0.0-1.0, "reason": "one line"}}"""


def run_classify_pass(valid_candidates, use_llm):
    results = []
    for u in valid_candidates:
        result = None
        if use_llm:
            result = _call_llm(build_classify_prompt(u), MODEL_TIERS["classify"])
        if result and "class_label" in result:
            results.append({**u, **result, "classify_verified": True})
        else:
            # Fail-safe default: CLARIFY, never FIX or KEEP without a real
            # classification having actually run -- asking the student to
            # attempt something themselves is the safest unverified default
            # (mirrors LRET's own produce-before-reveal pedagogy), unlike
            # asserting a correction (FIX) or endorsing usage (KEEP) blind.
            results.append({
                **u,
                "class_label": "CLARIFY",
                "family": u.get("family_hint") or "UNCLASSIFIED",
                "confidence": None,
                "reason": "No LLM classification available/enabled -- defaulted to CLARIFY rather than asserting FIX or KEEP unverified.",
                "classify_verified": False,
            })
    return results


# ---------------------------------------------------------------------------
# Pass 2 -- SUGGEST (only for FIX/ENHANCE; SPELLING FIX uses the corrected
# detector-errormap extraction above instead of an LLM call when a detector
# row is attached)
# ---------------------------------------------------------------------------

def build_suggest_prompt(unit):
    return f"""Given this candidate lexical unit classified as {unit['class_label']} (family: {unit.get('family')}), generate ONE corrected/enhanced version of the sentence.

Unit: "{unit.get('unit_text')}"
Sentence: "{unit.get('context')}"

Return ONLY this JSON: {{"suggestion_text": "the full corrected/enhanced sentence", "confidence": 0.0-1.0}}"""


def run_suggest_pass(classified_units, use_llm):
    out = []
    for u in classified_units:
        if u["class_label"] not in ("FIX", "ENHANCE"):
            out.append(u)
            continue
        if u.get("family") == "SPELLING" and u.get("detector_row"):
            correction, source = extract_spelling_correction(u["detector_row"])
            out.append({
                **u,
                "suggestion_text": correction,
                "suggestion_source": source,
                "suggestion_verified": source in ("audit_should_be_pattern", "audit_for_pattern"),
            })
            continue
        result = None
        if use_llm:
            result = _call_llm(build_suggest_prompt(u), MODEL_TIERS["suggest"])
        if result and "suggestion_text" in result:
            out.append({**u, "suggestion_text": result["suggestion_text"], "suggestion_source": "llm_suggest_pass", "suggestion_verified": True})
        else:
            out.append({
                **u,
                "suggestion_text": None,
                "suggestion_source": "no_llm_available",
                "suggestion_verified": False,
            })
    return out


# ---------------------------------------------------------------------------
# Pass 3 -- VERIFY (independent second opinion; demote borderline, suppress
# confidently-wrong, never demote a flagged-wrong item into KEEP)
# ---------------------------------------------------------------------------

def build_verify_prompt(unit):
    return f"""You are an independent second-opinion checker. A first pass classified this as {unit['class_label']} with suggestion "{unit.get('suggestion_text')}".

Unit: "{unit.get('unit_text')}"
Sentence: "{unit.get('context')}"

Decide ONE verdict:
- "confirmed": the classification and suggestion are correct as-is.
- "borderline": uncertain, plausible but not fully confident -- should be shown at a lower-stakes tier.
- "confidently_wrong": the classification or suggestion is actively wrong or nonsensical -- should not be shown at all.

Return ONLY this JSON: {{"verdict": "confirmed|borderline|confidently_wrong", "reason": "one line"}}"""


def apply_verify_verdict(unit, verdict):
    if verdict == "confirmed":
        return {**unit, "verify_action": "kept", "verify_verdict": verdict}
    if verdict == "confidently_wrong":
        return {**unit, "verify_action": "suppressed", "verify_verdict": verdict}
    # borderline -> demote one tier, but never land in KEEP (§B5)
    current = unit["class_label"]
    demoted = DEMOTION_LADDER.get(current)
    if demoted is None:
        # already at CLARIFY (the demotion floor) or at KEEP (shouldn't reach
        # verify borderline from KEEP in practice, but fail safe anyway) --
        # suppress rather than demote into/through KEEP.
        return {**unit, "verify_action": "suppressed", "verify_verdict": verdict,
                "verify_note": "already at demotion floor -- suppressed rather than risk landing in KEEP"}
    return {**unit, "class_label": demoted, "verify_action": "demoted",
            "verify_verdict": verdict, "demoted_from": current}


def run_verify_pass(suggested_units, use_llm):
    out = []
    for u in suggested_units:
        if u["class_label"] not in ("FIX", "ENHANCE"):
            out.append({**u, "verify_action": "not_applicable", "verify_verdict": None})
            continue
        result = None
        if use_llm:
            result = _call_llm(build_verify_prompt(u), MODEL_TIERS["verify"])
        if result and "verdict" in result:
            out.append(apply_verify_verdict(u, result["verdict"]))
        else:
            # Fail-safe: no independent check ran -- demote one tier rather
            # than ship an unverified FIX/ENHANCE at full stakes. This is
            # the safest default precisely because verify didn't run.
            out.append(apply_verify_verdict(u, "borderline"))
            out[-1]["verify_note"] = "No LLM verify pass available/enabled -- defaulted to borderline-demote rather than shipping unverified."
    return out


def run_pipeline(raw_candidates, use_llm):
    valid, dropped = run_filter_pass(raw_candidates)
    classified = run_classify_pass(valid, use_llm)
    suggested = run_suggest_pass(classified, use_llm)
    verified = run_verify_pass(suggested, use_llm)

    final = [u for u in verified if u.get("verify_action") != "suppressed"]
    suppressed = [u for u in verified if u.get("verify_action") == "suppressed"]

    return {
        "artifact_type": "lret_v2_reclassification_result",
        "schema_version": "lret_v2_reclassification_v1.0",
        "engine_version": ENGINE_VERSION,
        "model_tiers": MODEL_TIERS,
        "use_llm": use_llm,
        "llm_key_present": bool(os.environ.get("OPENAI_API_KEY")),
        "pass0_filter": {"input_count": len(raw_candidates), "valid_count": len(valid), "dropped": dropped},
        "pass3_verify_summary": {
            "total": len(verified),
            "confirmed": sum(1 for u in verified if u.get("verify_verdict") == "confirmed"),
            "demoted": sum(1 for u in verified if u.get("verify_action") == "demoted"),
            "suppressed": sum(1 for u in verified if u.get("verify_action") == "suppressed"),
        },
        "final_units": final,
        "suppressed_units": suppressed,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--classify-model", default=None)
    ap.add_argument("--suggest-model", default=None)
    ap.add_argument("--verify-model", default=None)
    ap.add_argument("--use-llm", action="store_true")
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    if args.classify_model:
        MODEL_TIERS["classify"] = args.classify_model
    if args.suggest_model:
        MODEL_TIERS["suggest"] = args.suggest_model
    if args.verify_model:
        MODEL_TIERS["verify"] = args.verify_model

    with open(args.candidates, "r", encoding="utf-8") as f:
        raw_candidates = json.load(f)

    result = run_pipeline(raw_candidates, args.use_llm)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"[lret_v2_pipeline] wrote {args.output} "
          f"(filter {result['pass0_filter']['valid_count']}/{result['pass0_filter']['input_count']} valid, "
          f"verify: {result['pass3_verify_summary']})")


if __name__ == "__main__":
    main()
