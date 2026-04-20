from __future__ import annotations

from pathlib import Path
import json
import time

from agts_research.config import ResearchConfig
from agts_research.models import (
    BranchStatus,
    MetaAction,
    MetaActionType,
    MetaEvent,
    ResearchAttempt,
    ResearchBranch,
    ResearchBranchSummary,
    ResearchMode,
    new_id,
)
from agts_research.provenance import record_provenance
from agts_research.storage import append_jsonl, branch_snapshots, read_state, write_state
from agts_research.workspace import create_branch_agents, create_branch_worktree, write_worker_files


def summarize_branch(cfg: ResearchConfig, branch: ResearchBranch, attempts: dict[str, ResearchAttempt]) -> ResearchBranchSummary:
    branch_attempts = [attempts[aid] for aid in branch.attempt_ids if aid in attempts]
    scored = [
        attempt
        for attempt in branch_attempts
        if attempt.score is not None and attempt.metadata.get("eval_split", "private_dev") == "private_dev"
    ]
    best = _best_attempt(cfg.evaluator.direction, scored)
    recent_failures = [
        attempt.title
        for attempt in branch_attempts[-5:]
        if attempt.status.value in {"failed", "timeout", "regressed"}
    ]
    score_trend = _score_trend(cfg.evaluator.direction, scored[-5:])
    open_questions = []
    if not branch_attempts:
        open_questions.append("No evaluated attempt yet.")
    if branch.evals_since_improvement >= cfg.heartbeat.pivot_after_stall:
        open_questions.append("Branch has stalled and may need a pivot.")
    if branch.best_attempt_id is None:
        open_questions.append("No best attempt has been established.")

    local_agts_runs = [run for attempt in branch_attempts for run in attempt.local_agts_runs]
    changed_files = sorted(
        {
            path
            for attempt in branch_attempts[-5:]
            for path in attempt.changed_files
            if _is_research_changed_file(path)
        }
    )
    eval_evidence = _eval_evidence(scored[-3:])
    note_evidence, note_paths = _note_evidence(branch)
    for note_path in note_paths:
        if note_path not in branch.note_paths:
            branch.note_paths.append(note_path)
    branch.uncertainty = _estimate_uncertainty(cfg, branch, scored, open_questions)
    summary = ResearchBranchSummary(
        branch_id=branch.branch_id,
        hypothesis=branch.hypothesis,
        current_best_result=best.title if best else "",
        best_score=best.score if best else None,
        score_trend=score_trend,
        key_evidence=eval_evidence + _diff_evidence(changed_files) + _local_agts_evidence(local_agts_runs) + note_evidence,
        failed_approaches=recent_failures,
        reusable_findings=_reusable_findings(branch_attempts, local_agts_runs),
        open_questions=open_questions,
        main_risk=_main_risk(branch, branch_attempts),
        recommended_action=_recommended_action(cfg, branch, scored),
        recommended_split_directions=_split_directions(branch),
    )
    branch.summary = summary
    branch.value_estimate = estimate_branch_value(cfg, branch)
    summary.value_of_information = estimate_value_of_information(cfg, branch)
    summary.policy_reason = _policy_reason(branch)
    return summary


def estimate_branch_value(cfg: ResearchConfig, branch: ResearchBranch) -> float:
    summary = branch.summary
    if summary is None:
        return 0.2
    progress = 0.15 if summary.best_score is None else 0.55
    trend_bonus = {"improving": 0.20, "flat": 0.05, "worsening": -0.10, "unknown": 0.0}.get(
        summary.score_trend,
        0.0,
    )
    stall_penalty = min(0.35, 0.08 * branch.evals_since_improvement)
    evidence_bonus = min(0.15, 0.03 * len(summary.key_evidence))
    risk_penalty = 0.10 if summary.main_risk else 0.0
    novelty_penalty = (1.0 - branch.novelty) * 0.20
    return max(0.0, min(1.0, progress + trend_bonus + evidence_bonus - stall_penalty - risk_penalty - novelty_penalty))


def estimate_value_of_information(cfg: ResearchConfig, branch: ResearchBranch) -> float:
    summary = branch.summary
    if summary is None:
        return 0.2
    trend = {"improving": 0.10, "flat": 0.03, "worsening": -0.12, "unknown": 0.06}.get(summary.score_trend, 0.0)
    uncertainty_bonus = 0.18 * branch.uncertainty
    novelty_bonus = 0.12 * branch.novelty
    stall_bonus = 0.07 if branch.evals_since_improvement >= cfg.heartbeat.pivot_after_stall else 0.0
    local_agts_bonus = 0.05 if any("local AGTS" in item for item in summary.key_evidence + summary.reusable_findings) else 0.0
    risk_penalty = 0.12 if summary.main_risk and "stagnation" not in summary.main_risk else 0.0
    cost_penalty = min(0.20, 0.02 * branch.cost.agent_turns + 0.01 * branch.eval_count)
    raw = branch.value_estimate * 0.72 + trend + uncertainty_bonus + novelty_bonus + stall_bonus + local_agts_bonus - risk_penalty - cost_penalty
    return max(0.0, min(1.0, raw))


