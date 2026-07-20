"""
feedback_engine_v6_adapter.py
=============================
STANDALONE FILE — no imports from v3/v4/v5 adapters.
All repair functions, templates, LLM helpers, and enrichment logic are inlined.

CHANGES vs feedback_engine_v5_adapter.py
-----------------------------------------
F28-B3  NOUN_NUMBER_COUNTABILITY key added to _RULE_FOR_LLM.
        Previously, detector errors of type NOUN_NUMBER_COUNTABILITY fell to the
        generic rule_statement because _RULE_FOR_LLM only had "NOUN_NUMBER".
        Fix: add explicit entry for NOUN_NUMBER_COUNTABILITY with a clear rule.

F28-B7  "1 overlapping errors" → "1 overlapping error".
        _build_combo_explanation() now uses grammatically correct singular/plural
        for the overlapping error count word.

F29     LR explanation internal text filter.
        FE v6.4 populates top_learning_priorities[i].why_this_matters with
        internal diagnostic text ("This target is selected because repeated
        detector rows map to..."). When populate_focus_area_explanations copies
        this verbatim it leaks implementation details to the student.
        Fix: _is_internal_text() detects marker phrases; when triggered,
        populate_focus_area_explanations_v6() replaces the text with a
        student-friendly template keyed on priority_reason and criterion.

F30     Essay-specific headline in sanitize_score_summary_v6().
        New optional band_scores: Dict parameter. When supplied, the reconstructed
        headline includes the actual criterion band (e.g. "Band 4.0") so each
        essay produces a different, accurate headline rather than a generic one.

CALL ORDER (Step 7 in pipeline_runner_v10.py)
----------------------------------------------
    report = generate_feedback_v2(...)                             # 7a frozen v2
    report = enrich_annotated_errors(report, errormap)            # 7b F13+F15
    report = inject_missing_annotated_errors(report, errormap,    # 7c F17
                 directive)
    report = fill_null_corrections_v5(report)                     # 7d F14+F22
    report = enrich_all_corrections(report)                       # 7e F14b
    report = enrich_with_sentence_context(report, errormap)       # 7f F10
    report = expand_all_error_instances(report, errormap)         # 7g F10
    report["broken_sentences"] = build_broken_sentences_section_v5( # 7h F11+F19
                 errormap.get("broken_sentences_raw", []), errormap)
    report = inject_missing_annotated_errors(report, errormap,    # 7c (already done above)
    report = populate_focus_area_explanations_v6(report,          # 7i F18+F21+F29
                 fe_bundle, directive)
    report = sanitize_score_summary_v6(report, fe_bundle,         # 7j F20+F30
                 band_scores=band_scores)

USAGE
-----
    from feedback_engine_v6_adapter import (
        enrich_annotated_errors,
        inject_missing_annotated_errors,
        fill_null_corrections_v5,
        enrich_all_corrections,
        enrich_with_sentence_context,
        expand_all_error_instances,
        build_broken_sentences_section_v5,
        populate_focus_area_explanations_v6,
        sanitize_score_summary_v6,
    )
"""
from __future__ import annotations

import os
import re as _re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

_ADAPTER_VERSION = "v6"


# =============================================================================
# SECTION A — CORRECTION TEMPLATES & RULE DESCRIPTIONS  (from v3/v4, + F28-B3)
# =============================================================================

_CORRECTION_TEMPLATES: Dict[str, str] = {
    # Grammar — verb
    "VERB_FORM":
        "After modal/auxiliary verbs (must, can, will, have to), use the bare "
        "infinitive (base form) of the main verb.",
    "TENSE":
        "Check that the verb tense matches the time reference in this sentence "
        "(past, present, or future).",
    "PASSIVE_VOICE":
        "Check the passive construction: 'be + past participle' "
        "(e.g. 'is written', 'was given').",
    "MODAL_USE":
        "Check which modal verb expresses the right degree of certainty, "
        "possibility, or obligation here.",
    # Grammar — agreement
    "SUBJECT_VERB_AGREEMENT":
        "The verb must agree with its subject in number: singular subject → "
        "singular verb (is/was/has), plural subject → plural verb (are/were/have).",
    "NOUN_NUMBER":
        "Check whether this noun should be singular or plural in this context.",
    # F28-B3: NOUN_NUMBER_COUNTABILITY (detector emits this type; was missing from templates)
    "NOUN_NUMBER_COUNTABILITY":
        "Check whether this noun should be singular or plural. Uncountable nouns "
        "(information, advice, research, equipment, furniture, knowledge) have no "
        "plural form. Countable nouns form the plural with -s/-es.",
    # Grammar — clause / sentence structure
    "CLAUSE_STRUCTURE":
        "A finite clause needs a subject and a main verb. Check whether a copula "
        "('is/was/are/were') or auxiliary is missing.",
    "SENTENCE_STRUCTURE":
        "Check that this sentence has a clear subject and predicate, and that "
        "clauses are connected correctly.",
    "FRAGMENT":
        "This looks like an incomplete sentence. Make sure it has a main clause "
        "with a subject and a finite verb.",
    "RUN_ON":
        "This sentence may be too long or incorrectly joined. Consider splitting "
        "it or adding a conjunction/semicolon.",
    # Grammar — comparison
    "COMPARATIVE_FORM":
        "For comparisons use either 'more [adjective]' (for longer adjectives) "
        "or '[adjective]-er' — never both together.",
    "SUPERLATIVE_FORM":
        "For superlatives use either 'most [adjective]' or '[adjective]-est' — "
        "never both together.",
    # Grammar — articles / determiners
    "ARTICLE_DETERMINER":
        "Check whether a/an/the is needed here, or whether no article is correct. "
        "Use 'a/an' for first mention, 'the' for known/specific referents.",
    # Grammar — pronouns / reference
    "PRONOUN":
        "Check that the pronoun agrees with its antecedent in number "
        "(singular/plural) and is the correct case (I/me, he/him, they/them).",
    "PRONOUN_AGREEMENT":
        "The pronoun must agree with the noun it refers to in number and gender.",
    # Grammar — prepositions
    "PREPOSITION":
        "Check which preposition is conventionally used after this verb, "
        "adjective, or noun. Some combinations are fixed "
        "(e.g. 'interested in', 'depend on').",
    # Grammar — punctuation / spelling / word form
    "PUNCTUATION":
        "Check punctuation at this point — a comma, semicolon, colon, or full "
        "stop may be missing or incorrectly placed.",
    "SPELLING":      "Check the spelling of this word.",
    "WORD_FORM":
        "Check that the correct word form is used here: noun, verb, adjective, "
        "or adverb (e.g. 'economy/economic/economically').",
    # Lexical resource
    "COLLOCATION":
        "This word combination is not standard in academic English. Use a "
        "collocation dictionary or check an academic corpus for the conventional "
        "pairing.",
    "LEXICAL_PRECISION":
        "This word is too vague or informal for the context. Choose a more "
        "precise or academically appropriate term.",
    "REGISTER":
        "This word or phrase is too informal for an academic essay. Replace it "
        "with a formal equivalent.",
    "WORD_CHOICE":
        "There is a more precise or appropriate word choice available here. "
        "Consider what you mean exactly and select the best word.",
    "REPETITION":
        "Avoid repeating the same word or phrase too closely. Use a synonym or "
        "restructure the sentence.",
    # Task achievement / coherence
    "CLAIM_SUPPORT":
        "This claim needs supporting evidence or an example. Develop the point "
        "further.",
    "TOPIC_SENTENCE":
        "The paragraph topic sentence should clearly state the main idea of the "
        "paragraph.",
    "COHERENCE":
        "The connection between this sentence and the previous one is not clear. "
        "Add a linking phrase or restructure to make the logical relationship "
        "explicit.",
    "DISCOURSE_MARKER":
        "Check that this linking word (however, therefore, although, etc.) "
        "correctly signals the logical relationship between ideas.",
    "REFERENCING":
        "Make the reference explicit — the reader should know exactly what "
        "'it', 'this', or 'they' refers to.",
}

