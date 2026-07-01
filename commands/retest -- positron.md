---
description: Full model-support battery on any branch — rebuild, unit tests, FPGA
  correctness vs the HuggingFace transformers source of truth, code review,
  comment-check, and a perf pass. Derives the target model set from the branch diff.
argument-hint: '[model|slug|tag…] [--all] [--no-perf] [--no-review] [--no-comments] [--no-semantic]'
---
Run the full model-support retest for $ARGUMENTS by following the `retest` skill, which
defines the complete phase-by-phase procedure -- derive the target model set from the branch
diff, then work Phases 0 through 6 (provision, rebuild, unit tests, the FPGA-vs-HuggingFace
headline gate, review, comment-check, and perf). Follow it exactly.
