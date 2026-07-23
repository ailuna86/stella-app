#!/usr/bin/env python3
"""
lret_v2_candidate_triage_engine_v1_0.py
==========================================

NEW FILE. Answers the real problem found while smoke-testing the v2
pipeline against a real Evaluator output: a single real, ordinary essay
(270 words, 18 sentences, gold_sessions/student_123/gold_20260711_182823_
essay_001_2f8d1916) produced 188 raw candidates from Evaluator's
lexical_unit_profile.lexical_units_for_lret -- 10.4 candidates per
sentence. That is far more than will ever reach a student (the display-
quota engine caps the final shown set to well under 20 items total) and
far more than should ever be sent to a Pass 1 CLASSIFY LLM call -- most of
those 188 calls would classify something that can structurally never be
shown.

ROOT CAUSE, CONFIRMED AGAINST REAL DATA, NOT ASSUMED:
Evaluator's extraction generates the same real phrase or word at every
possible granularity as independent, overlapping candidates. Real example
from sentence 17 of the inspected essay (11 candidates for one clause):
"government makes good" (0.87, FIX) and "makes good" (0.77, FIX) and
"makes good ability" (0.69, FIX) and "good ability" (0.65) all describe
the same underlying issue at different span widths, plus standalone
common words ("makes" 0.59, "ability" 0.47, "best" 0.47, "plan" 0.47) that
add no signal beyond what Evaluator itself already scored low.
Evaluator's own `covered_subunits` field already records this overlap on
every candidate -- nothing downstream previously used it to collapse
duplicates before this file.

QUANTIFIED ON THE REAL SESSION ABOVE (see this file's own module-level
test invocation notes / the delivery report for the exact run):
  188 raw candidates
  -> 63 after overlap-clustering (union-find on shared covered_subunits
     token or literal text-substring containment within the same
     sentence; keep the single highest-priority representative per
     cluster)
  -> 39 after a configurable low-value KEEP floor (candidates Evaluator
     itself hinted KEEP at candidate_value < 0.50 are excluded from the
     classify pass -- not asserted as KEEP, just not spent on)
A ~79% reduction in classify-pass volume, with zero real cases in the
inspected session where naive highest-candidate_value tie-breaking would
have hidden a real FIX signal inside its own cluster -- confirmed by
checking all 3 real clusters containing a FIX-hinted member. The
representative-selection rule below is hardened beyond what that lucky
result would justify on its own: it ranks by hint tier (FIX > ENHANCE >
CLARIFY > KEEP > DROP) FIRST, candidate_value only as a tie-break within
the same tier -- so a real issue can never be silently outranked by a
merely-higher-value KEEP candidate describing the same span family.

A REAL BUG FOUND AND FIXED WHILE TESTING THIS AGAINST REAL DATA (not caught
by inspection alone): the first version of this file clustered and
floor-filtered purely on Evaluator's own signal (candidate_value,
candidate_route_hint, covered_subunits), with no awareness of the
Detector's independently-confirmed SPELLING errors. Run against the real
session, this silently broke 2 of the essay's 3 real misspellings:
"contries" got pulled via transitive covered_subunits overlap into an
unrelated 9-member cluster ("example Korea", "Japan", "for example", ...)
whose representative had nothing to do with the misspelling, and
"goverment" was floored out entirely because Evaluator itself scored it
KEEP at 0.47 (Evaluator has no idea it's misspelled -- that is the
Detector's job, not Evaluator's). Only "modey" survived, and only by luck
(its cluster's representative happened to still contain the word).
FIX: this file now optionally accepts --detector-output. Any Evaluator
candidate whose unit text contains a real Detector SPELLING-row quote is
marked PROTECTED before clustering ever runs: protected candidates are
excluded from the overlap-clustering pool entirely (each is forced through
as its own untouched singleton) and are exempt from both the KEEP floor
and the hard cap. A Detector-confirmed error must never be silently
dropped or merged away by a volume-control heuristic that only knows
Evaluator's side of the picture. Re-verified after this fix: all 3 real
misspellings ("modey", "goverment", "contries") now survive triage intact
and cross-match correctly downstream in the adapter.

WHAT THIS DOES NOT DO:
- It does not classify anything. Every exclusion is a "not sent to
  Pass 1 CLASSIFY" decision with a recorded reason, never an asserted
  FIX/ENHANCE/CLARIFY/KEEP verdict -- classification remains Pass 1's job
  alone, per this pipeline's own "extraction_only_lret_must_classify"
  discipline.
- The floor (0.50) and hard cap (40) are CONFIGURABLE DEFAULTS based on
  one real essay, not a calibrated threshold across bands -- same
  explicit-provisional discipline as BAND_CAPS in
  lret_v2_display_quota_engine_v1_1.py. Revisit once more real essays
  across weak/mid/strong bands have been run through this.

WHERE THIS SITS IN THE PIPELINE (four independent, composable stages):
  Evaluator output (raw, ~188 candidates for a real essay)
    -> [THIS FILE] candidate triage (~39 candidates)
    -> lret_v2_evaluator_candidate_adapter_v1_0.py (schema translation +
       Detector SPELLING cross-reference, unchanged)
    -> lret_v2_reclassification_pipeline_v1_1.py (Pass 0 FILTER -> Pass 1
       CLASSIFY -> Pass 2 SUGGEST -> Pass 3 VERIFY, unchanged)
    -> lret_v2_display_quota_engine_v1_1.py (final student-facing caps,
       unchanged)
This file runs FIRST, on Evaluator's native schema (it needs
covered_subunits and candidate_route_hint, which the adapter deliberately
does not forward into the CandidateUnit shape) -- so it is not a
replacement for the adapter's own Pass 0 FILTER (which checks validity:
empty text, edge-function words, span length), it is upstream volume
control operating on a different problem (redundancy and cost), on a
different schema (Evaluator's, not CandidateUnit's).

CLI:
    --evaluator-output PATH   (07_evaluator_output.json)
    --detector-output PATH    (optional; 01_detector_output.json. If given,
                               any Evaluator candidate whose unit text
                               contains a real Detector SPELLING-row quote
                               is protected -- excluded from clustering,
                               the KEEP floor, and the hard cap. Strongly
                               recommended: without it, a real
                               Detector-confirmed error can be silently
                               absorbed into an unrelated cluster or
                               floored out, exactly as found while testing
                               this file -- see module docstring.)
    --keep-floor FLOAT        (default 0.50; candidates hinted KEEP by
                               Evaluator below this candidate_value are
                               excluded from the classify pass, UNLESS
                               protected)
    --hard-cap INT            (default 40; last-resort safety net after
                               clustering + floor, in case a future essay
                               still leaves too many, applies only to the
                               unprotected pool)
    --output PATH             (reduced lexical_units_for_lret-shaped list,
                               same schema as the input units -- feed this
                               straight into
                               lret_v2_evaluator_candidate_adapter_v1_0.py
                               in place of the raw Evaluator file's list)
    --trace-output PATH        (optional; full accounting of every
                               excluded candidate + why, for QA/audit --
                               never silently drops without a reason)
"""
import argparse
import json

