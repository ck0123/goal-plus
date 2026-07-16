#!/usr/bin/env bash

set -uo pipefail

interval="${INTERVAL:-5}"
run_limit="${RUN_LIMIT:-3}"
stale_after="${STALE_AFTER:-600}"
once=0
clear_screen=1
json_output=0
verbose=0
explicit_root=0
target=""
goal_id=""
run_id=""

usage() {
  cat <<'EOF'
Usage: ./scripts/monitor_goal_plus.sh [options] [directory]

Poll detailed Goal Plus/Search state for a project directory or runtime root.
The directory defaults to the current working directory. The script recognizes
runtime roots named .gp, .search, and .goal-plus, or a runtime root passed
directly.

Options:
  --once                 Print one snapshot and exit.
  --interval SECONDS     Refresh interval (default: INTERVAL or 5).
  --run-limit N          Newest runs shown (default: RUN_LIMIT or 3; 0 = all).
  --stale-after SECONDS  Age used for stale-worker warnings (default: 600).
  --goal GOAL_PLUS_ID    Select one Goal Plus record.
  --run RUN_ID           Select one Search run.
  --root PATH            Treat PATH as the runtime root; skip discovery.
  --json                 Emit the assembled read-only snapshot as JSON.
  --verbose              Show full per-worker usage, identity, directives, and handoffs.
  --no-clear             Do not clear an interactive terminal between polls.
  -h, --help             Show this help.

Environment:
  INTERVAL=2             Default refresh interval.
  RUN_LIMIT=3            Default newest-run limit.
  STALE_AFTER=600        Default stale threshold.

Examples:
  ./scripts/monitor_goal_plus.sh /path/to/project
  ./scripts/monitor_goal_plus.sh --once /path/to/project/.gp
  ./scripts/monitor_goal_plus.sh --run run_... /path/to/project
  INTERVAL=2 RUN_LIMIT=0 ./scripts/monitor_goal_plus.sh .
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --once)
      once=1
      shift
      ;;
    --interval)
      [[ -n "${2:-}" ]] || { echo "--interval requires a value" >&2; exit 2; }
      interval="$2"
      shift 2
      ;;
    --run-limit)
      [[ -n "${2:-}" ]] || { echo "--run-limit requires a value" >&2; exit 2; }
      run_limit="$2"
      shift 2
      ;;
    --stale-after)
      [[ -n "${2:-}" ]] || { echo "--stale-after requires a value" >&2; exit 2; }
      stale_after="$2"
      shift 2
      ;;
    --goal)
      [[ -n "${2:-}" ]] || { echo "--goal requires a value" >&2; exit 2; }
      goal_id="$2"
      shift 2
      ;;
    --run)
      [[ -n "${2:-}" ]] || { echo "--run requires a value" >&2; exit 2; }
      run_id="$2"
      shift 2
      ;;
    --root)
      [[ -n "${2:-}" ]] || { echo "--root requires a value" >&2; exit 2; }
      target="$2"
      explicit_root=1
      shift 2
      ;;
    --json)
      json_output=1
      once=1
      clear_screen=0
      shift
      ;;
    --verbose)
      verbose=1
      shift
      ;;
    --no-clear)
      clear_screen=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      if [[ -n "$target" ]]; then
        echo "Only one directory may be monitored at a time." >&2
        exit 2
      fi
      target="$1"
      shift
      ;;
  esac
done

target="${target:-.}"

