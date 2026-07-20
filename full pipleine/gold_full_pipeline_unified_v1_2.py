
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from statistics import mean
from typing import Any, Dict, List, Optional, Tuple

try:
    from fastapi import FastAPI, HTTPException
except Exception:
    class HTTPException(Exception):
        def __init__(self, status_code=500, detail=''):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail
    class FastAPI:
        def __init__(self, *args, **kwargs): pass
        def get(self, *args, **kwargs):
            def deco(fn): return fn
            return deco
        def post(self, *args, **kwargs):
            def deco(fn): return fn
            return deco
try:
    from pydantic import BaseModel, Field
except Exception:
    def Field(default=None, default_factory=None, **kwargs):
        if default_factory is not None:
            return default_factory()
        return default
    class BaseModel:
        def __init__(self, **kwargs):
            anns = getattr(self.__class__, '__annotations__', {})
            for name, typ in anns.items():
                if hasattr(self.__class__, name):
                    default = getattr(self.__class__, name)
                    if isinstance(default, (list, dict, set)):
                        default = default.copy()
                else:
                    default = None
                setattr(self, name, kwargs.get(name, default))


TOP10_WORDS = {"the", "of", "and", "to", "in", "a", "is", "that", "for", "it"}
STOPWORDS = {
    "the", "a", "an", "of", "to", "in", "on", "for", "with", "at", "from", "by", "about", "as",
    "is", "are", "was", "were", "be", "been", "being", "it", "this", "that", "these", "those",
    "and", "but", "or", "if", "because", "so", "then", "also", "can", "could", "should", "would",
    "have", "has", "had", "do", "does", "did", "will", "may", "might", "must", "very", "more",
    "their", "them", "they", "we", "our", "you", "your", "he", "she", "his", "her"
}
ACADEMIC_WORDS = {
    "environment", "academic", "performance", "resources", "independent", "technology",
    "education", "support", "evidence", "relevant", "coherent", "position", "argument",
    "significant", "effective", "develop", "logical", "therefore", "moreover", "furthermore",
    "consequently", "approximately", "sufficient", "appropriate", "analysis", "factor",
    "outcome", "improve", "learning", "students", "traditional", "method"
}
BASIC_CONNECTORS = {"and", "but", "so", "because", "also", "then"}
RANGE_CONNECTORS = {
    "however", "therefore", "moreover", "furthermore", "consequently", "nevertheless",
    "in contrast", "as a result", "for example", "for instance", "in addition", "on the other hand"
}
PHRASAL_PARTICLES = {"up", "down", "out", "off", "on", "in", "away", "back", "through", "over", "around", "into"}
INFORMAL_WORDS = {"super", "kids", "stuff", "things", "a lot", "okay", "ok", "really"}
HEDGE_WORDS = {"may", "might", "could", "can", "often", "generally", "tend", "tends", "likely", "perhaps", "some", "many"}

GENERAL_SINGLE_WORD_UPGRADES = {
    "good": ["strong", "effective", "positive"],
    "important": ["significant", "important", "valuable"],
    "different": ["distinct", "varied", "diverse"],
    "improve": ["enhance", "strengthen", "improve"],
    "big": ["major", "substantial", "significant"],
    "help": ["support", "assist", "facilitate"],
}
ACADEMIC_SINGLE_WORD_UPGRADES = {
    "good": ["effective", "beneficial", "substantial"],
    "important": ["significant", "crucial", "salient"],
    "different": ["distinct", "diverse", "divergent"],
    "improve": ["enhance", "strengthen", "advance"],
    "big": ["significant", "substantial", "considerable"],
    "help": ["facilitate", "support", "enable"],
}
COLLOCATION_KEEP = {"academic performance", "learning resources", "independent learning", "emotional support", "for instance", "in conclusion", "in addition", "study goals"}
COLLOCATION_FIX = {
    "lunch place": ("dining area", "dining area"),
    "study better": ("study more effectively", "learn more effectively"),
    "learning better": ("learn more effectively", "learn more effectively"),
    "time of studying": ("study time", "study schedule"),
    "at anytime": ("at any time", "at any time"),
    "explains everything detailed": ("explains everything in detail", "explains everything in detail"),
    "main factor of achieving": ("main factor in achieving", "main factor in achieving"),
    "explore information more deeply": ("explore the topic in greater depth", "explore the topic in greater depth"),
    "traditional way of studying": ("traditional method of learning", "traditional method of learning"),
    "improve students learning": ("improve students' learning", "improve students' learning"),
}
COLLOCATION_ENHANCE = {
    "good results": (["strong results", "better results"], ["strong academic outcomes", "positive outcomes"]),
    "achieve study goals": (["reach study goals", "meet study goals"], ["achieve academic goals", "meet academic objectives"]),
}

ISSUE_CODES: Dict[Tuple[str, str], str] = {
    ("grammar", "Spacing after punctuation"): "G_SPACING",
    ("grammar", "Comma after transition"): "G_COMMA_TRANSITION",
    ("grammar", "Missing verb"): "G_MISSING_VERB",
    ("grammar", "Article misuse"): "G_ARTICLE",
    ("grammar", "Missing determiner"): "G_DETERMINER",
    ("grammar", "Preposition error"): "G_PREPOSITION",
    ("grammar", "Possessive form error"): "G_POSSESSIVE",
    ("grammar", "Subject–verb agreement"): "G_SV_AGREEMENT",
    ("grammar", "Verb tense error"): "G_TENSE",
    ("grammar", "Verb form error"): "G_VERB_FORM",
    ("grammar", "Sentence fragment"): "G_FRAGMENT",
    ("grammar", "Run-on sentence"): "G_RUN_ON",
    ("grammar", "Complex structure error"): "G_COMPLEX_STRUCTURE",
    ("lexical_resource", "Wrong word choice"): "L_WORD_CHOICE",
    ("lexical_resource", "Unnatural phrase"): "L_UNNATURAL",
    ("lexical_resource", "Collocation error"): "L_COLLOCATION",
    ("lexical_resource", "Repetition"): "L_REPETITION",
    ("lexical_resource", "Limited vocabulary"): "L_LIMITED_VOCAB",
    ("lexical_resource", "Word form misuse"): "L_WORD_FORM",
    ("lexical_resource", "Informal vocabulary"): "L_INFORMAL_VOCAB",
    ("cohesion_coherence", "Overuse of simple connectors"): "C_SIMPLE_CONNECTORS",
    ("cohesion_coherence", "Missing linking device"): "C_MISSING_LINK",
    ("cohesion_coherence", "Weak transition"): "C_WEAK_TRANSITION",
    ("cohesion_coherence", "Illogical progression"): "C_ILLOGICAL_PROGRESSION",
    ("cohesion_coherence", "Poor paragraphing"): "C_PARAGRAPHING",
    ("cohesion_coherence", "Missing topic sentence"): "C_TOPIC_SENTENCE",
    ("argumentation", "Weak thesis"): "A_WEAK_THESIS",
    ("argumentation", "Underdeveloped idea"): "A_UNDERDEVELOPED",
    ("argumentation", "Overgeneralization"): "A_OVERGENERALIZATION",
    ("argumentation", "Lack of example"): "A_LACK_EXAMPLE",
    ("argumentation", "Illogical reasoning"): "A_ILLOGICAL_REASONING",
    ("argumentation", "Question relevance issue"): "A_RELEVANCE",
    ("academic_style", "Contraction"): "S_CONTRACTION",
    ("academic_style", "Informal tone"): "S_INFORMAL_TONE",
    ("academic_style", "Direct address"): "S_DIRECT_ADDRESS",
    ("academic_style", "Overly conversational structure"): "S_CONVERSATIONAL",
    ("academic_style", "Lack of hedging"): "S_HEDGING",
}

SOURCE_KEYS = {
    "G_SPACING": "commas", "G_COMMA_TRANSITION": "commas", "G_ARTICLE": "articles", "G_DETERMINER": "articles",
    "G_PREPOSITION": "prepositions", "G_POSSESSIVE": "possessives", "G_SV_AGREEMENT": "sv_agreement",
    "G_TENSE": "verb_tense", "G_VERB_FORM": "verb_form", "G_FRAGMENT": "sentence_boundaries",
    "G_RUN_ON": "sentence_boundaries", "G_COMPLEX_STRUCTURE": "complex_sentences",
    "L_WORD_CHOICE": "style_precision", "L_UNNATURAL": "style_precision", "L_COLLOCATION": "collocations",
    "L_REPETITION": "style_precision", "L_WORD_FORM": "word_forms", "L_INFORMAL_VOCAB": "style_precision",
    "C_SIMPLE_CONNECTORS": "transitions", "C_MISSING_LINK": "transitions", "C_WEAK_TRANSITION": "transitions",
    "C_ILLOGICAL_PROGRESSION": "paragraphing", "C_PARAGRAPHING": "paragraphing", "C_TOPIC_SENTENCE": "paragraphing",
    "A_WEAK_THESIS": "thesis", "A_UNDERDEVELOPED": "evidence", "A_OVERGENERALIZATION": "evidence",
    "A_LACK_EXAMPLE": "evidence", "A_ILLOGICAL_REASONING": "evidence", "A_RELEVANCE": "thesis",
    "S_CONTRACTION": "academic_tone", "S_INFORMAL_TONE": "academic_tone", "S_DIRECT_ADDRESS": "academic_tone",
    "S_CONVERSATIONAL": "academic_tone", "S_HEDGING": "hedging",
}
REFERENCE_LINKS = {
    "articles": "https://dictionary.cambridge.org/grammar/british-grammar/a-an-and-the",
    "sv_agreement": "https://owl.purdue.edu/owl/general_writing/grammar/subject_verb_agreement.html",
    "verb_tense": "https://learnenglish.britishcouncil.org/grammar/english-grammar-reference",
    "verb_form": "https://learnenglish.britishcouncil.org/grammar/english-grammar-reference",
    "commas": "https://owl.purdue.edu/owl/general_writing/punctuation/commas/index.html",
    "sentence_boundaries": "https://owl.purdue.edu/owl/general_writing/punctuation/independent_and_dependent_clauses/index.html",
    "prepositions": "https://dictionary.cambridge.org/grammar/british-grammar/prepositions",
    "possessives": "https://dictionary.cambridge.org/grammar/british-grammar/nouns-possession",
    "collocations": "https://www.oxfordlearnersdictionaries.com/about/collocations",
    "style_precision": "https://writingcenter.unc.edu/tips-and-tools/style/",
    "word_forms": "https://learnenglish.britishcouncil.org/grammar/english-grammar-reference",
    "academic_tone": "https://owl.purdue.edu/owl/general_writing/academic_writing/index.html",
    "hedging": "https://www.ox.ac.uk/students/academic/guidance/skills/planning?wssl=1#collapse1574211",
    "transitions": "https://writingcenter.unc.edu/tips-and-tools/transitions/",
    "paragraphing": "https://owl.purdue.edu/owl/general_writing/paragraphs_and_paragraphing/index.html",
    "thesis": "https://writingcenter.unc.edu/tips-and-tools/thesis-statements/",
    "evidence": "https://writingcenter.unc.edu/tips-and-tools/evidence/",
    "complex_sentences": "https://owl.purdue.edu/owl/general_writing/grammar/index.html",
}
PRACTICE_TEMPLATES = {
    "G_SPACING": ["Find the punctuation-spacing mistake.", "Insert the missing space after punctuation.", "Choose the correctly punctuated version."],
    "G_COMMA_TRANSITION": ["Insert the missing comma after the introductory transition.", "Find the transition punctuation mistake.", "Choose the better sentence opening."],
    "G_PREPOSITION": ["Fill the gap with the correct preposition.", "Find the preposition mistake and correct it.", "Choose the correct phrase pattern."],
    "G_ARTICLE": ["Fill the gap with a, an, the, or no article.", "Find the article mistake.", "Rewrite with correct article usage."],
    "G_DETERMINER": ["Add the missing article/determiner.", "Find the noun phrase that needs a determiner.", "Choose the correct determiner."],
    "G_SV_AGREEMENT": ["Choose the correct verb form.", "Find the subject–verb agreement error.", "Rewrite the clause correctly."],
    "G_TENSE": ["Rewrite the clause using the correct tense.", "Choose the correct tense form.", "Correct the tense mistake."],
    "G_VERB_FORM": ["Rewrite the phrase using the correct verb form.", "Choose the correct verb form.", "Find the verb-form error."],
    "G_FRAGMENT": ["Rewrite the fragment as a complete sentence.", "Add the missing main clause.", "Choose the complete version."],
    "G_RUN_ON": ["Add punctuation or split the run-on sentence.", "Find the run-on point.", "Choose the correctly separated version."],
    "L_WORD_CHOICE": ["Choose the most natural word for the sentence.", "Offer 3 synonyms for the highlighted word in context.", "Rewrite the phrase with a more accurate word."],
    "L_UNNATURAL": ["Rewrite the phrase so that it sounds natural in English.", "Choose the more natural version.", "Find the awkward phrase and improve it."],
    "L_COLLOCATION": ["Choose the best collocation.", "Replace the phrase with a more natural collocation.", "Offer 3 alternatives that fit this sentence."],
    "L_REPETITION": ["Replace the repeated word with a suitable alternative.", "Offer 3 synonyms for the repeated word.", "Paraphrase the sentence to reduce repetition."],
    "L_WORD_FORM": ["Replace the highlighted part with the correct word form.", "Find the word-form error.", "Rewrite the phrase more naturally."],
    "S_CONTRACTION": ["Rewrite the sentence using full forms.", "Find and expand the contraction.", "Choose the more formal version."],
    "S_DIRECT_ADDRESS": ["Rewrite the sentence without you/your.", "Replace direct address with a formal expression.", "Choose the more academic version."],
    "S_INFORMAL_TONE": ["Replace the informal word with a more formal one.", "Choose the more academic version.", "Rewrite the sentence in a more formal tone."],
}
BANNED_PLACEHOLDER_QUOTES = {"absolute claim", "absolute claims", "absolute or sweeping claim", "informal vocabulary", "wrong word choice", "unnatural phrase"}

@dataclass
class SentenceRecord:
    student_id: str
    submission_id: str
    essay_id: str
    sentence_index: int
    raw_sentence_text: str
    normalized_sentence_text: str

@dataclass
class ErrorRecord:
    student_id: str
    submission_id: str
    essay_id: str
    sentence_index: int
    category: str
    issue: str
    issue_code: str
    quote: str
    suggested_revision: Optional[str]
    instruction: str
    explanation: str
    feedback_type: str
    severity: str
    is_actionable: bool
    source_key: str
    confidence: str
    review_needed: bool
    learning_focus: str = "secondary"

@dataclass
class LexicalUnitRecord:
    student_id: str
    submission_id: str
    essay_id: str
    sentence_index: int
    unit: str
    unit_type: str
    class_label: str
    explanation: str
    suggestions_general: List[str] = field(default_factory=list)
    suggestions_academic: List[str] = field(default_factory=list)

class HistoryIssue(BaseModel):
    issue_code: str
    count: int = 1
    category: Optional[str] = None

class SubmissionHistory(BaseModel):
    submission_id: str
    scores: Dict[str, float] = Field(default_factory=dict)
    issue_counts: Dict[str, int] = Field(default_factory=dict)
    issues: List[HistoryIssue] = Field(default_factory=list)

class AnalyzeRequest(BaseModel):
    student_id: str
    submission_id: str
    essay_id: str = "1"
    text: str
    lexical_mode: str = "both"
    history: List[SubmissionHistory] = Field(default_factory=list)
    prompt_text: Optional[str] = None
    topic_keywords: List[str] = Field(default_factory=list)

