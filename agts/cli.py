from __future__ import annotations

from pathlib import Path
import argparse

from agts.adapters import ClaudeSDKAdapter, DryRunAdapter
from agts.engine import TreeOfThoughtEngine
from agts.models import SearchConfig
from agts.storage import create_run_dir, write_run_artifacts
from agts_research.cli import add_research_subparser, handle_research


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agts")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Run Agentic Tree Search on a task.")
    run.add_argument("task", help="Task prompt to solve.")
    run.add_argument(
        "--provider",
        choices=["claude-sdk", "dry-run"],
        default="claude-sdk",
        help="Model backend. Use dry-run for local smoke tests without Claude.",
    )
    run.add_argument("--model", default=None, help="Claude model name passed to the SDK.")
    run.add_argument("--max-steps", type=int, default=16)
    run.add_argument("--max-branches", type=int, default=4)
    run.add_argument("--max-depth", type=int, default=5)
    run.add_argument("--sdk-timeout", type=float, default=120.0)
    run.add_argument("--run-root", type=Path, default=Path(".tot/runs"))
    add_research_subparser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        return run(args)
    if args.command == "research":
        return handle_research(args)
    raise AssertionError(f"unhandled command: {args.command}")


def run(args: argparse.Namespace) -> int:
    cfg = SearchConfig(
        max_total_steps=args.max_steps,
        max_active_branches=args.max_branches,
        max_depth=args.max_depth,
    )
    if args.provider == "claude-sdk":
        adapter = ClaudeSDKAdapter(model=args.model, timeout_seconds=args.sdk_timeout)
    else:
        adapter = DryRunAdapter()

    engine = TreeOfThoughtEngine.from_adapter(adapter, cfg)
    answer, events, branches = engine.solve(args.task)
    run_dir = create_run_dir(args.run_root)
    write_run_artifacts(
        run_dir,
        task=args.task,
        answer=answer,
        events=events,
        branches=branches,
    )
    print(f"run_dir={run_dir}")
    print(f"final_answer={run_dir / 'final_answer.md'}")
    print(answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
