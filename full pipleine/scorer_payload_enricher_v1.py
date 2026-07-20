"""
scorer_payload_enricher_v1.py
==============================
NEW FILE — Sprint 2, Issue I-2 fix.

PROBLEM
-------
The detector (det_vip_v18d.x) always writes:
    scorer_payload.lr_positive_signals = {
        "ocd_positive_hits": 0,
        "LR11_dynamic_multiword_density": 0.0
    }

Both values are falsy. The scorer_engine_v2_1_6.py reads them as:
    _LR11_sp = float(lr_pos.get("LR11_dynamic_multiword_density") or 0.30)
    _ocd_sp  = int(lr_pos.get("ocd_positive_hits") or 0)

Because 0.0 is falsy, LR11 defaults to 0.30. ocd_hits stays 0.

Downstream gate (added in scorer v2.1.5):
    if bands["lexical_resource"] >= 7 and ocd_hits < 2 and LR11 < 0.35:
        cap LR to 6

LR11 = 0.30 < 0.35 always holds. ocd_hits = 0 < 2 always holds.
→ Any essay that the metrics score at Band 7 is silently capped to Band 6.

Additionally, LR4 (collocation control, 15% weight in LR formula) depends on LR11:
    LR4 = clamp01(0.45 + LR11 * 0.35 + (1 - semantic_lr_damage) * 0.20)
With LR11 = 0.30 vs. a correct 0.60, this depresses LR4 by ~0.105, which
depresses the LR composite by ~0.016 — worth roughly 0.14 bands.

SOLUTION
--------
This enricher runs BETWEEN the detector output load and the scorer call
(pipeline_runner_v12.py, step 1.5). It computes proxy values from the
COLLOCATION rows that ARE correctly produced by the detector.

Proxy logic (conservative by design):
    n_coll  = number of chargeable rows with rubric='lexical_resource'
              AND family IN ('COLLOCATION', 'SEMANTIC_COMBINATION')
    wc      = scorer_payload.metadata.word_count (default 280)

    coll_rate = n_coll / wc * 100   # errors per 100 words

    LR11_proxy:
        max(0.0, min(0.95, 0.70 - coll_rate * 0.15))

        Examples:
          0 errors in 250w → rate 0.00 → LR11 = 0.70  (strong control)
          1 error  in 250w → rate 0.40 → LR11 = 0.64  (good)
          2 errors in 225w → rate 0.89 → LR11 = 0.57  (adequate, Band 6-7)
          3 errors in 225w → rate 1.33 → LR11 = 0.50
          4 errors in 225w → rate 1.78 → LR11 = 0.43
          5 errors in 250w → rate 2.00 → LR11 = 0.40  (borderline)
          8 errors in 251w → rate 3.19 → LR11 = 0.22  (weak, Band 4-5)
         12 errors in 251w → rate 4.78 → LR11 = 0.00  (floor)

    Gate opens (LR11 >= 0.35):  5 or fewer errors per 250-word essay (rate < 2.33)
    Gate stays shut (LR11 < 0.35): 6+ errors per 250-word essay

    ocd_hits_proxy:
        0 errors → 3  (confident, well above gate threshold of 2)
        1 error  → 2  (meets gate threshold exactly)
        2 errors → 1  (does not meet gate threshold, relies on LR11)
        3+ errors → 0 (below threshold)

    Gate opens (ocd_hits >= 2):  0-1 COLLOCATION errors

Both conditions: gate suppressed iff LR11 >= 0.35 OR ocd_hits >= 2.
Combining the proxies:
    2 errors (225w): LR11 = 0.57 → gate bypassed via LR11 path
    3 errors (225w): ocd=0, LR11 = 0.50 → gate bypassed via LR11 path
    6 errors (250w): ocd=0, LR11 = 0.32 → gate fires (correct: too many errors)

The enricher DOES NOT overwrite if either value is already non-zero
(i.e., if a future detector version correctly populates these fields,
the enricher becomes a no-op).

SAFETY PROPERTIES
-----------------
- Conservative: does not unlock Band 7 for essays with high COLLOCATION density
- Monotone: more errors → lower proxy → gate more likely to fire
- No false Band 7 inflation for Band 5 essays: Band 5 typically has 4-8+ errors;
  enricher still gives LR11 < 0.35 or LR composite too low for Band 7 naturally
- Idempotent: calling twice has no additional effect

USAGE
-----
    from scorer_payload_enricher_v1 import enrich_scorer_payload

    # After loading detector output, before calling scorer:
    detector_result = detector_output["results"][0]
    enriched_result = enrich_scorer_payload(detector_result)
    # Pass enriched_result to scorer as normal
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# =============================================================================
# CONSTANTS
# =============================================================================

# Families that count as "multiword/collocation" errors
_COLL_FAMILIES: frozenset = frozenset({"COLLOCATION", "SEMANTIC_COMBINATION"})

# LR11 proxy formula coefficients
_LR11_BASE:  float = 0.70   # intercept: max LR11 when no errors
_LR11_SLOPE: float = 0.15   # penalty per error per 100 words
_LR11_FLOOR: float = 0.00   # minimum LR11 proxy
_LR11_CAP:   float = 0.95   # maximum LR11 proxy

# ocd_hits proxy thresholds
_OCD_PROXY_BY_N_ERRORS: Dict[int, int] = {
    0: 3,   # 0 errors → 3 hits (well above gate threshold of 2)
    1: 2,   # 1 error  → 2 hits (meets gate threshold)
    2: 1,   # 2 errors → 1 hit  (below threshold; gate relies on LR11)
}
_OCD_DEFAULT_HIGH_ERRORS: int = 0   # 3+ errors → 0 hits

# Fallback word count when scorer_payload.metadata.word_count is absent
_DEFAULT_WORD_COUNT: int = 280

# Enricher version tag (written to lr_positive_signals for audit)
_ENRICHER_VERSION: str = "scorer_payload_enricher_v1"

# =============================================================================
# CORE PROXY COMPUTATION
# =============================================================================

def _count_coll_rows(chargeable_rows: List[Dict[str, Any]]) -> int:
    """
    Count COLLOCATION and SEMANTIC_COMBINATION rows in the LR rubric.
    These are the error families most indicative of multiword phrase control.
    """
    return sum(
        1 for row in chargeable_rows
        if row.get("rubric") == "lexical_resource"
        and row.get("family") in _COLL_FAMILIES
    )


def _compute_lr11_proxy(n_coll: int, word_count: int) -> float:
    """
    Estimate LR11_dynamic_multiword_density from collocation error count.

    Formula: LR11 = clamp(LR11_BASE - (n_coll / wc * 100) * LR11_SLOPE, floor, cap)

    The formula is calibrated so that:
    - 0 errors: LR11 = 0.70 (strong vocabulary control)
    - ~3 errors per 100 words: LR11 ≈ 0.25 (gate threshold 0.35 not met)
    - Gate opens at LR11 >= 0.35 → requires < 2.33 errors per 100 words
    """
    wc = max(word_count, 1)
    coll_rate = n_coll / wc * 100
    lr11 = _LR11_BASE - coll_rate * _LR11_SLOPE
    return max(_LR11_FLOOR, min(_LR11_CAP, lr11))


def _compute_ocd_proxy(n_coll: int) -> int:
    """
    Estimate ocd_positive_hits from collocation error count.

    Fewer collocation errors → more likely the student used correct collocations.
    Threshold for gate bypass: ocd_hits >= 2.
    """
    return _OCD_PROXY_BY_N_ERRORS.get(n_coll, _OCD_DEFAULT_HIGH_ERRORS)


# =============================================================================
# PUBLIC API
# =============================================================================

def enrich_scorer_payload(
    detector_result: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Enrich scorer_payload.lr_positive_signals with proxy values when the
    detector has not computed them (both ocd_hits=0 and LR11=0.0).

    Args:
        detector_result: One element of detector_output["results"].
                         Modified in-place AND returned.

    Returns:
        The same detector_result dict, with scorer_payload.lr_positive_signals
        updated if enrichment was applied.
    """
    sp: Dict[str, Any] = detector_result.get("scorer_payload", {})
    lr_pos: Dict[str, Any] = sp.get("lr_positive_signals") or {}

    existing_ocd  = lr_pos.get("ocd_positive_hits", 0)
    existing_lr11 = lr_pos.get("LR11_dynamic_multiword_density", 0.0)

    # Guard: do not overwrite if either value is already non-zero
    # (future detector versions may compute these correctly)
    if existing_ocd or existing_lr11:
        sp["_enricher_status"] = "skipped_already_populated"
        return detector_result

    # Pull inputs from scorer_payload
    chargeable_rows: List[Dict[str, Any]] = sp.get("chargeable_detector_rows", [])
    meta: Dict[str, Any]                  = sp.get("metadata", {})
    word_count: int = int(meta.get("word_count") or _DEFAULT_WORD_COUNT)
    if word_count < 1:
        word_count = _DEFAULT_WORD_COUNT

    n_coll    = _count_coll_rows(chargeable_rows)
    coll_rate = round(n_coll / word_count * 100, 4)

    lr11_proxy = _compute_lr11_proxy(n_coll, word_count)
    ocd_proxy  = _compute_ocd_proxy(n_coll)

    # Gate analysis (for audit only — does not affect computation)
    gate_would_fire = (ocd_proxy < 2) and (lr11_proxy < 0.35)

    # Write proxy into scorer_payload
    sp["lr_positive_signals"] = {
        "ocd_positive_hits":              ocd_proxy,
        "LR11_dynamic_multiword_density": round(lr11_proxy, 4),
        # Audit fields (preserved through scorer but not used in scoring)
        "_enricher_version":   _ENRICHER_VERSION,
        "_enricher_applied":   True,
        "_proxy_basis": {
            "n_coll_rows":           n_coll,
            "word_count":            word_count,
            "coll_rate_per_100w":    coll_rate,
            "gate_would_fire":       gate_would_fire,
            "gate_condition_met":    not gate_would_fire,
        },
    }
    sp["_enricher_status"] = "applied"

    return detector_result


