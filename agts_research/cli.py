from __future__ import annotations

from pathlib import Path
import argparse
import os
import subprocess
import sys
import time

from agts.jsonutil import dumps
from agts_research.config import ResearchConfig, load_run_config
from agts_research.distill import distill_run
from agts_research.evaluator import submit_eval
from agts_research.eval_server import run_eval_server
from agts_research.heartbeat import heartbeat_action_record, heartbeat_prompt
from agts_research.meta import run_meta_step, summarize_branch
from agts_research.monitor import monitor_run
from agts_research.provenance import cleanup_shared_memory, refresh_provenance_index, validate_shared_memory
from agts_research.report import build_report, format_report
from agts_research.review import review_branch
from agts_research.runtime import launch_workers, read_agent_log, refresh_worker_status, stop_workers
from agts_research.storage import branch_snapshots, read_state, write_json_atomic, write_state
from agts_research.verifier import verification_approved, verify_branch
from agts_research.workspace import find_run_dir_from_worktree, start_research_run


def add_research_subparser(subparsers: argparse._SubParsersAction) -> None:
    research = subparsers.add_parser("research", help="Run Research Meta-AGTS commands.")
    research_sub = research.add_subparsers(dest="research_command", required=True)

    start = research_sub.add_parser("start", help="Create a research run.")
    start.add_argument("-c", "--config", required=True, type=Path)

    run = research_sub.add_parser("run", help="Create a run and start the research supervisor.")
    run.add_argument("-c", "--config", required=True, type=Path)
    run.add_argument("--iterations", type=int, default=100000)
    run.add_argument("--interval", type=float, default=10.0)
    run.add_argument("--worker-timeout", type=float, default=None)
    run.add_argument("--foreground", action="store_true", help="Run the supervisor in this terminal.")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--dry-run-seconds", type=float, default=2.0)
    run.add_argument("--quiet", action="store_true")

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
    eval_cmd.add_argument("--local", action="store_true", help=argparse.SUPPRESS)

    final_eval = research_sub.add_parser("final-eval", help="Run one supervisor-only final holdout evaluation.")
    final_eval.add_argument("run_dir", type=Path)
    final_eval.add_argument("--branch-id", default=None)
    final_eval.add_argument("-m", "--message", default="final holdout evaluation")
    final_eval.add_argument("--force", action="store_true", help="Bypass verifier approval requirement.")

    verify = research_sub.add_parser("verify", help="Run a supervisor verifier review for a branch.")
    verify.add_argument("run_dir", type=Path)
    verify.add_argument("--branch-id", default=None)
    verify.add_argument("-m", "--message", default="")
    verify.add_argument("--json", action="store_true")

    review = research_sub.add_parser("review", help="Run a supervisor critic review for a branch.")
    review.add_argument("run_dir", type=Path)
    review.add_argument("--branch-id", default=None)
    review.add_argument("--json", action="store_true")

    eval_server = research_sub.add_parser("eval-server", help=argparse.SUPPRESS)
    eval_server.add_argument("run_dir", type=Path)
    eval_server.add_argument("--idle-timeout", type=float, default=3600.0)

    summarize = research_sub.add_parser("summarize", help="Refresh branch summaries for a run.")
    summarize.add_argument("run_dir", type=Path)

    distill = research_sub.add_parser("distill", help="Consolidate cross-branch findings into shared memory.")
    distill.add_argument("run_dir", type=Path)
    distill.add_argument("--json", action="store_true")

    provenance = research_sub.add_parser("provenance", help="Refresh and print shared-memory provenance index.")
    provenance.add_argument("run_dir", type=Path)
    provenance.add_argument("--json", action="store_true")

    validate_memory = research_sub.add_parser("validate-memory", help="Validate shared notes, skills, and evidence.")
    validate_memory.add_argument("run_dir", type=Path)
    validate_memory.add_argument("--json", action="store_true")

    clean_memory = research_sub.add_parser("clean-memory", help="Report or quarantine invalid shared-memory files.")
    clean_memory.add_argument("run_dir", type=Path)
    clean_memory.add_argument("--apply", action="store_true", help="Move invalid files into evidence/quarantine.")
    clean_memory.add_argument("--json", action="store_true")

    report = research_sub.add_parser("report", help="Print a research benchmark report.")
    report.add_argument("run_dir", type=Path)
    report.add_argument("--json", action="store_true")

    launch = research_sub.add_parser("launch", help="Launch research worker subprocesses.")
    launch.add_argument("run_dir", type=Path)
    launch.add_argument("--agent-id", default=None)
    launch.add_argument("--dry-run", action="store_true", help="Launch a harmless sleeping worker for testing.")
    launch.add_argument("--prompt", default=None, help="Override the worker launch prompt.")
    launch.add_argument("--timeout", type=float, default=None, help="Kill worker if still running after this many seconds.")

    resume = research_sub.add_parser("resume", help="Resume a captured worker session.")
    resume.add_argument("run_dir", type=Path)
    resume.add_argument("--agent-id", required=True)
    resume.add_argument("--prompt", default=None, help="Prompt to send into the resumed session.")
    resume.add_argument("--timeout", type=float, default=None)

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
    if command == "run":
        return _run(args)
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
    if command == "final-eval":
        return _final_eval(args)
    if command == "verify":
        return _verify(args)
    if command == "review":
        return _review(args)
    if command == "eval-server":
        return _eval_server(args)
    if command == "summarize":
        return _summarize(args)
    if command == "distill":
        return _distill(args)
    if command == "provenance":
        return _provenance(args)
    if command == "validate-memory":
        return _validate_memory(args)
    if command == "clean-memory":
        return _clean_memory(args)
    if command == "report":
        return _report(args)
    if command == "launch":
        return _launch(args)
    if command == "resume":
        return _resume(args)
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


