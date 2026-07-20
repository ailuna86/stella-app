#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
feedback_engine_v6_4.py
VA English — Standalone Feedback Engine V6.4
June 2026

STANDALONE — no imports from other feedback engine files.
All data maps, rules, and logic are defined here.

SOURCES INCORPORATED
────────────────────
00  manifest              → hard boundary rules
01  output_sections       → required section list + constraints
02  student_label_map     → per-skill student labels + school_student_version
03  limiter_templates     → per-skill plain_title/explanation/why/first_action
04  priority_rules        → top-3 only, translate targets, explain score relevance
05  evidence_selection    → root-over-symptom, max 3, simplification rules
06  strength_rules        → allowed strength types, templates, fallback
07  improvement_strategy  → do_first / do_next / avoid_for_now per skill
08  practice_routing      → practice_route_id, exercise_family, difficulty_hint
09  tone_readability      → banned phrases, max 24 words/sentence
10  teacher_debug         → teacher_debug_view fields
11  output_schema         → required output fields (student_feedback + practice_routing + teacher_debug)
12  input_adapter         → required PE fields, fallbacks
13  target_validation     → per-target allowed families, min examples, downgrade/reroute
14  semantic_domain       → COST_SPENDING_DOMAIN, valid_determiner_constructions
15  priority_merge        → merge groups, overlap ratios, preferred targets
16  quality_gates         → 8 presentation-layer auto-repair gates
17  llm_policy            → disabled by default; allowed/forbidden ops

V6 ADDITIONS
────────────
V6.1  dual-report structure: short_report (30s) + detailed_report (full profile)
V6.2  actionable family titles: _family_action_title()
V6.engine  A2-B1 language: WHAT_A2B1, WHY_SCORE_A2B1, HOW_TO_FIX_A2B1, MarkdownRenderer

HARD BOUNDARY RULES (file 00)
──────────────────────────────
• Never re-evaluate the essay independently.
• Never create errors not present in PE output.
• Never invent band scores.
• Use PE rankings, evidence rows, targets as source of truth.
• Translate internal labels before showing to student.

USAGE
─────
    python feedback_engine_v6_4.py -i priority_out_4_4.json -o bundles.json \\
        --markdown report.md --validate --summary