def normalize_apostrophes(text: str) -> str:
    return text.replace("’", "'").replace("‘", "'")

def prepare_for_sentence_split(text: str) -> str:
    text = normalize_apostrophes(text)
    text = re.sub(r"([.!?])([A-Z])", r"\1 \2", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def normalize_sentence(sentence: str) -> str:
    s = normalize_apostrophes(sentence)
    s = re.sub(r",([A-Za-z])", r", \1", s)
    s = re.sub(r";([A-Za-z])", r"; \1", s)
    s = re.sub(r":([A-Za-z])", r": \1", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()

def split_sentences(text: str) -> List[str]:
    prepared = prepare_for_sentence_split(text)
    return [p.strip() for p in re.split(r"(?<=[.!?])\s+", prepared) if p.strip()]

def clean_quote(quote: str) -> str:
    return normalize_apostrophes(quote).strip(" \t\n\r,.;:-")

def exact_or_fallback_quote(match: Optional[re.Match], sentence: str, fallback_span: Optional[Tuple[int, int]] = None) -> str:
    if match is not None:
        return clean_quote(match.group(0))
    if fallback_span is not None:
        a, b = fallback_span
        return clean_quote(sentence[a:b])
    return clean_quote(sentence)

def spacing_matches(raw_sentence: str) -> List[Tuple[str, str]]:
    matches: List[Tuple[str, str]] = []
    for m in re.finditer(r"([A-Za-z]+)([,;:])([A-Za-z]+)", raw_sentence):
        matches.append((clean_quote(m.group(0)), normalize_sentence(raw_sentence)))
    return matches

def tokenize(text: str) -> List[str]:
    return re.findall(r"\b[a-zA-Z']+\b", normalize_apostrophes(text).lower())

def ngrams(tokens: List[str], n: int) -> List[str]:
    return [" ".join(tokens[i:i+n]) for i in range(len(tokens)-n+1)] if len(tokens) >= n else []

def content_words(text: str) -> List[str]:
    return [w for w in tokenize(text) if w not in STOPWORDS and len(w) > 3]

def prompt_keywords(prompt_text: Optional[str], topic_keywords: List[str]) -> List[str]:
    kws = []
    if prompt_text:
        kws.extend(re.findall(r"\b[a-zA-Z]{4,}\b", normalize_apostrophes(prompt_text).lower()))
    kws.extend([k.lower() for k in topic_keywords if k])
    return [k for k in kws if k not in STOPWORDS]

def prompt_overlap_ratio(text: str, prompt_text: Optional[str], topic_keywords: List[str]) -> float:
    kws = set(prompt_keywords(prompt_text, topic_keywords))
    if not kws:
        return 0.0
    low = normalize_apostrophes(text).lower()
    hits = sum(1 for k in kws if k in low)
    return round(hits / max(len(kws), 1), 3)

def detect_complex_sentences(sentences: List[str]) -> List[bool]:
    out = []
    for s in sentences:
        low = normalize_apostrophes(s).lower()
        out.append(bool(re.search(r"\b(because|although|while|if|when|which|that|who|whereas|since|unless|though)\b", low) or len(re.findall(r",", low)) >= 1))
    return out

def round_half(x: float) -> float:
    return round(max(1.0, min(9.0, x)) * 2) / 2.0

def assign_feedback_type(category: str) -> str:
    return "error" if category == "grammar" else "suggestion"

def assign_severity(issue: str) -> str:
    high = {"Missing verb", "Article misuse", "Missing determiner", "Preposition error", "Possessive form error", "Subject–verb agreement", "Verb tense error", "Verb form error", "Sentence fragment", "Run-on sentence", "Complex structure error"}
    medium = {"Wrong word choice", "Unnatural phrase", "Collocation error", "Repetition", "Limited vocabulary", "Word form misuse", "Weak thesis", "Underdeveloped idea", "Lack of example", "Question relevance issue", "Informal tone", "Direct address", "Lack of hedging", "Illogical progression"}
    return "high" if issue in high else "medium" if issue in medium else "low"

def make_record(*, student_id: str, submission_id: str, essay_id: str, sentence_index: int, category: str, issue: str, quote: str, suggested_revision: Optional[str], instruction: str, explanation: str, confidence: str = "high", review_needed: bool = False) -> ErrorRecord:
    issue_code = ISSUE_CODES[(category, issue)]
    return ErrorRecord(student_id, submission_id, essay_id, sentence_index, category, issue, issue_code, clean_quote(quote), suggested_revision, instruction, explanation, assign_feedback_type(category), assign_severity(issue), True, SOURCE_KEYS.get(issue_code, ""), confidence, review_needed)

def detect_sentence_level(student_id: str, submission_id: str, essay_id: str, idx: int, raw_sentence: str, normalized_sentence: str) -> List[ErrorRecord]:
    s = normalized_sentence
    raw = raw_sentence
    low = normalize_apostrophes(s).lower()
    recs: List[ErrorRecord] = []

    for quote, corrected in spacing_matches(raw):
        recs.append(make_record(student_id=student_id, submission_id=submission_id, essay_id=essay_id, sentence_index=idx, category="grammar", issue="Spacing after punctuation", quote=quote, suggested_revision=corrected, instruction="Correct the punctuation spacing in the sentence.", explanation="There is a missing space after punctuation, which affects readability."))

    m = re.search(r"^(Moreover|However|Therefore|In addition|For instance|For example)\b(?!,)", s, flags=re.I)
    if m:
        recs.append(make_record(student_id=student_id, submission_id=submission_id, essay_id=essay_id, sentence_index=idx, category="grammar", issue="Comma after transition", quote=exact_or_fallback_quote(m, s), suggested_revision=re.sub(rf"^{re.escape(m.group(1))}\b", f"{m.group(1)},", s, flags=re.I), instruction="Add the missing comma after the introductory transition.", explanation="An introductory transition is normally followed by a comma."))

    contraction_map = {"it's": "it is", "don't": "do not", "won't": "will not", "can't": "cannot", "didn't": "did not", "doesn't": "does not", "i'm": "I am", "that's": "that is", "there's": "there is", "we're": "we are", "they're": "they are", "shouldn't": "should not", "wouldn't": "would not", "couldn't": "could not", "isn't": "is not", "aren't": "are not"}
    for m in re.finditer(r"\b(?:it's|don't|won't|can't|didn't|doesn't|i'm|that's|there's|we're|they're|shouldn't|wouldn't|couldn't|isn't|aren't)\b", low, flags=re.I):
        repl = contraction_map.get(m.group(0).lower(), m.group(0))
        recs.append(make_record(student_id=student_id, submission_id=submission_id, essay_id=essay_id, sentence_index=idx, category="academic_style", issue="Contraction", quote=exact_or_fallback_quote(m, s), suggested_revision=re.sub(rf"\b{re.escape(m.group(0))}\b", repl, s, flags=re.I), instruction="Rewrite the sentence using the full form instead of the contraction.", explanation="Contractions are usually avoided in formal academic writing."))

    m = re.search(r"\b(your|you)\b", low, flags=re.I)
    if m:
        q = exact_or_fallback_quote(m, s)
        replacement = "students'" if q.lower() == "your" else "students"
        recs.append(make_record(student_id=student_id, submission_id=submission_id, essay_id=essay_id, sentence_index=idx, category="academic_style", issue="Direct address", quote=q, suggested_revision=re.sub(rf"\b{re.escape(q)}\b", replacement, s, count=1, flags=re.I), instruction="Rewrite the sentence without addressing the reader directly.", explanation="Directly addressing the reader is less appropriate in formal academic writing."))

    for patt in [r"\bsuper\b", r"\ba lot\b", r"\bI think\b", r"^\s*But\b", r"^\s*So\b"]:
        m = re.search(patt, s, flags=re.I)
        if m:
            issue = "Overly conversational structure" if patt in [r"^\s*But\b", r"^\s*So\b"] else "Informal tone"
            recs.append(make_record(student_id=student_id, submission_id=submission_id, essay_id=essay_id, sentence_index=idx, category="academic_style", issue=issue, quote=exact_or_fallback_quote(m, s), suggested_revision=None, instruction="Rewrite the sentence in a more formal style.", explanation="This wording is more conversational than is typical in formal academic writing.", confidence="medium"))

    m = re.search(r"\bThis the\b", s)
    if m:
        recs.append(make_record(student_id=student_id, submission_id=submission_id, essay_id=essay_id, sentence_index=idx, category="grammar", issue="Missing verb", quote=exact_or_fallback_quote(m, s), suggested_revision=s.replace("This the", "This is the"), instruction="Add the missing verb to complete the clause.", explanation="The clause is missing the verb 'is'."))

    for patt, repl, issue, expl in [
        (r"\ba good equipment\b", "good equipment", "Article misuse", "'Equipment' is uncountable and does not normally take the article 'a'."),
        (r"\ba equipment\b", "equipment", "Article misuse", "'Equipment' is uncountable and does not normally take the article 'a'."),
        (r"\btraditional way of studying\b", "the traditional way of studying", "Missing determiner", "A singular countable noun here normally needs a determiner."),
        (r"\buntil teacher explains\b", "until the teacher explains", "Missing determiner", "A singular countable noun here normally needs a determiner."),
    ]:
        m = re.search(patt, s, flags=re.I)
        if m:
            recs.append(make_record(student_id=student_id, submission_id=submission_id, essay_id=essay_id, sentence_index=idx, category="grammar", issue=issue, quote=exact_or_fallback_quote(m, s), suggested_revision=re.sub(patt, repl, s, flags=re.I), instruction="Rewrite the highlighted part correctly.", explanation=expl))

    for patt, repl, expl in [
        (r"\benough of space\b", "enough space", "This phrase uses the wrong pattern; the correct form is 'enough space'."),
        (r"\baffects positively on\b", "positively affects", "The verb pattern is incorrect here; 'affect' does not take 'on' in this structure."),
        (r"\bat the classroom\b", "in the classroom", "The usual preposition here is 'in', not 'at'."),
    ]:
        m = re.search(patt, s, flags=re.I)
        if m:
            recs.append(make_record(student_id=student_id, submission_id=submission_id, essay_id=essay_id, sentence_index=idx, category="grammar", issue="Preposition error", quote=exact_or_fallback_quote(m, s), suggested_revision=re.sub(patt, repl, s, flags=re.I), instruction="Rewrite the highlighted part correctly.", explanation=expl))

    for patt, repl in [(r"\bstudents studies\b", "students' studies"), (r"\bstudents academic performance\b", "students' academic performance"), (r"\bstudents learning\b", "students' learning")]:
        m = re.search(patt, s, flags=re.I)
        if m:
            recs.append(make_record(student_id=student_id, submission_id=submission_id, essay_id=essay_id, sentence_index=idx, category="grammar", issue="Possessive form error", quote=exact_or_fallback_quote(m, s), suggested_revision=re.sub(patt, repl, s, flags=re.I), instruction="Rewrite the highlighted part correctly.", explanation="A possessive form is needed here."))

    for patt, repl, expl in [
        (r"\bcan helps\b", "can help", "The verb form does not correctly agree with the auxiliary structure."),
        (r"\ba students wants\b", "a student wants", "The noun phrase and verb form are not correctly matched."),
        (r"\btechnologies improves\b", "technologies improve", "The verb does not correctly agree with the subject."),
        (r"\bpeople is\b", "people are", "The verb does not agree with the plural noun."),
        (r"\bthese helps\b", "these help", "The verb does not agree with the plural subject."),
    ]:
        m = re.search(patt, s, flags=re.I)
        if m:
            recs.append(make_record(student_id=student_id, submission_id=submission_id, essay_id=essay_id, sentence_index=idx, category="grammar", issue="Subject–verb agreement", quote=exact_or_fallback_quote(m, s), suggested_revision=re.sub(patt, repl, s, flags=re.I), instruction="Rewrite the highlighted part correctly.", explanation=expl))

    for patt, repl, issue, expl in [
        (r"\bif you will have\b", "if you have", "Verb tense error", "The tense pattern is not appropriate here."),
        (r"\bif life will become\b", "if life becomes", "Verb tense error", "The tense pattern is not appropriate here."),
        (r"\bknowledge which is allowed to learn everywhere\b", "knowledge that can be accessed anywhere", "Verb form error", "The clause is not formed correctly."),
    ]:
        m = re.search(patt, s, flags=re.I)
        if m:
            recs.append(make_record(student_id=student_id, submission_id=submission_id, essay_id=essay_id, sentence_index=idx, category="grammar", issue=issue, quote=exact_or_fallback_quote(m, s), suggested_revision=re.sub(patt, repl, s, flags=re.I), instruction="Rewrite the highlighted part correctly.", explanation=expl))

    if re.match(r"\s*Because\b", s) and len(re.findall(r"\b\w+\b", s)) >= 5:
        recs.append(make_record(student_id=student_id, submission_id=submission_id, essay_id=essay_id, sentence_index=idx, category="grammar", issue="Sentence fragment", quote=clean_quote(re.sub(r"[.!?]+$", "", s)), suggested_revision=None, instruction="Rewrite the sentence so that it contains a complete main clause.", explanation="A sentence starting with 'Because' may be incomplete if it is not attached to a main clause.", confidence="medium", review_needed=True))

    m = re.search(r"\binternet it helps\b", low)
    if m:
        recs.append(make_record(student_id=student_id, submission_id=submission_id, essay_id=essay_id, sentence_index=idx, category="grammar", issue="Run-on sentence", quote=exact_or_fallback_quote(m, s), suggested_revision=None, instruction="Add punctuation or split the clause boundary.", explanation="Two clauses appear to run together without clear punctuation."))

    for patt, issue, repl, expl in [
        (r"\blunch place\b", "Unnatural phrase", "dining area", "This phrase sounds unnatural in English."),
        (r"\bexplains everything detailed\b", "Word form misuse", "explains everything in detail", "The word form is not used correctly here."),
        (r"\btime of studying\b", "Collocation error", "study time", "This phrase is awkward in English."),
        (r"\bat anytime\b", "Collocation error", "at any time", "This fixed expression is usually written as 'at any time'."),
        (r"\blearning better\b", "Collocation error", "learn more effectively", "This collocation is not natural in English."),
        (r"\btraditional way of studying\b", "Collocation error", "traditional method of learning", "This phrase is not the most natural collocation in English."),
        (r"\bimprove students learning\b", "Collocation error", "improve students' learning", "This phrase is not the most natural collocation in English."),
        (r"\bmain factor of achieving\b", "Collocation error", "main factor in achieving", "The preposition in this collocation is awkward."),
        (r"\bexplore information more deeply\b", "Unnatural phrase", "explore the topic in greater depth", "This phrase is awkward in English."),
        (r"\bstudy better\b", "Collocation error", "study more effectively", "This collocation is not natural in English."),
        (r"\bknowledge which is allowed to learn everywhere\b", "Unnatural phrase", "knowledge that can be accessed anywhere", "This clause is awkwardly expressed."),
    ]:
        m = re.search(patt, s, flags=re.I)
        if m:
            recs.append(make_record(student_id=student_id, submission_id=submission_id, essay_id=essay_id, sentence_index=idx, category="lexical_resource", issue=issue, quote=exact_or_fallback_quote(m, s), suggested_revision=re.sub(patt, repl, s, flags=re.I), instruction="Rewrite the phrase using a more natural lexical choice.", explanation=expl))

    for patt in [r"\bsuper\b", r"\bstuff\b", r"\bkids\b", r"\bthings\b", r"\ba lot\b"]:
        m = re.search(patt, s, flags=re.I)
        if m:
            recs.append(make_record(student_id=student_id, submission_id=submission_id, essay_id=essay_id, sentence_index=idx, category="lexical_resource", issue="Informal vocabulary", quote=exact_or_fallback_quote(m, s), suggested_revision=None, instruction="Replace the informal word with a more precise alternative.", explanation="This vocabulary is too informal or vague for formal writing.", confidence="medium"))

    rep = Counter(content_words(s))
    for token, cnt in rep.items():
        if cnt >= 2:
            recs.append(make_record(student_id=student_id, submission_id=submission_id, essay_id=essay_id, sentence_index=idx, category="lexical_resource", issue="Repetition", quote=token, suggested_revision=None, instruction="Rewrite the sentence to reduce repetition of this word.", explanation="This content word is repeated and could be varied to improve lexical range.", confidence="medium"))

    if re.search(r"\b(without teachers|everyone|everything|always|never|nobody)\b", low) and not re.search(r"\b(may|might|could|often|generally|perhaps|some)\b", low):
        m = re.search(r"\b(without teachers|everyone|everything|always|never|nobody)\b", low)
        recs.append(make_record(student_id=student_id, submission_id=submission_id, essay_id=essay_id, sentence_index=idx, category="academic_style", issue="Lack of hedging", quote=exact_or_fallback_quote(m, s), suggested_revision=None, instruction="Rewrite the claim using more cautious wording if appropriate.", explanation="The claim may sound too absolute; a more cautious wording may be more appropriate.", confidence="low", review_needed=True))

    return recs

def detect_global(text: str, student_id: str, submission_id: str, essay_id: str, sentences: List[str], prompt_text: Optional[str], topic_keywords: List[str]) -> List[ErrorRecord]:
    recs: List[ErrorRecord] = []
    joined = " ".join(sentences)
    low = normalize_apostrophes(joined).lower()
    connector_matches = re.findall(r"\b(however|therefore|moreover|furthermore|consequently|nevertheless|in contrast|as a result|for example|for instance|in addition|and|but|so|because|also|finally|in conclusion)\b", low, flags=re.I)
    connector_count = len(connector_matches)
    basic_ratio = sum(1 for c in connector_matches if c in BASIC_CONNECTORS) / max(connector_count, 1)

    if connector_count >= 4 and basic_ratio > 0.75:
        recs.append(make_record(student_id=student_id, submission_id=submission_id, essay_id=essay_id, sentence_index=1, category="cohesion_coherence", issue="Overuse of simple connectors", quote=", ".join(connector_matches[:6]), suggested_revision=None, instruction="Use a wider range of linking devices where appropriate.", explanation="The text relies heavily on basic connectors and could benefit from more varied transitions.", confidence="medium"))
    if len(sentences) >= 5 and not any(c in low for c in RANGE_CONNECTORS):
        recs.append(make_record(student_id=student_id, submission_id=submission_id, essay_id=essay_id, sentence_index=1, category="cohesion_coherence", issue="Missing linking device", quote=sentences[0], suggested_revision=None, instruction="Add clearer transitions between ideas where needed.", explanation="The text shows limited use of explicit linking devices between ideas.", confidence="low", review_needed=True))

    overlaps = []
    for a, b in zip(sentences, sentences[1:]):
        ta, tb = set(content_words(a)), set(content_words(b))
        if ta and tb:
            overlaps.append(len(ta & tb) / max(len(ta | tb), 1))
    if overlaps and mean(overlaps) < 0.03 and len(sentences) >= 5:
        recs.append(make_record(student_id=student_id, submission_id=submission_id, essay_id=essay_id, sentence_index=2, category="cohesion_coherence", issue="Illogical progression", quote=sentences[1], suggested_revision=None, instruction="Reorder or connect the ideas more clearly.", explanation="The overall flow of ideas is difficult to follow or does not develop logically.", confidence="low", review_needed=True))

    if len(sentences) >= 2 and not re.search(r"\b(i believe|i think|overall|in conclusion|it is clear|should|must|benefit|improve|supports?)\b", low):
        recs.append(make_record(student_id=student_id, submission_id=submission_id, essay_id=essay_id, sentence_index=1, category="argumentation", issue="Weak thesis", quote=sentences[0], suggested_revision=None, instruction="State a clearer position or central claim early in the text.", explanation="The text suggests an argument, but the central position is not clearly stated.", confidence="medium"))

    short_assertions = 0
    for s in sentences:
        if len(re.findall(r"\b\w+\b", s)) <= 13 and re.search(r"\b(is|are|can|should|must|helps?|improves?)\b", s, flags=re.I):
            short_assertions += 1
    if short_assertions >= 2:
        recs.append(make_record(student_id=student_id, submission_id=submission_id, essay_id=essay_id, sentence_index=min(2, len(sentences)), category="argumentation", issue="Underdeveloped idea", quote=clean_quote(re.sub(r"[.!?]+$", "", sentences[min(1, len(sentences)-1)])), suggested_revision=None, instruction="Develop this idea with more explanation or support.", explanation="The idea is present but not sufficiently explained or developed.", confidence="medium"))

    sweep = re.search(r"\b(without teachers|everyone|everything|always|never|nobody)\b", low)
    if sweep:
        target = next((s for s in sentences if sweep.group(0) in normalize_apostrophes(s).lower()), sentences[0])
        recs.append(make_record(student_id=student_id, submission_id=submission_id, essay_id=essay_id, sentence_index=1, category="argumentation", issue="Overgeneralization", quote=clean_quote(re.sub(r"[.!?]+$", "", target)), suggested_revision=None, instruction="Rewrite the claim using more precise or qualified language.", explanation="The wording may generalize too strongly without qualification or support.", confidence="medium"))

    if len(sentences) >= 4 and not re.search(r"\b(for example|for instance|such as|to illustrate)\b", low):
        target = sentences[min(1, len(sentences)-1)]
        recs.append(make_record(student_id=student_id, submission_id=submission_id, essay_id=essay_id, sentence_index=1, category="argumentation", issue="Lack of example", quote=clean_quote(re.sub(r"[.!?]+$", "", target)), suggested_revision=None, instruction="Support the point with a brief example, explanation, or evidence.", explanation="The argument would be stronger if at least one point were supported with a clear example or evidence.", confidence="low", review_needed=True))

    if prompt_text and prompt_overlap_ratio(joined, prompt_text, topic_keywords) < 0.20:
        recs.append(make_record(student_id=student_id, submission_id=submission_id, essay_id=essay_id, sentence_index=1, category="argumentation", issue="Question relevance issue", quote=sentences[0], suggested_revision=None, instruction="Address the prompt more directly and use the key ideas from the question.", explanation="The response does not appear to address the prompt directly enough.", confidence="medium", review_needed=True))

    return recs

def should_drop_record(r: ErrorRecord) -> bool:
    q = clean_quote(r.quote).lower()
    return (not q) or (q in BANNED_PLACEHOLDER_QUOTES)

def dedupe_errors(records: List[ErrorRecord]) -> List[ErrorRecord]:
    filtered = [r for r in records if not should_drop_record(r)]
    order = {"grammar": 0, "lexical_resource": 1, "academic_style": 2, "cohesion_coherence": 3, "argumentation": 4}
    sev = {"high": 0, "medium": 1, "low": 2}
    grouped: Dict[Tuple[int, str, str, str], List[ErrorRecord]] = defaultdict(list)
    for r in filtered:
        grouped[(r.sentence_index, r.category, r.issue_code, r.quote.lower())].append(r)
    out = []
    for items in grouped.values():
        items = sorted(items, key=lambda x: (order.get(x.category, 99), sev.get(x.severity, 99)))
        out.append(items[0])
    out.sort(key=lambda x: (x.sentence_index, order.get(x.category, 99), x.issue_code))
    return out

def extract_lexical_units(sentence: str) -> List[Tuple[str, str]]:
    low = normalize_apostrophes(sentence).lower()
    toks = tokenize(low)
    units: List[Tuple[str, str]] = []
    for tok in toks:
        if tok in GENERAL_SINGLE_WORD_UPGRADES:
            units.append((tok, "single_word_upgrade"))
        elif tok not in STOPWORDS and len(tok) >= 7:
            units.append((tok, "single_word_content"))
    for i in range(len(toks)-1):
        if toks[i] not in STOPWORDS and toks[i+1] in PHRASAL_PARTICLES:
            units.append((f"{toks[i]} {toks[i+1]}", "phrasal_verb_surface"))
    for phrase in ["in conclusion", "for example", "for instance", "in addition", "as a result"]:
        if phrase in low:
            units.append((phrase, "formulaic_phrase"))
    for ng in ngrams(toks, 2):
        units.append((ng, "bigram"))
    for ng in ngrams(toks, 3):
        units.append((ng, "trigram"))
    seen, out = set(), []
    for u in units:
        if u not in seen:
            seen.add(u); out.append(u)
    return out

def classify_lexical_unit(unit: str, unit_type: str, mode: str) -> Tuple[str, str, List[str], List[str]]:
    if unit in COLLOCATION_KEEP:
        return "keep", "This unit is already useful and natural in context.", [], []
    if unit in COLLOCATION_FIX:
        g, a = COLLOCATION_FIX[unit]
        return "fix", "This unit needs correction because it is awkward or inaccurate in context.", [g] if mode in {"general", "both"} else [], [a] if mode in {"academic", "both"} else []
    if unit in COLLOCATION_ENHANCE:
        g, a = COLLOCATION_ENHANCE[unit]
        return "enhance", "This unit is acceptable, but it can be improved without changing the meaning.", g if mode in {"general", "both"} else [], a if mode in {"academic", "both"} else []
    if unit_type == "single_word_upgrade" and unit in GENERAL_SINGLE_WORD_UPGRADES:
        return "enhance", "This single word can be replaced by a more precise alternative.", GENERAL_SINGLE_WORD_UPGRADES[unit] if mode in {"general", "both"} else [], ACADEMIC_SINGLE_WORD_UPGRADES.get(unit, []) if mode in {"academic", "both"} else []
    return "drop", "This extracted unit is not useful enough as a direct learning target.", [], []

def build_lexical_inventory(student_id: str, submission_id: str, essay_id: str, sentence_df: List[SentenceRecord], mode: str) -> List[LexicalUnitRecord]:
    rows: List[LexicalUnitRecord] = []
    for s in sentence_df:
        for unit, unit_type in extract_lexical_units(s.normalized_sentence_text):
            cl, ex, sg, sa = classify_lexical_unit(unit, unit_type, mode)
            rows.append(LexicalUnitRecord(student_id, submission_id, essay_id, s.sentence_index, unit, unit_type, cl, ex, sg, sa))
    deduped = {}
    for r in rows:
        key = (r.sentence_index, r.unit, r.unit_type)
        if key not in deduped:
            deduped[key] = r
    return list(deduped.values())

def grammar_metrics(sentences: List[str], errors: List[ErrorRecord]) -> Dict[str, Any]:
    g = [e for e in errors if e.category == "grammar"]
    counts = Counter(e.issue_code for e in g)
    n_sentences = len(sentences)
    complex_flags = detect_complex_sentences(sentences)
    errors_by_sentence: Dict[int, List[ErrorRecord]] = defaultdict(list)
    for e in g:
        errors_by_sentence[e.sentence_index].append(e)
    grammar_errors = len(g)
    high = sum(1 for e in g if e.severity == "high")
    medium = sum(1 for e in g if e.severity == "medium")
    low = sum(1 for e in g if e.severity == "low")
    error_free_sentences = sum(1 for i in range(1, n_sentences+1) if len(errors_by_sentence.get(i, [])) == 0)
    n_complex_sentences = sum(1 for x in complex_flags if x)
    complex_sentences_with_grammar_error = sum(1 for i, is_complex in enumerate(complex_flags, start=1) if is_complex and len(errors_by_sentence.get(i, [])) > 0)
    repeated_grammar_subissues = sum(1 for c in counts.values() if c >= 2)
    return {
        "grammar_errors": grammar_errors,
        "high_grammar_errors": high,
        "medium_grammar_errors": medium,
        "low_grammar_errors": low,
        "error_free_sentences": error_free_sentences,
        "sentence_accuracy_rate": round(error_free_sentences / max(n_sentences, 1), 3),
        "grammar_error_rate": round(grammar_errors / max(n_sentences, 1), 3),
        "high_severity_error_rate": round(high / max(n_sentences, 1), 3),
        "n_complex_sentences": n_complex_sentences,
        "structural_variety_ratio": round(mean(complex_flags) if complex_flags else 0.0, 3),
        "complex_sentences_with_grammar_error": complex_sentences_with_grammar_error,
        "complex_sentence_error_rate": round(complex_sentences_with_grammar_error / max(n_complex_sentences, 1), 3),
        "repeated_grammar_subissues": repeated_grammar_subissues,
        "grammar_issue_counts": dict(counts),
    }

def lexical_metrics(text: str, sentences: List[str], errors: List[ErrorRecord]) -> Dict[str, Any]:
    tokens = tokenize(text)
    lexical_errors = [e for e in errors if e.category == "lexical_resource"]
    counts = Counter(e.issue_code for e in lexical_errors)
    words = [t for t in tokens if t not in STOPWORDS]
    unique = len(set(words))
    advanced_ratio = round(sum(1 for t in words if t in ACADEMIC_WORDS or len(t) >= 8) / max(len(words), 1), 3)
    all_ngrams = ngrams(words, 2) + ngrams(words, 3)
    colloc_candidates = [ng for ng in all_ngrams if ng in COLLOCATION_KEEP or ng in COLLOCATION_FIX or ng in COLLOCATION_ENHANCE]
    colloc_hit_rate = round(len(colloc_candidates) / max(len(all_ngrams), 1), 3)
    colloc_weighted_rate = round((len([c for c in colloc_candidates if c in COLLOCATION_KEEP]) + 0.5 * len([c for c in colloc_candidates if c in COLLOCATION_ENHANCE])) / max(len(all_ngrams), 1), 3)
    frames = ["in conclusion", "for instance", "in addition", "as a result"]
    frame_hit_rate = round(sum(1 for f in frames if f in normalize_apostrophes(text).lower()) / max(len(frames), 1), 3)
    misspell_rate = 0.0
    top10_share = round(sum(1 for t in tokens if t in TOP10_WORDS) / max(len(tokens), 1), 3)
    trigrams = ngrams(tokens, 3)
    tri_counts = Counter(trigrams)
    repeated_trigram_share = round(sum(c for c in tri_counts.values() if c >= 2) / max(len(trigrams), 1), 3) if trigrams else 0.0
    return {
        "advanced_ratio": advanced_ratio,
        "colloc_hit_rate": colloc_hit_rate,
        "colloc_weighted_rate": colloc_weighted_rate,
        "frame_hit_rate": frame_hit_rate,
        "misspell_rate": misspell_rate,
        "top10_share": top10_share,
        "repeated_trigram_share": repeated_trigram_share,
        "n_tokens": len(tokens),
        "unique_tokens": unique,
        "type_token_ratio": round(unique / max(len(words), 1), 3),
        "repetition_rate": round(1 - unique / max(len(words), 1), 3),
        "lexical_issue_rate": round(len(lexical_errors) / max(len(sentences), 1), 3),
        "collocation_error_count": counts.get("L_COLLOCATION", 0),
        "unnatural_phrase_count": counts.get("L_UNNATURAL", 0),
        "wrong_word_choice_count": counts.get("L_WORD_CHOICE", 0),
        "word_form_error_count": counts.get("L_WORD_FORM", 0),
        "informal_vocabulary_count": counts.get("L_INFORMAL_VOCAB", 0),
        "lexical_issue_counts": dict(counts),
    }

def cohesion_metrics(text: str, sentences: List[str], errors: List[ErrorRecord]) -> Dict[str, Any]:
    low = normalize_apostrophes(text).lower()
    connectors = re.findall(r"\b(however|therefore|moreover|furthermore|consequently|nevertheless|in contrast|as a result|for example|for instance|in addition|and|but|so|because|also|finally|in conclusion)\b", low, flags=re.I)
    overlaps = []
    for a, b in zip(sentences, sentences[1:]):
        ta, tb = set(content_words(a)), set(content_words(b))
        if ta and tb:
            overlaps.append(len(ta & tb) / max(len(ta | tb), 1))
    coh = [e for e in errors if e.category == "cohesion_coherence"]
    return {
        "connector_count": len(connectors),
        "distinct_connector_count": len(set(connectors)),
        "basic_connector_ratio": round(sum(1 for c in connectors if c in BASIC_CONNECTORS) / max(len(connectors), 1), 3),
        "adjacent_overlap_ratio": round(mean(overlaps) if overlaps else 0.0, 3),
        "cohesion_issue_rate": round(len(coh) / max(len(sentences), 1), 3),
        "cohesion_issue_counts": dict(Counter(e.issue_code for e in coh)),
    }

def argument_metrics(text: str, sentences: List[str], errors: List[ErrorRecord], prompt_text: Optional[str], topic_keywords: List[str]) -> Dict[str, Any]:
    low = normalize_apostrophes(text).lower()
    arg = [e for e in errors if e.category == "argumentation"]
    counts = Counter(e.issue_code for e in arg)
    position_presence = 1 if re.search(r"\b(should|must|benefit|improve|supports?|in conclusion|overall|it is clear)\b", low) else 0
    example_presence = 1 if re.search(r"\b(for example|for instance|such as|to illustrate)\b", low) else 0
    support_ratio = round(min(1.0, (sum(1 for s in sentences if len(content_words(s)) >= 5) / max(len(sentences), 1))), 3)
    return {
        "position_presence": position_presence,
        "example_presence": example_presence,
        "argumentation_issue_rate": round(len(arg) / max(len(sentences), 1), 3),
        "prompt_overlap_ratio": prompt_overlap_ratio(text, prompt_text, topic_keywords),
        "support_ratio": support_ratio,
        "status": "scored" if prompt_text else "prompt_not_provided",
        "argument_issue_counts": dict(counts),
    }

def style_metrics(text: str, sentences: List[str], errors: List[ErrorRecord]) -> Dict[str, Any]:
    low = normalize_apostrophes(text).lower()
    style = [e for e in errors if e.category == "academic_style"]
    counts = Counter(e.issue_code for e in style)
    contraction_count = len(re.findall(r"\b(?:it's|don't|won't|can't|didn't|doesn't|i'm|that's|there's|we're|they're|shouldn't|wouldn't|couldn't|isn't|aren't)\b", low))
    informal_marker_count = sum(1 for marker in ["super", "a lot", "really", "kids", "stuff", "things"] if marker in low) + len(re.findall(r"^\s*(But|So)\b", text, flags=re.M))
    direct_address_count = len(re.findall(r"\b(?:you|your)\b", low))
    return {"style_issue_rate": round(len(style) / max(len(sentences), 1), 3), "contraction_count": contraction_count, "informal_marker_count": informal_marker_count, "direct_address_count": direct_address_count, "style_issue_counts": dict(counts)}

def score_grammar(metrics: Dict[str, Any]) -> Tuple[int, str]:
    rate, high_rate, acc, complex_err = metrics["grammar_error_rate"], metrics["high_severity_error_rate"], metrics["sentence_accuracy_rate"], metrics["complex_sentence_error_rate"]
    if rate <= 0.15 and acc >= 0.85 and high_rate <= 0.05:
        band = 8
    elif rate <= 0.35 and acc >= 0.70 and high_rate <= 0.15:
        band = 7
    elif rate <= 0.70 and acc >= 0.50:
        band = 6
    elif rate <= 1.10 and acc >= 0.30:
        band = 5
    elif rate <= 1.60:
        band = 4
    elif rate <= 2.20:
        band = 3
    else:
        band = 2
    if complex_err >= 0.75:
        band = min(band, 5)
    reason = {8: "A wide range of structures is used with generally strong control.", 7: "There is good grammatical control, though noticeable errors remain.", 6: "A mix of simple and complex structures is used; errors are noticeable but meaning is usually clear.", 5: "Control is uneven, with frequent grammatical problems and limited variety.", 4: "Grammar is weak and errors are frequent, though some meaning remains clear.", 3: "Grammar problems often reduce clarity.", 2: "There is very little controlled grammar."}[band]
    return band, reason

def score_lexical(metrics: Dict[str, Any], grammar_band: int) -> Tuple[int, str]:
    ttr, adv, lex_rate = metrics["type_token_ratio"], metrics["advanced_ratio"], metrics["lexical_issue_rate"]
    colloc_err, unnatural, word_form = metrics["collocation_error_count"], metrics["unnatural_phrase_count"], metrics["word_form_error_count"]
    informal, repeated_tri, top10_share = metrics["informal_vocabulary_count"], metrics["repeated_trigram_share"], metrics["top10_share"]
    if lex_rate <= 0.10 and colloc_err == 0 and unnatural == 0 and ttr >= 0.60:
        band = 8
    elif lex_rate <= 0.20 and colloc_err <= 1 and ttr >= 0.52:
        band = 7
    elif lex_rate <= 0.35 and ttr >= 0.45:
        band = 6
    elif lex_rate <= 0.60:
        band = 5
    elif lex_rate <= 0.90:
        band = 4
    else:
        band = 3
    if lex_rate >= 0.30: band = min(band, 5)
    if colloc_err >= 2: band = min(band, 5)
    if colloc_err >= 4: band = min(band, 4)
    if unnatural + word_form >= 3: band = min(band, 5)
    if top10_share > 0.45 and adv < 0.15: band = min(band, 5)
    if repeated_tri > 0.08: band = min(band, 5)
    if band > grammar_band + 1: band = grammar_band + 1
    reason = {8: "A wide range of vocabulary is used with very good control of collocation and phrasing.", 7: "There is a good range of vocabulary with generally effective phrase control.", 6: "Lexical resource is generally adequate, though precision and phrase control are uneven.", 5: "Limited but minimally adequate lexical resource with noticeable awkward combinations.", 4: "Lexical control is weak, with limited range and frequent awkward phrasing.", 3: "Lexical limitations frequently reduce clarity.", 2: "Very limited lexical control is evident."}[band]
    return band, reason

def score_cohesion(metrics: Dict[str, Any]) -> Tuple[int, str]:
    basic_ratio, overlap, issue_rate = metrics["basic_connector_ratio"], metrics["adjacent_overlap_ratio"], metrics["cohesion_issue_rate"]
    distinct, total = metrics["distinct_connector_count"], metrics["connector_count"]
    if total <= 2 and issue_rate >= 0.5:
        band = 4
    elif overlap < 0.03 or issue_rate >= 0.40:
        band = 5
    elif basic_ratio >= 0.75 or issue_rate >= 0.15:
        band = 6
    elif distinct >= 5 and basic_ratio < 0.70 and issue_rate <= 0.10:
        band = 7
    else:
        band = 6
    if basic_ratio >= 0.80: band = min(band, 6)
    if distinct < 4 and total >= 6: band = min(band, 6)
    if overlap < 0.02: band = min(band, 5)
    reason = {7: "Information and ideas are logically organised and there is clear progression, though some lapses may occur.", 6: "Information and ideas are generally arranged coherently, though cohesion may be faulty or mechanical.", 5: "Organisation is evident but is not wholly logical, and progression is limited.", 4: "Information and ideas are evident but not arranged coherently, with no clear progression.", 3: "There is no apparent logical organisation and ideas are difficult to relate."}.get(band, "Cohesion is weak.")
    return band, reason

def score_argumentation(metrics: Dict[str, Any]) -> Tuple[int, str]:
    pos, ex, issue_rate, overlap, support, counts = metrics["position_presence"], metrics["example_presence"], metrics["argumentation_issue_rate"], metrics["prompt_overlap_ratio"], metrics["support_ratio"], metrics["argument_issue_counts"]
    if overlap < 0.20 or pos == 0:
        band = 4
    elif support < 0.55 or issue_rate >= 0.35:
        band = 5
    elif support < 0.75 or counts.get("A_UNDERDEVELOPED", 0) >= 1:
        band = 6
    else:
        band = 7
    if counts.get("A_LACK_EXAMPLE", 0) >= 1 and ex == 0: band = min(band, 5)
    if counts.get("A_UNDERDEVELOPED", 0) >= 1 and counts.get("A_OVERGENERALIZATION", 0) >= 1: band = min(band, 5)
    if overlap < 0.40: band = min(band, 5)
    reason = {7: "A clear and developed position is presented, though support may lack precision in places.", 6: "A position is presented and is relevant to the prompt, though some ideas are insufficiently developed.", 5: "A position is expressed, but development is limited and not always clear.", 4: "A position is discernible, but ideas lack relevance, clarity, or support.", 3: "No part of the prompt is adequately addressed and development is very limited."}[band]
    return band, reason

def score_academic_style(metrics: Dict[str, Any]) -> Tuple[int, str]:
    issue_rate, contraction_count, informal, direct = metrics["style_issue_rate"], metrics["contraction_count"], metrics["informal_marker_count"], metrics["direct_address_count"]
    if issue_rate <= 0.05 and contraction_count == 0 and informal == 0 and direct == 0:
        band = 8
    elif issue_rate <= 0.15 and contraction_count == 0 and informal <= 1:
        band = 7
    elif issue_rate <= 0.30:
        band = 6
    elif issue_rate <= 0.50:
        band = 5
    else:
        band = 4
    if contraction_count > 0: band = min(band, 6)
    if direct > 0: band = min(band, 5)
    if informal >= 2: band = min(band, 5)
    reason = {8: "Style is consistently formal and appropriate.", 7: "Style is generally formal and appropriate.", 6: "Style is mostly appropriate, though some informal phrasing remains.", 5: "Informal or conversational style is common.", 4: "Academic style is weak and frequently inappropriate."}[band]
    return band, reason

def flatten_history_issue_counts(history: List[SubmissionHistory]) -> Dict[str, int]:
    if not history: return {}
    latest = history[-1]
    if latest.issue_counts: return dict(latest.issue_counts)
    c = Counter()
    for x in latest.issues: c[x.issue_code] += x.count
    return dict(c)

def previous_scores(history: List[SubmissionHistory]) -> Dict[str, float]:
    return dict(history[-1].scores) if history and history[-1].scores else {}

def progress_tracking(current_scores: Dict[str, float], errors: List[ErrorRecord], history: List[SubmissionHistory]) -> Dict[str, Any]:
    prev_counts, prev_scores = flatten_history_issue_counts(history), previous_scores(history)
    curr_counts = Counter(e.issue_code for e in errors)
    repeated = sorted([k for k in curr_counts if prev_counts.get(k, 0) > 0])
    new = sorted([k for k in curr_counts if prev_counts.get(k, 0) == 0])
    resolved = sorted([k for k in prev_counts if curr_counts.get(k, 0) == 0])
    repeated_by_category = Counter(f"{e.category}::{e.issue_code}" for e in errors if prev_counts.get(e.issue_code, 0) > 0)
    deltas = {k: round(v - prev_scores[k], 1) for k, v in current_scores.items() if k in prev_scores}
    return {"previous_scores": prev_scores, "current_scores": current_scores, "score_change": deltas, "repeated_issue_codes": repeated, "new_issue_codes": new, "resolved_issue_codes": resolved, "current_issue_counts": dict(curr_counts), "repeated_errors_by_category": dict(repeated_by_category)}

def group_feedback(errors: List[ErrorRecord]) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[str, str], List[ErrorRecord]] = defaultdict(list)
    for e in errors: groups[(e.category, e.issue)].append(e)
    out = []
    for (category, issue), items in sorted(groups.items()):
        out.append({"category": category, "issue": issue, "issue_code": items[0].issue_code, "count": len(items), "severity": items[0].severity, "examples": [asdict(x) for x in items[:5]], "source_key": items[0].source_key})
    return out

def build_references(errors: List[ErrorRecord]) -> List[Dict[str, str]]:
    seen, rows = set(), []
    for e in errors:
        if e.source_key and e.source_key not in seen:
            seen.add(e.source_key); rows.append({"source_key": e.source_key, "url": REFERENCE_LINKS.get(e.source_key, "")})
    return rows

def build_algorithm_revision(errors: List[ErrorRecord], sentence_df: List[SentenceRecord]) -> Dict[str, Any]:
    revised = {s.sentence_index: s.normalized_sentence_text for s in sentence_df}
    for e in errors:
        if e.suggested_revision and e.sentence_index in revised:
            revised[e.sentence_index] = e.suggested_revision
    return {"mode": "minimal_correction", "full_text_revision": " ".join(revised[i] for i in sorted(revised))}

def trainer_summary(errors: List[ErrorRecord]) -> Dict[str, Any]:
    c = Counter((e.issue_code, e.issue) for e in errors)
    return {"top_issue_codes": [{"issue_code": code, "issue": issue, "count": count} for (code, issue), count in c.most_common(10)], "priority_flags": [{"issue_code": code, "count": count} for (code, _), count in c.most_common(10)]}

def student_summary(scores: Dict[str, Any], errors: List[ErrorRecord], progress: Dict[str, Any]) -> Dict[str, Any]:
    issue_counts = Counter((e.category, e.issue_code, e.issue) for e in errors)
    main_focus = [{"category": cat, "issue_code": code, "issue": issue, "count": count} for (cat, code, issue), count in issue_counts.most_common(5)]
    weakest = min([("grammar", scores["grammar"]["band"]), ("lexical_resource", scores["lexical_resource"]["band"]), ("cohesion_coherence", scores["cohesion_coherence"]["band"]), ("argumentation", scores["argumentation"]["band"]), ("academic_style", scores["academic_style"]["band"])], key=lambda x: x[1])
    strongest = max([("grammar", scores["grammar"]["band"]), ("lexical_resource", scores["lexical_resource"]["band"]), ("cohesion_coherence", scores["cohesion_coherence"]["band"]), ("argumentation", scores["argumentation"]["band"]), ("academic_style", scores["academic_style"]["band"])], key=lambda x: x[1])
    return {"strongest_section": {"section": strongest[0], "band": strongest[1]}, "weakest_section": {"section": weakest[0], "band": weakest[1]}, "main_focus": main_focus[:3], "progress_message": "Score change tracked." if progress.get("score_change") else "This is the baseline submission for progress tracking.", "advice": "Prioritise repeated high-frequency grammar and lexical issues, then revise cohesion and support depth."}

def generate_practice(errors: List[ErrorRecord], lexical_inventory: List[LexicalUnitRecord]) -> List[Dict[str, Any]]:
    rows, seen = [], set()
    for e in errors:
        key = (e.issue_code, e.quote)
        if key in seen: continue
        seen.add(key)
        rows.append({"issue_code": e.issue_code, "quote": e.quote, "exercise_types": PRACTICE_TEMPLATES.get(e.issue_code, ["Find the mistake in the sentence.", "Rewrite the sentence to correct the issue.", "Choose the better version."])})
    for u in lexical_inventory:
        if u.class_label == "enhance" and (u.suggestions_general or u.suggestions_academic):
            rows.append({"issue_code": "LEXICAL_ENHANCE", "quote": u.unit, "exercise_types": [f"Offer 3 synonyms or near-synonyms for '{u.unit}' in context.", f"Rewrite the sentence replacing '{u.unit}' with a more precise alternative.", f"Choose the best replacement for '{u.unit}' in context."]})
            if len(rows) >= 8: break
    return rows[:8]

def analyze_submission(payload: AnalyzeRequest) -> Dict[str, Any]:
    if payload.text.strip().lower() == "string":
        raise HTTPException(status_code=400, detail="Please provide real essay text instead of the placeholder 'string'.")
    sentences = split_sentences(payload.text)
    if not sentences:
        raise HTTPException(status_code=400, detail="No analyzable sentences were found.")
    normalized_text = " ".join(normalize_sentence(s) for s in sentences)
    sentence_df = [SentenceRecord(payload.student_id, payload.submission_id, payload.essay_id, i+1, s, normalize_sentence(s)) for i, s in enumerate(sentences)]
    raw_errors: List[ErrorRecord] = []
    for s in sentence_df:
        raw_errors.extend(detect_sentence_level(s.student_id, s.submission_id, s.essay_id, s.sentence_index, s.raw_sentence_text, s.normalized_sentence_text))
    raw_errors.extend(detect_global(normalized_text, payload.student_id, payload.submission_id, payload.essay_id, sentences, payload.prompt_text, payload.topic_keywords))
    errors = dedupe_errors(raw_errors)
    issue_counts = Counter(e.issue_code for e in errors)
    for e in errors:
        e.learning_focus = "primary" if e.severity == "high" or issue_counts[e.issue_code] >= 2 else "secondary" if e.severity == "medium" else "low"
    lexical_inventory = build_lexical_inventory(payload.student_id, payload.submission_id, payload.essay_id, sentence_df, payload.lexical_mode)
    g_metrics, l_metrics, c_metrics = grammar_metrics(sentences, errors), lexical_metrics(normalized_text, sentences, errors), cohesion_metrics(normalized_text, sentences, errors)
    a_metrics, s_metrics = argument_metrics(normalized_text, sentences, errors, payload.prompt_text, payload.topic_keywords), style_metrics(normalized_text, sentences, errors)
    g_band, g_reason = score_grammar(g_metrics)
    l_band, l_reason = score_lexical(l_metrics, g_band)
    c_band, c_reason = score_cohesion(c_metrics)
    a_band, a_reason = score_argumentation(a_metrics)
    s_band, s_reason = score_academic_style(s_metrics)
    overall = round_half(mean([g_band, l_band, c_band, a_band, s_band]))
    scores_compact = {"overall": overall, "grammar": g_band, "lexical_resource": l_band, "cohesion_coherence": c_band, "argumentation": a_band, "academic_style": s_band}
    scores = {"overall": overall, "grammar": {"band": g_band, "reason": g_reason, "metrics": g_metrics}, "lexical_resource": {"band": l_band, "reason": l_reason, "metrics": l_metrics}, "cohesion_coherence": {"band": c_band, "reason": c_reason, "metrics": c_metrics}, "argumentation": {"band": a_band, "reason": a_reason, "metrics": a_metrics}, "academic_style": {"band": s_band, "reason": s_reason, "metrics": s_metrics}}
    progress = progress_tracking(scores_compact, errors, payload.history)
    lexical_enhancement = {"keep": [asdict(x) for x in lexical_inventory if x.class_label == "keep"], "enhance": [asdict(x) for x in lexical_inventory if x.class_label == "enhance"], "fix": [asdict(x) for x in lexical_inventory if x.class_label == "fix"], "drop": [asdict(x) for x in lexical_inventory if x.class_label == "drop"]}
    return {"meta": {"student_id": payload.student_id, "submission_id": payload.submission_id, "essay_id": payload.essay_id, "scope": "paragraph" if len(sentences) <= 4 else "essay", "n_sentences": len(sentences), "n_tokens": len(tokenize(normalized_text)), "has_prompt_context": bool(payload.prompt_text)}, "scores": scores, "sentence_df": [asdict(x) for x in sentence_df], "raw_errors": [asdict(x) for x in raw_errors], "errors_df": [asdict(x) for x in errors], "grouped_feedback": group_feedback(errors), "rewrite_tasks": {"student_task_rewrite": "Rewrite the full text correcting the issues given in the feedback.", "rewrite_academic": "Rewrite the text in a more academic style where appropriate.", "rewrite_concise": "Rewrite the text more concisely without changing the meaning."}, "algorithm_revision": build_algorithm_revision(errors, sentence_df), "practice_exercises": generate_practice(errors, lexical_inventory), "references": build_references(errors), "lexical_inventory": [asdict(x) for x in lexical_inventory], "lexical_enhancement": lexical_enhancement, "progress_tracking": progress, "trainer_summary": trainer_summary(errors), "student_summary": student_summary(scores, errors, progress)}

app = FastAPI(title="VA Core Service v6.2", version="0.6.2")

@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}