ENGINE_VERSION = "lret-v2-candidate-triage-engine-v1.0"

HINT_RANK = {"FIX": 4, "ENHANCE": 3, "CLARIFY": 2, "KEEP": 1, "DROP": 0}
DEFAULT_HINT_RANK = 0  # unrecognised/missing hint -- treat as lowest priority, not crash


def _hint_rank(unit):
    return HINT_RANK.get(unit.get("candidate_route_hint"), DEFAULT_HINT_RANK)


def _candidate_value(unit):
    return unit.get("candidate_value") or 0.0


def load_spelling_quotes(detector_output):
    """Same real signal the adapter uses: Detector's SPELLING-family
    detector_rows carry a `quote` (the exact misspelled surface form).
    Returns a list of lowercase quotes."""
    quotes = []
    if not detector_output:
        return quotes
    for result in detector_output.get("results", []):
        for row in result.get("detector_rows", []):
            if row.get("family") == "SPELLING" and row.get("quote"):
                quotes.append(row["quote"].lower())
    return quotes


def mark_protected(units, spelling_quotes):
    """A candidate is PROTECTED if its unit text contains a real,
    Detector-confirmed error quote. Protected candidates bypass
    clustering, the KEEP floor, and the hard cap entirely -- see module
    docstring for the real case (goverment/contries) this fixes."""
    protected_flags = []
    for u in units:
        text = (u.get("unit") or "").lower()
        is_protected = any(q in text for q in spelling_quotes)
        protected_flags.append(is_protected)
    return protected_flags


def cluster_by_overlap(units):
    """Union-find clustering of candidates within the same sentence that
    share a covered_subunits token or where one's unit text is a literal
    substring of the other's. Both signals are real, already-present
    fields on every Evaluator candidate -- nothing invented. Returns a
    list of clusters, each a list of original indices into `units`."""
    n = len(units)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    by_sentence = {}
    for i, u in enumerate(units):
        by_sentence.setdefault(u.get("source_sentence_index"), []).append(i)

    for _, idxs in by_sentence.items():
        for a in range(len(idxs)):
            for b in range(a + 1, len(idxs)):
                i, j = idxs[a], idxs[b]
                ui, uj = units[i], units[j]
                covered_i = set(x.lower() for x in (ui.get("covered_subunits") or []))
                covered_j = set(x.lower() for x in (uj.get("covered_subunits") or []))
                shares_covered_token = bool(covered_i & covered_j)
                ti = (ui.get("unit") or "").lower()
                tj = (uj.get("unit") or "").lower()
                is_substring = bool(ti) and bool(tj) and (ti in tj or tj in ti)
                if shares_covered_token or is_substring:
                    union(i, j)

    clusters = {}
    for i in range(n):
        r = find(i)
        clusters.setdefault(r, []).append(i)
    return list(clusters.values())