"""

from __future__ import annotations

import json
import re
import sys
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

FEEDBACK_ENGINE_VERSION = "feedback_engine_v6.4"
FEEDBACK_BUNDLE_SCHEMA  = "FEEDBACK_ENGINE_OUTPUT_V6_4"
PE_INPUT_CONTRACT       = "priority_engine_output_v4"

META_SKILLS = {"DISCOURSE_EVALUABILITY", "MEANING_RECOVERABILITY"}


# =============================================================================
# PART 1 — STUDENT LABEL TRANSLATIONS  (file 02)
# =============================================================================

STUDENT_LABELS: Dict[str, Dict[str, str]] = {
    "SEMANTIC_EVALUABILITY": {
        "student_label":         "Sentence clarity",
        "school_student_version": "Your reader may understand your topic, but some sentences are confusing. First, make each sentence express one clear idea.",
        "avoid_words": ["semantic", "evaluability", "discourse reliability"],
    },
    "MEANING_RECOVERABILITY": {
        "student_label":         "Clear meaning",
        "school_student_version": "Some sentences need to be simpler and clearer.",
    },
    "DISCOURSE_EVALUABILITY": {
        "student_label":         "Essay logic (diagnostic)",
        "school_student_version": "Before improving paragraph logic, first make your sentences clearer.",
    },
    "GRAMMAR_CONTROL": {
        "student_label":         "Grammar accuracy",
        "school_student_version": "You need more control over basic sentence grammar, especially the forms that appear many times.",
    },
    "SENTENCE_CONSTRUCTION": {
        "student_label":         "Sentence building",
        "school_student_version": "Practise building complete sentences with a clear subject, verb, and idea.",
    },
    "LEXICAL_CONTROL": {
        "student_label":         "Word choice and combinations",
        "school_student_version": "Do not only learn difficult words. Practise using common words in correct combinations.",
    },
    "COHERENCE_CONTROL": {
        "student_label":         "Paragraph and idea flow",
        "school_student_version": "Make sure every sentence clearly supports the paragraph's main idea.",
    },
    "IDEA_DEVELOPMENT": {
        "student_label":         "Idea support",
        "school_student_version": "Do not only state your idea. Explain why it is true and give a clear example.",
    },
    "TASK_FULFILMENT": {
        "student_label":         "Answering the question",
        "school_student_version": "Make sure your essay answers exactly what the question asks.",
    },
}

RUBRIC_LABELS: Dict[str, str] = {
    "GRA":  "Grammar",
    "LR":   "Vocabulary",
    "CC":   "Coherence and Cohesion",
    "TR":   "Task Response",
    "META": "Sentence Clarity",
}

META_RUBRIC_LABEL: Dict[str, str] = {"META": "Sentence Clarity"}


# =============================================================================
# PART 2 — LIMITER EXPLANATION TEMPLATES  (file 03)
# =============================================================================

LIMITER_TEMPLATES: Dict[str, Dict[str, str]] = {
    "SEMANTIC_EVALUABILITY": {
        "plain_title":    "Your main problem is sentence clarity.",
        "main_explanation": "Some sentences are difficult to understand because grammar and word choice break the meaning. The reader may not be sure what you are trying to say.",
        "why_it_matters": "In IELTS writing, clear meaning comes before advanced vocabulary or complex arguments. If the sentence meaning is unclear, the examiner cannot fully reward your ideas.",
        "first_action":   "Practise rewriting unclear sentences into one simple, correct sentence before trying to make them more advanced.",
        "example_intro":  "These examples show where meaning becomes difficult to follow:",
    },
    "GRAMMAR_CONTROL": {
        "plain_title":    "Your main problem is repeated grammar accuracy.",
        "main_explanation": "The same types of grammar mistakes appear several times. They make otherwise understandable ideas look less controlled.",
        "why_it_matters": "IELTS rewards accuracy and control. Repeated grammar mistakes can keep the grammar score lower even if the essay has good ideas.",
        "first_action":   "Focus on the grammar patterns that repeat most often, not every small mistake.",
        "example_intro":  "These examples show the grammar pattern to practise:",
    },
    "LEXICAL_CONTROL": {
        "plain_title":    "Your main problem is word choice and word combinations.",
        "main_explanation": "Some words are not wrong by themselves, but they do not fit naturally with the other words in the sentence.",
        "why_it_matters": "IELTS Lexical Resource rewards precise and natural word use. Unnatural combinations reduce clarity and make the writing sound translated.",
        "first_action":   "Practise useful word combinations from your essay topic instead of memorising isolated advanced words.",
        "example_intro":  "These examples show word combinations to repair:",
    },
    "SENTENCE_CONSTRUCTION": {
        "plain_title":    "Your main problem is sentence building.",
        "main_explanation": "Some sentences are incomplete or incorrectly connected. This makes the message difficult to follow.",
        "why_it_matters": "Complex sentences help only when they are controlled. Broken sentence structure can reduce both grammar and clarity.",
        "first_action":   "Practise building complete sentences with subject + verb + object/complement first.",
        "example_intro":  "These examples show sentences that need rebuilding:",
    },
    "COHERENCE_CONTROL": {
        "plain_title":    "Your main problem is idea flow.",
        "main_explanation": "The reader can see your topic, but the ideas do not always connect smoothly.",
        "why_it_matters": "IELTS Coherence and Cohesion rewards clear progression. Each idea should lead naturally to the next.",
        "first_action":   "Before adding more ideas, practise connecting each sentence to the paragraph's main point.",
        "example_intro":  "These examples show places where idea flow needs repair:",
    },
    "IDEA_DEVELOPMENT": {
        "plain_title":    "Your main problem is developing ideas.",
        "main_explanation": "Some points are stated but not explained enough. The reader needs more reasoning or examples.",
        "why_it_matters": "IELTS Task Response rewards clear, extended, well-supported ideas.",
        "first_action":   "For each main idea, add one explanation sentence and one example or result sentence.",
        "example_intro":  "These examples show ideas that need more support:",
    },
    "TASK_FULFILMENT": {
        "plain_title":    "Your main problem is answering the task fully.",
        "main_explanation": "The essay may not fully cover what the question asks.",
        "why_it_matters": "If the essay does not answer all parts of the question, other strengths cannot fully compensate.",
        "first_action":   "Underline each part of the question and check that each one is answered in the essay.",
        "example_intro":  "These examples show task-response issues:",
    },
    "_fallback": {
        "plain_title":    "Your main problem is the highest-priority area from the analysis.",
        "main_explanation": "This area appears repeatedly in the evidence and should be improved first.",
        "why_it_matters": "Fixing repeated high-impact problems usually gives faster improvement than correcting isolated mistakes.",
        "first_action":   "Start with the top practice target shown below.",
        "example_intro":  "These examples show what to practise:",
    },
}


# =============================================================================
# PART 3 — TARGET ID → STUDENT TITLE  (files 04, 13)
# =============================================================================

TARGET_TITLES: Dict[str, str] = {
    # Semantic / sentence clarity
    "MEANING_RECOVERY_FIRST":           "Make unclear sentences clear",
    "SKILL_SEMANTIC_EVALUABILITY":      "Make unclear sentences clear",
    "SEMANTIC_EVALUABILITY":            "Make unclear sentences clear",
    "CLAUSE_BOUNDARY_CONTROL":          "Make sentences clear and well-formed",
    "SENTENCE_CONSTRUCTION":            "Build complete sentences correctly",
    # Grammar
    "GRAMMAR_CONTROL":                  "Fix repeated grammar mistakes",
    "VERB_FORM_PATTERN_CONTROL":        "Use correct verb forms after modals and patterns",
    "MODAL_BASE_VERB_CONTROL":          "Use the base verb after modal verbs",
    "VERB_PATTERN_GERUND_INFINITIVE_CONTROL": "Use gerund and infinitive patterns correctly",
    "SUBJECT_VERB_AGREEMENT_CONTROL":   "Make subjects and verbs agree",
    "COMPARATIVE_STRUCTURE_CONTROL":    "Use comparative forms correctly",
    "QUANTITY_EXPRESSIONS":             "Use quantity and comparison phrases correctly",
    # Articles / nouns
    "ARTICLE_NOUN_CONTROL":             "Use articles and noun forms correctly",
    # Vocabulary
    "LEXICAL_CONTROL":                  "Improve word choice and word combinations",
    "ABSTRACT_NOUN_COLLOCATIONS":       "Use natural word combinations",
    "CHANGE_COST_EXPRESSIONS":          "Use natural word combinations",
    "LEXICAL_WORD_CHOICE_PRECISION":    "Choose more precise words",
    "COLLOCATION_PHRASE_CONTROL":       "Build natural word combinations",
    "SPELLING_ACCURACY":                "Improve spelling accuracy",
    "SPELLING_WORD_FORM_ACCURACY":      "Improve spelling and word-form accuracy",
    # Coherence
    "REFERENCE_AND_TRANSITION_CONTROL": "Connect ideas with clear transitions",
    "COHERENCE_CONTROL":                "Organise paragraphs and ideas clearly",
    # Task response
    "IDEA_DEVELOPMENT":                 "Explain and support ideas more fully",
    "TASK_FULFILMENT":                  "Answer all parts of the task clearly",
}


# =============================================================================
# PART 4 — IMPROVEMENT STRATEGY RULES  (file 07)
# =============================================================================

IMPROVEMENT_STRATEGY_RULES: Dict[str, Dict[str, str]] = {
    "SEMANTIC_EVALUABILITY": {
        "do_first":    "Rewrite unclear sentences into simple, correct sentences.",
        "do_next":     "Practise the repeated grammar or word-combination pattern that causes unclear meaning.",
        "avoid_for_now": "Do not focus on advanced vocabulary or complex linking words until your sentences are clear.",
    },
    "GRAMMAR_CONTROL": {
        "do_first":    "Practise only the grammar pattern that appears most often.",
        "do_next":     "Use the correct pattern in new IELTS-topic sentences.",
        "avoid_for_now": "Do not try to correct every grammar topic at once.",
    },
    "LEXICAL_CONTROL": {
        "do_first":    "Practise natural word combinations from your essay topic.",
        "do_next":     "Replace translated phrases with simple, natural English combinations.",
        "avoid_for_now": "Do not memorise rare advanced words before you can control common combinations.",
    },
    "SENTENCE_CONSTRUCTION": {
        "do_first":    "Practise building one complete sentence at a time: subject + verb + object.",
        "do_next":     "Try joining two short sentences with a simple connector: 'and', 'but', 'so'.",
        "avoid_for_now": "Do not try to write complex sentences until simple ones are accurate.",
    },
    "IDEA_DEVELOPMENT": {
        "do_first":    "Add one explanation sentence after each claim.",
        "do_next":     "Add one concrete example or consequence.",
        "avoid_for_now": "Do not add more claims without explaining them.",
    },
    "TASK_FULFILMENT": {
        "do_first":    "Underline each part of the question. Check your essay answers each one.",
        "do_next":     "Make your position or view clear in the introduction.",
        "avoid_for_now": "Do not write more content before checking you have answered all parts.",
    },
    "COHERENCE_CONTROL": {
        "do_first":    "Connect each sentence clearly to the paragraph's main idea.",
        "do_next":     "Add a transition word between paragraphs.",
        "avoid_for_now": "Do not add more content before your current paragraphs are clear.",
    },
    "_fallback": {
        "do_first":    "Start with the most important grammar or vocabulary pattern found in your essay.",
        "do_next":     "Use the corrected pattern in new sentences about the same topic.",
        "avoid_for_now": "Do not try to improve every area at the same time.",
    },
}


# =============================================================================
# PART 5 — PRACTICE ROUTES  (file 08)
# =============================================================================

PRACTICE_ROUTES: Dict[str, Dict[str, Any]] = {
    "MEANING_RECOVERY_FIRST":           {"practice_route_id": "SENTENCE_CLARITY_REPAIR",         "exercise_family": "sentence_repair",             "difficulty_hint": "foundation_to_intermediate", "bank_tags": ["sentence_meaning","clause_repair","predicate_object_fit"], "recommended_formats": ["choose_clearer_sentence","rewrite_one_clear_sentence","identify_broken_meaning"]},
    "SKILL_SEMANTIC_EVALUABILITY":      {"practice_route_id": "SENTENCE_CLARITY_REPAIR",         "exercise_family": "sentence_repair",             "difficulty_hint": "foundation_to_intermediate", "bank_tags": ["sentence_meaning","clause_repair"],                       "recommended_formats": ["choose_clearer_sentence","rewrite_one_clear_sentence"]},
    "SEMANTIC_EVALUABILITY":            {"practice_route_id": "SENTENCE_CLARITY_REPAIR",         "exercise_family": "sentence_repair",             "difficulty_hint": "foundation_to_intermediate", "bank_tags": ["sentence_meaning","clause_repair"],                       "recommended_formats": ["choose_clearer_sentence","rewrite_one_clear_sentence"]},
    "ARTICLE_NOUN_CONTROL":             {"practice_route_id": "ARTICLE_NOUN_NUMBER_DRILL",        "exercise_family": "grammar_microdrill",          "difficulty_hint": "foundation",                 "bank_tags": ["articles","plural_nouns","countability"],                 "recommended_formats": ["minimal_pair","fill_gap","correct_phrase"]},
    "VERB_FORM_PATTERN_CONTROL":        {"practice_route_id": "VERB_FORM_PATTERN_DRILL",         "exercise_family": "grammar_pattern_drill",       "difficulty_hint": "foundation_to_intermediate", "bank_tags": ["verb_form","modal_base_verb","has_to_base_verb"],          "recommended_formats": ["choose_form","rewrite_sentence","pattern_substitution"]},
    "QUANTITY_EXPRESSIONS":             {"practice_route_id": "QUANTITY_COMPARISON_PHRASES",     "exercise_family": "lexico_grammar_phrase_drill", "difficulty_hint": "intermediate",               "bank_tags": ["number_of","fewer_less","comparison"],                    "recommended_formats": ["phrase_choice","collocation_repair","topic_sentence_rewrite"]},
    "CHANGE_COST_EXPRESSIONS":          {"practice_route_id": "ABSTRACT_NOUN_COLLOCATION_DRILL", "exercise_family": "academic_collocation_drill",  "difficulty_hint": "intermediate",               "bank_tags": ["increase_in","cost_of","government_spending"],             "recommended_formats": ["collocation_match","phrase_replacement"]},
    "ABSTRACT_NOUN_COLLOCATIONS":       {"practice_route_id": "ABSTRACT_NOUN_COLLOCATION_DRILL", "exercise_family": "academic_collocation_drill",  "difficulty_hint": "intermediate_to_advanced",   "bank_tags": ["contribute_to","create_opportunities","gain_experience"],  "recommended_formats": ["collocation_match","repair_translated_phrase","write_new_sentence"]},
    "REFERENCE_AND_TRANSITION_CONTROL": {"practice_route_id": "REFERENCE_TRANSITION_REPAIR",     "exercise_family": "coherence_microdrill",        "difficulty_hint": "intermediate",               "bank_tags": ["reference_clarity","cause_result_connector","contrast_connector"], "recommended_formats": ["choose_connector","repair_reference","link_sentences"]},
    "CLAUSE_BOUNDARY_CONTROL":          {"practice_route_id": "SENTENCE_CLARITY_REPAIR",         "exercise_family": "sentence_repair",             "difficulty_hint": "foundation_to_intermediate", "bank_tags": ["clause_boundary","sentence_structure"],                    "recommended_formats": ["rewrite_one_clear_sentence","choose_clearer_sentence"]},
    "_fallback":                        {"practice_route_id": "GENERAL_ERROR_REPAIR",             "exercise_family": "mixed_error_repair",          "difficulty_hint": "adaptive",                   "bank_tags": ["essay_specific_errors"],                                   "recommended_formats": ["correct_phrase","rewrite_sentence"]},
}


# =============================================================================
# PART 6 — A2-B1 LANGUAGE MAPS  (V6.engine)
# =============================================================================

# what: plain description of the error (≤12 words, no jargon)
WHAT_A2B1: Dict[Tuple[str, str], str] = {
    ("CLAUSE_STRUCTURE",        "FIX_CLAUSE"):      "This sentence is hard to understand.",
    ("CONSTRUCTION",            "FIX_CLAUSE"):      "This sentence is not complete.",
    ("WORD_ORDER",              "FIX_WORD_ORDER"):  "The word order is not natural here.",
    ("SENTENCE_STRUCTURE",      "FIX_CLAUSE"):      "This sentence is unclear.",
    ("SUBJECT_VERB_AGREEMENT",  "FIX_SVA"):         "The verb does not match the subject.",
    ("SUBJECT_VERB_AGREEMENT",  "FIX_VERB_FORM"):   "The verb does not match the subject.",
    ("VERB_FORM",               "FIX_VERB_FORM"):   "The verb form is wrong.",
    ("VERB_PATTERN",            "FIX_VERB_FORM"):   "The verb pattern is wrong.",
    ("VERB_TENSE",              "FIX_VERB_TENSE"):  "The verb tense is wrong.",
    ("MODAL_CONTROL",           "FIX_MODAL"):       "After a modal verb, use the base form.",
    ("COMPARATIVE_FORM",        "FIX_COMPARATIVE"): "This comparison is not written correctly.",
    ("COMPARATIVE_FORM",        "FIX_VERB_FORM"):   "This comparison is not written correctly.",
    ("ARTICLE",                 "FIX_ARTICLE"):     "The article (a/an/the) is wrong here.",
    ("ARTICLE_NOUN",            "FIX_ARTICLE"):     "The article or noun form is wrong.",
    ("NOUN_NUMBER",             "FIX_NOUN"):        "Check singular or plural here.",
    ("PREPOSITION",             "FIX_PREPOSITION"): "The preposition is wrong.",
    ("PUNCTUATION",             "FIX_PUNCTUATION"): "The punctuation is wrong here.",
    ("COMMA_SPLICE",            "FIX_PUNCTUATION"): "Do not join two sentences with only a comma.",
    ("COLLOCATION",             "FIX_COLLOCATION"): "These words do not go together in English.",
    ("SEMANTIC_COMBINATION",    "FIX_COLLOCATION"): "These words do not go together in English.",
    ("WORD_CHOICE",             "FIX_WORD_CHOICE"): "This word does not fit the meaning here.",
    ("LEXICAL_PRECISION",       "FIX_WORD_CHOICE"): "A more precise word is needed here.",
    ("WORD_FORM",               "FIX_WORD_FORM"):   "This is the wrong form of the word.",
    ("SPELLING",                "FIX_SPELLING"):    "This word is spelled wrong.",
    ("MISSING_TRANSITION",      "FIX_TRANSITION"):  "A linking word is missing here.",
    ("DISCOURSE_CONNECTOR",     "FIX_TRANSITION"):  "A linking word is missing or wrong.",
    ("IDEA_DEVELOPMENT",        "FIX_IDEA"):        "This idea needs more support.",
    ("POSITION_CLARITY",        "FIX_POSITION"):    "Your position is not clear here.",
}

WHAT_A2B1_FAMILY_FALLBACK: Dict[str, str] = {
    "CLAUSE_STRUCTURE":         "This sentence is hard to understand.",
    "CONSTRUCTION":             "This sentence is not complete.",
    "WORD_ORDER":               "The word order is not natural here.",
    "SUBJECT_VERB_AGREEMENT":   "The verb does not match the subject.",
    "VERB_FORM":                "The verb form is wrong.",
    "COMPARATIVE_FORM":         "This comparison is not written correctly.",
    "ARTICLE":                  "The article is wrong here.",
    "ARTICLE_NOUN":             "The article or noun form is wrong.",
    "PREPOSITION":              "The preposition is wrong.",
    "PUNCTUATION":              "The punctuation is wrong here.",
    "COMMA_SPLICE":             "Two sentences are joined incorrectly.",
    "COLLOCATION":              "These words do not go together in English.",
    "SEMANTIC_COMBINATION":     "These words do not go together in English.",
    "WORD_CHOICE":              "This word does not fit the meaning here.",
    "WORD_FORM":                "This is the wrong form of the word.",
    "SPELLING":                 "This word is spelled wrong.",
    "MISSING_TRANSITION":       "A linking word is missing here.",
    "IDEA_DEVELOPMENT":         "This idea needs more support.",
}

WHY_SCORE_A2B1: Dict[str, str] = {
    "GRA":  "Grammar mistakes reduce your Grammar score.",
    "LR":   "This kind of mistake reduces your Vocabulary score.",
    "CC":   "This makes your essay harder to follow. It reduces your Coherence score.",
    "TR":   "This makes your argument weaker. It reduces your Task Response score.",
    "META": "When sentences are unclear, it is harder for the examiner to score your grammar and ideas.",
}

WHY_SCORE_FAMILY_OVERRIDE: Dict[str, str] = {
    "CLAUSE_STRUCTURE":     "Unclear sentences affect Grammar, Vocabulary, and Coherence scores.",
    "CONSTRUCTION":         "Unclear sentences affect Grammar, Vocabulary, and Coherence scores.",
    "COLLOCATION":          "Unnatural word combinations reduce your Vocabulary score.",
    "SEMANTIC_COMBINATION": "Unnatural word combinations reduce your Vocabulary score.",
    "SPELLING":             "Spelling mistakes reduce your Vocabulary score.",
    "WORD_FORM":            "Wrong word forms reduce your Vocabulary score.",
    "MISSING_TRANSITION":   "Missing links between ideas reduce your Coherence score.",
    "COMMA_SPLICE":         "Punctuation errors reduce your Grammar score.",
}

HOW_TO_FIX_A2B1: Dict[str, str] = {
    "FIX_CLAUSE":       "Write this as one short, clear sentence. Use simple words.",
    "FIX_WORD_ORDER":   "Put the subject first, then the verb, then the object.",
    "FIX_SVA":          "Check: does the verb end in -s for he/she/it? For I/we/they use the base verb.",
    "FIX_VERB_FORM":    "After 'can/will/must/should/may', use the base verb (no -s, -ing, or -ed).",
    "FIX_VERB_TENSE":   "Decide: is this past, present, or future? Use the right tense.",
    "FIX_MODAL":        "After 'can/will/must/should/may', use the base verb. Example: 'can go', not 'can going'.",
    "FIX_COMPARATIVE":  "Use 'more [adjective] than' or '[adjective]-er than'. Example: 'more expensive than'.",
    "FIX_ARTICLE":      "Check: use 'a' for singular countable nouns (first mention), 'the' for known nouns.",
    "FIX_NOUN":         "Check: is this singular (one) or plural (more than one)? Add -s for plural.",
    "FIX_PREPOSITION":  "Look up which preposition goes with this word. Use a dictionary.",
    "FIX_PUNCTUATION":  "Use a full stop (.) to end each sentence. Do not join two sentences with only a comma.",
    "FIX_COLLOCATION":  "Look up this phrase in a dictionary. Use a natural English combination.",
    "FIX_WORD_CHOICE":  "Think about the exact meaning. Choose a more precise, common word.",
    "FIX_WORD_FORM":    "Check: do you need a verb, noun, adjective, or adverb? Use the right form.",
    "FIX_SPELLING":     "Look up the correct spelling. Write the word again correctly.",
    "FIX_TRANSITION":   "Add a linking word. Try: 'However', 'Therefore', 'In addition', 'For example'.",
    "FIX_IDEA":         "Add a reason or an example. Why is this true? Give evidence.",
    "FIX_POSITION":     "Write one clear sentence that says what you think. Example: 'I believe that...'.",
}

HOW_TO_FIX_FAMILY_FALLBACK: Dict[str, str] = {
    "CLAUSE_STRUCTURE":         "Write this as one short, clear sentence.",
    "CONSTRUCTION":             "Write this as one short, clear sentence.",
    "SUBJECT_VERB_AGREEMENT":   "Check that the verb matches the subject.",
    "VERB_FORM":                "Use the correct verb form after modal verbs.",
    "COMPARATIVE_FORM":         "Use 'more [adjective] than' for comparisons.",
    "ARTICLE":                  "Check if you need 'a', 'an', 'the', or no article.",
    "PREPOSITION":              "Look up the correct preposition in a dictionary.",
    "COLLOCATION":              "Look up this phrase and use a natural combination.",
    "WORD_CHOICE":              "Choose a more precise, common word.",
    "WORD_FORM":                "Check which form (verb/noun/adjective) is needed here.",
    "SPELLING":                 "Look up the correct spelling and write it again.",
    "MISSING_TRANSITION":       "Add a linking word like 'However' or 'Therefore'.",
    "COMMA_SPLICE":             "Use a full stop between sentences, not a comma.",
}

FOCUS_NOTES_A2B1: Dict[str, str] = {
    "PRIMARY FOCUS": "This is the most important area to practise right now. Start here. It will help your score the most.",
    "WORK ON NEXT":  "This is important too. Practise this after your main focus is more stable.",
    "MONITOR":       "This is a real weakness, but it is not the most important right now. Be aware of it as you practise.",
    "DIAGNOSTIC":    "This is a diagnostic signal. It is not something to practise directly. It will improve as your main focus gets better.",
}

# Banned student-facing phrases (file 09)
BANNED_PHRASES = [
    "semantic evaluability", "discourse evaluability", "dependency-adjusted pressure",
    "root promotion", "scorer pressure", "rubric profile", "semantic trust",
    "eci", "display_safety", "student_safe", "family_purity",
]


# =============================================================================
# PART 7 — FAMILY → ACTION TITLE  (V6.2)
# =============================================================================

FAMILY_TO_TARGET: Dict[str, str] = {
    "SUBJECT_VERB_AGREEMENT": "SUBJECT_VERB_AGREEMENT_CONTROL",
    "VERB_FORM":              "VERB_FORM_PATTERN_CONTROL",
    "VERB_PATTERN":           "VERB_FORM_PATTERN_CONTROL",
    "COMPARATIVE_FORM":       "QUANTITY_EXPRESSIONS",
    "ARTICLE":                "ARTICLE_NOUN_CONTROL",
    "ARTICLE_NOUN":           "ARTICLE_NOUN_CONTROL",
    "NOUN_NUMBER":            "ARTICLE_NOUN_CONTROL",
    "CLAUSE_STRUCTURE":       "CLAUSE_BOUNDARY_CONTROL",
    "CONSTRUCTION":           "CLAUSE_BOUNDARY_CONTROL",
    "COLLOCATION":            "ABSTRACT_NOUN_COLLOCATIONS",
    "SEMANTIC_COMBINATION":   "ABSTRACT_NOUN_COLLOCATIONS",
    "WORD_CHOICE":            "LEXICAL_WORD_CHOICE_PRECISION",
    "SPELLING":               "SPELLING_ACCURACY",
    "WORD_FORM":              "SPELLING_WORD_FORM_ACCURACY",
    "MISSING_TRANSITION":     "REFERENCE_AND_TRANSITION_CONTROL",
    "DISCOURSE_CONNECTOR":    "REFERENCE_AND_TRANSITION_CONTROL",
    "IDEA_DEVELOPMENT":       "IDEA_DEVELOPMENT",
    "POSITION_CLARITY":       "TASK_FULFILMENT",
    "PREPOSITION":            "GRAMMAR_CONTROL",
    "PUNCTUATION":            "GRAMMAR_CONTROL",
    "COMMA_SPLICE":           "GRAMMAR_CONTROL",
}


# =============================================================================
# PART 8 — VALID DETERMINER CONSTRUCTIONS  (file 14)
# =============================================================================

VALID_DETERMINER_PATTERNS = [
    re.compile(r"^\s*a\s+(few|little)\s+\w+", re.I),
    re.compile(r"^\s*a\s+(number|variety|range|series|group|set|pair|couple|majority|minority|proportion|percentage|lot)\s+of\s+\w+", re.I),
    re.compile(r"^\s*a\s+(large|small|great)\s+(amount|deal)\s+of\s+\w+", re.I),
    re.compile(r"^\s*as\s+a\s+(result|consequence)\b", re.I),
    re.compile(r"^\s*a\s+(result|consequence)\b", re.I),
]

COST_SPENDING_CORE_KEYWORDS = {
    "cost", "costs", "expense", "expenses", "spending", "expenditure",
    "budget", "funding", "funds", "money", "price", "prices", "tax", "taxes",
    "pension", "pensions", "healthcare", "financial", "finance", "pay", "payment",
}

COST_SPENDING_SUPPORT_ONLY = {
    "increase", "decrease", "decline", "rise", "fall", "reduce", "reduction",
    "change", "changes", "changing",
}


# =============================================================================
# PART 9 — TARGET VALIDATION RULES  (file 13)
# =============================================================================

TARGET_VALIDATION_RULES: Dict[str, Dict[str, Any]] = {
    "MEANING_RECOVERY_FIRST": {
        "allowed_families":      ["CLAUSE_STRUCTURE","CONSTRUCTION","SEMANTIC_COMBINATION","WORD_ORDER","PREDICATE_ARGUMENT","MALFORMED_CONSTRUCTION"],
        "soft_allowed_families": ["WORD_CHOICE","COLLOCATION"],
        "minimum_examples":      2,
        "metric_support":        True,
        "fallback_targets":      ["GRAMMAR_CONTROL","LEXICAL_CONTROL","SENTENCE_CONSTRUCTION"],
    },
    "SKILL_SEMANTIC_EVALUABILITY": {
        "allowed_families":      ["CLAUSE_STRUCTURE","CONSTRUCTION","SEMANTIC_COMBINATION","WORD_ORDER","PREDICATE_ARGUMENT","MALFORMED_CONSTRUCTION"],
        "soft_allowed_families": ["WORD_CHOICE","COLLOCATION"],
        "minimum_examples":      2,
        "metric_support":        True,
        "fallback_targets":      ["GRAMMAR_CONTROL","LEXICAL_CONTROL","SENTENCE_CONSTRUCTION"],
    },
    "ARTICLE_NOUN_CONTROL": {
        "allowed_families":  ["ARTICLE_DETERMINER","NOUN_NUMBER_COUNTABILITY","ARTICLE","ARTICLE_NOUN","NOUN_NUMBER"],
        "minimum_examples":  1,
        "validity_gate":     "valid_determiner_check",
        "fallback_targets":  [],
        "if_not_supported":  "suppress",
    },
    "VERB_FORM_PATTERN_CONTROL": {
        "allowed_families":      ["VERB_FORM","VERB_PATTERN","VERB_TENSE","SUBJECT_VERB_AGREEMENT"],
        "soft_allowed_families": ["CLAUSE_STRUCTURE","CONSTRUCTION"],
        "minimum_examples":      1,
        "repeated_claim_min":    2,
        "fallback_targets":      ["GRAMMAR_CONTROL","SENTENCE_CONSTRUCTION"],
    },
    "ABSTRACT_NOUN_COLLOCATIONS": {
        "allowed_families":  ["COLLOCATION","WORD_CHOICE","LEXICAL_PRECISION","WORD_FORM","REGISTER","SEMANTIC_COMBINATION"],
        "minimum_examples":  1,
        "repeated_claim_min": 2,
        "fallback_targets":  ["LEXICAL_CONTROL"],
    },
    "CHANGE_COST_EXPRESSIONS": {
        "allowed_families":    ["COLLOCATION","WORD_CHOICE","LEXICAL_PRECISION"],
        "minimum_examples":    1,
        "requires_domain":     "COST_SPENDING_DOMAIN",
        "student_visible":     False,
        "always_reroute_to":   "ABSTRACT_NOUN_COLLOCATIONS",
        "fallback_targets":    ["ABSTRACT_NOUN_COLLOCATIONS"],
    },
    "QUANTITY_EXPRESSIONS": {
        "allowed_families":      ["COMPARATIVE_FORM","QUANTIFIER_USAGE"],
        "soft_allowed_families": ["NOUN_NUMBER_COUNTABILITY","NOUN_NUMBER"],
        "minimum_examples":      1,
        "fallback_targets":      [],
        "if_not_supported":      "suppress",
    },
    "REFERENCE_AND_TRANSITION_CONTROL": {
        "allowed_families": ["TRANSITION","MISSING_TRANSITION","REFERENCE_COHESION","COHESIVE_DEVICE","PRONOUN_REFERENCE","DISCOURSE_CONNECTOR"],
        "minimum_examples": 1,
        "fallback_targets": [],
        "if_not_supported": "suppress",
    },
    "SPELLING_ACCURACY": {
        "allowed_families": ["SPELLING"],
        "minimum_examples": 2,
        "fallback_targets": [],
        "if_not_supported": "downgrade_to_error_profile_only",
    },
    "CLAUSE_BOUNDARY_CONTROL": {
        "allowed_families":      ["CLAUSE_STRUCTURE","CONSTRUCTION","SENTENCE_STRUCTURE","COMMA_SPLICE"],
        "soft_allowed_families": ["WORD_ORDER","PUNCTUATION"],
        "minimum_examples":      1,
        "fallback_targets":      ["GRAMMAR_CONTROL"],
    },
}

# Priority merge groups (file 15)
MERGE_GROUPS: Dict[str, Dict[str, Any]] = {
    "LEXICAL_COLLOCATION_GROUP": {
        "targets":              ["CHANGE_COST_EXPRESSIONS","ABSTRACT_NOUN_COLLOCATIONS","LEXICAL_CONTROL"],
        "preferred_target":     "ABSTRACT_NOUN_COLLOCATIONS",
        "preferred_title":      "Use natural word combinations",
        "min_family_overlap":   0.6,
    },
    "GRAMMAR_VERB_GROUP": {
        "targets":              ["GRAMMAR_CONTROL","VERB_FORM_PATTERN_CONTROL","SENTENCE_CONSTRUCTION"],
        "preferred_target":     "VERB_FORM_PATTERN_CONTROL",
        "preferred_title":      "Use correct verb forms after modals and patterns",
        "min_family_overlap":   0.6,
    },
    "SENTENCE_CLARITY_GROUP": {
        "targets":              ["MEANING_RECOVERY_FIRST","SKILL_SEMANTIC_EVALUABILITY","SEMANTIC_EVALUABILITY","CLAUSE_BOUNDARY_CONTROL"],
        "preferred_target":     "MEANING_RECOVERY_FIRST",
        "preferred_title":      "Make unclear sentences clear",
        "min_family_overlap":   0.5,
    },
}

# Hard no-merge pairs
NO_MERGE_PAIRS = [
    {"ARTICLE_NOUN_CONTROL", "VERB_FORM_PATTERN_CONTROL"},
    {"ARTICLE_NOUN_CONTROL", "GRAMMAR_CONTROL"},
]


# =============================================================================
# PART 10 — ECI GATES  (V6.0)
# =============================================================================

def check_eci_block(essay_result: Dict[str, Any]) -> bool:
    if essay_result.get("eci_block"):
        return True
    for flag in (essay_result.get("qa_flags") or []):
        if isinstance(flag, dict) and flag.get("flag") == "ECI_BLOCKED":
            return True
        if isinstance(flag, str) and "ECI_BLOCKED" in flag:
            return True
    dc = essay_result.get("debug_counts") or {}
    if dc.get("eci_hard_block") or dc.get("all_meta_fallback_block"):
        return True
    return False


def compute_eci(essay_result: Dict[str, Any]) -> float:
    pl         = essay_result.get("primary_limiter") or {}
    evidence   = pl.get("evidence") or []
    safe_rows  = [e for e in evidence if e.get("display_safety_status") == "student_safe"]
    total_rows = len(evidence)
    if total_rows == 0:
        return 0.0
    safe_ratio = len(safe_rows) / total_rows
    conf_env   = pl.get("confidence_envelope") or {}
    conf       = float(conf_env.get("scorer_confidence") or 0)
    pressure   = float(pl.get("dependency_adjusted_pressure") or 0)
    eci        = (safe_ratio * 0.5) + (min(conf, 1.0) * 0.3) + (min(pressure / 10.0, 1.0) * 0.2)
    return round(eci, 4)


def eci_tier(eci: float) -> str:
    if eci >= 0.65:
        return "high"
    if eci >= 0.35:
        return "medium"
    return "blocked"


def band_context_available(essay_result: Dict[str, Any]) -> bool:
    pl   = essay_result.get("primary_limiter") or {}
    conf = float((pl.get("confidence_envelope") or {}).get("scorer_confidence") or 0)
    return conf >= 0.40


def evidence_family_allowed(skill: str, family: str) -> bool:
    rule = TARGET_VALIDATION_RULES.get(skill.upper()) or {}
    allowed      = [f.upper() for f in (rule.get("allowed_families") or [])]
    soft_allowed = [f.upper() for f in (rule.get("soft_allowed_families") or [])]
    if not allowed:
        return True
    return family.upper() in allowed or family.upper() in soft_allowed


# =============================================================================
# PART 11 — DOMAIN VALIDATION  (file 14)
# =============================================================================

def check_cost_spending_domain(text: str) -> bool:
    words = set(re.findall(r"\b\w+\b", text.lower()))
    return bool(words & COST_SPENDING_CORE_KEYWORDS)


def is_valid_determiner_construction(quote: str) -> bool:
    for pattern in VALID_DETERMINER_PATTERNS:
        if pattern.match(quote.strip()):
            return True
    return False


# =============================================================================
# PART 12 — TARGET VALIDATION + DOWNGRADE  (file 13)
# =============================================================================

def validate_target(
    target: Dict[str, Any],
    semantic_summary: Dict[str, Any],
    essay_text: str = "",
) -> Tuple[bool, Optional[str], str]:
    """
    Returns (is_valid, reroute_target_id_or_None, reason).
    Checks: always_reroute, family purity, minimum examples, domain gate, metric support.
    """
    tid  = (target.get("target_id") or "").upper()
    rule = TARGET_VALIDATION_RULES.get(tid) or {}

    # Always reroute (e.g. CHANGE_COST_EXPRESSIONS)
    if rule.get("always_reroute_to"):
        return False, rule["always_reroute_to"], "always_reroute_policy"

    # student_safe_evidence_count from PE target_validation
    tv         = target.get("target_validation") or {}
    safe_count = int(tv.get("student_safe_evidence_count") or 0)
    purity     = float(tv.get("family_purity") or 1.0)
    min_ex     = int(rule.get("minimum_examples") or 1)

    if purity < 0.55:
        fallbacks = rule.get("fallback_targets") or []
        return False, (fallbacks[0] if fallbacks else None), f"family_purity={purity:.2f}<0.55"

    if safe_count < min_ex:
        fallbacks = rule.get("fallback_targets") or []
        return False, (fallbacks[0] if fallbacks else None), f"safe_count={safe_count}<min_examples={min_ex}"

    # Determiner validity gate
    if rule.get("validity_gate") == "valid_determiner_check":
        quotes = target.get("example_quotes") or []
        if all(is_valid_determiner_construction(q) for q in quotes if q):
            return False, None, "all_quotes_are_valid_determiner_constructions"

    # Metric support for sentence clarity targets
    if rule.get("metric_support") and semantic_summary:
        mr = float(semantic_summary.get("mean_recoverability") or 1.0)
        mt = float(semantic_summary.get("mean_semantic_trust") or 1.0)
        bc = int(semantic_summary.get("blocked_sentence_count") or 0)
        if mr >= 0.62 and mt >= 0.62 and bc < 2:
            fallbacks = rule.get("fallback_targets") or []
            return False, (fallbacks[0] if fallbacks else None), "metric_support_not_satisfied"

    return True, None, "ok"


def _get_merge_group(tid: str) -> Optional[str]:
    for gname, grp in MERGE_GROUPS.items():
        if tid.upper() in [t.upper() for t in grp["targets"]]:
            return gname
    return None


def _dominant_family_set(target: Dict[str, Any]) -> set:
    fams = target.get("dominant_families") or []
    result = set()
    for f in fams:
        if isinstance(f, dict):
            result.add(f.get("family", "").upper())
        else:
            result.add(str(f).upper())
    return result


def apply_merge_rules(targets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Merge overlapping priorities per file 15.
    Returns deduplicated, merged list (keeps ordering by rank).
    """
    if len(targets) <= 1:
        return targets

    merged_out: List[Dict[str, Any]] = []
    consumed: set = set()

    for i, t1 in enumerate(targets):
        if i in consumed:
            continue
        tid1   = (t1.get("target_id") or "").upper()
        grp1   = _get_merge_group(tid1)
        fams1  = _dominant_family_set(t1)

        for j, t2 in enumerate(targets):
            if j <= i or j in consumed:
                continue
            tid2 = (t2.get("target_id") or "").upper()

            # Hard no-merge check
            if {tid1, tid2} in NO_MERGE_PAIRS:
                continue

            grp2 = _get_merge_group(tid2)
            if grp1 and grp1 == grp2:
                fams2 = _dominant_family_set(t2)
                if fams1 and fams2:
                    overlap = len(fams1 & fams2) / max(len(fams1), len(fams2))
                else:
                    overlap = 1.0  # same group, no family data → merge
                min_overlap = MERGE_GROUPS[grp1]["min_family_overlap"]
                if overlap >= min_overlap:
                    consumed.add(j)
                    # Merge quotes from t2 into t1
                    t1_quotes = list(t1.get("example_quotes") or [])
                    for q in (t2.get("example_quotes") or []):
                        if q not in t1_quotes:
                            t1_quotes.append(q)
                    t1["example_quotes"] = t1_quotes[:3]
                    # Use preferred target info
                    pref = MERGE_GROUPS[grp1]["preferred_target"]
                    if TARGET_TITLES.get(pref):
                        t1["_merged_title"] = MERGE_GROUPS[grp1]["preferred_title"]
                        t1["_merged_target_id"] = pref

        merged_out.append(t1)

    return merged_out


