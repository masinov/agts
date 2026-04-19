from __future__ import annotations

from pathlib import Path
import argparse

from agts.jsonutil import dumps
from agts_research.config import ResearchConfig, load_run_config
from agts_research.evaluator import submit_eval
from agts_research.heartbeat import heartbeat_prompt
from agts_research.meta import run_meta_step, summarize_branch
from agts_research.monitor import monitor_run
from agts_research.runtime import launch_workers, read_agent_log, refresh_worker_status, stop_workers
from agts_research.storage import branch_snapshots, read_state, write_state
from agts_research.workspace import find_run_dir_from_worktree, start_research_run


def add_research_subparser(subparsers: argparse._SubParsersAction) -> None:
    research = subparsers.add_parser("research", help="Run Research Meta-AGTS commands.")
    research_sub = research.add_subparsers(dest="research_command", required=True)

    start = research_sub.add_parser("start", help="Create a research run.")
    start.add_argument("-c", "--config", required=True, type=Path)

    status = research_sub.add_parser("status", help="Show research run status.")
    status.add_argument("run_dir", type=Path)

    step = research_sub.add_parser("step", help="Run one meta-controller step.")
    step.add_argument("run_dir", type=Path)

    advance = research_sub.add_parser("advance", help="Run one meta step and launch selected worker(s).")
    advance.add_argument("run_dir", type=Path)
    advance.add_argument("--dry-run", action="store_true")
    advance.add_argument("--prompt", default=None)
    advance.add_argument("--dry-run-seconds", type=float, default=2.0)
    advance.add_argument("--timeout", type=float, default=None)

    monitor = research_sub.add_parser("monitor", help="Run a bounded research monitor loop.")
    monitor.add_argument("run_dir", type=Path)
    monitor.add_argument("--iterations", type=int, default=10)
    monitor.add_argument("--interval", type=float, default=5.0)
    monitor.add_argument("--dry-run", action="store_true")
    monitor.add_argument("--dry-run-seconds", type=float, default=2.0)
    monitor.add_argument("--worker-timeout", type=float, default=None)
    monitor.add_argument("--stop-on-exit", action="store_true")
    monitor.add_argument("--quiet", action="store_true")

    eval_cmd = research_sub.add_parser("eval", help="Submit an evaluated attempt from a branch worktree.")
    eval_cmd.add_argument("-m", "--message", required=True)
    eval_cmd.add_argument("--config", type=Path, default=None)
    eval_cmd.add_argument("--workdir", type=Path, default=Path("."))

    summarize = research_sub.add_parser("summarize", help="Refresh branch summaries for a run.")
    summarize.add_argument("run_dir", type=Path)

    launch = research_sub.add_parser("launch", help="Launch research worker subprocesses.")
    launch.add_argument("run_dir", type=Path)
    launch.add_argument("--agent-id", default=None)
    launch.add_argument("--dry-run", action="store_true", help="Launch a harmless sleeping worker for testing.")
    launch.add_argument("--prompt", default=None, help="Override the worker launch prompt.")
    launch.add_argument("--timeout", type=float, default=None, help="Kill worker if still running after this many seconds.")

    workers = research_sub.add_parser("workers", help="Show research worker process status.")
    workers.add_argument("run_dir", type=Path)

    stop = research_sub.add_parser("stop", help="Stop research worker subprocesses.")
    stop.add_argument("run_dir", type=Path)
    stop.add_argument("--agent-id", default=None)
    stop.add_argument("--force", action="store_true")

    logs = research_sub.add_parser("logs", help="Print worker logs.")
    logs.add_argument("run_dir", type=Path)
    logs.add_argument("--agent-id", default=None)
    logs.add_argument("-n", "--lines", type=int, default=80)


def handle_research(args: argparse.Namespace) -> int:
    command = args.research_command
    if command == "start":
        return _start(args)
    if command == "status":
        return _status(args)
    if command == "step":
        return _step(args)
    if command == "advance":
        return _advance(args)
    if command == "monitor":
        return _monitor(args)
    if command == "eval":
        return _eval(args)
    if command == "summarize":
        return _summarize(args)
    if command == "launch":
        return _launch(args)
    if command == "workers":
        return _workers(args)
    if command == "stop":
        return _stop(args)
    if command == "logs":
        return _logs(args)
    raise AssertionError(f"unhandled research command: {command}")


def _start(args: argparse.Namespace) -> int:
    cfg = ResearchConfig.from_file(args.config)
    state = start_research_run(cfg)
    print(f"run_dir={state.run_dir}")
    print(f"root_branch={next(iter(state.branches))}")
    print(f"worktree={next(iter(state.branches.values())).worktree_path}")
    return 0


