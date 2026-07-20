"""
fp_filter_v1.py
===============
NEW FILE — no previous version exists.

PURPOSE
-------
False-positive filter that sits between the Detector output and the PE/FE engines.
Removes detector rows that are likely false positives before they corrupt the
Priority Engine's pressure scores and the Feedback Engine's commentary.

WHY THIS IS CRITICAL
---------------------
The detector currently passes ALL detected spans downstream.  False positives:
  • Inflate pressure on the wrong criterion (e.g. CLAUSE_STRUCTURE under LR)
  • Cause PE to nominate the wrong limiter family as the student's primary weakness
  • Force FE to write feedback on errors the essay does not actually contain
  • Corrupt the LIE/learner profile over multiple sessions

FILTER LAYERS (applied in order)
----------------------------------
1. Confidence threshold  — rows below min_confidence are removed.
2. Must-not-catch list   — spans matching known acceptable patterns (from benchmark
                           section_3_must_not_catch or a static registry) are removed.
3. Criterion–family consistency — families that belong to the wrong criterion are
   re-routed or removed.  Example: CLAUSE_STRUCTURE is a GRA family; if the detector
   files it under lexical_resource it is re-mapped to grammatical_range_accuracy.
4. Duplicate span deduplication — exact-duplicate (span, criterion) pairs are collapsed
   to the highest-confidence instance.

USAGE
------
    from fp_filter_v1 import FPFilter

    filt = FPFilter(
        min_confidence=0.50,
        must_not_catch=[
            "this costs a lot of money",
            "even though an ageing population can cause some problems",
        ],
    )
    filtered_rows, audit = filt.filter(detector_rows)
    # filtered_rows: list of detector student_row dicts (same schema)
    # audit: FPAudit object with counts and removal_log

SCHEMA NOTES
------------
Each detector student_row is expected to have at minimum:
  {
    "error_type": str,
    "criterion":  str,           # "lexical_resource" | "grammar" | ...
    "span":       str,           # the flagged text span
    "confidence": float,         # 0-1
    ...
  }
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ── Criterion–family consistency map ─────────────────────────────────────────
# Maps family name → correct v2 criterion.  If a detector row files this
# family under a different criterion, the row is re-mapped (not dropped).

_FAMILY_TO_CRITERION: Dict[str, str] = {
    # GRA families — must NEVER appear under lexical_resource
    "CLAUSE_STRUCTURE":         "grammatical_range_accuracy",
    "SUBJECT_VERB_AGREEMENT":   "grammatical_range_accuracy",
    "TENSE_CONSISTENCY":        "grammatical_range_accuracy",
    "VERB_FORM":                "grammatical_range_accuracy",
    "ARTICLE_DETERMINER":       "grammatical_range_accuracy",
    "NOUN_NUMBER_COUNTABILITY": "grammatical_range_accuracy",
    "COMPARATIVE_FORM":         "grammatical_range_accuracy",
    "PRONOUN_REFERENCE":        "grammatical_range_accuracy",
    "PREPOSITION":              "grammatical_range_accuracy",
    "SENTENCE_STRUCTURE":       "grammatical_range_accuracy",
    "GRAMMAR_CONTROL":          "grammatical_range_accuracy",

    # LR families — must NEVER appear under grammar
    "COLLOCATION":              "lexical_resource",
    "LEXICAL_PRECISION":        "lexical_resource",
    "WORD_FORM":                "lexical_resource",
    "LEXICAL_RANGE":            "lexical_resource",
    "SPELLING":                 "lexical_resource",
    "REGISTER":                 "lexical_resource",
    "LEXICAL_CONTROL":          "lexical_resource",

    # CC families
    "TRANSITION":               "coherence_cohesion",
    "PARAGRAPH_STRUCTURE":      "coherence_cohesion",
    "REFERENCE_CHAIN":          "coherence_cohesion",
    "COHESIVE_DEVICE":          "coherence_cohesion",
    "DISCOURSE_MARKER":         "coherence_cohesion",

    # TA families
    "POSITION_CLARITY":         "task_achievement",
    "TASK_COMPLETENESS":        "task_achievement",
    "IDEA_DEVELOPMENT":         "task_achievement",
    "RELEVANCE":                "task_achievement",
    "TASK_RESPONSE":            "task_achievement",
}

# Detector uses "grammar" shorthand; v2 contracts use full name
_CRITERION_NORMALISE: Dict[str, str] = {
    "grammar":  "grammatical_range_accuracy",
    "GRA":      "grammatical_range_accuracy",
    "TR":       "task_achievement",
    "TA":       "task_achievement",
    "CC":       "coherence_cohesion",
    "LR":       "lexical_resource",
    "task_response": "task_achievement",
}


def _normalise_criterion(raw: str) -> str:
    return _CRITERION_NORMALISE.get(raw, raw)


# ── Removal log entry ─────────────────────────────────────────────────────────

@dataclass
class RemovalRecord:
    span: str
    error_type: str
    criterion: str
    reason: str
    detail: str


@dataclass
class RemapRecord:
    span: str
    error_type: str
    old_criterion: str
    new_criterion: str
    family: str


@dataclass
class FPAudit:
    rows_in: int = 0
    rows_out: int = 0
    removed: int = 0
    remapped: int = 0
    duplicates_collapsed: int = 0
    removal_log: List[RemovalRecord] = field(default_factory=list)
    remap_log: List[RemapRecord] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"FP filter: {self.rows_in} in → {self.rows_out} out",
            f"  removed={self.removed}  remapped={self.remapped}  "
            f"dupes_collapsed={self.duplicates_collapsed}",
        ]
        for r in self.removal_log:
            lines.append(f"  [REMOVED] {r.reason}: '{r.span}' "
                         f"({r.criterion}/{r.error_type}) — {r.detail}")
        for r in self.remap_log:
            lines.append(f"  [REMAPPED] '{r.span}' {r.family}: "
                         f"{r.old_criterion} → {r.new_criterion}")
        return "\n".join(lines)


# ── Main filter class ─────────────────────────────────────────────────────────

class FPFilter:
    """
    Stateless false-positive filter for detector student_rows.

    Parameters
    ----------
    min_confidence : float
        Rows with confidence < this threshold are removed.  Default 0.50.
    must_not_catch : list[str]
        List of text spans (or substrings) that should never be flagged.
        Case-insensitive substring match.
    must_not_catch_patterns : list[str]
        Additional regex patterns (case-insensitive) to match must-not-catch spans.
    remap_family_criterion : bool
        If True (default), re-map rows whose family belongs to a different criterion
        rather than dropping them.
    drop_on_remap_failure : bool
        If True, drop a row when family remap is attempted but family is unknown.
        Default False (keep with original criterion).
    """

    def __init__(
        self,
        min_confidence: float = 0.50,
        must_not_catch: Optional[List[str]] = None,
        must_not_catch_patterns: Optional[List[str]] = None,
        remap_family_criterion: bool = True,
        drop_on_remap_failure: bool = False,
    ) -> None:
        self.min_confidence = min_confidence
        self._mnc_literals: List[str] = [s.lower() for s in (must_not_catch or [])]
        self._mnc_patterns: List[re.Pattern] = [
            re.compile(p, re.IGNORECASE)
            for p in (must_not_catch_patterns or [])
        ]
        self.remap_family_criterion = remap_family_criterion
        self.drop_on_remap_failure = drop_on_remap_failure

    # ── Public ────────────────────────────────────────────────────────────────

    def filter(
        self,
        rows: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], FPAudit]:
        """
        Apply all filter layers.

        Returns
        -------
        (filtered_rows, audit)
        """
        audit = FPAudit(rows_in=len(rows))

        # Layer 1 — confidence threshold
        after_conf, removed_conf = self._filter_confidence(rows)
        audit.removed += len(removed_conf)
        audit.removal_log.extend(removed_conf)

        # Layer 2 — must-not-catch
        after_mnc, removed_mnc = self._filter_must_not_catch(after_conf)
        audit.removed += len(removed_mnc)
        audit.removal_log.extend(removed_mnc)

        # Layer 3 — criterion–family consistency (remap or drop)
        after_remap, remapped, removed_remap = self._filter_criterion_family(after_mnc)
        audit.remapped += len(remapped)
        audit.remap_log.extend(remapped)
        audit.removed += len(removed_remap)
        audit.removal_log.extend(removed_remap)

        # Layer 4 — deduplicate (span, criterion) pairs
        after_dedup, n_dupes = self._dedup(after_remap)
        audit.duplicates_collapsed += n_dupes

        audit.rows_out = len(after_dedup)
        return after_dedup, audit

    # ── Layer 1: confidence ───────────────────────────────────────────────────

    def _filter_confidence(
        self,
        rows: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], List[RemovalRecord]]:
        kept, removed = [], []
        for row in rows:
            conf = self._get_confidence(row)
            if conf < self.min_confidence:
                removed.append(RemovalRecord(
                    span=self._get_span(row),
                    error_type=row.get("error_type", ""),
                    criterion=row.get("criterion", ""),
                    reason="low_confidence",
                    detail=f"confidence={conf:.3f} < threshold={self.min_confidence}",
                ))
            else:
                kept.append(row)
        return kept, removed

    # ── Layer 2: must-not-catch ───────────────────────────────────────────────

    def _filter_must_not_catch(
        self,
        rows: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], List[RemovalRecord]]:
        if not self._mnc_literals and not self._mnc_patterns:
            return rows, []
        kept, removed = [], []
        for row in rows:
            span = self._get_span(row).lower()
            matched = False
            for literal in self._mnc_literals:
                if literal in span or span in literal:
                    removed.append(RemovalRecord(
                        span=self._get_span(row),
                        error_type=row.get("error_type", ""),
                        criterion=row.get("criterion", ""),
                        reason="must_not_catch",
                        detail=f"matched literal: '{literal}'",
                    ))
                    matched = True
                    break
            if not matched:
                for pattern in self._mnc_patterns:
                    if pattern.search(self._get_span(row)):
                        removed.append(RemovalRecord(
                            span=self._get_span(row),
                            error_type=row.get("error_type", ""),
                            criterion=row.get("criterion", ""),
                            reason="must_not_catch",
                            detail=f"matched pattern: {pattern.pattern}",
                        ))
                        matched = True
                        break
            if not matched:
                kept.append(row)
        return kept, removed

    # ── Layer 3: criterion–family consistency ─────────────────────────────────

    def _filter_criterion_family(
        self,
        rows: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], List[RemapRecord], List[RemovalRecord]]:
        kept, remapped, removed = [], [], []
        for row in rows:
            error_type = row.get("error_type") or row.get("family") or ""
            raw_criterion = row.get("criterion", "")
            current_crit = _normalise_criterion(raw_criterion)

            correct_crit = _FAMILY_TO_CRITERION.get(error_type.upper())

            if correct_crit is None:
                # Family unknown — keep as-is
                new_row = dict(row)
                new_row["criterion"] = current_crit
                kept.append(new_row)
                continue

            if correct_crit == current_crit:
                # Already correct
                new_row = dict(row)
                new_row["criterion"] = current_crit
                kept.append(new_row)
                continue

            # Mismatch
            if self.remap_family_criterion:
                new_row = dict(row)
                new_row["criterion"] = correct_crit
                kept.append(new_row)
                remapped.append(RemapRecord(
                    span=self._get_span(row),
                    error_type=error_type,
                    old_criterion=current_crit,
                    new_criterion=correct_crit,
                    family=error_type,
                ))
            else:
                removed.append(RemovalRecord(
                    span=self._get_span(row),
                    error_type=error_type,
                    criterion=current_crit,
                    reason="criterion_family_mismatch",
                    detail=f"family {error_type} belongs to {correct_crit}, not {current_crit}",
                ))

        return kept, remapped, removed

    # ── Layer 4: deduplication ────────────────────────────────────────────────

    def _dedup(
        self,
        rows: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], int]:
        seen: Dict[Tuple[str, str], Tuple[float, int]] = {}  # key → (conf, list_idx)
        result_slots: Dict[int, Dict[str, Any]] = {}
        n_dupes = 0

        for i, row in enumerate(rows):
            span = self._get_span(row).lower().strip()
            crit = _normalise_criterion(row.get("criterion", ""))
            key = (span, crit)
            conf = self._get_confidence(row)

            if key not in seen:
                seen[key] = (conf, i)
                result_slots[i] = row
            else:
                n_dupes += 1
                prev_conf, prev_idx = seen[key]
                if conf > prev_conf:
                    # New row has higher confidence — replace
                    del result_slots[prev_idx]
                    seen[key] = (conf, i)
                    result_slots[i] = row
                # else: keep existing, discard current

        return [result_slots[k] for k in sorted(result_slots)], n_dupes

    # ── Utilities ─────────────────────────────────────────────────────────────

    @staticmethod
    def _get_span(row: Dict[str, Any]) -> str:
        return (
            row.get("span")
            or row.get("text_span")
            or row.get("student_text")
            or row.get("quote")
            or ""
        )

    @staticmethod
    def _get_confidence(row: Dict[str, Any]) -> float:
        raw = (
            row.get("confidence")
            or row.get("confidence_score")
            or row.get("score")
        )
        if raw is None:
            return 1.0  # assume confident if field missing
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 1.0


# ── Convenience: build filter from benchmark must_not_catch list ──────────────

def build_filter_from_benchmark(
    must_not_catch: List[Dict[str, Any]],
    min_confidence: float = 0.50,
) -> FPFilter:
    """
    Construct an FPFilter from a benchmark section_3_must_not_catch list.

    Each entry is expected to have a 'quote' key (the text span to protect).
    """
    spans = [entry.get("quote", "") for entry in must_not_catch if entry.get("quote")]
    return FPFilter(min_confidence=min_confidence, must_not_catch=spans)