# =============================================================================
# PART 13 — HELPERS
# =============================================================================

def _rubric_label(rubric: str) -> str:
    r = rubric.upper()
    if r in META_RUBRIC_LABEL:
        return META_RUBRIC_LABEL[r]
    return RUBRIC_LABELS.get(r, rubric)


def _student_label(skill: str) -> str:
    entry = STUDENT_LABELS.get(skill.upper()) or {}
    if entry.get("student_label"):
        return entry["student_label"]
    return skill.replace("_", " ").title()


def _school_student_version(skill: str) -> Optional[str]:
    entry = STUDENT_LABELS.get(skill.upper()) or {}
    return entry.get("school_student_version")


def _target_title(tid: str) -> str:
    return TARGET_TITLES.get(tid.upper()) or tid.replace("_", " ").title()


def _family_action_title(family: str) -> str:
    target = FAMILY_TO_TARGET.get(family.upper())
    if target:
        title = TARGET_TITLES.get(target)
        if title:
            return title
    direct = TARGET_TITLES.get(family.upper())
    if direct:
        return direct
    return family.replace("_", " ").title()


def _family_name(family: str) -> str:
    NAMES = {
        "CLAUSE_STRUCTURE":       "sentence structure",
        "CONSTRUCTION":           "sentence construction",
        "SUBJECT_VERB_AGREEMENT": "subject-verb agreement",
        "VERB_FORM":              "verb form",
        "VERB_PATTERN":           "verb pattern",
        "COMPARATIVE_FORM":       "comparison form",
        "ARTICLE":                "article use",
        "ARTICLE_NOUN":           "article and noun use",
        "NOUN_NUMBER":            "singular/plural",
        "PREPOSITION":            "preposition",
        "PUNCTUATION":            "punctuation",
        "COMMA_SPLICE":           "sentence joining",
        "COLLOCATION":            "word combinations",
        "SEMANTIC_COMBINATION":   "word combinations",
        "WORD_CHOICE":            "word choice",
        "WORD_FORM":              "word form",
        "SPELLING":               "spelling",
        "MISSING_TRANSITION":     "linking words",
        "DISCOURSE_CONNECTOR":    "linking words",
        "IDEA_DEVELOPMENT":       "idea development",
        "POSITION_CLARITY":       "position clarity",
    }
    return NAMES.get(family.upper(), family.replace("_", " ").lower())