def _run(args: argparse.Namespace) -> int:
    cfg = ResearchConfig.from_file(args.config)
    state = start_research_run(cfg)
    run_dir = Path(state.run_dir).resolve()
    root_branch = next(iter(state.branches))
    worktree = next(iter(state.branches.values())).worktree_path
    print(f"run_dir={run_dir}")
    print(f"root_branch={root_branch}")
    print(f"worktree={worktree}")

    if args.foreground:
        monitor_run(
            run_dir,
            iterations=args.iterations,
            interval=args.interval,
            dry_run=args.dry_run,
            dry_run_seconds=args.dry_run_seconds,
            worker_timeout=args.worker_timeout,
            verbose=not args.quiet,
        )
        print(f"monitor_complete run_dir={run_dir}")
        return 0

    log_path = run_dir / "public" / "supervisor.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = _detached_monitor_command(args, run_dir)
    log_file = log_path.open("ab")
    try:
        process = subprocess.Popen(
            command,
            cwd=Path.cwd(),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        log_file.close()
    write_json_atomic(
        run_dir / "public" / "supervisor.process.json",
        {
            "pid": process.pid,
            "started_at": time.time(),
            "log": str(log_path),
            "command": command,
            "run_dir": str(run_dir),
        },
    )
    print(f"monitor_pid={process.pid}")
    print(f"monitor_log={log_path}")
    print(f"supervisor_process={run_dir / 'public' / 'supervisor.process.json'}")
    return 0


def _detached_monitor_command(args: argparse.Namespace, run_dir: Path) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "agts.cli",
        "research",
        "monitor",
        str(run_dir),
        "--iterations",
        str(args.iterations),
        "--interval",
        str(args.interval),
    ]
    if args.worker_timeout is not None:
        command.extend(["--worker-timeout", str(args.worker_timeout)])
    if args.dry_run:
        command.append("--dry-run")
        command.extend(["--dry-run-seconds", str(args.dry_run_seconds)])
    if args.quiet:
        command.append("--quiet")
    return command


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
            f"evals={branch.eval_count} turns={branch.cost.agent_turns} "
            f"wall={branch.cost.wall_seconds:.1f}s best={best_score} "
            f"value={branch.value_estimate:.2f} voi={(branch.summary.value_of_information if branch.summary else 0.0):.2f}"
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
        heartbeat_action_record(
            args.run_dir,
            cfg=cfg,
            iteration=-1,
            action=action,
            branch=branch,
            agent=state.agents[agent_id],
            prompt=prompt,
        )
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
    attempt = submit_eval(cfg=cfg, message=args.message, workdir=workdir, use_server=not args.local)
    print(f"attempt_id={attempt.attempt_id}")
    print(f"status={attempt.status.value}")
    print(f"score={attempt.score}")
    if attempt.eval_log_path:
        print(f"eval_log={attempt.eval_log_path}")
    return 0


