#!/usr/bin/env python3
"""
lret_v2_reclassification_pipeline_v1_1.py
============================================

v1.1 -- bugfix pass over lret_v2_reclassification_pipeline_v1_0.py. v1_0 is
left byte-for-byte untouched on disk (project convention: engine files are
never edited in place, only superseded by a new version-numbered file).

WHAT CHANGED FROM v1_0 (three real, verified problems found while checking
v1_0 against this project's own established conventions and against what
actually happens when you call a real LLM, rather than trusting v1_0's
docstring claims at face value):

1. MODEL_TIERS defaulted to "gpt-5-mini" / "gpt-5-nano" / "gpt-5". These are
   not verified, working model names anywhere else in this project -- this
   is the exact same bug class already found and fixed twice before for
   Vocabulary Coach's CHEAP_MODEL (which the project's own confirmed-real
   reference, det_vip_v18d_3_topic_alignment_risk.py, uses as "gpt-4o-mini").
   Since the user's explicit goal this pass is "write a code so I could run
   it with LLM," a bogus default is a functional blocker, not a cosmetic
   one -- a call to a nonexistent model just fails.
   FIX: all three tiers now default to "gpt-4o-mini", the one model name
   independently confirmed elsewhere in this project. I have NOT invented
   a "stronger" or "cheaper" alternative to fill out three genuinely
   different tiers, because no other model name is verified anywhere in
   this codebase -- inventing one would repeat the exact mistake being
   fixed. All three remain independently overridable via
   LRET_V2_CLASSIFY_MODEL / LRET_V2_SUGGEST_MODEL / LRET_V2_VERIFY_MODEL,
   so you can point classify/verify at a stronger model of your choice
   (e.g. "gpt-4o") once you've confirmed it against your own OpenAI account
   -- see the run instructions delivered alongside this file.

2. EDGE_FUNCTION_WORDS mixed genuine universal function words ("a", "the",
   "and", "it"...) with essay-specific CONTENT words lifted from the one
   sample essay's dropped_units ("money", "more money", "medical", "more
   medical", "companies", "some companies", "new", "more new", "problem",
   "one problem"). Left as-is, this would silently drop any future
   candidate containing ordinary content words like "money" or "companies"
   -- directly contradicting the engine's own stated "no topic/essay-
   specific runtime rules" principle (module docstring, SCOPE NOTE).
   FIX: stripped to genuine universal function words only. If per-essay
   noise words like "a lot"/"also" need filtering again, that's a
   real, separate signal (e.g. matching qa.source_audit.dropped_units'
   *reason*, not a hardcoded word list) -- flagged here, not invented.

3. _call_llm() called json.loads() directly on the raw model response with
   no defense against markdown code fences (models frequently wrap JSON in
   ```json ... ``` even when asked not to) and no `response_format`
   parameter, so a real call had a real, avoidable chance of a silent parse
   failure. FIX: strips ```/```json fences before parsing, and passes
   response_format={"type": "json_object"} (supported by gpt-4o-mini and
   later chat-completions models) to make the model far more likely to
   return bare JSON in the first place. If response_format itself is
   rejected by whatever model is configured, the call retries once without
   it rather than failing outright -- still fails safe (returns None) if
   both attempts fail.

Everything else (Pass 0 FILTER structure, extract_spelling_correction's
regexes, the CLASSIFY/SUGGEST/VERIFY prompts, the demotion ladder, run_
pipeline's shape) is unchanged from v1_0 -- those were checked against real
data in v1_0 and hold up.

SCOPE NOTE (unchanged from v1_0): this does not reimplement the ~11,000-line
extraction/registry machinery in lret_engine_v1_12_0_meaning_sensitive_
detector_families.py. This is the new post-extraction FILTER->CLASSIFY->
SUGGEST->VERIFY layer, given candidate units with their detector row (if
any) already attached the way the real engine already attaches it.

CLI (unchanged):
    --candidates PATH        (JSON list of raw candidate units, see
                              CandidateUnit shape below)
    --classify-model STR     (default: gpt-4o-mini, see MODEL_TIERS)
    --suggest-model STR      (default: gpt-4o-mini)
    --verify-model STR       (default: gpt-4o-mini)
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

TO ACTUALLY RUN THIS AGAINST A REAL LLM:
    export OPENAI_API_KEY=sk-...
    pip install openai
    python lret_v2_reclassification_pipeline_v1_1.py \\
        --candidates my_candidates.json --use-llm --output result.json
Without OPENAI_API_KEY set, --use-llm is accepted but every pass silently
falls back to its documented fail-safe default (CLARIFY / no suggestion /
borderline-demote) -- this is intentional, not a bug: the pipeline never
fabricates a classification, suggestion, or verify verdict it didn't
actually get from a model.
"""
import argparse
import json
import os
import re
import sys