def _pressure_to_priority(pressure: float) -> str:
    if pressure >= 7.0:
        return "very_high"
    if pressure >= 4.5:
        return "high"
    if pressure >= 2.0:
        return "medium"
    return "low"


def _focus_label(
    skill: str, rank: int, pressure: float,
    primary_pressure: float, primary_skill: str,
) -> str:
    if skill.upper() in META_SKILLS:
        return "DIAGNOSTIC"
    if skill.upper() == primary_skill.upper():
        return "PRIMARY FOCUS"
    if rank <= 3 and primary_pressure > 0 and pressure >= 0.5 * primary_pressure:
        return "WORK ON NEXT"
    return "MONITOR"


def _what_a2b1(family: str, repair_op: str) -> str:
    key = (family.upper(), repair_op.upper())
    if key in WHAT_A2B1:
        return WHAT_A2B1[key]
    return WHAT_A2B1_FAMILY_FALLBACK.get(family.upper(), "There is an error here.")


def _why_score_a2b1(family: str, rubric: str) -> str:
    override = WHY_SCORE_FAMILY_OVERRIDE.get(family.upper())
    if override:
        return override
    return WHY_SCORE_A2B1.get(rubric.upper(), "This kind of mistake can reduce your score.")


def _how_to_fix_a2b1(family: str, repair_op: str) -> str:
    direct = HOW_TO_FIX_A2B1.get(repair_op.upper())
    if direct:
        return direct
    return HOW_TO_FIX_FAMILY_FALLBACK.get(family.upper(), "Check this carefully and correct it.")