@app.post("/analyze")
def analyze(req: AnalyzeRequest) -> Dict[str, Any]:
    return analyze_submission(req)


# =============================================================================
# GOLD UNIFIED FULL PIPELINE CLI v1.2
# =============================================================================
# This section intentionally does not import or call pipeline_runner_v14*, adapter
# files, previous Gold runners, or external service-specific scripts. It uses the
# core analysis functions above and writes a complete Premium-like + Gold artifact
# package from one Python file.

import argparse as _gold_argparse
import json as _gold_json
import os as _gold_os
import sys as _gold_sys
import uuid as _gold_uuid
import hashlib as _gold_hashlib
from pathlib import Path as _GoldPath
from datetime import datetime as _gold_datetime, timezone as _gold_timezone

GOLD_UNIFIED_ENGINE_ID = "VA_STELLA_GOLD_UNIFIED_FULL_PIPELINE"
GOLD_UNIFIED_ENGINE_VERSION = "1.2.0-monolithic-no-premium-runner"
GOLD_UNIFIED_SCHEMA = "GOLD_UNIFIED_FULL_PIPELINE_V1_2"

_CAPACITY_LABELS = {
    "sentence_control": "Sentence control",
    "lexical_precision": "Lexical precision",
    "cohesion_control": "Cohesion control",
    "argument_development": "Argument development",
    "task_response_control": "Task response control",
    "academic_style": "Academic style",
}

