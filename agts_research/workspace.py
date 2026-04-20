from __future__ import annotations

from pathlib import Path
import os
import shutil
import subprocess
import time

from agts_research.config import ResearchConfig
from agts_research.instructions import branch_brief, worker_instructions
from agts_research.models import (
    AgentSpec,
    BranchStatus,
    ResearchBranch,
    ResearchMode,
    ResearchRunState,
    new_id,
)
from agts_research.storage import create_run_id, ensure_run_layout, write_state
from agts_research.storage import write_json_atomic


IGNORE_DIRS = {
    ".git",
    ".research",
    ".tot",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}


def start_research_run(cfg: ResearchConfig) -> ResearchRunState:
    run_id = create_run_id(cfg.task.name)
    run_dir = Path(cfg.workspace.results_dir).resolve() / run_id
    ensure_run_layout(run_dir)
    repo_dir = run_dir / "repo"
    _copy_seed(Path(cfg.workspace.seed_path).resolve(), repo_dir)
    _copy_private(cfg, run_dir)

    branch = ResearchBranch(
        branch_id=new_id("rb"),
        parent_id=None,
        title="root research branch",
        hypothesis=cfg.task.description,
        research_mode=ResearchMode.BASELINE_REPRODUCTION,
        status=BranchStatus.ACTIVE,
        depth=0,
    )
    worktree = create_branch_worktree(run_dir, repo_dir, branch.branch_id)
    branch.worktree_path = str(worktree)

    agents = create_branch_agents(cfg, branch, worktree)

    state = ResearchRunState(
        run_id=run_id,
        task_name=cfg.task.name,
        task_description=cfg.task.description,
        run_dir=str(run_dir),
        repo_dir=str(repo_dir),
        created_at=time.time(),
        branches={branch.branch_id: branch},
        agents={agent.agent_id: agent for agent in agents},
    )
    if agents:
        write_worker_files(cfg, run_dir, branch, agents[0])
    _commit_branch_setup(worktree)
    write_json_atomic(run_dir / "config.json", cfg.to_dict())
    write_state(run_dir, state)
    return state


def create_branch_worktree(run_dir: Path, repo_dir: Path, branch_id: str) -> Path:
    worktrees_dir = run_dir / "worktrees"
    worktree = worktrees_dir / branch_id
    if worktree.exists():
        return worktree

    if _is_git_repo(repo_dir):
        branch_name = f"agts-research/{branch_id}"
        _ensure_initial_commit(repo_dir)
        subprocess.run(["git", "branch", branch_name], cwd=repo_dir, capture_output=True, text=True)
        result = subprocess.run(
            ["git", "worktree", "add", str(worktree), branch_name],
            cwd=repo_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return worktree

    shutil.copytree(
        repo_dir,
        worktree,
        ignore=shutil.ignore_patterns(*IGNORE_DIRS),
    )
    return worktree


def write_worker_files(
    cfg: ResearchConfig,
    run_dir: Path,
    branch: ResearchBranch,
    agent: AgentSpec,
) -> None:
    worktree = Path(agent.worktree_path)
    shared_dir = worktree / _shared_dir_name(cfg.agents.runtime)
    shared_dir.mkdir(parents=True, exist_ok=True)
    _symlink_public(run_dir, shared_dir)

    (worktree / ".agts_research_dir").write_text(str(run_dir), encoding="utf-8")
    (worktree / ".agts_branch_id").write_text(branch.branch_id, encoding="utf-8")
    (worktree / ".agts_agent_id").write_text(agent.agent_id, encoding="utf-8")
    (worktree / "AGTS_RESEARCH.md").write_text(branch_brief(branch), encoding="utf-8")
    (worktree / "CLAUDE.md").write_text(
        worker_instructions(
            cfg,
            branch,
            agent_id=agent.agent_id,
            agent_role=agent.role,
            shared_dir_name=shared_dir.name,
        ),
        encoding="utf-8",
    )
    _ensure_worktree_gitignore(worktree)
    _write_worktree_helper(worktree)
    notes_dir = shared_dir / "notes" / branch.branch_id
    notes_dir.mkdir(parents=True, exist_ok=True)
    latest_note = notes_dir / "latest.md"
    if not latest_note.exists():
        latest_note.write_text(_branch_note_template(branch), encoding="utf-8")


def find_run_dir_from_worktree(workdir: Path) -> Path:
    breadcrumb = workdir / ".agts_research_dir"
    if not breadcrumb.exists():
        raise RuntimeError(f"No .agts_research_dir found in {workdir}")
    return Path(breadcrumb.read_text(encoding="utf-8").strip()).resolve()


def read_worktree_identity(workdir: Path) -> tuple[str, str]:
    branch = workdir / ".agts_branch_id"
    agent = workdir / ".agts_agent_id"
    if not branch.exists() or not agent.exists():
        raise RuntimeError("worktree is missing .agts_branch_id or .agts_agent_id")
    return (
        branch.read_text(encoding="utf-8").strip(),
        agent.read_text(encoding="utf-8").strip(),
    )


def create_branch_agents(cfg: ResearchConfig, branch: ResearchBranch, worktree: Path) -> list[AgentSpec]:
    roles = _agent_roles(cfg)
    agents: list[AgentSpec] = []
    for index, role in enumerate(roles):
        suffix = chr(ord("a") + index)
        agent_id = f"agent-{branch.branch_id}-{suffix}"
        agent = AgentSpec(
            agent_id=agent_id,
            branch_id=branch.branch_id,
            role=role,
            runtime=cfg.agents.runtime,
            model=cfg.agents.model,
            worktree_path=str(worktree),
        )
        branch.assigned_agents.append(agent_id)
        agents.append(agent)
    return agents


def _agent_roles(cfg: ResearchConfig) -> list[str]:
    limit = max(1, min(cfg.search.max_agents_per_branch, cfg.agents.max_agents))
    roles = [role for role in cfg.agents.roles if role]
    if not roles:
        roles = ["research_worker"]
    if "research_worker" not in roles:
        roles.insert(0, "research_worker")
    return roles[:limit]


def _copy_seed(seed_path: Path, repo_dir: Path) -> None:
    if any(repo_dir.iterdir()):
        return
    if seed_path.is_file():
        repo_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(seed_path, repo_dir / seed_path.name)
    elif seed_path.is_dir():
        for item in seed_path.iterdir():
            if item.name in IGNORE_DIRS:
                continue
            dst = repo_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dst, ignore=shutil.ignore_patterns(*IGNORE_DIRS))
            else:
                shutil.copy2(item, dst)
    else:
        repo_dir.mkdir(parents=True, exist_ok=True)
    _ensure_git_repo(repo_dir)