def _final_eval(args: argparse.Namespace) -> int:
    if os.environ.get("AGTS_WORKER_SANDBOX") == "1":
        raise RuntimeError("final-eval is supervisor-only and cannot run inside a worker sandbox")
    cfg = load_run_config(args.run_dir)
    state = read_state(args.run_dir)
    if args.branch_id:
        branch = state.branches[args.branch_id]
    else:
        scored_attempts = [
            attempt
            for attempt in state.attempts.values()
            if attempt.score is not None and attempt.metadata.get("eval_split") != "final_holdout"
        ]
        if cfg.evaluator.direction == "minimize":
            best_attempt = min(scored_attempts, key=lambda attempt: attempt.score, default=None)
        else:
            best_attempt = max(scored_attempts, key=lambda attempt: attempt.score, default=None)
        branch = state.branches[best_attempt.branch_id] if best_attempt else next(iter(state.branches.values()))
    if not branch.worktree_path:
        raise RuntimeError(f"branch {branch.branch_id} has no worktree")
    if cfg.search.verify_before_finalize and not args.force:
        if not verification_approved(args.run_dir, branch.branch_id):
            raise RuntimeError(
                f"branch {branch.branch_id} needs an approved verifier review before final-eval; "
                "run `python -m agts.cli research verify <run_dir> --branch-id "
                f"{branch.branch_id}` or pass --force"
            )
    attempt = submit_eval(
        cfg=cfg,
        message=args.message,
        workdir=Path(branch.worktree_path),
        final=True,
        use_server=False,
    )
    print(f"attempt_id={attempt.attempt_id}")
    print(f"status={attempt.status.value}")
    print(f"score={attempt.score}")
    print("eval_split=final_holdout")
    if attempt.eval_log_path:
        print(f"eval_log={attempt.eval_log_path}")
    return 0


def _verify(args: argparse.Namespace) -> int:
    artifact = verify_branch(args.run_dir, branch_id=args.branch_id, message=args.message)
    if args.json:
        print(dumps(artifact))
    else:
        print(f"branch_id={artifact['branch_id']}")
        print(f"approved={str(artifact['approved']).lower()}")
        print(f"best_private_dev_attempt_id={artifact['best_private_dev_attempt_id']}")
        print(f"best_private_dev_score={artifact['best_private_dev_score']}")
        for check in artifact["checks"]:
            status = "ok" if check["ok"] else "fail"
            print(f"{status} {check['name']}: {check['detail']}")
    return 0


def _review(args: argparse.Namespace) -> int:
    artifact = review_branch(args.run_dir, branch_id=args.branch_id)
    if args.json:
        print(dumps(artifact))
    else:
        print(f"branch_id={artifact['branch_id']}")
        for item in artifact["risks"]:
            print(f"risk: {item}")
        for item in artifact["recommendations"]:
            print(f"recommendation: {item}")
    return 0


def _eval_server(args: argparse.Namespace) -> int:
    run_eval_server(args.run_dir, idle_timeout=args.idle_timeout)
    return 0


def _summarize(args: argparse.Namespace) -> int:
    cfg = load_run_config(args.run_dir)
    state = read_state(args.run_dir)
    for branch in state.branches.values():
        summarize_branch(cfg, branch, state.attempts)
    write_state(args.run_dir, state)
    print(dumps(branch_snapshots(state)))
    return 0