_GENERIC_RULE = (
    "Review this carefully. Consider whether the grammar rule, word form, "
    "or expression is correct in this context."
)

# LLM rule descriptions (used in per-excerpt LLM prompt)
_RULE_FOR_LLM: Dict[str, str] = {
    "VERB_FORM":
        "After modal/auxiliary verbs (must/can/will/would/should/may/might/"
        "have to/has to/need to/ought to) use the BASE FORM (bare infinitive) "
        "of the main verb. E.g. 'has to spent' → 'has to spend'.",
    "SUBJECT_VERB_AGREEMENT":
        "With a third-person singular subject (he/she/it/this/that/a noun) "
        "the present simple verb needs -s: 'it makes', 'she has', 'this gives'. "
        "E.g. 'it have' → 'it has', 'this make' → 'this makes'.",
    "COMPARATIVE_FORM":
        "Use EITHER '-er' OR 'more', never both together. "
        "E.g. 'more stronger' → 'stronger'; 'more older' → 'older'. "
        "'so older' → 'much older' (use 'much/far' to intensify comparatives).",
    "CLAUSE_STRUCTURE":
        "A finite clause needs a clear subject AND a finite verb. "
        "Check whether a copula (is/was/are/were) or auxiliary is missing.",
    "ARTICLE_DETERMINER":
        "Use 'a/an' for first mention of countable singular nouns, "
        "'the' for specific/known referents, no article for plural general nouns. "
        "Uncountable nouns (information/advice/knowledge) do not take 'a/an'.",
    "WORD_FORM":
        "Check whether a noun, verb, adjective, or adverb is needed at this "
        "position. E.g. 'economy' (noun) vs 'economic' (adj) vs 'economically' (adv).",
    "COLLOCATION":
        "This word combination is not standard. Check the conventional pairing: "
        "e.g. 'peoples' → 'people' (already plural); "
        "'do progress' → 'make progress'.",
    "LEXICAL_PRECISION":
        "The word/phrase is too vague or informal for academic writing. "
        "Suggest a more precise academic equivalent.",
    "PREPOSITION":
        "Check which preposition collocates with this verb/adjective/noun. "
        "Some combinations are fixed: 'depend on', 'interested in', "
        "'spend money on', 'take care of'.",
    "NOUN_NUMBER":
        "Check whether this noun should be singular or plural. "
        "Uncountable nouns (information, advice, research) have no plural form.",
    # F28-B3: added for NOUN_NUMBER_COUNTABILITY
    "NOUN_NUMBER_COUNTABILITY":
        "Check whether this noun should be singular or plural. "
        "Uncountable nouns (information, advice, research, equipment, furniture) "
        "have no plural form. Countable nouns form plural with -s/-es. "
        "E.g. 'peoples' → 'people', 'informations' → 'information', "
        "'issues' → 'issue' (when singular intended).",
}


# =============================================================================
# SECTION B — PATTERN-BASED REPAIR GENERATORS  (from v3, unchanged)
# =============================================================================

def _repair_verb_form(excerpt: str) -> Optional[str]:
    """has to spent → has to spend; must walked → must walk."""
    _IRREGULAR_TO_BASE: Dict[str, str] = {
        "spent": "spend", "gone": "go", "done": "do", "written": "write",
        "taken": "take", "given": "give", "seen": "see", "been": "be",
        "come": "come", "run": "run", "known": "know", "shown": "show",
        "grown": "grow", "drawn": "draw", "found": "find", "kept": "keep",
        "left": "leave", "lost": "lose", "made": "make", "meant": "mean",
        "met": "meet", "paid": "pay", "put": "put", "read": "read",
        "said": "say", "sold": "sell", "sent": "send", "set": "set",
        "sat": "sit", "slept": "sleep", "stood": "stand",
        "taught": "teach", "told": "tell", "thought": "think", "wore": "wear",
        "won": "win", "brought": "bring", "bought": "buy", "built": "build",
        "caught": "catch", "felt": "feel", "heard": "hear", "held": "hold",
        "learnt": "learn", "lent": "lend", "led": "lead",
    }
    m = _re.search(
        r'\b(have to|has to|had to|ought to|need to)\s+(\w+)\b',
        excerpt, _re.IGNORECASE,
    )
    if not m:
        m = _re.search(
            r'\b(must|will|would|can|could|shall|should|may|might|have|has|had)'
            r'\s+(\w+)\b',
            excerpt, _re.IGNORECASE,
        )
    if m:
        aux, bad_verb = m.group(1), m.group(2).lower()
        base = _IRREGULAR_TO_BASE.get(bad_verb)
        if not base:
            stripped = _re.sub(r'(ed|ing|en)$', '', bad_verb).rstrip('n')
            if stripped and stripped != bad_verb:
                base = stripped
        if base and base != bad_verb:
            corrected = excerpt.replace(m.group(2), base)
            return (
                f"In '{excerpt}': after '{aux}' use the base (infinitive) form "
                f"→ '{corrected}'"
            )
    return None


def _repair_comparative(excerpt: str) -> Optional[str]:
    """more stronger → stronger; more better → better."""
    m = _re.search(r'\bmore\s+(\w+er)\b', excerpt, _re.IGNORECASE)
    if m:
        adj_er = m.group(1)
        corrected = excerpt.replace(m.group(0), adj_er)
        return (
            f"In '{excerpt}': 'more' + '-er' is redundant — use either "
            f"'{adj_er}' or 'more {adj_er[:-2]}' (not both). → '{corrected}'"
        )
    m2 = _re.search(r'\bso\s+(\w+er)\b', excerpt, _re.IGNORECASE)
    if m2:
        return (
            f"In '{excerpt}': use 'much {m2.group(1)}' or 'far {m2.group(1)}' "
            f"to intensify a comparative — not 'so'."
        )
    return None


