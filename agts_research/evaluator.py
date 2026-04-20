from __future__ import annotations

from pathlib import Path
from contextlib import contextmanager
import fcntl
import json
import os
import shlex
import subprocess
import time

from agts_research.config import ResearchConfig
from agts_research.models import AttemptStatus, BranchStatus, ResearchAttempt, ScoreBundle, new_id
from agts_research.provenance import record_provenance
from agts_research.storage import append_jsonl, branch_snapshots, read_state, write_state
from agts_research.workspace import find_run_dir_from_worktree, read_worktree_identity


def submit_eval(
    *,
    cfg: ResearchConfig,
    message: str,
    workdir: Path,
    final: bool = False,
    use_server: bool = True,
) -> ResearchAttempt:
    run_dir = find_run_dir_from_worktree(workdir)
    if use_server and not final:
        from agts_research.eval_server import request_eval, server_ready
        from agts_research.hydrate import hydrate_attempt

        if server_ready(run_dir):
            response = request_eval(run_dir, message=message, workdir=workdir)
            attempt = response.get("attempt")
            if not isinstance(attempt, dict):
                raise RuntimeError("evaluator server returned no attempt")
            return hydrate_attempt(attempt)
        if os.environ.get("AGTS_WORKER_SANDBOX") == "1":
            raise RuntimeError(
                "evaluator server is unavailable; refusing to run private-dev eval inside worker sandbox"
            )

    with _eval_lock(run_dir):
        state = read_state(run_dir)
        branch_id, agent_id = read_worktree_identity(workdir)
        branch = state.branches[branch_id]
        if not final and branch.status != BranchStatus.ACTIVE:
            raise RuntimeError(f"cannot submit private-dev eval for branch {branch_id} with status={branch.status.value}")

        changed_files = _changed_files(workdir)
        commit_hash = _commit_if_possible(workdir, message)
        score_bundle, feedback, eval_log_path, timed_out = _run_evaluator(cfg, run_dir, workdir, final=final)
        score = score_bundle.primary
        previous_best = _best_score(state, branch.best_attempt_id)
        status = _status_for_score(cfg, score, previous_best, timed_out, valid=score_bundle.valid)
        local_agts_runs = _local_agts_runs(workdir)
        local_agts_used = bool(local_agts_runs)

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
            local_agts_runs=local_agts_runs,
            metadata={
                "eval_split": "final_holdout" if final else "private_dev",
                "score_bundle": _score_bundle_dict(score_bundle),
                "local_agts_used": local_agts_used,
                "local_agts_run_count": len(local_agts_runs),
                "improved_after_local_agts": local_agts_used and status == AttemptStatus.IMPROVED,
            },
        )
        state.attempts[attempt.attempt_id] = attempt
        branch.attempt_ids.append(attempt.attempt_id)
        branch.cost.evals += 1
        if not final:
            branch.eval_count += 1
        if not final and _is_better(cfg.evaluator.direction, score, previous_best):
            branch.best_attempt_id = attempt.attempt_id
            branch.evals_since_improvement = 0
        elif not final:
            branch.evals_since_improvement += 1
        write_state(run_dir, state)
        if eval_log_path:
            record_provenance(
                run_dir,
                path=eval_log_path,
                kind="eval_log",
                source="evaluator",
                branch_id=branch_id,
                agent_id=agent_id,
                metadata={"attempt_id": attempt.attempt_id, "eval_split": attempt.metadata["eval_split"]},
            )
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
                "eval_split": "final_holdout" if final else "private_dev",
                "local_agts_used": local_agts_used,
                "local_agts_runs": local_agts_runs,
                "branches": branch_snapshots(state),
            },
        )
        return attempt


