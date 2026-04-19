## 1) Core data structures

```python
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
import json
import math
import time
import uuid


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"
```

```python
class BranchMode(str, Enum):
    DIRECT_SOLVE = "direct_solve"
    DECOMPOSE = "decompose"
    INDEPENDENT_RE_DERIVE = "independent_rederive"
    TOOL_VERIFY = "tool_verify"
    COUNTEREXAMPLE_SEARCH = "counterexample_search"
    ASSUMPTION_STRESS_TEST = "assumption_stress_test"
    COMPRESS_AND_FINALIZE = "compress_and_finalize"
```

```python
class ActionType(str, Enum):
    CONTINUE = "continue"
    SPLIT = "split"
    STOP = "stop"
    FINALIZE = "finalize"
```

```python
@dataclass
class Cost:
    tokens: int = 0
    tool_calls: int = 0
    steps: int = 0

    def total(self, token_weight: float = 1e-4, tool_weight: float = 0.1, step_weight: float = 0.05) -> float:
        return (
            self.tokens * token_weight
            + self.tool_calls * tool_weight
            + self.steps * step_weight
        )
```

```python
@dataclass
class WorkerDelta:
    reasoning_delta: str
    new_evidence: List[str]
    updated_candidate_answer: str
    confidence: float
    key_risk: str
    proposed_next_step: str
    should_request_split: bool
    suggested_split_modes: List[BranchMode]
    tokens_used: int = 0
```

```python
@dataclass
class BranchState:
    branch_id: str
    parent_id: Optional[str]
    depth: int
    mode: BranchMode
    trace: List[Dict[str, Any]] = field(default_factory=list)
    facts: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    subgoals: List[str] = field(default_factory=list)
    tool_results: List[str] = field(default_factory=list)
    candidate_answer: str = ""
    confidence: float = 0.0
    main_risk: str = ""
    next_best_action: str = ""
    cost: Cost = field(default_factory=Cost)
    status: str = "active"
    stagnation_count: int = 0
```

```python
@dataclass
class BranchSummary:
    branch_id: str
    mode: BranchMode
    current_hypothesis: str
    best_candidate_answer: str
    evidence_found: List[str]
    open_questions: List[str]
    failure_mode: str
    progress_score: float
    confidence: float
    novelty: float
    expected_remaining_steps: int
    recommended_next_action: str
    recommended_split_modes: List[BranchMode]
```

```python
@dataclass
class Action:
    type: ActionType
    branch_id: str
    mode1: Optional[BranchMode] = None
    mode2: Optional[BranchMode] = None
    reason: str = ""
```

```python
@dataclass
class VerifierResult:
    score: float
    passed: bool
    reasons: List[str]
```

```python
@dataclass
class SearchConfig:
    max_active_branches: int = 4
    max_depth: int = 6
    max_total_steps: int = 20
    finalize_threshold: float = 0.9
    split_threshold: float = 0.65
    stop_threshold: float = 0.20
    stagnation_limit: int = 2
    token_weight: float = 1e-4
    tool_weight: float = 0.1
    step_weight: float = 0.05
```

---

## 2) Model adapter

This lets you swap in Minimax 2.7 or any other model backend.

```python
class LLMAdapter:
    def generate_json(self, system_prompt: str, user_prompt: str, schema_hint: Dict[str, Any]) -> Dict[str, Any]:
        """
        Replace this with your actual inference call.
        Must return a parsed JSON dict matching the requested shape.
        """
        raise NotImplementedError
```

For an MVP, keep everything JSON-constrained.

---

## 3) Prompt templates

### Worker prompt

```python
WORKER_SYSTEM = """
You are a local reasoning worker inside a branching deliberation system.
You do NOT decide globally.
You only advance your assigned branch by one step.
Return JSON only.
""".strip()
```

