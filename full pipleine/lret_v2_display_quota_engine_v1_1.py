#!/usr/bin/env python3
"""
lret_v2_display_quota_engine_v1_1.py
=====================================

v1.1 -- bugfix pass over lret_v2_display_quota_engine_v1_0.py. v1_0 is left
byte-for-byte untouched on disk (project convention: engine files are never
edited in place, only superseded by a new version-numbered file).

WHAT CHANGED FROM v1_0 (both found by re-checking v1_0's claims against a
real session file, gold_sessions/student_123/gold_20260711_182823_essay_001_
2f8d1916/07d_lret_session.json, rather than trusting the v1_0 docstring):

1. `detector_confidence` does NOT exist on real fix_units. v1_0's docstring
   claimed it was "confirmed present... 0.80-0.82, varies per unit -- the
   real differentiator" and `rank_fix()` used it as its secondary sort key.
   Checked directly against real fix_units[0].keys() -- the field is absent.
   That means v1_0's secondary sort key silently evaluated to 0.0 for every
   real unit, i.e. the FIX ranking silently degraded to safety_level-only
   ordering with no real tie-break at all. Fixed here by using
   `occurrence_count` instead -- a field confirmed present on real fix_units
   (how many times this exact error recurs in the essay), which is also a
   defensible real differentiator: an error that repeats matters more to
   surface than a one-off.

2. `--session` is required=True in argparse, but the old `load_json()`
   silently returned None for a missing/invalid path (same helper used for
   the optional `--score-contract`), so a bad `--session` path crashed later
   with an opaque AttributeError on `session.get(...)` instead of a clear
   error. Same bug class already fixed twice earlier in this project
   (vocab_coach_selection_engine_v1_1.py / vocab_coach_ledger_update_v1_1.py)
   via a `require_json()` helper that raises a clear, actionable error for
   REQUIRED paths while `load_json()` (unchanged) stays the fail-safe/
   optional-with-default path for `--score-contract`.

Everything else (BAND_CAPS, KEEP_TYPE_RANK, EVIDENCE_ROLE_RANK,
FIX_SAFETY_RANK, extract_lexical_band, band_to_tier, rank_by_candidate_value,
keep_quality_score, rank_keep, build_display's shape) is unchanged from v1_0
-- those were checked against real data in v1_0 and hold up.

CLI (unchanged):
    --session PATH           (required; a real LRET session JSON)
    --score-contract PATH    (optional; missing/unrecognised -> mid-band
                              default, same fail-safe convention as
                              vocab_coach_selection_engine)
    --output PATH
"""
import argparse
import json
import os

ENGINE_VERSION = "lret-v2-display-quota-engine-v1.1"

# ---------------------------------------------------------------------------
# Band-conditioned display caps -- Addendum A §A2's illustrative table,
# EXPLICITLY PROVISIONAL (unchanged from v1_0 -- still blocked on the
# accuracy audit + scorer recalibration, see LRET_Accuracy_Audit_Findings_v1).
# ---------------------------------------------------------------------------

BAND_CAPS = {
    "weak":   {"band_range": (0.0, 5.0),  "fix_cap": 5, "enhance_cap": 2, "clarify_cap": 3, "keep_shown": 2},
    "mid":    {"band_range": (5.0, 6.75), "fix_cap": 4, "enhance_cap": 5, "clarify_cap": 2, "keep_shown": 3},
    "strong": {"band_range": (6.75, 10.0), "fix_cap": 3, "enhance_cap": 3, "clarify_cap": 1, "keep_shown": 5},
}
DEFAULT_TIER = "mid"

KEEP_TYPE_RANK = {
    "keep_collocation": 2,
    "keep_formulaic_expression": 2,
    "keep_topic_vocabulary": 1,
    "keep_phrase": 1,
}
EVIDENCE_ROLE_RANK = {
    "academic_expression": 1,
}

FIX_SAFETY_RANK = {
    "detector_validated_arbitrated_fix": 2,
}
FIX_SAFETY_RANK_DEFAULT = 1


def load_json(path, default=None):
    """Fail-safe loader for OPTIONAL inputs (e.g. --score-contract): missing
    or unreadable -> default, never raises. Do not use this for required
    CLI arguments -- use require_json() below instead."""
    if not path or not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def require_json(path, arg_name):
    """Loader for REQUIRED inputs. Raises a clear, actionable error instead
    of silently returning None (which previously caused an opaque
    AttributeError deep inside build_display() when --session was missing
    or invalid)."""
    if not path:
        raise SystemExit(f"error: --{arg_name} is required but was not provided.")
    if not os.path.exists(path):
        raise SystemExit(f"error: --{arg_name} path does not exist: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"error: --{arg_name} at {path} is not valid JSON: {exc}")


def extract_lexical_band(score_contract):
    """Unchanged from v1_0 -- same resilient multi-key lookup, same
    fail-safe-to-None-then-mid-band convention."""
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
    """v1_1 fix: secondary key changed from the nonexistent `detector_
    confidence` to `occurrence_count` (confirmed real on fix_units -- how
    many times this exact error recurs in the essay). A recurring error is
    a reasonable real differentiator to prioritise over a one-off, and unlike
    detector_confidence it is not silently zero for every real unit."""
    def key(u):
        safety_rank = FIX_SAFETY_RANK.get(u.get("safety_level"), FIX_SAFETY_RANK_DEFAULT)
        occurrence = u.get("occurrence_count", 0) or 0
        return (safety_rank, occurrence)
    return sorted(fix_units, key=key, reverse=True)


def rank_by_candidate_value(units):
    return sorted(units, key=lambda u: (u.get("candidate_value") or 0.0), reverse=True)


def keep_quality_score(unit):
    cv = unit.get("candidate_value") or 0.0
    type_rank = KEEP_TYPE_RANK.get(unit.get("keep_type"), 0)
    role_rank = EVIDENCE_ROLE_RANK.get(unit.get("positive_evidence_role"), 0)
    return (round(cv, 2), type_rank, role_rank)


def rank_keep(keep_units):
    return sorted(keep_units, key=keep_quality_score, reverse=True)


# ---------------------------------------------------------------------------
# Main transform (unchanged from v1_0 except fix_fields below)
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

    # v1_1: dropped detector_confidence (not a real field), added
    # occurrence_count (the real one actually used for ranking above) so the
    # displayed payload matches what actually drove the ordering.
    fix_fields = ["unit_id", "unit_text", "context", "safety_level", "occurrence_count", "error_family", "suggestions"]
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
            "configurable default -- they are NOT yet calibrated against the "
            "LRET_Accuracy_Audit_Findings_v1 results or a scorer recalibration. "
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

    session = require_json(args.session, "session")
    score_contract = load_json(args.score_contract, default=None)

    display = build_display(session, score_contract)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(display, f, indent=2, ensure_ascii=False)
    print(f"[lret_v2_display_quota v1.1] wrote {args.output} "
          f"(tier={display['band_gate']['tier']}, "
          f"fix {display['fix']['shown_count']}/{display['fix']['total_internal']}, "
          f"enhance {display['enhance']['shown_count']}/{display['enhance']['total_internal']}, "
          f"clarify {display['clarify']['shown_count']}/{display['clarify']['total_internal']}, "
          f"keep {display['keep']['shown_count']}/{display['keep']['total_internal']})")


if __name__ == "__main__":
    main()
