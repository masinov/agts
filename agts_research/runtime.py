from __future__ import annotations

from pathlib import Path
import json
import os
import signal
import subprocess
import sys
import time

from agts_research.config import ResearchConfig, load_run_config
from agts_research.models import AgentSpec, WorkerProcessStatus
from agts_research.storage import append_jsonl, read_state, write_state


def launch_workers(
    run_dir: Path,
    *,
    agent_id: str | None = None,
    dry_run: bool = False,
    prompt: str | None = None,
    dry_run_seconds: float = 2.0,
    timeout_seconds: float | None = None,
) -> list[AgentSpec]:
    cfg = load_run_config(run_dir)
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
        command = _launch_command(
            cfg,
            agent,
            dry_run=dry_run,
            prompt=prompt,
            dry_run_seconds=dry_run_seconds,
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
        agent.status = WorkerProcessStatus.RUNNING.value
        agent.log_path = str(log_path)
        agent.started_at = time.time()
        agent.stopped_at = None
        agent.exit_code = None
        agent.launch_command = command
        agent.timeout_seconds = timeout_seconds
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
            continue
        alive = _pid_alive(agent.pid)
        if alive:
            if agent.status == WorkerProcessStatus.STOPPED.value:
                continue
            if _timed_out(agent):
                _terminate_agent_process(agent, force=True)
                agent.status = WorkerProcessStatus.TIMED_OUT.value
                agent.stopped_at = time.time()
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
            _write_agent_process_snapshot(run_dir, agent)
        if agent.log_path:
            agent.session_id = _extract_session_id(Path(agent.log_path)) or agent.session_id
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
            agent.status = WorkerProcessStatus.EXITED.value if agent.pid else agent.status
            stopped.append(agent)
            continue
        sig = signal.SIGKILL if force else signal.SIGINT
        _send_signal(agent.pid, sig)
        agent.status = WorkerProcessStatus.STOPPED.value
        agent.stopped_at = time.time()
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
    agent: AgentSpec,
    *,
    dry_run: bool,
    prompt: str | None = None,
    dry_run_seconds: float = 2.0,
) -> list[str]:
    if dry_run:
        return [
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
            effective_prompt,
        ]
        return command
    raise RuntimeError(f"unsupported research worker runtime: {cfg.agents.runtime}")


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
    # We do not own the Popen object after launch, so exit status is best-effort.
    return agent.exit_code


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
        "session_id": agent.session_id,
        "timeout_seconds": agent.timeout_seconds,
        "launch_command": agent.launch_command,
    }


def _write_agent_process_snapshot(run_dir: Path, agent: AgentSpec) -> None:
    meta_path = Path(run_dir) / "public" / "agents" / f"{agent.agent_id}.process.json"
    meta_path.write_text(json.dumps(_agent_process_snapshot(agent), indent=2) + "\n", encoding="utf-8")


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