```python
def worker_user_prompt(task: str, branch: BranchState) -> str:
    return f"""
Task:
{task}

Branch mode:
{branch.mode.value}

Branch state:
{json.dumps({
    "branch_id": branch.branch_id,
    "depth": branch.depth,
    "candidate_answer": branch.candidate_answer,
    "facts": branch.facts[-8:],
    "assumptions": branch.assumptions[-8:],
    "subgoals": branch.subgoals[-8:],
    "tool_results": branch.tool_results[-5:],
    "confidence": branch.confidence,
    "main_risk": branch.main_risk,
    "next_best_action": branch.next_best_action,
    "recent_trace": branch.trace[-3:]
}, ensure_ascii=False, indent=2)}

Advance this branch by one useful step.
Be concise but substantive.
Return JSON with keys:
reasoning_delta, new_evidence, updated_candidate_answer, confidence,
key_risk, proposed_next_step, should_request_split, suggested_split_modes, tokens_used
""".strip()
```

### Summarizer prompt

```python
SUMMARIZER_SYSTEM = """
You summarize a branch for a supervisor.
The summary must be compact, structured, and decision-relevant.
Return JSON only.
""".strip()
```

```python
def summarizer_user_prompt(task: str, branch: BranchState) -> str:
    return f"""
Task:
{task}

Full branch state:
{json.dumps({
    "mode": branch.mode.value,
    "trace": branch.trace[-6:],
    "facts": branch.facts[-10:],
    "assumptions": branch.assumptions[-10:],
    "subgoals": branch.subgoals[-10:],
    "tool_results": branch.tool_results[-5:],
    "candidate_answer": branch.candidate_answer,
    "confidence": branch.confidence,
    "main_risk": branch.main_risk,
    "next_best_action": branch.next_best_action,
    "cost": asdict(branch.cost),
    "stagnation_count": branch.stagnation_count
}, ensure_ascii=False, indent=2)}

Return JSON with keys:
current_hypothesis, best_candidate_answer, evidence_found, open_questions,
failure_mode, progress_score, confidence, expected_remaining_steps,
recommended_next_action, recommended_split_modes
""".strip()
```

---

## 4) Novelty estimation

A simple placeholder is enough to start. Later you can replace this with embedding similarity.

```python
def jaccard_similarity(a: List[str], b: List[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / max(1, len(sa | sb))
```

```python
def estimate_novelty(summary: BranchSummary, other_summaries: List[BranchSummary]) -> float:
    if not other_summaries:
        return 1.0

    sims = []
    this_items = [summary.current_hypothesis] + summary.evidence_found + [summary.best_candidate_answer]
    for other in other_summaries:
        other_items = [other.current_hypothesis] + other.evidence_found + [other.best_candidate_answer]
        sims.append(jaccard_similarity(this_items, other_items))

    mean_sim = sum(sims) / len(sims)
    return max(0.0, min(1.0, 1.0 - mean_sim))
```

---

## 5) Worker and summarizer wrappers

```python
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
                "tokens_used": "int"
            }
        )

        modes = [BranchMode(m) for m in data.get("suggested_split_modes", []) if m in set(m.value for m in BranchMode)]
        return WorkerDelta(
            reasoning_delta=data["reasoning_delta"],
            new_evidence=data.get("new_evidence", []),
            updated_candidate_answer=data.get("updated_candidate_answer", ""),
            confidence=float(data.get("confidence", 0.0)),
            key_risk=data.get("key_risk", ""),
            proposed_next_step=data.get("proposed_next_step", ""),
            should_request_split=bool(data.get("should_request_split", False)),
            suggested_split_modes=modes,
            tokens_used=int(data.get("tokens_used", 0)),
        )
```

