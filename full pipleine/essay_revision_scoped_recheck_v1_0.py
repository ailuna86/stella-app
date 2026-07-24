#!/usr/bin/env python3
"""ST.ELLA Essay Revision Scoped Re-check v1.0
=================================================

Session_Flow_and_Vocab_Expansion_Spec_v1.docx Section 1 ("Essay Revision: a
scoped, real re-check, not a full re-band"). NOTE ON NAMING: the spec's own
provisional filename suggestion was "lret_v2_revision_scoped_recheck_v1_0.py"
-- that is a naming artifact from the spec author and is misleading (this
engine is NOT part of LRET, the lexical-resource enhancement engine; it
wraps the Detector, not LRET). This file is deliberately named without the
"lret_v2" prefix to avoid that confusion.

What this engine does
----------------------
Given an ORIGINAL essay and a STUDENT-REVISED essay, it:
  1. Diffs the two texts to find which sentences the student actually
     rewrote (see "Sentence alignment" below).
  2. For every changed sentence, runs the real Detector (det_vip via
     det_vip_cli_bridge_v1_1.py) on the sentence's PARAGRAPH, once for the
     original paragraph text and once for the revised paragraph text, and
     compares the local-language errors (grammar / lexical_resource rubric
     only -- see "Scope" below) found in each changed sentence before vs.
     after.
  3. Produces one aggregated, honest, plain-English summary ("You rewrote 3
     sentences. 2 are now error-free. 1 introduced a new article error.")
     plus a per-sentence before/after breakdown.

What this engine explicitly does NOT do (per spec 1.1)
--------------------------------------------------------
- It does NOT produce a new holistic band score.
- It does NOT claim Task Response or Coherence & Cohesion improved (or
  worsened) -- those criteria genuinely cannot be judged from a handful of
  edited sentences in isolation; doing so would be a false-precision claim.
- It does NOT run Scorer / Verifier / Adjudicator. Detector only.

Scope (why grammar/lexical_resource only)
------------------------------------------
Confirmed directly against a real session's 01d_detector_for_scorer.json:
every row the Detector produces at essay level carries
layer == "layer3_local_language" and rubric in {"grammar",
"lexical_resource"} -- there is no row-level coherence/task-response signal
at the per-sentence layer. This matches the spec's own reasoning (local
language quality can be judged sentence-by-sentence; holistic criteria
cannot). Rows are filtered to rubric in {"grammar", "lexical_resource"}
defensively, in case some essay produces a differently-tagged row.

Sentence alignment -- method and KNOWN LIMITATIONS (spec Section 5, "open/
provisional")
------------------------------------------------------------------------------
The spec leaves this deliberately open ("needs a real method... not yet
designed in detail... left to your judgment"). This engine implements:

  1. Paragraph split: both texts are split into paragraphs on blank lines
     (\n\s*\n), the same convention every other engine in this codebase
     uses (see stella-frontend/lib/server/text.ts's normalizeParagraphBreaks
     doc comment, which lists the other engines sharing this contract).
  2. Paragraph alignment: difflib.SequenceMatcher over the (whitespace/case
     normalized) paragraph list. "equal" blocks are skipped entirely (no
     Detector call needed for unchanged paragraphs -- this is also what
     keeps the cost down per spec 1.2). "replace" blocks are paired
     POSITIONALLY (paragraph i of the original block <-> paragraph i of the
     revised block); any length mismatch inside a replace block is treated
     as trailing inserts/deletes. Pure "insert" paragraphs (wholly new,
     e.g. the student added a paragraph) have no original counterpart, so
     their sentences are reported separately as "new sentences added"
     (after-only, no before/after delta possible). Pure "delete" paragraphs
     are reported as a count only -- nothing to re-check once text is gone.
  3. Sentence split (per paired paragraph): sentences are split using the
     EXACT SAME regex det_vip_v18d_3_topic_alignment_risk.py's own
     sentence_split_with_spans() uses ([^.!?]+(?:[.!?]+|$)). This is
     deliberate, not a coincidence: when this engine later feeds a single
     paragraph to the Detector as a mini one-paragraph "essay" (see below),
     the Detector will segment it with that same regex, so this engine's
     own sentence numbering lines up with the Detector's own
     row["sentence_index"] for that mini-essay run.
  4. Sentence alignment (within a paired paragraph): difflib.SequenceMatcher
     over the normalized sentence lists, same opcode handling as paragraphs
     -- "equal" sentences are untouched (skipped), "replace" blocks are
     paired positionally, "insert" sentences are new-sentence-added,
     "delete" sentences are removed (counted, not re-checked).

  KNOWN LIMITATIONS (documented per spec 5's explicit request):
  - Positional pairing inside a "replace" block is NOT true semantic
    alignment. If a student reorders two sentences within the same edited
    block, or merges two sentences into one, or splits one into two, this
    method will pair them by POSITION, not by meaning -- e.g. splitting one
    long sentence into two will show original-sentence-1 vs revised
    sentence-1 and (spuriously) treat revised-sentence-2 as a "new sentence
    added" rather than recognising it as part of the same edit. This is the
    exact gap the spec's Section 5 flags as "not yet designed in detail".
  - The regex sentence splitter (like det_vip's own) does not special-case
    abbreviations, decimals, or initials (e.g. "the U.K." or "Mr. Smith")
    and can over-split on them. Because the SAME regex is used for both this
    engine's own segmentation and (indirectly) the Detector's, sentence
    counts should usually still line up -- but if they don't for a given
    paragraph, row attribution falls back to fuzzy text matching (see
    attribute_rows_to_sentences()) rather than failing outright.
  - Purely cosmetic changes (whitespace, capitalisation only) are treated as
    "unchanged" and are not re-checked, which is intentional (nothing was
    substantively rewritten).

Detector invocation -- how a "sentence in paragraph context" is actually run
------------------------------------------------------------------------------
det_vip_cli_bridge_v1_1.py (and the PremiumDetectorV9 it wraps) does not have
a "check just this sentence" mode -- it only takes a full `essay_text` string
and runs its own paragraph/sentence segmentation, idea-map, and layered
analysis over it. There is no minimum-length gate in the engine (confirmed
by reading det_vip_v18d_3_topic_alignment_risk.py directly -- no
`word_count <` / `too_short` gate exists), so this engine constructs a
minimal, valid input shape for the Detector: the PARAGRAPH containing the
changed sentence(s) is submitted as a one-paragraph mini "essay" (twice: once
with the original paragraph text, once with the revised paragraph text). This
gives the Detector real sentence-level paragraph context for the sentence(s)
that changed, without a full ~270-word essay re-run, matching spec 1.1's "in
its paragraph context" requirement and spec 1.2's cost scoping (typically 1-5
sentences -> at most 2 Detector calls per edited paragraph, not one call for
the whole essay).

Known caveat: because only one paragraph (not the whole essay) is submitted,
the Detector's own topic-alignment / idea-map / task-schema layers, which
depend on full-essay context, will be noisy or degenerate for this mini
"essay" -- this is expected and is exactly why this engine only reads
layer3_local_language / grammar+lexical_resource rows and ignores everything
else the Detector returns.

Sandbox verification caveat (be read before trusting a "no errors found"
result from this machine)
------------------------------------------------------------------------------
This engine was smoke-tested in a sandbox that is MISSING: an OPENAI_API_KEY,
the en_core_web_sm spaCy model, language_tool_python, and det_vip's own
decision-registry / resource JSON files (family_lock_registry_v2.json,
rule_registry_v1.json, task_schema_registry_v1.json, etc. -- these load from
a hardcoded path on the production machine per
DEFAULT_V9_REGISTRY_DIR/DEFAULT_V9_RESOURCE_DIR in
det_vip_v18d_3_topic_alignment_risk.py and are not present in this sandbox).
With all of those missing, PremiumDetectorV9 runs in a fully degraded mode
and finds 0 raw candidates regardless of input -- this was confirmed directly
(qa.resource_quality_status == "degraded_resource_loading",
qa.decision_registry_status == "degraded_decision_registry_loading" in a
real subprocess run). This engine's CLI plumbing (building request/response
files, invoking the subprocess, parsing whatever JSON shape comes back) was
verified for real against that degraded run. The diff/alignment/delta logic
itself was additionally verified against realistic, hand-built detector-row
fixtures shaped exactly like the real rows in
01d_detector_for_scorer.json (same field names/values) to prove the
alignment and delta computation are correct independent of the sandbox's
resource gaps -- see the verification transcript in this engine's PR/report,
not reproduced here. On a machine with the real registries/resources/API
key (i.e. production), this engine's Detector calls will produce real
detection results the same way the existing pipeline's own detector stage
does.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = "ESSAY_REVISION_SCOPED_RECHECK_V1_0"
ENGINE_ID = "VA_STELLA_ESSAY_REVISION_SCOPED_RECHECK"
ENGINE_VERSION = "1.0.0-detector-only-changed-sentences"

DEFAULT_DETECTOR_SCRIPT = "det_vip_cli_bridge_v1_1.py"
IN_SCOPE_RUBRICS = {"grammar", "lexical_resource"}

# Copied verbatim from det_vip_v18d_3_topic_alignment_risk.py's paragraph_split()
# / sentence_split_with_spans() so this engine's own sentence numbering lines
# up with what the Detector itself will compute when handed the same
# paragraph text as a mini one-paragraph "essay". See module docstring,
# "Sentence alignment" section, point 3.
_PARA_SPLIT_RE = re.compile(r"\S(?:.*?\S)?(?=\n\s*\n|$)", flags=re.S)
_SENT_SPLIT_RE = re.compile(r"[^.!?]+(?:[.!?]+|$)", flags=re.S)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, data: Any, pretty: bool = False) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2 if pretty else None)
        f.write("\n")


def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def normalize_for_compare(s: str) -> str:
    return normalize_ws(s).lower()


def split_paragraphs(text: str) -> List[str]:
    out = []
    for m in _PARA_SPLIT_RE.finditer(text or ""):
        para = m.group(0).strip()
        if para:
            out.append(para)
    if not out and (text or "").strip():
        out.append(text.strip())
    return out


def split_sentences(paragraph_text: str) -> List[str]:
    out = []
    for m in _SENT_SPLIT_RE.finditer(paragraph_text or ""):
        s = m.group(0).strip()
        if s:
            out.append(s)
    return out


# ---------------------------------------------------------------------------
# Alignment
# ---------------------------------------------------------------------------

class AlignedUnit:
    """One outcome of aligning two ordered lists of text units (paragraphs or
    sentences). kind is one of: "unchanged", "changed" (has both an original
    and a revised unit that differ), "added" (revised-only, no original
    counterpart), "removed" (original-only, no revised counterpart)."""

    __slots__ = ("kind", "original", "revised", "orig_index", "rev_index")

    def __init__(self, kind: str, original: Optional[str], revised: Optional[str],
                 orig_index: Optional[int], rev_index: Optional[int]):
        self.kind = kind
        self.original = original
        self.revised = revised
        self.orig_index = orig_index
        self.rev_index = rev_index


def align_units(original_units: List[str], revised_units: List[str]) -> List[AlignedUnit]:
    """Align two ordered lists of text units with difflib.SequenceMatcher.

    "replace" opcodes (block of M original units vs N revised units) are
    paired POSITIONALLY, min(M, N) pairs; leftovers become "removed"
    (if M > N) or "added" (if N > M). See module docstring for the known
    limitations of positional-within-block pairing (reordering/merging/
    splitting inside one edited block is not detected as such).
    """
    norm_orig = [normalize_for_compare(u) for u in original_units]
    norm_rev = [normalize_for_compare(u) for u in revised_units]
    sm = difflib.SequenceMatcher(None, norm_orig, norm_rev, autojunk=False)
    out: List[AlignedUnit] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                out.append(AlignedUnit("unchanged", original_units[i1 + k], revised_units[j1 + k], i1 + k, j1 + k))
        elif tag == "replace":
            m = i2 - i1
            n = j2 - j1
            paired = min(m, n)
            for k in range(paired):
                out.append(AlignedUnit("changed", original_units[i1 + k], revised_units[j1 + k], i1 + k, j1 + k))
            if m > n:
                for k in range(paired, m):
                    out.append(AlignedUnit("removed", original_units[i1 + k], None, i1 + k, None))
            elif n > m:
                for k in range(paired, n):
                    out.append(AlignedUnit("added", None, revised_units[j1 + k], None, j1 + k))
        elif tag == "delete":
            for k in range(i1, i2):
                out.append(AlignedUnit("removed", original_units[k], None, k, None))
        elif tag == "insert":
            for k in range(j1, j2):
                out.append(AlignedUnit("added", None, revised_units[k], None, k))
    return out


# ---------------------------------------------------------------------------
# Detector invocation
# ---------------------------------------------------------------------------

class DetectorRunResult:
    def __init__(self, rows: List[Dict[str, Any]], status: str, note: str = "",
                 resource_quality_status: Optional[str] = None,
                 decision_registry_status: Optional[str] = None,
                 llm_status: Optional[str] = None):
        self.rows = rows
        self.status = status
        self.note = note
        self.resource_quality_status = resource_quality_status
        self.decision_registry_status = decision_registry_status
        self.llm_status = llm_status


def run_detector_on_text(
    text: str,
    prompt_text: str,
    task_type: str,
    python_exe: str,
    detector_script: str,
    work_dir: Path,
    engine_module_dir: Optional[str],
    require_llm: bool,
    timeout_seconds: int,
    tag: str,
) -> DetectorRunResult:
    """Runs det_vip_cli_bridge_v1_1.py on `text` treated as a standalone
    mini "essay" (see module docstring). Returns the in-scope
    (grammar/lexical_resource) rows plus a status for transparency."""
    work_dir.mkdir(parents=True, exist_ok=True)
    stamp = uuid.uuid4().hex[:12]
    in_file = work_dir / f"mini_essay_{tag}_{stamp}.json"
    out_file = work_dir / f"mini_essay_{tag}_{stamp}_detector.json"
    write_json(str(in_file), {
        "essay_id": f"recheck_{tag}_{stamp}",
        "student_id": "revision_scoped_recheck",
        "task_type": task_type or "WT2",
        "prompt_text": prompt_text or "",
        "essay_text": text,
    })

    cmd = [
        python_exe,
        detector_script,
        "--input", str(in_file),
        "--output", str(out_file),
        "--essay-index", "0",
        "--allow-over-word-limit",
    ]
    if require_llm:
        cmd.append("--require-llm")
    if engine_module_dir:
        cmd.extend(["--engine-module-dir", engine_module_dir])

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(Path(detector_script).resolve().parent) if os.path.dirname(detector_script) else None,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return DetectorRunResult([], "error", f"detector timed out after {timeout_seconds}s")
    except FileNotFoundError as e:
        return DetectorRunResult([], "error", f"could not launch detector subprocess: {e}")

    if proc.returncode != 0:
        return DetectorRunResult([], "error", f"detector exited {proc.returncode}: {(proc.stderr or '')[-800:]}")

    if not out_file.exists():
        return DetectorRunResult([], "error", "detector produced no output file")

    try:
        raw = read_json(str(out_file))
    except Exception as e:  # pragma: no cover - defensive
        return DetectorRunResult([], "error", f"could not parse detector output: {e}")

    results = raw.get("results") or []
    if not results:
        return DetectorRunResult([], "error", "detector output had no results[]")
    res = results[0]
    rows = res.get("student_rows") or res.get("all_rows") or []
    in_scope_rows = [r for r in rows if isinstance(r, dict) and (r.get("rubric") in IN_SCOPE_RUBRICS)]

    qa = res.get("qa") or {}
    return DetectorRunResult(
        in_scope_rows,
        "ok",
        note=f"{len(rows)} total rows, {len(in_scope_rows)} in-scope (grammar/lexical_resource)",
        resource_quality_status=qa.get("resource_quality_status"),
        decision_registry_status=qa.get("decision_registry_status"),
        llm_status=qa.get("llm_status"),
    )


def attribute_rows_to_sentences(rows: List[Dict[str, Any]], sentences: List[str]) -> Dict[int, List[Dict[str, Any]]]:
    """Maps each detector row to one of `sentences` (0-based index into the
    list this engine itself split for the same paragraph text).

    Primary method: the Detector's own row["sentence_index"] is 1-based and,
    because the paragraph was submitted as a single-paragraph mini-essay, it
    should equal (list index + 1) here -- both this engine and det_vip use
    the identical sentence-splitting regex (see module docstring).

    Fallback: if the index is out of range (e.g. the two segmenters
    disagreed on sentence count for this particular paragraph -- see the
    abbreviation/decimal caveat in the module docstring), fall back to fuzzy
    text matching of row["local_quote"] / row["quote"] against each
    candidate sentence, keeping the best match if its similarity ratio is
    at least 0.5. Rows that still can't be confidently attributed are
    dropped from the per-sentence view (they are still counted in the raw
    detector-run note for transparency, just not surfaced as belonging to a
    specific sentence)."""
    out: Dict[int, List[Dict[str, Any]]] = {}
    for row in rows:
        idx = row.get("sentence_index")
        target_i: Optional[int] = None
        if isinstance(idx, int) and 1 <= idx <= len(sentences):
            target_i = idx - 1
        else:
            quote = normalize_for_compare(str(row.get("local_quote") or row.get("quote") or ""))
            best_ratio = 0.0
            best_i = None
            for i, sent in enumerate(sentences):
                ratio = difflib.SequenceMatcher(None, quote, normalize_for_compare(sent)).ratio()
                if quote and quote in normalize_for_compare(sent):
                    ratio = max(ratio, 0.9)
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_i = i
            if best_i is not None and best_ratio >= 0.5:
                target_i = best_i
        if target_i is None:
            continue
        out.setdefault(target_i, []).append(row)
    return out


def summarize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "family": row.get("family") or row.get("issue_code") or "UNKNOWN",
        "rubric": row.get("rubric"),
        "quote": row.get("quote") or row.get("local_quote") or "",
        "message": row.get("problem_statement") or row.get("explanation") or row.get("student_message") or "",
        "suggested_revision": row.get("repair_hypothesis") or row.get("suggested_revision"),
        "severity": row.get("severity"),
    }


def diff_error_lists(before: List[Dict[str, Any]], after: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Multiset diff on (family) between before/after rows for one sentence.
    A single sentence with, say, 2 article errors before and 1 after counts
    as 1 fixed, not 2 -- this is a per-error count diff, not a boolean
    has-errors flip."""
    before_fams = Counter(r["family"] for r in before)
    after_fams = Counter(r["family"] for r in after)
    fixed_count = before_fams - after_fams
    introduced_count = after_fams - before_fams

    def pick(rows: List[Dict[str, Any]], counter: Counter) -> List[Dict[str, Any]]:
        remaining = Counter(counter)
        picked = []
        for r in rows:
            fam = r["family"]
            if remaining.get(fam, 0) > 0:
                picked.append(r)
                remaining[fam] -= 1
        return picked

    fixed = pick(before, fixed_count)
    introduced = pick(after, introduced_count)
    persisting_count = before_fams & after_fams
    persisting = pick(after, persisting_count)
    return {"fixed": fixed, "introduced": introduced, "persisting": persisting}


