from __future__ import annotations

import unittest
from pathlib import Path
import json
import sys
import tempfile
import time

from agts_research.config import ResearchConfig
from agts_research.distill import distill_run
from agts_research.evaluator import submit_eval
from agts_research.evaluator import _extract_score_bundle, _is_research_changed_file, _status_for_score
from agts_research.heartbeat import heartbeat_action_record, heartbeat_prompt
from agts_research.meta import _apply_novelty_penalties, _validated_split_directions, choose_meta_action, summarize_branch
from agts_research.meta import _global_budget_exhausted
from agts_research.monitor import monitor_run
from agts_research.models import (
    AgentSpec,
    AttemptStatus,
    MetaAction,
    MetaActionType,
    ResearchAttempt,
    ResearchBranch,
    ResearchMode,
    WorkerProcessStatus,
)
from agts_research.provenance import cleanup_shared_memory, record_provenance, validate_shared_memory
from agts_research.report import build_report
from agts_research.runtime import launch_workers, refresh_worker_status, stop_workers
from agts_research.runtime import _extract_token_usage, _launch_command
from agts_research.storage import ensure_run_layout, read_state, write_json_atomic, write_state
from agts_research.models import ResearchRunState
from agts_research.workspace import find_run_dir_from_worktree, read_worktree_identity, start_research_run


def _write_seed(seed_dir: Path) -> None:
    seed_dir.mkdir(parents=True, exist_ok=True)
    (seed_dir / "solver.py").write_text("def solve():\n    return 1\n", encoding="utf-8")
    (seed_dir / ".tot").mkdir()
    (seed_dir / ".tot" / "ignored.txt").write_text("ignore me\n", encoding="utf-8")


def _research_config(seed_dir: Path, results_dir: Path, **overrides) -> ResearchConfig:
    data = {
        "task": {"name": "unit research", "description": "test task"},
        "workspace": {"seed_path": str(seed_dir), "results_dir": str(results_dir)},
        "agents": {"runtime": "claude_code", "model": "test-model", "sandbox": False},
        "search": {"max_evals": 4, "max_active_branches": 1},
    }
    for key, value in overrides.items():
        data[key] = value
    return ResearchConfig.from_dict(data)


def _read_jsonl(path: Path) -> list[dict]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            assert isinstance(value, dict)
            records.append(value)
    return records


class EvaluatorParsingTests(unittest.TestCase):
    def test_score_bundle_line_is_parsed(self) -> None:
        bundle = _extract_score_bundle(
            'AGTS_SCORE_BUNDLE={"score": 0.75, "valid": true, "metrics": {"bins": 4}}\nscore: 0.75',
            direction="maximize",
            split="private_dev",
        )

        self.assertEqual(bundle.primary, 0.75)
        self.assertTrue(bundle.valid)
        self.assertEqual(bundle.metrics["bins"], 4)
        self.assertEqual(bundle.split, "private_dev")

    def test_invalid_bundle_classifies_as_failed(self) -> None:
        cfg = ResearchConfig.from_dict({"task": {"name": "x", "description": "x"}})
        status = _status_for_score(cfg, 0.0, None, False, valid=False)

        self.assertEqual(status, AttemptStatus.FAILED)

    def test_changed_file_filter_excludes_scaffolding(self) -> None:
        self.assertFalse(_is_research_changed_file(".agts_branch_id"))
        self.assertFalse(_is_research_changed_file(".claude/"))
        self.assertFalse(_is_research_changed_file(".tot/runs/test/events.jsonl"))
        self.assertTrue(_is_research_changed_file("solver.py"))