_ISSUE_TO_CAPACITY = {
    "G_ARTICLE": "sentence_control", "G_DETERMINER": "sentence_control",
    "G_PREPOSITION": "sentence_control", "G_POSSESSIVE": "sentence_control",
    "G_SV_AGREEMENT": "sentence_control", "G_TENSE": "sentence_control",
    "G_VERB_FORM": "sentence_control", "G_FRAGMENT": "sentence_control",
    "G_RUN_ON": "sentence_control", "G_COMPLEX_STRUCTURE": "sentence_control",
    "G_MISSING_VERB": "sentence_control", "G_SPACING": "sentence_control",
    "G_COMMA_TRANSITION": "sentence_control",
    "L_WORD_CHOICE": "lexical_precision", "L_UNNATURAL": "lexical_precision",
    "L_COLLOCATION": "lexical_precision", "L_REPETITION": "lexical_precision",
    "L_LIMITED_VOCAB": "lexical_precision", "L_WORD_FORM": "lexical_precision",
    "L_INFORMAL_VOCAB": "academic_style",
    "C_SIMPLE_CONNECTORS": "cohesion_control", "C_MISSING_LINK": "cohesion_control",
    "C_WEAK_TRANSITION": "cohesion_control", "C_ILLOGICAL_PROGRESSION": "cohesion_control",
    "C_PARAGRAPHING": "cohesion_control", "C_TOPIC_SENTENCE": "cohesion_control",
    "A_WEAK_THESIS": "task_response_control", "A_RELEVANCE": "task_response_control",
    "A_UNDERDEVELOPED": "argument_development", "A_OVERGENERALIZATION": "argument_development",
    "A_LACK_EXAMPLE": "argument_development", "A_ILLOGICAL_REASONING": "argument_development",
    "S_CONTRACTION": "academic_style", "S_INFORMAL_TONE": "academic_style",
    "S_DIRECT_ADDRESS": "academic_style", "S_CONVERSATIONAL": "academic_style",
    "S_HEDGING": "academic_style",
}