def _practice_route(tid: str) -> Dict[str, Any]:
    route = PRACTICE_ROUTES.get(tid.upper()) or PRACTICE_ROUTES.get("_fallback")
    return dict(route)


def _improvement_strategy(skill: str) -> Dict[str, str]:
    key = skill.upper()
    strat = IMPROVEMENT_STRATEGY_RULES.get(key) or IMPROVEMENT_STRATEGY_RULES.get("_fallback")
    return dict(strat)


def _limiter_template(skill: str) -> Dict[str, str]:
    key = skill.upper()
    tmpl = LIMITER_TEMPLATES.get(key) or LIMITER_TEMPLATES.get("_fallback")
    return dict(tmpl)


def _check_banned_phrases(text: str) -> List[str]:
    found = []
    lo = text.lower()
    for phrase in BANNED_PHRASES:
        if phrase in lo:
            found.append(phrase)
    return found


# =============================================================================
# PART 14 — EVIDENCE BUILDERS
# =============================================================================

def build_evidence_item_a2b1(ev_row: Dict[str, Any], rubric: str) -> Dict[str, Any]:
    family    = (ev_row.get("family") or "").upper()
    repair_op = (ev_row.get("repair_operation") or "").upper()
    quote     = (ev_row.get("quote") or "").strip()
    row_id    = ev_row.get("row_id")
    is_root   = not ev_row.get("is_symptom", False)
    return {
        "quote":      quote,
        "what":       _what_a2b1(family, repair_op),
        "why_score":  _why_score_a2b1(family, rubric),
        "how_to_fix": _how_to_fix_a2b1(family, repair_op),
        "family":     family,
        "repair_op":  repair_op,
        "row_id":     row_id,
        "is_root":    is_root,
    }


def _safe_evidence(skill: str, evidence: List[Dict], max_items: int = 3) -> List[Dict]:
    """Filter student-safe rows, prefer root over symptom (file 05)."""
    safe = [e for e in evidence
            if e.get("display_safety_status") == "student_safe"
            and evidence_family_allowed(skill, e.get("family") or "")]
    # Prefer root rows
    root_rows    = [e for e in safe if not e.get("is_symptom", False)]
    symptom_rows = [e for e in safe if e.get("is_symptom", False)]
    ordered = root_rows + symptom_rows
    # Deduplicate by quote
    seen: set = set()
    deduped = []
    for e in ordered:
        q = (e.get("quote") or "").strip()
        if q not in seen:
            seen.add(q)
            deduped.append(e)
    return deduped[:max_items]


def _target_evidence_items(target: Dict[str, Any], rubric: str) -> List[Dict]:
    """Build evidence items from a fine_grained_training_target."""
    ev_examples = target.get("evidence_examples") or []
    quotes      = target.get("example_quotes") or []
    fam         = ""
    dom_fams    = target.get("dominant_families") or []
    if dom_fams:
        first = dom_fams[0]
        fam = first.get("family") if isinstance(first, dict) else str(first)

    items = []
    if ev_examples:
        for ev in ev_examples[:3]:
            built = build_evidence_item_a2b1(ev, rubric)
            items.append(built)
    else:
        for q in quotes[:3]:
            items.append({
                "quote":      q,
                "what":       _what_a2b1(fam, ""),
                "why_score":  _why_score_a2b1(fam, rubric),
                "how_to_fix": _how_to_fix_a2b1(fam, ""),
                "family":     fam,
                "repair_op":  "",
                "row_id":     None,
                "is_root":    True,
            })
    return items


# =============================================================================
# PART 15 — SECTION BUILDERS
# =============================================================================

def build_overall_summary(
    primary_limiter: Dict[str, Any],
    semantic_summary: Dict[str, Any],
    pattern_intelligence: Dict[str, Any],
    bands: Dict[str, Any],
) -> str:
    """35-70 words. Must include main_problem + what_to_focus_on_first. No raw pressure. (file 01)"""
    skill      = primary_limiter.get("skill") or ""
    label      = _student_label(skill)
    tmpl       = _limiter_template(skill)
    band       = bands.get("overall")
    band_str   = f" (Band {band})" if band else ""
    pi_note    = pattern_intelligence.get("task_type_specific_note") or ""
    pi_note_s  = (pi_note[:80] + "…") if len(pi_note) > 80 else pi_note

    summary = (
        f"Your main area to work on is {label.lower()}{band_str}. "
        f"{tmpl['plain_title'].replace('Your main problem is ', '').capitalize()} "
        f"{tmpl['first_action']}"
    )
    if pi_note_s:
        summary += f" Note: {pi_note_s}"
    # Trim to ~70 words
    words = summary.split()
    if len(words) > 70:
        summary = " ".join(words[:70]) + "…"
    return summary


def build_main_score_limiter(
    primary_limiter: Dict[str, Any],
    safe_evidence:   List[Dict],
    rubric:          str,
    eci_tier_val:    str,
) -> Dict[str, Any]:
    """main_score_limiter section from file 01 + templates from file 03."""
    skill  = primary_limiter.get("skill") or ""
    label  = _student_label(skill)
    tmpl   = _limiter_template(skill)
    ssv    = _school_student_version(skill)

    evidence_items = [build_evidence_item_a2b1(e, rubric) for e in safe_evidence[:3]]
    # NO_GENERIC_ADVICE: first_action must be specific (file 04)
    first_action = tmpl["first_action"]

    return {
        "title":              tmpl["plain_title"],
        "student_label":      label,
        "explanation":        tmpl["main_explanation"],
        "school_note":        ssv or "",
        "why_it_matters":     tmpl["why_it_matters"],
        "first_action":       first_action,
        "example_intro":      tmpl["example_intro"],
        "examples":           evidence_items,
        "evidence_mode":      "full" if evidence_items else "label_only",
        "rubric":             rubric,
        "rubric_plain":       _rubric_label(rubric),
        "eci_tier":           eci_tier_val,
    }