class RunLifecycleTests(unittest.TestCase):
    def test_config_loading_preserves_heartbeat_registry(self) -> None:
        cfg = ResearchConfig.from_dict(
            {
                "task": {"name": "x", "description": "x"},
                "heartbeat": {
                    "pivot_after_stall": 2,
                    "trigger_registry": {"baseline": "custom baseline"},
                },
            }
        )

        self.assertEqual(cfg.heartbeat.pivot_after_stall, 2)
        self.assertEqual(cfg.heartbeat.trigger_registry["baseline"], "custom baseline")

    def test_start_research_run_creates_isolated_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed_dir = root / "seed"
            results_dir = root / "runs"
            _write_seed(seed_dir)
            private_file = root / "hidden.json"
            private_file.write_text('{"secret": true}\n', encoding="utf-8")
            cfg = _research_config(
                seed_dir,
                results_dir,
                evaluator={
                    "type": "command",
                    "command": f"{sys.executable} eval.py",
                    "private_paths": [str(private_file)],
                },
            )

            state = start_research_run(cfg)
            branch = next(iter(state.branches.values()))
            worktree = Path(branch.worktree_path or "")

            self.assertTrue(worktree.exists())
            self.assertTrue((worktree / "solver.py").exists())
            self.assertFalse((worktree / ".tot").exists())
            self.assertTrue((worktree / ".agts_research_dir").exists())
            self.assertTrue((worktree / ".claude" / "notes").exists())
            self.assertEqual(find_run_dir_from_worktree(worktree), Path(state.run_dir))
            self.assertEqual(read_worktree_identity(worktree), (branch.branch_id, branch.assigned_agents[0]))
            self.assertTrue((Path(state.run_dir) / "private" / private_file.name).exists())

    def test_configured_worker_roles_create_specialized_agents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed_dir = root / "seed"
            results_dir = root / "runs"
            _write_seed(seed_dir)
            cfg = _research_config(
                seed_dir,
                results_dir,
                agents={
                    "runtime": "claude_code",
                    "model": "test-model",
                    "sandbox": False,
                    "max_agents": 3,
                    "roles": ["implementation_worker", "literature_worker", "distillation_worker"],
                },
                search={"max_evals": 4, "max_active_branches": 1, "max_agents_per_branch": 3},
            )
            state = start_research_run(cfg)
            branch = next(iter(state.branches.values()))
            roles = [state.agents[agent_id].role for agent_id in branch.assigned_agents]

            self.assertEqual(roles, ["research_worker", "implementation_worker", "literature_worker"])
            claude_text = (Path(branch.worktree_path or "") / "CLAUDE.md").read_text(encoding="utf-8")
            self.assertIn("- role: research_worker", claude_text)

    def test_launch_rewrites_worktree_identity_for_specialized_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed_dir = root / "seed"
            results_dir = root / "runs"
            _write_seed(seed_dir)
            cfg = _research_config(
                seed_dir,
                results_dir,
                agents={
                    "runtime": "claude_code",
                    "model": "test-model",
                    "sandbox": False,
                    "max_agents": 2,
                    "roles": ["implementation_worker"],
                },
                search={"max_evals": 4, "max_active_branches": 1, "max_agents_per_branch": 2},
            )
            state = start_research_run(cfg)
            branch = next(iter(state.branches.values()))
            agent_id = branch.assigned_agents[1]
            worktree = Path(branch.worktree_path or "")
            try:
                launch_workers(Path(state.run_dir), agent_id=agent_id, dry_run=True, dry_run_seconds=0.01)
                self.assertEqual(read_worktree_identity(worktree), (branch.branch_id, agent_id))
                claude_text = (worktree / "CLAUDE.md").read_text(encoding="utf-8")
                self.assertIn("- role: implementation_worker", claude_text)
                self.assertIn("Focus on code changes", claude_text)
            finally:
                stop_workers(Path(state.run_dir), force=True)

    def test_eval_submission_writes_attempt_and_score_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed_dir = root / "seed"
            results_dir = root / "runs"
            _write_seed(seed_dir)
            (seed_dir / "eval.py").write_text(
                "print('AGTS_SCORE_BUNDLE={\"score\": 0.42, \"valid\": true, \"metrics\": {\"ok\": 1}}')\n",
                encoding="utf-8",
            )
            cfg = _research_config(
                seed_dir,
                results_dir,
                evaluator={"type": "command", "command": f"{sys.executable} eval.py", "direction": "maximize"},
            )
            state = start_research_run(cfg)
            branch = next(iter(state.branches.values()))

            attempt = submit_eval(cfg=cfg, message="unit eval", workdir=Path(branch.worktree_path or ""), use_server=False)
            updated = read_state(state.run_dir)

            self.assertEqual(attempt.score, 0.42)
            self.assertEqual(attempt.metadata["score_bundle"]["metrics"]["ok"], 1)
            self.assertIn(attempt.attempt_id, updated.attempts)
            self.assertEqual(updated.branches[branch.branch_id].best_attempt_id, attempt.attempt_id)
            self.assertTrue(Path(attempt.eval_log_path or "").exists())

    def test_monitor_does_not_duplicate_running_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed_dir = root / "seed"
            results_dir = root / "runs"
            _write_seed(seed_dir)
            cfg = _research_config(seed_dir, results_dir)
            state = start_research_run(cfg)
            run_dir = Path(state.run_dir)
            try:
                monitor_run(run_dir, iterations=2, interval=0.02, dry_run=True, dry_run_seconds=0.4)
                updated = read_state(run_dir)
                branch = next(iter(updated.branches.values()))
                heartbeat_lines = (run_dir / "public" / "heartbeat" / "actions.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
                events = _read_jsonl(run_dir / "meta_events.jsonl")
                process_json = json.loads(
                    (run_dir / "public" / "agents" / f"{branch.assigned_agents[0]}.process.json").read_text(
                        encoding="utf-8"
                    )
                )

                self.assertEqual(branch.cost.agent_turns, 1)
                self.assertEqual(len(heartbeat_lines), 1)
                self.assertTrue(any(event.get("type") == "monitor_tick" for event in events))
                self.assertEqual(process_json["agent_id"], branch.assigned_agents[0])
            finally:
                stop_workers(run_dir, force=True)
                refresh_worker_status(run_dir)

    def test_report_and_public_json_artifacts_are_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed_dir = root / "seed"
            results_dir = root / "runs"
            _write_seed(seed_dir)
            (seed_dir / "eval.py").write_text(
                "print('AGTS_SCORE_BUNDLE={\"score\": 0.5, \"valid\": true, \"metrics\": {\"m\": 2}}')\n",
                encoding="utf-8",
            )
            cfg = _research_config(
                seed_dir,
                results_dir,
                evaluator={"type": "command", "command": f"{sys.executable} eval.py"},
            )
            state = start_research_run(cfg)
            branch = next(iter(state.branches.values()))
            submit_eval(cfg=cfg, message="report eval", workdir=Path(branch.worktree_path or ""), use_server=False)

            report = build_report(Path(state.run_dir))

            self.assertEqual(report["private_dev_eval_count"], 1)
            self.assertIn("tokens", report["resource_usage"])
            self.assertEqual(report["best_private_dev"]["score_bundle"]["metrics"]["m"], 2)
            json.dumps(report)
            for path in (Path(state.run_dir) / "public").rglob("*.json"):
                json.loads(path.read_text(encoding="utf-8"))
            for path in (Path(state.run_dir) / "public").rglob("*.jsonl"):
                _read_jsonl(path)

    def test_monitor_stop_on_exit_stops_launched_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed_dir = root / "seed"
            results_dir = root / "runs"
            _write_seed(seed_dir)
            cfg = _research_config(seed_dir, results_dir)
            state = start_research_run(cfg)
            run_dir = Path(state.run_dir)

            monitor_run(
                run_dir,
                iterations=1,
                interval=0.01,
                dry_run=True,
                dry_run_seconds=1.0,
                stop_on_exit=True,
            )
            agents = refresh_worker_status(run_dir)

            self.assertEqual(agents[0].status, WorkerProcessStatus.STOPPED.value)
            self.assertEqual(agents[0].exit_classification, "stopped")

    def test_worker_token_usage_is_accounted_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed_dir = root / "seed"
            results_dir = root / "runs"
            _write_seed(seed_dir)
            cfg = _research_config(seed_dir, results_dir)
            state = start_research_run(cfg)
            run_dir = Path(state.run_dir)
            agent = next(iter(state.agents.values()))
            log_path = run_dir / "public" / "agents" / "usage.log"
            log_path.write_text(
                '{"session_id":"s1","usage":{"input_tokens":10,"output_tokens":5}}\n'
                "tokens_used=21\n",
                encoding="utf-8",
            )
            agent.pid = 99999999
            agent.status = WorkerProcessStatus.RUNNING.value
            agent.log_path = str(log_path)
            write_state(run_dir, state)

            refresh_worker_status(run_dir)
            refresh_worker_status(run_dir)
            updated = read_state(run_dir)
            updated_agent = updated.agents[agent.agent_id]
            updated_branch = updated.branches[agent.branch_id]

            self.assertEqual(updated_agent.accounted_tokens, 21)
            self.assertEqual(updated_branch.cost.tokens, 21)
            self.assertEqual(updated_agent.session_id, "s1")

    def test_token_usage_parser_handles_common_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "usage.log"
            log.write_text(
                '{"usage":{"prompt_tokens":3,"completion_tokens":4}}\n'
                '{"message":{"usage":{"input_tokens":5,"output_tokens":6,"cache_read_input_tokens":7}}}\n'
                "total_tokens: 19\n",
                encoding="utf-8",
            )

            self.assertEqual(_extract_token_usage(log), 19)

    def test_claude_resume_command_uses_captured_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = root / "worktree"
            worktree.mkdir()
            cfg = ResearchConfig.from_dict(
                {
                    "task": {"name": "x", "description": "x"},
                    "agents": {"runtime": "claude_code", "model": "test-model", "sandbox": False},
                }
            )
            agent = AgentSpec(
                agent_id="agent-rb-a",
                branch_id="rb",
                role="research_worker",
                runtime="claude_code",
                model="test-model",
                worktree_path=str(worktree),
                session_id="session-123",
            )

            command = _launch_command(
                cfg,
                root,
                agent,
                dry_run=False,
                prompt="continue",
                resume_session=True,
            )

            self.assertIn("--resume", command)
            self.assertIn("session-123", command)
            self.assertEqual(command[-1], "continue")

    def test_worker_turn_limit_blocks_direct_relaunch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed_dir = root / "seed"
            results_dir = root / "runs"
            _write_seed(seed_dir)
            cfg = _research_config(
                seed_dir,
                results_dir,
                agents={"runtime": "claude_code", "model": "test-model", "sandbox": False, "max_turns": 1},
            )
            state = start_research_run(cfg)
            run_dir = Path(state.run_dir)
            agent_id = next(iter(state.agents))
            try:
                first = launch_workers(run_dir, agent_id=agent_id, dry_run=True, dry_run_seconds=0.01)
                self.assertEqual(len(first), 1)
                time.sleep(0.03)
                refresh_worker_status(run_dir)
                second = launch_workers(run_dir, agent_id=agent_id, dry_run=True, dry_run_seconds=0.01)
                updated = read_state(run_dir)
                branch = next(iter(updated.branches.values()))

                self.assertEqual(second, [])
                self.assertEqual(branch.cost.agent_turns, 1)
                self.assertTrue(
                    any(event.get("type") == "worker_turn_limit" for event in _read_jsonl(run_dir / "meta_events.jsonl"))
                )
            finally:
                stop_workers(run_dir, force=True)


class BudgetPolicyTests(unittest.TestCase):
    def test_private_dev_eval_budget_ignores_final_holdout(self) -> None:
        cfg = ResearchConfig.from_dict(
            {
                "task": {"name": "x", "description": "x"},
                "search": {"max_evals": 1},
            }
        )
        branch = ResearchBranch(
            branch_id="rb",
            parent_id=None,
            title="branch",
            hypothesis="hypothesis",
            research_mode=ResearchMode.BASELINE_REPRODUCTION,
        )
        attempts = {
            "final": ResearchAttempt(
                attempt_id="final",
                branch_id="rb",
                agent_id="agent",
                title="final",
                score=1.0,
                status=AttemptStatus.IMPROVED,
                timestamp=0.0,
                metadata={"eval_split": "final_holdout"},
            )
        }

        self.assertEqual(_global_budget_exhausted(cfg, [branch], attempts), "")

        attempts["dev"] = ResearchAttempt(
            attempt_id="dev",
            branch_id="rb",
            agent_id="agent",
            title="dev",
            score=1.0,
            status=AttemptStatus.IMPROVED,
            timestamp=0.0,
            metadata={"eval_split": "private_dev"},
        )

        self.assertIn("private-dev eval budget exhausted", _global_budget_exhausted(cfg, [branch], attempts))

    def test_agent_turn_budget(self) -> None:
        cfg = ResearchConfig.from_dict(
            {
                "task": {"name": "x", "description": "x"},
                "search": {"max_evals": 10, "max_agent_turns": 2},
            }
        )
        branch = ResearchBranch(
            branch_id="rb",
            parent_id=None,
            title="branch",
            hypothesis="hypothesis",
            research_mode=ResearchMode.BASELINE_REPRODUCTION,
        )
        branch.cost.agent_turns = 2

        self.assertIn("agent-turn budget exhausted", _global_budget_exhausted(cfg, [branch], {}))


class ValuePolicyTests(unittest.TestCase):
    def test_novelty_penalizes_duplicate_branch(self) -> None:
        first = ResearchBranch(
            branch_id="a",
            parent_id=None,
            title="same branch",
            hypothesis="try best fit decreasing for bin packing",
            research_mode=ResearchMode.IMPLEMENTATION_EXPERIMENT,
        )
        second = ResearchBranch(
            branch_id="b",
            parent_id=None,
            title="same branch copy",
            hypothesis="try best fit decreasing for bin packing",
            research_mode=ResearchMode.IMPLEMENTATION_EXPERIMENT,
        )

        _apply_novelty_penalties([first, second])

        self.assertEqual(first.novelty, 1.0)
        self.assertLess(second.novelty, 1.0)

    def test_stalled_low_voi_branch_requests_verify(self) -> None:
        cfg = ResearchConfig.from_dict(
            {
                "task": {"name": "x", "description": "x"},
                "search": {"max_evals": 10, "split_threshold": 0.95, "verify_before_finalize": True},
                "heartbeat": {"pivot_after_stall": 1},
            }
        )
        branch = ResearchBranch(
            branch_id="rb",
            parent_id=None,
            title="branch",
            hypothesis="hypothesis",
            research_mode=ResearchMode.BASELINE_REPRODUCTION,
        )
        branch.evals_since_improvement = 1
        branch.attempt_ids.append("a")
        attempts = {
            "a": ResearchAttempt(
                attempt_id="a",
                branch_id="rb",
                agent_id="agent",
                title="attempt",
                score=1.0,
                status=AttemptStatus.BASELINE,
                timestamp=0.0,
                metadata={"eval_split": "private_dev"},
            )
        }
        summarize_branch(cfg, branch, attempts)
        branch.summary.value_of_information = 0.5

        action = choose_meta_action(cfg, [branch], attempts)

        self.assertEqual(action.type, MetaActionType.VERIFY)

    def test_split_direction_validation_deduplicates(self) -> None:
        branch = ResearchBranch(
            branch_id="rb",
            parent_id=None,
            title="branch",
            hypothesis="hypothesis",
            research_mode=ResearchMode.BASELINE_REPRODUCTION,
        )

        directions = _validated_split_directions(branch, ["same repair path", "same repair path"])

        self.assertEqual(len(directions), 2)
        self.assertNotEqual(directions[0], directions[1])


class DistillationTests(unittest.TestCase):
    def test_distill_run_writes_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            ensure_run_layout(run_dir)
            cfg = ResearchConfig.from_dict({"task": {"name": "x", "description": "x"}})
            write_json_atomic(run_dir / "config.json", cfg.to_dict())
            branch = ResearchBranch(
                branch_id="rb",
                parent_id=None,
                title="branch",
                hypothesis="hypothesis",
                research_mode=ResearchMode.BASELINE_REPRODUCTION,
            )
            branch.attempt_ids.append("a")
            attempt = ResearchAttempt(
                attempt_id="a",
                branch_id="rb",
                agent_id="agent",
                title="attempt",
                score=1.0,
                status=AttemptStatus.IMPROVED,
                timestamp=0.0,
                changed_files=["solver.py"],
                metadata={"eval_split": "private_dev"},
            )
            state = ResearchRunState(
                run_id="run",
                task_name="x",
                task_description="x",
                run_dir=str(run_dir),
                repo_dir=str(run_dir / "repo"),
                created_at=0.0,
                branches={"rb": branch},
                attempts={"a": attempt},
            )
            write_state(run_dir, state)

            artifact = distill_run(run_dir)

            self.assertEqual(artifact["branch_count"], 1)
            self.assertTrue((run_dir / "public" / "summaries" / "distilled_findings.json").exists())
            self.assertTrue((run_dir / "public" / "evidence" / "provenance.jsonl").exists())


class SharedMemoryTests(unittest.TestCase):
    def test_provenance_index_and_memory_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            ensure_run_layout(run_dir)
            note = run_dir / "public" / "notes" / "rb.md"
            note.write_text(
                "# Branch rb\n\n## Current Hypothesis\nx\n\n## Evidence\n- y\n\n## Next Action\nz\n",
                encoding="utf-8",
            )
            evidence = run_dir / "public" / "evidence" / "manual.md"
            evidence.write_text("manual evidence\n", encoding="utf-8")

            record_provenance(run_dir, path=note, kind="note", source="test", branch_id="rb")
            result = validate_shared_memory(run_dir)

            self.assertTrue(result["ok"])
            self.assertEqual(result["provenance"]["record_count"], 1)
            self.assertTrue((run_dir / "public" / "summaries" / "memory_validation.json").exists())

    def test_heartbeat_action_record_persists_prompt_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            ensure_run_layout(run_dir)
            cfg = ResearchConfig.from_dict({"task": {"name": "x", "description": "x"}})
            branch = ResearchBranch(
                branch_id="rb",
                parent_id=None,
                title="branch",
                hypothesis="hypothesis",
                research_mode=ResearchMode.BASELINE_REPRODUCTION,
            )
            agent = AgentSpec(
                agent_id="agent-rb-a",
                branch_id="rb",
                role="research_worker",
                runtime="claude",
                model="model",
                worktree_path=str(run_dir / "worktrees" / "rb"),
            )
            action = MetaAction(type=MetaActionType.CONTINUE, branch_id="rb", reason="highest value")
            prompt = heartbeat_prompt(cfg, branch, reason=action.reason)

            record = heartbeat_action_record(
                run_dir,
                cfg=cfg,
                iteration=3,
                action=action,
                branch=branch,
                agent=agent,
                prompt=prompt,
            )

            self.assertEqual(record["trigger"], "baseline")
            self.assertEqual(record["trigger_description"], "first evaluated attempt for a branch")
            self.assertEqual(len(record["prompt_sha256"]), 64)
            self.assertTrue((run_dir / "public" / "heartbeat" / "actions.jsonl").exists())

    def test_cleanup_shared_memory_quarantines_invalid_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            ensure_run_layout(run_dir)
            bad_note = run_dir / "public" / "notes" / "bad.md"
            bad_note.write_text("# Missing required sections\n", encoding="utf-8")

            report = cleanup_shared_memory(run_dir, apply=True)

            self.assertEqual(report["candidate_count"], 1)
            self.assertEqual(report["quarantined_count"], 1)
            self.assertFalse(bad_note.exists())
            self.assertTrue(Path(report["items"][0]["quarantined_path"]).exists())


if __name__ == "__main__":
    unittest.main()