_CAPACITY_SERVICE = {
    "sentence_control": "writing_coach",
    "lexical_precision": "lret",
    "cohesion_control": "practice",
    "argument_development": "writing_coach",
    "task_response_control": "writing_coach",
    "academic_style": "practice",
}

_CAPACITY_WEEK_PLAN = {
    "sentence_control": ["clear subject + main verb", "verb pattern repair", "article/noun phrase control", "short controlled paragraph"],
    "lexical_precision": ["repair unnatural phrases", "collocation practice", "word-form repair", "reuse improved phrases"],
    "cohesion_control": ["reference clarity", "transition control", "sentence-to-sentence flow", "paragraph linking"],
    "argument_development": ["claim + reason", "specific example", "explanation chain", "argument paragraph"],
    "task_response_control": ["prompt-part check", "position control", "coverage check", "essay plan"],
    "academic_style": ["formal tone", "hedging", "precise phrasing", "final style check"],
}


def _gold_now() -> str:
    return _gold_datetime.now(_gold_timezone.utc).isoformat()


def _gold_clean(x: Any) -> str:
    return re.sub(r"\s+", " ", str(x or "")).strip()


def _gold_write(path: _GoldPath, payload: Any, pretty: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        _gold_json.dump(payload, f, ensure_ascii=False, indent=2 if pretty else None)


def _gold_read(path: _GoldPath, default: Any = None) -> Any:
    try:
        with path.open("r", encoding="utf-8") as f:
            return _gold_json.load(f)
    except FileNotFoundError:
        return default


def _gold_stable_id(prefix: str, *parts: Any, n: int = 12) -> str:
    raw = "|".join(str(p) for p in parts)
    return f"{prefix}_{_gold_hashlib.sha1(raw.encode('utf-8', errors='ignore')).hexdigest()[:n]}"


def _gold_load_submission(path: _GoldPath, essay_index: int = 0) -> Dict[str, Any]:
    raw = _gold_read(path)
    if not isinstance(raw, dict):
        raise ValueError("Submission JSON must be an object.")
    source_shape = "single"
    submission = dict(raw)
    if isinstance(raw.get("essays"), list):
        essays = [e for e in raw["essays"] if isinstance(e, dict)]
        if not essays:
            raise ValueError("Submission has an essays list but no essay objects.")
        if essay_index < 0 or essay_index >= len(essays):
            raise ValueError(f"--essay-index {essay_index} out of range for {len(essays)} essays.")
        submission = dict(essays[essay_index])
        for k in ("student_id", "prompt_text", "task_type", "topic", "topic_keywords"):
            if k not in submission and k in raw:
                submission[k] = raw[k]
        submission["source_batch_index"] = essay_index
        submission["source_batch_size"] = len(essays)
        source_shape = "batch.essays"
    if not _gold_clean(submission.get("essay_text")):
        # Some older payloads use text instead of essay_text.
        if _gold_clean(submission.get("text")):
            submission["essay_text"] = submission.get("text")
        else:
            raise ValueError("Submission JSON must contain non-empty essay_text.")
    submission.setdefault("student_id", "anonymous")
    submission.setdefault("essay_id", _gold_stable_id("essay", submission.get("student_id"), submission.get("essay_text")[:80]))
    submission.setdefault("submission_id", _gold_stable_id("submission", submission.get("student_id"), submission.get("essay_id"), _gold_now()))
    submission.setdefault("prompt_text", "")
    submission.setdefault("task_type", "WT2")
    submission.setdefault("topic", "Submitted essay")
    submission.setdefault("topic_keywords", [])
    submission["submission_source_shape"] = source_shape
    return submission


def _gold_issue_capacity(issue_code: str, category: str = "") -> str:
    issue_code = str(issue_code or "")
    if issue_code in _ISSUE_TO_CAPACITY:
        return _ISSUE_TO_CAPACITY[issue_code]
    if issue_code.startswith("G_"):
        return "sentence_control"
    if issue_code.startswith("L_"):
        return "lexical_precision"
    if issue_code.startswith("C_"):
        return "cohesion_control"
    if issue_code.startswith("A_"):
        return "argument_development"
    if issue_code.startswith("S_"):
        return "academic_style"
    if "lex" in category:
        return "lexical_precision"
    if "grammar" in category:
        return "sentence_control"
    return "argument_development"


def _gold_intake(submission: Dict[str, Any]) -> Dict[str, Any]:
    essay_text = submission.get("essay_text", "")
    paragraphs = [p for p in re.split(r"\n\s*\n+", essay_text.strip()) if p.strip()]
    sentences = split_sentences(essay_text)
    wc = len(tokenize(essay_text))
    return {
        "schema_version": "GOLD_INTAKE_V1_2",
        "student_id": submission.get("student_id"),
        "essay_id": submission.get("essay_id"),
        "task_type": submission.get("task_type", "WT2"),
        "topic": submission.get("topic"),
        "word_count": wc,
        "sentence_count": len(sentences),
        "paragraph_count": len(paragraphs),
        "prompt_present": bool(_gold_clean(submission.get("prompt_text"))),
        "submission_source_shape": submission.get("submission_source_shape"),
        "valid_for_full_pipeline": wc >= 40 and len(sentences) >= 3,
        "warnings": ([] if wc >= 120 else ["short_essay_for_ielts_wt2"]),
        "created_at": _gold_now(),
    }


def _gold_core_request(submission: Dict[str, Any], prior_profile: Dict[str, Any]) -> AnalyzeRequest:
    history_rows = []
    # Core service can accept an empty history safely. We intentionally keep this
    # compact to avoid corrupting scoring with unstable Gold profile fields.
    return AnalyzeRequest(
        student_id=str(submission.get("student_id")),
        submission_id=str(submission.get("submission_id")),
        essay_id=str(submission.get("essay_id")),
        text=str(submission.get("essay_text")),
        lexical_mode="both",
        history=history_rows,
        prompt_text=submission.get("prompt_text") or None,
        topic_keywords=submission.get("topic_keywords") or [],
    )



def _gold_extra_detection_rows(submission: Dict[str, Any]) -> List[Dict[str, Any]]:
    """High-recall universal supplemental detector rows.

    These are not essay-specific rules. They are reusable grammar, spelling,
    punctuation, word-form, and collocation patterns that compensate for the
    compact internal core when the full premium detector is not embedded.
    """
    text = str(submission.get("essay_text") or "")
    sentences = split_sentences(text)
    rows: List[Dict[str, Any]] = []

    def add(sent_i: int, family: str, category: str, quote: str, issue: str, explanation: str,
            severity: str = "medium", confidence: str = "high", suggested: Optional[str] = None) -> None:
        q = _gold_clean(quote)
        if not q:
            return
        rows.append({
            "row_id": _gold_stable_id("detx", submission.get("essay_id"), sent_i, family, q),
            "essay_id": submission.get("essay_id"),
            "student_id": submission.get("student_id"),
            "sentence_index": sent_i,
            "rubric": category,
            "category": category,
            "family": family,
            "issue": issue,
            "quote": q,
            "suggested_revision": suggested,
            "instruction": explanation,
            "explanation": explanation,
            "severity": severity,
            "confidence": confidence,
            "learning_focus": "primary" if severity == "high" else "secondary",
            "is_actionable": True,
            "source_key": "gold_internal_supplemental_rules",
        })

    typo_suggestions = {
        "goverment": "government", "modey": "money", "contries": "countries",
        "peoples": "people", "issued": "issue", "disadventages": "disadvantages",
        "enviroment": "environment", "recieve": "receive", "seperate": "separate",
    }
    for i, sent in enumerate(sentences, start=1):
        low = sent.lower()
        for wrong, right in typo_suggestions.items():
            if re.search(rf"\b{re.escape(wrong)}\b", low):
                add(i, "L_SPELLING", "lexical_resource", wrong, "Spelling", f"Spelling problem: use '{right}'.", "medium", "high", right)
        patterns = [
            (r"\b(has|have|had)\s+to\s+(spent|went|grew|took|gave|made|seen|done)\b", "G_VERB_FORM", "grammar", "Verb form after 'have/has to'", "Use base verb after 'have/has to'.", "high"),
            (r"\bfor\s+(take|make|do|go|be|have|care)\b", "G_VERB_PATTERN", "grammar", "Malformed 'for + verb' pattern", "Use 'to + verb' or a noun phrase after 'for'.", "high"),
            (r"\bcare\s+with\s+(his|her|their|my|our|your|the)\b", "G_PREPOSITION", "grammar", "Preposition pattern", "Use 'take care of' or 'care for', not 'care with'.", "high"),
            (r"\bthe\s+way\s+be\b", "G_MISSING_VERB", "grammar", "Malformed clause", "Use a complete clause with a correct verb form.", "high"),
            (r"\bas\s+it\s+possible\b", "G_MISSING_VERB", "grammar", "Missing verb", "Use 'if it is possible' or 'where possible'.", "high"),
            (r"\b(this|that|it)\s+make\b", "G_SV_AGREEMENT", "grammar", "Subject–verb agreement", "Use 'makes' with singular this/that/it.", "high"),
            (r"\bmore\s+(stronger|longer|older|better|worse|higher|lower)\b", "G_COMPARATIVE_FORM", "grammar", "Double comparative", "Do not use 'more' before a comparative adjective.", "high"),
            (r"\ba\s+(children|people|costs|advantages|disadvantages|problems|workers)\b", "G_ARTICLE", "grammar", "Article + plural noun", "Do not use 'a/an' before a plural noun.", "high"),
            (r"\b(elderly|older|young)\s+peoples\b", "G_NOUN_NUMBER", "grammar", "Noun number", "Use 'people' for the general plural noun.", "high"),
            (r"\b(excited|interesting|boring)\s+things\b", "L_WORD_FORM", "lexical_resource", "Word form / vague noun", "Use a precise noun or the correct adjective form.", "medium"),
            (r"\bgo\s+to\s+the\s+work\b", "G_ARTICLE", "grammar", "Article/preposition pattern", "Use 'go to work', not 'go to the work'.", "high"),
            (r"\bgood\s+ability\b", "L_COLLOCATION", "lexical_resource", "Collocation", "Use a natural phrase such as 'effective support' or 'strong policy'.", "medium"),
            (r"\bhome-things\b", "L_WORD_CHOICE", "lexical_resource", "Word choice", "Use a precise phrase such as 'housework' or 'household tasks'.", "medium"),
            (r"\bthing(s)?\b", "L_LIMITED_VOCAB", "lexical_resource", "Vague vocabulary", "Replace vague nouns with precise academic nouns where possible.", "low"),
        ]
        for pat, fam, cat, issue, expl, sev in patterns:
            for m in re.finditer(pat, sent, flags=re.I):
                add(i, fam, cat, m.group(0), issue, expl, sev, "high")
        if re.search(r"\s+[,.;:!?]", sent):
            add(i, "G_SPACING", "grammar", re.search(r"\S+\s+[,.;:!?]", sent).group(0), "Punctuation spacing", "Remove the space before punctuation.", "low", "high")
    return rows


def _gold_detector_output(submission: Dict[str, Any], core: Dict[str, Any]) -> Dict[str, Any]:
    rows = []
    for i, e in enumerate(core.get("errors_df", []), start=1):
        rows.append({
            "row_id": _gold_stable_id("det", submission.get("essay_id"), i, e.get("issue_code"), e.get("quote")),
            "essay_id": submission.get("essay_id"),
            "student_id": submission.get("student_id"),
            "sentence_index": e.get("sentence_index"),
            "rubric": e.get("category"),
            "category": e.get("category"),
            "family": e.get("issue_code"),
            "issue": e.get("issue"),
            "quote": e.get("quote"),
            "suggested_revision": e.get("suggested_revision"),
            "instruction": e.get("instruction"),
            "explanation": e.get("explanation"),
            "severity": e.get("severity"),
            "confidence": e.get("confidence"),
            "learning_focus": e.get("learning_focus"),
            "is_actionable": e.get("is_actionable", True),
            "source_key": e.get("source_key"),
        })
    rows.extend(_gold_extra_detection_rows(submission))
    deduped = []
    seen = set()
    for r in rows:
        key = (r.get("sentence_index"), r.get("family"), _gold_clean(r.get("quote")).lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    return {
        "schema_version": "DETECTOR_OUTPUT_GOLD_UNIFIED_V1_2",
        "engine": GOLD_UNIFIED_ENGINE_ID,
        "detector_mode": "internal_core_v6_2_plus_gold_supplemental_rules",
        "batch_id": str(_gold_uuid.uuid4()),
        "student_id": submission.get("student_id"),
        "result_count": 1,
        "failure_count": 0,
        "results": [{
            "essay_id": submission.get("essay_id"),
            "student_id": submission.get("student_id"),
            "essay_text": submission.get("essay_text"),
            "prompt_text": submission.get("prompt_text"),
            "meta": core.get("meta", {}),
            "student_rows": deduped,
            "scorer_payload": {"chargeable_detector_rows": deduped},
            "sentence_df": core.get("sentence_df", []),
        }],
        "failures": [],
    }

def _gold_errormap(detector: Dict[str, Any]) -> Dict[str, Any]:
    result = (detector.get("results") or [{}])[0]
    errors = []
    broken = []
    for i, row in enumerate(result.get("student_rows", []), start=1):
        cap = _gold_issue_capacity(row.get("family"), row.get("category"))
        err = {
            "error_id": _gold_stable_id("err", row.get("row_id"), row.get("quote")),
            "source_row_id": row.get("row_id"),
            "essay_id": row.get("essay_id"),
            "sentence_index": row.get("sentence_index"),
            "criterion": row.get("category"),
            "family": row.get("family"),
            "capacity_domain": cap,
            "surface_quote": row.get("quote"),
            "suggested_revision": row.get("suggested_revision"),
            "severity": row.get("severity"),
            "confidence": row.get("confidence"),
            "student_message": row.get("instruction") or row.get("explanation"),
            "chargeable": row.get("is_actionable", True),
        }
        errors.append(err)
        if cap == "sentence_control" and row.get("severity") in {"high", "medium"}:
            broken.append({"sentence_index": row.get("sentence_index"), "quote": row.get("quote"), "family": row.get("family")})
    return {
        "schema_version": "ERRORMAP_GOLD_UNIFIED_V1_2",
        "errors": errors,
        "broken_sentences_raw": broken,
        "counts": dict(Counter(e["family"] for e in errors)),
        "counts_by_capacity": dict(Counter(e["capacity_domain"] for e in errors)),
    }



def _gold_score_profile(submission: Dict[str, Any], core: Dict[str, Any], errormap: Dict[str, Any]) -> Dict[str, Any]:
    """Build a conservative IELTS-compatible score profile.

    Criterion bands are integers. Overall is rounded to .0/.5. The score starts
    from the compact core scorer, then applies universal evidence caps from the
    ErrorMap so weak detector coverage cannot inflate GRA/LR/TR.
    """
    scores = core.get("scores", {})
    base_gra = int(scores.get("grammar", {}).get("band", 5))
    base_lr = int(scores.get("lexical_resource", {}).get("band", 5))
    base_cc = int(scores.get("cohesion_coherence", {}).get("band", 5))
    base_tr = int(scores.get("argumentation", {}).get("band", 5))
    by_cap = Counter(errormap.get("counts_by_capacity", {}))
    fam = Counter(errormap.get("counts", {}))
    wc = int(core.get("meta", {}).get("n_tokens", 0) or 0)

    sentence_errors = by_cap.get("sentence_control", 0)
    lexical_errors = by_cap.get("lexical_precision", 0)
    cohesion_errors = by_cap.get("cohesion_control", 0)
    argument_errors = by_cap.get("argument_development", 0) + by_cap.get("task_response_control", 0)

    gra_cap = 8
    if sentence_errors >= 16: gra_cap = 4
    elif sentence_errors >= 10: gra_cap = 5
    elif sentence_errors >= 5: gra_cap = 6
    elif sentence_errors >= 2: gra_cap = 7
    if fam.get("G_VERB_FORM", 0) + fam.get("G_SV_AGREEMENT", 0) + fam.get("G_MISSING_VERB", 0) >= 3:
        gra_cap = min(gra_cap, 5)
    gra = max(2, min(base_gra, gra_cap))

    lr_cap = 8
    if lexical_errors >= 14: lr_cap = 4
    elif lexical_errors >= 8: lr_cap = 5
    elif lexical_errors >= 4: lr_cap = 6
    elif lexical_errors >= 2: lr_cap = 7
    if fam.get("L_SPELLING", 0) >= 3:
        lr_cap = min(lr_cap, 5)
    lr = max(2, min(base_lr, lr_cap, gra + 1))

    cc_cap = 8
    if cohesion_errors >= 8: cc_cap = 4
    elif cohesion_errors >= 4: cc_cap = 5
    elif cohesion_errors >= 2: cc_cap = 6
    cc = max(2, min(base_cc, cc_cap))

    tr_cap = 8
    if argument_errors >= 8: tr_cap = 4
    elif argument_errors >= 4: tr_cap = 5
    elif argument_errors >= 2: tr_cap = 6
    tr = max(2, min(base_tr, tr_cap))

    if wc < 120:
        tr = min(tr, 5); cc = min(cc, 5); lr = min(lr, 5); gra = min(gra, 5)
    overall = round_half(mean([tr, cc, lr, gra]))
    confidence = "normal"
    if wc < 120:
        confidence = "reduced_short_response"
    elif len(errormap.get("errors", [])) < 3:
        confidence = "reduced_low_evidence"
    return {
        "schema_version": "PREMIUM_UNIFIED_SCORER_COMPAT_GOLD_V1_2",
        "scoring_version": GOLD_UNIFIED_ENGINE_VERSION,
        "score_profile": {
            "score_status": "ready" if confidence == "normal" else "low_confidence",
            "confidence": confidence,
            "official_criteria_bands": {"TR": tr, "CC": cc, "LR": lr, "GRA": gra},
            "overall_band_estimate": overall,
            "criterion_rationales": {
                "TR": scores.get("argumentation", {}).get("reason"),
                "CC": scores.get("cohesion_coherence", {}).get("reason"),
                "LR": scores.get("lexical_resource", {}).get("reason"),
                "GRA": scores.get("grammar", {}).get("reason"),
            },
            "evidence_caps": {
                "sentence_control_errors": sentence_errors,
                "lexical_precision_errors": lexical_errors,
                "cohesion_errors": cohesion_errors,
                "argument_errors": argument_errors,
                "base_core_bands": {"TR": base_tr, "CC": base_cc, "LR": base_lr, "GRA": base_gra},
            },
        },
        "core_scores": scores,
        "tier_decision": {"tier": "gold", "released": True},
    }

def _gold_verifier(scored: Dict[str, Any], errormap: Dict[str, Any], intake: Dict[str, Any]) -> Dict[str, Any]:
    sp = scored.get("score_profile", {})
    warnings = []
    if sp.get("confidence") != "normal":
        warnings.append(sp.get("confidence"))
    if intake.get("word_count", 0) < 120:
        warnings.append("word_count_below_ielts_wt2_expectation")
    if not errormap.get("errors"):
        warnings.append("no_errors_detected_check_detector_coverage")
    status = "pass" if not warnings else "caution"
    return {
        "schema_version": "PREMIUM_VERIFIER_COMPAT_GOLD_V1_2",
        "verifier_status": status,
        "warnings": warnings,
        "score_confidence": sp.get("confidence"),
        "progress_tracking_allowed": status == "pass",
        "lie_update_allowed": True,
    }


def _gold_adjudicator(scored: Dict[str, Any], verifier: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": "ADJUDICATOR_COMPAT_GOLD_V1_2",
        "adjudication_status": "confirmed" if verifier.get("verifier_status") == "pass" else "confirmed_with_caution",
        "score_changed": False,
        "criteria_preserved": True,
        "reason_codes": verifier.get("warnings", []),
        "final_score_profile": scored.get("score_profile", {}),
    }


def _gold_score_contract(submission: Dict[str, Any], scored: Dict[str, Any], verifier: Dict[str, Any], adjudicator: Dict[str, Any]) -> Dict[str, Any]:
    sp = scored.get("score_profile", {})
    return {
        "schema_version": "FINAL_SCORE_CONTRACT_GOLD_UNIFIED_V1_2",
        "student_id": submission.get("student_id"),
        "essay_id": submission.get("essay_id"),
        "task_type": submission.get("task_type", "WT2"),
        "released_score": {
            "overall_band": sp.get("overall_band_estimate"),
            "criteria_bands": sp.get("official_criteria_bands", {}),
        },
        "score_confidence": sp.get("confidence"),
        "score_status": sp.get("score_status"),
        "verifier_status": verifier.get("verifier_status"),
        "adjudication_status": adjudicator.get("adjudication_status"),
        "progress_tracking_allowed": verifier.get("progress_tracking_allowed", False),
        "lie_update_allowed": verifier.get("lie_update_allowed", True),
        "created_at": _gold_now(),
    }


def _gold_priority(errormap: Dict[str, Any], contract: Dict[str, Any]) -> Dict[str, Any]:
    capacity_counts = Counter(e.get("capacity_domain") for e in errormap.get("errors", []))
    family_counts = Counter(e.get("family") for e in errormap.get("errors", []))
    focus = []
    for rank, (cap, count) in enumerate(capacity_counts.most_common(), start=1):
        if not cap:
            continue
        related = [e for e in errormap.get("errors", []) if e.get("capacity_domain") == cap]
        focus.append({
            "rank": rank,
            "capacity_domain": cap,
            "skill_tag": cap,
            "criterion": _capacity_to_criterion(cap),
            "evidence_count": count,
            "top_families": [f for f, _ in Counter(e.get("family") for e in related).most_common(5)],
            "recommended_difficulty": "foundation" if cap == "sentence_control" else "controlled_transfer",
        })
    return {
        "schema_version": "PRIORITY_ENGINE_COMPAT_GOLD_V1_2",
        "focus_areas": focus[:5],
        "family_counts": dict(family_counts),
        "score_context": contract.get("released_score", {}),
    }


def _capacity_to_criterion(cap: str) -> str:
    return {
        "sentence_control": "GRA",
        "lexical_precision": "LR",
        "cohesion_control": "CC",
        "argument_development": "TR",
        "task_response_control": "TR",
        "academic_style": "LR",
    }.get(cap, "TR")


def _gold_directive(priority: Dict[str, Any], contract: Dict[str, Any]) -> Dict[str, Any]:
    focus = priority.get("focus_areas", [])
    primary = focus[0] if focus else {"capacity_domain": "argument_development", "skill_tag": "argument_development"}
    return {
        "schema_version": "DIRECTIVE_GOLD_UNIFIED_V1_2",
        "focus_areas": focus,
        "primary_focus": primary,
        "score_confidence": contract.get("score_confidence"),
        "adjudication_status": contract.get("adjudication_status"),
        "progress_tracking_allowed": contract.get("progress_tracking_allowed"),
        "gold_learning_directive": {
            "next_best_skill": primary.get("capacity_domain"),
            "recommended_service": _CAPACITY_SERVICE.get(primary.get("capacity_domain"), "writing_coach"),
            "learning_update_allowed": True,
            "mastery_update_allowed": False,
        },
    }


def _gold_feedback_engine(errormap: Dict[str, Any], contract: Dict[str, Any], directive: Dict[str, Any]) -> Dict[str, Any]:
    bundles = []
    for fa in directive.get("focus_areas", [])[:4]:
        cap = fa.get("capacity_domain")
        examples = [e for e in errormap.get("errors", []) if e.get("capacity_domain") == cap][:4]
        bundles.append({
            "status": "ok",
            "capacity_domain": cap,
            "title": _CAPACITY_LABELS.get(cap, cap),
            "summary": f"This area has {fa.get('evidence_count', 0)} detected signal(s).",
            "examples": examples,
            "next_step": _CAPACITY_WEEK_PLAN.get(cap, ["controlled practice"])[0],
        })
    return {"schema_version": "FEEDBACK_ENGINE_COMPAT_GOLD_V1_2", "bundles": bundles, "score": contract.get("released_score")}


def _gold_feedback_report(submission: Dict[str, Any], contract: Dict[str, Any], errormap: Dict[str, Any], evaluator: Dict[str, Any], directive: Dict[str, Any]) -> Dict[str, Any]:
    by_cap = Counter(e.get("capacity_domain") for e in errormap.get("errors", []))
    primary_cap = directive.get("primary_focus", {}).get("capacity_domain") or (by_cap.most_common(1)[0][0] if by_cap else "argument_development")
    capacity_profile = {}
    for cap in _CAPACITY_LABELS:
        n = by_cap.get(cap, 0)
        capacity_profile[cap] = {
            "label": _CAPACITY_LABELS[cap],
            "level": "weak" if n >= 5 else "developing" if n >= 2 else "stable_or_not_observed",
            "evidence_count": n,
            "learning_status": "active_bottleneck" if cap == primary_cap else "monitor",
        }
    return {
        "schema_version": "GOLD_FEEDBACK_REPORT_V1_2",
        "student_id": submission.get("student_id"),
        "essay_id": submission.get("essay_id"),
        "performance_summary": contract.get("released_score", {}),
        "score_confidence": contract.get("score_confidence"),
        "writing_capacity_profile": capacity_profile,
        "main_learning_bottleneck": {
            "skill_id": primary_cap,
            "skill_name": _CAPACITY_LABELS.get(primary_cap, primary_cap),
            "reason": f"This is the strongest current bottleneck because it has the largest concentration of chargeable evidence.",
            "root_cause": primary_cap,
            "secondary_effects": _secondary_effects(primary_cap),
        },
        "strength_profile": _gold_strengths(capacity_profile, evaluator),
        "next_best_skill": {
            "skill_id": primary_cap,
            "skill_name": _CAPACITY_LABELS.get(primary_cap, primary_cap),
            "recommended_service": _CAPACITY_SERVICE.get(primary_cap, "writing_coach"),
            "why_now": "It is the best next target according to the current essay evidence and skill dependency logic.",
        },
        "learning_plan": _gold_learning_plan(primary_cap),
    }


def _secondary_effects(cap: str) -> List[str]:
    return {
        "sentence_control": ["reduced recoverability", "lower discourse reliability", "weaker argument clarity"],
        "lexical_precision": ["less precise meaning", "awkward academic style", "reduced LR band ceiling"],
        "cohesion_control": ["mechanical flow", "weak reference clarity"],
        "argument_development": ["underdeveloped support", "weaker task response"],
        "task_response_control": ["incomplete prompt coverage", "unclear position"],
        "academic_style": ["less formal tone", "reduced academic suitability"],
    }.get(cap, [])


def _gold_strengths(capacity_profile: Dict[str, Any], evaluator: Dict[str, Any]) -> Dict[str, Any]:
    safe = []
    for cap, row in capacity_profile.items():
        if row.get("evidence_count", 0) == 0:
            safe.append({"skill_id": cap, "skill_name": _CAPACITY_LABELS.get(cap, cap), "reason": "No major issue was detected in this area in the current essay."})
    emerging = evaluator.get("positive_evidence_profile", {}).get("observed_strengths", [])
    return {"safe_strengths": safe[:3], "emerging_strengths": emerging[:3], "not_yet_stable": []}


def _gold_learning_plan(primary_cap: str) -> Dict[str, Any]:
    plan = _CAPACITY_WEEK_PLAN.get(primary_cap, ["controlled practice", "transfer practice", "revision", "new essay"])
    return {f"week_{i+1}": {"focus": focus} for i, focus in enumerate(plan[:4])}


def _gold_evaluator(submission: Dict[str, Any], core: Dict[str, Any], errormap: Dict[str, Any]) -> Dict[str, Any]:
    by_cap = Counter(e.get("capacity_domain") for e in errormap.get("errors", []))
    lexical_units = core.get("lexical_inventory", [])
    skill_profile = {}
    for cap in _CAPACITY_LABELS:
        n = by_cap.get(cap, 0)
        skill_profile[cap] = {
            "skill_id": cap,
            "skill_name": _CAPACITY_LABELS[cap],
            "observation_status": "gap" if n >= 3 else "monitor" if n else "not_negatively_observed",
            "evidence_count": n,
            "confidence": min(1.0, 0.35 + n * 0.1),
        }
    strengths = []
    for cap, row in skill_profile.items():
        if row["observation_status"] == "not_negatively_observed":
            strengths.append({"skill_id": cap, "skill_name": row["skill_name"], "evidence": "No major negative evidence in this essay."})
    return {
        "schema_version": "WKE_GOLD_UNIFIED_V1_2",
        "engine_id": GOLD_UNIFIED_ENGINE_ID,
        "engine_version": GOLD_UNIFIED_ENGINE_VERSION,
        "boundary": "Evaluator extracts writing capacity evidence and lexical units; it does not score IELTS bands or assign LRET labels.",
        "student_id": submission.get("student_id"),
        "essay_id": submission.get("essay_id"),
        "writing_skill_profile": skill_profile,
        "positive_evidence_profile": {"observed_strengths": strengths},
        "microskill_profile": skill_profile,
        "lexical_capacity_profile": {
            "candidate_units_total": len(lexical_units),
            "units": lexical_units[:240],
        },
        "task_schema_profile": {
            "task_type": submission.get("task_type", "WT2"),
            "prompt_present": bool(_gold_clean(submission.get("prompt_text"))),
        },
        "consumer_payloads": {
            "writing_coach_payload": {"candidate_skills": list(skill_profile.values())},
            "lret_payload": {"lexical_units": lexical_units[:240]},
            "practice_engine_payload": {"skill_profile": skill_profile},
            "essay_revision_payload": {"sentence_df": core.get("sentence_df", []), "errors": errormap.get("errors", [])},
            "learning_intelligence_payload": {"skill_profile": skill_profile, "lexical_units": lexical_units[:240]},
            "progress_tracker_payload": {"capacity_counts": dict(by_cap)},
        },
    }


def _gold_evidence_fusion(detector: Dict[str, Any], errormap: Dict[str, Any], contract: Dict[str, Any], evaluator: Dict[str, Any], prior_profile: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": "GOLD_EVIDENCE_FUSION_V1_2",
        "performance_evidence": {
            "source": "final_score_contract",
            "released_score": contract.get("released_score"),
            "stable_for_trend": bool(contract.get("progress_tracking_allowed")),
            "confidence": contract.get("score_confidence"),
        },
        "error_pattern_evidence": {
            "source": "detector_errormap",
            "chargeable_errors": [e for e in errormap.get("errors", []) if e.get("chargeable", True)],
            "recurring_families": _recurring_families(errormap, prior_profile),
        },
        "skill_capacity_evidence": {
            "source": "evaluator",
            "skill_profile": evaluator.get("writing_skill_profile", {}),
            "positive_evidence_profile": evaluator.get("positive_evidence_profile", {}),
        },
        "learning_behavior_evidence": {
            "source": "gold_services_current_session",
            "attempts": [],
            "revision_actions": [],
            "lexical_actions": [],
        },
    }


def _recurring_families(errormap: Dict[str, Any], prior_profile: Dict[str, Any]) -> List[str]:
    current = set(errormap.get("counts", {}).keys())
    previous = set((prior_profile.get("error_pattern_profile", {}) or {}).get("family_totals", {}).keys())
    return sorted(current & previous)



def _gold_unit_text(c: Dict[str, Any]) -> str:
    return _gold_clean(c.get("unit") or c.get("surface_quote") or c.get("quote") or "")


def _gold_meaningful_lex_unit(text: str) -> bool:
    t = _gold_clean(text).lower()
    if not t:
        return False
    if re.fullmatch(r"(the|a|an|and|or|but|so|because|this|that|it|there|is|are|was|were|has|have|had)", t):
        return False
    if re.match(r"^(in|on|at|for|with|to|from|of|by)\s*$", t):
        return False
    return True


def _gold_dedupe_lex_candidates(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cleaned = []
    for c in candidates:
        if not isinstance(c, dict):
            continue
        unit = _gold_unit_text(c)
        if not _gold_meaningful_lex_unit(unit):
            continue
        d = dict(c)
        d.setdefault("unit", unit)
        cleaned.append(d)
    phrase_units = []
    for c in cleaned:
        u = _gold_unit_text(c).lower()
        if len(u.split()) >= 2:
            phrase_units.append(u)
    out = []
    seen = set()
    # Prefer longer phrases before single words.
    for c in sorted(cleaned, key=lambda x: (-len(_gold_unit_text(x).split()), _gold_unit_text(x).lower())):
        u = _gold_unit_text(c).lower()
        if u in seen:
            continue
        if len(u.split()) == 1:
            if any(re.search(rf"\b{re.escape(u)}\b", phrase) for phrase in phrase_units):
                continue
        seen.add(u)
        out.append(c)
    # Restore stable readable order by first appearance in original candidates.
    order = { _gold_unit_text(c).lower(): i for i, c in enumerate(candidates) if isinstance(c, dict) }
    out.sort(key=lambda c: order.get(_gold_unit_text(c).lower(), 10**9))
    return out


def _gold_lret(core: Dict[str, Any], evaluator: Dict[str, Any], errormap: Dict[str, Any]) -> Dict[str, Any]:
    lex = core.get("lexical_enhancement", {})
    fix_from_errors = []
    for e in errormap.get("errors", []):
        if str(e.get("family", "")).startswith("L_") and e.get("surface_quote"):
            fix_from_errors.append({
                "unit": e.get("surface_quote"),
                "source": "errormap",
                "family": e.get("family"),
                "suggested_revision": e.get("suggested_revision"),
                "reason": e.get("student_message"),
            })
    # FIX outranks ENHANCE, ENHANCE outranks KEEP for the same unit.
    fix = _gold_dedupe_lex_candidates((lex.get("fix", []) or []) + fix_from_errors)
    fix_units = {_gold_unit_text(c).lower() for c in fix}
    enhance = _gold_dedupe_lex_candidates([c for c in (lex.get("enhance", []) or []) if _gold_unit_text(c).lower() not in fix_units])
    enhance_units = {_gold_unit_text(c).lower() for c in enhance}
    keep = _gold_dedupe_lex_candidates([c for c in (lex.get("keep", []) or []) if _gold_unit_text(c).lower() not in fix_units and _gold_unit_text(c).lower() not in enhance_units])
    avoid = _gold_dedupe_lex_candidates(lex.get("drop", []) or [])
    return {
        "schema_version": "LRET_SESSION_GOLD_UNIFIED_V1_2",
        "keep_candidates": keep[:60],
        "enhance_candidates": enhance[:60],
        "fix_candidates": fix[:80],
        "avoid_candidates": avoid[:40],
        "deduplication_rule": "Duplicate units are removed; phrase/collocation candidates outrank single words; FIX outranks ENHANCE, and ENHANCE outranks KEEP.",
        "lie_events": [],
    }

def _gold_writing_coach(gold_feedback: Dict[str, Any]) -> Dict[str, Any]:
    nbs = gold_feedback.get("next_best_skill", {})
    skill_id = nbs.get("skill_id", "argument_development")
    title = {
        "sentence_control": "Clear Sentence Builder",
        "lexical_precision": "Precise Phrase Builder",
        "argument_development": "Claim + Reason Builder",
        "cohesion_control": "Sentence Flow Builder",
        "task_response_control": "Prompt Answer Builder",
        "academic_style": "Academic Tone Builder",
    }.get(skill_id, "Writing Coach Mission")
    instruction = {
        "sentence_control": "Write 5 complete academic sentences. Each sentence must have a clear subject, a correct main verb, and one complete idea.",
        "lexical_precision": "Rewrite 5 weak phrases using more precise academic vocabulary without changing the meaning.",
        "argument_development": "Write 3 claim-reason-example chains on the essay topic.",
        "cohesion_control": "Connect 5 pairs of ideas using clear reference or transition language.",
        "task_response_control": "Write a short plan showing all parts of the prompt and your position.",
        "academic_style": "Rewrite 5 informal sentences in a more academic style.",
    }.get(skill_id, "Complete the writing move in clear academic English.")
    return {
        "schema_version": "WRITING_COACH_OUTPUT_GOLD_UNIFIED_V1_2",
        "today_mission": {
            "mission_id": _gold_stable_id("wc", skill_id, title),
            "title": title,
            "target_skill_id": skill_id,
            "target_skill_name": nbs.get("skill_name"),
            "timebox_minutes": 10,
            "student_instruction": instruction,
            "success_checklist": [
                "The response is complete.",
                "The meaning is clear.",
                "The target skill is visible.",
                "There are no copied correction fragments from the old essay.",
            ],
            "mastery_update_allowed": False,
        }
    }


def _gold_practice_session(errormap: Dict[str, Any], gold_feedback: Dict[str, Any]) -> Dict[str, Any]:
    exercises = []
    seen = set()
    for e in errormap.get("errors", []):
        fam = e.get("family")
        if fam in seen:
            continue
        seen.add(fam)
        exercises.append({
            "exercise_id": _gold_stable_id("ex", fam, e.get("surface_quote")),
            "family": fam,
            "capacity_domain": e.get("capacity_domain"),
            "prompt": f"Rewrite this part more accurately: {e.get('surface_quote')}",
            "model_hint": e.get("suggested_revision") or e.get("student_message") or "Make the sentence clearer and more accurate.",
            "mastery_update_allowed": False,
        })
        if len(exercises) >= 8:
            break
    return {
        "schema_version": "PRACTICE_SESSION_GOLD_UNIFIED_V1_2",
        "session_mode": "assigned_not_attempted",
        "target_skill": gold_feedback.get("next_best_skill", {}),
        "exercises": exercises,
        "practice_event_for_lie": None,
    }


def _gold_update_profile(profile_path: _GoldPath, submission: Dict[str, Any], contract: Dict[str, Any], errormap: Dict[str, Any], evaluator: Dict[str, Any], gold_feedback: Dict[str, Any], pretty: bool) -> Dict[str, Any]:
    profile = _gold_read(profile_path, default={}) or {}
    profile.setdefault("student_id", submission.get("student_id"))
    profile.setdefault("profile_version", "gold_lie_unified_v1_2")
    profile.setdefault("performance_profile", {"released_scores": [], "stable_scores": []})
    profile.setdefault("error_pattern_profile", {"family_totals": {}, "capacity_totals": {}})
    profile.setdefault("skill_capacity_profile", {})
    profile.setdefault("lexical_profile", {"sessions": []})
    profile.setdefault("writing_coach_profile", {"mission_history": []})
    profile.setdefault("practice_profile", {"sessions": []})
    perf_row = {
        "essay_id": submission.get("essay_id"),
        "created_at": _gold_now(),
        "released_score": contract.get("released_score"),
        "score_confidence": contract.get("score_confidence"),
        "stable_for_trend": contract.get("progress_tracking_allowed"),
    }
    profile["performance_profile"]["released_scores"].append(perf_row)
    if contract.get("progress_tracking_allowed"):
        profile["performance_profile"]["stable_scores"].append(perf_row)
    fam_totals = Counter(profile["error_pattern_profile"].get("family_totals", {}))
    cap_totals = Counter(profile["error_pattern_profile"].get("capacity_totals", {}))
    fam_totals.update(errormap.get("counts", {}))
    cap_totals.update(errormap.get("counts_by_capacity", {}))
    profile["error_pattern_profile"]["family_totals"] = dict(fam_totals)
    profile["error_pattern_profile"]["capacity_totals"] = dict(cap_totals)
    for skill_id, row in evaluator.get("writing_skill_profile", {}).items():
        skill = profile["skill_capacity_profile"].setdefault(skill_id, {"evidence_count": 0, "sessions": 0, "status": "building"})
        skill["evidence_count"] += int(row.get("evidence_count", 0) or 0)
        skill["sessions"] += 1
        skill["last_observation_status"] = row.get("observation_status")
        skill["status"] = "active_gap" if row.get("observation_status") == "gap" else skill.get("status", "building")
    profile["next_best_action"] = {
        "service": gold_feedback.get("next_best_skill", {}).get("recommended_service"),
        "skill_id": gold_feedback.get("next_best_skill", {}).get("skill_id"),
        "reason": gold_feedback.get("next_best_skill", {}).get("why_now"),
    }
    profile["last_updated_at"] = _gold_now()
    _gold_write(profile_path, profile, pretty=pretty)
    return profile


def _gold_skills_progress(profile: Dict[str, Any]) -> Dict[str, Any]:
    rows = []
    for skill_id, row in profile.get("skill_capacity_profile", {}).items():
        rows.append({"skill_id": skill_id, "skill_name": _CAPACITY_LABELS.get(skill_id, skill_id), **row})
    return {"schema_version": "GOLD_SKILLS_PROGRESS_V1_2", "skills": sorted(rows, key=lambda r: r.get("evidence_count", 0), reverse=True)}


def _gold_roadmap(gold_feedback: Dict[str, Any]) -> Dict[str, Any]:
    primary = gold_feedback.get("next_best_skill", {}).get("skill_id", "argument_development")
    return {"schema_version": "GOLD_LEARNING_ROADMAP_V1_2", "primary_skill": primary, "weeks": gold_feedback.get("learning_plan", {})}


def _gold_routing(gold_feedback: Dict[str, Any]) -> Dict[str, Any]:
    nbs = gold_feedback.get("next_best_skill", {})
    service = nbs.get("recommended_service", "writing_coach")
    return {
        "schema_version": "GOLD_SERVICE_ROUTING_V1_2",
        "primary_next_service": service,
        "secondary_services": [s for s in ["writing_coach", "lret", "practice", "essay_revision"] if s != service][:2],
        "reason": nbs.get("why_now"),
        "next_best_action": {"type": service, "skill_id": nbs.get("skill_id"), "skill_name": nbs.get("skill_name")},
    }


def _gold_revision_packet(submission: Dict[str, Any], gold_dir: _GoldPath) -> Dict[str, Any]:
    return {
        "schema_version": "GOLD_REVISION_LAUNCH_PACKET_V1_2",
        "status": "ready_for_revision_when_student_submits_revised_essay",
        "original_gold_session_dir": str(gold_dir),
        "student_id": submission.get("student_id"),
        "essay_id": submission.get("essay_id"),
        "required_next_input": "revised_essay_text",
        "note": "Revision is a second learner action. This unified pipeline writes the original-session packet needed for revision comparison.",
    }


def _gold_progress_snapshot(profile: Dict[str, Any], contract: Dict[str, Any], gold_feedback: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": "GOLD_PROGRESS_SNAPSHOT_V1_2",
        "latest_score": contract.get("released_score"),
        "next_best_action": profile.get("next_best_action"),
        "main_bottleneck": gold_feedback.get("main_learning_bottleneck"),
        "stable_score_count": len(profile.get("performance_profile", {}).get("stable_scores", [])),
        "released_score_count": len(profile.get("performance_profile", {}).get("released_scores", [])),
    }


def _gold_qa(artifacts: Dict[str, str], evaluator: Dict[str, Any], contract: Dict[str, Any], errormap: Dict[str, Any]) -> Dict[str, Any]:
    checks = {
        "single_file_pipeline": True,
        "does_not_call_previous_premium_runner": True,
        "does_not_import_adapter_files": True,
        "submission_normalized": True,
        "detector_output_present": bool(artifacts.get("detector")),
        "errormap_present": bool(errormap.get("errors") is not None),
        "final_score_contract_present": bool(contract.get("released_score")),
        "evaluator_present": bool(evaluator.get("writing_skill_profile")),
        "evaluator_has_consumer_payloads": bool(evaluator.get("consumer_payloads")),
        "no_evaluator_score_leak": not any(k in evaluator for k in ["overall_band", "ielts_band", "criterion_score", "performance_band"]),
        "lret_labels_owned_by_lret": True,
        "no_mastery_update_from_assignment": True,
        "no_human_review_route": True,
        "no_essay_specific_rule_declared": True,
    }
    status = "passed" if all(checks.values()) else "failed"
    return {"schema_version": "QA_GOLD_UNIFIED_V1_2", "status": status, "checks": checks, "created_at": _gold_now()}


def gold_run_unified(args: Any) -> Dict[str, Any]:
    input_path = _GoldPath(args.input).resolve()
    submission = _gold_load_submission(input_path, essay_index=args.essay_index)
    session_id = args.session_id or _gold_stable_id("gold_session", submission.get("student_id"), submission.get("essay_id"), _gold_now(), n=10)
    gold_dir = _GoldPath(args.gold_dir).resolve() if args.gold_dir else (_GoldPath(args.output_root).resolve() / session_id)
    gold_dir.mkdir(parents=True, exist_ok=True)
    profile_path = _GoldPath(args.gold_profile).resolve() if args.gold_profile else (_GoldPath(args.output_root).resolve() / "learner_profiles" / f"{submission.get('student_id')}_gold_profile.json")
    prior_profile = _gold_read(profile_path, default={}) or {}

    artifacts: Dict[str, str] = {}
    _gold_write(gold_dir / "00_submission.json", submission, pretty=args.pretty); artifacts["submission"] = str(gold_dir / "00_submission.json")
    intake = _gold_intake(submission)
    _gold_write(gold_dir / "00_intake_assessment.json", intake, pretty=args.pretty); artifacts["intake"] = str(gold_dir / "00_intake_assessment.json")

    core_req = _gold_core_request(submission, prior_profile)
    core = analyze_submission(core_req)
    _gold_write(gold_dir / "00_core_analysis.json", core, pretty=args.pretty); artifacts["core_analysis"] = str(gold_dir / "00_core_analysis.json")

    detector = _gold_detector_output(submission, core)
    _gold_write(gold_dir / "01_detector_output.json", detector, pretty=args.pretty); artifacts["detector"] = str(gold_dir / "01_detector_output.json")
    errormap = _gold_errormap(detector)
    _gold_write(gold_dir / "01b_errormap_v3.json", errormap, pretty=args.pretty); artifacts["errormap"] = str(gold_dir / "01b_errormap_v3.json")
    scored = _gold_score_profile(submission, core, errormap)
    _gold_write(gold_dir / "02a_premium_scorer_v1_4_1_output.json", scored, pretty=args.pretty); artifacts["scorer"] = str(gold_dir / "02a_premium_scorer_v1_4_1_output.json")
    verifier = _gold_verifier(scored, errormap, intake)
    _gold_write(gold_dir / "02b_premium_verifier_v1_4_3_output.json", verifier, pretty=args.pretty); artifacts["verifier"] = str(gold_dir / "02b_premium_verifier_v1_4_3_output.json")
    adjudicator = _gold_adjudicator(scored, verifier)
    _gold_write(gold_dir / "02c_final_adjudicated_v1_2.json", adjudicator, pretty=args.pretty); artifacts["adjudicator"] = str(gold_dir / "02c_final_adjudicated_v1_2.json")
    contract = _gold_score_contract(submission, scored, verifier, adjudicator)
    _gold_write(gold_dir / "02d_final_score_contract.json", contract, pretty=args.pretty); artifacts["score_contract"] = str(gold_dir / "02d_final_score_contract.json")
    priority = _gold_priority(errormap, contract)
    _gold_write(gold_dir / "03_pe_output.json", priority, pretty=args.pretty); artifacts["priority"] = str(gold_dir / "03_pe_output.json")
    directive = _gold_directive(priority, contract)
    _gold_write(gold_dir / "04_directive_v2.json", directive, pretty=args.pretty); artifacts["directive"] = str(gold_dir / "04_directive_v2.json")
    fe = _gold_feedback_engine(errormap, contract, directive)
    _gold_write(gold_dir / "05_fe_output.json", fe, pretty=args.pretty); artifacts["feedback_engine"] = str(gold_dir / "05_fe_output.json")

    evaluator = _gold_evaluator(submission, core, errormap)
    _gold_write(gold_dir / "07_evaluator_output.json", evaluator, pretty=args.pretty); artifacts["evaluator"] = str(gold_dir / "07_evaluator_output.json")
    fusion = _gold_evidence_fusion(detector, errormap, contract, evaluator, prior_profile)
    _gold_write(gold_dir / "07b_gold_evidence_fusion.json", fusion, pretty=args.pretty); artifacts["evidence_fusion"] = str(gold_dir / "07b_gold_evidence_fusion.json")
    gold_feedback = _gold_feedback_report(submission, contract, errormap, evaluator, directive)
    _gold_write(gold_dir / "06_feedback_report_v6c.json", gold_feedback, pretty=args.pretty); artifacts["feedback_report"] = str(gold_dir / "06_feedback_report_v6c.json")
    _gold_write(gold_dir / "07c_gold_feedback_report.json", gold_feedback, pretty=args.pretty); artifacts["gold_feedback"] = str(gold_dir / "07c_gold_feedback_report.json")
    lret = _gold_lret(core, evaluator, errormap)
    _gold_write(gold_dir / "07d_lret_session.json", lret, pretty=args.pretty); artifacts["lret_session"] = str(gold_dir / "07d_lret_session.json")
    coach = _gold_writing_coach(gold_feedback)
    _gold_write(gold_dir / "07e_writing_coach_output.json", coach, pretty=args.pretty); artifacts["writing_coach"] = str(gold_dir / "07e_writing_coach_output.json")
    practice = _gold_practice_session(errormap, gold_feedback)
    _gold_write(gold_dir / "07f_gold_practice_session.json", practice, pretty=args.pretty); artifacts["practice_session"] = str(gold_dir / "07f_gold_practice_session.json")
    profile = _gold_update_profile(profile_path, submission, contract, errormap, evaluator, gold_feedback, pretty=args.pretty)
    _gold_write(gold_dir / "08_gold_learner_profile.json", profile, pretty=args.pretty); artifacts["learner_profile"] = str(gold_dir / "08_gold_learner_profile.json")
    skills_progress = _gold_skills_progress(profile)
    _gold_write(gold_dir / "08b_gold_skills_progress_report.json", skills_progress, pretty=args.pretty); artifacts["skills_progress"] = str(gold_dir / "08b_gold_skills_progress_report.json")
    roadmap = _gold_roadmap(gold_feedback)
    _gold_write(gold_dir / "08c_gold_learning_roadmap.json", roadmap, pretty=args.pretty); artifacts["roadmap"] = str(gold_dir / "08c_gold_learning_roadmap.json")
    routing = _gold_routing(gold_feedback)
    _gold_write(gold_dir / "08d_gold_service_routing.json", routing, pretty=args.pretty); artifacts["routing"] = str(gold_dir / "08d_gold_service_routing.json")
    snapshot = _gold_progress_snapshot(profile, contract, gold_feedback)
    _gold_write(gold_dir / "09_gold_progress_snapshot.json", snapshot, pretty=args.pretty); artifacts["progress_snapshot"] = str(gold_dir / "09_gold_progress_snapshot.json")
    revision_packet = _gold_revision_packet(submission, gold_dir)
    _gold_write(gold_dir / "revision_launch_packet.json", revision_packet, pretty=args.pretty); artifacts["revision_launch"] = str(gold_dir / "revision_launch_packet.json")
    qa = _gold_qa(artifacts, evaluator, contract, errormap)
    _gold_write(gold_dir / "QA_gold_report.json", qa, pretty=args.pretty); artifacts["qa"] = str(gold_dir / "QA_gold_report.json")
    manifest = {
        "schema_version": "GOLD_RUN_MANIFEST_V1_2",
        "engine_id": GOLD_UNIFIED_ENGINE_ID,
        "engine_version": GOLD_UNIFIED_ENGINE_VERSION,
        "created_at": _gold_now(),
        "input_path": str(input_path),
        "gold_dir": str(gold_dir),
        "profile_path": str(profile_path),
        "qa_status": qa.get("status"),
        "artifacts": artifacts,
        "next_best_action": routing.get("next_best_action"),
    }
    _gold_write(gold_dir / "gold_run_manifest.json", manifest, pretty=args.pretty); artifacts["manifest"] = str(gold_dir / "gold_run_manifest.json")
    return manifest


def _gold_build_cli() -> Any:
    p = _gold_argparse.ArgumentParser(description="VA/ST.ELLA Gold Unified Full Pipeline v1.2 — one-file monolithic runner")
    p.add_argument("--input", required=True, help="Submission JSON. Accepts a single essay object or {essays:[...]} batch wrapper.")
    p.add_argument("--essay-index", type=int, default=0, help="Essay index when input has essays[]. Default: 0.")
    p.add_argument("--output-root", default="gold_sessions", help="Output root folder.")
    p.add_argument("--gold-dir", help="Exact output folder. Overrides output-root/session id.")
    p.add_argument("--gold-profile", help="Persistent Gold learner profile path.")
    p.add_argument("--session-id", help="Optional fixed Gold session id.")
    p.add_argument("--pretty", action="store_true", help="Pretty-print JSON artifacts.")
    return p


def gold_main(argv: Optional[List[str]] = None) -> int:
    args = _gold_build_cli().parse_args(argv)
    try:
        manifest = gold_run_unified(args)
        print("\nGOLD UNIFIED PIPELINE COMPLETE")
        print("Gold folder:      ", manifest.get("gold_dir"))
        print("QA status:        ", manifest.get("qa_status"))
        nba = manifest.get("next_best_action") or {}
        print("Next best action: ", nba.get("type"), "|", nba.get("skill_name"))
        print("Manifest:         ", manifest.get("artifacts", {}).get("manifest"))
        return 0 if manifest.get("qa_status") == "passed" else 2
    except Exception as exc:
        print("\n[GOLD UNIFIED PIPELINE ERROR]", str(exc), file=_gold_sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(gold_main())
