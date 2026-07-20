# Final registry v3 consolidation audit

## Result
Created one consolidated canonical runtime package. Separate duplicate source lists are no longer runtime inputs.

## Main consolidations
- `lexical_registry.json`: merged lexical registry, words master, VA lexeme index, BNC/COCA frequency words, AWL, NAWL, legacy lexicon. Rows: 76,334.
- `discourse_registry.json`: merged final discourse registry and `discourse_markers.txt`. Rows: 102.
- `noun_governance_registry.json`: merged noun governance, mass abstract nouns, collective singular nouns. Rows: 4,364.
- `positive_collocations_registry.tsv`: merged all positive collocation/phrase sources, including ACL. Rows: 17,629.
- `preposition_governance_registry.tsv`: merged final governed prepositions, VerbNet preposition frames, and phrasal-governance patterns. Rows: 6,335.
- `verb_complement_registry.tsv`: merged final verb complements and VerbNet complement frames. Rows: 11,941.

## Duplicate/source cleanup
- Copy duplicate files detected in source folder: 27. They are not included as runtime files.
- Raw/research files detected: 30. They are not included unless already parsed into a consolidated registry.

## Conflict policy
- Duplicates are merged within each canonical registry.
- If CEFR conflicts, lower/easier CEFR is retained.
- POS conflicts are preserved as a union unless one side is `unknown`.
- Sources are unioned.
- Confidence uses the maximum available confidence.
- Positive collocation/phrase absence is never an error.
- Governance absence is never an error.

## Important runtime instruction
Detector and LRET should now load only `FINAL_REGISTRY_MANIFEST.json` from this folder. Do not separately load `discourse_markers.txt`, `mass_abstract_nouns.txt`, `collective_singular.txt`, `academic_collocations.tsv`, or old `collocations.tsv`; they have already been merged.
