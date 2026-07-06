Use `/goal-plus` for this ordinary coding task. Load the project-local
`goal-plus` skill and use the `search-runtime` MCP `goal_plus_*` tools.

You are in `examples/goal-and-goal-plus/goal-plus`.

Fix the local `initial_program.py` bug so the local evaluator reaches
`combined_score = 1.0`.

Constraints:

- Edit only `initial_program.py`.
- Do not edit `evaluator.py`, `config.yaml`, `FIXTURE.md`, or files outside this directory.
- Keep the patch minimal.
- This is an ordinary single-path bug fix. Treat it as Goal Mode unless your
  triage finds a real need for Search Mode.
- Do not create a SearchSpec or run multi-candidate search for this experiment.
- Before editing, call `goal_plus_create(raw_goal=...)`.
- Then call `goal_plus_record_triage(...)` with Goal Mode / non-search
  reasoning.
- After verification, call `goal_plus_set_status(status="complete", evidence=[...])`.
- Before the final answer, call `goal_plus_gate(event="stop", context={})`.
- Run this verifier command before the final answer:

```bash
python -c "from evaluator import evaluate; import json; print(json.dumps(evaluate('initial_program.py'), indent=2, sort_keys=True))"
```

Final answer requirements:

- State whether you stayed in Goal Mode or entered Search Mode.
- State the file changed.
- Include the verifier result summary.
- End with a fenced JSON block tagged `experiment_result`:

```experiment_result
{
  "entrypoint": "goal-plus",
  "mode": "goal",
  "search_mode_used": false,
  "changed_files": ["initial_program.py"],
  "combined_score": 1.0,
  "verification_command": "python -c \"from evaluator import evaluate; import json; print(json.dumps(evaluate('initial_program.py'), indent=2, sort_keys=True))\""
}
```
