#!/usr/bin/env python3
"""
li_engine_premium_v1_0.py
VA English Learning Assistant — LI Engine Premium v1.0
June 2026

Specification: li_engine_premium_spec_v1_0.md

ROLE
====
Stateful cross-session intelligence layer for premium-tier students.
Operates above LI Engine Light (rubric-only) by consuming three input
channels per essay submission:

  Channel 1 (synchronous): FG downstream dict — ECI gate, rubric pressure,
      dominant families, overall band, task_type.
  Channel 2 (synchronous): PE learning_intelligence_ingestion_payload —
      full skill-level pressure, dependency-adjusted pressures, semantic health,
      recommended targets, confirmed strengths.
  Channel 3 (async, optional): Practice Engine mastery signals —
      per-family mastery float, exercise counts, last_practiced timestamp.

DOES NOT:
  - Score essays
  - Modify PE or FG output
  - Generate student-facing text (FG's job)
  - Assign exercises (Practice Engine's job)
  - Call LLMs (Gold tier only)

ARCHITECTURE NOTE
=================
Progress Tracker is a DOWNSTREAM CONSUMER of LIE output. It does not feed
data into LIE. LIE reads from PE directly (via pe_li_payload) and from its
own persisted student_record JSON.

STORAGE
=======
  li_premium_student_{student_id}.json  (separate from Light's li_student_*.json)

DEGRADATION
===========
  - pe_li_payload absent       → rubric-only mode (Light-equivalent)
  - practice signals absent    → no mastery discounts
  - eci_tier == "blocked"      → skip accumulation, return current profile
  - eci_tier == "medium"       → accumulate rubric + band; skip families
  - skill_pressure_vector == {}→ skip skill-level accumulation for session

CLI
===
  python li_engine_premium_v1_0.py -i input.json --storage ./li_premium_storage
  python li_engine_premium_v1_0.py -i input.json --storage ./li_premium_storage --profile-only
  python li_engine_premium_v1_0.py --migrate-light li_student_s001.json --storage ./li_premium_storage
"""

from __future__ import annotations

import argparse
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# Version constants
# ─────────────────────────────────────────────────────────────────────────────

LI_ENGINE_VERSION       = "li_engine_premium_v1.0"
STUDENT_RECORD_SCHEMA   = "LI_STUDENT_RECORD_PREMIUM_V1.0"
STUDENT_PROFILE_SCHEMA  = "LI_STUDENT_PROFILE_PREMIUM_V1.0"
LIGHT_RECORD_SCHEMA     = "LI_STUDENT_RECORD_V1.1"   # for migration detection

# ─────────────────────────────────────────────────────────────────────────────
# Constants — inherited from Light v1.1 (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

DECAY_BASE                      = 0.85
PRESSURE_PERSISTENCE_THRESHOLD  = 3.0
RESOLVED_PRESSURE_THRESHOLD     = 1.5
APPEARANCE_RATE_THRESHOLD       = 0.50
TREND_MIN_SESSIONS              = 3
TREND_SLOPE_THRESHOLD           = 0.10
MAX_RECOMMENDED_FOCUS           = 3
DATA_CONFIDENCE_HIGH            = 4
DATA_CONFIDENCE_MEDIUM          = 2

# ─────────────────────────────────────────────────────────────────────────────
# Constants — new in Premium
# ─────────────────────────────────────────────────────────────────────────────

SKILL_PERSISTENCE_THRESHOLD     = 2.5
SKILL_RESOLVED_THRESHOLD        = 1.2

LIMITER_SHIFT_CONVERGENCE_RATIO = 0.75
LIMITER_SHIFT_MIN_SESSIONS      = 3

MASTERY_DISCOUNT_FULL           = 0.70   # mastery >= 0.85
MASTERY_DISCOUNT_STRONG         = 0.50   # mastery >= 0.70
MASTERY_DISCOUNT_PARTIAL        = 0.25   # mastery >= 0.50
MASTERY_STALE_DAYS              = 30

PLATEAU_MIN_SESSIONS            = 4
PLATEAU_SLOPE_MAX               = 0.05
PLATEAU_MIN_PRESSURE            = 2.0

TASK_TYPE_MIN_SESSIONS          = 2
TRACKED_TASK_TYPES              = ["argument", "causes_effects", "problem_solution", "two_part"]

# normalisation map for task_type variants arriving from FG/PE
TASK_TYPE_NORM: Dict[str, str] = {
    "cause_effect":       "causes_effects",
    "causes_effects":     "causes_effects",
    "cause_and_effect":   "causes_effects",
    "problem_solution":   "problem_solution",
    "problem_and_solution": "problem_solution",
    "two_part":           "two_part",
    "two_part_question":  "two_part",
    "argument":           "argument",
    "opinion":            "argument",
    "agree_disagree":     "argument",
    "discuss":            "argument",
    "discussion":         "argument",
}

RECOVERABILITY_CONCERN_THRESHOLD = 0.72
RECOVERABILITY_SESSIONS_MIN      = 3

ALL_RUBRICS = ["GRA", "LR", "TR", "CC"]


# ─────────────────────────────────────────────────────────────────────────────
# Utility: timestamps
# ─────────────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        # Python 3.7+ fromisoformat doesn't handle 'Z' suffix
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _is_stale(iso_str: Optional[str]) -> bool:
    """Return True if the timestamp is older than MASTERY_STALE_DAYS or unparseable."""
    dt = _parse_iso(iso_str)
    if dt is None:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt) > timedelta(days=MASTERY_STALE_DAYS)


# ─────────────────────────────────────────────────────────────────────────────
# Utility: safe numeric
# ─────────────────────────────────────────────────────────────────────────────

def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
        return default if math.isnan(v) else v
    except Exception:
        return default


