# Step Tiers + Dead Code Cleanup Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fake MCP-level step tracking (`max_steps`, `max_tool_calls`, `record_agent_step`) with four OpenCode agent variants at fixed step tiers (Flash=15, Default=50, Deep=100, ExtraDeep=150), and document how to observe OpenCode's step count via SQLite.

**Architecture:** OpenCode's frontmatter `steps` field is the only real step bound — it's enforced by the host, ends with a clean summarize-and-stop, and is visible via the SQLite `part` table. The MCP runtime stops trying to track steps itself. Specs select a tier via `worker_agent_type` (e.g. `AnySearchAgentFlash`).

**Tech Stack:** Python 3, Pydantic, FastMCP, pytest, OpenCode agent markdown.

---

## File Structure

- Create: `.opencode/agents/AnySearchAgentFlash.md` (steps: 15)
- Modify: `.opencode/agents/AnySearchAgent.md` (already steps: 50 — keep as Default)
- Create: `.opencode/agents/AnySearchAgentDeep.md` (steps: 100)
- Create: `.opencode/agents/AnySearchAgentExtraDeep.md` (steps: 150)
- Modify: `src/agentic_any_search_mcp/models.py` — drop `max_steps`, `max_tool_calls`, `tokens` counter slot
- Modify: `src/agentic_any_search_mcp/runtime.py` — drop `record_agent_step`, drop step/tool_call finalize branches, drop counters init for those keys
- Modify: `src/agentic_any_search_mcp/tools.py` — drop `search_record_agent_step`
- Modify: `src/agentic_any_search_mcp/server.py` — drop registration
- Modify: `tests/test_models.py`, `tests/test_runtime_unit.py`, `tests/test_tools.py`, `tests/test_server.py` — drop tests for removed APIs
- Modify: `.opencode/agents/AnySearchAgent.md` — drop "call record_agent_step" line
- Modify: `.opencode/skills/search/SKILL.md` — drop `search_record_agent_step` row, add tier table
- Modify: `docs/debugging-runtime.md` — add "Checking OpenCode step count" section
- Modify: `README.md` — mention four tiers in the worker_agent_type description

---

## Task 1: Create three new agent variants (Flash / Deep / ExtraDeep)

**Files:**
- Create: `.opencode/agents/AnySearchAgentFlash.md`
- Create: `.opencode/agents/AnySearchAgentDeep.md`
- Create: `.opencode/agents/AnySearchAgentExtraDeep.md`
- Read for reference: `.opencode/agents/AnySearchAgent.md`

- [ ] **Step 1: Read current AnySearchAgent.md to use as the template**

Run: `cat .opencode/agents/AnySearchAgent.md`
Note the frontmatter fields (name, description, mode, temperature, steps, permission) and the body. The three new files will mirror this exactly except for `name`, `description`, and `steps`.

- [ ] **Step 2: Create AnySearchAgentFlash.md**

Write `.opencode/agents/AnySearchAgentFlash.md` with this frontmatter and the same body as AnySearchAgent.md (copy verbatim from `# AnySearchAgent` onward):

```markdown
---
name: AnySearchAgentFlash
description: Fast AnySearchAgent variant for smoke tests and cheap iterations - bounded to 15 OpenCode steps. Use when worker_agent_type=AnySearchAgentFlash is set in the spec.
mode: subagent
temperature: 0.2
steps: 15

permission:
  task: deny
  todowrite: deny
  bash:
    "rm*": deny
    "*/rm*": deny
    "mv*": deny
    "rmdir*": deny
    "unlink*": deny
    "trash*": deny
    "find*delete*": deny
---

# AnySearchAgentFlash

(Same body as AnySearchAgent.md — copy from `# AnySearchAgent` onward.)
```

- [ ] **Step 3: Create AnySearchAgentDeep.md**

Same pattern, frontmatter:

```markdown
---
name: AnySearchAgentDeep
description: Deep-exploration AnySearchAgent variant bounded to 100 OpenCode steps. Use when worker_agent_type=AnySearchAgentDeep is set in the spec and the task needs sustained iteration.
mode: subagent
temperature: 0.2
steps: 100

permission:
  task: deny
  todowrite: deny
  bash:
    "rm*": deny
    "*/rm*": deny
    "mv*": deny
    "rmdir*": deny
    "unlink*": deny
    "trash*": deny
    "find*delete*": deny
---
```

- [ ] **Step 4: Create AnySearchAgentExtraDeep.md**

Same pattern, frontmatter:

```markdown
---
name: AnySearchAgentExtraDeep
description: Long-running AnySearchAgent variant bounded to 150 OpenCode steps. Use when worker_agent_type=AnySearchAgentExtraDeep is set in the spec and the task is expected to need extensive search.
mode: subagent
temperature: 0.2
steps: 150

