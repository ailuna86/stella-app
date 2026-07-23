#!/usr/bin/env python3
"""
lret_v2_display_quota_engine_v1_0.py
=====================================

Implements LRET_v2_Spec.docx §3.2 + Addendum A (§A1, §A2) + Addendum B (§B6,
§B7): turns a full LRET session's internal FIX/ENHANCE/CLARIFY/KEEP pool into
what a student actually sees -- capped, ranked, band-conditioned.

NEW FILE. Does not edit or import from any existing lret_engine_*.py file
(standalone, per project convention) -- it is a pure post-processing layer
that reads an existing LRET session JSON (any version whose schema matches
the real one this was built and tested against: lret-engine-v1.12.0, schema
LRET_OUTPUT_V1.5) and produces a separate "what gets shown" artifact.

Every ranking signal used below is a REAL field, confirmed present in a real
session (lret_v1_12_0_smoke_output_with_detector.json), not invented:
    - fix_units:      safety_level (single value in the sample: "detector_
                       validated_arbitrated_fix"), detector_confidence
                       (0.80-0.82, varies per unit -- the real differentiator)
    - enhance_units:   candidate_value (0.69 in the sample; the schema
                       supports a real range)
    - clarify_units:   candidate_value (0.55/0.65/0.69 in the sample)
    - keep_units:      candidate_value (0.42-0.85, confirmed range),
                       keep_type (keep_collocation/keep_formulaic_expression/
                       keep_topic_vocabulary/keep_phrase), positive_evidence_role
                       (academic_expression/phrase_control/collocation_control/
                       topic_control)

BAND-CONDITIONED CAPS -- explicitly provisional, not final numbers:
Addendum A's ratio table (§A2) and Addendum B's caps-setting formula (§B7)
both state plainly that the exact cap numbers depend on two pieces of work
that have not happened yet: the LRET_v2_Spec.docx §2 accuracy audit, and the
user's own in-progress scorer recalibration. Per the user's explicit
instruction ("continue working on lret" despite those dependencies), this
engine is built now with Addendum A's illustrative table as the CONFIGURABLE
DEFAULT (see BAND_CAPS below) -- not hardcoded inline, easy to replace once
real numbers exist -- and every output artifact is stamped
`"caps_provisional": true` with the reason, so nobody downstream mistakes a
placeholder number for a calibrated one.

CLI:
    --session PATH          (a real LRET session JSON)
    --score-contract PATH    (optional; same resilient lookup as
                              vocab_coach_selection_engine_v1_0.py; missing/
                              unrecognised -> mid-band default, same fail-safe
                              convention used there)
    --output PATH
"""
import argparse
import json
import os

ENGINE_VERSION = "lret-v2-display-quota-engine-v1.0"

# ---------------------------------------------------------------------------
# Band-conditioned display caps -- Addendum A §A2's illustrative table,
# EXPLICITLY PROVISIONAL (see module docstring). A config dict, not inline
# logic, so it can be replaced wholesale once the accuracy audit + scorer
# recalibration in Addendum A/B land.
# ---------------------------------------------------------------------------

BAND_CAPS = {
    "weak":   {"band_range": (0.0, 5.0),  "fix_cap": 5, "enhance_cap": 2, "clarify_cap": 3, "keep_shown": 2},
    "mid":    {"band_range": (5.0, 6.75), "fix_cap": 4, "enhance_cap": 5, "clarify_cap": 2, "keep_shown": 3},
    "strong": {"band_range": (6.75, 10.0), "fix_cap": 3, "enhance_cap": 3, "clarify_cap": 1, "keep_shown": 5},
}
DEFAULT_TIER = "mid"

# keep_type / positive_evidence_role tie-break ordering, per Addendum A §A1 /
# Addendum B §B6 (grounded in IELTS Lexical Resource descriptor language:
# natural collocation + register control are what's explicitly rewarded at
# higher bands -- not an arbitrary field-ordering preference).
KEEP_TYPE_RANK = {
    "keep_collocation": 2,
    "keep_formulaic_expression": 2,
    "keep_topic_vocabulary": 1,
    "keep_phrase": 1,
}
EVIDENCE_ROLE_RANK = {
    "academic_expression": 1,
}

# safety_level tier ordering for FIX -- config, not hardcoded logic, since
# new safety_level strings will appear as the engine evolves (only one value
# exists in the confirmed real sample; unrecognised values fall back to a
# neutral middle rank rather than crashing or silently sorting last/first).
FIX_SAFETY_RANK = {
    "detector_validated_arbitrated_fix": 2,
}
FIX_SAFETY_RANK_DEFAULT = 1


def load_json(path, default=None):
    if not path or not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_lexical_band(score_contract):
    """Mirrors vocab_coach_selection_engine_v1_0.py's extract_lexical_band --
    same resilient multi-key lookup, same fail-safe-to-None-then-mid-band
    convention, since no real score-contract file exists here either to
    confirm the exact field name against."""
    if not score_contract:
        return None, "no_score_contract_provided"
    candidates = [
        ("lexical_resource_band_estimate", lambda c: c.get("lexical_resource_band_estimate")),
        ("criteria.lexical_resource.band", lambda c: (c.get("criteria") or {}).get("lexical_resource", {}).get("band")),
        ("lexical_resource.band", lambda c: (c.get("lexical_resource") or {}).get("band")),
        ("overall_band_estimate", lambda c: c.get("overall_band_estimate")),
        ("overall_band", lambda c: c.get("overall_band")),
    ]
    for key_name, getter in candidates:
        try:
            val = getter(score_contract)
        except Exception:
            val = None
        if isinstance(val, (int, float)):
            return float(val), key_name
    return None, "no_recognised_band_field"