def _repair_sva(excerpt: str, issue: str) -> Optional[str]:
    """This make → This makes; they was → they were."""
    _ADJ_SUFFIXES = (
        'ible', 'able', 'ful', 'ous', 'ive', 'al', 'ial',
        'ic', 'ical', 'ent', 'ant', 'ary', 'ory', 'ble',
    )
    m = _re.search(r'\b(this|it|he|she|that)\s+(\w+)\b', excerpt, _re.IGNORECASE)
    if m:
        subj, verb = m.group(1), m.group(2)
        if not verb.lower().endswith(_ADJ_SUFFIXES):
            if not verb.endswith('s') and verb not in ('is', 'was', 'has', 'does'):
                corrected = excerpt.replace(m.group(0), f"{subj} {verb}s")
                return (
                    f"In '{excerpt}': '{subj}' is singular, so the verb needs "
                    f"a third-person singular form → '{corrected}'"
                )
    if issue:
        return f"In '{excerpt}': {issue} Check the subject–verb pair."
    return None


def _repair_article(excerpt: str) -> Optional[str]:
    """a children → the children / children."""
    m_a_pl = _re.search(r'\ba\s+([a-z]+(?:ren|s))\b', excerpt, _re.IGNORECASE)
    if m_a_pl:
        noun = m_a_pl.group(1)
        return (
            f"In '{excerpt}': '{noun}' is plural — use 'the {noun}' (specific) "
            f"or no article (general). Don't use 'a' with plural nouns."
        )
    return None


def _repair_word_form(excerpt: str, issue: str) -> Optional[str]:
    """Another issued is → Another issue is."""
    m = _re.search(r'\b(\w+ed)\b', excerpt)
    if m:
        candidate = m.group(1)
        base = candidate[:-1] if candidate.endswith('ed') else candidate
        return (
            f"In '{excerpt}': '{candidate}' appears to be a verb form used as a "
            f"noun. Try '{base}' (noun form) → e.g. 'Another {base} is...'"
        )
    if issue:
        return f"In '{excerpt}': {issue}"
    return None


def _repair_collocation(excerpt: str) -> Optional[str]:
    """older peoples → older people."""
    if _re.search(r'\bpeoples\b', excerpt, _re.IGNORECASE):
        corrected = _re.sub(r'\bpeoples\b', 'people', excerpt, flags=_re.IGNORECASE)
        return (
            f"In '{excerpt}': 'people' is already plural — remove the 's' "
            f"→ '{corrected}'"
        )
    return None


def _build_personalised_correction(err: dict) -> str:
    """Build a student-facing correction string that references their actual text."""
    family  = (err.get("family") or "").upper()
    excerpt = (err.get("excerpt") or "").strip()
    issue   = (err.get("issue") or "").strip()
    specific: Optional[str] = None
    if family == "VERB_FORM":
        specific = _repair_verb_form(excerpt)
    elif family == "COMPARATIVE_FORM":
        specific = _repair_comparative(excerpt)
    elif family == "SUBJECT_VERB_AGREEMENT":
        specific = _repair_sva(excerpt, issue)
    elif family == "ARTICLE_DETERMINER":
        specific = _repair_article(excerpt)
    elif family == "WORD_FORM":
        specific = _repair_word_form(excerpt, issue)
    elif family == "COLLOCATION":
        specific = _repair_collocation(excerpt)
    if specific:
        return specific
    rule = _CORRECTION_TEMPLATES.get(family, _GENERIC_RULE)
    if excerpt:
        return f"In '{excerpt}': {rule}"
    return rule


# =============================================================================
# SECTION C — LLM CORRECTION  (from v4/v5, + F22 trivial-output check)
# =============================================================================

def _correction_is_trivial(excerpt: str, correction: str) -> bool:
    """
    F22: Return True if the LLM correction is self-contradictory — the suggested
    fix is identical to (or barely changed from) the original excerpt.
    """
    excerpt_clean    = (excerpt or "").strip().lower()
    correction_lower = (correction or "").lower()
    if not excerpt_clean or not correction_lower:
        return False
    if "→" in correction or "->" in correction:
        arrow       = "→" if "→" in correction else "->"
        after_arrow = correction.split(arrow)[-1].strip().lower().strip("'\"` ")
        if after_arrow == excerpt_clean:
            return True
        if excerpt_clean in after_arrow and len(after_arrow) <= len(excerpt_clean) + 8:
            return True
    if len(correction.strip()) < len(excerpt.strip()) + 10:
        return True
    return False