```python
class Summarizer:
    def __init__(self, llm: LLMAdapter):
        self.llm = llm

    def summarize(self, task: str, branch: BranchState, other_summaries: List[BranchSummary]) -> BranchSummary:
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
                "recommended_split_modes": ["BranchMode strings"]
            }
        )

        prelim = BranchSummary(
            branch_id=branch.branch_id,
            mode=branch.mode,
            current_hypothesis=data.get("current_hypothesis", ""),
            best_candidate_answer=data.get("best_candidate_answer", branch.candidate_answer),
            evidence_found=data.get("evidence_found", []),
            open_questions=data.get("open_questions", []),
            failure_mode=data.get("failure_mode", ""),
            progress_score=float(data.get("progress_score", 0.0)),
            confidence=float(data.get("confidence", branch.confidence)),
            novelty=0.0,
            expected_remaining_steps=int(data.get("expected_remaining_steps", 2)),
            recommended_next_action=data.get("recommended_next_action", "continue"),
            recommended_split_modes=[
                BranchMode(m)
                for m in data.get("recommended_split_modes", [])
                if m in set(mode.value for mode in BranchMode)
            ],
        )
        prelim.novelty = estimate_novelty(prelim, other_summaries)
        return prelim
```

---

## 6) Verifier

Start simple. You can later replace this with a learned verifier or task-specific one.

```python
class Verifier:
    def verify(self, task: str, answer: str, summaries: List[BranchSummary]) -> VerifierResult:
        """
        Replace with exact checks, tool execution, agreement checks, etc.
        """
        if not answer.strip():
            return VerifierResult(score=0.0, passed=False, reasons=["empty answer"])

        agreement_bonus = 0.0
        if summaries:
            sameish = sum(1 for s in summaries if s.best_candidate_answer.strip() == answer.strip())
            agreement_bonus = min(0.2, 0.1 * max(0, sameish - 1))

        base = 0.55 if len(answer) > 20 else 0.25
        score = max(0.0, min(1.0, base + agreement_bonus))
        return VerifierResult(
            score=score,
            passed=score >= 0.9,
            reasons=["placeholder verifier"]
        )
```

---

## 7) Branch utility model

Start heuristic. Later this becomes your learned (Q_\psi).

```python
class HeuristicValueModel:
    def __init__(self, cfg: SearchConfig):
        self.cfg = cfg

    def score(self, summary: BranchSummary, branch: BranchState) -> float:
        risk = 1.0 if summary.failure_mode else 0.0
        cost = branch.cost.total(
            token_weight=self.cfg.token_weight,
            tool_weight=self.cfg.tool_weight,
            step_weight=self.cfg.step_weight
        )

        u = (
            0.30 * summary.progress_score
            + 0.20 * summary.confidence
            + 0.25 * summary.novelty
            + 0.10 * (1.0 / max(1, summary.expected_remaining_steps))
            + 0.05 * (1.0 if summary.best_candidate_answer else 0.0)
            - 0.15 * risk
            - 0.25 * cost
        )
        return max(0.0, min(1.0, u))
```

---

## 8) Rule-based supervisor

This is the right first controller.