def _distill(args: argparse.Namespace) -> int:
    artifact = distill_run(args.run_dir)
    if args.json:
        print(dumps(artifact))
    else:
        print(f"distilled={args.run_dir / 'public' / 'summaries' / 'distilled_findings.md'}")
        print(f"branches={artifact['branch_count']} attempts={artifact['attempt_count']}")
        print(f"reusable_findings={len(artifact['reusable_findings'])}")
        print(f"failed_approaches={len(artifact['failed_approaches'])}")
    return 0


def _provenance(args: argparse.Namespace) -> int:
    artifact = refresh_provenance_index(args.run_dir)
    if args.json:
        print(dumps(artifact))
    else:
        print(f"records={artifact['record_count']}")
        for kind, count in sorted(artifact["counts_by_kind"].items()):
            print(f"{kind}={count}")
        print(f"index={args.run_dir / 'public' / 'evidence' / 'provenance_index.json'}")
    return 0


def _validate_memory(args: argparse.Namespace) -> int:
    artifact = validate_shared_memory(args.run_dir)
    if args.json:
        print(dumps(artifact))
    else:
        print(f"ok={str(artifact['ok']).lower()}")
        for group in ("notes", "skills", "evidence"):
            failed = [item for item in artifact[group] if not item["ok"]]
            print(f"{group}={len(artifact[group])} failed={len(failed)}")
            for item in failed[:10]:
                print(f"fail {group}: {item['path']} missing={','.join(item['missing'])}")
        print(f"validation={args.run_dir / 'public' / 'summaries' / 'memory_validation.json'}")
    return 0


def _clean_memory(args: argparse.Namespace) -> int:
    artifact = cleanup_shared_memory(args.run_dir, apply=args.apply)
    if args.json:
        print(dumps(artifact))
    else:
        print(f"applied={str(artifact['applied']).lower()}")
        print(f"candidate_count={artifact['candidate_count']}")
        print(f"quarantined_count={artifact['quarantined_count']}")
        for item in artifact["items"][:10]:
            print(f"candidate: {item['path']} missing={','.join(item['missing'])}")
        print(f"cleanup={args.run_dir / 'public' / 'summaries' / 'memory_cleanup.json'}")
    return 0


def _report(args: argparse.Namespace) -> int:
    report = build_report(args.run_dir)
    if args.json:
        print(dumps(report))
    else:
        print(format_report(report), end="")
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


def _resume(args: argparse.Namespace) -> int:
    refresh_worker_status(args.run_dir)
    state = read_state(args.run_dir)
    agent = state.agents.get(args.agent_id)
    if agent is None:
        raise RuntimeError(f"no agent {args.agent_id} in {args.run_dir}")
    if not agent.session_id:
        raise RuntimeError(f"agent {args.agent_id} has no captured session_id")
    prompt = args.prompt or (
        "Resume this AGTS Research branch. Read CLAUDE.md, AGTS_RESEARCH.md, recent attempts, "
        "eval logs, and branch notes. Continue one bounded branch-local step and update the branch note."
    )
    agents = launch_workers(
        args.run_dir,
        agent_id=args.agent_id,
        prompt=prompt,
        timeout_seconds=args.timeout,
        resume_session=True,
    )
    for launched in agents:
        print(
            f"resumed {launched.agent_id} session={launched.session_id} "
            f"pid={launched.pid} log={launched.log_path}"
        )
    if not agents:
        print(f"no worker resumed for {args.agent_id}")
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
            f"branch={agent.branch_id} session={agent.session_id} "
            f"exit={agent.exit_classification or 'unknown'} tokens={agent.accounted_tokens} "
            f"worktree={agent.worktree_path} log={agent.log_path}"
        )
    return 0


def _stop(args: argparse.Namespace) -> int:
    agents = stop_workers(args.run_dir, agent_id=args.agent_id, force=args.force)
    for agent in agents:
        print(f"{agent.agent_id} status={agent.status} pid={agent.pid}")
    return 0
