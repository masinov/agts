from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any
import time
import uuid


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class BranchMode(str, Enum):
    DIRECT_SOLVE = "direct_solve"
    DECOMPOSE = "decompose"
    INDEPENDENT_RE_DERIVE = "independent_rederive"
    TOOL_VERIFY = "tool_verify"
    COUNTEREXAMPLE_SEARCH = "counterexample_search"
    ASSUMPTION_STRESS_TEST = "assumption_stress_test"
    COMPRESS_AND_FINALIZE = "compress_and_finalize"


class ActionType(str, Enum):
    CONTINUE = "continue"
    SPLIT = "split"
    STOP = "stop"
    FINALIZE = "finalize"
    FORCED_FINALIZE = "forced_finalize"


@dataclass
class Cost:
    tokens: int = 0
    tool_calls: int = 0
    steps: int = 0

    def total(
        self,
        token_weight: float = 1e-4,
        tool_weight: float = 0.1,
        step_weight: float = 0.05,
    ) -> float:
        return (
            self.tokens * token_weight
            + self.tool_calls * tool_weight
            + self.steps * step_weight
        )


@dataclass
class WorkerDelta:
    reasoning_delta: str
    new_evidence: list[str]
    updated_candidate_answer: str
    confidence: float
    key_risk: str
    proposed_next_step: str
    should_request_split: bool
    suggested_split_modes: list[BranchMode]
    tokens_used: int = 0


@dataclass
class BranchState:
    branch_id: str
    parent_id: str | None
    depth: int
    mode: BranchMode
    trace: list[dict[str, Any]] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    subgoals: list[str] = field(default_factory=list)
    tool_results: list[str] = field(default_factory=list)
    candidate_answer: str = ""
    confidence: float = 0.0
    main_risk: str = ""
    next_best_action: str = ""
    cost: Cost = field(default_factory=Cost)
    status: str = "active"
    stagnation_count: int = 0


@dataclass
class BranchSummary:
    branch_id: str
    mode: BranchMode
    current_hypothesis: str
    best_candidate_answer: str
    evidence_found: list[str]
    open_questions: list[str]
    failure_mode: str
    progress_score: float
    confidence: float
    novelty: float
    expected_remaining_steps: int
    recommended_next_action: str
    recommended_split_modes: list[BranchMode]


@dataclass
class Action:
    type: ActionType
    branch_id: str
    mode1: BranchMode | None = None
    mode2: BranchMode | None = None
    reason: str = ""
    expected_gain: float = 0.0
    expected_cost: float = 0.0


@dataclass
class VerifierResult:
    score: float
    passed: bool
    reasons: list[str]
    remaining_gaps: list[str] = field(default_factory=list)
    finalization_risk: str = "medium"


@dataclass
class SearchConfig:
    max_active_branches: int = 4
    max_depth: int = 5
    max_total_steps: int = 16
    finalize_threshold: float = 0.90
    split_threshold: float = 0.62
    stop_threshold: float = 0.18
    stagnation_limit: int = 2
    token_weight: float = 1e-4
    tool_weight: float = 0.1
    step_weight: float = 0.05


@dataclass
class SearchEvent:
    timestamp: float
    action: dict[str, Any]
    branch_snapshots: list[dict[str, Any]]
    summaries: dict[str, dict[str, Any]]
    values: dict[str, float]
    final_reward: float | None = None

    @classmethod
    def now(
        cls,
        action: Action | dict[str, Any],
        branches: list[BranchState],
        summaries: dict[str, BranchSummary],
        values: dict[str, float],
        final_reward: float | None = None,
    ) -> "SearchEvent":
        action_data = asdict(action) if isinstance(action, Action) else action
        return cls(
            timestamp=time.time(),
            action=action_data,
            branch_snapshots=[snapshot_branch(branch) for branch in branches],
            summaries={key: asdict(value) for key, value in summaries.items()},
            values=values.copy(),
            final_reward=final_reward,
        )


def snapshot_branch(branch: BranchState) -> dict[str, Any]:
    return {
        "branch_id": branch.branch_id,
        "parent_id": branch.parent_id,
        "depth": branch.depth,
        "mode": branch.mode.value,
        "candidate_answer": branch.candidate_answer,
        "confidence": branch.confidence,
        "main_risk": branch.main_risk,
        "next_best_action": branch.next_best_action,
        "status": branch.status,
        "cost": asdict(branch.cost),
        "stagnation_count": branch.stagnation_count,
    }