def band_to_tier(band_value):
    if band_value is None:
        return DEFAULT_TIER, "no_band_value_defaulted_to_mid"
    for tier, cfg in BAND_CAPS.items():
        lo, hi = cfg["band_range"]
        if lo <= band_value < hi:
            return tier, f"band {band_value} in [{lo},{hi})"
    return DEFAULT_TIER, f"band {band_value} outside all configured ranges, defaulted to mid"


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def rank_fix(fix_units):
    def key(u):
        safety_rank = FIX_SAFETY_RANK.get(u.get("safety_level"), FIX_SAFETY_RANK_DEFAULT)
        conf = u.get("detector_confidence", 0.0) or 0.0
        return (safety_rank, conf)
    return sorted(fix_units, key=key, reverse=True)


def rank_by_candidate_value(units):
    return sorted(units, key=lambda u: (u.get("candidate_value") or 0.0), reverse=True)


def keep_quality_score(unit):
    cv = unit.get("candidate_value") or 0.0
    type_rank = KEEP_TYPE_RANK.get(unit.get("keep_type"), 0)
    role_rank = EVIDENCE_ROLE_RANK.get(unit.get("positive_evidence_role"), 0)
    # candidate_value is the primary sort key (rounded to 2dp so genuine ties
    # actually tie rather than floating-point noise splitting them), the two
    # tie-breaks only matter within that rounding band.
    return (round(cv, 2), type_rank, role_rank)


def rank_keep(keep_units):
    return sorted(keep_units, key=keep_quality_score, reverse=True)


# ---------------------------------------------------------------------------
# Main transform
# ---------------------------------------------------------------------------

def build_display(session, score_contract):
    band_value, band_source = extract_lexical_band(score_contract)
    tier, tier_reason = band_to_tier(band_value)
    caps = BAND_CAPS[tier]

    fix_units = session.get("fix_units", [])
    enhance_units = session.get("enhance_units", [])
    clarify_units = session.get("clarify_units", [])
    keep_units = session.get("keep_units", [])

    ranked_fix = rank_fix(fix_units)
    ranked_enhance = rank_by_candidate_value(enhance_units)
    ranked_clarify = rank_by_candidate_value(clarify_units)
    ranked_keep = rank_keep(keep_units)

    shown_fix = ranked_fix[: caps["fix_cap"]]
    shown_enhance = ranked_enhance[: caps["enhance_cap"]]
    shown_clarify = ranked_clarify[: caps["clarify_cap"]]
    shown_keep = ranked_keep[: caps["keep_shown"]]

    def slim(u, fields):
        return {k: u.get(k) for k in fields}

    fix_fields = ["unit_id", "unit_text", "context", "safety_level", "detector_confidence", "error_family", "suggestions"]
    enhance_fields = ["unit_id", "unit_text", "context", "candidate_value", "suggestions"]
    clarify_fields = ["unit_id", "unit_text", "context", "candidate_value", "clarify_reason", "phase1_prompt"]
    keep_fields = ["unit_id", "unit_text", "context", "candidate_value", "keep_type", "positive_evidence_role"]

    return {
        "artifact_type": "lret_v2_student_facing_display",
        "schema_version": "lret_v2_display_v1.0",
        "engine_version": ENGINE_VERSION,
        "identity": session.get("identity"),
        "band_gate": {
            "band_value_used": band_value,
            "band_source": band_source,
            "tier": tier,
            "tier_reason": tier_reason,
            "caps_applied": caps,
        },
        "caps_provisional": True,
        "caps_provisional_reason": (
            "These caps are Addendum A §A2's illustrative starting table, used as a "
            "configurable default per explicit user instruction to keep building -- "
            "they are NOT yet calibrated against the LRET_v2_Spec.docx §2 accuracy "
            "audit or the user's in-progress scorer recalibration (Addendum B §B7). "
            "Replace BAND_CAPS once both exist; do not treat these numbers as final."
        ),
        "fix": {
            "total_internal": len(fix_units),
            "shown_count": len(shown_fix),
            "items": [slim(u, fix_fields) for u in shown_fix],
        },
        "enhance": {
            "total_internal": len(enhance_units),
            "shown_count": len(shown_enhance),
            "items": [slim(u, enhance_fields) for u in shown_enhance],
        },
        "clarify": {
            "total_internal": len(clarify_units),
            "shown_count": len(shown_clarify),
            "items": [slim(u, clarify_fields) for u in shown_clarify],
        },
        "keep": {
            "total_internal": len(keep_units),
            "shown_count": len(shown_keep),
            "display_mode": "count_plus_ranked_examples",
            "summary_text": f"{len(keep_units)} words and phrases already working well",
            "items": [slim(u, keep_fields) for u in shown_keep],
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--score-contract", default=None)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    session = load_json(args.session)
    score_contract = load_json(args.score_contract, default=None)

    display = build_display(session, score_contract)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(display, f, indent=2, ensure_ascii=False)
    print(f"[lret_v2_display_quota] wrote {args.output} "
          f"(tier={display['band_gate']['tier']}, "
          f"fix {display['fix']['shown_count']}/{display['fix']['total_internal']}, "
          f"enhance {display['enhance']['shown_count']}/{display['enhance']['total_internal']}, "
          f"clarify {display['clarify']['shown_count']}/{display['clarify']['total_internal']}, "
          f"keep {display['keep']['shown_count']}/{display['keep']['total_internal']})")


if __name__ == "__main__":
    main()