```python
class RuleBasedSupervisor:
    def __init__(self, cfg: SearchConfig):
        self.cfg = cfg

    def choose_action(
        self,
        task: str,
        branches: List[BranchState],
        summaries: Dict[str, BranchSummary],
        values: Dict[str, float],
        verifier: Verifier,
        total_steps_used: int,
    ) -> Action:
        # 1. Try finalize
        best_finalize_id = None
        best_finalize_score = -1.0

        summary_list = list(summaries.values())
        for b in branches:
            vr = verifier.verify(task, b.candidate_answer, summary_list)
            if vr.score > best_finalize_score:
                best_finalize_score = vr.score
                best_finalize_id = b.branch_id

        if best_finalize_score >= self.cfg.finalize_threshold and best_finalize_id is not None:
            return Action(
                type=ActionType.FINALIZE,
                branch_id=best_finalize_id,
                reason=f"verifier score {best_finalize_score:.2f} above threshold"
            )

        # 2. Stop dominated or stagnant branches
        stoppable = []
        for b in branches:
            s = summaries[b.branch_id]
            v = values[b.branch_id]
            if b.depth >= self.cfg.max_depth:
                stoppable.append((b.branch_id, "max depth"))
            elif b.stagnation_count >= self.cfg.stagnation_limit:
                stoppable.append((b.branch_id, "stagnation"))
            elif v < self.cfg.stop_threshold:
                stoppable.append((b.branch_id, f"low utility {v:.2f}"))

        if len(stoppable) > 0 and len(branches) > 1:
            bid, reason = sorted(stoppable, key=lambda x: values.get(x[0], 0.0))[0]
            return Action(type=ActionType.STOP, branch_id=bid, reason=reason)

        # 3. Consider split on best promising uncertain branch
        if len(branches) < self.cfg.max_active_branches:
            split_candidates = []
            for b in branches:
                s = summaries[b.branch_id]
                v = values[b.branch_id]
                high_open = len(s.open_questions) >= 2
                wants_split = len(s.recommended_split_modes) >= 2
                if v >= self.cfg.split_threshold and (high_open or wants_split):
                    split_candidates.append((b, s, v))

            if split_candidates:
                b, s, v = sorted(split_candidates, key=lambda t: t[2], reverse=True)[0]
                modes = s.recommended_split_modes[:2]
                if len(modes) < 2:
                    modes = [BranchMode.TOOL_VERIFY, BranchMode.COUNTEREXAMPLE_SEARCH]
                return Action(
                    type=ActionType.SPLIT,
                    branch_id=b.branch_id,
                    mode1=modes[0],
                    mode2=modes[1],
                    reason=f"high utility {v:.2f} with unresolved uncertainty"
                )

        # 4. Otherwise continue best branch
        best_branch = max(branches, key=lambda b: values[b.branch_id])
        return Action(
            type=ActionType.CONTINUE,
            branch_id=best_branch.branch_id,
            reason=f"best utility {values[best_branch.branch_id]:.2f}"
        )
```

---

## 9) Branch transitions

```python
def apply_worker_delta(branch: BranchState, delta: WorkerDelta) -> BranchState:
    prev_answer = branch.candidate_answer.strip()
    new_answer = delta.updated_candidate_answer.strip()

    if new_answer and new_answer != prev_answer:
        branch.stagnation_count = 0
    else:
        branch.stagnation_count += 1

    branch.trace.append({
        "reasoning_delta": delta.reasoning_delta,
        "new_evidence": delta.new_evidence,
        "updated_candidate_answer": delta.updated_candidate_answer,
        "confidence": delta.confidence,
        "key_risk": delta.key_risk,
        "proposed_next_step": delta.proposed_next_step,
        "should_request_split": delta.should_request_split,
        "suggested_split_modes": [m.value for m in delta.suggested_split_modes],
    })

    branch.facts.extend(delta.new_evidence)
    branch.candidate_answer = delta.updated_candidate_answer or branch.candidate_answer
    branch.confidence = delta.confidence
    branch.main_risk = delta.key_risk
    branch.next_best_action = delta.proposed_next_step
    branch.cost.tokens += delta.tokens_used
    branch.cost.steps += 1

    return branch
```

```python
def split_branch(parent: BranchState, mode1: BranchMode, mode2: BranchMode) -> Tuple[BranchState, BranchState]:
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
```

---

## 10) Trace logging

You will need this for offline training.

```python
@dataclass
class SearchEvent:
    timestamp: float
    action: Dict[str, Any]
    branch_snapshots: List[Dict[str, Any]]
    summaries: Dict[str, Dict[str, Any]]
    values: Dict[str, float]
    final_reward: Optional[float] = None
```

```python
def snapshot_branch(branch: BranchState) -> Dict[str, Any]:
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
```

---

## 11) The search engine