def choose_meta_action(
    cfg: ResearchConfig,
    branches: list[ResearchBranch],
    attempts: dict[str, ResearchAttempt],
    *,
    run_dir: Path | None = None,
) -> MetaAction:
    active = [branch for branch in branches if branch.status == BranchStatus.ACTIVE]
    budget_reason = _global_budget_exhausted(cfg, branches, attempts)
    if budget_reason:
        best = _best_branch(cfg, branches, attempts)
        return MetaAction(MetaActionType.FINALIZE, best.branch_id, budget_reason)
    if not active:
        best = _best_branch(cfg, branches, attempts)
        if cfg.search.verify_before_finalize and run_dir is not None and not _verification_approved(run_dir, best.branch_id):
            return MetaAction(MetaActionType.VERIFY, best.branch_id, "no active branches remain; verifier approval required before finalization")
        return MetaAction(MetaActionType.FINALIZE, best.branch_id, "no active branches remain and verifier approval is satisfied")

    stoppable = [
        branch
        for branch in active
        if _branch_voi(branch) < cfg.search.stop_threshold and branch.eval_count > 0
    ]
    if stoppable and len(active) > 1:
        branch = sorted(stoppable, key=_branch_voi)[0]
        return MetaAction(MetaActionType.STOP, branch.branch_id, "low value-of-information estimate")

    verify_candidates = [
        branch
        for branch in active
        if branch.summary
        and branch.summary.best_score is not None
        and branch.evals_since_improvement >= cfg.heartbeat.pivot_after_stall
        and _branch_voi(branch) < cfg.search.split_threshold
    ]
    if verify_candidates and cfg.search.verify_before_finalize:
        branch = sorted(verify_candidates, key=lambda item: item.value_estimate, reverse=True)[0]
        return MetaAction(
            MetaActionType.VERIFY,
            branch.branch_id,
            "branch has stalled with a candidate; verifier review is higher value than more exploration",
            expected_gain=_branch_voi(branch),
            expected_cost=0.05,
        )

    split_candidates = [
        branch
        for branch in active
        if _branch_voi(branch) >= cfg.search.split_threshold
        and branch.evals_since_improvement >= cfg.heartbeat.pivot_after_stall
        and len(branches) < cfg.search.max_branches
    ]
    if split_candidates:
        branch = sorted(split_candidates, key=_branch_voi, reverse=True)[0]
        directions = branch.summary.recommended_split_directions if branch.summary else _split_directions(branch)
        return MetaAction(
            MetaActionType.SPLIT,
            branch.branch_id,
            "high value branch has stalled; split into alternative directions",
            direction_a=directions[0],
            direction_b=directions[1],
            expected_gain=_branch_voi(branch),
            expected_cost=0.2,
        )

    best = sorted(active, key=_branch_voi, reverse=True)[0]
    return MetaAction(
        MetaActionType.CONTINUE,
        best.branch_id,
        "best active branch by value-of-information estimate",
        expected_gain=_branch_voi(best),
        expected_cost=0.1,
    )


def run_meta_step(cfg: ResearchConfig, run_dir: Path) -> MetaAction:
    state = read_state(run_dir)
    for branch in state.branches.values():
        summarize_branch(cfg, branch, state.attempts)
    _apply_novelty_penalties(list(state.branches.values()))
    for branch in state.branches.values():
        branch.value_estimate = estimate_branch_value(cfg, branch)
        if branch.summary:
            branch.summary.value_of_information = estimate_value_of_information(cfg, branch)
            branch.summary.policy_reason = _policy_reason(branch)
    action = choose_meta_action(cfg, list(state.branches.values()), state.attempts, run_dir=run_dir)
    if action.type == MetaActionType.STOP:
        state.branches[action.branch_id].status = BranchStatus.STOPPED
    elif action.type == MetaActionType.SPLIT:
        _apply_split(cfg, state, action)
    elif action.type == MetaActionType.FINALIZE:
        state.finalized_branch_id = action.branch_id
        state.branches[action.branch_id].status = BranchStatus.FINALIZED
    write_state(run_dir, state)
    append_jsonl(
        run_dir / "meta_events.jsonl",
        MetaEvent.now(
            action,
            branch_snapshots(state),
            attempts_seen=len(state.attempts),
            reason=action.reason,
        ),
    )
    return action


