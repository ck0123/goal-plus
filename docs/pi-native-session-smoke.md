# Pi Native Session Resume Smoke

This page records the real-host checks used to choose the Pi continuation
boundary. Both checks ran on 2026-07-19 with Pi 0.80.6 and
`openai-codex/gpt-5.6-luna` through the project Goal Plus extension.

## Pi Mechanism Confirmed

Pi's CLI resolves `--session-id` inside the configured `--session-dir`: it
opens the exact existing session when found and creates it only when missing.
Its RPC `get_entries(since=<entry_id>)` returns entries strictly after that
stable id and rejects an unknown cursor. Goal Plus uses these two native
contracts directly; it does not reconstruct conversation state from prompts.

## Cross-Process Native Resume

The first check used a real Search run and verifier-backed candidate:

- run: `run_20260718_225437_41c8be4a`
- candidate: `c001`
- native session: `agent_20260718_225437_41c8be4a_001`
- process dispatches: PID `81151`, then PID `81315`
- persisted session: one JSONL file under `.gp/host-sessions/pi/`
- incremental cursor: the second dispatch started from entry `fbb37c38`
- verifier score: `1.0` after the first dispatch, `2.0` after the second

The second process loaded the same native conversation and runtime agent
session, continued the same candidate workspace, and reported only the new
entry usage while retaining cumulative usage. A third dispatch used PID
`81828` and again resumed from the exact prior cursor, confirming that the
contract is not tied to either of the first two processes.

After all three dispatches, the native JSONL was 216 KB with 32 lines. The
third process parsed that file locally, while Goal Plus received only four new
entries after cursor `af1e67bc`; the compact metadata event log was 44 KB.
The measured cumulative model cost was `$0.0851268`, of which the third
dispatch contributed `$0.0213828`. This separates Pi's local session-load cost
from the previous full-history RPC transfer cost.

## Same-PID Prototype

The control check kept one foreground `pi --mode rpc` process alive and sent
two Goal Plus worker prompts through it:

- run: `run_20260718_230234_9a0251a7`
- native session: `agent_20260718_230234_9a0251a7_001`
- both turns: PID `83356`
- persisted session files: `1`
- first turn entries: `6`
- second turn incremental entries: `4`
- runtime resume context: `mode=native_session`,
  `is_native_session_resume=true`

The first turn returned `LIVE-TURN-ONE`; the second returned
`LIVE-TURN-TWO` and confirmed native continuation. Neither turn edited the
workspace or ran the verifier. This proves that a persistent same-PID Pi RPC
supervisor is technically possible on the Goal Plus surface.

## Dispatch Deadline Reset Regression

Run `run_20260719_043531_2e9d9810` verified that dispatch-scoped closeout
messages cannot control a later native-session resume. The first process was
intentionally limited to 15 seconds, timed out, and persisted a closeout
message with score `0.0`. The next process resumed the same native session with
a fresh 120-second budget, treated the old closeout as historical, changed
`answer.txt` to `9`, and recorded worker and parent verifier scores of `9.0`.

After final selection and promotion verification, the durable score history was
`0.0 -> 9.0 -> 9.0 -> 9.0`. Continuation prompts now state explicitly that only
deadline, closeout, and time-advisory messages after the latest launch apply to
the current dispatch.

## Decision

The product contract requires native conversation continuity, not stable OS
process identity. Cross-process resume already preserves the native session,
candidate, workspace, runtime handle, and incremental metrics cursor. A
persistent supervisor would additionally own idle process lifetime, crash
recovery, leases, upgrades, and cleanup without improving those semantics.

Therefore the default remains cross-process native-session resume. Implement a
persistent Pi supervisor only if a future requirement explicitly depends on
the same PID or on in-memory process state that Pi does not persist.