def _llm_correction_for_excerpt_v5(
    excerpt: str,
    error_type: str,
    sentence: str = "",
) -> Optional[str]:
    """
    F14+F22: Per-excerpt LLM correction with trivial-output self-check.
    Returns None if API unavailable, key missing, call fails, or output trivial.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return None
    rule_desc = _RULE_FOR_LLM.get(error_type, "")
    if not rule_desc:
        return None
    try:
        import openai
        client = openai.OpenAI(api_key=api_key)
        prompt = (
            "You are correcting a specific IELTS student error. "
            "Write ONE correction sentence for this exact excerpt.\n\n"
            f"Error type:    {error_type}\n"
            f"Student wrote: \"{excerpt}\"\n"
        )
        if sentence:
            prompt += f"Full sentence:  \"{sentence}\"\n"
        prompt += (
            f"Grammar rule:  {rule_desc}\n\n"
            "Format your answer EXACTLY as:\n"
            f"In '{excerpt}': [specific explanation] → '[corrected form]'\n\n"
            "The corrected form MUST be different from the original. "
            "If you cannot produce a confident specific correction, reply with: null"
        )
        response = client.chat.completions.create(
            model       = os.environ.get("VIP_CHEAP_MODEL", "gpt-4o-mini"),
            messages    = [{"role": "user", "content": prompt}],
            max_tokens  = 120,
            temperature = 0.1,
        )
        text = (response.choices[0].message.content or "").strip()
        if text.lower() == "null" or not text:
            return None
        if _correction_is_trivial(excerpt, text):
            return None
        return text
    except Exception:
        return None


# =============================================================================
# SECTION D — ERRORMAP LOOKUP & ENRICHMENT  (from v4, F13+F15)
# =============================================================================

def _build_errormap_lookup(
    errormap_v3: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """Build {excerpt[:80]: error_entry} from the errormap for fast matching."""
    lookup: Dict[str, Dict] = {}
    for err in errormap_v3.get("errors", []):
        excerpt = (
            (err.get("location") or {}).get("excerpt", "")
            or err.get("excerpt", "")
        )
        key = excerpt[:80]
        if key and key not in lookup:
            lookup[key] = err
    return lookup


def enrich_annotated_errors(
    report: Dict[str, Any],
    errormap_v3: Dict[str, Any],
) -> Dict[str, Any]:
    """
    F13: Add error_type, family, criterion, char offsets, sentence to every
    annotated_error by matching against the v3 errormap.

    F15: After enriching, re-route annotated_errors whose criterion != their
    focus_area criterion to the correct block, then deduplicate by excerpt.

    Mutates report in place. Returns report.
    """
    em_lookup = _build_errormap_lookup(errormap_v3)
    fa_list: List[Dict] = report.get("focus_area_feedback", []) or []

    # Step 1: enrich each annotated_error
    for fa in fa_list:
        for err in fa.get("annotated_errors", []) or []:
            if not isinstance(err, dict):
                continue
            excerpt = (err.get("excerpt") or "")[:80]
            em_err  = em_lookup.get(excerpt)
            if not em_err:
                for key, candidate in em_lookup.items():
                    if excerpt and (
                        key.startswith(excerpt[:30])
                        or excerpt[:30] in key
                    ):
                        em_err = candidate
                        break
            if not em_err:
                continue
            em_loc = em_err.get("location", {})
            e_type = em_err.get("error_type", "")
            crit   = em_err.get("criterion", "")
            if e_type:
                err["error_type"] = e_type
                err["family"]     = e_type
            if crit:
                err["criterion"] = crit
            cs = em_loc.get("char_start", 0)
            ce = em_loc.get("char_end",   0)
            if cs or ce:
                err["char_start"] = cs
                err["char_end"]   = ce
            sentence = em_loc.get("sentence", "")
            if sentence:
                err["sentence"]       = sentence
                err["sentence_index"] = em_loc.get("sentence_index", -1)

    # Step 2 (F15): reroute misclassified errors
    misrouted_errors: List[tuple] = []
    for fa in fa_list:
        fa_criterion = fa.get("criterion", "")
        for err in list(fa.get("annotated_errors", []) or []):
            if not isinstance(err, dict):
                continue
            err_criterion = err.get("criterion", "")
            if err_criterion and err_criterion != fa_criterion:
                misrouted_errors.append((fa, err, err_criterion))

    for (source_fa, err, correct_crit) in misrouted_errors:
        ae = source_fa.get("annotated_errors", [])
        if err in ae:
            ae.remove(err)
        source_fa.setdefault("_rerouted_out", []).append({
            "excerpt":        err.get("excerpt", ""),
            "from_criterion": source_fa.get("criterion", ""),
            "to_criterion":   correct_crit,
        })

    criterion_to_fa: Dict[str, Dict] = {
        fa.get("criterion", ""): fa for fa in fa_list
    }
    for (_, err, correct_crit) in misrouted_errors:
        target_fa = criterion_to_fa.get(correct_crit)
        if target_fa is not None:
            target_fa.setdefault("annotated_errors", []).append(err)

    rerouted_count = len(misrouted_errors)

    # Step 3 (F15): deduplicate by excerpt across all focus areas
    seen_excerpts: Set[str] = set()
    dupes_removed = 0
    for fa in fa_list:
        deduped: List[Dict] = []
        for err in fa.get("annotated_errors", []) or []:
            if not isinstance(err, dict):
                continue
            excerpt_key = (err.get("excerpt") or "")[:80].strip().lower()
            if not excerpt_key:
                deduped.append(err)
                continue
            if excerpt_key in seen_excerpts:
                dupes_removed += 1
                continue
            seen_excerpts.add(excerpt_key)
            deduped.append(err)
        fa["annotated_errors"] = deduped

    report["_fe_v4_enriched"]      = True
    report["_fe_v4_rerouted"]      = rerouted_count
    report["_fe_v4_dupes_removed"] = dupes_removed
    return report


# =============================================================================
# SECTION E — INJECT MISSING ANNOTATED ERRORS  (from v5, F17)
# =============================================================================

_CRITERION_LABELS_E: Dict[str, str] = {
    "grammatical_range_accuracy": "Grammar (GRA)",
    "lexical_resource":           "Vocabulary (LR)",
    "task_achievement":           "Task Achievement (TA)",
    "coherence_cohesion":         "Coherence & Cohesion (CC)",
}


def inject_missing_annotated_errors(
    report: Dict[str, Any],
    errormap_v3: Dict[str, Any],
    directive: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    F17: For focus areas with 0 annotated_errors, pull matching errors from the
    errormap and inject them. Called after enrich_annotated_errors, before
    fill_null_corrections_v5 so the fill step handles injected entries.

    Mutates report in place. Returns report.
    """
    em_errors: List[Dict] = errormap_v3.get("errors", [])
    fa_list:   List[Dict] = report.get("focus_area_feedback", []) or []

    already_annotated: Set[str] = set()
    for fa in fa_list:
        for ae in fa.get("annotated_errors", []) or []:
            if isinstance(ae, dict):
                exc = (ae.get("excerpt") or "")[:80].strip().lower()
                if exc:
                    already_annotated.add(exc)

    injected_total = 0

    for fa in fa_list:
        if len(fa.get("annotated_errors", []) or []) > 0:
            continue
        fa_criterion = fa.get("criterion", "")
        if not fa_criterion:
            continue
        matching: List[Dict] = []
        for em_err in em_errors:
            if em_err.get("criterion") != fa_criterion:
                continue
            loc     = em_err.get("location", {})
            excerpt = (loc.get("excerpt") or em_err.get("excerpt") or "")[:80]
            if excerpt.strip().lower() in already_annotated:
                continue
            matching.append(em_err)

        if not matching:
            fa["_no_errormap_errors"] = True
            continue

        injected: List[Dict] = []
        for em_err in matching:
            loc     = em_err.get("location", {})
            excerpt = loc.get("excerpt") or em_err.get("excerpt") or ""
            e_type  = em_err.get("error_type", "")
            ae = {
                "excerpt":          excerpt,
                "error_type":       e_type,
                "family":           e_type,
                "criterion":        fa_criterion,
                "char_start":       loc.get("char_start", 0),
                "char_end":         loc.get("char_end",   0),
                "sentence":         loc.get("sentence", ""),
                "sentence_index":   loc.get("sentence_index", -1),
                "correction":       None,
                "correction_source": None,
                "_injected":        True,
            }
            injected.append(ae)
            already_annotated.add(excerpt[:80].strip().lower())

        fa["annotated_errors"] = injected
        injected_total += len(injected)

    report["_fe_v5_injected_errors"] = injected_total
    return report


# =============================================================================
# SECTION F — FILL NULL CORRECTIONS  (from v5, F14+F22)
# =============================================================================