def enrich_detector_output(
    detector_output: Dict[str, Any],
    result_index: int = 0,
) -> Dict[str, Any]:
    """
    Convenience wrapper: enriches a specific result in a full detector output
    object (the top-level dict with a "results" list).

    Args:
        detector_output: The full detector output dict (has "results" key).
        result_index:    Which result to enrich. Default 0 (single-essay runs).

    Returns:
        The same detector_output dict, modified in-place.
    """
    results = detector_output.get("results", [])
    if not results or result_index >= len(results):
        return detector_output
    enrich_scorer_payload(results[result_index])
    return detector_output


# =============================================================================
# DIAGNOSTIC / PREVIEW
# =============================================================================

def preview_enrichment(
    detector_result: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Compute what the enricher WOULD write without modifying detector_result.
    Useful for logging and unit tests.

    Returns a dict describing the enrichment outcome.
    """
    sp        = detector_result.get("scorer_payload", {})
    lr_pos    = sp.get("lr_positive_signals") or {}
    existing_ocd  = lr_pos.get("ocd_positive_hits", 0)
    existing_lr11 = lr_pos.get("LR11_dynamic_multiword_density", 0.0)

    if existing_ocd or existing_lr11:
        return {
            "would_apply": False,
            "reason": "lr_positive_signals already populated",
            "existing": {"ocd_positive_hits": existing_ocd,
                         "LR11": existing_lr11},
        }

    rows       = sp.get("chargeable_detector_rows", [])
    meta       = sp.get("metadata", {})
    word_count = int(meta.get("word_count") or _DEFAULT_WORD_COUNT)
    if word_count < 1:
        word_count = _DEFAULT_WORD_COUNT

    n_coll     = _count_coll_rows(rows)
    coll_rate  = round(n_coll / word_count * 100, 4)
    lr11_proxy = _compute_lr11_proxy(n_coll, word_count)
    ocd_proxy  = _compute_ocd_proxy(n_coll)
    gate_fires = (ocd_proxy < 2) and (lr11_proxy < 0.35)

    # What the scorer would use with old zeros (via 'or' fallback):
    old_lr11_effective = 0.30   # float(0.0 or 0.30)
    old_ocd_effective  = 0      # int(0 or 0)

    return {
        "would_apply": True,
        "proxy": {
            "n_coll_rows":        n_coll,
            "word_count":         word_count,
            "coll_rate_per_100w": coll_rate,
            "ocd_positive_hits":  ocd_proxy,
            "LR11":               round(lr11_proxy, 4),
        },
        "gate_analysis": {
            "old_lr11_effective":  old_lr11_effective,
            "new_lr11":            round(lr11_proxy, 4),
            "old_ocd":             old_ocd_effective,
            "new_ocd":             ocd_proxy,
            "gate_fires_old":      True,   # always fired with ocd=0, LR11=0.30
            "gate_fires_new":      gate_fires,
            "gate_condition":      "LR>=7 AND ocd_hits<2 AND LR11<0.35",
            "LR11_threshold":      0.35,
            "ocd_threshold":       2,
        },
        "impact_note": (
            f"LR11: 0.30 (old effective) → {round(lr11_proxy, 4)} (proxy). "
            f"LR4 impact ≈ +{round((lr11_proxy - 0.30) * 0.35 * 0.15, 4)} on LR composite. "
            f"Gate: {'STILL FIRES' if gate_fires else 'SUPPRESSED'}."
        ),
    }