permission:
  task: deny
  todowrite: deny
  bash:
    "rm*": deny
    "*/rm*": deny
    "mv*": deny
    "rmdir*": deny
    "unlink*": deny
    "trash*": deny
    "find*delete*": deny
---
```

- [ ] **Step 5: Update test_opencode_assets.py to cover all four tiers**

Add a parametrized test that asserts each variant file exists with the right `steps:` value:

```python
@pytest.mark.parametrize(
    "agent_file,expected_steps",
    [
        ("AnySearchAgentFlash.md", 15),
        ("AnySearchAgent.md", 50),
        ("AnySearchAgentDeep.md", 100),
        ("AnySearchAgentExtraDeep.md", 150),
    ],
)
def test_any_search_agent_tier_has_expected_step_cap(
    agent_file: str, expected_steps: int
) -> None:
    text = (ROOT / ".opencode" / "agents" / agent_file).read_text(encoding="utf-8")
    assert f"steps: {expected_steps}" in text
    assert "mode: subagent" in text
```

- [ ] **Step 6: Run new test to verify it passes**

Run: `python -m pytest tests/test_opencode_assets.py -v`
Expected: PASS for all four parametrized cases.

- [ ] **Step 7: Commit**

```bash
git add .opencode/agents/AnySearchAgentFlash.md \
        .opencode/agents/AnySearchAgentDeep.md \
        .opencode/agents/AnySearchAgentExtraDeep.md \
        tests/test_opencode_assets.py
git commit -m "feat(agents): add Flash/Deep/ExtraDeep step-tier variants"
```

---

## Task 2: Remove `max_steps` and `max_tool_calls` from AgentSessionBudget

**Files:**
- Modify: `src/agentic_any_search_mcp/models.py:373-374`
- Modify: `tests/test_models.py:146-147, 176`

- [ ] **Step 1: Update test_models.py first to reflect the new shape**

In `tests/test_models.py`, find the AgentSessionBudget construction (around line 146) and remove the two fields. The block currently looks like:

```python
        max_wall_seconds=120,
        deadline_at="2026-01-01T00:00:00Z",
        max_steps=12,
        max_tool_calls=40,
        max_verifier_runs=3,
```

Change to:

```python
        max_wall_seconds=120,
        deadline_at="2026-01-01T00:00:00Z",
        max_verifier_runs=3,
```

And around line 176, remove:

```python
    assert session.budget.max_steps == 12
    assert session.budget.max_tool_calls == 40
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_models.py -v`
Expected: FAIL — `max_steps` / `max_tool_calls` no longer accepted.

Wait — actually since the fields are still on the model, the test will pass but the assertions on `max_steps` will fail because we removed them. Run anyway; failures point to the fields to remove.

- [ ] **Step 3: Drop fields from AgentSessionBudget**

In `src/agentic_any_search_mcp/models.py`, find (around line 373):

```python
class AgentSessionBudget(SearchModel):
    max_wall_seconds: int = Field(gt=0)
    deadline_at: str
    max_steps: int | None = Field(default=None, gt=0)
    max_tool_calls: int | None = Field(default=None, gt=0)
    max_verifier_runs: int = Field(default=3, ge=1)
```

Change to:

```python
class AgentSessionBudget(SearchModel):
    max_wall_seconds: int = Field(gt=0)
    deadline_at: str
    max_verifier_runs: int = Field(default=3, ge=1)
```

- [ ] **Step 4: Run full test suite to find downstream references**

Run: `python -m pytest -q`
Expected: failures in runtime / test_runtime_unit around `max_steps` and `max_tool_calls`. Note them for Task 3.

- [ ] **Step 5: Commit**

```bash
git add src/agentic_any_search_mcp/models.py tests/test_models.py
git commit -m "refactor(models): drop max_steps and max_tool_calls from AgentSessionBudget"
```

---

## Task 3: Remove `record_agent_step` runtime + tools + server

**Files:**
- Modify: `src/agentic_any_search_mcp/runtime.py` (multiple locations)
- Modify: `src/agentic_any_search_mcp/tools.py:160-178`
- Modify: `src/agentic_any_search_mcp/server.py:121-130`
- Modify: `tests/test_runtime_unit.py:280, 319+` (drop the max_steps=max_steps test)
- Modify: `tests/test_tools.py:140, 223` (drop search_record_agent_step assertion)
- Modify: `tests/test_server.py:38, 119` (drop registration entry)

- [ ] **Step 1: Drop `record_agent_step` from runtime.py**

Find the method (around line 689) and delete the entire method body. Also remove the references in `start_agent_session` that pull `max_steps` / `max_tool_calls` from the requested budget (around line 449-450):

```python
                "max_steps": requested_budget.get("max_steps"),
                "max_tool_calls": requested_budget.get("max_tool_calls"),
