Use `/goal` for this ordinary coding task.

You are in `examples/goal-and-goal-plus/goal`.

Fix the local `initial_program.py` bug so the local evaluator reaches
`combined_score = 1.0`.

Constraints:

- Edit only `initial_program.py`.
- Do not edit `evaluator.py`, `config.yaml`, `FIXTURE.md`, or files outside this directory.
- Keep the patch minimal.
- Run this verifier command before the final answer:

```bash
python -c "from evaluator import evaluate; import json; print(json.dumps(evaluate('initial_program.py'), indent=2, sort_keys=True))"
```

Final answer requirements:

- State whether you stayed in ordinary goal mode.
- State the file changed.
- Include the verifier result summary.
- End with a fenced JSON block tagged `experiment_result`:

```experiment_result
{
  "entrypoint": "goal",
  "mode": "goal",
  "search_mode_used": false,
  "changed_files": ["initial_program.py"],
  "combined_score": 1.0,
  "verification_command": "python -c \"from evaluator import evaluate; import json; print(json.dumps(evaluate('initial_program.py'), indent=2, sort_keys=True))\""
}
```