def fill_null_corrections_v5(report: Dict[str, Any]) -> Dict[str, Any]:
    """
    F14+F22: Fill annotated_errors with correction=None.

    Fallback chain:
      1. Pattern-based repair (fast, no LLM)
      2. Per-excerpt LLM call with F22 trivial-output self-check
      3. Rule statement with excerpt reference

    Mutates report in place. Returns report.
    """
    filled_rule   = 0
    filled_llm_v5 = 0

    for fa in report.get("focus_area_feedback", []) or []:
        for err in fa.get("annotated_errors", []) or []:
            if not isinstance(err, dict):
                continue
            if bool(err.get("correction")):
                err.setdefault("correction_source", "llm")
                continue

            family   = (err.get("family") or err.get("error_type") or "").upper()
            excerpt  = (err.get("excerpt") or "").strip()
            issue    = (err.get("issue") or "").strip()
            sentence = (err.get("sentence") or "").strip()

            # Step 1: pattern-based repair
            specific: Optional[str] = None
            if family == "VERB_FORM":
                specific = _repair_verb_form(excerpt)
            elif family == "COMPARATIVE_FORM":
                specific = _repair_comparative(excerpt)
            elif family == "SUBJECT_VERB_AGREEMENT":
                specific = _repair_sva(excerpt, issue)
            elif family == "ARTICLE_DETERMINER":
                specific = _repair_article(excerpt)
            elif family == "WORD_FORM":
                specific = _repair_word_form(excerpt, issue)
            elif family == "COLLOCATION":
                specific = _repair_collocation(excerpt)

            if specific:
                err["correction"]        = specific
                err["model_example"]     = specific
                err["correction_source"] = "rule_template"
                filled_rule += 1
                continue

            # Step 2: LLM (with F22 self-check)
            if family in _RULE_FOR_LLM and excerpt:
                llm_correction = _llm_correction_for_excerpt_v5(
                    excerpt, family, sentence
                )
                if llm_correction:
                    err["correction"]        = llm_correction
                    err["model_example"]     = llm_correction
                    err["correction_source"] = "llm_v5"
                    filled_llm_v5 += 1
                    continue

            # Step 3: rule statement
            rule = _CORRECTION_TEMPLATES.get(family, _GENERIC_RULE)
            if excerpt and "Review this carefully" not in rule:
                correction = f"In '{excerpt}': {rule}"
            elif excerpt:
                correction = (
                    f"In '{excerpt}': check the grammar rule that applies here. "
                    f"Consider the sentence structure and word form."
                )
            else:
                correction = rule

            err["correction"]        = correction
            err["model_example"]     = correction
            err["correction_source"] = "rule_statement"
            filled_rule += 1

    report["_fe_v5_corrections_rule"]   = filled_rule
    report["_fe_v5_corrections_llm_v5"] = filled_llm_v5
    report["_adapter_version"]          = _ADAPTER_VERSION
    return report


def enrich_all_corrections(report: Dict[str, Any]) -> Dict[str, Any]:
    """
    F14b: Post-process all annotated_errors — including those already filled by
    the frozen v2 adapter (LR LLM corrections) — to make each correction
    excerpt-specific if it isn't already.

    Mutates report in place. Returns report.
    """
    enriched = 0
    for fa in report.get("focus_area_feedback", []) or []:
        for err in fa.get("annotated_errors", []) or []:
            if not isinstance(err, dict):
                continue
            correction = err.get("correction", "") or ""
            excerpt    = (err.get("excerpt") or "").strip()
            already_specific = (
                excerpt
                and (
                    f"'{excerpt}'" in correction
                    or f'"{excerpt}"' in correction
                    or excerpt.lower()[:25] in correction.lower()
                )
            )
            if already_specific:
                continue
            family   = (err.get("family") or err.get("error_type") or "").upper()
            issue    = (err.get("issue") or "").strip()
            sentence = (err.get("sentence") or "").strip()

            specific: Optional[str] = None
            if family == "VERB_FORM":
                specific = _repair_verb_form(excerpt)
            elif family == "COMPARATIVE_FORM":
                specific = _repair_comparative(excerpt)
            elif family == "SUBJECT_VERB_AGREEMENT":
                specific = _repair_sva(excerpt, issue)
            elif family == "ARTICLE_DETERMINER":
                specific = _repair_article(excerpt)
            elif family == "WORD_FORM":
                specific = _repair_word_form(excerpt, issue)
            elif family == "COLLOCATION":
                specific = _repair_collocation(excerpt)

            if specific:
                err["correction"]        = specific
                err["model_example"]     = specific
                err["correction_source"] = "rule_template_v6"
                enriched += 1
                continue

            if family in _RULE_FOR_LLM and excerpt:
                llm_correction = _llm_correction_for_excerpt_v5(
                    excerpt, family, sentence
                )
                if llm_correction:
                    err["correction"]        = llm_correction
                    err["model_example"]     = llm_correction
                    err["correction_source"] = "llm_v5_enriched"
                    enriched += 1
                    continue

            if excerpt:
                rule = _CORRECTION_TEMPLATES.get(family, "")
                if rule:
                    new_correction = f"In '{excerpt}': {rule}"
                    err["correction"]        = new_correction
                    err["model_example"]     = new_correction
                    err["correction_source"] = "rule_statement_v6"
                    enriched += 1

    report["_fe_v4_enriched_corrections"] = enriched
    return report


# =============================================================================
# SECTION G — SENTENCE CONTEXT + ALL INSTANCES  (from v4, F10)
# =============================================================================

def enrich_with_sentence_context(
    report: Dict[str, Any],
    errormap_v3: Dict[str, Any],
) -> Dict[str, Any]:
    """
    F10-A: Add sentence, sentence_index, char offsets to annotated_errors by
    matching against the v3 errormap. No-op for fields already populated.
    Mutates report in place. Returns report.
    """
    em_lookup = _build_errormap_lookup(errormap_v3)
    for fa in report.get("focus_area_feedback", []) or []:
        for err in fa.get("annotated_errors", []) or []:
            if not isinstance(err, dict):
                continue
            if err.get("sentence"):
                continue
            excerpt = (err.get("excerpt") or "")[:80]
            em_err  = em_lookup.get(excerpt)
            if not em_err:
                continue
            loc = em_err.get("location", {})
            err["sentence"]       = loc.get("sentence", "")
            err["sentence_index"] = loc.get("sentence_index", -1)
            if not err.get("char_start"):
                err["char_start"] = loc.get("char_start", 0)
                err["char_end"]   = loc.get("char_end",   0)
    return report


def expand_all_error_instances(
    report: Dict[str, Any],
    errormap_v3: Dict[str, Any],
) -> Dict[str, Any]:
    """
    F10-B: Add all_instances[] to each focus_area_feedback block.
    Contains ALL occurrences of that criterion's errors from the errormap,
    not just the representative shown in annotated_errors.
    Mutates report in place. Returns report.
    """
    em_errors = errormap_v3.get("errors", [])
    correction_by_excerpt: Dict[str, str] = {}
    for fa in report.get("focus_area_feedback", []) or []:
        for err in fa.get("annotated_errors", []) or []:
            exc  = (err.get("excerpt") or "")[:80]
            corr = err.get("correction", "")
            if exc and corr:
                correction_by_excerpt[exc] = corr

    for fa in report.get("focus_area_feedback", []) or []:
        fa_criterion = fa.get("criterion", "")
        matching_errors = [
            e for e in em_errors if e.get("criterion") == fa_criterion
        ]
        instances: List[Dict[str, Any]] = []
        for n, em_err in enumerate(matching_errors, start=1):
            loc     = em_err.get("location", {})
            excerpt = loc.get("excerpt", em_err.get("excerpt", ""))
            instances.append({
                "instance_n":     n,
                "family":         em_err.get("error_type", ""),
                "excerpt":        excerpt,
                "sentence":       loc.get("sentence", ""),
                "sentence_index": loc.get("sentence_index", -1),
                "char_start":     loc.get("char_start", 0),
                "char_end":       loc.get("char_end", 0),
                "correction":     correction_by_excerpt.get(excerpt[:80], ""),
            })
        fa["all_instances"] = instances
    return report


