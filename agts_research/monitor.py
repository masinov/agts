from __future__ import annotations

from pathlib import Path
import time

from agts_research.config import load_run_config
from agts_research.heartbeat import heartbeat_action_record, heartbeat_prompt
from agts_research.meta import run_meta_step
from agts_research.models import BranchStatus, MetaActionType, WorkerProcessStatus
from agts_research.runtime import launch_workers, refresh_worker_status
from agts_research.storage import append_jsonl, read_state


def monitor_run(
    run_dir: Path,
    *,
    iterations: int = 10,
    interval: float = 5.0,
    dry_run: bool = False,
    dry_run_seconds: float = 2.0,
    worker_timeout: float | None = None,
    stop_on_exit: bool = False,
    verbose: bool = False,
) -> None:
    cfg = load_run_config(run_dir)
    for index in range(iterations):
        agents = refresh_worker_status(run_dir)
        state = read_state(run_dir)
        running_branch_ids = {
            agent.branch_id
            for agent in agents
            if agent.status == WorkerProcessStatus.RUNNING.value
        }
        running_worker_count = sum(1 for agent in agents if agent.status == WorkerProcessStatus.RUNNING.value)
        worker_slots = max(0, cfg.search.max_active_branches - running_worker_count)
        private_dev_evals = sum(
            1
            for attempt in state.attempts.values()
            if attempt.metadata.get("eval_split", "private_dev") == "private_dev"
        )
        if state.finalized_branch_id is not None or private_dev_evals >= cfg.search.max_evals:
            if verbose:
                print(
                    f"monitor_stop iteration={index} attempts={len(state.attempts)} "
                    "reason=finalized_or_eval_budget_exhausted",
                    flush=True,
                )
            append_jsonl(
                run_dir / "meta_events.jsonl",
                {
                    "timestamp": time.time(),
                    "type": "monitor_stop",
                    "iteration": index,
                    "reason": "finalized or eval budget exhausted",
                },
            )
            return

        action = run_meta_step(cfg, run_dir)
        state = read_state(run_dir)
        launched = []
        if action.type == MetaActionType.CONTINUE:
            branch = state.branches.get(action.branch_id)
            if (
                worker_slots > 0
                and branch
                and branch.status == BranchStatus.ACTIVE
                and branch.branch_id not in running_branch_ids
            ):
                for agent_id in branch.assigned_agents[:1]:
                    agent = state.agents.get(agent_id)
                    if agent is None:
                        continue
                    prompt = heartbeat_prompt(cfg, branch, reason=action.reason)
                    heartbeat_action_record(
                        run_dir,
                        cfg=cfg,
                        iteration=index,
                        action=action,
                        branch=branch,
                        agent=agent,
                        prompt=prompt,
                    )
                    new_agents = launch_workers(
                        run_dir,
                        agent_id=agent_id,
                        dry_run=dry_run,
                        prompt=prompt,
                        dry_run_seconds=dry_run_seconds,
                        timeout_seconds=worker_timeout,
                    )
                    launched.extend(new_agents)
                    worker_slots = max(0, worker_slots - len(new_agents))
        elif action.type == MetaActionType.SPLIT:
            for branch in state.branches.values():
                if worker_slots <= 0:
                    break
                if branch.parent_id == action.branch_id and branch.branch_id not in running_branch_ids:
                    for agent_id in branch.assigned_agents[:1]:
                        agent = state.agents.get(agent_id)
                        if agent is None:
                            continue
                        prompt = heartbeat_prompt(cfg, branch, reason=action.reason)
                        heartbeat_action_record(
                            run_dir,
                            cfg=cfg,
                            iteration=index,
                            action=action,
                            branch=branch,
                            agent=agent,
                            prompt=prompt,
                        )
                        new_agents = launch_workers(
                            run_dir,
                            agent_id=agent_id,
                            dry_run=dry_run,
                            prompt=prompt,
                            dry_run_seconds=dry_run_seconds,
                            timeout_seconds=worker_timeout,
                        )
                        launched.extend(new_agents)
                        worker_slots = max(0, worker_slots - len(new_agents))
        elif action.type == MetaActionType.VERIFY:
            from agts_research.verifier import verify_branch

            verify_branch(run_dir, branch_id=action.branch_id, message=action.reason)

        tick = {
            "timestamp": time.time(),
            "type": "monitor_tick",
            "iteration": index,
            "action": action.type.value,
            "branch_id": action.branch_id,
            "launched_agents": [agent.agent_id for agent in launched],
            "running_branches": sorted(running_branch_ids),
            "running_workers": running_worker_count,
            "worker_slots": worker_slots,
        }
        if verbose:
            print(
                f"monitor_tick iteration={index} action={action.type.value} "
                f"branch={action.branch_id} launched={len(launched)} "
                f"running={running_worker_count} attempts={len(state.attempts)}",
                flush=True,
            )
        append_jsonl(
            run_dir / "meta_events.jsonl",
            tick,
        )
        if index < iterations - 1:
            time.sleep(interval)
    if stop_on_exit:
        from agts_research.runtime import stop_workers

        stop_workers(run_dir, force=True)