def _apply_split(cfg: ResearchConfig, state, action: MetaAction) -> None:
    parent = state.branches[action.branch_id]
    parent.status = BranchStatus.SPLIT
    directions = _validated_split_directions(
        parent,
        [action.direction_a or "alternative implementation", action.direction_b or "counterexample search"],
    )
    modes = [ResearchMode.IMPLEMENTATION_EXPERIMENT, ResearchMode.COUNTEREXAMPLE_SEARCH]
    run_dir = Path(state.run_dir)
    repo_dir = Path(state.repo_dir)
    for index, direction in enumerate(directions):
        brief = _split_brief(parent, direction, index=index)
        branch = ResearchBranch(
            branch_id=new_id("rb"),
            parent_id=parent.branch_id,
            title=f"{parent.title}: {direction}",
            hypothesis=f"{parent.hypothesis}\n\nSplit direction: {direction}\n\n{brief}",
            research_mode=modes[index],
            depth=parent.depth + 1,
        )
        worktree = create_branch_worktree(run_dir, repo_dir, branch.branch_id)
        branch.worktree_path = str(worktree)
        state.branches[branch.branch_id] = branch
        agents = create_branch_agents(cfg, branch, worktree)
        for agent in agents:
            state.agents[agent.agent_id] = agent
        if agents:
            write_worker_files(cfg, run_dir, branch, agents[0])
        brief_path = run_dir / "public" / "evidence" / "split_briefs" / f"{branch.branch_id}.md"
        brief_path.parent.mkdir(parents=True, exist_ok=True)
        brief_path.write_text(brief + "\n", encoding="utf-8")
        record_provenance(
            run_dir,
            path=brief_path,
            kind="evidence",
            source="split_brief",
            branch_id=branch.branch_id,
            metadata={"parent_branch_id": parent.branch_id, "direction": direction},
        )
        rel_path = str(brief_path.relative_to(run_dir))
        branch.evidence_paths.append(rel_path)


def _best_attempt(direction: str, attempts: list[ResearchAttempt]) -> ResearchAttempt | None:
    if not attempts:
        return None
    if direction == "minimize":
        return min(attempts, key=lambda attempt: attempt.score)
    return max(attempts, key=lambda attempt: attempt.score)


def _best_branch(cfg: ResearchConfig, branches: list[ResearchBranch], attempts: dict[str, ResearchAttempt]) -> ResearchBranch:
    def score(branch: ResearchBranch) -> tuple[float, float]:
        best = attempts.get(branch.best_attempt_id or "")
        raw = best.score if best and best.score is not None else float("-inf")
        if cfg.evaluator.direction == "minimize" and raw != float("-inf"):
            raw = -raw
        return (raw, branch.value_estimate)

    return max(branches, key=score)


def _private_dev_attempts(attempts: dict[str, ResearchAttempt]) -> list[ResearchAttempt]:
    return [
        attempt
        for attempt in attempts.values()
        if attempt.metadata.get("eval_split", "private_dev") == "private_dev"
    ]


def _global_budget_exhausted(
    cfg: ResearchConfig,
    branches: list[ResearchBranch],
    attempts: dict[str, ResearchAttempt],
) -> str:
    private_dev_evals = len(_private_dev_attempts(attempts))
    if private_dev_evals >= cfg.search.max_evals:
        return "private-dev eval budget exhausted; explicit budget exhaustion finalization"
    total_turns = sum(branch.cost.agent_turns for branch in branches)
    if cfg.search.max_agent_turns > 0 and total_turns >= cfg.search.max_agent_turns:
        return "agent-turn budget exhausted; explicit budget exhaustion finalization"
    total_wall = sum(branch.cost.wall_seconds for branch in branches)
    if cfg.search.max_wall_seconds > 0 and total_wall >= cfg.search.max_wall_seconds:
        return "wall-clock budget exhausted; explicit budget exhaustion finalization"
    return ""


def _verification_approved(run_dir: Path, branch_id: str) -> bool:
    path = run_dir / "public" / "evidence" / "verifications" / f"{branch_id}.json"
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(data.get("approved"))


def _score_trend(direction: str, attempts: list[ResearchAttempt]) -> str:
    if len(attempts) < 2:
        return "unknown"
    first = attempts[0].score
    last = attempts[-1].score
    if first is None or last is None:
        return "unknown"
    if direction == "minimize":
        if last < first:
            return "improving"
        if last > first:
            return "worsening"
    else:
        if last > first:
            return "improving"
        if last < first:
            return "worsening"
    return "flat"


