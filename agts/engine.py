from __future__ import annotations

from dataclasses import asdict

from agts.adapters import LLMAdapter
from agts.models import (
    Action,
    ActionType,
    BranchMode,
    BranchState,
    BranchSummary,
    Cost,
    SearchConfig,
    SearchEvent,
    VerifierResult,
    WorkerDelta,
    new_id,
)
from agts.prompts import (
    SUMMARIZER_SYSTEM,
    VERIFIER_SYSTEM,
    WORKER_SYSTEM,
    summarizer_user_prompt,
    verifier_user_prompt,
    worker_user_prompt,
)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _parse_modes(values: list[object]) -> list[BranchMode]:
    allowed = {mode.value: mode for mode in BranchMode}
    modes: list[BranchMode] = []
    for value in values:
        mode = allowed.get(str(value))
        if mode is not None:
            modes.append(mode)
    return modes


def jaccard_similarity(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / max(1, len(sa | sb))


def estimate_novelty(summary: BranchSummary, other_summaries: list[BranchSummary]) -> float:
    if not other_summaries:
        return 1.0

    this_items = [summary.current_hypothesis] + summary.evidence_found + [summary.best_candidate_answer]
    similarities = []
    for other in other_summaries:
        other_items = [other.current_hypothesis] + other.evidence_found + [other.best_candidate_answer]
        similarities.append(jaccard_similarity(this_items, other_items))
    return _clamp01(1.0 - (sum(similarities) / len(similarities)))


class Worker:
    def __init__(self, llm: LLMAdapter):
        self.llm = llm

    def step(self, task: str, branch: BranchState) -> WorkerDelta:
        data = self.llm.generate_json(
            system_prompt=WORKER_SYSTEM,
            user_prompt=worker_user_prompt(task, branch),
            schema_hint={
                "reasoning_delta": "string",
                "new_evidence": ["string"],
                "updated_candidate_answer": "string",
                "confidence": "float in [0,1]",
                "key_risk": "string",
                "proposed_next_step": "string",
                "should_request_split": "bool",
                "suggested_split_modes": ["BranchMode strings"],
                "tokens_used": "int",
            },
        )
        return WorkerDelta(
            reasoning_delta=str(data.get("reasoning_delta", "")),
            new_evidence=[str(item) for item in data.get("new_evidence", [])],
            updated_candidate_answer=str(data.get("updated_candidate_answer", "")),
            confidence=_clamp01(float(data.get("confidence", 0.0))),
            key_risk=str(data.get("key_risk", "")),
            proposed_next_step=str(data.get("proposed_next_step", "")),
            should_request_split=bool(data.get("should_request_split", False)),
            suggested_split_modes=_parse_modes(data.get("suggested_split_modes", [])),
            tokens_used=int(data.get("tokens_used", data.get("tokens_used_estimate", 0))),
        )


class Summarizer:
    def __init__(self, llm: LLMAdapter):
        self.llm = llm

    def summarize(
        self,
        task: str,
        branch: BranchState,
        other_summaries: list[BranchSummary],
    ) -> BranchSummary:
        data = self.llm.generate_json(
            system_prompt=SUMMARIZER_SYSTEM,
            user_prompt=summarizer_user_prompt(task, branch),
            schema_hint={
                "current_hypothesis": "string",
                "best_candidate_answer": "string",
                "evidence_found": ["string"],
                "open_questions": ["string"],
                "failure_mode": "string",
                "progress_score": "float in [0,1]",
                "confidence": "float in [0,1]",
                "expected_remaining_steps": "int",
                "recommended_next_action": "string",
                "recommended_split_modes": ["BranchMode strings"],
            },
        )
        summary = BranchSummary(
            branch_id=branch.branch_id,
            mode=branch.mode,
            current_hypothesis=str(data.get("current_hypothesis", "")),
            best_candidate_answer=str(data.get("best_candidate_answer", branch.candidate_answer)),
            evidence_found=[str(item) for item in data.get("evidence_found", [])],
            open_questions=[str(item) for item in data.get("open_questions", [])],
            failure_mode=str(data.get("failure_mode", "")),
            progress_score=_clamp01(float(data.get("progress_score", 0.0))),
            confidence=_clamp01(float(data.get("confidence", branch.confidence))),
            novelty=0.0,
            expected_remaining_steps=max(1, int(data.get("expected_remaining_steps", 2))),
            recommended_next_action=str(data.get("recommended_next_action", "continue")),
            recommended_split_modes=_parse_modes(data.get("recommended_split_modes", [])),
        )
        summary.novelty = estimate_novelty(summary, other_summaries)
        return summary


class Verifier:
    def __init__(self, llm: LLMAdapter | None = None):
        self.llm = llm

    def verify(
        self,
        task: str,
        answer: str,
        summaries: list[BranchSummary],
    ) -> VerifierResult:
        if not answer.strip():
            return VerifierResult(score=0.0, passed=False, reasons=["empty answer"])

        if self.llm is not None:
            data = self.llm.generate_json(
                system_prompt=VERIFIER_SYSTEM,
                user_prompt=verifier_user_prompt(task, answer, [asdict(summary) for summary in summaries]),
                schema_hint={
                    "score": "float in [0,1]",
                    "passed": "bool",
                    "reasons": ["string"],
                    "remaining_gaps": ["string"],
                    "finalization_risk": "low|medium|high",
                },
            )
            score = _clamp01(float(data.get("score", 0.0)))
            return VerifierResult(
                score=score,
                passed=bool(data.get("passed", score >= 0.9)),
                reasons=[str(item) for item in data.get("reasons", [])],
                remaining_gaps=[str(item) for item in data.get("remaining_gaps", [])],
                finalization_risk=str(data.get("finalization_risk", "medium")),
            )

        sameish = sum(1 for summary in summaries if summary.best_candidate_answer.strip() == answer.strip())
        agreement_bonus = min(0.2, 0.1 * max(0, sameish - 1))
        base = 0.55 if len(answer) > 20 else 0.25
        score = _clamp01(base + agreement_bonus)
        return VerifierResult(
            score=score,
            passed=score >= 0.9,
            reasons=["heuristic verifier"],
        )


class HeuristicValueModel:
    def __init__(self, cfg: SearchConfig):
        self.cfg = cfg

    def score(self, summary: BranchSummary, branch: BranchState) -> float:
        risk = 1.0 if summary.failure_mode else 0.0
        cost = branch.cost.total(
            token_weight=self.cfg.token_weight,
            tool_weight=self.cfg.tool_weight,
            step_weight=self.cfg.step_weight,
        )
        utility = (
            0.30 * summary.progress_score
            + 0.20 * summary.confidence
            + 0.25 * summary.novelty
            + 0.10 * (1.0 / max(1, summary.expected_remaining_steps))
            + 0.05 * (1.0 if summary.best_candidate_answer else 0.0)
            - 0.15 * risk
            - 0.25 * cost
        )
        return _clamp01(utility)


class RuleBasedSupervisor:
    def __init__(self, cfg: SearchConfig):
        self.cfg = cfg

    def choose_action(
        self,
        task: str,
        branches: list[BranchState],
        summaries: dict[str, BranchSummary],
        values: dict[str, float],
        verifier: Verifier,
        total_steps_used: int,
    ) -> Action:
        summary_list = list(summaries.values())
        best_finalize_id: str | None = None
        best_finalize_score = -1.0

        for branch in branches:
            result = verifier.verify(task, branch.candidate_answer, summary_list)
            if result.score > best_finalize_score:
                best_finalize_id = branch.branch_id
                best_finalize_score = result.score

        if best_finalize_id and best_finalize_score >= self.cfg.finalize_threshold:
            return Action(
                type=ActionType.FINALIZE,
                branch_id=best_finalize_id,
                reason=f"verifier score {best_finalize_score:.2f} above threshold",
            )

        stoppable: list[tuple[str, str]] = []
        for branch in branches:
            value = values[branch.branch_id]
            if branch.depth >= self.cfg.max_depth:
                stoppable.append((branch.branch_id, "max depth"))
            elif branch.stagnation_count >= self.cfg.stagnation_limit:
                stoppable.append((branch.branch_id, "stagnation"))
            elif value < self.cfg.stop_threshold:
                stoppable.append((branch.branch_id, f"low utility {value:.2f}"))

        if stoppable and len(branches) > 1:
            branch_id, reason = sorted(stoppable, key=lambda item: values.get(item[0], 0.0))[0]
            return Action(type=ActionType.STOP, branch_id=branch_id, reason=reason)

        if len(branches) < self.cfg.max_active_branches:
            split_candidates: list[tuple[BranchState, BranchSummary, float]] = []
            for branch in branches:
                summary = summaries[branch.branch_id]
                value = values[branch.branch_id]
                high_open = len(summary.open_questions) >= 2
                wants_split = len(summary.recommended_split_modes) >= 2
                if value >= self.cfg.split_threshold and (high_open or wants_split):
                    split_candidates.append((branch, summary, value))
            if split_candidates:
                branch, summary, value = sorted(split_candidates, key=lambda item: item[2], reverse=True)[0]
                modes = summary.recommended_split_modes[:2]
                if len(modes) < 2:
                    modes = [BranchMode.TOOL_VERIFY, BranchMode.COUNTEREXAMPLE_SEARCH]
                return Action(
                    type=ActionType.SPLIT,
                    branch_id=branch.branch_id,
                    mode1=modes[0],
                    mode2=modes[1],
                    reason=f"high utility {value:.2f} with unresolved uncertainty",
                    expected_gain=value,
                    expected_cost=0.1,
                )

        best = max(branches, key=lambda branch: values[branch.branch_id])
        return Action(
            type=ActionType.CONTINUE,
            branch_id=best.branch_id,
            reason=f"best utility {values[best.branch_id]:.2f}",
            expected_gain=values[best.branch_id],
            expected_cost=0.05,
        )


def apply_worker_delta(branch: BranchState, delta: WorkerDelta) -> BranchState:
    prev_answer = branch.candidate_answer.strip()
    new_answer = delta.updated_candidate_answer.strip()
    branch.stagnation_count = 0 if new_answer and new_answer != prev_answer else branch.stagnation_count + 1

    branch.trace.append(
        {
            "reasoning_delta": delta.reasoning_delta,
            "new_evidence": delta.new_evidence,
            "updated_candidate_answer": delta.updated_candidate_answer,
            "confidence": delta.confidence,
            "key_risk": delta.key_risk,
            "proposed_next_step": delta.proposed_next_step,
            "should_request_split": delta.should_request_split,
            "suggested_split_modes": [mode.value for mode in delta.suggested_split_modes],
        }
    )
    branch.facts.extend(delta.new_evidence)
    branch.candidate_answer = delta.updated_candidate_answer or branch.candidate_answer
    branch.confidence = delta.confidence
    branch.main_risk = delta.key_risk
    branch.next_best_action = delta.proposed_next_step
    branch.cost.tokens += delta.tokens_used
    branch.cost.steps += 1
    return branch


def split_branch(parent: BranchState, mode1: BranchMode, mode2: BranchMode) -> tuple[BranchState, BranchState]:
    def make_child(mode: BranchMode) -> BranchState:
        return BranchState(
            branch_id=new_id("b"),
            parent_id=parent.branch_id,
            depth=parent.depth + 1,
            mode=mode,
            trace=list(parent.trace),
            facts=list(parent.facts),
            assumptions=list(parent.assumptions),
            subgoals=list(parent.subgoals),
            tool_results=list(parent.tool_results),
            candidate_answer=parent.candidate_answer,
            confidence=parent.confidence,
            main_risk=parent.main_risk,
            next_best_action=parent.next_best_action,
            cost=Cost(
                tokens=parent.cost.tokens,
                tool_calls=parent.cost.tool_calls,
                steps=parent.cost.steps,
            ),
            status="active",
            stagnation_count=0,
        )

    return make_child(mode1), make_child(mode2)


class TreeOfThoughtEngine:
    def __init__(
        self,
        worker: Worker,
        summarizer: Summarizer,
        supervisor: RuleBasedSupervisor,
        verifier: Verifier,
        value_model: HeuristicValueModel,
        cfg: SearchConfig,
    ) -> None:
        self.worker = worker
        self.summarizer = summarizer
        self.supervisor = supervisor
        self.verifier = verifier
        self.value_model = value_model
        self.cfg = cfg

    @classmethod
    def from_adapter(cls, adapter: LLMAdapter, cfg: SearchConfig | None = None) -> "TreeOfThoughtEngine":
        config = cfg or SearchConfig()
        return cls(
            worker=Worker(adapter),
            summarizer=Summarizer(adapter),
            supervisor=RuleBasedSupervisor(config),
            verifier=Verifier(adapter),
            value_model=HeuristicValueModel(config),
            cfg=config,
        )

    def _init_branch(self) -> BranchState:
        return BranchState(
            branch_id=new_id("b"),
            parent_id=None,
            depth=0,
            mode=BranchMode.DIRECT_SOLVE,
        )

    def solve(self, task: str) -> tuple[str, list[SearchEvent], list[BranchState]]:
        branches = [self._init_branch()]
        events: list[SearchEvent] = []
        total_steps_used = 0

        while branches and total_steps_used < self.cfg.max_total_steps:
            summaries = self._summarize_branches(task, branches)
            values = {
                branch.branch_id: self.value_model.score(summaries[branch.branch_id], branch)
                for branch in branches
            }
            action = self.supervisor.choose_action(
                task=task,
                branches=branches,
                summaries=summaries,
                values=values,
                verifier=self.verifier,
                total_steps_used=total_steps_used,
            )
            events.append(SearchEvent.now(action, branches, summaries, values))

            if action.type == ActionType.FINALIZE:
                target = self._get_branch(branches, action.branch_id)
                result = self.verifier.verify(task, target.candidate_answer, list(summaries.values()))
                events[-1].final_reward = result.score
                return target.candidate_answer, events, branches

            if action.type == ActionType.STOP:
                target = self._get_branch(branches, action.branch_id)
                target.status = "stopped"
                branches = [branch for branch in branches if branch.branch_id != action.branch_id]
                continue

            if action.type == ActionType.SPLIT:
                parent = self._get_branch(branches, action.branch_id)
                if action.mode1 is None or action.mode2 is None:
                    action.mode1 = BranchMode.TOOL_VERIFY
                    action.mode2 = BranchMode.COUNTEREXAMPLE_SEARCH
                child1, child2 = split_branch(parent, action.mode1, action.mode2)
                parent.status = "split"
                branches = [branch for branch in branches if branch.branch_id != parent.branch_id]
                branches.extend([child1, child2])
                continue

            if action.type == ActionType.CONTINUE:
                target = self._get_branch(branches, action.branch_id)
                apply_worker_delta(target, self.worker.step(task, target))
                total_steps_used += 1

        return self._forced_finalize(task, branches, events)

    def _summarize_branches(
        self,
        task: str,
        branches: list[BranchState],
    ) -> dict[str, BranchSummary]:
        summaries: dict[str, BranchSummary] = {}
        for branch in branches:
            summary = self.summarizer.summarize(task, branch, list(summaries.values()))
            summaries[branch.branch_id] = summary
        return summaries

    def _forced_finalize(
        self,
        task: str,
        branches: list[BranchState],
        events: list[SearchEvent],
    ) -> tuple[str, list[SearchEvent], list[BranchState]]:
        if not branches:
            return "", events, branches

        summaries = self._summarize_branches(task, branches)
        values = {
            branch.branch_id: self.value_model.score(summaries[branch.branch_id], branch)
            for branch in branches
        }
        best = max(
            branches,
            key=lambda branch: (
                self.verifier.verify(task, branch.candidate_answer, list(summaries.values())).score,
                values[branch.branch_id],
            ),
        )
        result = self.verifier.verify(task, best.candidate_answer, list(summaries.values()))
        events.append(
            SearchEvent.now(
                {"type": ActionType.FORCED_FINALIZE.value, "branch_id": best.branch_id},
                branches,
                summaries,
                values,
                final_reward=result.score,
            )
        )
        return best.candidate_answer, events, branches

    @staticmethod
    def _get_branch(branches: list[BranchState], branch_id: str) -> BranchState:
        for branch in branches:
            if branch.branch_id == branch_id:
                return branch
        raise KeyError(f"unknown branch: {branch_id}")
