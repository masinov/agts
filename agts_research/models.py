from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
import time
import uuid


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class ResearchMode(str, Enum):
    LITERATURE_SURVEY = "literature_survey"
    BASELINE_REPRODUCTION = "baseline_reproduction"
    INDEPENDENT_HYPOTHESIS = "independent_hypothesis"
    IMPLEMENTATION_EXPERIMENT = "implementation_experiment"
    ABLATION = "ablation"
    COUNTEREXAMPLE_SEARCH = "counterexample_search"
    THEORY_CHECK = "theory_check"
    FAILURE_ANALYSIS = "failure_analysis"
    SKILL_DISTILLATION = "skill_distillation"
    PAPER_SYNTHESIS = "paper_synthesis"


class BranchStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    STOPPED = "stopped"
    SPLIT = "split"
    FINALIZED = "finalized"


class AttemptStatus(str, Enum):
    PENDING = "pending"
    IMPROVED = "improved"
    BASELINE = "baseline"
    REGRESSED = "regressed"
    FAILED = "failed"
    TIMEOUT = "timeout"


class MetaActionType(str, Enum):
    CONTINUE = "continue"
    SPLIT = "split"
    ASSIGN_AGENT = "assign_agent"
    PAUSE = "pause"
    STOP = "stop"
    VERIFY = "verify"
    DISTILL = "distill"
    FINALIZE = "finalize"


class WorkerProcessStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    EXITED = "exited"
    STOPPED = "stopped"
    TIMED_OUT = "timed_out"
    UNKNOWN = "unknown"


@dataclass
class ResearchCost:
    agent_turns: int = 0
    evals: int = 0
    tokens: int = 0
    wall_seconds: float = 0.0


@dataclass
class LocalAgtsConfig:
    enabled: bool = True
    mode: str = "optional"  # optional | required | disabled
    max_steps: int = 4
    before_eval: bool = True
    after_failed_eval: bool = True
    before_pivot: bool = True


@dataclass
class AgentSpec:
    agent_id: str
    branch_id: str
    role: str
    runtime: str
    model: str
    worktree_path: str
    status: str = "created"
    session_id: str | None = None
    pid: int | None = None
    log_path: str | None = None
    started_at: float | None = None
    stopped_at: float | None = None
    exit_code: int | None = None
    launch_command: list[str] = field(default_factory=list)
    timeout_seconds: float | None = None


@dataclass
class ResearchBranchSummary:
    branch_id: str
    hypothesis: str
    current_best_result: str = ""
    best_score: float | None = None
    score_trend: str = "unknown"
    key_evidence: list[str] = field(default_factory=list)
    failed_approaches: list[str] = field(default_factory=list)
    reusable_findings: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    main_risk: str = ""
    recommended_action: str = "continue"
    recommended_split_directions: list[str] = field(default_factory=list)


@dataclass
class ResearchBranch:
    branch_id: str
    parent_id: str | None
    title: str
    hypothesis: str
    research_mode: ResearchMode
    status: BranchStatus = BranchStatus.ACTIVE
    depth: int = 0
    worktree_path: str | None = None
    assigned_agents: list[str] = field(default_factory=list)
    best_attempt_id: str | None = None
    attempt_ids: list[str] = field(default_factory=list)
    note_paths: list[str] = field(default_factory=list)
    skill_paths: list[str] = field(default_factory=list)
    evidence_paths: list[str] = field(default_factory=list)
    summary: ResearchBranchSummary | None = None
    value_estimate: float = 0.0
    uncertainty: float = 1.0
    novelty: float = 1.0
    eval_count: int = 0
    evals_since_improvement: int = 0
    cost: ResearchCost = field(default_factory=ResearchCost)


@dataclass
class ResearchAttempt:
    attempt_id: str
    branch_id: str
    agent_id: str
    title: str
    score: float | None
    status: AttemptStatus
    timestamp: float
    commit_hash: str | None = None
    parent_attempt_id: str | None = None
    feedback: str = ""
    changed_files: list[str] = field(default_factory=list)
    eval_log_path: str | None = None
    local_agts_runs: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MetaAction:
    type: MetaActionType
    branch_id: str
    reason: str
    direction_a: str | None = None
    direction_b: str | None = None
    expected_gain: float = 0.0
    expected_cost: float = 0.0


@dataclass
class MetaEvent:
    timestamp: float
    action: dict[str, Any]
    branches: list[dict[str, Any]]
    attempts_seen: int
    reason: str = ""

    @classmethod
    def now(
        cls,
        action: MetaAction | dict[str, Any],
        branches: list[dict[str, Any]],
        attempts_seen: int,
        reason: str = "",
    ) -> "MetaEvent":
        from agts.jsonutil import to_jsonable

        return cls(
            timestamp=time.time(),
            action=to_jsonable(action),
            branches=branches,
            attempts_seen=attempts_seen,
            reason=reason,
        )


@dataclass
class ResearchRunState:
    run_id: str
    task_name: str
    task_description: str
    run_dir: str
    repo_dir: str
    created_at: float
    branches: dict[str, ResearchBranch] = field(default_factory=dict)
    attempts: dict[str, ResearchAttempt] = field(default_factory=dict)
    agents: dict[str, AgentSpec] = field(default_factory=dict)
    finalized_branch_id: str | None = None
