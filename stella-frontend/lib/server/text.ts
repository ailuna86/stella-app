// Shared text-normalization helpers used before essay text is written to
// disk or handed to the Python pipeline.
//
// Every paragraph-detection implementation across the pipeline
// (detector_cli_v1_4_4.py, va_premium_evaluator_v8_3_wke_standalone.py,
// det_vip_v18d_2.py, essay_revision_full_pipeline_runner_v4_7_1.py,
// gold_revision_universal_engine_v1_7_1.py) independently requires a blank
// line (regex \n\s*\n+) between paragraphs. But every essay/revision
// textarea in this app is a plain <textarea> — pressing Enter inserts
// exactly one "\n", and nothing in the UI asks students to press it twice
// between paragraphs. Confirmed via a real user session
// (gold_20260719_212849_..._ed924a2c/10_revision_workspace.json) that a
// genuinely 5-paragraph essay came back with original_paragraph_count: 1
// and every sentence merged into one paragraph labeled "introduction" — the
// exact bug reported ("all paragraphs fell into introduction"). Normalizing
// every run of newlines to a blank line, once, before text is stored or
// sent to any pipeline stage, honors every engine's existing
// paragraph-boundary contract as written, without touching any of the
// frozen Python paragraph-splitting code.
export function normalizeParagraphBreaks(text: string): string {
  return text.replace(/\n+/g, "\n\n");
}