def _run_evaluator(
    cfg: ResearchConfig,
    run_dir: Path,
    workdir: Path,
    *,
    final: bool = False,
) -> tuple[ScoreBundle, str, Path | None, bool]:
    command = cfg.evaluator.final_command if final and cfg.evaluator.final_command else cfg.evaluator.command
    if cfg.evaluator.type == "none" or not command:
        return ScoreBundle(primary=None, direction=cfg.evaluator.direction), "No evaluator configured.", None, False

    eval_id = new_id("eval")
    log_path = run_dir / "public" / "eval_logs" / f"{eval_id}.log"
    env = os.environ.copy()
    env["AGTS_EVAL_SPLIT"] = "final_holdout" if final else "private_dev"
    env["AGTS_PRIVATE_DIR"] = str((run_dir / "private").resolve())
    command_args = shlex.split(command)
    try:
        result = subprocess.run(
            command_args,
            shell=False,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=cfg.evaluator.timeout,
            env=env,
        )
        output = "\n".join(
            [
                f"$ {command}",
                f"split={env['AGTS_EVAL_SPLIT']}",
                f"returncode={result.returncode}",
                "--- stdout ---",
                result.stdout,
                "--- stderr ---",
                result.stderr,
            ]
        )
        log_path.write_text(output, encoding="utf-8")
        score_bundle = _extract_score_bundle(
            result.stdout,
            direction=cfg.evaluator.direction,
            split=env["AGTS_EVAL_SPLIT"],
        )
        if score_bundle.primary is None and result.returncode == 0:
            score_bundle.primary = 1.0
        feedback = result.stderr.strip() or result.stdout.strip()[-1000:] or "Evaluator completed."
        if result.returncode != 0:
            score_bundle.valid = False
            score_bundle.failure_reason = f"evaluator returned {result.returncode}"
        score_bundle.raw_feedback = feedback
        return score_bundle, feedback, log_path, False
    except subprocess.TimeoutExpired as exc:
        log_path.write_text(
            f"$ {command}\nTIMEOUT after {cfg.evaluator.timeout}s\n{exc}",
            encoding="utf-8",
        )
        return (
            ScoreBundle(
                primary=None,
                direction=cfg.evaluator.direction,
                split="final_holdout" if final else "private_dev",
                valid=False,
                failure_reason=f"Evaluation timed out after {cfg.evaluator.timeout}s.",
            ),
            f"Evaluation timed out after {cfg.evaluator.timeout}s.",
            log_path,
            True,
        )


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


def _extract_score_bundle(stdout: str, *, direction: str, split: str) -> ScoreBundle:
    structured = _extract_structured_feedback(stdout)
    if structured:
        primary = structured.get("score", structured.get("primary"))
        metrics = structured.get("metrics", {})
        if not isinstance(metrics, dict):
            metrics = {}
        for key, value in structured.items():
            if key not in {"score", "primary", "metrics", "valid", "failure_reason", "split", "direction"}:
                metrics[key] = value
        return ScoreBundle(
            primary=float(primary) if primary is not None else None,
            direction=str(structured.get("direction", direction)),
            split=str(structured.get("split", split)),
            metrics=metrics,
            valid=bool(structured.get("valid", True)),
            failure_reason=str(structured.get("failure_reason", "")),
        )
    return ScoreBundle(primary=_extract_score(stdout), direction=direction, split=split)


def _extract_structured_feedback(stdout: str) -> dict[str, object] | None:
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("AGTS_SCORE_BUNDLE="):
            stripped = stripped.split("=", 1)[1].strip()
        elif stripped.startswith("AGTS_SCORE_BUNDLE:"):
            stripped = stripped.split(":", 1)[1].strip()
        elif not stripped.startswith("{"):
            continue
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and ("score" in value or "primary" in value or "metrics" in value):
            return value
    return None


def _score_bundle_dict(bundle: ScoreBundle) -> dict[str, object]:
    return {
        "primary": bundle.primary,
        "direction": bundle.direction,
        "split": bundle.split,
        "metrics": bundle.metrics,
        "valid": bundle.valid,
        "failure_reason": bundle.failure_reason,
        "raw_feedback": bundle.raw_feedback,
    }


def _status_for_score(
    cfg: ResearchConfig,
    score: float | None,
    previous_best: float | None,
    timed_out: bool,
    *,
    valid: bool = True,
) -> AttemptStatus:
    if timed_out:
        return AttemptStatus.TIMEOUT
    if not valid:
        return AttemptStatus.FAILED
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
    files = [line[3:] for line in result.stdout.splitlines() if len(line) > 3]
    return [path for path in files if _is_research_changed_file(path)]


def _local_agts_runs(workdir: Path) -> list[str]:
    root = workdir / ".tot" / "runs"
    if not root.exists():
        return []
    runs: list[Path] = []
    for item in root.iterdir():
        if not item.is_dir():
            continue
        if (item / "final_answer.md").exists() or (item / "events.jsonl").exists():
            runs.append(item)
    runs.sort(key=lambda path: path.stat().st_mtime)
    return [_display_path(path, workdir) for path in runs]


def _display_path(path: Path, workdir: Path) -> str:
    try:
        return str(path.relative_to(workdir))
    except ValueError:
        return str(path)


def _is_research_changed_file(path: str) -> bool:
    ignored = {
        ".agts_agent_id",
        ".agts_branch_id",
        ".agts_research_dir",
        "AGTS_RESEARCH.md",
        "CLAUDE.md",
        "agts-research",
    }
    if path in ignored:
        return False
    if path.startswith(".claude/") or path == ".claude/":
        return False
    if path.startswith(".tot/") or path == ".tot":
        return False
    return True


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


@contextmanager
def _eval_lock(run_dir: Path):
    lock_path = run_dir / "public" / "evaluator" / "eval.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