```

Those two lines should be deleted; the surrounding dict comprehension stays.

- [ ] **Step 2: Drop the counters init for steps / tool_calls / tokens**

In `AgentSessionRecord` (around line 384 in models.py), the default_factory for `counters` is:

```python
counters: dict[str, int] = Field(
    default_factory=lambda: {
        "steps": 0,
        "tool_calls": 0,
        "verifier_runs": 0,
        "tokens": 0,
    }
)
```

Change to keep only `verifier_runs`:

```python
counters: dict[str, int] = Field(
    default_factory=lambda: {"verifier_runs": 0}
)
```

- [ ] **Step 3: Drop search_record_agent_step from tools.py**

Delete the method at tools.py:160-178:

```python
def search_record_agent_step(
    self,
    agent_session_id: str,
    steps_delta: int = 0,
    tool_calls_delta: int = 0,
    verifier_runs_delta: int = 0,
    tokens_delta: int = 0,
) -> dict[str, Any]:
    return self.runtime.record_agent_step(
        agent_session_id=agent_session_id,
        steps_delta=steps_delta,
        tool_calls_delta=tool_calls_delta,
        verifier_runs_delta=verifier_runs_delta,
        tokens_delta=tokens_delta,
    ).model_dump(mode="json")
```

- [ ] **Step 4: Drop registration from server.py**

Delete the tool registration block at server.py:121-130:

```python
@mcp.tool()
def search_record_agent_step(
    agent_session_id: str,
    steps_delta: int = 0,
    tool_calls_delta: int = 0,
    verifier_runs_delta: int = 0,
    tokens_delta: int = 0,
) -> dict[str, Any]:
    return tools.search_record_agent_step(
        agent_session_id,
        steps_delta,
        tool_calls_delta,
        verifier_runs_delta,
        tokens_delta,
    )
```

- [ ] **Step 5: Drop test references**

In `tests/test_tools.py`:
- Line 140: remove `runtime.record_agent_step.return_value = ...`
- Line 223: remove `assert tools.search_record_agent_step("agent_001", steps_delta=1)["counters"]["steps"] == 1`

In `tests/test_server.py`:
- Line 38: remove `"search_record_agent_step",` from the registered tool list
- Lines 119-121: remove the stub `def search_record_agent_step(self, *args, **kwargs): return {...}` from the fake runtime

In `tests/test_runtime_unit.py`:
- Find the test that passes `budget={"max_wall_seconds": 120, "max_steps": 2, "max_tool_calls": 4}` (around line 280) and the test that calls `runtime.record_agent_step(...)` (around line 319). Either delete these tests entirely or rewrite them to test only verifier_runs + max_wall_seconds.

For test_runtime_unit.py, locate the impacted test name(s) by running:

```bash
python -m pytest tests/test_runtime_unit.py -v 2>&1 | grep FAIL
```

For each FAILed test that touches `record_agent_step` / `max_steps` / `max_tool_calls`, delete it. If the test was specifically about step enforcement, it's now obsolete — that behavior is gone.

- [ ] **Step 6: Run full test suite**

Run: `python -m pytest -q`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/agentic_any_search_mcp/runtime.py \
        src/agentic_any_search_mcp/models.py \
        src/agentic_any_search_mcp/tools.py \
        src/agentic_any_search_mcp/server.py \
        tests/
git commit -m "refactor: remove record_agent_step and step/tool_call budget tracking"
```

---

## Task 4: Update prompts and docs

**Files:**
- Modify: `.opencode/agents/AnySearchAgent.md:37, 111`
- Modify: `.opencode/skills/search/SKILL.md:50`
- Modify: `docs/debugging-runtime.md` (add OpenCode step section)
- Modify: `README.md` (mention tier variants)

- [ ] **Step 1: Update AnySearchAgent.md**

Find line 37 (Required Input section):

```text
Read `context.budget.deadline_at`, `context.budget.max_steps`, `context.budget.max_tool_calls`, and `context.budget.max_verifier_runs`. Treat the deadline as a hard delivery deadline for the candidate artifact.
```

Change to:

```text
Read `context.budget.deadline_at` and `context.budget.max_verifier_runs`. Treat the deadline as a hard delivery deadline for the candidate artifact. The OpenCode `steps` budget (15/50/100/150 depending on the agent variant you were launched as) is enforced by the host — you will be asked to summarize and stop when it runs out.
```

