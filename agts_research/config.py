from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json

from agts_research.models import LocalAgtsConfig


@dataclass
class ResearchTaskConfig:
    name: str
    description: str
    objective: str = "maximize score"


@dataclass
class ResearchWorkspaceConfig:
    seed_path: str = "."
    results_dir: str = ".research/runs"
    setup: list[str] = field(default_factory=list)


@dataclass
class ResearchEvaluatorConfig:
    type: str = "none"  # none | command
    command: str = ""
    final_command: str = ""
    timeout: int = 300
    direction: str = "maximize"  # maximize | minimize
    private_paths: list[str] = field(default_factory=list)
    holdout_paths: list[str] = field(default_factory=list)


@dataclass
class ResearchAgentsConfig:
    runtime: str = "claude_code"
    model: str = "minimax-2.7"
    max_agents: int = 1
    max_turns: int = 200
    launch: bool = False
    sandbox: bool = True
    sandbox_backend: str = "bwrap"
    roles: list[str] = field(default_factory=lambda: ["research_worker"])


@dataclass
class ResearchSearchConfig:
    max_branches: int = 6
    max_active_branches: int = 4
    max_agents_per_branch: int = 1
    max_evals: int = 40
    max_agent_turns: int = 0
    max_wall_seconds: float = 0.0
    split_threshold: float = 0.68
    stop_threshold: float = 0.20
    verify_before_finalize: bool = True


@dataclass
class ResearchHeartbeatConfig:
    reflect_every: int = 1
    consolidate_every: int = 5
    pivot_after_stall: int = 3
    trigger_registry: dict[str, str] = field(
        default_factory=lambda: {
            "baseline": "first evaluated attempt for a branch",
            "stall_or_pivot": "branch is stalled or selected for pivot analysis",
            "split_followup": "new branch created from a split action",
            "verification": "branch is near finalization or needs verifier attention",
            "continue": "ordinary branch-local research step",
        }
    )


@dataclass
class ResearchConfig:
    task: ResearchTaskConfig
    workspace: ResearchWorkspaceConfig = field(default_factory=ResearchWorkspaceConfig)
    evaluator: ResearchEvaluatorConfig = field(default_factory=ResearchEvaluatorConfig)
    agents: ResearchAgentsConfig = field(default_factory=ResearchAgentsConfig)
    workers_local_agts: LocalAgtsConfig = field(default_factory=LocalAgtsConfig)
    search: ResearchSearchConfig = field(default_factory=ResearchSearchConfig)
    heartbeat: ResearchHeartbeatConfig = field(default_factory=ResearchHeartbeatConfig)
    config_path: str | None = None

    @classmethod
    def from_file(cls, path: str | Path) -> "ResearchConfig":
        path = Path(path)
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".json":
            data = json.loads(text)
        else:
            data = _load_yaml_like(text)
        cfg = cls.from_dict(data)
        cfg.config_path = str(path)
        return cfg

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResearchConfig":
        workers = data.get("workers", {})
        local_agts = workers.get("local_agts", data.get("workers_local_agts", {}))
        return cls(
            task=ResearchTaskConfig(**data["task"]),
            workspace=ResearchWorkspaceConfig(**data.get("workspace", {})),
            evaluator=ResearchEvaluatorConfig(**data.get("evaluator", {})),
            agents=ResearchAgentsConfig(**data.get("agents", {})),
            workers_local_agts=LocalAgtsConfig(**local_agts),
            search=ResearchSearchConfig(**data.get("search", {})),
            heartbeat=ResearchHeartbeatConfig(**data.get("heartbeat", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        from agts.jsonutil import to_jsonable

        return to_jsonable(self)


def load_run_config(run_dir: str | Path) -> ResearchConfig:
    return ResearchConfig.from_file(Path(run_dir) / "config.json")


def _load_yaml_like(text: str) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError:
        return _load_tiny_yaml(text)
    value = yaml.safe_load(text)
    if not isinstance(value, dict):
        raise ValueError("research config must be a mapping")
    return value


def _load_tiny_yaml(text: str) -> dict[str, Any]:
    """Small YAML subset parser for simple AGTS config files.

    Supports nested mappings by two-space indentation, scalar strings/numbers,
    booleans, and inline JSON-style lists. PyYAML is preferred when installed.
    """
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        key, sep, raw_value = line.strip().partition(":")
        if not sep:
            raise ValueError(f"unsupported config line: {raw_line}")
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if raw_value.strip() == "":
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _parse_scalar(raw_value.strip())
    return root


def _parse_scalar(value: str) -> Any:
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "None"}:
        return None
    if value.startswith("[") and value.endswith("]"):
        return json.loads(value)
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value