# =============================================================================
# SECTION H — BROKEN SENTENCES  (from v4/v5, F11+F19, + F28-B7)
# =============================================================================

_COMBO_EXPLANATIONS: Dict[frozenset, str] = {
    frozenset({"VERB_FORM", "CLAUSE_STRUCTURE"}):
        "Missing or incorrect verb form breaks the clause structure — "
        "the examiner cannot determine the predicate.",
    frozenset({"SUBJECT_VERB_AGREEMENT", "VERB_FORM"}):
        "Both the subject–verb agreement and the verb form are wrong. "
        "Correct the verb form first.",
    frozenset({"PREPOSITION", "VERB_FORM"}):
        "Incorrect preposition use combined with a verb form error "
        "makes the intended meaning unclear.",
    frozenset({"PREPOSITION", "CLAUSE_STRUCTURE"}):
        "The clause structure is incomplete and the preposition is wrong — "
        "the examiner cannot establish the subject–verb relationship.",
    frozenset({"VERB_FORM", "CLAUSE_STRUCTURE", "PREPOSITION"}):
        "Overlapping errors in verb form, preposition, and clause structure "
        "make this sentence unrecoverable without rewriting.",
    frozenset({"COLLOCATION", "SUBJECT_VERB_AGREEMENT"}):
        "Non-standard word use combined with agreement errors makes "
        "the intended meaning hard to recover.",
    frozenset({"COMPARATIVE_FORM", "CLAUSE_STRUCTURE"}):
        "The comparative construction and clause structure are both wrong — "
        "the relationship being expressed is unclear.",
    frozenset({"WORD_FORM", "CLAUSE_STRUCTURE"}):
        "An incorrect word form (noun/verb/adjective) compounds the "
        "clause structure problem — the predicate cannot be identified.",
}


def _build_combo_explanation(families: List[str]) -> str:
    """
    Generate explanation from family combination or fallback.
    F28-B7: uses grammatically correct singular/plural for the count word.
    """
    family_set = frozenset(families[:3])
    for combo, explanation in _COMBO_EXPLANATIONS.items():
        if combo.issubset(family_set):
            return explanation
    family_labels = [f.replace("_", " ").title() for f in families[:4]]
    joined = ", ".join(family_labels)
    count  = len(families)
    # F28-B7: "error" singular when count == 1
    error_word = "error" if count == 1 else "errors"
    return (
        f"This sentence has {count} overlapping {error_word} ({joined}). "
        f"Each error compounds the others — the sentence needs full rewriting."
    )


def _build_rewrite_prompt(sentence_text: str) -> str:
    """Short actionable rewrite instruction based on sentence length."""
    word_count = len(sentence_text.split())
    if word_count >= 25:
        return (
            "This sentence is trying to say too much at once. "
            "Break it into two: one sentence for the main idea, "
            "one for the supporting detail."
        )
    return (
        "Rewrite this as a simple Subject + Verb + Object sentence first, "
        "then add one piece of detail."
    )


def _get_families_from_errormap(
    sent_char_start: int,
    sent_char_end: int,
    errormap_errors: List[Dict],
) -> List[str]:
    """
    F19: Find error_type values for errors whose char range overlaps this sentence.
    Overlap condition: error.char_start < sent_char_end AND error.char_end > sent_char_start.
    """
    families: List[str] = []
    use_char = sent_char_start > 0 or sent_char_end > 0
    for err in errormap_errors:
        loc = err.get("location", {})
        es  = loc.get("char_start", -1)
        ee  = loc.get("char_end",   -1)
        et  = err.get("error_type") or err.get("family") or ""
        if not et:
            continue
        if use_char and es >= 0 and ee > 0:
            if es < sent_char_end and ee > sent_char_start:
                if et not in families:
                    families.append(et)
    return families


def build_broken_sentences_section_v5(
    broken_sentences_raw: List[Dict[str, Any]],
    errormap_v3: Dict[str, Any],
) -> Dict[str, Any]:
    """
    F11+F19: Build the broken_sentences section from errormap broken_sentences_raw.
    Cross-references char ranges to find actual overlapping error families (F19).
    Returns dict with schema "BROKEN_SENTENCES_V2".
    """
    if not broken_sentences_raw:
        return {
            "schema":       "BROKEN_SENTENCES_V2",
            "count":        0,
            "sentences":    [],
            "student_note": None,
        }

    em_errors: List[Dict] = errormap_v3.get("errors", [])
    sentences: List[Dict[str, Any]] = []

    for raw in broken_sentences_raw:
        severity     = raw.get("severity", "moderate")
        raw_families = raw.get("error_families", [])
        char_start   = raw.get("char_start", 0)
        char_end     = raw.get("char_end",   0)

        crossref_families = _get_families_from_errormap(
            char_start, char_end, em_errors
        )
        if crossref_families:
            families = crossref_families
        elif raw_families:
            families = raw_families
        else:
            sent_idx = raw.get("sentence_index", -1)
            families = []
            if sent_idx >= 0:
                for err in em_errors:
                    loc = err.get("location", {})
                    if loc.get("sentence_index") == sent_idx:
                        et = err.get("error_type") or ""
                        if et and et not in families:
                            families.append(et)

        if severity == "moderate" and len(families) < 3:
            continue

        sent_text      = raw.get("sentence_text", "")
        explanation    = _build_combo_explanation(families)
        rewrite_prompt = _build_rewrite_prompt(sent_text)

        sentences.append({
            "sentence_index":  raw.get("sentence_index", -1),
            "sentence_text":   sent_text,
            "char_start":      char_start,
            "char_end":        char_end,
            "recoverability":  raw.get("recoverability_score", 0.0),
            "severity":        severity,
            "error_families":  families,
            "explanation":     explanation,
            "rewrite_prompt":  rewrite_prompt,
            "_family_source":  (
                "crossref" if crossref_families else
                ("detector" if raw_families else "sentence_index")
            ),
        })

    count = len(sentences)
    student_note = None
    if count >= 3:
        student_note = (
            f"⚠️  {count} sentences in your essay have overlapping errors that make "
            "them very hard to follow. Focus on these first — fixing them will have "
            "the biggest impact on your band score."
        )
    elif count > 0:
        student_note = (
            f"{count} sentence{'s' if count > 1 else ''} in your essay "
            f"need{'s' if count == 1 else ''} significant rewriting "
            "before other improvements will have full effect."
        )

    return {
        "schema":       "BROKEN_SENTENCES_V2",
        "count":        count,
        "sentences":    sentences,
        "student_note": student_note,
    }


# =============================================================================
# SECTION I — FOCUS AREA EXPLANATIONS  (from v5, + F29 internal text filter)
# =============================================================================

