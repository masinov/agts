from __future__ import annotations

from typing import Any, Protocol
import asyncio

from agts.jsonutil import parse_json_object


class LLMAdapter(Protocol):
    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        schema_hint: dict[str, Any],
    ) -> dict[str, Any]:
        ...


class ClaudeSDKAdapter:
    """Synchronous wrapper around the Claude Agent SDK query API."""

    def __init__(
        self,
        *,
        allowed_tools: list[str] | None = None,
        model: str | None = None,
        cwd: str | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        self.allowed_tools = allowed_tools or ["Read", "Glob", "Grep", "Bash"]
        self.model = model
        self.cwd = cwd
        self.timeout_seconds = timeout_seconds

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        schema_hint: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            return asyncio.run(
                asyncio.wait_for(
                    self._generate_json(system_prompt, user_prompt, schema_hint),
                    timeout=self.timeout_seconds,
                )
            )
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise RuntimeError(
                f"Claude SDK call timed out after {self.timeout_seconds:.0f}s"
            ) from exc

    async def _generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        schema_hint: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            from claude_agent_sdk import ClaudeAgentOptions, query
        except ImportError as exc:
            raise RuntimeError(
                "claude-agent-sdk is not installed. Install with `pip install -e .[claude]`."
            ) from exc

        prompt = "\n\n".join(
            [
                system_prompt,
                "Return one JSON object and no prose.",
                f"Schema hint: {schema_hint}",
                user_prompt,
            ]
        )
        options_kwargs: dict[str, Any] = {"allowed_tools": self.allowed_tools}
        if self.model:
            options_kwargs["model"] = self.model
        if self.cwd:
            options_kwargs["cwd"] = self.cwd

        chunks: list[str] = []
        async for message in query(prompt=prompt, options=ClaudeAgentOptions(**options_kwargs)):
            result = getattr(message, "result", None)
            if result:
                chunks.append(str(result))
            content = getattr(message, "content", None)
            if isinstance(content, str):
                chunks.append(content)
            elif isinstance(content, list):
                for item in content:
                    text = getattr(item, "text", None)
                    if text:
                        chunks.append(str(text))
        return parse_json_object("\n".join(chunks))


class DryRunAdapter:
    """Deterministic adapter for smoke tests without a model backend."""

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        schema_hint: dict[str, Any],
    ) -> dict[str, Any]:
        keys = set(schema_hint)
        if "reasoning_delta" in keys:
            return {
                "reasoning_delta": "Established an initial candidate and identified verification as the next useful step.",
                "new_evidence": ["dry-run placeholder evidence"],
                "updated_candidate_answer": "Dry-run candidate answer. Configure claude-agent-sdk for real branch work.",
                "confidence": 0.35,
                "key_risk": "No real model call was made.",
                "proposed_next_step": "Run with --provider claude-sdk.",
                "should_request_split": False,
                "suggested_split_modes": ["tool_verify", "counterexample_search"],
                "tokens_used": 0,
            }
        if "current_hypothesis" in keys:
            return {
                "current_hypothesis": "A placeholder dry-run branch can demonstrate orchestration.",
                "best_candidate_answer": "Dry-run candidate answer. Configure claude-agent-sdk for real branch work.",
                "evidence_found": ["dry-run placeholder evidence"],
                "open_questions": ["Replace dry-run adapter with Claude SDK."],
                "failure_mode": "No real reasoning backend.",
                "progress_score": 0.45,
                "confidence": 0.35,
                "expected_remaining_steps": 1,
                "recommended_next_action": "continue",
                "recommended_split_modes": [],
            }
        if "score" in keys:
            return {
                "score": 0.4,
                "passed": False,
                "reasons": ["dry-run verifier"],
                "remaining_gaps": ["No real verification was performed."],
                "finalization_risk": "high",
            }
        return {}