def _estimate_uncertainty(
    cfg: ResearchConfig,
    branch: ResearchBranch,
    scored: list[ResearchAttempt],
    open_questions: list[str],
) -> float:
    if not scored:
        return 1.0
    sample_bonus = max(0.0, 0.45 - 0.08 * len(scored))
    question_bonus = min(0.30, 0.10 * len(open_questions))
    stall_reduction = min(0.25, 0.06 * branch.evals_since_improvement)
    trend_bonus = 0.10 if _score_trend(cfg.evaluator.direction, scored[-5:]) == "unknown" else 0.0
    return max(0.05, min(1.0, 0.20 + sample_bonus + question_bonus + trend_bonus - stall_reduction))


def _apply_novelty_penalties(branches: list[ResearchBranch]) -> None:
    seen: list[ResearchBranch] = []
    for branch in sorted(branches, key=lambda item: (item.depth, item.branch_id)):
        branch.novelty = 1.0
        tokens = _branch_tokens(branch)
        for other in seen:
            similarity = _jaccard(tokens, _branch_tokens(other))
            if similarity > 0.72:
                branch.novelty = min(branch.novelty, max(0.2, 1.0 - similarity + 0.2))
        seen.append(branch)


def _branch_tokens(branch: ResearchBranch) -> set[str]:
    text = f"{branch.title} {branch.hypothesis}"
    if branch.summary:
        text += " " + " ".join(branch.summary.key_evidence[:5])
    return {token for token in _tokenize(text) if len(token) > 3}


def _tokenize(text: str) -> list[str]:
    import re

    return re.findall(r"[a-z0-9_]+", text.lower())


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _branch_voi(branch: ResearchBranch) -> float:
    if branch.summary:
        return branch.summary.value_of_information
    return branch.value_estimate


def _policy_reason(branch: ResearchBranch) -> str:
    summary = branch.summary
    if summary is None:
        return "no summary available"
    return (
        f"value={branch.value_estimate:.2f}, uncertainty={branch.uncertainty:.2f}, "
        f"novelty={branch.novelty:.2f}, trend={summary.score_trend}, "
        f"stall={branch.evals_since_improvement}, voi={summary.value_of_information:.2f}"
    )


def _main_risk(branch: ResearchBranch, attempts: list[ResearchAttempt]) -> str:
    if not attempts:
        return "branch has not been evaluated"
    if branch.evals_since_improvement >= 3:
        return "stagnation after repeated evals"
    if attempts[-1].status.value in {"failed", "timeout"}:
        return "latest eval failed"
    return ""


def _recommended_action(cfg: ResearchConfig, branch: ResearchBranch, scored: list[ResearchAttempt]) -> str:
    if branch.evals_since_improvement >= cfg.heartbeat.pivot_after_stall:
        return "split"
    if not scored:
        return "continue"
    return "continue"


def _split_directions(branch: ResearchBranch) -> list[str]:
    summary = branch.summary
    if summary is None:
        return [
            "establish a stronger baseline and measurement protocol",
            "try a substantially different counter-hypothesis",
        ]
    directions: list[str] = []
    if summary.score_trend in {"flat", "worsening"} or branch.evals_since_improvement > 0:
        directions.append("targeted failure analysis and repair of the current best approach")
    else:
        directions.append("exploit and strengthen the current best approach")
    if summary.failed_approaches:
        directions.append("counter-hypothesis that avoids recent failed assumptions")
    elif summary.key_evidence:
        directions.append("orthogonal strategy based on unexplained evidence gaps")
    else:
        directions.append("independent alternative baseline")
    return _validated_split_directions(branch, directions)