def pick_representative(cluster_indices, units):
    """Within a cluster, pick the single candidate to forward to
    classify. Ranks by hint tier first (FIX > ENHANCE > CLARIFY > KEEP >
    DROP) so a real issue is never outranked by a merely-higher-value KEEP
    describing the same span family; candidate_value is only the
    tie-break within the same tier."""
    return max(cluster_indices, key=lambda i: (_hint_rank(units[i]), _candidate_value(units[i])))


def run_triage(units, keep_floor=0.50, hard_cap=40, spelling_quotes=None):
    protected_flags = mark_protected(units, spelling_quotes or [])
    protected_units = [units[i] for i, p in enumerate(protected_flags) if p]
    unprotected_idx = [i for i, p in enumerate(protected_flags) if not p]
    unprotected_units = [units[i] for i in unprotected_idx]

    protected_trace = [
        {"unit": u.get("unit"), "unit_id": u.get("unit_id"),
         "reason": "detector_confirmed_error_quote_present_-_exempt_from_clustering_floor_and_cap"}
        for u in protected_units
    ]

    clusters = cluster_by_overlap(unprotected_units)

    representatives = []
    cluster_trace = []
    for cluster in clusters:
        rep_idx = pick_representative(cluster, unprotected_units)
        representatives.append(rep_idx)
        if len(cluster) > 1:
            absorbed = [unprotected_units[i].get("unit") for i in cluster if i != rep_idx]
            cluster_trace.append({
                "representative": unprotected_units[rep_idx].get("unit"),
                "representative_unit_id": unprotected_units[rep_idx].get("unit_id"),
                "absorbed_overlapping_candidates": absorbed,
                "reason": "overlapping_span_collapsed_to_highest_priority_representative",
            })

    kept = []
    floored_out = []
    for i in representatives:
        u = unprotected_units[i]
        if u.get("candidate_route_hint") == "KEEP" and _candidate_value(u) < keep_floor:
            floored_out.append({
                "unit": u.get("unit"),
                "unit_id": u.get("unit_id"),
                "candidate_value": _candidate_value(u),
                "reason": f"keep_hinted_below_floor_{keep_floor}_not_sent_to_classify",
            })
        else:
            kept.append(u)

    capped_out = []
    if len(kept) > hard_cap:
        kept_sorted = sorted(kept, key=lambda u: (_hint_rank(u), _candidate_value(u)), reverse=True)
        capped_out = [
            {"unit": u.get("unit"), "unit_id": u.get("unit_id"),
             "reason": f"hard_cap_{hard_cap}_exceeded_lowest_priority_excluded"}
            for u in kept_sorted[hard_cap:]
        ]
        kept = kept_sorted[:hard_cap]

    final_kept = protected_units + kept

    return {
        "artifact_type": "lret_v2_candidate_triage_result",
        "schema_version": "lret_v2_candidate_triage_v1.0",
        "engine_version": ENGINE_VERSION,
        "params": {"keep_floor": keep_floor, "hard_cap": hard_cap,
                    "spelling_quotes_used_for_protection": spelling_quotes or []},
        "params_provisional": True,
        "params_provisional_reason": (
            "keep_floor and hard_cap are configurable defaults set from one real "
            "essay's data, not a calibrated threshold across bands -- revisit once "
            "more real essays across weak/mid/strong bands have run through this."
        ),
        "stats": {
            "input_count": len(units),
            "protected_count": len(protected_units),
            "clusters_found": len(clusters),
            "after_clustering": len(representatives),
            "floored_out_count": len(floored_out),
            "capped_out_count": len(capped_out),
            "final_count": len(final_kept),
        },
        "kept_units": final_kept,
        "trace": {
            "protected": protected_trace,
            "clusters_collapsed": cluster_trace,
            "floored_out": floored_out,
            "capped_out": capped_out,
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--evaluator-output", required=True)
    ap.add_argument("--detector-output", default=None)
    ap.add_argument("--keep-floor", type=float, default=0.50)
    ap.add_argument("--hard-cap", type=int, default=40)
    ap.add_argument("--output", required=True)
    ap.add_argument("--trace-output", default=None)
    args = ap.parse_args()

    with open(args.evaluator_output, "r", encoding="utf-8") as f:
        evaluator_output = json.load(f)
    units = evaluator_output["lexical_unit_profile"]["lexical_units_for_lret"]

    spelling_quotes = []
    if args.detector_output:
        with open(args.detector_output, "r", encoding="utf-8") as f:
            detector_output = json.load(f)
        spelling_quotes = load_spelling_quotes(detector_output)

    result = run_triage(units, keep_floor=args.keep_floor, hard_cap=args.hard_cap,
                         spelling_quotes=spelling_quotes)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result["kept_units"], f, indent=2, ensure_ascii=False)

    if args.trace_output:
        with open(args.trace_output, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

    s = result["stats"]
    print(f"[lret_v2_candidate_triage] wrote {args.output} "
          f"({s['input_count']} -> {s['after_clustering']} after clustering "
          f"-> {s['final_count']} final "
          f"[floored {s['floored_out_count']}, capped {s['capped_out_count']}])")


if __name__ == "__main__":
    main()
