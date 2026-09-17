# Goal Plus Ultra Codex smoke fixture

This fixture preserves the source and a sanitized evidence projection from a
real Codex `/goal-plus` run. Raw `.gp` state and the native transcript remain
local-only according to the repository commit-hygiene contract.

## Task

Implement `parse_duration` with these rules:

- accepted units are `ms`, `s`, `m`, and `h`, case-insensitively;
- one or more positive integer/unit tokens may be adjacent or whitespace-separated;
- repeated units are allowed and their values are summed;
- leading and trailing whitespace is allowed;
- empty input, zero/negative/decimal values, unknown units, and any unmatched
  text raise `ValueError`;
- the result is the exact total number of milliseconds as an `int`.

The original run started from a `NotImplementedError` baseline. A real Codex
ordinary subagent added focused edge-case tests while Main owned production
implementation and final integration.

The original test artifacts were named `test_duration.py` and
`test_duration_edge_cases.py`. They are stored here as
`duration_test_source.py` and `duration_edge_case_test_source.py` so pytest
does not collect this historical source as part of the product test suite.

## Evidence

`evidence.json` contains only reviewable orchestration facts:

- model and effective reasoning effort;
- the real Codex worker handle and transcript digest;
- normalized work-item and event ordering;
- baseline, focused, and final pytest counts;
- proof that final pytest evidence was accepted before Goal Plus completed.

The digest identifies the retained local transcript without publishing its
private reasoning, prompts, tool payloads, host paths, or raw log content.