if ! [[ "$interval" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  echo "Invalid interval: $interval" >&2
  exit 2
fi
if ! [[ "$run_limit" =~ ^[0-9]+$ ]]; then
  echo "Invalid run limit: $run_limit" >&2
  exit 2
fi
if ! [[ "$stale_after" =~ ^[0-9]+$ ]]; then
  echo "Invalid stale threshold: $stale_after" >&2
  exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
export PYTHONPATH="$repo_root/src${PYTHONPATH:+:$PYTHONPATH}"

trap 'exit 0' INT TERM

while true; do
  if [[ "$clear_screen" -eq 1 && -t 1 ]]; then
    clear
  fi

  GP_MONITOR_TARGET="$target" \
  GP_MONITOR_EXPLICIT_ROOT="$explicit_root" \
  GP_MONITOR_GOAL_ID="$goal_id" \
  GP_MONITOR_RUN_ID="$run_id" \
  GP_MONITOR_RUN_LIMIT="$run_limit" \
  GP_MONITOR_STALE_AFTER="$stale_after" \
  GP_MONITOR_JSON="$json_output" \
  GP_MONITOR_VERBOSE="$verbose" \
  python3 - <<'PY'
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
from typing import Any

from goal_plus.monitor import goal_plus_monitor_snapshot


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def runtime_markers(path: Path) -> bool:
    return any((path / name).exists() for name in ("goal-plus", "runs", "specs"))


def newest_state_mtime(path: Path) -> float:
    mtimes: list[float] = []
    for pattern in ("goal-plus/gp_*/goal.json", "runs/run_*/run.json"):
        for candidate in path.glob(pattern):
            try:
                mtimes.append(candidate.stat().st_mtime)
            except OSError:
                pass
    return max(mtimes, default=0.0)


def resolve_runtime_root(target: Path, explicit: bool) -> Path:
    target = target.expanduser().resolve()
    if explicit:
        if not target.is_dir():
            raise FileNotFoundError(f"runtime root is not a directory: {target}")
        return target

    candidates: list[Path] = []
    if runtime_markers(target) or target.name in {".gp", ".search", ".goal-plus"}:
        candidates.append(target)
    for base in (target, *target.parents):
        for name in (".gp", ".search", ".goal-plus"):
            candidate = base / name
            if candidate.is_dir():
                candidates.append(candidate)
        if candidates:
            break
    if not candidates:
        raise FileNotFoundError(
            f"no Goal Plus runtime root found from {target}; expected .gp, .search, or .goal-plus"
        )
    unique = list(dict.fromkeys(candidates))
    return max(unique, key=lambda path: (newest_state_mtime(path), path.name == ".gp"))


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(normalized)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def age(value: Any) -> str:
    parsed = parse_time(value)
    if parsed is None:
        return "-"
    seconds = max(0, int((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    if seconds < 86400:
        return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"
    return f"{seconds // 86400}d{seconds % 86400 // 3600:02d}h"


def duration(value: Any) -> str:
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return "-"
    seconds = max(0, int(round(float(value))))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"


def score(value: Any) -> str:
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return "-"
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.6g}"


def tokens(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "-"
    number = float(value)
    if abs(number) >= 1_000_000:
        return f"{number / 1_000_000:.2f}m"
    if abs(number) >= 1_000:
        return f"{number / 1_000:.1f}k"
    return str(int(number))


def money(value: Any) -> str:
    return f"${float(value):.4f}" if isinstance(value, (int, float)) else "-"


def short(value: Any, limit: int = 150) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def path_state(value: Any) -> str:
    if not isinstance(value, dict):
        return "-"
    path = value.get("path")
    if not path:
        return "-"
    return f"{path} ({'exists' if value.get('exists') else 'missing'})"


def select_goal(root: Path, requested: str) -> str | None:
    if requested:
        return requested
    ranked: list[tuple[float, float, str]] = []
    for path in (root / "goal-plus").glob("gp_*/goal.json"):
        goal = load(path)
        updated = parse_time(goal.get("updated_at"))
        updated_epoch = updated.timestamp() if updated is not None else path.stat().st_mtime
        task_run_ids = [
            item.get("run_id")
            for item in goal.get("search_tasks", [])
            if isinstance(item, dict) and item.get("run_id")
        ]
        linked = goal.get("linked_search")
        if isinstance(linked, dict) and linked.get("run_id"):
            task_run_ids.append(linked["run_id"])
        run_epochs = []
        for task_run_id in dict.fromkeys(task_run_ids):
            run = load(root / "runs" / str(task_run_id) / "run.json")
            created = parse_time(run.get("created_at"))
            if created is not None:
                run_epochs.append(created.timestamp())
        activity_epoch = max(run_epochs) if run_epochs else updated_epoch
        ranked.append((activity_epoch, updated_epoch, path.parent.name))
    return max(ranked)[2] if ranked else None


def run_ids_for(root: Path, snapshot: dict[str, Any], requested: str, limit: int) -> list[str]:
    if requested:
        return [requested]
    linked = [
        str(item["run_id"])
        for item in snapshot.get("search_tasks", [])
        if isinstance(item, dict) and item.get("run_id") and item.get("run_exists")
    ]
    if linked:
        values = list(dict.fromkeys(linked))
    else:
        paths = sorted(
            (root / "runs").glob("run_*/run.json"),
            key=lambda path: path.stat().st_mtime,
        )
        values = [path.parent.name for path in paths]
    if limit > 0:
        values = values[-limit:]
    return values


def read_spec(root: Path, snapshot: dict[str, Any]) -> dict[str, Any]:
    run = snapshot.get("run")
    if not isinstance(run, dict) or not run.get("frozen_spec_id"):
        return {}
    frozen = load(root / "specs" / str(run["frozen_spec_id"]) / "frozen_spec.json")
    spec = frozen.get("spec")
    return spec if isinstance(spec, dict) else {}


def read_plans(root: Path, run_id: str) -> list[dict[str, Any]]:
    return [load(path) for path in sorted((root / "runs" / run_id / "plans").glob("plan_*.json"))]


def read_candidate(root: Path, run_id: str, candidate_id: str) -> dict[str, Any]:
    return load(root / "runs" / run_id / "candidates" / candidate_id / "candidate.json")


def read_sessions(root: Path, run_id: str) -> dict[str, dict[str, Any]]:
    result = {}
    for path in sorted((root / "runs" / run_id / "agent_sessions").glob("agent_*.json")):
        payload = load(path)
        if payload.get("agent_session_id"):
            result[str(payload["agent_session_id"])] = payload
    return result


def read_pools(root: Path, run_id: str) -> list[dict[str, Any]]:
    pools = []
    for path in sorted((root / "host-pools" / "pi").glob("pool_*/pool.json")):
        pool = load(path)
        if pool.get("run_id") != run_id:
            continue
        jobs = [load(job_path) for job_path in sorted(path.parent.glob("jobs/job_*/job.json"))]
        pools.append({"pool": pool, "jobs": jobs})
    return pools


def run_artifacts(root: Path, run_id: str) -> dict[str, Any]:
    run_dir = root / "runs" / run_id
    patches = sorted((run_dir / "promotion").glob("*.patch"))
    report = run_dir / "report.md"
    return {
        "report": str(report) if report.exists() else None,
        "promotion_patches": [str(path) for path in patches],
    }


def handoff_summary(session: dict[str, Any]) -> tuple[str | None, list[str]]:
    handle = session.get("host_handle")
    metadata = handle.get("metadata") if isinstance(handle, dict) else None
    progress = metadata.get("progress_handoff") if isinstance(metadata, dict) else None
    if not isinstance(progress, dict):
        return None, []
    model = progress.get("model_handoff") if isinstance(progress.get("model_handoff"), dict) else progress
    summary = model.get("summary") if isinstance(model, dict) else None
    next_steps = model.get("next_steps") if isinstance(model, dict) else None
    return (str(summary) if summary else None, [str(item) for item in next_steps or []])


def assemble(root: Path, goal_id: str | None, requested_run: str, limit: int, stale: int) -> dict[str, Any]:
    primary = goal_plus_monitor_snapshot(
        root,
        goal_plus_id=goal_id,
        run_id=requested_run or None,
        stale_after_seconds=stale,
    )
    ids = run_ids_for(root, primary, requested_run, limit)
    runs = []
    for candidate_run_id in ids:
        detail = goal_plus_monitor_snapshot(
            root,
            run_id=candidate_run_id,
            stale_after_seconds=stale,
        )
        candidates = {
            candidate_id: read_candidate(root, candidate_run_id, candidate_id)
            for candidate_id in detail.get("candidates", {})
        }
        runs.append(
            {
                "snapshot": detail,
                "spec": read_spec(root, detail),
                "plans": read_plans(root, candidate_run_id),
                "candidate_records": candidates,
                "session_records": read_sessions(root, candidate_run_id),
                "pi_pools": read_pools(root, candidate_run_id),
                "artifacts": run_artifacts(root, candidate_run_id),
            }
        )
    return {
        "snapshot_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "runtime_root": str(root),
        "goal_plus_id": goal_id,
        "primary": primary,
        "runs": runs,
    }


def render_verbose_goal(bundle: dict[str, Any]) -> None:
    primary = bundle["primary"]
    goal = primary.get("goal_plus")
    if not isinstance(goal, dict):
        print("goal: no Goal Plus record; showing Search runs directly")
        return
    print(
        f"goal {goal.get('goal_plus_id')}: status={goal.get('status')} "
        f"phase={goal.get('phase')} revision={goal.get('goal_revision')} "
        f"updated={age(goal.get('updated_at'))} ago"
    )
    print(f"  objective: {short(goal.get('raw_goal'), 190)}")
    next_action = goal.get("next_action")
    if isinstance(next_action, dict):
        action = next_action.get("kind") or next_action.get("action") or "pending"
        detail = next_action.get("instruction") or next_action.get("reason") or next_action
        print(f"  next: {action} | {short(detail, 170)}")
    final_check = goal.get("latest_final_check")
    if isinstance(final_check, dict):
        print(
            f"  final check: status={final_check.get('status', '-')} "
            f"verdict={final_check.get('verdict') or '-'} "
            f"checker={final_check.get('checker_host') or '-'}"
        )
    aggregate = primary.get("search_task_aggregate") or {}
    print(
        "  aggregate: "
        f"tasks={aggregate.get('search_tasks_total', 0)} "
        f"rounds={aggregate.get('started_rounds_total', 0)}/{aggregate.get('planning_rounds_total', 0)} "
        f"candidates={aggregate.get('candidates_evaluated', 0)}/{aggregate.get('candidates_total', 0)} "
        f"sessions={aggregate.get('worker_sessions_total', 0)} "
        f"verifiers={aggregate.get('verifier_runs_total', 0)} "
        f"cost={money(aggregate.get('estimated_cost_total'))}"
    )


def render_plans(plans: list[dict[str, Any]]) -> None:
    if not plans:
        print("  plans: none")
        return
    print("  plans:")
    for plan in plans:
        trace = plan.get("strategy_trace") if isinstance(plan.get("strategy_trace"), dict) else {}
        started = plan.get("started_candidate_ids") or []
        print(
            f"    {plan.get('plan_id')}: status={plan.get('status')} "
            f"requested={plan.get('requested_k')} planned={plan.get('planned_k')} "
            f"started={','.join(started) or '-'}"
        )
        if trace.get("selection_rule"):
            print(f"      selection: {short(trace['selection_rule'], 170)}")
        state_keys = (
            "generation", "population_size", "tree_depth", "sampling_mode",
            "parent_candidate_id", "selected_worker_agent_type", "seed",
        )
        state = {key: trace[key] for key in state_keys if key in trace}
        if state:
            print(f"      strategy state: {json.dumps(state, ensure_ascii=False, sort_keys=True)}")


def render_pools(pools: list[dict[str, Any]]) -> None:
    for item in pools:
        pool = item["pool"]
        jobs = item["jobs"]
        counts = Counter(str(job.get("status", "unknown")) for job in jobs)
        count_text = ", ".join(f"{key}={counts[key]}" for key in sorted(counts)) or "no jobs"
        print(
            f"  pi pool {pool.get('pool_id')}: state={pool.get('state')} "
            f"parallel={pool.get('max_parallel')} {count_text} "
            f"updated={age(pool.get('updated_at'))} ago (persisted host snapshot)"
        )
        for job in jobs:
            print(
                f"    job {job.get('job_id')}: candidate={job.get('candidate_id')} "
                f"status={job.get('status')} pid={job.get('pid', '-')} "
                f"redispatch={bool(job.get('redispatch'))} updated={age(job.get('updated_at'))} ago"
            )
            if job.get("error"):
                print(f"      error: {short(job['error'], 180)}")


def render_subagent(subagent: dict[str, Any], session: dict[str, Any]) -> None:
    observability = subagent.get("observability") or {}
    identity = observability.get("identity") or {}
    execution = observability.get("execution") or {}
    usage = observability.get("usage") or {}
    context = observability.get("context") or {}
    handoff = observability.get("handoff") or {}
    model = execution.get("model") or "-"
    effort = execution.get("reasoning_effort") or "-"
    print(
        f"      agent {subagent.get('agent_session_id')}: host={subagent.get('host')} "
        f"liveness={subagent.get('liveness')} terminal={execution.get('terminal_state', '-')} "
        f"model={model}/{effort} updated={age(subagent.get('updated_at'))} ago"
    )
    print(
        "        usage: "
        f"active={duration(execution.get('duration_seconds'))} "
        f"wall={duration(execution.get('wall_duration_seconds'))} "
        f"tokens={tokens(usage.get('total_tokens'))} "
        f"in={tokens(usage.get('input_tokens'))} cached={tokens(usage.get('cached_input_tokens'))} "
        f"out={tokens(usage.get('output_tokens'))} reasoning={tokens(usage.get('reasoning_output_tokens'))} "
        f"cost={money(usage.get('cost_usd'))} tools={usage.get('tool_calls', '-')} "
        f"messages={usage.get('assistant_messages', '-')}"
    )
    percent = context.get("percent")
    percent_text = f"{float(percent):.1f}%" if isinstance(percent, (int, float)) else "-"
    print(
        f"        context: {tokens(context.get('tokens'))}/{tokens(context.get('context_window'))} "
        f"({percent_text}, {context.get('source', '-')}); "
        f"session_verifiers={subagent.get('session_verifier_count', 0)} "
        f"handoff={'yes' if handoff.get('present') else 'no'} "
        f"source={observability.get('source', '-')}"
    )
    if identity.get("task_name") or identity.get("nickname") or identity.get("native_session_id"):
        print(
            f"        identity: task={identity.get('task_name') or '-'} "
            f"nickname={identity.get('nickname') or '-'} "
            f"native={identity.get('native_session_id') or identity.get('external_id') or '-'}"
        )
    directive = session.get("directive") if isinstance(session, dict) else None
    if directive:
        print(f"        directive: {short(directive, 185)}")
    summary, next_steps = handoff_summary(session)
    if summary:
        print(f"        handoff: {short(summary, 185)}")
    if next_steps:
        print(f"        next steps: {short('; '.join(next_steps), 180)}")
    if subagent.get("time_advisory_sent"):
        print(f"        advisory: {short(subagent.get('time_advisory'), 180)}")
    if subagent.get("timed_out") or subagent.get("runner_failed") or subagent.get("error"):
        print(
            f"        failure: timed_out={subagent.get('timed_out')} "
            f"runner_failed={subagent.get('runner_failed')} "
            f"stage={subagent.get('failure_stage') or '-'} error={short(subagent.get('error'), 150)}"
        )
    errors = observability.get("errors") or []
    for error in errors:
        print(f"        observability warning: {short(error, 180)}")
    if subagent.get("session_file", {}).get("path"):
        print(f"        session file: {path_state(subagent.get('session_file'))}")


def render_candidate(
    candidate_id: str,
    summary: dict[str, Any],
    record: dict[str, Any],
    subagents: list[dict[str, Any]],
    sessions: dict[str, dict[str, Any]],
    direction: str,
) -> None:
    best_value = summary.get("best_iteration_score")
    if not isinstance(best_value, (int, float)):
        iterations = record.get("iterations") if isinstance(record.get("iterations"), list) else []
        eligible = [
            iteration
            for iteration in iterations
            if isinstance(iteration, dict)
            and isinstance(iteration.get("score"), (int, float))
            and iteration.get("process_passed") is not False
            and not iteration.get("touched_denied_files")
            and not iteration.get("changed_outside_allowed")
        ]
        if eligible:
            chooser = min if direction == "minimize" else max
            best_value = chooser(eligible, key=lambda item: float(item["score"]))["score"]
    print(
        f"    {candidate_id}: status={summary.get('status')} "
        f"sessions={summary.get('agent_session_count', 0)} verifiers={summary.get('verifier_count', 0)} "
        f"latest={score(summary.get('last_score'))} best={score(best_value)} "
        f"last_verifier={age(summary.get('last_verifier_at'))} ago "
        f"ledger_rows={(summary.get('results_tsv') or {}).get('row_count', 0)}"
    )
    results_tsv = summary.get("results_tsv")
    if isinstance(results_tsv, dict) and results_tsv.get("path"):
        print(f"      ledger: {path_state(results_tsv)}")
    task = record.get("task") if isinstance(record.get("task"), dict) else {}
    proposal = task.get("proposal") if isinstance(task.get("proposal"), dict) else {}
    metadata = proposal.get("metadata") if isinstance(proposal.get("metadata"), dict) else {}
    action = metadata.get("search_action") or "-"
    family = metadata.get("feature_family") or metadata.get("focus") or "-"
    parents = proposal.get("parent_candidate_ids") or task.get("parent_candidate_ids") or []
    print(f"      decision: action={action} family={family} parents={','.join(parents) or '-'}")
    if proposal.get("intent"):
        print(f"      intent: {short(proposal['intent'], 185)}")
    hypothesis = proposal.get("hypothesis") or task.get("hypothesis")
    if hypothesis:
        print(f"      hypothesis: {short(hypothesis, 185)}")
    if proposal.get("expected_tradeoff"):
        print(f"      tradeoff: {short(proposal['expected_tradeoff'], 180)}")
    iterations = record.get("iterations") if isinstance(record.get("iterations"), list) else []
    if iterations:
        latest = iterations[-1]
        print(
            f"      latest iteration: i{latest.get('iteration', '?')} "
            f"score={score(latest.get('score'))} pass={latest.get('process_passed')} "
            f"git={str(latest.get('git_head') or '-')[:10]} "
            f"ledger_git={str(latest.get('ledger_git_head') or '-')[:10]}"
        )
        if latest.get("hypothesis"):
            print(f"        verifier hypothesis: {short(latest['hypothesis'], 175)}")
        if latest.get("failure_class"):
            print(f"        failure class: {latest['failure_class']}")
    changed = summary.get("changed_files") or []
    if changed:
        print(f"      changed: {', '.join(changed)}")
    if summary.get("touched_denied_files") or summary.get("changed_outside_allowed"):
        print(
            f"      EDIT-SURFACE WARNING: denied={summary.get('touched_denied_files')} "
            f"outside_allowed={summary.get('changed_outside_allowed')}"
        )
    for subagent in subagents:
        session_id = str(subagent.get("agent_session_id"))
        render_subagent(subagent, sessions.get(session_id, {}))


def render_verbose_run(item: dict[str, Any]) -> None:
    snapshot = item["snapshot"]
    run = snapshot.get("run") or {}
    run_id = run.get("run_id") or snapshot.get("selected_run_id")
    spec = item["spec"]
    strategy = snapshot.get("strategy") or {}
    main = snapshot.get("main_agent") or {}
    budget = spec.get("budget") if isinstance(spec.get("budget"), dict) else {}
    print()
    print("=" * 96)
    print(
        f"run {run_id}: state={run.get('state', '-')} "
        f"metric={spec.get('metric_name', '-')} direction={spec.get('metric_direction', '-')} "
        f"best={score(run.get('best_score'))} selected={run.get('selected_candidate_id') or '-'}"
    )
    print("=" * 96)
    if spec.get("objective"):
        print(f"  objective: {short(spec['objective'], 190)}")
    history = strategy.get("history_policy") if isinstance(strategy.get("history_policy"), dict) else {}
    history_text = history.get("scope", "-")
    if history.get("scope") == "top_n" and history.get("top_n") is not None:
        history_text += f"(top_n={history['top_n']})"
    print(
        f"  strategy: {strategy.get('name', '-')}/{strategy.get('driver', '-')} "
        f"host={strategy.get('worker_host', '-')} mode={strategy.get('worker_mode', '-')} "
        f"history={history_text}"
    )
    run_age_label = "age" if run.get("state") in {"promoted", "aborted", "failed"} else "elapsed"
    print(
        f"  progress: candidates={run.get('candidates_evaluated', 0)}/{run.get('candidates_total', 0)} "
        f"cap={budget.get('max_candidates', '-')} parallel={budget.get('max_parallel', '-')} "
        f"rounds={run.get('started_rounds_total', 0)}/{run.get('planning_rounds_total', 0)} "
        f"sessions={main.get('subagent_count', 0)} verifiers={main.get('verifier_count', 0)} "
        f"{run_age_label}={duration(main.get('elapsed_seconds'))} cost={money(main.get('estimated_cost_total'))}"
    )
    if run.get("selected_candidate_id"):
        selected_score = run.get("selected_score")
        selected_iteration = run.get("selected_iteration")
        selected_git_head = run.get("selected_git_head")
        selected_record = item["candidate_records"].get(str(run["selected_candidate_id"]), {})
        if not isinstance(selected_score, (int, float)):
            report = selected_record.get("score_report")
            if isinstance(report, dict):
                selected_score = report.get("aggregate_score")
        selected_iterations = (
            selected_record.get("iterations")
            if isinstance(selected_record.get("iterations"), list)
            else []
        )
        if selected_iterations:
            latest_selected = selected_iterations[-1]
            selected_iteration = selected_iteration or latest_selected.get("iteration")
            selected_git_head = selected_git_head or latest_selected.get("git_head")
        print(
            f"  selection: candidate={run.get('selected_candidate_id')} "
            f"score={score(selected_score)} iteration={selected_iteration} "
            f"git={str(selected_git_head or '-')[:10]}"
        )
    artifacts = item["artifacts"]
    print(
        f"  artifacts: report={artifacts.get('report') or '-'} "
        f"promotion={','.join(artifacts.get('promotion_patches') or []) or '-'}"
    )
    render_plans(item["plans"])
    render_pools(item["pi_pools"])
    print("  candidates:")
    subagents_by_candidate: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for subagent in snapshot.get("subagents", []):
        subagents_by_candidate[str(subagent.get("candidate_id"))].append(subagent)
    for candidate_id, summary in sorted((snapshot.get("candidates") or {}).items()):
        render_candidate(
            candidate_id,
            summary,
            item["candidate_records"].get(candidate_id, {}),
            subagents_by_candidate.get(candidate_id, []),
            item["session_records"],
            str(spec.get("metric_direction") or "maximize"),
        )
    warnings = snapshot.get("warnings") or []
    if warnings:
        print("  warnings:")
        for warning in warnings:
            print(f"    - {short(warning, 190)}")


def compact_worker_state(subagent: dict[str, Any]) -> str:
    observability = subagent.get("observability") or {}
    execution = observability.get("execution") or {}
    terminal = execution.get("terminal_state")
    if terminal and terminal != "unknown":
        return str(terminal)
    return str(subagent.get("liveness") or "-")


def render_compact_goal(bundle: dict[str, Any]) -> None:
    primary = bundle["primary"]
    goal = primary.get("goal_plus")
    if not isinstance(goal, dict):
        print("goal: - (showing Search runs directly)")
        return
    aggregate = primary.get("search_task_aggregate") or {}
    print(
        f"goal {goal.get('goal_plus_id')}: status={goal.get('status')} "
        f"phase={goal.get('phase')} revision={goal.get('goal_revision')} | "
        f"tasks={aggregate.get('search_tasks_total', 0)} "
        f"candidates={aggregate.get('candidates_evaluated', 0)}/{aggregate.get('candidates_total', 0)} "
        f"sessions={aggregate.get('worker_sessions_total', 0)} "
        f"verifiers={aggregate.get('verifier_runs_total', 0)}"
    )
    print(f"  objective: {short(goal.get('raw_goal'), 180)}")
    next_action = goal.get("next_action")
    if isinstance(next_action, dict):
        action = next_action.get("kind") or next_action.get("action") or "pending"
        detail = next_action.get("instruction") or next_action.get("reason") or next_action
        print(f"  next: {action} | {short(detail, 165)}")


def render_compact_pools(pools: list[dict[str, Any]]) -> None:
    for item in pools:
        pool = item["pool"]
        jobs = item["jobs"]
        counts = Counter(str(job.get("status", "unknown")) for job in jobs)
        count_text = ",".join(f"{key}={counts[key]}" for key in sorted(counts)) or "no-jobs"
        print(
            f"  pool {pool.get('pool_id')}: state={pool.get('state')} "
            f"parallel={pool.get('max_parallel')} {count_text}"
        )


def render_compact_candidate(
    candidate_id: str,
    summary: dict[str, Any],
    record: dict[str, Any],
    subagents: list[dict[str, Any]],
    direction: str,
) -> None:
    iterations = record.get("iterations") if isinstance(record.get("iterations"), list) else []
    best_value = summary.get("best_iteration_score")
    if not isinstance(best_value, (int, float)):
        eligible = [
            iteration
            for iteration in iterations
            if isinstance(iteration, dict)
            and isinstance(iteration.get("score"), (int, float))
            and iteration.get("process_passed") is not False
            and not iteration.get("touched_denied_files")
            and not iteration.get("changed_outside_allowed")
        ]
        if eligible:
            chooser = min if direction == "minimize" else max
            best_value = chooser(eligible, key=lambda item: float(item["score"]))["score"]

    worker_states = [compact_worker_state(item) for item in subagents]
    print(
        f"  {candidate_id}: worker={','.join(worker_states) or '-'} "
        f"candidate={summary.get('status')} sessions={len(subagents)} "
        f"iterations={summary.get('verifier_count', 0)} "
        f"ledger={(summary.get('results_tsv') or {}).get('row_count', 0)} "
        f"latest={score(summary.get('last_score'))} best={score(best_value)}"
    )

    task = record.get("task") if isinstance(record.get("task"), dict) else {}
    proposal = task.get("proposal") if isinstance(task.get("proposal"), dict) else {}
    metadata = proposal.get("metadata") if isinstance(proposal.get("metadata"), dict) else {}
    action = metadata.get("search_action") or "-"
    family = metadata.get("feature_family") or metadata.get("focus") or "-"
    intent = proposal.get("intent") or proposal.get("hypothesis") or task.get("hypothesis")
    print(f"    decision={action}/{family}: {short(intent, 165)}")

    if iterations:
        latest = iterations[-1]
        print(
            f"    latest i{latest.get('iteration', '?')} "
            f"score={score(latest.get('score'))} pass={latest.get('process_passed')} "
            f"git={str(latest.get('git_head') or '-')[:8]} | "
            f"{short(latest.get('hypothesis'), 105)}"
        )

    if subagents:
        latest_agent = max(
            subagents,
            key=lambda item: parse_time(item.get("updated_at")) or datetime.min.replace(tzinfo=timezone.utc),
        )
        observability = latest_agent.get("observability") or {}
        identity = observability.get("identity") or {}
        execution = observability.get("execution") or {}
        usage = observability.get("usage") or {}
        context = observability.get("context") or {}
        handoff = observability.get("handoff") or {}
        percent = context.get("percent")
        percent_text = f"{float(percent):.1f}%" if isinstance(percent, (int, float)) else "-"
        name = identity.get("nickname") or latest_agent.get("agent_session_id")
        print(
            f"    agent={name} model={execution.get('model') or '-'}"
            f"/{execution.get('reasoning_effort') or '-'} "
            f"active={duration(execution.get('duration_seconds'))} "
            f"tokens={tokens(usage.get('total_tokens'))} ctx={percent_text} "
            f"verifiers={latest_agent.get('session_verifier_count', 0)} "
            f"handoff={'yes' if handoff.get('present') else 'no'}"
        )

    if summary.get("touched_denied_files") or summary.get("changed_outside_allowed"):
        print(
            f"    WARNING edit-surface denied={summary.get('touched_denied_files')} "
            f"outside_allowed={summary.get('changed_outside_allowed')}"
        )


def render_compact_run(item: dict[str, Any]) -> None:
    snapshot = item["snapshot"]
    run = snapshot.get("run") or {}
    spec = item["spec"]
    strategy = snapshot.get("strategy") or {}
    direction = str(spec.get("metric_direction") or "maximize")
    print()
    print(
        f"run {run.get('run_id')}: state={run.get('state')} "
        f"{direction} {spec.get('metric_name', '-')} best={score(run.get('best_score'))} "
        f"selected={run.get('selected_candidate_id') or '-'} | "
        f"strategy={strategy.get('name', '-')}/{strategy.get('worker_host', '-')} "
        f"candidates={run.get('candidates_evaluated', 0)}/{run.get('candidates_total', 0)} "
        f"rounds={run.get('started_rounds_total', 0)}/{run.get('planning_rounds_total', 0)}"
    )
    for plan in item["plans"]:
        trace = plan.get("strategy_trace") if isinstance(plan.get("strategy_trace"), dict) else {}
        print(
            f"  {plan.get('plan_id')}: {plan.get('status')} "
            f"requested={plan.get('requested_k')} planned={plan.get('planned_k')} | "
            f"{short(trace.get('selection_rule'), 120)}"
        )
    render_compact_pools(item["pi_pools"])
    subagents_by_candidate: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for subagent in snapshot.get("subagents", []):
        subagents_by_candidate[str(subagent.get("candidate_id"))].append(subagent)
    for candidate_id, summary in sorted((snapshot.get("candidates") or {}).items()):
        render_compact_candidate(
            candidate_id,
            summary,
            item["candidate_records"].get(candidate_id, {}),
            subagents_by_candidate.get(candidate_id, []),
            direction,
        )
    artifacts = item["artifacts"]
    if artifacts.get("report") or artifacts.get("promotion_patches"):
        report_text = Path(artifacts["report"]).name if artifacts.get("report") else "-"
        promotion_text = ",".join(
            Path(path).name for path in artifacts.get("promotion_patches") or []
        ) or "-"
        print(
            f"  artifacts: report={report_text} promotion={promotion_text}"
        )
    for warning in snapshot.get("warnings") or []:
        print(f"  WARNING: {short(warning, 175)}")


target = Path(os.environ["GP_MONITOR_TARGET"])
explicit_root = os.environ.get("GP_MONITOR_EXPLICIT_ROOT") == "1"
requested_goal = os.environ.get("GP_MONITOR_GOAL_ID", "")
requested_run = os.environ.get("GP_MONITOR_RUN_ID", "")
limit = int(os.environ.get("GP_MONITOR_RUN_LIMIT", "3"))
stale = int(os.environ.get("GP_MONITOR_STALE_AFTER", "600"))
as_json = os.environ.get("GP_MONITOR_JSON") == "1"
verbose = os.environ.get("GP_MONITOR_VERBOSE") == "1"

try:
    root = resolve_runtime_root(target, explicit_root)
    goal_id = select_goal(root, requested_goal)
    bundle = assemble(root, goal_id, requested_run, limit, stale)
except Exception as exc:
    print(f"Goal Plus monitor error: {type(exc).__name__}: {exc}", file=sys.stderr)
    raise SystemExit(1)

if as_json:
    print(json.dumps(bundle, indent=2, ensure_ascii=False, default=str))
    raise SystemExit(0)

print(datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"))
print(f"directory: {target.expanduser().resolve()}")
print(f"runtime:   {root}")
print(f"filter:    goal={goal_id or '-'} run={requested_run or 'latest/linked'} stale_after={stale}s")
print()
if verbose:
    render_verbose_goal(bundle)
else:
    render_compact_goal(bundle)
if not bundle["runs"]:
    print("\nNo frozen Search run yet; the goal may still be in intake or spec discovery.")
for item in bundle["runs"]:
    if verbose:
        render_verbose_run(item)
    else:
        render_compact_run(item)
PY
  status=$?

  if [[ "$once" -eq 1 ]]; then
    exit "$status"
  fi
  sleep "$interval"
done