ENGINE_VERSION = "lret-v2-reclassification-pipeline-v1.1"

MODEL_TIERS = {
    "classify": os.environ.get("LRET_V2_CLASSIFY_MODEL", "gpt-4o-mini"),
    "suggest": os.environ.get("LRET_V2_SUGGEST_MODEL", "gpt-4o-mini"),
    "verify": os.environ.get("LRET_V2_VERIFY_MODEL", "gpt-4o-mini"),
}
# v1_1: all three tiers default to "gpt-4o-mini" -- the one model name
# independently confirmed elsewhere in this project (det_vip_v18d_3_topic_
# alignment_risk.py). v1_0 defaulted to "gpt-5-mini"/"gpt-5-nano"/"gpt-5",
# none of which are verified anywhere in this codebase. Per Addendum B §B4's
# volume-asymmetry reasoning (classify runs on every candidate, suggest only
# on FIX/ENHANCE, verify only on suggest's output), it may still be worth
# pointing classify/verify at a stronger model than suggest once you've
# confirmed a specific model name against your own OpenAI account -- set
# LRET_V2_CLASSIFY_MODEL / LRET_V2_VERIFY_MODEL to do that. Not defaulted
# here because no such name is verified in this project; guessing one would
# repeat the exact mistake this fix addresses.

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

# v1_1: stripped to genuine universal function words only. v1_0 mixed these
# with essay-specific CONTENT words copied from one sample essay's dropped_
# units ("money", "more money", "medical", "more medical", "companies",
# "some companies", "new", "more new", "problem", "one problem") -- those
# are ordinary topic vocabulary in a different essay and would have been
# silently dropped as "no lexical content" if left in, contradicting this
# engine's own stated no-essay-specific-rules principle.
EDGE_FUNCTION_WORDS = {
    "a", "an", "the", "a lot", "also", "some", "more", "one", "and", "so",
    "for", "that", "this", "these", "those", "it", "they", "them", "he",
    "she", "of", "in", "on", "at", "to", "is", "are", "was", "were", "be",
    "been", "being", "very", "really", "just",
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
# Real, corrected fix for Addendum B §B3's finding (unchanged from v1_0 --
# already verified against real data there): prefer the audit-derived
# correction over the Detector's own context-blind repair_hypothesis, for
# SPELLING-family candidates with an attached detector_row.
# ---------------------------------------------------------------------------

_AUDIT_SHOULD_BE_RE = re.compile(
    r"stage7_v12_audit_(?:confirm|possible_fp):.*?should be '([^']+)'",
    re.IGNORECASE,
)
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
# LLM call helper
# ---------------------------------------------------------------------------

def _strip_code_fences(text):
    """v1_1 addition: models frequently wrap JSON in ```json ... ``` or
    ``` ... ``` even when told to return raw JSON. Strip that before
    json.loads rather than letting a real call fail on a cosmetic wrapper."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t)
    return t.strip()


def _call_llm(prompt, model):
    """Only attempts a call if OPENAI_API_KEY is present, returns None
    (never a fabricated result) on any error -- same fail-safe contract as
    Vocabulary Coach's _call_llm_judge. v1_1: strips markdown code fences
    before parsing, and requests response_format={"type": "json_object"}
    (supported by gpt-4o-mini and other current chat-completions models) to
    make bare-JSON output far more likely; if the configured model rejects
    that parameter, retries once without it before giving up."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        import openai  # type: ignore
        client = openai.OpenAI(api_key=api_key)
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=600,
                response_format={"type": "json_object"},
            )
        except Exception:
            # Some models/endpoints reject response_format -- retry once
            # without it rather than failing outright.
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=600,
            )
        raw = resp.choices[0].message.content
        return json.loads(_strip_code_fences(raw))
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
    current = unit["class_label"]
    demoted = DEMOTION_LADDER.get(current)
    if demoted is None:
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

    if not os.path.exists(args.candidates):
        raise SystemExit(f"error: --candidates path does not exist: {args.candidates}")
    with open(args.candidates, "r", encoding="utf-8") as f:
        raw_candidates = json.load(f)

    result = run_pipeline(raw_candidates, args.use_llm)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"[lret_v2_pipeline v1.1] wrote {args.output} "
          f"(filter {result['pass0_filter']['valid_count']}/{result['pass0_filter']['input_count']} valid, "
          f"verify: {result['pass3_verify_summary']})")


if __name__ == "__main__":
    main()
