from __future__ import annotations

from pathlib import Path
import json
import os
import re
import signal
import subprocess
import sys
import time

from agts_research.config import ResearchConfig, load_run_config
from agts_research.eval_server import ensure_eval_server
from agts_research.models import AgentSpec, WorkerProcessStatus
from agts_research.storage import append_jsonl, read_state, write_state
from agts_research.workspace import write_worker_files


_LOCAL_PROCESSES: dict[int, subprocess.Popen] = {}


def launch_workers(
    run_dir: Path,
    *,
    agent_id: str | None = None,
    dry_run: bool = False,
    prompt: str | None = None,
    dry_run_seconds: float = 2.0,
    timeout_seconds: float | None = None,
    resume_session: bool = False,
) -> list[AgentSpec]:
    cfg = load_run_config(run_dir)
    if not dry_run:
        ensure_eval_server(run_dir)
    state = read_state(run_dir)
    selected = [
        agent
        for agent in state.agents.values()
        if agent_id is None or agent.agent_id == agent_id
    ]
    if not selected:
        raise RuntimeError(f"no matching agents found in {run_dir}")

    launched: list[AgentSpec] = []
    for agent in selected:
        if agent.status == WorkerProcessStatus.RUNNING.value and _pid_alive(agent.pid):
            launched.append(agent)
            continue
        branch = state.branches.get(agent.branch_id)
        if branch and cfg.agents.max_turns > 0 and branch.cost.agent_turns >= cfg.agents.max_turns:
            append_jsonl(
                Path(run_dir) / "meta_events.jsonl",
                {
                    "timestamp": time.time(),
                    "type": "worker_turn_limit",
                    "agent_id": agent.agent_id,
                    "branch_id": agent.branch_id,
                    "max_turns": cfg.agents.max_turns,
                    "agent_turns": branch.cost.agent_turns,
                },
            )
            continue
        if branch:
            write_worker_files(cfg, Path(run_dir), branch, agent)
        command = _launch_command(
            cfg,
            Path(run_dir),
            agent,
            dry_run=dry_run,
            prompt=prompt,
            dry_run_seconds=dry_run_seconds,
            resume_session=resume_session,
        )
        launch_token = str(int(time.time() * 1000))
        log_path = Path(run_dir) / "public" / "agents" / f"{agent.agent_id}-{launch_token}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = log_path.open("wb")
        process = subprocess.Popen(
            command,
            cwd=agent.worktree_path,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        log_file.close()
        agent.pid = process.pid
        _LOCAL_PROCESSES[process.pid] = process
        agent.status = WorkerProcessStatus.RUNNING.value
        agent.log_path = str(log_path)
        agent.started_at = time.time()
        agent.stopped_at = None
        agent.exit_code = None
        agent.exit_classification = ""
        agent.launch_command = command
        agent.timeout_seconds = timeout_seconds
        agent.accounted_wall_seconds = 0.0
        agent.accounted_tokens = 0
        if branch:
            branch.cost.agent_turns += 1
        _write_agent_process_snapshot(run_dir, agent)
        launched.append(agent)
        append_jsonl(
            Path(run_dir) / "meta_events.jsonl",
            {
                "timestamp": time.time(),
                "type": "worker_launch",
                "agent_id": agent.agent_id,
                "branch_id": agent.branch_id,
                "pid": agent.pid,
                "dry_run": dry_run,
                "resume_session": resume_session,
                "session_id": agent.session_id,
                "command": command,
            },
        )
    write_state(run_dir, state)
    return launched


def refresh_worker_status(run_dir: Path) -> list[AgentSpec]:
    state = read_state(run_dir)
    for agent in state.agents.values():
        if agent.pid is None:
            if agent.status == WorkerProcessStatus.RUNNING.value:
                agent.status = WorkerProcessStatus.UNKNOWN.value
            if agent.log_path:
                agent.session_id = _extract_session_id(Path(agent.log_path)) or agent.session_id
                _account_worker_tokens(state, agent)
                _write_agent_process_snapshot(run_dir, agent)
            continue
        alive = _agent_alive(agent)
        if alive:
            if agent.status == WorkerProcessStatus.STOPPED.value:
                continue
            if _timed_out(agent):
                _terminate_agent_process(agent, force=True)
                agent.exit_code = _reap_local_process(agent.pid, timeout=0.2)
                agent.status = WorkerProcessStatus.TIMED_OUT.value
                agent.exit_classification = "timed_out"
                agent.stopped_at = time.time()
                _account_worker_wall_seconds(state, agent)
                _write_agent_process_snapshot(run_dir, agent)
                append_jsonl(
                    Path(run_dir) / "meta_events.jsonl",
                    {
                        "timestamp": time.time(),
                        "type": "worker_timeout",
                        "agent_id": agent.agent_id,
                        "branch_id": agent.branch_id,
                        "pid": agent.pid,
                        "timeout_seconds": agent.timeout_seconds,
                    },
                )
                continue
            agent.status = WorkerProcessStatus.RUNNING.value
            if agent.log_path:
                agent.session_id = _extract_session_id(Path(agent.log_path)) or agent.session_id
            continue
        if agent.status == WorkerProcessStatus.RUNNING.value:
            agent.status = WorkerProcessStatus.EXITED.value
            agent.stopped_at = time.time()
            agent.exit_code = _read_exit_code(agent)
            agent.exit_classification = _classify_exit(agent.exit_code)
            _account_worker_wall_seconds(state, agent)
            _account_worker_tokens(state, agent)
            _write_agent_process_snapshot(run_dir, agent)
        if agent.log_path:
            agent.session_id = _extract_session_id(Path(agent.log_path)) or agent.session_id
            _account_worker_tokens(state, agent)
            _write_agent_process_snapshot(run_dir, agent)
    write_state(run_dir, state)
    return list(state.agents.values())


def stop_workers(run_dir: Path, *, agent_id: str | None = None, force: bool = False) -> list[AgentSpec]:
    state = read_state(run_dir)
    stopped: list[AgentSpec] = []
    for agent in state.agents.values():
        if agent_id is not None and agent.agent_id != agent_id:
            continue
        if agent.pid is None or not _pid_alive(agent.pid):
            agent.exit_code = _read_exit_code(agent)
            agent.exit_classification = _classify_exit(agent.exit_code) if agent.pid else agent.exit_classification
            agent.status = WorkerProcessStatus.EXITED.value if agent.pid else agent.status
            stopped.append(agent)
            continue
        sig = signal.SIGKILL if force else signal.SIGINT
        _send_signal(agent.pid, sig)
        reaped_exit_code = _reap_local_process(agent.pid, timeout=0.2)
        agent.status = WorkerProcessStatus.STOPPED.value
        agent.stopped_at = time.time()
        agent.exit_code = reaped_exit_code if reaped_exit_code is not None else _read_exit_code(agent)
        agent.exit_classification = "stopped"
        _write_agent_process_snapshot(run_dir, agent)
        stopped.append(agent)
        append_jsonl(
            Path(run_dir) / "meta_events.jsonl",
            {
                "timestamp": time.time(),
                "type": "worker_stop",
                "agent_id": agent.agent_id,
                "branch_id": agent.branch_id,
                "pid": agent.pid,
                "force": force,
            },
        )
    write_state(run_dir, state)
    return stopped


def read_agent_log(run_dir: Path, *, agent_id: str | None = None, lines: int = 80) -> list[tuple[str, str]]:
    state = read_state(run_dir)
    selected = [
        agent
        for agent in state.agents.values()
        if agent_id is None or agent.agent_id == agent_id
    ]
    output: list[tuple[str, str]] = []
    for agent in selected:
        if not agent.log_path:
            output.append((agent.agent_id, ""))
            continue
        path = Path(agent.log_path)
        if not path.exists():
            output.append((agent.agent_id, ""))
            continue
        output.append((agent.agent_id, _tail(path, lines)))
    return output


def _launch_command(
    cfg: ResearchConfig,
    run_dir: Path,
    agent: AgentSpec,
    *,
    dry_run: bool,
    prompt: str | None = None,
    dry_run_seconds: float = 2.0,
    resume_session: bool = False,
) -> list[str]:
    if dry_run:
        command = [
            sys.executable,
            "-u",
            "-c",
            (
                "import time, pathlib; "
                "pathlib.Path('.worker-dry-run.txt').write_text('worker launched\\n'); "
                "print('AGTS research dry-run worker started', flush=True); "
                f"time.sleep({dry_run_seconds!r}); "
                "print('AGTS research dry-run worker finished', flush=True)"
            ),
        ]
        return command
    if cfg.agents.runtime == "claude_code":
        effective_prompt = prompt or (
            "You are running in non-interactive one-shot mode. Do not ask the user for approval. "
            "Read CLAUDE.md and AGTS_RESEARCH.md. Begin work on this AGTS Research branch. "
            "You are authorized to run ./agts-research eval. If no attempts exist, submit a baseline eval first. "
            "Write or update the branch note before exiting."
        )
        command = [
            "claude",
            "--print",
            "--permission-mode",
            "bypassPermissions",
            "--model",
            cfg.agents.model,
        ]
        if resume_session:
            if not agent.session_id:
                raise RuntimeError(f"agent {agent.agent_id} has no captured session_id to resume")
            command.extend(["--resume", agent.session_id])
        command.append(effective_prompt)
        return _sandbox_command(cfg, run_dir, agent, command)
    raise RuntimeError(f"unsupported research worker runtime: {cfg.agents.runtime}")


def _sandbox_command(
    cfg: ResearchConfig,
    run_dir: Path,
    agent: AgentSpec,
    command: list[str],
) -> list[str]:
    if not cfg.agents.sandbox:
        return command
    if cfg.agents.sandbox_backend != "bwrap":
        raise RuntimeError(f"unsupported worker sandbox backend: {cfg.agents.sandbox_backend}")

    hidden_paths = _hidden_paths(cfg, run_dir)
    sandbox = [
        "bwrap",
        "--die-with-parent",
        "--dev-bind",
        "/",
        "/",
        "--setenv",
        "AGTS_WORKER_SANDBOX",
        "1",
    ]
    for path in hidden_paths:
        if path.is_dir():
            sandbox.extend(["--tmpfs", str(path)])
        elif path.exists():
            sandbox.extend(["--ro-bind", "/dev/null", str(path)])
    sandbox.extend(["--chdir", str(Path(agent.worktree_path).resolve())])
    return sandbox + command


def _hidden_paths(cfg: ResearchConfig, run_dir: Path) -> list[Path]:
    paths = [run_dir / "private"]
    for raw in [*cfg.evaluator.private_paths, *cfg.evaluator.holdout_paths]:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        paths.append(path.resolve())
    return paths


def _pid_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    stat_path = Path(f"/proc/{pid}/stat")
    if stat_path.exists():
        try:
            parts = stat_path.read_text(encoding="utf-8").split()
            if len(parts) > 2 and parts[2] == "Z":
                return False
        except OSError:
            pass
    return True


def _agent_alive(agent: AgentSpec) -> bool:
    if agent.pid is None:
        return False
    process = _LOCAL_PROCESSES.get(agent.pid)
    if process is not None:
        returncode = process.poll()
        if returncode is None:
            return True
        agent.exit_code = returncode
        _LOCAL_PROCESSES.pop(agent.pid, None)
        return False
    return _pid_alive(agent.pid)


def _timed_out(agent: AgentSpec) -> bool:
    if agent.timeout_seconds is None or agent.started_at is None:
        return False
    return time.time() - agent.started_at > agent.timeout_seconds


def _terminate_agent_process(agent: AgentSpec, *, force: bool) -> None:
    if agent.pid is None:
        return
    sig = signal.SIGKILL if force else signal.SIGINT
    _send_signal(agent.pid, sig)


def _send_signal(pid: int, sig: signal.Signals) -> None:
    try:
        os.killpg(os.getpgid(pid), sig)
    except (ProcessLookupError, PermissionError):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass


def _read_exit_code(agent: AgentSpec) -> int | None:
    if agent.pid is not None:
        process = _LOCAL_PROCESSES.get(agent.pid)
        if process is not None:
            returncode = process.poll()
            if returncode is not None:
                _LOCAL_PROCESSES.pop(agent.pid, None)
            return returncode
    return agent.exit_code


def _classify_exit(exit_code: int | None) -> str:
    if exit_code is None:
        return "unknown"
    if exit_code == 0:
        return "clean"
    if exit_code < 0:
        return "signaled"
    return "crashed"


def _reap_local_process(pid: int, *, timeout: float) -> int | None:
    process = _LOCAL_PROCESSES.get(pid)
    if process is None:
        return None
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    _LOCAL_PROCESSES.pop(pid, None)
    return returncode


def _agent_process_snapshot(agent: AgentSpec) -> dict[str, object]:
    return {
        "agent_id": agent.agent_id,
        "branch_id": agent.branch_id,
        "pid": agent.pid,
        "status": agent.status,
        "log_path": agent.log_path,
        "started_at": agent.started_at,
        "stopped_at": agent.stopped_at,
        "exit_code": agent.exit_code,
        "exit_classification": agent.exit_classification,
        "session_id": agent.session_id,
        "timeout_seconds": agent.timeout_seconds,
        "accounted_wall_seconds": agent.accounted_wall_seconds,
        "accounted_tokens": agent.accounted_tokens,
        "launch_command": agent.launch_command,
    }


def _write_agent_process_snapshot(run_dir: Path, agent: AgentSpec) -> None:
    meta_path = Path(run_dir) / "public" / "agents" / f"{agent.agent_id}.process.json"
    meta_path.write_text(json.dumps(_agent_process_snapshot(agent), indent=2) + "\n", encoding="utf-8")


def _account_worker_wall_seconds(state, agent: AgentSpec) -> None:
    if agent.started_at is None or agent.stopped_at is None:
        return
    elapsed = max(0.0, agent.stopped_at - agent.started_at)
    delta = max(0.0, elapsed - agent.accounted_wall_seconds)
    if delta <= 0:
        return
    branch = state.branches.get(agent.branch_id)
    if branch:
        branch.cost.wall_seconds += delta
    agent.accounted_wall_seconds += delta


def _account_worker_tokens(state, agent: AgentSpec) -> None:
    if not agent.log_path:
        return
    total = _extract_token_usage(Path(agent.log_path))
    delta = max(0, total - agent.accounted_tokens)
    if delta <= 0:
        return
    branch = state.branches.get(agent.branch_id)
    if branch:
        branch.cost.tokens += delta
    agent.accounted_tokens += delta


def _extract_token_usage(log_path: Path) -> int:
    if not log_path.exists():
        return 0
    try:
        lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return 0
    best_total = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        json_value = _parse_json_line(stripped)
        if isinstance(json_value, dict):
            best_total = max(best_total, _tokens_from_json(json_value))
        best_total = max(best_total, _tokens_from_text(stripped))
    return best_total


def _parse_json_line(line: str) -> object | None:
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _tokens_from_json(value: dict[str, object]) -> int:
    candidates: list[int] = []
    for key in ("tokens_used", "tokens_used_estimate", "total_tokens", "token_count"):
        candidates.append(_int_value(value.get(key)))
    usage = value.get("usage")
    if isinstance(usage, dict):
        candidates.append(_int_value(usage.get("total_tokens")))
        candidates.append(
            _int_value(usage.get("input_tokens"))
            + _int_value(usage.get("output_tokens"))
            + _int_value(usage.get("cache_creation_input_tokens"))
            + _int_value(usage.get("cache_read_input_tokens"))
        )
        candidates.append(
            _int_value(usage.get("prompt_tokens"))
            + _int_value(usage.get("completion_tokens"))
        )
    message = value.get("message")
    if isinstance(message, dict):
        candidates.append(_tokens_from_json(message))
    return max(candidates, default=0)


def _tokens_from_text(line: str) -> int:
    patterns = [
        r"\btokens_used(?:_estimate)?\s*[=:]\s*(\d+)\b",
        r"\btotal_tokens\s*[=:]\s*(\d+)\b",
    ]
    values = []
    for pattern in patterns:
        for match in re.finditer(pattern, line):
            values.append(int(match.group(1)))
    return max(values, default=0)


def _int_value(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def _extract_session_id(log_path: Path) -> str | None:
    if not log_path.exists():
        return None
    try:
        lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            session_id = data.get("session_id")
            if isinstance(session_id, str) and session_id:
                return session_id
    return None


def _tail(path: Path, lines: int) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])
