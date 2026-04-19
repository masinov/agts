from __future__ import annotations

from pathlib import Path
import subprocess
import time

from agts_research.config import ResearchConfig
from agts_research.models import AttemptStatus, ResearchAttempt, new_id
from agts_research.storage import append_jsonl, branch_snapshots, read_state, write_state
from agts_research.workspace import find_run_dir_from_worktree, read_worktree_identity


def submit_eval(
    *,
    cfg: ResearchConfig,
    message: str,
    workdir: Path,
) -> ResearchAttempt:
    run_dir = find_run_dir_from_worktree(workdir)
    state = read_state(run_dir)
    branch_id, agent_id = read_worktree_identity(workdir)
    branch = state.branches[branch_id]

    changed_files = _changed_files(workdir)
    commit_hash = _commit_if_possible(workdir, message)
    score, feedback, eval_log_path, timed_out = _run_evaluator(cfg, run_dir, workdir)
    previous_best = _best_score(state, branch.best_attempt_id)
    status = _status_for_score(cfg, score, previous_best, timed_out)

    attempt = ResearchAttempt(
        attempt_id=new_id("ra"),
        branch_id=branch_id,
        agent_id=agent_id,
        title=message,
        score=score,
        status=status,
        timestamp=time.time(),
        commit_hash=commit_hash,
        parent_attempt_id=branch.best_attempt_id,
        feedback=feedback,
        changed_files=changed_files,
        eval_log_path=str(eval_log_path) if eval_log_path else None,
    )
    state.attempts[attempt.attempt_id] = attempt
    branch.attempt_ids.append(attempt.attempt_id)
    branch.eval_count += 1
    branch.cost.evals += 1
    if _is_better(cfg.evaluator.direction, score, previous_best):
        branch.best_attempt_id = attempt.attempt_id
        branch.evals_since_improvement = 0
    else:
        branch.evals_since_improvement += 1
    write_state(run_dir, state)
    append_jsonl(
        run_dir / "meta_events.jsonl",
        {
            "timestamp": time.time(),
            "type": "attempt",
            "attempt_id": attempt.attempt_id,
            "branch_id": branch_id,
            "agent_id": agent_id,
            "score": score,
            "status": status.value,
            "message": message,
            "branches": branch_snapshots(state),
        },
    )
    return attempt


def _run_evaluator(
    cfg: ResearchConfig,
    run_dir: Path,
    workdir: Path,
) -> tuple[float | None, str, Path | None, bool]:
    if cfg.evaluator.type == "none" or not cfg.evaluator.command:
        return None, "No evaluator configured.", None, False

    eval_id = new_id("eval")
    log_path = run_dir / "public" / "eval_logs" / f"{eval_id}.log"
    try:
        result = subprocess.run(
            cfg.evaluator.command,
            shell=True,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=cfg.evaluator.timeout,
        )
        output = "\n".join(
            [
                f"$ {cfg.evaluator.command}",
                f"returncode={result.returncode}",
                "--- stdout ---",
                result.stdout,
                "--- stderr ---",
                result.stderr,
            ]
        )
        log_path.write_text(output, encoding="utf-8")
        score = _extract_score(result.stdout)
        if score is None and result.returncode == 0:
            score = 1.0
        feedback = result.stderr.strip() or result.stdout.strip()[-1000:] or "Evaluator completed."
        return score, feedback, log_path, False
    except subprocess.TimeoutExpired as exc:
        log_path.write_text(
            f"$ {cfg.evaluator.command}\nTIMEOUT after {cfg.evaluator.timeout}s\n{exc}",
            encoding="utf-8",
        )
        return None, f"Evaluation timed out after {cfg.evaluator.timeout}s.", log_path, True


def _extract_score(stdout: str) -> float | None:
    import re

    patterns = [
        r"score\s*[:=]\s*(-?\d+(?:\.\d+)?)",
        r"AGTS_SCORE\s*[:=]\s*(-?\d+(?:\.\d+)?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, stdout, flags=re.IGNORECASE)
        if match:
            return float(match.group(1))
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        try:
            return float(line)
        except ValueError:
            continue
    return None


def _status_for_score(
    cfg: ResearchConfig,
    score: float | None,
    previous_best: float | None,
    timed_out: bool,
) -> AttemptStatus:
    if timed_out:
        return AttemptStatus.TIMEOUT
    if score is None:
        return AttemptStatus.FAILED
    if previous_best is None:
        return AttemptStatus.IMPROVED
    if _is_better(cfg.evaluator.direction, score, previous_best):
        return AttemptStatus.IMPROVED
    if score == previous_best:
        return AttemptStatus.BASELINE
    return AttemptStatus.REGRESSED


def _best_score(state, best_attempt_id: str | None) -> float | None:
    if not best_attempt_id:
        return None
    attempt = state.attempts.get(best_attempt_id)
    return attempt.score if attempt else None


def _is_better(direction: str, score: float | None, previous: float | None) -> bool:
    if score is None:
        return False
    if previous is None:
        return True
    if direction == "minimize":
        return score < previous
    return score > previous


def _changed_files(workdir: Path) -> list[str]:
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=workdir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    return [line[3:] for line in result.stdout.splitlines() if len(line) > 3]


def _commit_if_possible(workdir: Path, message: str) -> str | None:
    if subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=workdir, capture_output=True).returncode != 0:
        return None
    subprocess.run(["git", "add", "-A"], cwd=workdir, capture_output=True, text=True)
    status = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=workdir)
    if status.returncode == 0:
        return _head(workdir)
    result = subprocess.run(["git", "commit", "-m", message], cwd=workdir, capture_output=True, text=True)
    if result.returncode != 0:
        return _head(workdir)
    return _head(workdir)


def _head(workdir: Path) -> str | None:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=workdir, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return result.stdout.strip()