def sentence_status(before: List[Dict[str, Any]], after: List[Dict[str, Any]]) -> Tuple[str, str]:
    if not before and not after:
        return "already_clean_rewrite", "No local-language errors detected before or after — this looks like a stylistic/content edit, not an error fix."
    if before and not after:
        return "now_error_free", "Error-free now — nice fix."
    if not before and after:
        return "introduced_new_error", "This rewrite introduced a new error that wasn't there before."
    # both non-empty
    before_fams = Counter(r["family"] for r in before)
    after_fams = Counter(r["family"] for r in after)
    if after_fams == before_fams:
        return "still_has_errors", "Still has the same error(s) as before — this sentence wasn't fixed."
    if sum(after_fams.values()) < sum(before_fams.values()):
        return "partially_improved", "Fewer errors than before, but not fully error-free yet."
    if sum(after_fams.values()) > sum(before_fams.values()):
        return "got_worse", "This rewrite has MORE local-language errors than the original sentence did."
    return "changed_errors", "The specific error(s) changed — some fixed, some new."


def build_honest_summary(sentence_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(sentence_results)
    now_error_free = sum(1 for s in sentence_results if s["status"] == "now_error_free")
    already_clean = sum(1 for s in sentence_results if s["status"] == "already_clean_rewrite")
    introduced_new = sum(1 for s in sentence_results if s["status"] in ("introduced_new_error", "got_worse", "changed_errors"))
    still_has_errors = sum(1 for s in sentence_results if s["status"] in ("still_has_errors", "partially_improved", "got_worse", "changed_errors"))
    total_fixed = sum(len(s["fixed"]) for s in sentence_results)
    total_introduced = sum(len(s["introduced"]) for s in sentence_results)

    if n == 0:
        text = "You didn't rewrite any sentences we could detect changes in."
    else:
        parts = [f"You rewrote {n} sentence{'s' if n != 1 else ''}."]
        if now_error_free or already_clean:
            clean_total = now_error_free + already_clean
            parts.append(f"{clean_total} {'is' if clean_total == 1 else 'are'} now error-free.")
        introduced_rows = [r for s in sentence_results for r in s["introduced"]]
        if introduced_rows:
            if len(introduced_rows) == 1:
                fam = introduced_rows[0]["family"].replace("_", " ").lower()
                parts.append(f"1 introduced a new {fam} error.")
            else:
                fam_counts = Counter(r["family"].replace("_", " ").lower() for r in introduced_rows)
                top_fam, top_n = fam_counts.most_common(1)[0]
                parts.append(f"{len(introduced_rows)} new errors were introduced (most commonly {top_fam}).")
        if still_has_errors and not introduced_rows:
            parts.append(f"{still_has_errors} still {'has' if still_has_errors == 1 else 'have'} at least one error left to fix.")
        text = " ".join(parts)

    return {
        "sentences_rewritten": n,
        "now_error_free": now_error_free,
        "already_clean_rewrite": already_clean,
        "still_has_errors": still_has_errors,
        "introduced_new_error_sentences": introduced_new,
        "total_errors_fixed": total_fixed,
        "total_errors_introduced": total_introduced,
        "honest_summary_text": text,
        "scope_disclaimer": (
            "This is a sentence-level grammar/vocabulary check of only the sentences you "
            "rewrote — it is not a full re-evaluation. It does not re-score Task Response or "
            "Coherence & Cohesion, and it is not a new overall band score."
        ),
    }


def process_request(
    original_text: str,
    revised_text: str,
    prompt_text: str,
    task_type: str,
    python_exe: str,
    detector_script: str,
    work_dir: Path,
    engine_module_dir: Optional[str],
    require_llm: bool,
    timeout_seconds: int,
    max_detector_calls: int,
) -> Dict[str, Any]:
    orig_paragraphs = split_paragraphs(original_text)
    rev_paragraphs = split_paragraphs(revised_text)
    para_alignment = align_units(orig_paragraphs, rev_paragraphs)

    sentence_results: List[Dict[str, Any]] = []
    new_sentence_results: List[Dict[str, Any]] = []
    removed_sentence_count = 0
    detector_runs: List[Dict[str, Any]] = []
    calls_made = 0
    truncated = False

    for para_unit in para_alignment:
        if para_unit.kind == "unchanged":
            continue

        if para_unit.kind == "removed":
            # Whole paragraph removed -- nothing left to re-check.
            if para_unit.original:
                removed_sentence_count += len(split_sentences(para_unit.original))
            continue

        if para_unit.kind == "added":
            # Whole new paragraph -- after-only check, no "before" exists.
            rev_sentences = split_sentences(para_unit.revised or "")
            if not rev_sentences:
                continue
            if calls_made >= max_detector_calls:
                truncated = True
                continue
            calls_made += 1
            after_result = run_detector_on_text(
                para_unit.revised or "", prompt_text, task_type, python_exe, detector_script,
                work_dir, engine_module_dir, require_llm, timeout_seconds, tag="new_para_after",
            )
            detector_runs.append({
                "paragraph_kind": "added", "direction": "after", "status": after_result.status,
                "note": after_result.note, "resource_quality_status": after_result.resource_quality_status,
                "decision_registry_status": after_result.decision_registry_status, "llm_status": after_result.llm_status,
            })
            attributed = attribute_rows_to_sentences(after_result.rows, rev_sentences) if after_result.status == "ok" else {}
            for i, sent in enumerate(rev_sentences):
                errs = [summarize_row(r) for r in attributed.get(i, [])]
                new_sentence_results.append({
                    "revised_text": sent,
                    "errors_after": errs,
                    "status": "new_sentence_clean" if not errs else "new_sentence_has_errors",
                })
            continue

        # kind == "changed": a paragraph pair where at least one sentence differs.
        orig_para = para_unit.original or ""
        rev_para = para_unit.revised or ""
        orig_sentences = split_sentences(orig_para)
        rev_sentences = split_sentences(rev_para)
        sent_alignment = align_units(orig_sentences, rev_sentences)

        changed_units = [u for u in sent_alignment if u.kind == "changed"]
        added_units = [u for u in sent_alignment if u.kind == "added"]
        removed_units = [u for u in sent_alignment if u.kind == "removed"]
        removed_sentence_count += len(removed_units)

        if not changed_units and not added_units:
            continue  # paragraph text differs only cosmetically at the unit-diff level

        if calls_made + 2 > max_detector_calls:
            truncated = True
            continue
        calls_made += 2
        before_result = run_detector_on_text(
            orig_para, prompt_text, task_type, python_exe, detector_script,
            work_dir, engine_module_dir, require_llm, timeout_seconds, tag="before",
        )
        after_result = run_detector_on_text(
            rev_para, prompt_text, task_type, python_exe, detector_script,
            work_dir, engine_module_dir, require_llm, timeout_seconds, tag="after",
        )
        detector_runs.append({
            "paragraph_kind": "changed", "direction": "before", "status": before_result.status,
            "note": before_result.note, "resource_quality_status": before_result.resource_quality_status,
            "decision_registry_status": before_result.decision_registry_status, "llm_status": before_result.llm_status,
        })
        detector_runs.append({
            "paragraph_kind": "changed", "direction": "after", "status": after_result.status,
            "note": after_result.note, "resource_quality_status": after_result.resource_quality_status,
            "decision_registry_status": after_result.decision_registry_status, "llm_status": after_result.llm_status,
        })

        before_attributed = attribute_rows_to_sentences(before_result.rows, orig_sentences) if before_result.status == "ok" else {}
        after_attributed = attribute_rows_to_sentences(after_result.rows, rev_sentences) if after_result.status == "ok" else {}

        for u in changed_units:
            before_errs = [summarize_row(r) for r in before_attributed.get(u.orig_index, [])]
            after_errs = [summarize_row(r) for r in after_attributed.get(u.rev_index, [])]
            delta = diff_error_lists(before_errs, after_errs)
            status, status_label = sentence_status(before_errs, after_errs)
            sentence_results.append({
                "original_text": u.original,
                "revised_text": u.revised,
                "errors_before": before_errs,
                "errors_after": after_errs,
                "fixed": delta["fixed"],
                "introduced": delta["introduced"],
                "persisting": delta["persisting"],
                "status": status,
                "status_label": status_label,
            })

        for u in added_units:
            after_errs = [summarize_row(r) for r in after_attributed.get(u.rev_index, [])]
            new_sentence_results.append({
                "revised_text": u.revised,
                "errors_after": after_errs,
                "status": "new_sentence_clean" if not after_errs else "new_sentence_has_errors",
            })

    summary = build_honest_summary(sentence_results)
    summary["new_sentences_added"] = len(new_sentence_results)
    summary["sentences_removed"] = removed_sentence_count
    summary["truncated_for_cost_cap"] = truncated

    return {
        "schema_version": SCHEMA_VERSION,
        "engine_id": ENGINE_ID,
        "engine_version": ENGINE_VERSION,
        "created_at": now_iso(),
        "method": {
            "paragraph_alignment": "difflib.SequenceMatcher over blank-line-split paragraphs; equal blocks skipped, replace blocks paired positionally",
            "sentence_alignment": "difflib.SequenceMatcher over regex-split sentences within each paired paragraph; equal skipped, replace paired positionally",
            "detector_invocation": "det_vip_cli_bridge_v1_1.py run on the whole paragraph (as a mini one-paragraph essay), once per direction (before/after), per changed paragraph",
            "in_scope_rubrics": sorted(IN_SCOPE_RUBRICS),
            "known_limitations": [
                "positional pairing inside a replace block does not detect sentence reordering, merges, or splits as such",
                "regex sentence splitter does not special-case abbreviations/decimals and can over-split; row attribution falls back to fuzzy text matching when index-based lookup is out of range",
                "Detector's non-local-language layers (idea map, task schema, topic alignment) are noisy on a single-paragraph mini-essay input and are deliberately ignored",
            ],
        },
        "summary": summary,
        "sentence_results": sentence_results,
        "new_sentence_results": new_sentence_results,
        "detector_runs": detector_runs,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Scoped essay-revision re-check: diffs original vs. revised text, runs the "
                    "Detector (grammar/lexical_resource only) on changed sentences in paragraph "
                    "context, and reports an honest before/after delta. Not a full re-band."
    )
    ap.add_argument("--request", required=True, help="JSON: {original:{essay_text}, revised:{essay_text}, prompt:{prompt_text, task_type}}")
    ap.add_argument("--output", required=True)
    ap.add_argument("--pretty", action="store_true")
    ap.add_argument("--detector-script", default=DEFAULT_DETECTOR_SCRIPT)
    ap.add_argument("--python-executable", default=sys.executable)
    ap.add_argument("--engine-module-dir", default=None, help="Passthrough to det_vip_cli_bridge_v1_1.py's --engine-module-dir")
    ap.add_argument("--no-llm", action="store_true", help="Do not pass --require-llm through to the Detector (rule/spaCy/LT passes only). Off by default, matching the existing pipeline's det_vip config (which always passes --require-llm).")
    ap.add_argument("--timeout-seconds", type=int, default=90, help="Per-Detector-subprocess-call timeout.")
    ap.add_argument("--max-detector-calls", type=int, default=12, help="Safety cap on total Detector subprocess calls for one recheck request (2 calls per changed paragraph).")
    ap.add_argument("--work-dir", default=None, help="Directory for mini-essay temp files. Defaults to a subfolder next to --output.")
    args = ap.parse_args(argv)

    req = read_json(args.request)
    original_text = str((req.get("original") or {}).get("essay_text") or "")
    revised_text = str((req.get("revised") or {}).get("essay_text") or "")
    prompt = req.get("prompt") or {}
    prompt_text = str(prompt.get("prompt_text") or "")
    task_type = str(prompt.get("task_type") or "WT2")

    if not original_text.strip() or not revised_text.strip():
        raise SystemExit("--request must include both original.essay_text and revised.essay_text")

    work_dir = Path(args.work_dir) if args.work_dir else Path(args.output).resolve().parent / "scoped_recheck_tmp"

    result = process_request(
        original_text=original_text,
        revised_text=revised_text,
        prompt_text=prompt_text,
        task_type=task_type,
        python_exe=args.python_executable,
        detector_script=args.detector_script,
        work_dir=work_dir,
        engine_module_dir=args.engine_module_dir,
        require_llm=not args.no_llm,
        timeout_seconds=args.timeout_seconds,
        max_detector_calls=args.max_detector_calls,
    )
    write_json(args.output, result, pretty=args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