Find line 111 (Session Rules section):

```text
2. Call `search-runtime_search_record_agent_step(steps_delta=1)` after each MCP tool call if you want fine-grained step tracking; otherwise rely on the OpenCode `steps` budget.
```

Delete this line entirely (renumber subsequent items if needed).

Apply the same edits to the three new variant files (Flash / Deep / ExtraDeep) since they copied the body verbatim.

- [ ] **Step 2: Update SKILL.md**

Find line 50:

```text
| `search_record_agent_step` | `search-runtime_search_record_agent_step` |
```

Delete the row.

In the same SKILL.md, find the `strategy.worker_agent_type` description (around the SearchSpec section) and add a tier table:

```markdown
`strategy.worker_agent_type` selects the OpenCode subagent variant, which fixes the per-session step cap:

| Variant | Steps | Use when |
|---|---|---|
| `AnySearchAgentFlash` | 15 | Smoke tests, cheap iterations |
| `AnySearchAgent` (default) | 50 | Standard autoresearch loop |
| `AnySearchAgentDeep` | 100 | Sustained iteration on harder problems |
| `AnySearchAgentExtraDeep` | 150 | Extensive search, complex fixtures |
```

- [ ] **Step 3: Add "Checking OpenCode step count" to docs/debugging-runtime.md**

Append a new section before "Common Failure Modes":

```markdown
## Checking OpenCode Step Count

The MCP runtime does not track agent steps — OpenCode enforces the per-agent `steps` cap (defined in each `.opencode/agents/*.md` frontmatter) and you read the live count from the SQLite DB.

### Step count for a session

```bash
sqlite3 ~/.local/share/opencode/opencode.db \
  "SELECT count(*) FROM part
   WHERE session_id='<SID>'
     AND json_extract(data, '\$.type')='step-start';"
```

### All recent subagent sessions with step usage

```bash
sqlite3 ~/.local/share/opencode/opencode.db \
  "SELECT s.id, s.title, count(p.id) as step_starts
   FROM session s
   LEFT JOIN part p ON p.session_id = s.id
     AND json_extract(p.data, '\$.type')='step-start'
   WHERE s.agent='AnySearchAgent'
   GROUP BY s.id
   ORDER BY s.time_created DESC LIMIT 10;"
```

When the step cap is reached OpenCode injects a system prompt instructing the agent to summarize and stop — the session ends cleanly without a hard kill.
```

- [ ] **Step 4: Update README.md**

Find the existing line that mentions `worker_agent_type` (around the candidate execution description). Append:

```markdown
`strategy.worker_agent_type` can be set to `AnySearchAgent` (default, 50 steps), `AnySearchAgentFlash` (15), `AnySearchAgentDeep` (100), or `AnySearchAgentExtraDeep` (150). The step cap is enforced by OpenCode.
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add .opencode/agents/AnySearchAgent.md \
        .opencode/agents/AnySearchAgentFlash.md \
        .opencode/agents/AnySearchAgentDeep.md \
        .opencode/agents/AnySearchAgentExtraDeep.md \
        .opencode/skills/search/SKILL.md \
        docs/debugging-runtime.md \
        README.md
git commit -m "docs: four step-tier variants + drop record_agent_step from prompts"
```

---

## Task 5: Validation

- [ ] **Step 1: Full pytest**

Run: `python -m pytest -q`
Expected: all pass.

- [ ] **Step 2: Compileall**

Run: `python -m compileall -q src tests`
Expected: no errors.

- [ ] **Step 3: Grep for residual references**

Run: `grep -rn "record_agent_step\|max_steps\|max_tool_calls" src/ tests/ .opencode/ docs/ README.md 2>/dev/null | grep -v __pycache__ | grep -v docs/superpowers/plans`
Expected: no matches (or only matches inside `verifier_runs`/`max_verifier_runs`).

- [ ] **Step 4: Commit fixups if any**

If anything needed adjustment:

```bash
git add -A
git commit -m "fix: address validation issues from step-tier cleanup"
```

---

## Notes

- **No new MCP state fields.** Step count stays in OpenCode SQLite only — MCP runtime doesn't try to mirror it.
- **No removal of `max_verifier_runs`.** That one works (enforced inside `run_verifier`); only the fake `max_steps` / `max_tool_calls` go away.
- **No removal of `wall_clock_seconds`.** Time bound stays; it's the only runtime-side hard stop.
- **Variant body is identical.** Only frontmatter (`name`, `description`, `steps`) differs. If you later want variant-specific instructions, edit each file's body individually.