```python
class TreeOfThoughtEngine:
    def __init__(
        self,
        worker: Worker,
        summarizer: Summarizer,
        supervisor: RuleBasedSupervisor,
        verifier: Verifier,
        value_model: HeuristicValueModel,
        cfg: SearchConfig,
    ):
        self.worker = worker
        self.summarizer = summarizer
        self.supervisor = supervisor
        self.verifier = verifier
        self.value_model = value_model
        self.cfg = cfg

    def _init_branch(self) -> BranchState:
        return BranchState(
            branch_id=new_id("b"),
            parent_id=None,
            depth=0,
            mode=BranchMode.DIRECT_SOLVE,
        )

    def solve(self, task: str) -> Tuple[str, List[SearchEvent]]:
        branches: List[BranchState] = [self._init_branch()]
        events: List[SearchEvent] = []
        total_steps_used = 0

        while branches and total_steps_used < self.cfg.max_total_steps:
            # Summarize in sequence so novelty can use prior summaries
            summaries: Dict[str, BranchSummary] = {}
            ordered = []
            for b in branches:
                prev_summaries = list(summaries.values())
                s = self.summarizer.summarize(task, b, prev_summaries)
                summaries[b.branch_id] = s
                ordered.append(s)

            values = {
                b.branch_id: self.value_model.score(summaries[b.branch_id], b)
                for b in branches
            }

            action = self.supervisor.choose_action(
                task=task,
                branches=branches,
                summaries=summaries,
                values=values,
                verifier=self.verifier,
                total_steps_used=total_steps_used,
            )

            events.append(SearchEvent(
                timestamp=time.time(),
                action=asdict(action),
                branch_snapshots=[snapshot_branch(b) for b in branches],
                summaries={k: asdict(v) for k, v in summaries.items()},
                values=values.copy(),
            ))

            if action.type == ActionType.FINALIZE:
                target = next(b for b in branches if b.branch_id == action.branch_id)
                answer = target.candidate_answer
                vr = self.verifier.verify(task, answer, list(summaries.values()))
                events[-1].final_reward = vr.score
                return answer, events

            elif action.type == ActionType.STOP:
                for b in branches:
                    if b.branch_id == action.branch_id:
                        b.status = "stopped"
                branches = [b for b in branches if b.branch_id != action.branch_id]

            elif action.type == ActionType.SPLIT:
                parent = next(b for b in branches if b.branch_id == action.branch_id)
                c1, c2 = split_branch(parent, action.mode1, action.mode2)
                parent.status = "split"
                branches = [b for b in branches if b.branch_id != parent.branch_id]
                branches.extend([c1, c2])

            elif action.type == ActionType.CONTINUE:
                target = next(b for b in branches if b.branch_id == action.branch_id)
                delta = self.worker.step(task, target)
                apply_worker_delta(target, delta)
                total_steps_used += 1

        # Forced finalize
        if not branches:
            return "", events

        # Pick best by verifier first, utility second
        summaries = {}
        for b in branches:
            summaries[b.branch_id] = self.summarizer.summarize(task, b, list(summaries.values()))
        best = max(
            branches,
            key=lambda b: (
                self.verifier.verify(task, b.candidate_answer, list(summaries.values())).score,
                self.value_model.score(summaries[b.branch_id], b)
            )
        )
        answer = best.candidate_answer
        vr = self.verifier.verify(task, answer, list(summaries.values()))
        events.append(SearchEvent(
            timestamp=time.time(),
            action={"type": "forced_finalize", "branch_id": best.branch_id},
            branch_snapshots=[snapshot_branch(b) for b in branches],
            summaries={k: asdict(v) for k, v in summaries.items()},
            values={b.branch_id: self.value_model.score(summaries[b.branch_id], b) for b in branches},
            final_reward=vr.score,
        ))
        return answer, events
```

---

## 12) Training data extraction

This is how you bootstrap self-supervision from logs.

### A. Branch value targets

For each event and branch summary at that event, use the final reward as a weak target.

You can improve it later with better credit assignment.