_CRIT_LABEL: Dict[str, str] = {
    "grammatical_range_accuracy": "Grammar accuracy",
    "lexical_resource":           "Vocabulary range",
    "task_achievement":           "Task achievement",
    "coherence_cohesion":         "Coherence and cohesion",
}

_PRIORITY_SENTENCES: Dict[str, str] = {
    "recurring_error":    "This area has come up as a weakness in multiple sessions — it needs consistent attention.",
    "high_impact_gap":    "Improving this area will have the biggest direct effect on your band score.",
    "new_weakness":       "This area was flagged for the first time in this session.",
    "secondary_limiter":  "This is a secondary area limiting your score after your main weakness.",
}

# F29: markers that identify internal diagnostic text from FE v6.4
_INTERNAL_TEXT_MARKERS: List[str] = [
    "repeated detector rows",
    "detector rows map to",
    "this target is selected because",
    "error families:",
    "sessions_flagged",
    "criterion errors",
]

# F29: student-friendly LR explanation templates by priority_reason
_LR_EXPLANATION_TEMPLATES: Dict[str, str] = {
    "recurring_error": (
        "Your vocabulary choice has been flagged in multiple sessions. "
        "The main pattern is using words in combinations that sound unnatural "
        "in English — this is called a collocation error. For example, some "
        "word pairings that feel correct in your first language don't work the "
        "same way in English. Fixing this consistently will move your score up."
    ),
    "high_impact_gap": (
        "Your vocabulary range is the biggest gap between your current band and "
        "your target. You are using mostly common, simple words where a wider "
        "range of precise academic vocabulary is expected. Focus on using words "
        "in their correct combinations and choosing more accurate terms."
    ),
    "new_weakness": (
        "Your vocabulary was flagged for the first time in this session. "
        "Look at the specific examples in the error analysis above — these show "
        "the kind of word combination issues to avoid."
    ),
    "secondary_limiter": (
        "Your vocabulary range is a secondary factor limiting your score. "
        "After addressing your main weakness, focus on using more precise "
        "academic collocations and word forms."
    ),
}
_LR_EXPLANATION_DEFAULT = (
    "Your vocabulary range and precision need development. "
    "Focus on using natural word combinations and precise academic vocabulary "
    "to show the examiner a wider range of language."
)

# F29: GRA student-friendly templates
_GRA_EXPLANATION_TEMPLATES: Dict[str, str] = {
    "recurring_error": (
        "Grammar accuracy errors have appeared in several of your sessions. "
        "The same patterns keep recurring — this suggests a rule that hasn't "
        "been fully internalised yet. Check the specific error types in the "
        "detailed feedback above and focus on those rules in your practice."
    ),
    "high_impact_gap": (
        "Grammar accuracy is your biggest gap from your target band. "
        "The examiner awards GRA marks for both the range of structures you use "
        "and the accuracy of your sentences. Fixing the recurring error types "
        "shown above will have the most direct effect on your score."
    ),
    "new_weakness": (
        "Grammar accuracy was flagged this session. Review the specific errors "
        "highlighted above and practise the relevant rules before your next essay."
    ),
}
_GRA_EXPLANATION_DEFAULT = (
    "Grammar accuracy needs improvement. Focus on the specific error types "
    "identified in your detailed feedback — subject–verb agreement, verb forms, "
    "and sentence structure are the most common sources of GRA errors."
)


def _is_internal_text(text: str) -> bool:
    """
    F29: Return True if text contains internal FE diagnostic markers that should
    not be shown to students.
    """
    tl = (text or "").lower()
    return any(marker.lower() in tl for marker in _INTERNAL_TEXT_MARKERS)


def _get_fe_priority_for_criterion(
    criterion: str,
    skill_tag: str,
    fe_priorities: List[Dict],
) -> Optional[Dict]:
    """Find the FE top_learning_priority entry that best matches this focus area."""
    crit_keywords: Dict[str, List[str]] = {
        "grammatical_range_accuracy": [
            "GRAM", "ARTICLE", "VERB", "SVA", "CLAUSE",
            "COMPARATIVE", "COMPARATIVE_FORM",
        ],
        "lexical_resource": [
            "LEXIC", "VOCAB", "COLLOC", "WORD", "QUANTITY",
        ],
        "task_achievement": [
            "TASK", "ARGUMENT", "POSITION", "CLAIM",
        ],
        "coherence_cohesion": [
            "COHER", "COHES", "PARAGRAPH", "TRANSIT", "REFERENCE",
        ],
    }
    keywords = crit_keywords.get(criterion, [])
    for p in fe_priorities:
        tid = (p.get("target_id") or "").upper()
        if any(k in tid for k in keywords):
            return p
    return None


def _get_student_explanation(
    criterion: str,
    priority_reason: str,
    current_band: Optional[float],
    target_band: Optional[float],
    skill_tag: str,
    crit_label: str,
) -> str:
    """
    F29: Generate a student-friendly explanation when FE text is internal or missing.
    """
    band_str = (
        f" (currently Band {current_band:.1f}"
        + (f" → target {target_band:.1f}" if target_band else "")
        + ")"
        if current_band else ""
    )
    if criterion == "lexical_resource":
        template = _LR_EXPLANATION_TEMPLATES.get(
            priority_reason, _LR_EXPLANATION_DEFAULT
        )
        return template
    if criterion == "grammatical_range_accuracy":
        template = _GRA_EXPLANATION_TEMPLATES.get(
            priority_reason, _GRA_EXPLANATION_DEFAULT
        )
        return template
    # Generic for TA / CC
    skill_str = (
        f" Focus: {skill_tag.replace('_', ' ').title()}."
        if skill_tag else ""
    )
    priority_map = {
        "recurring_error":
            f"Your {crit_label}{band_str} has been a recurring weakness."
            f"{skill_str} Consistent improvement here will raise your overall score.",
        "high_impact_gap":
            f"Improving your {crit_label}{band_str} will have the biggest direct "
            f"effect on your band score.{skill_str}",
        "new_weakness":
            f"Your {crit_label} was flagged for the first time this session."
            f"{skill_str}",
    }
    return priority_map.get(
        priority_reason,
        f"Your {crit_label}{band_str} needs work.{skill_str}",
    )


