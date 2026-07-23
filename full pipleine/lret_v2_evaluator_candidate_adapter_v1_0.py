#!/usr/bin/env python3
"""
lret_v2_evaluator_candidate_adapter_v1_0.py
==============================================

NEW FILE. Answers a direct question this project needed a real answer to:
"can lret_v2_reclassification_pipeline_v1_1.py run on Evaluator's output?"

SHORT ANSWER: not as-is. Evaluator's real output
(07_evaluator_output.json -> lexical_unit_profile.lexical_units_for_lret)
IS the right raw-candidate source -- it is explicitly stamped
`"classification_policy": "extraction_only_lret_must_classify"`, i.e.
Evaluator already knows it is not the one that should classify these units;
that is this pipeline's job. But feeding it in directly does not work,
for two real reasons found by actually trying it (not by inspection):

1. FIELD NAME MISMATCH. Evaluator emits `"unit"`; the pipeline's
   CandidateUnit shape expects `"unit_text"`. Fed in unchanged, every
   candidate's unit_text is None and Pass 0 FILTER drops all of them as
   `empty_unit_text`.

2. SENTENCE-INDEX MISMATCH BETWEEN ENGINES (a genuine, previously-unknown
   cross-engine bug, confirmed directly against real session
   gold_sessions/student_123/gold_20260711_182823_essay_001_2f8d1916/):
   the Detector's `detector_rows` (01_detector_output.json) use a
   DIFFERENT sentence_index counter than Evaluator's
   source_sentence_index for the exact same physical sentence -- e.g. the
   sentence containing "modey"/"goverment" is source_sentence_index=2 in
   Evaluator's output but sentence_index=3 in the Detector's row for the
   same spelling error; the "contries" sentence is 3 in Evaluator, 4 in
   the Detector. Confirmed on 2/2 checkable real cases -- a consistent
   off-by-one, not noise. Matching Detector rows onto Evaluator candidates
   by sentence_index (the obvious first approach) silently matches ZERO
   real candidates, confirmed by actually running that version first.
   FIX USED HERE: match on quote-appears-as-a-substring-in-unit-text
   instead, which is real, sentence-index-independent, and confirmed
   working against real data.

WHAT THIS FILE DOES:
    1. Loads a real Evaluator output JSON and a real Detector output JSON
       for the same essay/submission.
    2. Renames Evaluator's `unit` -> `unit_text`, keeps context/
       source_sentence_index/source_paragraph_index as-is.
    3. Cross-references the Detector's SPELLING-family `detector_rows`
       onto matching Evaluator candidates (quote-substring match) and
       attaches a `detector_row` + `family_hint: "SPELLING"` so those
       candidates can use the pipeline's verified spelling-correction fast
       path (extract_spelling_correction()) instead of an unguided LLM
       guess.
    4. Writes a CandidateUnit-shaped JSON list, ready for
       lret_v2_reclassification_pipeline_v1_1.py's --candidates argument.

WHAT THIS FILE DOES NOT DO:
    - It does not resolve family_hint for anything other than SPELLING --
      Evaluator's own `candidate_route_hint` (FIX/ENHANCE/CLARIFY/KEEP/
      DROP) is deliberately NOT copied into class_label or family_hint,
      because Evaluator's own schema stamps these candidates
      "extraction_only_lret_must_classify" -- i.e. that hint is not meant
      to be trusted as a final answer, only Pass 1 CLASSIFY is. Passing
      it through would silently defeat the point of re-classifying.
    - It does not fix the underlying sentence-index mismatch between
      Evaluator and the Detector -- that is a real cross-engine
      inconsistency worth fixing at the source (in whichever engine's
      sentence-splitting differs), flagged here, not patched over there.

CLI:
    --evaluator-output PATH   (07_evaluator_output.json)
    --detector-output PATH    (01_detector_output.json)
    --output PATH             (CandidateUnit-shaped JSON list)
"""
import argparse
import json


def load_lexical_units(evaluator_output):
    return evaluator_output["lexical_unit_profile"]["lexical_units_for_lret"]


def load_spelling_rows(detector_output):
    rows = []
    for result in detector_output.get("results", []):
        rows.extend([r for r in result.get("detector_rows", []) if r.get("family") == "SPELLING"])
    return rows


def find_detector_row(unit, spelling_rows):
    """Quote-substring match -- see module docstring for why sentence_index
    matching does not work between these two real engines' outputs."""
    unit_text = (unit.get("unit") or "").lower()
    for row in spelling_rows:
        quote = (row.get("quote") or "").lower()
        if quote and quote in unit_text:
            return row
    return None


def build_candidates(evaluator_output, detector_output):
    units = load_lexical_units(evaluator_output)
    spelling_rows = load_spelling_rows(detector_output)

    candidates = []
    matched = 0
    for u in units:
        row = find_detector_row(u, spelling_rows)
        if row:
            matched += 1
        candidates.append({
            "unit_text": u.get("unit"),
            "context": u.get("context"),
            "source_sentence_index": u.get("source_sentence_index"),
            "source_paragraph_index": u.get("source_paragraph_index"),
            "family_hint": "SPELLING" if row else None,
            "detector_row": {
                "repair_hypothesis": row.get("repair_hypothesis"),
                "arbitration_reasons": row.get("arbitration_reasons"),
                "detector_confidence": row.get("confidence"),
            } if row else None,
            # Kept for traceability -- NOT consumed by the pipeline, and
            # deliberately not mapped to class_label/family_hint (see
            # docstring: Evaluator's own schema says LRET must classify).
            "_evaluator_unit_id": u.get("unit_id"),
            "_evaluator_candidate_route_hint": u.get("candidate_route_hint"),
            "_evaluator_candidate_value": u.get("candidate_value"),
        })

    return candidates, {"total": len(units), "spelling_rows_available": len(spelling_rows), "matched_to_detector_row": matched}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--evaluator-output", required=True)
    ap.add_argument("--detector-output", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    with open(args.evaluator_output, "r", encoding="utf-8") as f:
        evaluator_output = json.load(f)
    with open(args.detector_output, "r", encoding="utf-8") as f:
        detector_output = json.load(f)

    candidates, stats = build_candidates(evaluator_output, detector_output)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(candidates, f, indent=2, ensure_ascii=False)

    print(f"[lret_v2_evaluator_candidate_adapter] wrote {args.output} "
          f"({stats['total']} candidates, {stats['matched_to_detector_row']}/"
          f"{stats['spelling_rows_available']} spelling rows cross-matched)")


if __name__ == "__main__":
    main()