def _status(args: argparse.Namespace) -> int:
    state = read_state(args.run_dir)
    print(f"run_id={state.run_id}")
    print(f"task={state.task_name}")
    print(f"branches={len(state.branches)} attempts={len(state.attempts)} agents={len(state.agents)}")
    for branch in state.branches.values():
        best = state.attempts.get(branch.best_attempt_id or "")
        best_score = best.score if best else None
        print(
            f"- {branch.branch_id} status={branch.status.value} "
            f"evals={branch.eval_count} best={best_score} value={branch.value_estimate:.2f}"
        )
    return 0


def _step(args: argparse.Namespace) -> int:
    cfg = load_run_config(args.run_dir)
    action = run_meta_step(cfg, args.run_dir)
    print(dumps(action))
    return 0


def _advance(args: argparse.Namespace) -> int:
    cfg = load_run_config(args.run_dir)
    action = run_meta_step(cfg, args.run_dir)
    print(dumps(action))
    state = read_state(args.run_dir)
    agent_ids: list[str] = []
    if action.type.value == "continue":
        branch = state.branches.get(action.branch_id)
        if branch:
            agent_ids.extend(branch.assigned_agents[:1])
    elif action.type.value == "split":
        for branch in state.branches.values():
            if branch.parent_id == action.branch_id and branch.assigned_agents:
                agent_ids.append(branch.assigned_agents[0])

    for agent_id in agent_ids:
        branch = state.branches[state.agents[agent_id].branch_id]
        prompt = args.prompt or heartbeat_prompt(cfg, branch, reason=action.reason)
        agents = launch_workers(
            args.run_dir,
            agent_id=agent_id,
            dry_run=args.dry_run,
            prompt=prompt,
            dry_run_seconds=args.dry_run_seconds,
            timeout_seconds=args.timeout,
        )
        for agent in agents:
            print(f"launched {agent.agent_id} pid={agent.pid} log={agent.log_path}")
    if not agent_ids:
        print("no worker launched for this action")
    return 0


def _monitor(args: argparse.Namespace) -> int:
    monitor_run(
        args.run_dir,
        iterations=args.iterations,
        interval=args.interval,
        dry_run=args.dry_run,
        dry_run_seconds=args.dry_run_seconds,
        worker_timeout=args.worker_timeout,
        stop_on_exit=args.stop_on_exit,
        verbose=not args.quiet,
    )
    print(f"monitor_complete run_dir={args.run_dir}")
    return 0


def _eval(args: argparse.Namespace) -> int:
    workdir = args.workdir.resolve()
    run_dir = find_run_dir_from_worktree(workdir)
    cfg = ResearchConfig.from_file(args.config) if args.config else load_run_config(run_dir)
    attempt = submit_eval(cfg=cfg, message=args.message, workdir=workdir)
    print(f"attempt_id={attempt.attempt_id}")
    print(f"status={attempt.status.value}")
    print(f"score={attempt.score}")
    if attempt.eval_log_path:
        print(f"eval_log={attempt.eval_log_path}")
    return 0


def _summarize(args: argparse.Namespace) -> int:
    cfg = load_run_config(args.run_dir)
    state = read_state(args.run_dir)
    for branch in state.branches.values():
        summarize_branch(cfg, branch, state.attempts)
    write_state(args.run_dir, state)
    print(dumps(branch_snapshots(state)))
    return 0


def _launch(args: argparse.Namespace) -> int:
    agents = launch_workers(
        args.run_dir,
        agent_id=args.agent_id,
        dry_run=args.dry_run,
        prompt=args.prompt,
        timeout_seconds=args.timeout,
    )
    for agent in agents:
        print(
            f"{agent.agent_id} status={agent.status} pid={agent.pid} "
            f"branch={agent.branch_id} log={agent.log_path}"
        )
    return 0


def _logs(args: argparse.Namespace) -> int:
    logs = read_agent_log(args.run_dir, agent_id=args.agent_id, lines=args.lines)
    for agent_id, text in logs:
        print(f"==> {agent_id} <==")
        if text:
            print(text)
    return 0


def _workers(args: argparse.Namespace) -> int:
    agents = refresh_worker_status(args.run_dir)
    for agent in agents:
        print(
            f"{agent.agent_id} status={agent.status} pid={agent.pid} "
            f"branch={agent.branch_id} worktree={agent.worktree_path} log={agent.log_path}"
        )
    return 0


def _stop(args: argparse.Namespace) -> int:
    agents = stop_workers(args.run_dir, agent_id=args.agent_id, force=args.force)
    for agent in agents:
        print(f"{agent.agent_id} status={agent.status} pid={agent.pid}")
    return 0
