from __future__ import annotations

from dataclasses import asdict
import json

from agts.jsonutil import to_jsonable
from agts.models import BranchState


WORKER_SYSTEM = """
You are a local reasoning worker inside Agentic Tree Search.
You do not decide globally.
Advance your assigned branch by one useful step.
Return JSON only.
""".strip()


SUMMARIZER_SYSTEM = """
You summarize one branch for a global supervisor.
The summary must be compact, structured, and decision-relevant.
Return JSON only.
""".strip()


VERIFIER_SYSTEM = """
You are a verifier for one branch candidate.
Do not solve the task from scratch.
Score whether the candidate is ready to finalize.
Return JSON only.
""".strip()


def worker_user_prompt(task: str, branch: BranchState) -> str:
    state = {
        "branch_id": branch.branch_id,
        "depth": branch.depth,
        "mode": branch.mode.value,
        "candidate_answer": branch.candidate_answer,
        "facts": branch.facts[-8:],
        "assumptions": branch.assumptions[-8:],
        "subgoals": branch.subgoals[-8:],
        "tool_results": branch.tool_results[-5:],
        "confidence": branch.confidence,
        "main_risk": branch.main_risk,
        "next_best_action": branch.next_best_action,
        "recent_trace": branch.trace[-3:],
    }
    return f"""
Task:
{task}

Branch state:
{json.dumps(state, ensure_ascii=False, indent=2)}

Advance this branch by one concise but substantive step.
Return JSON with keys:
reasoning_delta, new_evidence, updated_candidate_answer, confidence,
key_risk, proposed_next_step, should_request_split, suggested_split_modes,
tokens_used
""".strip()


def summarizer_user_prompt(task: str, branch: BranchState) -> str:
    state = {
        "branch_id": branch.branch_id,
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
        "stagnation_count": branch.stagnation_count,
    }
    return f"""
Task:
{task}

Full branch state:
{json.dumps(state, ensure_ascii=False, indent=2)}

Return JSON with keys:
current_hypothesis, best_candidate_answer, evidence_found, open_questions,
failure_mode, progress_score, confidence, expected_remaining_steps,
recommended_next_action, recommended_split_modes
""".strip()


def verifier_user_prompt(task: str, answer: str, summaries: list[dict[str, object]]) -> str:
    return f"""
Task:
{task}

Candidate answer:
{answer}

Available branch summaries:
{json.dumps(to_jsonable(summaries), ensure_ascii=False, indent=2)}

Return JSON with keys:
score, passed, reasons, remaining_gaps, finalization_risk
""".strip()