def build_top_learning_priorities(
    fine_grained_targets: List[Dict[str, Any]],
    primary_limiter:      Dict[str, Any],
    semantic_summary:     Dict[str, Any],
    bands:                Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Top 3 validated, merged priorities (files 04, 13, 15)."""
    rubric = (primary_limiter.get("rubric") or "GRA")

    # Step 1: validate each target
    validated = []
    for t in fine_grained_targets:
        ok, reroute, reason = validate_target(t, semantic_summary)
        if ok:
            validated.append(t)
        elif reroute:
            # Reroute: clone with new target_id
            t2 = dict(t)
            t2["target_id"]     = reroute
            t2["_rerouted_from"] = t.get("target_id")
            t2["_reroute_reason"] = reason
            ok2, _, _ = validate_target(t2, semantic_summary)
            if ok2:
                validated.append(t2)

    # Step 2: merge overlapping priorities
    merged = apply_merge_rules(validated)

    # Step 3: build priority items, top 3 only (file 04)
    priorities = []
    for i, t in enumerate(merged[:3]):
        tid         = t.get("target_id") or ""
        title       = t.get("_merged_title") or _target_title(tid)
        ev_items    = _target_evidence_items(t, rubric)
        route       = _practice_route(t.get("_merged_target_id") or tid)
        why_score   = WHY_SCORE_A2B1.get(rubric, "This matters for your IELTS score.")
        # EXPLAIN_WHY_WITH_SCORE_RELEVANCE (file 04)
        why_text    = t.get("why_this_priority") or why_score
        practice_f  = t.get("practice_focus") or route.get("recommended_formats", [""])[0]

        priorities.append({
            "priority_number":      i + 1,
            "target_id":            tid,
            "student_friendly_title": title,
            "why_this_matters":     why_text,
            "examples":             ev_items,
            "practice_focus":       practice_f,
            "practice_route_id":    route.get("practice_route_id"),
            "exercise_family":      route.get("exercise_family"),
            "difficulty_hint":      route.get("difficulty_hint"),
            "bank_tags":            route.get("bank_tags"),
            "recommended_formats":  route.get("recommended_formats"),
        })

    return priorities


def build_strengths(
    display_decisions: Dict[str, Any],
    strengths_raw:     List[Dict[str, Any]],
    semantic_summary:  Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Strength section (files 01, 06).
    Uses display_decisions.show_in_short_feedback.max_strengths (PE pre-filtered).
    Max 2 items. Translate IDs. Do not invent. (file 06)
    """
    DISALLOWED = {"word_count", "paragraph_count", "you tried", "basic structure present without"}

    ssfb         = (display_decisions.get("show_in_short_feedback") or {})
    display_list = ssfb.get("max_strengths") or strengths_raw

    STRENGTH_TRANSLATIONS: Dict[str, str] = {
        "BASIC_STRUCTURE_PRESENT": "You have a usable essay structure. This gives you a base to improve.",
        "POSITION_SIGNAL_PRESENT": "Your opinion or direction is visible. This helps the reader follow your answer.",
        "DETECTOR_LEXICAL_CONTROL": "Your vocabulary area is more controlled than your main problem. Build on it.",
        "IDEAS_RECOVERABLE": "Many of your ideas can still be understood. Improvement can be fast if clarity improves.",
        "RELATIVE_RUBRIC_STRENGTH": "One IELTS area is less pressured than your main problem. Use it as a base.",
    }

    out = []
    for s in display_list:
        sid  = s.get("id") or ""
        text = STRENGTH_TRANSLATIONS.get(sid) or s.get("strength") or ""
        if not text:
            continue
        # Filter disallowed vague praise
        lo = text.lower()
        if any(d in lo for d in DISALLOWED):
            if sid != "BASIC_STRUCTURE_PRESENT":
                continue
        how_next = s.get("how_to_use_for_next_band") or ""
        out.append({
            "id":           sid,
            "student_text": text,
            "how_to_use":   how_next,
            "confidence":   s.get("confidence") or "medium",
        })
        if len(out) >= 2:
            break

    # Fallback: if no valid strengths (file 06)
    if not out:
        mr = float(semantic_summary.get("mean_recoverability") or 0)
        if mr >= 0.65:
            out.append({
                "id": "IDEAS_RECOVERABLE_AUTO",
                "student_text": "Many of your ideas can still be understood. Fast improvement is possible if sentence clarity improves.",
                "how_to_use": "Focus on sentence clarity first.",
                "confidence": "medium",
            })
        else:
            out.append({
                "id": "CAUTIOUS_FALLBACK",
                "student_text": "The clearest positive point is that you attempted to answer the task. The main focus should now be clarity and accuracy.",
                "how_to_use": "",
                "confidence": "low",
            })
    return out


def build_improvement_strategy(primary_skill: str, secondary_skills: List[str]) -> Dict[str, str]:
    """do_first / do_next / avoid_for_now  (file 07)."""
    strat = _improvement_strategy(primary_skill)
    return {
        "do_first":       strat["do_first"],
        "do_next":        strat["do_next"],
        "avoid_for_now":  strat["avoid_for_now"],
    }


def build_recommended_practice(top_priorities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Practice routing section (files 01, 08)."""
    seen_routes: set = set()
    out = []
    for p in top_priorities:
        route_id = p.get("practice_route_id")
        if not route_id or route_id in seen_routes:
            continue
        seen_routes.add(route_id)
        out.append({
            "practice_route_id":    route_id,
            "exercise_family":      p.get("exercise_family"),
            "difficulty_hint":      p.get("difficulty_hint"),
            "bank_tags":            p.get("bank_tags") or [],
            "recommended_formats":  p.get("recommended_formats") or [],
            "evidence_target_id":   p.get("target_id"),
            "priority_number":      p.get("priority_number"),
        })
    return out


def build_short_report(
    overall_summary:     str,
    main_score_limiter:  Dict[str, Any],
    watch_also:          List[Dict[str, Any]],
    band_ctx:            Optional[Dict[str, Any]],
    strengths:           List[Dict[str, Any]],
) -> Dict[str, Any]:
    """30-second read: summary + main focus + watch also + strengths snapshot."""
    pf_examples = main_score_limiter.get("examples") or []
    # Show top 2 examples in short report
    top_ev = pf_examples[:2]
    return {
        "heading": "Your Focus Right Now",
        "overall_summary": overall_summary,
        "primary_focus": {
            "skill_label":  main_score_limiter.get("student_label"),
            "rubric_plain": main_score_limiter.get("rubric_plain"),
            "summary":      main_score_limiter.get("explanation"),
            "first_action": main_score_limiter.get("first_action"),
            "top_evidence": top_ev,
        },
        "strengths_snapshot": strengths[:1],
        "watch_also": watch_also,
        "band_snapshot": {
            "overall":     (band_ctx or {}).get("overall"),
            "target_band": (band_ctx or {}).get("target_band"),
        } if band_ctx else None,
    }


def build_detailed_report(
    essay_result:     Dict[str, Any],
    primary_skill:    str,
    primary_pressure: float,
    bands:            Dict[str, Any],
) -> Dict[str, Any]:
    """Full weakness profile — all skills ranked by pressure (V6.1/6.2)."""
    skill_profiles = essay_result.get("skill_profiles") or []
    sorted_sp = sorted(
        [sp for sp in skill_profiles
         if float(sp.get("dependency_adjusted_pressure") or sp.get("pressure") or 0) > 0],
        key=lambda sp: float(sp.get("dependency_adjusted_pressure") or sp.get("pressure") or 0),
        reverse=True,
    )
    weakness_profile = []
    for rank, sp in enumerate(sorted_sp):
        skill    = sp.get("skill") or ""
        rubric   = sp.get("rubric") or ""
        label    = _student_label(skill) or sp.get("student_label") or skill.replace("_", " ").title()
        pressure = float(sp.get("dependency_adjusted_pressure") or sp.get("pressure") or 0)
        f_label  = _focus_label(skill, rank + 1, pressure, primary_pressure, primary_skill)
        f_note   = FOCUS_NOTES_A2B1.get(f_label, "")
        dom_fams = sp.get("dominant_families") or []
        raw_ev   = sp.get("examples") or []
        safe_ev  = _safe_evidence(skill, raw_ev)

        # Group by family (V6.2 style)
        fam_groups: Dict[str, List] = {}
        for e in safe_ev:
            fam = (e.get("family") or "").upper()
            fam_groups.setdefault(fam, []).append(e)

        error_families = []
        for d in dom_fams:
            if isinstance(d, dict):
                fam = d.get("family") or ""
                cnt = d.get("count")
            else:
                fam = str(d)
                cnt = None
            rows = fam_groups.get(fam.upper(), [])
            samp = rows[:2]
            ev_items = [build_evidence_item_a2b1(e, rubric) for e in samp]
            error_families.append({
                "family":          fam,
                "family_plain":    _family_action_title(fam),
                "count":           cnt,
                "sample_evidence": [{"quote": e["quote"], "what": e["what"], "how_to_fix": e["how_to_fix"]} for e in ev_items],
                "action":          ev_items[0]["how_to_fix"] if ev_items else "",
            })

        weakness_profile.append({
            "rank":            rank + 1,
            "skill":           skill,
            "student_label":   label,
            "rubric":          rubric,
            "rubric_plain":    _rubric_label(rubric),
            "pressure":        round(pressure, 3),
            "priority_level":  _pressure_to_priority(pressure),
            "focus_label":     f_label,
            "focus_note":      f_note,
            "is_diagnostic":   skill.upper() in META_SKILLS,
            "error_families":  error_families,
            "evidence_mode":   "full" if safe_ev else "label_only",
        })

    return {
        "heading": "Your Full Picture",
        "intro": (
            "Below you can see all problems found in your essay, from most important to least. "
            "The ★ problem is the one to work on first. "
            "Work through the others in order as your writing improves."
        ),
        "weakness_profile":                 weakness_profile,
        "_gold_full_error_table_available": True,
    }


def build_teacher_debug(
    essay_result:   Dict[str, Any],
    validated_tids: List[str],
    qa_flags:       List[str],
) -> Dict[str, Any]:
    """teacher_debug_view section (files 01, 10)."""
    pl   = essay_result.get("primary_limiter") or {}
    sls  = essay_result.get("secondary_limiters") or []
    rvs  = essay_result.get("root_vs_symptom") or {}
    dc   = essay_result.get("debug_counts") or {}
    sp   = essay_result.get("skill_profiles") or []
    fp   = essay_result.get("family_profiles") or {}
    return {
        "engine_version":               FEEDBACK_ENGINE_VERSION,
        "input_schema_version":         PE_INPUT_CONTRACT,
        "primary_limiter_internal_code": pl.get("skill"),
        "primary_pressure":             pl.get("dependency_adjusted_pressure"),
        "secondary_limiters_codes":     [s.get("skill") for s in sls],
        "training_target_ids":          validated_tids,
        "evidence_row_ids":             [e.get("row_id") for e in (pl.get("evidence") or []) if e.get("row_id")],
        "root_vs_symptom_summary":      rvs,
        "debug_counts":                 dc,
        "skill_profiles_summary":       [{"skill": s.get("skill"), "pressure": s.get("dependency_adjusted_pressure")} for s in sp],
        "family_profiles":              fp,
        "qa_flags_engine":              qa_flags,
    }


# =============================================================================
# PART 16 — OUTPUT QUALITY GATES  (file 16)
# =============================================================================

def apply_output_quality_gates(bundle: Dict[str, Any]) -> Tuple[Dict[str, Any], str, List[str]]:
    """
    Apply 8 presentation-layer gates. Auto-repair where safe; flag otherwise.
    Returns (bundle, qa_status, qa_flag_list).
    """
    flags: List[str] = []
    sf = bundle.get("student_feedback") or {}
    priorities = sf.get("top_learning_priorities") or []

    # Gate: NO_REPEATED_WORDING_WITH_SINGLE_EXAMPLE
    for p in priorities:
        why   = (p.get("why_this_matters") or "").lower()
        evs   = p.get("examples") or []
        if ("repeated" in why or "more than once" in why or "several times" in why) and len(evs) < 2:
            p["why_this_matters"] = (
                "This is a useful target. The current evidence shows one clear example. "
                "Treat it as a focused repair, not a repeated pattern."
            )
            flags.append("NO_REPEATED_WORDING_WITH_SINGLE_EXAMPLE:auto_repaired")

    # Gate: NO_VALID_DETERMINER_FALSE_POSITIVES
    repaired_priorities = []
    for p in priorities:
        if (p.get("target_id") or "").upper() in ("ARTICLE_NOUN_CONTROL",):
            evs    = p.get("examples") or []
            valid  = [e for e in evs if not is_valid_determiner_construction(e.get("quote") or "")]
            if not valid and evs:
                flags.append(f"NO_VALID_DETERMINER_FALSE_POSITIVES:suppressed {p.get('target_id')}")
                continue  # suppress priority
            p["examples"] = valid
        repaired_priorities.append(p)
    sf["top_learning_priorities"] = repaired_priorities

    # Gate: NO_TOPIC_ROUTE_WITHOUT_DOMAIN (CHANGE_COST_EXPRESSIONS)
    for p in repaired_priorities:
        if (p.get("target_id") or "").upper() == "CHANGE_COST_EXPRESSIONS":
            evs  = p.get("examples") or []
            text = " ".join(e.get("quote") or "" for e in evs)
            if not check_cost_spending_domain(text):
                p["practice_route_id"]       = "ABSTRACT_NOUN_COLLOCATION_DRILL"
                p["student_friendly_title"]  = "Use natural word combinations"
                p["target_id"]               = "ABSTRACT_NOUN_COLLOCATIONS"
                flags.append("NO_TOPIC_ROUTE_WITHOUT_DOMAIN:rerouted_to_broad_lexical")

    # Gate: NO_DUPLICATE_PRIORITY_EVIDENCE (>= 60% quote overlap between any two priorities)
    all_quotes = [
        {e.get("quote") for e in (p.get("examples") or [])} for p in repaired_priorities
    ]
    dedup_priorities = []
    consumed_prios: set = set()
    for i, p in enumerate(repaired_priorities):
        if i in consumed_prios:
            continue
        for j in range(i + 1, len(repaired_priorities)):
            if j in consumed_prios:
                continue
            q1, q2 = all_quotes[i], all_quotes[j]
            if q1 and q2:
                overlap = len(q1 & q2) / max(len(q1), len(q2))
                if overlap >= 0.6:
                    consumed_prios.add(j)
                    flags.append(f"NO_DUPLICATE_PRIORITY_EVIDENCE:merged priority {j+1} into {i+1}")
        dedup_priorities.append(p)
    sf["top_learning_priorities"] = dedup_priorities

    # Re-number after suppression/merge
    for idx, p in enumerate(sf.get("top_learning_priorities") or []):
        p["priority_number"] = idx + 1

    # Gate: NO_PRACTICE_ROUTE_WITHOUT_VALIDATED_EXAMPLES
    rec_practice = sf.get("recommended_practice") or []
    valid_routes = []
    for r in rec_practice:
        tid  = r.get("evidence_target_id") or ""
        prio = next((p for p in (sf.get("top_learning_priorities") or []) if p.get("target_id") == tid), None)
        if prio and (prio.get("examples") or []):
            valid_routes.append(r)
        elif not tid:
            valid_routes.append(r)
        else:
            flags.append(f"NO_PRACTICE_ROUTE_WITHOUT_VALIDATED_EXAMPLES:suppressed {r.get('practice_route_id')}")
    sf["recommended_practice"] = valid_routes

    # Gate: NO_SCORE_BLOCK_IF_SCORES_NULL
    bc = bundle.get("band_context")
    if bc and all(bc.get(f) is None for f in ("overall", "gra", "lr", "cc", "tr")):
        bundle.pop("band_context", None)
        flags.append("NO_SCORE_BLOCK_IF_SCORES_NULL:band_context_hidden")

    # Gate: NO_RELATIVE_STRENGTHS_IN_STUDENT_VIEW
    strengths = sf.get("strengths") or []
    clean_strengths = []
    for s in strengths:
        text = (s.get("student_text") or "").lower()
        if "not biggest obstacle" in text or "relatively less" in text or "less pressured" in text:
            debug = bundle.setdefault("teacher_debug", {})
            debug.setdefault("suppressed_strengths", []).append(s)
            flags.append(f"NO_RELATIVE_STRENGTHS_IN_STUDENT_VIEW:moved_to_debug {s.get('id')}")
        else:
            clean_strengths.append(s)
    sf["strengths"] = clean_strengths

    bundle["student_feedback"] = sf

    # Determine QA status
    blockers = [f for f in flags if "suppressed" in f.lower() or "rerouted" in f.lower()]
    if not flags:
        qa_status = "pass"
    elif blockers:
        qa_status = "needs_review"
    else:
        qa_status = "warning"

    return bundle, qa_status, flags


# =============================================================================
# PART 17 — BAND CONTEXT BUILDER
# =============================================================================

def build_band_context(overall: Any, rubric: str, pressure: float) -> Dict[str, Any]:
    try:
        band_f = float(overall)
    except (TypeError, ValueError):
        return {}
    target = band_f + 0.5 if pressure >= 5.0 else band_f + 1.0
    target = min(target, 9.0)
    gain_est  = "meaningful" if pressure >= 5.0 else "marginal"
    gain_conf = "medium"
    return {
        "overall":      overall,
        "target_band":  target,
        "gain_estimate": gain_est,
        "gain_confidence": gain_conf,
        "rubric":       rubric,
    }


# =============================================================================
# PART 18 — GENERATE BUNDLE  (main pipeline)
# =============================================================================

def generate_feedback_bundle(essay_result: Dict[str, Any]) -> Dict[str, Any]:
    essay_id = str(essay_result.get("essay_id") or "unknown")
    now      = datetime.now(timezone.utc).isoformat()

    # Input adapter: required fields (file 12)
    pl       = essay_result.get("primary_limiter") or {}
    if not pl:
        return {"schema_version": FEEDBACK_BUNDLE_SCHEMA, "essay_id": essay_id,
                "status": "error", "error": "NO_PRIMARY_LIMITER"}

    meta      = essay_result.get("metadata") or {}
    bands     = essay_result.get("bands_if_available") or {}
    sl_list   = essay_result.get("secondary_limiters") or []
    targets   = essay_result.get("fine_grained_training_targets") or []
    sem_sum   = essay_result.get("semantic_summary") or {}
    pattern_i = essay_result.get("pattern_intelligence") or {}
    band_unlock = essay_result.get("band_unlock") or {}
    rvs       = essay_result.get("root_vs_symptom") or {}
    disp      = essay_result.get("display_decisions") or {}
    strengths_raw = essay_result.get("strengths") or []
    qa_flags_pe   = essay_result.get("qa_flags") or []

    # ── Gate 1: explicit ECI block ────────────────────────────────────────────
    if check_eci_block(essay_result):
        return {
            "schema_version": FEEDBACK_BUNDLE_SCHEMA, "essay_id": essay_id,
            "generated_at": now, "engine_version": FEEDBACK_ENGINE_VERSION,
            "status": "blocked_eci",
            "downstream": {"eci": 0.0, "eci_tier": "blocked",
                           "overall_band": bands.get("overall")},
        }

    # ── Gate 2: compute ECI ────────────────────────────────────────────────────
    eci      = compute_eci(essay_result)
    tier     = eci_tier(eci)
    if tier == "blocked":
        return {
            "schema_version": FEEDBACK_BUNDLE_SCHEMA, "essay_id": essay_id,
            "generated_at": now, "engine_version": FEEDBACK_ENGINE_VERSION,
            "status": "blocked_eci",
            "downstream": {"eci": round(eci, 4), "eci_tier": "blocked",
                           "overall_band": bands.get("overall")},
        }

    skill    = pl.get("skill") or ""
    rubric   = pl.get("rubric") or "GRA"
    pressure = float(pl.get("dependency_adjusted_pressure") or 0.0)
    overall  = bands.get("overall")

    # ── Gate 3: filter safe evidence ──────────────────────────────────────────
    raw_ev   = pl.get("evidence") or []
    safe_ev  = _safe_evidence(skill, raw_ev, max_items=3)

    # ── Gate 6: band context ──────────────────────────────────────────────────
    band_ctx: Optional[Dict[str, Any]] = None
    if band_context_available(essay_result) and overall is not None:
        band_ctx = build_band_context(overall, rubric, pressure)

    # ── Section: overall_summary ──────────────────────────────────────────────
    overall_summary = build_overall_summary(pl, sem_sum, pattern_i, bands)

    # ── Section: main_score_limiter ───────────────────────────────────────────
    main_limiter_section = build_main_score_limiter(pl, safe_ev, rubric, tier)

    # ── Section: top_learning_priorities ─────────────────────────────────────
    top_priorities = build_top_learning_priorities(targets, pl, sem_sum, bands)
    validated_tids = [p.get("target_id") for p in top_priorities]

    # ── Section: strengths ────────────────────────────────────────────────────
    strengths_section = build_strengths(disp, strengths_raw, sem_sum)

    # ── Section: improvement_strategy ────────────────────────────────────────
    sec_skills = [s.get("skill") or "" for s in sl_list]
    improvement_strat = build_improvement_strategy(skill, sec_skills)

    # ── Section: recommended_practice ────────────────────────────────────────
    recommended_practice = build_recommended_practice(top_priorities)

    # ── watch_also for short_report ───────────────────────────────────────────
    watch_also = []
    for sl in sl_list[:2]:
        sl_skill    = sl.get("skill") or ""
        sl_pressure = float(sl.get("pressure") or 0.0)
        sl_label    = _student_label(sl_skill) or sl_skill.replace("_", " ").title()
        sl_fams     = sl.get("dominant_families") or []
        top_fam     = ""
        if sl_fams:
            first = sl_fams[0]
            top_fam = _family_name(first.get("family") if isinstance(first, dict) else str(first))
        if sl_pressure >= 0.5 * pressure and sl_skill.upper() != skill.upper():
            note = f"Also watch your {sl_label.lower()}"
            note += f" — especially {top_fam}." if top_fam else "."
            focus_lbl = _focus_label(sl_skill, 2, sl_pressure, pressure, skill)
            watch_also.append({"skill": sl_skill, "student_label": sl_label,
                                "focus_label": focus_lbl, "note": note})

    # ── Section: short_report ─────────────────────────────────────────────────
    short_report = build_short_report(
        overall_summary, main_limiter_section, watch_also, band_ctx, strengths_section)

    # ── Section: detailed_report ──────────────────────────────────────────────
    detailed_report = build_detailed_report(essay_result, skill, pressure, bands)

    # ── Section: learning_intelligence ───────────────────────────────────────
    li_block = {
        "fastest_improvement_route":  pattern_i.get("fastest_improvement_route"),
        "improvement_potential":      pattern_i.get("improvement_potential"),
        "task_type_specific_note":    pattern_i.get("task_type_specific_note"),
        "dominant_failure_pattern":   pattern_i.get("dominant_failure_pattern"),
        "band_focus": {
            "rubric":  (band_unlock.get("local_unlock") or {}).get("rubric"),
            "target":  (band_unlock.get("local_unlock") or {}).get("target"),
        },
        "unlock_to_next": ((band_unlock.get("band_matrix_reference") or {}).get("unlock_to_next") or []),
        "improvement_potential_note": pattern_i.get("improvement_potential"),
    }

    # ── Section: teacher_debug ────────────────────────────────────────────────
    teacher_debug = build_teacher_debug(essay_result, validated_tids, [])

    # ── Downstream ────────────────────────────────────────────────────────────
    downstream = {
        "primary_rubric":    rubric,
        "primary_pressure":  round(pressure, 4),
        "eci":               round(eci, 4),
        "eci_tier":          tier,
        "overall_band":      overall,
        "word_count":        meta.get("word_count"),
        "task_type":         meta.get("task_type"),
    }

    bundle: Dict[str, Any] = {
        "schema_version":  FEEDBACK_BUNDLE_SCHEMA,
        "engine_version":  FEEDBACK_ENGINE_VERSION,
        "essay_id":        essay_id,
        "generated_at":    now,
        "status":          "ok" if tier == "high" else "partial",
        # V4 schema sections
        "student_feedback": {
            "overall_summary":        overall_summary,
            "main_score_limiter":     main_limiter_section,
            "top_learning_priorities": top_priorities,
            "strengths":              strengths_section,
            "improvement_strategy":   improvement_strat,
            "recommended_practice":   recommended_practice,
        },
        # V6 dual-report
        "short_report":          short_report,
        "detailed_report":       detailed_report,
        # Supporting blocks
        "learning_intelligence": li_block,
        "practice_routing": {
            "routes":           recommended_practice,
            "source_target_ids": validated_tids,
        },
        "teacher_debug": teacher_debug,
        "downstream":    downstream,
    }

    if band_ctx:
        bundle["band_context"] = band_ctx

    # ── Output quality gates (file 16) ────────────────────────────────────────
    bundle, qa_status, qa_flags_engine = apply_output_quality_gates(bundle)
    bundle["qa_status"] = qa_status
    bundle["qa_flags"]  = qa_flags_engine

    return bundle


# =============================================================================
# PART 19 — VALIDATION
# =============================================================================

def validate_feedback_bundle(bundle: Dict[str, Any]) -> List[str]:
    violations: List[str] = []

    if bundle.get("schema_version") != FEEDBACK_BUNDLE_SCHEMA:
        violations.append(f"schema_version wrong: {bundle.get('schema_version')}")
    if bundle.get("status") not in ("ok", "partial", "blocked_eci", "error"):
        violations.append(f"status invalid: {bundle.get('status')}")
    if not bundle.get("downstream"):
        violations.append("downstream missing")

    if bundle.get("status") in ("blocked_eci", "error"):
        return violations

    sf = bundle.get("student_feedback") or {}
    required_sf = ["overall_summary","main_score_limiter","top_learning_priorities",
                   "strengths","improvement_strategy","recommended_practice"]
    for field in required_sf:
        if not sf.get(field) and sf.get(field) != []:
            violations.append(f"student_feedback.{field} missing")

    imp = sf.get("improvement_strategy") or {}
    for field in ("do_first","do_next","avoid_for_now"):
        if not imp.get(field):
            violations.append(f"improvement_strategy.{field} missing")

    if not bundle.get("short_report"):
        violations.append("short_report missing")
    if not bundle.get("detailed_report"):
        violations.append("detailed_report missing")
    if not bundle.get("teacher_debug"):
        violations.append("teacher_debug missing")
    if not bundle.get("practice_routing"):
        violations.append("practice_routing missing")

    # Check for banned phrases in student-facing text
    texts_to_check = [
        sf.get("overall_summary") or "",
        (sf.get("main_score_limiter") or {}).get("explanation") or "",
    ]
    for t in texts_to_check:
        found = _check_banned_phrases(t)
        if found:
            violations.append(f"BANNED_PHRASE_IN_STUDENT_TEXT: {found}")

    # Evidence items must have A2-B1 fields
    msl    = sf.get("main_score_limiter") or {}
    for i, ev in enumerate(msl.get("examples") or []):
        for field in ("what","why_score","how_to_fix"):
            if not ev.get(field):
                violations.append(f"main_score_limiter.examples[{i}].{field} missing")

    return violations


# =============================================================================
# PART 20 — MARKDOWN RENDERER
# =============================================================================

class MarkdownRenderer:

    def render(self, bundle: Dict[str, Any]) -> str:
        eid    = bundle.get("essay_id", "?")
        status = bundle.get("status", "?")
        lines: List[str] = [f"## Essay {eid}\n"]

        if status in ("blocked_eci", "error"):
            lines += [
                "### Feedback Not Available",
                "",
                "There is not enough evidence to generate safe feedback for this essay.",
                "Please resubmit with a longer or clearer essay.",
                "",
            ]
            return "\n".join(lines)

        sf = bundle.get("student_feedback") or {}
        sr = bundle.get("short_report") or {}
        dr = bundle.get("detailed_report") or {}
        bc = bundle.get("band_context")

        # ── Overall summary ───────────────────────────────────────────────────
        lines += [
            "### Summary",
            "",
            sf.get("overall_summary") or "",
            "",
        ]

        # ── Strengths ─────────────────────────────────────────────────────────
        strengths = sf.get("strengths") or []
        if strengths:
            lines += ["### What You Do Well", ""]
            for s in strengths:
                lines.append(f"✅ {s.get('student_text', '')}")
                if s.get("how_to_use"):
                    lines.append(f"   → {s['how_to_use']}")
            lines.append("")

        # ── Main focus ────────────────────────────────────────────────────────
        msl = sf.get("main_score_limiter") or {}
        lines += [
            f"### ★ Your Main Focus: {msl.get('student_label', '')} ({msl.get('rubric_plain', '')})",
            "",
            msl.get("explanation") or "",
            "",
            f"**Why it matters:** {msl.get('why_it_matters', '')}",
            "",
        ]
        if msl.get("examples"):
            lines.append(msl.get("example_intro") or "Examples from your essay:")
            lines.append("")
            for ev in msl.get("examples") or []:
                lines += [
                    f"> ❌ **\"{ev['quote']}\"**",
                    f">",
                    f"> {ev['what']}",
                    f">",
                    f"> ✏️ {ev['how_to_fix']}",
                    "",
                ]
        lines += [f"**Your next step:** {msl.get('first_action', '')}", ""]

        # ── Top learning priorities ───────────────────────────────────────────
        priorities = sf.get("top_learning_priorities") or []
        if priorities:
            lines += ["### Your Top 3 Practice Targets", ""]
            for p in priorities:
                lines += [
                    f"**{p['priority_number']}. {p.get('student_friendly_title', '')}**",
                    "",
                    p.get("why_this_matters") or "",
                    "",
                ]
                for ev in (p.get("examples") or []):
                    lines += [
                        f"> ❌ **\"{ev['quote']}\"**",
                        f">",
                        f"> {ev['what']}",
                        f">",
                        f"> ✏️ {ev['how_to_fix']}",
                        "",
                    ]

        # ── Improvement strategy ──────────────────────────────────────────────
        imp = sf.get("improvement_strategy") or {}
        if imp:
            lines += [
                "### Your Improvement Plan",
                "",
                f"**First:** {imp.get('do_first', '')}",
                "",
                f"**Next:** {imp.get('do_next', '')}",
                "",
                f"**Avoid for now:** {imp.get('avoid_for_now', '')}",
                "",
            ]

        # ── Watch also ────────────────────────────────────────────────────────
        watch = (sr.get("watch_also") or [])
        if watch:
            lines += ["### Also Watch", ""]
            for w in watch:
                tag = {"WORK ON NEXT": "⚠️", "MONITOR": "👁", "DIAGNOSTIC": "ℹ️"}.get(
                    w.get("focus_label", ""), "•")
                lines.append(f"{tag} {w['note']}")
            lines.append("")

        lines.append("---\n")

        # ── Full picture ──────────────────────────────────────────────────────
        lines += ["### Your Full Picture", "", dr.get("intro") or "", ""]
        for item in (dr.get("weakness_profile") or []):
            badge = {"PRIMARY FOCUS": "★", "WORK ON NEXT": "⚠️",
                     "MONITOR": "👁", "DIAGNOSTIC": "ℹ️"}.get(item["focus_label"], "•")
            score_str = ""
            lines += [
                f"#### {item['rank']}. {item['student_label']} "
                f"({item['rubric_plain']}{score_str})  `{badge} {item['focus_label']}`",
                "",
                item.get("focus_note") or "",
                "",
            ]
            if not item.get("is_diagnostic"):
                for ef in (item.get("error_families") or []):
                    cnt_s = f" ({ef['count']} found)" if ef.get("count") else ""
                    lines += [f"**{ef['family_plain']}**{cnt_s}", ""]
                    for ev in (ef.get("sample_evidence") or []):
                        lines += [
                            f"> ❌ **\"{ev['quote']}\"**",
                            f">",
                            f"> {ev['what']}",
                            f">",
                            f"> ✏️ {ev['how_to_fix']}",
                            "",
                        ]

        # ── Band progress ─────────────────────────────────────────────────────
        if bc:
            lines += [
                "### Band Progress",
                "",
                f"Current band: **{bc.get('overall')}** → Target: **{bc.get('target_band')}**",
                f"{bc.get('gain_estimate', '').capitalize()} improvement potential.",
                "",
            ]

        return "\n".join(lines)


# =============================================================================
# PART 21 — CLI
# =============================================================================

def main(argv=None):
    import argparse

    p = argparse.ArgumentParser(description="VA English Premium Feedback Engine V6.4")
    p.add_argument("--input",    "-i", required=True,  help="PE V4.4 JSON output file")
    p.add_argument("--output",   "-o", default=None,   help="JSON bundle output file")
    p.add_argument("--markdown", "-m", default=None,   help="Markdown report output file")
    p.add_argument("--validate", action="store_true",  help="Validate every bundle")
    p.add_argument("--summary",  action="store_true",  help="Print console summary per essay")
    args = p.parse_args(argv)

    with open(args.input, encoding="utf-8") as f:
        pe_output = json.load(f)

    results = (
        pe_output.get("results")
        if isinstance(pe_output.get("results"), list)
        else [pe_output]
    )

    renderer   = MarkdownRenderer()
    bundles    = []
    md_parts: List[str] = ["# VA English — Premium Feedback Report\n"]
    statuses   = {}
    violations_total = 0

    for r in results:
        b = generate_feedback_bundle(r)
        bundles.append(b)
        s = b.get("status", "?")
        statuses[s] = statuses.get(s, 0) + 1

        if args.validate:
            viols = validate_feedback_bundle(b)
            if viols:
                violations_total += len(viols)
                print(f"[VALIDATE] essay {b.get('essay_id')}: {viols}")

        md_parts.append(renderer.render(b))

        if args.summary and s not in ("blocked_eci", "error"):
            sf  = b.get("student_feedback") or {}
            imp = sf.get("improvement_strategy") or {}
            msl = sf.get("main_score_limiter") or {}
            qs  = b.get("qa_status")
            print(f"\n── Essay {b['essay_id']} [{s}] [qa:{qs}] ──")
            print(f"  Focus : {msl.get('student_label')} ({msl.get('rubric_plain')})")
            print(f"  Do first: {imp.get('do_first', '')}")
            print(f"  Avoid  : {imp.get('avoid_for_now', '')}")
            prios = sf.get("top_learning_priorities") or []
            for prio in prios:
                evs = prio.get("examples") or []
                ex  = f'  e.g. "{evs[0]["quote"]}"' if evs else ""
                print(f"  P{prio['priority_number']}: {prio.get('student_friendly_title')}{ex}")
            print(f"  QA flags: {b.get('qa_flags') or []}")

    print(f"\n[ENGINE v6.4] {len(bundles)} essays processed: {statuses}")
    if args.validate:
        print(f"[ENGINE v6.4] {violations_total} total validation violations.")

    if args.output:
        out = {"schema_version": FEEDBACK_BUNDLE_SCHEMA,
               "engine_version": FEEDBACK_ENGINE_VERSION,
               "bundles": bundles}
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"[ENGINE v6.4] JSON → {args.output}")

    md_text = "\n---\n".join(md_parts)
    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as f:
            f.write(md_text)
        print(f"[ENGINE v6.4] Markdown → {args.markdown}")

    if not args.output and not args.markdown:
        print(md_text)


if __name__ == "__main__":
    raise SystemExit(main())