def populate_focus_area_explanations_v6(
    report: Dict[str, Any],
    fe_bundle: Dict[str, Any],
    directive: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    F18+F21+F29: Fill explanation, priority_note, and difficulty on every
    focus area.

    Sources (in priority order):
      explanation:
        1. main_score_limiter.explanation — if criterion matches MSL rubric
           (only if NOT internal text — F29)
        2. top_learning_priorities[i].why_this_matters — if target_id matches
           (only if NOT internal text — F29)
        3. Student-friendly template from _get_student_explanation() — F29
           (always used as fallback when text is internal or missing)

      priority_note:
        1. main_score_limiter.first_action — for primary criterion
        2. _PRIORITY_SENTENCES[priority_reason] — from directive
        3. Generic fallback

      difficulty (F21):
        - directive.focus_areas[i].recommended_difficulty

    Mutates report in place. Returns report.
    """
    sf       = fe_bundle.get("student_feedback", {})
    msl      = sf.get("main_score_limiter", {})
    fe_prios = sf.get("top_learning_priorities", []) or []

    dir_fas: Dict[str, Dict] = {}
    if directive:
        for dfa in directive.get("focus_areas", []) or []:
            c = dfa.get("criterion", "")
            if c:
                dir_fas[c] = dfa

    msl_rubric = (msl.get("rubric") or "").upper()
    _rubric_to_crit = {
        "GRA": "grammatical_range_accuracy",
        "LR":  "lexical_resource",
        "TA":  "task_achievement",
        "CC":  "coherence_cohesion",
    }
    _crit_to_rubric = {v: k for k, v in _rubric_to_crit.items()}

    explanations_set = 0

    for fa in report.get("focus_area_feedback", []) or []:
        criterion  = fa.get("criterion", "")
        skill_tag  = fa.get("skill_tag", "")
        crit_label = _CRIT_LABEL.get(criterion, criterion)

        # Bands from fa, then directive
        current_band = fa.get("current_band") or (
            (fa.get("score_summary") or {}).get("criteria_bands", {}).get(criterion)
        )
        target_band  = fa.get("target_band")
        dfa          = dir_fas.get(criterion, {})
        if not current_band:
            current_band = dfa.get("current_band")
        if not target_band:
            target_band  = dfa.get("target_band")
        priority_reason = dfa.get("priority_reason", "")

        # --- explanation ---
        explanation = fa.get("explanation") or ""
        if not explanation:
            fa_rubric = _crit_to_rubric.get(criterion)
            # 1. MSL match (only if not internal text)
            if fa_rubric and fa_rubric == msl_rubric:
                candidate = msl.get("explanation", "")
                if candidate and not _is_internal_text(candidate):
                    explanation = candidate

            # 2. FE top_learning_priorities match (only if not internal text)
            if not explanation:
                fe_prio = _get_fe_priority_for_criterion(
                    criterion, skill_tag, fe_prios
                )
                if fe_prio:
                    candidate = fe_prio.get("why_this_matters", "")
                    if candidate and not _is_internal_text(candidate):
                        explanation = candidate

            # 3. Student-friendly template (F29 — always fires when above failed)
            if not explanation:
                explanation = _get_student_explanation(
                    criterion, priority_reason,
                    current_band, target_band,
                    skill_tag, crit_label,
                )

        fa["explanation"] = explanation

        # --- priority_note ---
        priority_note = fa.get("priority_note") or ""
        if not priority_note:
            fa_rubric = _crit_to_rubric.get(criterion)
            if fa_rubric and fa_rubric == msl_rubric:
                priority_note = msl.get("first_action", "")
            if not priority_note and priority_reason:
                priority_note = _PRIORITY_SENTENCES.get(priority_reason, "")
            if not priority_note:
                priority_note = (
                    f"Work on {crit_label} to move closer to your target band."
                )
        fa["priority_note"] = priority_note

        # --- difficulty (F21) ---
        if not fa.get("difficulty"):
            diff = dfa.get("recommended_difficulty", "")
            if diff:
                fa["difficulty"] = diff

        # --- sessions_flagged ---
        if not fa.get("sessions_flagged") and dfa.get("sessions_flagged"):
            fa["sessions_flagged"] = dfa["sessions_flagged"]

        if explanation:
            explanations_set += 1

    report["_fe_v5_explanations_set"] = explanations_set
    report["_adapter_version"]        = _ADAPTER_VERSION
    return report


# =============================================================================
# SECTION J — HEADLINE SANITIZER  (from v5, F20, + F30 essay-specific bands)
# =============================================================================

_HEADLINE_ARTIFACT_RE = _re.compile(
    r"\.\s+Repeated\s+[^.]+\.\s+",
    _re.IGNORECASE,
)

_RUBRIC_TO_CRIT_FULL: Dict[str, str] = {
    "GRA": "grammatical_range_accuracy",
    "LR":  "lexical_resource",
    "TA":  "task_achievement",
    "CC":  "coherence_cohesion",
}

_CRIT_LABEL_SHORT: Dict[str, str] = {
    "grammatical_range_accuracy": "grammar accuracy",
    "lexical_resource":           "vocabulary range",
    "task_achievement":           "task achievement",
    "coherence_cohesion":         "coherence and cohesion",
}


def sanitize_score_summary_v6(
    report: Dict[str, Any],
    fe_bundle: Dict[str, Any],
    band_scores: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    F20+F30: Clean the score_summary headline.

    F20: Remove the 'Repeated grammar accuracy.' concatenation artifact from the
    frozen FE v6.4 output. Reconstructs headline from main_score_limiter.

    F30: When band_scores is supplied, enriches the headline with the actual
    criterion band so each essay produces a distinct, accurate headline.
    Example output: "Your grammar accuracy is at Band 4.0 — the main gap in
    your score. Focus on subject–verb agreement and verb form patterns."

    Mutates report in place. Returns report.
    """
    ss       = report.get("score_summary") or {}
    headline = ss.get("headline_message", "") or ""

    if not headline:
        return report

    original = headline

    sf          = fe_bundle.get("student_feedback", {})
    msl         = sf.get("main_score_limiter", {})
    msl_title   = (msl.get("title") or "").strip().rstrip(".")
    first_action = (msl.get("first_action") or "").strip()
    msl_rubric   = (msl.get("rubric") or "").upper()

    # F30: use actual band when available
    if band_scores and msl_rubric:
        crit_key  = _RUBRIC_TO_CRIT_FULL.get(msl_rubric, "")
        crit_band = (
            band_scores.get("criteria_scores", {})
                       .get(crit_key, {})
                       .get("band")
        )
        holistic  = band_scores.get("holistic_band")
        crit_name = _CRIT_LABEL_SHORT.get(crit_key, msl_rubric.lower())

        if crit_band is not None:
            action = (first_action or "Focus on the patterns that repeat most often.").rstrip(".")
            clean_headline = (
                f"Your {crit_name} is at Band {crit_band:.1f}. "
                f"{action}."
            )
        elif msl_title and first_action:
            clean_headline = f"{msl_title}. {first_action}"
        else:
            clean_headline = _strip_artifact(headline)
    elif msl_title and first_action:
        clean_headline = f"{msl_title}. {first_action}"
    else:
        clean_headline = _strip_artifact(headline)

    ss["headline_message"]   = clean_headline
    ss["_headline_original"] = original
    report["score_summary"]  = ss
    report["_fe_v5_headline_sanitized"] = (clean_headline != original)
    return report


def _strip_artifact(headline: str) -> str:
    """Remove the 'Repeated <X>.' artifact and rejoin cleanly."""
    if _HEADLINE_ARTIFACT_RE.search(headline):
        parts = _HEADLINE_ARTIFACT_RE.split(headline)
        result = ". ".join(p.strip().rstrip(".") for p in parts if p.strip())
        if not result.endswith("."):
            result += "."
        return result
    return headline
