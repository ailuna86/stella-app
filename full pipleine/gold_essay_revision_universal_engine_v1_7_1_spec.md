# Gold Essay Revision Engine V1.7.1 Specification

## Status

**Release type:** stabilization patch after V1.7  
**Primary goal:** freeze the ER architecture with student-safe, role-specific AI comparison and versioned-output consistency.  
**Evaluator status:** accepted as-is. V1.7.1 does not require new Evaluator sentence-control or paragraph-function payloads.

## Product decision

V1.7 is the first freezeable ER architecture because it fixes the major AI comparison blocker: the model rewrite is generated from the **original essay**, while the student revision is used only for comparison. V1.7.1 does not redesign this logic. It stabilizes it for release-candidate use.

## V1.7.1 scope

### In scope

1. AI comparison stabilization.
2. Role-specific student-facing explanations.
3. Deterministic schema fallback without requiring an LLM.
4. Versioned V1.7.1 output files.
5. Student report synchronization with AI model availability.
6. Lightweight regression checker for ER artifacts.
7. Standalone executable Python files.

### Out of scope

1. New Detector logic.
2. New Scorer logic.
3. New Evaluator sentence-control or paragraph-function payloads.
4. Human review.
5. Essay-specific rules or hardcoded essay IDs.

## Correct ER flow

```text
Original essay
    ↓
Detector + Scorer + Evaluator
    ↓
ER workspace: annotated original essay and self-revision guidance
    ↓
Student writes revised essay
    ↓
Revision comparator compares original vs revised
    ↓
AI comparison generates model rewrite from ORIGINAL essay
    ↓
Student sees three-way comparison:
Original paragraph → Student revision → AI model rewrite
```

## AI comparison policy

The AI model rewrite must be generated from:

```text
original essay + task type + prompt + revision workspace/task schema
```

The revised essay must be used only for:

```text
comparison display and learning reflection
```

It must not be used as the source text for model generation.

## Validation and fallback

V1.7.1 keeps the V1.7 generation strategy:

```text
generate → validate → repair/regenerate → validate → schema fallback if needed
```

The final student-visible model is released only if:

1. word count is at least 250 words;
2. word count does not exceed 320 words;
3. four paragraph roles are present when the task requires four paragraphs;
4. introduction has task frame, position, and preview;
5. body paragraphs contain role-pure development and a specific example;
6. conclusion summarizes and gives final position without new examples;
7. QA status is `pass`.

If LLM generation fails, V1.7.1 uses deterministic schema fallback. This fallback is not a scaffold: it creates a complete student-visible model essay that still passes the same structure and word-count gate.

## Role-specific student explanations

V1.7 produced some generic explanations such as “It develops one main idea with support” for introductions and conclusions. V1.7.1 replaces these with role-specific comments.

### Introduction comments

- It frames the task before giving details.
- It states a clear final position.
- It previews both sides without using an example.

### Disadvantage/problem body comments

- It keeps the paragraph focused on one negative side.
- It explains the problem before the example.
- It links the example back to the disadvantage.

### Advantage body comments

- It keeps the paragraph focused on one positive side.
- It explains the benefit before the example.
- It links the example back to the final position.

### Conclusion comments

- It summarizes the main body arguments.
- It gives a clear final answer to the task.
- It does not add a new example or new policy idea.

## Versioned-output policy

V1.7.1 writes and expects these active files:

```text
revision_ai_comparison_v1_7_1.json
revision_ai_comparison_v1_7_1.md
revision_ai_comparison_v1_7_1.html
revision_student_report.json
revision_student_report.md
revision_run_manifest.json
```

Legacy AI comparison files are ignored by the manifest. The manifest must include:

```json
{
  "active_ai_comparison_version": "v1_7_1",
  "ai_comparison_artifact_created": true,
  "ai_model_available_to_student": true,
  "ai_model_generation_status": "generated_with_schema_fallback_passed_structure_gate"
}
```

## Student report synchronization

The student report must not infer AI availability from the existence of an AI file alone. It must check:

```text
model_available_to_student == true
```

and a valid generation status:

```text
generated_with_llm_passed_structure_gate
generated_with_repaired_llm_passed_structure_gate
generated_with_schema_fallback_passed_structure_gate
```

## Regression checker

V1.7.1 includes a lightweight release-candidate checker. It verifies:

1. AI comparison schema is V1.7.1.
2. Student report schema is V1.4.1.
3. AI model availability is synchronized.
4. model essay passes QA.
5. no generic V1.7 comments remain for introduction/conclusion.
6. manifest points to V1.7.1 active AI comparison.

## Freeze rule

ER V1.7.1 may be frozen as the current ER architecture if:

1. regression checker passes;
2. AI model is available to student;
3. student report and AI comparison agree;
4. model essay is generated from original essay;
5. no invalid model text is shown;
6. no technical debug terms appear in student-facing Markdown/HTML.

## Deliverables

- `gold_revision_ai_comparison_generator_v1_7_1.py`
- `gold_revision_student_report_renderer_v1_4_1.py`
- `essay_revision_full_pipeline_runner_v4_7_1.py`
- `gold_revision_universal_engine_v1_7_1.py`
- `gold_wt2_prewrite_planner_v1_7_1.py`
- `gold_er_v1_7_1_regression_check.py`