```python
def extract_value_training_rows(task: str, events: List[SearchEvent]) -> List[Dict[str, Any]]:
    if not events:
        return []

    final_reward = events[-1].final_reward if events[-1].final_reward is not None else 0.0
    rows = []
    for ev in events:
        for branch_id, summary in ev.summaries.items():
            rows.append({
                "task": task,
                "branch_id": branch_id,
                "summary": summary,
                "remaining_horizon": len(events),
                "target_value": final_reward,
            })
    return rows
```

### B. Supervisor imitation targets

```python
def extract_supervisor_training_rows(task: str, events: List[SearchEvent]) -> List[Dict[str, Any]]:
    rows = []
    for ev in events:
        rows.append({
            "task": task,
            "branch_summaries": ev.summaries,
            "values": ev.values,
            "action": ev.action,
            "final_reward": ev.final_reward,
        })
    return rows
```

---

## 13) Better credit assignment later

Once the MVP works, replace “every branch gets final reward” with something better.

A simple branch credit rule:

* winning finalized branch: (+1.0)
* branch that produced evidence reused by winner: (+0.5)
* branch that exposed a flaw in another branch: (+0.3)
* expensive dead end: (-0.2)
* branch stopped due to redundancy: (0.0)

You can operationalize this from trace overlap and branch lineage.

An approximate labeler:

```python
def assign_branch_credit(events: List[SearchEvent]) -> Dict[str, float]:
    credits: Dict[str, float] = {}
    if not events:
        return credits

    last = events[-1]
    final_branch = last.action.get("branch_id")

    all_branch_ids = set()
    for ev in events:
        all_branch_ids.update(ev.summaries.keys())

    for bid in all_branch_ids:
        credits[bid] = -0.05  # mild default penalty for compute use

    if final_branch:
        credits[final_branch] = 1.0

    return credits
```

Later you will want lineage-aware and evidence-aware credit.

---

## 14) The supervisor learning upgrade path

Once you have logs, train three models.

### Value model (Q_\psi)

Input:

* task embedding
* branch summary
* remaining budget

Output:

* expected utility

### Split model

Input:

* task embedding
* branch summary

Output:

* best two child modes

### Action policy

Input:

* set of branch summaries and values
* budget state

Output:

* continue / split / stop / finalize

You do not need online RL first. Start with:

1. behavior cloning from the rule policy
2. relabel good and bad actions using final reward
3. train a better policy offline

---

## 15) Most important practical constraints

For a cheap model, keep these hard constraints:

```python
cfg = SearchConfig(
    max_active_branches=4,
    max_depth=5,
    max_total_steps=16,
    finalize_threshold=0.90,
    split_threshold=0.62,
    stop_threshold=0.18,
    stagnation_limit=2,
)
```

That is enough to get useful behavior without branch explosion.

---

## 16) What to improve first after the MVP

In order:

1. **Verifier**
   Better correctness signal gives the biggest gain.

2. **Novelty estimator**
   Prevents duplicated branches.

3. **Value model**
   Makes stopping and splitting smarter.

4. **Mode-specific prompts**
   Strongly affects branch diversity.

5. **Credit assignment**
   Helps learning, but only after traces are decent.

---

## 17) A clean minimal run loop in words

The system now works like this:

1. Create one branch.
2. Worker advances one branch at a time.
3. Summarizer compresses each branch.
4. Supervisor scores the active set.
5. Supervisor either:

   * continues the best branch
   * splits a promising but uncertain branch
   * stops a weak or stagnant branch
   * finalizes a verified branch
6. Everything is logged.
7. Logged traces become self-supervised training data.

That is the whole prototype.

---

## 18) The key conceptual shift

The branch summary is your real control state.

Not the raw chain-of-thought.
Not the final answer.
Not the worker confidence alone.

What matters is whether the summary predicts:

* future solvability
* unresolved uncertainty
* diversity value
* expected remaining cost

That is the core of the architecture.

---