def _safe_float_or_none(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
        return None if math.isnan(v) else v
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Temporal decay weights (inherited from Light v1.1)
# ─────────────────────────────────────────────────────────────────────────────

def _compute_weights(n_sessions: int) -> List[float]:
    """
    Exponential decay: w(i) = DECAY_BASE^(N-1-i), normalised to sum=1.
    Most recent session (i=N-1) has weight 1 before normalisation.
    """
    if n_sessions <= 0:
        return []
    raw   = [DECAY_BASE ** (n_sessions - 1 - i) for i in range(n_sessions)]
    total = sum(raw)
    return [w / total for w in raw]


# ─────────────────────────────────────────────────────────────────────────────
# Trend direction — OLS slope (inherited from Light v1.1)
# ─────────────────────────────────────────────────────────────────────────────

def _trend_direction(values: List[float]) -> str:
    """
    OLS slope over time-ordered values.
    Positive slope = worsening (pressure rising or band falling).
    Returns 'stable' if fewer than TREND_MIN_SESSIONS values.
    """
    if len(values) < TREND_MIN_SESSIONS:
        return "stable"
    n  = len(values)
    xs = list(range(n))
    xm = sum(xs) / n
    ym = sum(values) / n
    num = sum((xs[i] - xm) * (values[i] - ym) for i in range(n))
    den = sum((xs[i] - xm) ** 2 for i in range(n))
    if den == 0:
        return "stable"
    slope = num / den
    if slope > TREND_SLOPE_THRESHOLD:
        return "worsening"
    if slope < -TREND_SLOPE_THRESHOLD:
        return "improving"
    return "stable"


def _trend_direction_bands(values: List[float]) -> str:
    """
    Same OLS, but direction is INVERTED for band values:
    rising band = improving; falling band = worsening.
    """
    raw = _trend_direction(values)
    if raw == "worsening":
        return "improving"
    if raw == "improving":
        return "worsening"
    return "stable"


# ─────────────────────────────────────────────────────────────────────────────
# Mastery discount
# ─────────────────────────────────────────────────────────────────────────────

def _compute_mastery_discount(mastery: float) -> float:
    if mastery >= 0.85:
        return MASTERY_DISCOUNT_FULL
    if mastery >= 0.70:
        return MASTERY_DISCOUNT_STRONG
    if mastery >= 0.50:
        return MASTERY_DISCOUNT_PARTIAL
    return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Task-type normalisation
# ─────────────────────────────────────────────────────────────────────────────

def _norm_task_type(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    key = str(raw).lower().strip()
    return TASK_TYPE_NORM.get(key)


# ─────────────────────────────────────────────────────────────────────────────
# Student record I/O
# ─────────────────────────────────────────────────────────────────────────────

def _record_path(student_id: str, storage_path: str) -> str:
    return os.path.join(storage_path, f"li_premium_student_{student_id}.json")


def load_student_record(student_id: str, storage_path: str) -> Optional[Dict[str, Any]]:
    """Load premium student record JSON. Returns None if not found."""
    path = _record_path(student_id, storage_path)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_student_record(record: Dict[str, Any], storage_path: str) -> None:
    """Persist premium student record."""
    student_id = record.get("student_id", "unknown")
    path       = _record_path(student_id, storage_path)
    os.makedirs(storage_path, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)


def _empty_student_record(student_id: str, now: str) -> Dict[str, Any]:
    return {
        "schema_version":   STUDENT_RECORD_SCHEMA,
        "student_id":       student_id,
        "created_at":       now,
        "updated_at":       now,
        "session_count":    0,
        "submitted_count":  0,
        "band_history":     [],
        "rubric_pressure_history":  {r: [] for r in ALL_RUBRICS},
        "skill_pressure_history":   {},
        "family_pressure_history":  {},
        "semantic_health_history":  [],
        "target_history":           [],
        "task_type_pressure_history": {tt: {"rubric_pressure": {r: [] for r in ALL_RUBRICS}}
                                       for tt in TRACKED_TASK_TYPES},
        "mastery_state":    {},
        "accumulated_profile": {
            "rubric_weighted_pressure":  {r: 0.0 for r in ALL_RUBRICS},
            "skill_weighted_pressure":   {},
            "family_weighted_pressure":  {},
            "rubric_appearance_counts":  {r: 0 for r in ALL_RUBRICS},
            "skill_appearance_counts":   {},
        },
        "debug_events": [],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Input validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_li_input(li_input: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    Validate the input dict before accumulation.
    Returns (is_valid, error_message).
    """
    if not li_input.get("student_id"):
        return False, "student_id missing — must be injected by API layer"
    if not li_input.get("essay_id"):
        return False, "essay_id missing"
    ds = li_input.get("fg_downstream")
    if not isinstance(ds, dict):
        return False, "fg_downstream dict missing"
    eci_tier = ds.get("eci_tier")
    if eci_tier not in ("high", "medium", "blocked"):
        return False, f"fg_downstream.eci_tier invalid: {eci_tier!r}"
    if eci_tier != "blocked":
        if ds.get("primary_rubric") not in (ALL_RUBRICS + [None]):
            return False, f"fg_downstream.primary_rubric invalid: {ds.get('primary_rubric')!r}"
    return True, None


# ─────────────────────────────────────────────────────────────────────────────
# Weighted pressure recompute helpers
# ─────────────────────────────────────────────────────────────────────────────

def _recompute_weighted_pressure(history: List[Dict[str, Any]]) -> float:
    """
    Recompute DECAY_BASE-weighted pressure from a list of
    {session_index, pressure, essay_id} entries.
    Full recompute on every call — stable under record reload.
    """
    pressures = [e["pressure"] for e in history]
    weights   = _compute_weights(len(pressures))
    return round(sum(w * p for w, p in zip(weights, pressures)), 4)


# ─────────────────────────────────────────────────────────────────────────────
# Channel 1: FG downstream accumulation
# ─────────────────────────────────────────────────────────────────────────────

def _accumulate_fg_downstream(
    record:     Dict[str, Any],
    ds:         Dict[str, Any],
    session_idx: int,
    essay_id:   str,
    submitted:  str,
) -> None:
    """
    Accumulate data from fg_downstream:
      - overall band + per-rubric bands into band_history
      - rubric pressure history
      - family pressure history (high/medium ECI only)
      - task-type-separated rubric pressure
    """
    eci_tier = ds.get("eci_tier", "high")

    # Band history
    per_rubric_bands: Dict[str, Optional[float]] = {}
    # FG downstream doesn't carry per-rubric bands directly; they come from PE payload.
    # We store null here and patch them in from pe_li_payload if available.
    for r in ALL_RUBRICS:
        per_rubric_bands[r] = None

    record.setdefault("band_history", []).append({
        "essay_id":         essay_id,
        "submitted_at":     submitted,
        "overall_band":     _safe_float_or_none(ds.get("overall_band")),
        "task_type":        _norm_task_type(ds.get("task_type")),
        "per_rubric_bands": per_rubric_bands,
    })

    # Rubric pressure
    primary_rubric   = ds.get("primary_rubric")
    primary_pressure = _safe_float(ds.get("primary_pressure"), 0.0)

    rph = record.setdefault("rubric_pressure_history", {r: [] for r in ALL_RUBRICS})
    for r in ALL_RUBRICS:
        rph.setdefault(r, [])
        p = primary_pressure if r == primary_rubric else 0.0
        rph[r].append({"session_index": session_idx, "pressure": p, "essay_id": essay_id})

    # Appearance counts
    acc = record.setdefault("accumulated_profile", {})
    app_r = acc.setdefault("rubric_appearance_counts", {r: 0 for r in ALL_RUBRICS})
    if primary_rubric and primary_rubric in ALL_RUBRICS:
        app_r[primary_rubric] = app_r.get(primary_rubric, 0) + 1

    # Family pressure (high + medium ECI)
    if eci_tier in ("high", "medium"):
        dom_fams     = ds.get("dominant_families") or []
        fam_pressure = primary_pressure / len(dom_fams) if dom_fams else 0.0
        fph = record.setdefault("family_pressure_history", {})
        for fam in dom_fams:
            fph.setdefault(fam, []).append(
                {"session_index": session_idx, "pressure": fam_pressure, "essay_id": essay_id}
            )

    # Task-type-separated rubric pressure
    task_type = _norm_task_type(ds.get("task_type"))
    if task_type in TRACKED_TASK_TYPES:
        tth = record.setdefault("task_type_pressure_history", {})
        tt_entry = tth.setdefault(task_type, {"rubric_pressure": {r: [] for r in ALL_RUBRICS}})
        tt_rp = tt_entry.setdefault("rubric_pressure", {r: [] for r in ALL_RUBRICS})
        for r in ALL_RUBRICS:
            tt_rp.setdefault(r, [])
            p = primary_pressure if r == primary_rubric else 0.0
            tt_rp[r].append({"session_index": session_idx, "pressure": p, "essay_id": essay_id})


# ─────────────────────────────────────────────────────────────────────────────
# Channel 2: PE LI payload accumulation
# ─────────────────────────────────────────────────────────────────────────────

def _accumulate_pe_li_payload(
    record:     Dict[str, Any],
    pe_payload: Dict[str, Any],
    session_idx: int,
    essay_id:   str,
    eci_tier:   str,
) -> None:
    """
    Accumulate data from pe_li_payload:
      - skill-level pressure history
      - per-rubric bands (patch into the last band_history entry)
      - semantic health history
      - target history
    """
    # Skill pressure
    top_skills = pe_payload.get("top_skills_by_pressure") or []
    if top_skills and eci_tier in ("high", "medium"):
        sph = record.setdefault("skill_pressure_history", {})
        acc = record.setdefault("accumulated_profile", {})
        app_s = acc.setdefault("skill_appearance_counts", {})

        seen_skills = set()
        for entry in top_skills:
            skill = entry.get("skill")
            if not skill:
                continue
            pressure = _safe_float(
                entry.get("dependency_adjusted_pressure") or entry.get("pressure"), 0.0
            )
            sph.setdefault(skill, []).append({
                "session_index":              session_idx,
                "pressure":                   pressure,
                "dependency_adjusted_pressure": pressure,
                "essay_id":                   essay_id,
            })
            app_s[skill] = app_s.get(skill, 0) + 1
            seen_skills.add(skill)

        # Zero-pad skills that have history but were absent this session
        for skill, entries in sph.items():
            if skill not in seen_skills:
                entries.append({
                    "session_index":              session_idx,
                    "pressure":                   0.0,
                    "dependency_adjusted_pressure": 0.0,
                    "essay_id":                   essay_id,
                })

    # Per-rubric bands — patch into the last band_history entry if available
    # PE doesn't emit per-rubric bands in li_payload currently (open question §16.1)
    # We leave per_rubric_bands as null until PE patches skill_pressure_vector.

    # Semantic health
    sem = pe_payload.get("semantic_health") or {}
    rec  = _safe_float_or_none(sem.get("mean_recoverability"))
    adr  = _safe_float_or_none(sem.get("affected_discourse_ratio"))
    if rec is not None or adr is not None:
        record.setdefault("semantic_health_history", []).append({
            "session_index":            session_idx,
            "essay_id":                 essay_id,
            "mean_recoverability":      rec,
            "affected_discourse_ratio": adr,
        })

    # Target history (top 3 recommended targets for this session)
    targets = pe_payload.get("recommended_targets") or []
    if targets:
        record.setdefault("target_history", []).append({
            "session_index": session_idx,
            "essay_id":      essay_id,
            "targets": [
                {
                    "target_id":       t.get("target_id"),
                    "learning_target": t.get("learning_target"),
                    "pressure":        _safe_float(t.get("pressure"), 0.0),
                    "rank":            t.get("rank", 0),
                }
                for t in targets[:3]
            ],
        })


# ─────────────────────────────────────────────────────────────────────────────
# Channel 3: Practice Engine mastery signals
# ─────────────────────────────────────────────────────────────────────────────

def _update_mastery_state(
    record:  Dict[str, Any],
    signals: List[Dict[str, Any]],
) -> None:
    """
    Upsert mastery state from Practice Engine signals.
    Stale signals (> MASTERY_STALE_DAYS) are recorded but discount is zeroed.
    """
    ms = record.setdefault("mastery_state", {})
    for sig in signals:
        family = sig.get("family")
        if not family:
            continue
        mastery  = _safe_float(sig.get("mastery"), 0.0)
        last_prac = sig.get("last_practiced")
        stale     = _is_stale(last_prac)
        discount  = 0.0 if stale else _compute_mastery_discount(mastery)
        ms[family] = {
            "mastery":        round(mastery, 4),
            "skill":          sig.get("skill"),
            "exercise_count": int(sig.get("exercise_count") or 0),
            "last_practiced": last_prac,
            "discount":       round(discount, 4),
            "stale":          stale,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Recompute all weighted pressures (full recompute — stable under reload)
# ─────────────────────────────────────────────────────────────────────────────

def _recompute_all_weighted_pressures(record: Dict[str, Any]) -> None:
    """
    Recompute rubric, skill, and family weighted pressures in accumulated_profile.
    Called after every accumulation. Does NOT apply mastery discounts here —
    discounts are applied in build_student_profile_premium() at read time.
    """
    acc = record.setdefault("accumulated_profile", {})

    # Rubric
    rwp = {}
    for r in ALL_RUBRICS:
        history = record.get("rubric_pressure_history", {}).get(r, [])
        rwp[r]  = _recompute_weighted_pressure(history)
    acc["rubric_weighted_pressure"] = rwp

    # Skill
    swp = {}
    for skill, history in (record.get("skill_pressure_history") or {}).items():
        swp[skill] = _recompute_weighted_pressure(history)
    acc["skill_weighted_pressure"] = swp

    # Family
    fwp = {}
    for fam, history in (record.get("family_pressure_history") or {}).items():
        fwp[fam] = _recompute_weighted_pressure(history)
    acc["family_weighted_pressure"] = fwp


# ─────────────────────────────────────────────────────────────────────────────
# Main accumulation entry point
# ─────────────────────────────────────────────────────────────────────────────

def accumulate(
    li_input:     Dict[str, Any],
    storage_path: str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Main entry point. Processes one essay submission.

    li_input fields:
      student_id      : str  (REQUIRED, injected by API layer)
      essay_id        : str
      submitted_at    : ISO8601
      fg_downstream   : dict  (REQUIRED — from Feedback Generator)
      pe_li_payload   : dict  (OPTIONAL — from PE learning_intelligence_ingestion_payload)
      practice_mastery_signals : list  (OPTIONAL — from Practice Engine)

    Returns (updated_student_record, student_profile_premium).
    ECI-blocked sessions do NOT update history; current profile is returned unchanged.
    """
    now        = _now_iso()
    student_id = str(li_input["student_id"])
    essay_id   = str(li_input.get("essay_id") or "unknown")
    submitted  = str(li_input.get("submitted_at") or now)

    ds         = li_input.get("fg_downstream") or {}
    pe_payload = li_input.get("pe_li_payload") or {}
    mastery_sigs = li_input.get("practice_mastery_signals") or []

    eci_tier = ds.get("eci_tier", "high")

    record = load_student_record(student_id, storage_path) \
             or _empty_student_record(student_id, now)
    record["updated_at"]     = now
    record["submitted_count"] = record.get("submitted_count", 0) + 1

    # ── ECI-blocked: skip all accumulation ──
    if eci_tier == "blocked":
        record.setdefault("debug_events", []).append({
            "event":      "eci_blocked_skip",
            "essay_id":   essay_id,
            "submitted":  submitted,
        })
        save_student_record(record, storage_path)
        return record, build_student_profile_premium(record)

    # ── Non-blocked: full accumulation ──
    session_idx = record.get("session_count", 0)   # 0-based before increment
    record["session_count"] = session_idx + 1

    # Channel 1: FG downstream
    _accumulate_fg_downstream(record, ds, session_idx, essay_id, submitted)

    # Channel 2: PE LI payload (optional)
    if pe_payload:
        _accumulate_pe_li_payload(record, pe_payload, session_idx, essay_id, eci_tier)
    else:
        record.setdefault("debug_events", []).append({
            "event":    "pe_li_payload_absent",
            "essay_id": essay_id,
            "note":     "Degraded to rubric-only accumulation (Light-equivalent)",
        })

    # Channel 3: Practice mastery signals (optional)
    if mastery_sigs:
        _update_mastery_state(record, mastery_sigs)

    # Recompute all weighted pressures
    _recompute_all_weighted_pressures(record)

    save_student_record(record, storage_path)
    return record, build_student_profile_premium(record)


# ─────────────────────────────────────────────────────────────────────────────
# Profile builder helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_band_trajectory(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Overall + per-rubric band trajectories.
    Band direction is inverted vs pressure: rising band = improving.
    """
    band_history = record.get("band_history") or []

    # Overall
    overall_bands = [
        b["overall_band"] for b in band_history
        if b.get("overall_band") is not None
    ]
    overall_dir = _trend_direction_bands(overall_bands)
    overall_traj: Dict[str, Any] = {
        "direction":   overall_dir,
        "recent_band": overall_bands[-1] if overall_bands else None,
        "first_band":  overall_bands[0]  if overall_bands else None,
        "delta":       round(overall_bands[-1] - overall_bands[0], 1)
                       if len(overall_bands) >= 2 else None,
    }

    # Per-rubric
    per_rubric: Dict[str, Dict[str, Any]] = {}
    for r in ALL_RUBRICS:
        rb = [
            b["per_rubric_bands"].get(r)
            for b in band_history
            if isinstance(b.get("per_rubric_bands"), dict)
            and b["per_rubric_bands"].get(r) is not None
        ]
        if len(rb) < TREND_MIN_SESSIONS:
            per_rubric[r] = {
                "direction":   "insufficient_data",
                "recent_band": rb[-1] if rb else None,
                "delta":       None,
            }
        else:
            per_rubric[r] = {
                "direction":   _trend_direction_bands(rb),
                "recent_band": rb[-1],
                "delta":       round(rb[-1] - rb[0], 1),
            }

    return {"overall": overall_traj, "per_rubric": per_rubric}


def _build_semantic_health_trajectory(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Trend over mean_recoverability; chronic concern flag.
    """
    history = record.get("semantic_health_history") or []
    recs = [
        h["mean_recoverability"] for h in history
        if h.get("mean_recoverability") is not None
    ]

    if len(recs) < TREND_MIN_SESSIONS:
        trend = "insufficient_data"
    else:
        trend = _trend_direction_bands(recs)   # higher recoverability = better

    chronic = (
        len(recs) >= RECOVERABILITY_SESSIONS_MIN
        and all(v < RECOVERABILITY_CONCERN_THRESHOLD for v in recs[-RECOVERABILITY_SESSIONS_MIN:])
    )

    note: Optional[str] = None
    if chronic:
        note = (
            f"Mean recoverability has been below {RECOVERABILITY_CONCERN_THRESHOLD} "
            f"for the last {RECOVERABILITY_SESSIONS_MIN} sessions. "
            "Semantic clarity should be prioritised before higher-level discourse targets."
        )

    return {
        "recoverability_trend":  trend,
        "recent_recoverability": recs[-1] if recs else None,
        "chronic_concern":       chronic,
        "note":                  note,
    }


def _build_task_type_profile(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Per-task-type rubric pressure summary.
    Only included for task types with >= TASK_TYPE_MIN_SESSIONS sessions.
    """
    tth    = record.get("task_type_pressure_history") or {}
    acc    = record.get("accumulated_profile") or {}
    out: Dict[str, Any] = {}

    for tt in TRACKED_TASK_TYPES:
        tt_entry = tth.get(tt, {})
        tt_rp    = tt_entry.get("rubric_pressure", {})

        # Count sessions for this task type (non-zero entries in any rubric history)
        session_count = max(
            len(tt_rp.get(r, [])) for r in ALL_RUBRICS
        ) if tt_rp else 0

        if session_count < TASK_TYPE_MIN_SESSIONS:
            out[tt] = {"session_count": session_count, "primary_rubric": None,
                       "rubric_pressure": {r: 0.0 for r in ALL_RUBRICS}}
            continue

        # Compute weighted pressure per rubric for this task type
        tt_rwp = {}
        for r in ALL_RUBRICS:
            tt_rwp[r] = _recompute_weighted_pressure(tt_rp.get(r, []))

        primary_r = max(tt_rwp, key=lambda r: tt_rwp[r]) if any(tt_rwp.values()) else None

        out[tt] = {
            "session_count":   session_count,
            "primary_rubric":  primary_r if (primary_r and tt_rwp.get(primary_r, 0) > 0) else None,
            "rubric_pressure": {r: round(tt_rwp[r], 4) for r in ALL_RUBRICS},
        }

    return out


def _detect_plateau(
    history:   List[Dict[str, Any]],
    wp:        float,
    appearance_rate: float,
) -> bool:
    """
    Plateau = pressure persistently high but OLS slope is near-zero.
    Requires: appearance_rate >= APPEARANCE_RATE_THRESHOLD,
              wp >= PLATEAU_MIN_PRESSURE,
              at least PLATEAU_MIN_SESSIONS non-zero pressure values,
              |slope| <= PLATEAU_SLOPE_MAX.
    """
    if appearance_rate < APPEARANCE_RATE_THRESHOLD:
        return False
    if wp < PLATEAU_MIN_PRESSURE:
        return False
    nonzero = [e["pressure"] for e in history if e["pressure"] > 0]
    if len(nonzero) < PLATEAU_MIN_SESSIONS:
        return False
    recent = nonzero[-PLATEAU_MIN_SESSIONS:]
    n  = len(recent)
    xs = list(range(n))
    xm = sum(xs) / n
    ym = sum(recent) / n
    num = sum((xs[i] - xm) * (recent[i] - ym) for i in range(n))
    den = sum((xs[i] - xm) ** 2 for i in range(n))
    if den == 0:
        return True   # perfectly flat = plateau
    slope = num / den
    return abs(slope) <= PLATEAU_SLOPE_MAX


def _detect_limiter_shift(
    rwp:         Dict[str, float],
    n_sessions:  int,
) -> Dict[str, Any]:
    """
    Check if any secondary rubric is converging on the primary (>= LIMITER_SHIFT_CONVERGENCE_RATIO).
    """
    empty: Dict[str, Any] = {
        "active":            False,
        "primary_rubric":    None,
        "converging_rubric": None,
        "convergence_ratio": None,
        "note":              None,
    }
    if n_sessions < LIMITER_SHIFT_MIN_SESSIONS:
        return empty
    pressures = [(r, v) for r, v in rwp.items() if v > 0]
    if len(pressures) < 2:
        return empty
    pressures.sort(key=lambda x: -x[1])
    primary_r, primary_p = pressures[0]
    if primary_p <= 0:
        return empty
    for rubric, pressure in pressures[1:]:
        ratio = pressure / primary_p
        if ratio >= LIMITER_SHIFT_CONVERGENCE_RATIO:
            return {
                "active":            True,
                "primary_rubric":    primary_r,
                "converging_rubric": rubric,
                "convergence_ratio": round(ratio, 3),
                "note": (
                    f"{rubric} pressure is {ratio:.0%} of {primary_r} pressure "
                    f"over {n_sessions} sessions. "
                    f"If {primary_r} is addressed, {rubric} may become the binding limiter."
                ),
            }
    return empty


def _top_families_for_rubric(
    rubric:     str,
    fwp:        Dict[str, float],
    family_map: Dict[str, str],   # family → rubric
    n:          int = 3,
) -> List[str]:
    """Top N families by weighted pressure that belong to this rubric."""
    rubric_fams = [
        (fam, p) for fam, p in fwp.items()
        if family_map.get(fam) == rubric and p > 0
    ]
    rubric_fams.sort(key=lambda x: -x[1])
    return [f for f, _ in rubric_fams[:n]]


def _top_skills_for_rubric(
    rubric:     str,
    swp:        Dict[str, float],
    skill_rubric_map: Dict[str, str],   # skill → rubric
    n:          int = 2,
) -> List[str]:
    """Top N skills by weighted pressure for this rubric."""
    rub_skills = [
        (skill, p) for skill, p in swp.items()
        if skill_rubric_map.get(skill) == rubric and p > 0
    ]
    rub_skills.sort(key=lambda x: -x[1])
    return [s for s, _ in rub_skills[:n]]


# Fallback: derive rubric from skill name using known mappings
_SKILL_TO_RUBRIC: Dict[str, str] = {
    "GRAMMAR_CONTROL":          "GRA",
    "SENTENCE_CONSTRUCTION":    "GRA",
    "LEXICAL_CONTROL":          "LR",
    "LEXICAL_PRECISION":        "LR",
    "COLLOCATION_CONTROL":      "LR",
    "LEXICAL_FORM_CONTROL":     "LR",
    "REGISTER_CONTROL":         "LR",
    "SEMANTIC_PHRASE_CONTROL":  "LR",
    "TASK_FULFILMENT":          "TR",
    "POSITION_CONTROL":         "TR",
    "IDEA_DEVELOPMENT":         "TR",
    "SUPPORT_DEVELOPMENT":      "TR",
    "REASONING_CHAIN_CONTROL":  "TR",
    "EXAMPLE_USAGE":            "TR",
    "COHERENCE_CONTROL":        "CC",
    "COHESIVE_DEVICE_CONTROL":  "CC",
    "MEANING_RECOVERABILITY":   "META",
    "SEMANTIC_EVALUABILITY":    "META",
    "DISCOURSE_EVALUABILITY":   "META",
}

# Fallback: derive rubric from family name
_FAMILY_TO_RUBRIC: Dict[str, str] = {
    "ARTICLE_DETERMINER":         "GRA",
    "NOUN_NUMBER_COUNTABILITY":   "GRA",
    "SUBJECT_VERB_AGREEMENT":     "GRA",
    "VERB_FORM":                  "GRA",
    "VERB_TENSE":                 "GRA",
    "PREPOSITION_PATTERN":        "GRA",
    "COMPARATIVE_FORM":           "GRA",
    "GRAMMAR_PUNCTUATION":        "GRA",
    "CLAUSE_STRUCTURE":           "GRA",
    "VERB_PATTERN":               "GRA",
    "CONSTRUCTION":               "GRA",
    "CONDITIONAL_STRUCTURE":      "GRA",
    "SPELLING":                   "LR",
    "WORD_FORM":                  "LR",
    "COLLOCATION":                "LR",
    "WORD_CHOICE":                "LR",
    "LEXICAL_PRECISION":          "LR",
    "SEMANTIC_COMBINATION":       "LR",
    "REGISTER":                   "LR",
    "REPETITION":                 "LR",
    "PROMPT_COVERAGE":            "TR",
    "PROMPT_RELEVANCE":           "TR",
    "TASK_COMPLETENESS":          "TR",
    "POSITION_CLARITY":           "TR",
    "UNSUPPORTED_CLAIM":          "TR",
    "WEAK_EXAMPLE":               "TR",
    "REASONING_CHAIN":            "TR",
    "INCOMPLETE_ARGUMENT":        "TR",
    "LOGICAL_PROGRESSION":        "CC",
    "TOPIC_SHIFT":                "CC",
    "REFERENCE_BREAK":            "CC",
    "TRANSITION":                 "CC",
    "MISSING_TRANSITION":         "CC",
    "PARAGRAPH_STRUCTURE":        "CC",
}


# ─────────────────────────────────────────────────────────────────────────────
# Main profile builder
# ─────────────────────────────────────────────────────────────────────────────

def build_student_profile_premium(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pure function — reads student_record, returns li_profile_premium.
    Can be called without a new accumulation (e.g. on dashboard load).
    Mastery discounts are applied here at read time, not stored in weighted pressures.
    """
    now        = _now_iso()
    student_id = record.get("student_id", "unknown")
    n_sessions = record.get("session_count", 0)
    acc        = record.get("accumulated_profile") or {}

    # Raw weighted pressures (pre-discount)
    rwp_raw  = dict(acc.get("rubric_weighted_pressure") or {r: 0.0 for r in ALL_RUBRICS})
    swp_raw  = dict(acc.get("skill_weighted_pressure") or {})
    fwp      = dict(acc.get("family_weighted_pressure") or {})
    app_r    = acc.get("rubric_appearance_counts") or {r: 0 for r in ALL_RUBRICS}
    app_s    = acc.get("skill_appearance_counts") or {}
    rph      = record.get("rubric_pressure_history") or {}
    sph      = record.get("skill_pressure_history") or {}
    ms       = record.get("mastery_state") or {}

    # Apply mastery discounts to family-linked skill/rubric pressures
    # We discount family weighted pressure, and propagate that down to rwp/swp
    # Discount is applied per family; if family maps to a rubric, that rubric's
    # effective pressure is reduced proportionally.
    fwp_discounted = {}
    for fam, fp in fwp.items():
        state    = ms.get(fam, {})
        discount = 0.0 if state.get("stale") else _safe_float(state.get("discount"), 0.0)
        fwp_discounted[fam] = round(fp * (1.0 - discount), 4)

    # Effective skill weighted pressure = apply family discounts proportionally
    # (simplified: discount skill directly if its mastery_state has a matching family entry)
    swp_effective: Dict[str, float] = {}
    for skill, sp in swp_raw.items():
        # Find any mastery discount for families that map to this skill's rubric
        rubric   = _SKILL_TO_RUBRIC.get(skill, "UNKNOWN")
        # Take max discount from families belonging to this rubric
        max_disc = max(
            (
                0.0 if ms.get(fam, {}).get("stale") else _safe_float(ms.get(fam, {}).get("discount"), 0.0)
                for fam, r in _FAMILY_TO_RUBRIC.items()
                if r == rubric and fam in ms
            ),
            default=0.0,
        )
        swp_effective[skill] = round(sp * (1.0 - max_disc), 4)

    # Effective rubric weighted pressure
    rwp_effective: Dict[str, float] = {}
    for r in ALL_RUBRICS:
        rp = rwp_raw.get(r, 0.0)
        max_disc = max(
            (
                0.0 if ms.get(fam, {}).get("stale") else _safe_float(ms.get(fam, {}).get("discount"), 0.0)
                for fam, rubric in _FAMILY_TO_RUBRIC.items()
                if rubric == r and fam in ms
            ),
            default=0.0,
        )
        rwp_effective[r] = round(rp * (1.0 - max_disc), 4)

    # Data confidence
    if n_sessions >= DATA_CONFIDENCE_HIGH:
        confidence = "high"
    elif n_sessions >= DATA_CONFIDENCE_MEDIUM:
        confidence = "medium"
    else:
        confidence = "low"

    # ── Persistent rubric limiters ──
    persistent: List[Dict[str, Any]] = []
    for r in ALL_RUBRICS:
        wp    = rwp_effective.get(r, 0.0)
        count = app_r.get(r, 0)
        rate  = count / n_sessions if n_sessions > 0 else 0.0
        if wp >= PRESSURE_PERSISTENCE_THRESHOLD or (rate >= APPEARANCE_RATE_THRESHOLD and n_sessions >= 2):
            pressures  = [e["pressure"] for e in (rph.get(r) or [])]
            trend      = _trend_direction(pressures)
            plateau    = _detect_plateau(rph.get(r) or [], wp, rate)
            dom_fams   = _top_families_for_rubric(r, fwp_discounted, _FAMILY_TO_RUBRIC)
            dom_skills = _top_skills_for_rubric(r, swp_effective, _SKILL_TO_RUBRIC)
            persistent.append({
                "rubric":              r,
                "weighted_pressure":   round(wp, 4),
                "appearance_rate":     round(rate, 3),
                "trend":               trend,
                "dominant_families":   dom_fams,
                "dominant_skills":     dom_skills,
                "plateau_detected":    plateau,
                "plateau_note":        (
                    f"{r} pressure has remained high for {PLATEAU_MIN_SESSIONS}+ sessions "
                    "with no improvement trend. Consider changing practice approach."
                ) if plateau else None,
            })
    persistent.sort(key=lambda x: -x["weighted_pressure"])

    # ── Persistent skill limiters ──
    persistent_skills: List[Dict[str, Any]] = []
    for skill, wp in swp_effective.items():
        rubric = _SKILL_TO_RUBRIC.get(skill, "UNKNOWN")
        if rubric == "META":
            continue   # META skills not surfaced as persistent skill limiters
        count = app_s.get(skill, 0)
        rate  = count / n_sessions if n_sessions > 0 else 0.0
        if wp >= SKILL_PERSISTENCE_THRESHOLD or (rate >= APPEARANCE_RATE_THRESHOLD and n_sessions >= 2):
            pressures    = [e["pressure"] for e in (sph.get(skill) or [])]
            trend        = _trend_direction(pressures)
            plateau      = _detect_plateau(sph.get(skill) or [], wp, rate)
            dom_fams     = [
                fam for fam, r in _FAMILY_TO_RUBRIC.items()
                if r == rubric and fwp_discounted.get(fam, 0) > 0
            ]
            dom_fams.sort(key=lambda f: -fwp_discounted.get(f, 0))
            # Mastery discount info
            max_disc = max(
                (
                    0.0 if ms.get(fam, {}).get("stale") else _safe_float(ms.get(fam, {}).get("discount"), 0.0)
                    for fam in dom_fams if fam in ms
                ),
                default=0.0,
            )
            persistent_skills.append({
                "skill":                            skill,
                "rubric":                           rubric,
                "weighted_pressure":                round(swp_raw.get(skill, 0.0), 4),
                "dep_adjusted_weighted_pressure":   round(wp, 4),
                "appearance_rate":                  round(rate, 3),
                "trend":                            trend,
                "dominant_families":                dom_fams[:3],
                "mastery_discount_applied":         round(max_disc, 4),
                "plateau_detected":                 plateau,
            })
    persistent_skills.sort(key=lambda x: -x["dep_adjusted_weighted_pressure"])

    # ── Resolved rubric limiters ──
    resolved: List[Dict[str, Any]] = []
    for r in ALL_RUBRICS:
        wp    = rwp_effective.get(r, 0.0)
        count = app_r.get(r, 0)
        if count >= 2 and wp < RESOLVED_PRESSURE_THRESHOLD:
            pressures = [e["pressure"] for e in (rph.get(r) or []) if e["pressure"] > 0]
            resolved.append({
                "rubric":            r,
                "last_pressure":     round(pressures[-1] if pressures else 0.0, 4),
                "sessions_resolved": n_sessions - count,
            })

    # ── Resolved skill limiters ──
    resolved_skills: List[Dict[str, Any]] = []
    for skill, wp in swp_effective.items():
        rubric = _SKILL_TO_RUBRIC.get(skill, "UNKNOWN")
        if rubric == "META":
            continue
        count = app_s.get(skill, 0)
        if count >= 2 and wp < SKILL_RESOLVED_THRESHOLD:
            pressures = [e["pressure"] for e in (sph.get(skill) or []) if e["pressure"] > 0]
            resolved_skills.append({
                "skill":             skill,
                "rubric":            rubric,
                "last_pressure":     round(pressures[-1] if pressures else 0.0, 4),
                "sessions_resolved": n_sessions - count,
            })

    # ── Limiter shift alert ──
    limiter_shift = _detect_limiter_shift(rwp_effective, n_sessions)

    # ── Band trajectory ──
    band_trajectory = _build_band_trajectory(record)

    # ── Semantic health trajectory ──
    sem_trajectory = _build_semantic_health_trajectory(record)

    # ── Task-type profile ──
    tt_profile = _build_task_type_profile(record)

    # ── Recommended focus ──
    recommended = _build_recommended_focus(
        persistent, limiter_shift, rwp_effective, swp_effective, fwp_discounted,
        record.get("target_history") or [], n_sessions,
    )

    # ── Evidence of improvement ──
    improvements: List[Dict[str, Any]] = []
    # Resolved rubrics
    for lim in resolved:
        improvements.append({
            "rubric": lim["rubric"],
            "skill":  None,
            "note":   f"{lim['rubric']} pressure is now below the resolved threshold — good progress.",
        })
    # Improving trend in persistent limiters
    for lim in persistent:
        if lim["trend"] == "improving":
            improvements.append({
                "rubric": lim["rubric"],
                "skill":  None,
                "note":   f"{lim['rubric']} pressure is on a downward trend across recent sessions.",
            })
    # Resolved skills
    for lim in resolved_skills:
        improvements.append({
            "rubric": lim["rubric"],
            "skill":  lim["skill"],
            "note":   f"{lim['skill']} pressure is now low — this area appears under control.",
        })

    # ── Mastery acknowledgements ──
    mastery_acks: List[Dict[str, Any]] = []
    for fam, state in ms.items():
        if state.get("stale"):
            continue
        mastery = _safe_float(state.get("mastery"), 0.0)
        if mastery >= 0.70:
            mastery_acks.append({
                "family":  fam,
                "skill":   state.get("skill"),
                "mastery": round(mastery, 3),
                "note":    f"Practice data shows strong mastery of {fam} "
                           f"({mastery:.0%}). Pressure in this area is discounted.",
            })

    return {
        "schema_version":           STUDENT_PROFILE_SCHEMA,
        "li_engine_version":        LI_ENGINE_VERSION,
        "student_id":               student_id,
        "generated_at":             now,
        "session_count":            n_sessions,
        "data_confidence":          confidence,
        "persistent_limiters":      persistent,
        "persistent_skill_limiters": persistent_skills,
        "resolved_limiters":        resolved,
        "resolved_skill_limiters":  resolved_skills,
        "limiter_shift_alert":      limiter_shift,
        "band_trajectory":          band_trajectory,
        "semantic_health_trajectory": sem_trajectory,
        "task_type_profile":        tt_profile,
        "recommended_focus":        recommended,
        "evidence_of_improvement":  improvements,
        "mastery_acknowledgements": mastery_acks,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Recommended focus builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_recommended_focus(
    persistent:      List[Dict[str, Any]],
    limiter_shift:   Dict[str, Any],
    rwp:             Dict[str, float],
    swp:             Dict[str, float],
    fwp:             Dict[str, float],
    target_history:  List[Dict[str, Any]],
    n_sessions:      int,
) -> List[Dict[str, Any]]:
    """
    Priority order:
      1. Worsening trend + persistent
      2. Plateau detected
      3. Persistent stable limiter
      4. Limiter shift alert (converging secondary)
      5. Highest recent pressure if no persistent limiters yet
    """
    recommended: List[Dict[str, Any]] = []
    seen: set = set()

    # Most recent session's targets per rubric (for recommended_targets field)
    recent_targets_by_rubric: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    if target_history:
        last_session_targets = target_history[-1].get("targets") or []
        for t in last_session_targets:
            # Targets don't carry rubric directly; we can't map easily here — include all
            for r in ALL_RUBRICS:
                recent_targets_by_rubric[r].append(t)

    def _add(rubric: str, rationale: str) -> None:
        if rubric in seen or len(recommended) >= MAX_RECOMMENDED_FOCUS:
            return
        lim   = next((x for x in persistent if x["rubric"] == rubric), None)
        fams  = lim["dominant_families"] if lim else _top_families_for_rubric(rubric, fwp, _FAMILY_TO_RUBRIC)
        skills = lim["dominant_skills"] if lim else _top_skills_for_rubric(rubric, swp, _SKILL_TO_RUBRIC)
        rec_targets = recent_targets_by_rubric.get(rubric, [])[:2]
        recommended.append({
            "rubric":              rubric,
            "dominant_skills":     skills,
            "dominant_families":   fams,
            "rationale":           rationale,
            "recommended_targets": rec_targets,
        })
        seen.add(rubric)

    # Priority 1: worsening
    for lim in persistent:
        if lim["trend"] == "worsening":
            _add(lim["rubric"], f"Persistent across sessions with a worsening trend.")

    # Priority 2: plateau
    for lim in persistent:
        if lim.get("plateau_detected"):
            count_str = f"{int(round(lim['appearance_rate'] * n_sessions))} of {n_sessions} sessions"
            _add(lim["rubric"], f"Persistent limiter ({count_str}) — plateau detected. Try a different practice approach.")

    # Priority 3: stable persistent
    for lim in persistent:
        count_str = f"{int(round(lim['appearance_rate'] * n_sessions))} of {n_sessions} sessions"
        _add(lim["rubric"], f"Persistent across {count_str}.")

    # Priority 4: limiter shift (converging secondary)
    if limiter_shift.get("active"):
        conv_r = limiter_shift.get("converging_rubric")
        if conv_r:
            ratio = limiter_shift.get("convergence_ratio", 0)
            _add(conv_r, f"Rising pressure ({ratio:.0%} of primary). May become the binding limiter.")

    # Priority 5: fallback — highest recent pressure
    if not recommended and n_sessions > 0:
        top_r = max(ALL_RUBRICS, key=lambda r: rwp.get(r, 0.0))
        if rwp.get(top_r, 0.0) > 0:
            _add(top_r, "Highest pressure in most recent session.")

    return recommended


# ─────────────────────────────────────────────────────────────────────────────
# Downstream output adapters
# ─────────────────────────────────────────────────────────────────────────────

def to_feedback_generator_input(profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    Slim contract passed to Feedback Generator before the next essay session.
    FG uses this to contextualise feedback (persistent vs new, plateau, improvements).
    """
    return {
        "li_version":              profile.get("li_engine_version"),
        "student_id":              profile.get("student_id"),
        "session_count":           profile.get("session_count"),
        "data_confidence":         profile.get("data_confidence"),
        "persistent_limiters":     profile.get("persistent_limiters"),
        "resolved_limiters":       profile.get("resolved_limiters"),
        "band_trajectory":         profile.get("band_trajectory"),
        "limiter_shift_alert":     profile.get("limiter_shift_alert"),
        "semantic_health_trajectory": profile.get("semantic_health_trajectory"),
        "evidence_of_improvement": profile.get("evidence_of_improvement"),
        "recommended_focus":       profile.get("recommended_focus"),
    }


def to_practice_engine_input(profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    Contract passed to Practice Engine after each session.
    PE uses this to select exercises, adjust difficulty, and avoid already-mastered families.
    """
    focus_rubrics: List[str] = []
    focus_skills:  List[str] = []
    focus_fams:    List[str] = []
    seen_fam: set = set()
    seen_skill: set = set()

    for f in (profile.get("recommended_focus") or []):
        r = f.get("rubric")
        if r and r not in focus_rubrics:
            focus_rubrics.append(r)
        for s in (f.get("dominant_skills") or []):
            if s not in seen_skill:
                focus_skills.append(s)
                seen_skill.add(s)
        for fam in (f.get("dominant_families") or []):
            if fam not in seen_fam:
                focus_fams.append(fam)
                seen_fam.add(fam)

    plateau_rubrics = [
        lim["rubric"] for lim in (profile.get("persistent_limiters") or [])
        if lim.get("plateau_detected")
    ]

    # Mastery state summary (non-stale only)
    # Loaded from profile's acknowledgements to avoid re-reading record
    mastery_summary: Dict[str, Any] = {}
    for ack in (profile.get("mastery_acknowledgements") or []):
        fam = ack.get("family")
        if fam:
            mastery_summary[fam] = {
                "mastery":  ack.get("mastery"),
                "skill":    ack.get("skill"),
            }

    return {
        "student_id":      profile.get("student_id"),
        "data_confidence": profile.get("data_confidence"),
        "focus_rubrics":   focus_rubrics,
        "focus_skills":    focus_skills[:6],
        "focus_families":  focus_fams[:6],
        "band_trajectory": profile.get("band_trajectory", {}).get("overall"),
        "plateau_rubrics": plateau_rubrics,
        "mastery_state":   mastery_summary,
        "semantic_health": profile.get("semantic_health_trajectory"),
    }


def to_progress_tracker_payload(
    profile: Dict[str, Any],
    record:  Dict[str, Any],
) -> Dict[str, Any]:
    """
    Read-only payload for Progress Tracker (dashboard layer).
    PT does NOT write back to LIE.
    """
    band_history = record.get("band_history") or []
    overall_hist = [
        {"submitted_at": b["submitted_at"], "overall_band": b["overall_band"]}
        for b in band_history
        if b.get("overall_band") is not None
    ]
    per_rubric_hist: Dict[str, List[Dict[str, Any]]] = {r: [] for r in ALL_RUBRICS}
    for b in band_history:
        prb = b.get("per_rubric_bands") or {}
        for r in ALL_RUBRICS:
            if prb.get(r) is not None:
                per_rubric_hist[r].append({
                    "submitted_at": b["submitted_at"],
                    "band":         prb[r],
                })

    return {
        "student_id":              profile.get("student_id"),
        "updated_at":              profile.get("generated_at"),
        "session_count":           profile.get("session_count"),
        "data_confidence":         profile.get("data_confidence"),
        "overall_band_history":    overall_hist,
        "per_rubric_band_history": per_rubric_hist,
        "persistent_limiters":     profile.get("persistent_limiters"),
        "resolved_limiters":       profile.get("resolved_limiters"),
        "band_trajectory":         profile.get("band_trajectory"),
        "semantic_health_trajectory": profile.get("semantic_health_trajectory"),
        "evidence_of_improvement": profile.get("evidence_of_improvement"),
        "recommended_focus":       profile.get("recommended_focus"),
        "task_type_profile":       profile.get("task_type_profile"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_premium_profile(profile: Dict[str, Any]) -> List[str]:
    """Returns list of violations (empty = valid)."""
    violations: List[str] = []

    if profile.get("schema_version") != STUDENT_PROFILE_SCHEMA:
        violations.append(f"schema_version wrong: {profile.get('schema_version')!r}")
    if not profile.get("student_id"):
        violations.append("student_id missing")
    if profile.get("data_confidence") not in ("high", "medium", "low"):
        violations.append(f"data_confidence invalid: {profile.get('data_confidence')!r}")

    for lim in (profile.get("persistent_limiters") or []):
        if lim.get("rubric") not in ALL_RUBRICS:
            violations.append(f"persistent_limiters.rubric invalid: {lim.get('rubric')!r}")
        if lim.get("trend") not in ("improving", "stable", "worsening"):
            violations.append(f"persistent_limiters.trend invalid: {lim.get('trend')!r}")

    traj = (profile.get("band_trajectory") or {}).get("overall") or {}
    if traj.get("direction") not in ("improving", "stable", "worsening", None):
        violations.append(f"band_trajectory.overall.direction invalid: {traj.get('direction')!r}")

    if len(profile.get("recommended_focus") or []) > MAX_RECOMMENDED_FOCUS:
        violations.append(f"recommended_focus exceeds max {MAX_RECOMMENDED_FOCUS}")

    shift = profile.get("limiter_shift_alert") or {}
    if shift.get("active") and shift.get("convergence_ratio") is not None:
        ratio = shift["convergence_ratio"]
        if not (0.0 <= ratio <= 1.0):
            violations.append(f"limiter_shift_alert.convergence_ratio out of range: {ratio}")

    sem = profile.get("semantic_health_trajectory") or {}
    valid_trends = ("improving", "stable", "worsening", "insufficient_data")
    if sem.get("recoverability_trend") not in valid_trends:
        violations.append(f"semantic_health_trajectory.recoverability_trend invalid: {sem.get('recoverability_trend')!r}")

    return violations


# ─────────────────────────────────────────────────────────────────────────────
# Migration: Light v1.1 → Premium v1.0
# ─────────────────────────────────────────────────────────────────────────────

def migrate_from_light_record(
    light_record: Dict[str, Any],
    storage_path: str,
) -> Dict[str, Any]:
    """
    Migrate a LI_STUDENT_RECORD_V1.1 (Light) record to Premium format.

    Copies:
      - band_history (without per_rubric_bands — set to null)
      - rubric_pressure_history
      - family_pressure_history
      - accumulated_profile.rubric_weighted_pressure
      - accumulated_profile.family_weighted_pressure
      - accumulated_profile.appearance_counts → rubric_appearance_counts

    Premium-only fields are initialised empty.
    Stamps migrated_from in the record.
    """
    now = _now_iso()
    student_id = light_record.get("student_id", "unknown")

    premium = _empty_student_record(student_id, now)
    premium["created_at"]     = light_record.get("created_at", now)
    premium["updated_at"]     = now
    premium["session_count"]  = light_record.get("session_count", 0)
    premium["submitted_count"]= light_record.get("submitted_count", 0)

    # Band history — add empty per_rubric_bands
    for b in (light_record.get("band_history") or []):
        premium["band_history"].append({
            "essay_id":         b.get("essay_id", "unknown"),
            "submitted_at":     b.get("submitted_at", now),
            "overall_band":     b.get("overall_band"),
            "task_type":        None,
            "per_rubric_bands": {r: None for r in ALL_RUBRICS},
        })

    # Rubric pressure history
    for r in ALL_RUBRICS:
        premium["rubric_pressure_history"][r] = list(
            light_record.get("rubric_pressure_history", {}).get(r, [])
        )

    # Family pressure history
    premium["family_pressure_history"] = dict(
        light_record.get("family_pressure_history") or {}
    )

    # Accumulated profile
    light_acc = light_record.get("accumulated_profile") or {}
    premium["accumulated_profile"]["rubric_weighted_pressure"] = dict(
        light_acc.get("rubric_weighted_pressure") or {r: 0.0 for r in ALL_RUBRICS}
    )
    premium["accumulated_profile"]["family_weighted_pressure"] = dict(
        light_acc.get("family_weighted_pressure") or {}
    )
    # Light uses "appearance_counts"; Premium uses "rubric_appearance_counts"
    premium["accumulated_profile"]["rubric_appearance_counts"] = dict(
        light_acc.get("appearance_counts") or {r: 0 for r in ALL_RUBRICS}
    )

    premium["migrated_from"] = {
        "schema":    light_record.get("schema_version", LIGHT_RECORD_SCHEMA),
        "migrated_at": now,
    }
    premium["debug_events"].append({
        "event":   "migrated_from_light",
        "at":      now,
        "note":    "skill_pressure_history and per_rubric_bands will populate from next session onward.",
    })

    save_student_record(premium, storage_path)
    return premium


# ─────────────────────────────────────────────────────────────────────────────
# Example input
# ─────────────────────────────────────────────────────────────────────────────

EXAMPLE_LI_INPUT: Dict[str, Any] = {
    "student_id":   "student_001",
    "essay_id":     "essay_007",
    "submitted_at": "2026-06-14T10:00:00Z",

    "fg_downstream": {
        "primary_rubric":            "GRA",
        "primary_pressure":          10.235,
        "dominant_families":         ["VERB_FORM", "ARTICLE_DETERMINER", "CLAUSE_STRUCTURE"],
        "eci":                       21.99,
        "eci_tier":                  "high",
        "training_target_count":     4,
        "has_student_safe_evidence": True,
        "word_count":                270,
        "overall_band":              5.0,
        "task_type":                 "causes_effects",
    },

    "pe_li_payload": {
        "li_contract_version":   "LI_INGESTION_V1.1",
        "source_engine":         "priority_engine_v4.4.0",
        "essay_id":              "essay_007",
        "student_id":            None,
        "top_skills_by_pressure": [
            {"skill": "GRAMMAR_CONTROL",       "rubric": "GRA", "pressure": 8.1,  "rank": 1},
            {"skill": "SENTENCE_CONSTRUCTION", "rubric": "GRA", "pressure": 4.3,  "rank": 2},
            {"skill": "LEXICAL_CONTROL",       "rubric": "LR",  "pressure": 2.1,  "rank": 3},
        ],
        "recommended_targets": [
            {"target_id": "VERB_FORM_PATTERN_CONTROL", "learning_target": "Verb form and pattern control",
             "pressure": 6.4, "roi": "high", "rank": 1},
            {"target_id": "ARTICLE_NOUN_CONTROL",      "learning_target": "Article + noun-number control",
             "pressure": 4.2, "roi": "medium", "rank": 2},
        ],
        "confirmed_strengths": [
            {"id": "BASIC_STRUCTURE_PRESENT", "skill": "COHERENCE_CONTROL", "confidence": "high"}
        ],
        "overall_band_estimate": 5.0,
        "semantic_health": {
            "mean_recoverability":      0.81,
            "affected_discourse_ratio": 0.12,
        },
    },

    "practice_mastery_signals": [
        {
            "family":         "SPELLING",
            "skill":          "LEXICAL_FORM_CONTROL",
            "mastery":        0.87,
            "exercise_count": 24,
            "last_practiced": "2026-06-13T18:00:00Z",
        }
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    """
    Usage:
      # Accumulate one session:
      python li_engine_premium_v1_0.py -i input.json --storage ./li_premium_storage

      # Read current profile without accumulating:
      python li_engine_premium_v1_0.py -i input.json --storage ./li_premium_storage --profile-only

      # Migrate a Light v1.1 record:
      python li_engine_premium_v1_0.py --migrate-light li_student_s001.json --storage ./li_premium_storage

      # Run built-in example:
      python li_engine_premium_v1_0.py --example --storage ./li_premium_storage
    """
    import sys

    p = argparse.ArgumentParser(description="LI Engine Premium v1.0")
    p.add_argument("--input",         "-i",  help="LI input JSON file (student_id, essay_id, fg_downstream, ...)")
    p.add_argument("--storage",       "-s",  default="./li_premium_storage",
                   help="Student record storage directory")
    p.add_argument("--profile-only",         action="store_true",
                   help="Print current profile without accumulating a new session")
    p.add_argument("--migrate-light",        metavar="FILE",
                   help="Migrate a Light v1.1 student record JSON to Premium format")
    p.add_argument("--example",              action="store_true",
                   help="Run the built-in example input")
    p.add_argument("--fg-output",            action="store_true",
                   help="Also print to_feedback_generator_input() contract")
    p.add_argument("--pe-output",            action="store_true",
                   help="Also print to_practice_engine_input() contract")
    p.add_argument("--pt-output",            action="store_true",
                   help="Also print to_progress_tracker_payload() contract")
    args = p.parse_args(argv)

    # ── Migration mode ──
    if args.migrate_light:
        try:
            with open(args.migrate_light, encoding="utf-8") as f:
                light = json.load(f)
        except Exception as e:
            print(f"[LI Premium] Error reading Light record: {e}", file=sys.stderr)
            return 1
        premium = migrate_from_light_record(light, args.storage)
        print(f"[LI Premium] Migrated student {premium['student_id']} "
              f"({premium['session_count']} sessions) → {_record_path(premium['student_id'], args.storage)}")
        return 0

    # ── Load input ──
    if args.example:
        li_input = EXAMPLE_LI_INPUT
    elif args.input:
        try:
            with open(args.input, encoding="utf-8") as f:
                li_input = json.load(f)
        except Exception as e:
            print(f"[LI Premium] Error reading input: {e}", file=sys.stderr)
            return 1
    else:
        p.print_help()
        return 1

    # ── Validate ──
    valid, err = validate_li_input(li_input)
    if not valid:
        print(f"[LI Premium] Input validation error: {err}", file=sys.stderr)
        return 1

    student_id = str(li_input["student_id"])

    # ── Profile-only mode ──
    if args.profile_only:
        record = load_student_record(student_id, args.storage) \
                 or _empty_student_record(student_id, _now_iso())
        profile = build_student_profile_premium(record)
    else:
        record, profile = accumulate(li_input, args.storage)
        print(f"[LI Premium] Session {record.get('session_count')} accumulated "
              f"for student {student_id}")

    # ── Validate output ──
    violations = validate_premium_profile(profile)
    if violations:
        print(f"[LI Premium] Profile violations: {violations}", file=sys.stderr)

    # ── Print outputs ──
    print(json.dumps(profile, ensure_ascii=False, indent=2))

    if args.fg_output:
        print("\n── FG contract ──")
        print(json.dumps(to_feedback_generator_input(profile), ensure_ascii=False, indent=2))

    if args.pe_output:
        print("\n── Practice Engine contract ──")
        print(json.dumps(to_practice_engine_input(profile), ensure_ascii=False, indent=2))

    if args.pt_output:
        print("\n── Progress Tracker payload ──")
        print(json.dumps(to_progress_tracker_payload(profile, record), ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