def _validated_split_directions(branch: ResearchBranch, directions: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen_tokens: list[set[str]] = []
    for raw in directions:
        direction = " ".join(raw.split()).strip()
        if not direction:
            continue
        tokens = {token for token in _tokenize(direction) if len(token) > 3}
        if any(_jaccard(tokens, existing) > 0.65 for existing in seen_tokens):
            continue
        cleaned.append(direction)
        seen_tokens.append(tokens)
        if len(cleaned) == 2:
            break
    fallbacks = [
        "verify and strengthen the current best approach",
        "try a substantially different counter-hypothesis",
    ]
    for fallback in fallbacks:
        if len(cleaned) >= 2:
            break
        if fallback not in cleaned:
            cleaned.append(fallback)
    return cleaned[:2]


def _split_brief(parent: ResearchBranch, direction: str, *, index: int) -> str:
    summary = parent.summary
    evidence = summary.key_evidence[:5] if summary else []
    failed = summary.failed_approaches[:5] if summary else []
    reusable = summary.reusable_findings[:5] if summary else []
    open_questions = summary.open_questions[:5] if summary else []
    return "\n".join(
        [
            f"# Split Brief {index + 1}: {direction}",
            "",
            f"Parent branch: {parent.branch_id}",
            f"Direction: {direction}",
            "",
            "## Parent Evidence",
            *_markdown_items(evidence, fallback="none"),
            "",
            "## Failed Or Weak Approaches",
            *_markdown_items(failed, fallback="none"),
            "",
            "## Reusable Findings",
            *_markdown_items(reusable, fallback="none"),
            "",
            "## Open Questions",
            *_markdown_items(open_questions, fallback="none"),
            "",
            "## Worker Constraints",
            "- Preserve the solver/API contract.",
            "- Do not read hidden evaluator material.",
            "- Run private-dev eval only through `./agts-research eval`.",
            "- Update the branch note using the required schema.",
        ]
    )


def _markdown_items(items: list[str], *, fallback: str) -> list[str]:
    if not items:
        return [f"- {fallback}"]
    return [f"- {item}" for item in items]


def _eval_evidence(attempts: list[ResearchAttempt]) -> list[str]:
    evidence: list[str] = []
    for attempt in attempts:
        detail = f"{attempt.title}: score={attempt.score}"
        metrics = attempt.metadata.get("score_bundle", {}).get("metrics", {})
        if isinstance(metrics, dict) and metrics:
            metric_text = ", ".join(f"{key}={value}" for key, value in list(metrics.items())[:3])
            detail = f"{detail}; metrics={metric_text}"
        if attempt.feedback:
            first_line = next((line.strip() for line in attempt.feedback.splitlines() if line.strip()), "")
            if first_line and first_line not in detail:
                detail = f"{detail}; feedback={first_line[:160]}"
        evidence.append(detail)
    return evidence


def _diff_evidence(changed_files: list[str]) -> list[str]:
    if not changed_files:
        return []
    preview = ", ".join(changed_files[:6])
    if len(changed_files) > 6:
        preview += f", +{len(changed_files) - 6} more"
    return [f"recent changed files: {preview}"]


def _local_agts_evidence(local_agts_runs: list[str]) -> list[str]:
    if not local_agts_runs:
        return []
    preview = ", ".join(local_agts_runs[-3:])
    return [f"local AGTS used in {len(local_agts_runs)} linked run(s): {preview}"]


def _note_evidence(branch: ResearchBranch) -> tuple[list[str], list[str]]:
    if not branch.worktree_path:
        return [], []
    breadcrumb = Path(branch.worktree_path) / ".agts_research_dir"
    if not breadcrumb.exists():
        return [], []
    try:
        run_dir = Path(breadcrumb.read_text(encoding="utf-8").strip())
    except OSError:
        return [], []
    note_dir = run_dir / "public" / "notes" / branch.branch_id
    latest = note_dir / "latest.md"
    if not latest.exists():
        return [], []
    try:
        lines = [line.strip() for line in latest.read_text(encoding="utf-8", errors="ignore").splitlines()]
    except OSError:
        return [], []
    evidence = []
    for line in lines:
        if not line or line.startswith("#"):
            continue
        if line in {"- none", "none", "No work recorded yet.", "- used: no", "- runs: none"}:
            continue
        if line.startswith("## "):
            continue
        evidence.append(f"branch note: {line[:180]}")
        if len(evidence) >= 3:
            break
    try:
        rel_path = str(latest.relative_to(run_dir))
    except ValueError:
        rel_path = str(latest)
    return evidence, [rel_path]


def _reusable_findings(attempts: list[ResearchAttempt], local_agts_runs: list[str]) -> list[str]:
    findings: list[str] = []
    improved_with_local = [
        attempt
        for attempt in attempts
        if attempt.metadata.get("improved_after_local_agts")
    ]
    if improved_with_local:
        findings.append(f"local AGTS preceded {len(improved_with_local)} improved attempt(s)")
    if local_agts_runs:
        findings.append("local AGTS artifacts are available for branch-level audit")
    repeated_files = sorted(
        {
            path
            for attempt in attempts
            for path in attempt.changed_files
            if _is_research_changed_file(path)
        }
    )
    if repeated_files:
        findings.append(f"implementation activity concentrated in: {', '.join(repeated_files[:5])}")
    return findings


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