def _copy_private(cfg: ResearchConfig, run_dir: Path) -> None:
    private_root = run_dir / "private"
    for raw in [*cfg.evaluator.private_paths, *cfg.evaluator.holdout_paths]:
        src = Path(raw).resolve()
        if not src.exists():
            continue
        dst = private_root / src.name
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def _ensure_git_repo(repo_dir: Path) -> None:
    if _is_git_repo(repo_dir):
        return
    subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True, text=True)
    _ensure_initial_commit(repo_dir)


def _ensure_initial_commit(repo_dir: Path) -> None:
    if not _is_git_repo(repo_dir):
        return
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True)
    if result.returncode == 0:
        return
    subprocess.run(["git", "add", "-A"], cwd=repo_dir, capture_output=True, text=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "Initial AGTS research seed"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
    )


def _is_git_repo(path: Path) -> bool:
    if not (path / ".git").exists():
        return False
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=path,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def _shared_dir_name(runtime: str) -> str:
    if runtime == "codex":
        return ".codex"
    if runtime == "opencode":
        return ".opencode"
    return ".claude"


def _symlink_public(run_dir: Path, shared_dir: Path) -> None:
    public = run_dir / "public"
    for item in ["attempts", "notes", "skills", "evidence", "eval_logs", "summaries", "heartbeat"]:
        src = public / item
        dst = shared_dir / item
        if dst.exists() or dst.is_symlink():
            continue
        try:
            rel = os.path.relpath(src.resolve(), shared_dir.resolve())
            dst.symlink_to(rel)
        except OSError:
            dst.symlink_to(src.resolve())


def _write_worktree_helper(worktree: Path) -> None:
    source_root = Path(__file__).resolve().parent.parent
    helper = worktree / "agts-research"
    helper.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"export PYTHONPATH=\"{source_root}:${{PYTHONPATH:-}}\"\n"
        "exec python -m agts.cli research \"$@\"\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)


def _commit_branch_setup(worktree: Path) -> None:
    if subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=worktree, capture_output=True).returncode != 0:
        return
    subprocess.run(["git", "add", "-A"], cwd=worktree, capture_output=True, text=True)
    status = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=worktree)
    if status.returncode == 0:
        return
    subprocess.run(
        ["git", "commit", "-m", "Add AGTS research branch scaffolding"],
        cwd=worktree,
        capture_output=True,
        text=True,
    )


def _ensure_worktree_gitignore(worktree: Path) -> None:
    path = worktree / ".gitignore"
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    needed = [".tot/", ".research/"]
    changed = False
    for item in needed:
        if item not in existing:
            existing.append(item)
            changed = True
    if changed:
        path.write_text("\n".join(existing).rstrip() + "\n", encoding="utf-8")


def _branch_note_template(branch: ResearchBranch) -> str:
    return f"""# Branch {branch.branch_id} Notes

## Current Hypothesis
{branch.hypothesis}

## Latest Work
No work recorded yet.

## Evidence
- none

## Failed Assumptions
- none

## Local AGTS
- used: no
- runs: none

## Recommended Next Action
continue

## Open Questions
- none
"""
