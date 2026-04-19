• Yes. That separation is cleaner.

  You want two related but distinct products:

  1. agts
     Local reasoning/search enhancer for one Claude Code-style agent.

  2. agts-research
     Multi-agent autonomous research system with durable branches, worktrees,
     evals, shared memory, heartbeats, and a meta-controller.

  agts-research can optionally give each worker access to agts, but it should not require it.
  That means agts remains a lightweight “thinking/search tool,” while agts-research becomes the
  full autonomous research orchestration layer.

  Clean Product Boundary
  agts:

  Purpose:
    Improve one agent's local reasoning and execution.

  Scope:
    One task, one local branch graph, one process/session.

  State:
    .tot/runs/<run_id>/
      branches.json
      events.jsonl
      final_answer.md
      training_rows.jsonl

  Control:
    Local coordinator chooses continue/split/stop/finalize inside a bounded run.

  Use case:
    Claude Code is working normally, and can invoke AGTS for deeper local deliberation.

  agts-research:

  Purpose:
    Run long-horizon autonomous research over many durable branches.

  Scope:
    Many agents, many worktrees, many eval attempts, shared memory.

  State:
    .research/runs/<run_id>/
      branches/
      attempts/
      notes/
      skills/
      eval_logs/
      agents/
      worktrees/
      private/
      meta_events.jsonl

  Control:
    Meta-controller allocates budget and agents across research branches.

  Use case:
    Autonomous discovery, benchmark optimization, experiment-driven research,
    paper/system development.

  The relationship should be:

  agts-research worker agent
    may call agts locally
    may ignore agts and work directly
    must report attempts/evidence/results back to agts-research

  So agts is a local capability, not the global architecture.

  Architecture
  I’d implement agts-research as a separate package/module inside the same repo:

  agts/
    existing local AGTS package

  agts_research/
    cli.py
    config.py
    models.py
    meta_controller.py
    workspace.py
    agent_runtime.py
    evaluator.py
    memory.py
    heartbeat.py
    summarizer.py
    credit.py

  .claude/
    agents/
      agts-research-meta.md
      agts-research-worker.md
      agts-research-critic.md
      agts-research-verifier.md
    skills/
      agts-local/
      agts-research-run/
      agts-research-audit/

  CLI split:

  agts run "local task" --provider claude-sdk

  and:

  agts-research start -c research.yaml
  agts-research status
  agts-research eval -m "implemented baseline reproduction"
  agts-research stop
  agts-research resume
  agts-research audit

  Or one binary with subcommands:

  agts run ...
  agts research start -c research.yaml
  agts research eval -m "..."

  I slightly prefer the second if we want one installable tool, but internally keep the packages
  separate.

  Core Model
  agts-research should not use the same BranchState as local AGTS. It needs a durable research
  branch model:

  @dataclass
  class ResearchBranch:
      branch_id: str
      parent_id: str | None
      title: str
      hypothesis: str
      research_mode: ResearchMode
      status: BranchStatus

      worktree_path: str | None
      assigned_agents: list[str]

      best_attempt_id: str | None
      attempt_ids: list[str]
      note_paths: list[str]
      skill_paths: list[str]
      evidence_paths: list[str]

      summary: ResearchBranchSummary | None
      value_estimate: float
      uncertainty: float
      novelty: float

      eval_count: int
      evals_since_improvement: int
      cost: ResearchCost

  Attempts should be commit/eval artifacts:

  @dataclass
  class ResearchAttempt:
      attempt_id: str
      branch_id: str
      agent_id: str
      commit_hash: str | None
      parent_attempt_id: str | None
      title: str
      score: float | None
      status: str
      feedback: str
      timestamp: str
      changed_files: list[str]
      eval_log_path: str | None
      metadata: dict

  Summaries should be compact and controller-facing:

  @dataclass
  class ResearchBranchSummary:
      branch_id: str
      hypothesis: str
      current_best_result: str
      best_score: float | None
      score_trend: str
      key_evidence: list[str]
      failed_approaches: list[str]
      reusable_findings: list[str]
      open_questions: list[str]
      main_risk: str
      recommended_action: str
      recommended_split_directions: list[str]

  Meta-Controller
  The meta-controller owns research topology and budget:

  continue(branch)
  split(branch, direction_a, direction_b)
  assign_agent(branch, role)
  pause(branch)
  stop(branch)
  merge(branch_a, branch_b)
  distill(branch)
  verify(branch)
  finalize(branch)

  Importantly, the meta-controller does not do the worker’s job. It should issue branch briefs
  and constraints:

  Branch b12:
    Hypothesis: CUDA shared-memory tiling can reduce runtime.
    Goal: produce one evaluated attempt.
    Constraints: preserve API, no private eval access.
    Optional local AGTS: enabled.

  Then the worker operates autonomously inside its worktree.

  Worker Modes
  Research workers should have roles/modes like:

  literature_survey
  baseline_reproduction
  implementation_experiment
  ablation
  counterexample_search
  theory_check
  failure_analysis
  skill_distillation
  paper_synthesis

  A worker with local AGTS enabled can call:

  agts run "Plan and critique the next experiment for branch b12" --provider claude-sdk

  or via a Claude Code skill:

  /agts-local Plan next experiment for this branch.

  But the worker should not be forced to call AGTS. Some tasks are better handled directly.

  Toggle Design
  Make local AGTS use a branch-level and run-level option:

  workers:
    local_agts:
      enabled: true
      mode: optional        # optional | required | disabled
      max_steps: 4
      trigger:
        on_start: false
        before_eval: true
        after_failed_eval: true
        before_pivot: true

  Interpretation:

  disabled:
    Worker does not use local AGTS.

  optional:
    Worker instructions mention AGTS as an available tool.

  required:
    Worker must run local AGTS at configured checkpoints.

  I would default to optional, because forced deliberation can waste compute.

  Research Config
  Example:

  task:
    name: kernel-optimization
    description: |
      Improve the runtime of the provided kernel while preserving correctness.
    objective: minimize runtime

  workspace:
    seed_path: ./examples/kernel/seed
    results_dir: ./.research/runs

  evaluator:
    type: command
    command: python eval.py
    timeout: 300
    direction: minimize
    private_paths:
      - ./examples/kernel/private_tests

  agents:
    runtime: claude_code
    model: minimax-2.7
    max_agents: 4
    max_turns: 200

  workers:
    local_agts:
      enabled: true
      mode: optional
      max_steps: 4

  search:
    max_branches: 6
    max_active_branches: 4
    max_agents_per_branch: 2
    max_evals: 40
    split_threshold: 0.68
    stop_threshold: 0.20
    verify_before_finalize: true

  heartbeat:
    reflect_every: 1
    consolidate_every: 5
    pivot_after_stall: 3

  Directory Layout
  Use a CORAL-like layout but named for research, not .tot:

  .research/runs/<run_id>/
    config.yaml
    meta_state.json
    meta_events.jsonl

    repo/
      # cloned seed repo

    worktrees/
      branch-b1-agent-a/
      branch-b2-agent-a/

    public/
      attempts/
        <attempt_id>.json
      notes/
        branch-b1/
        branch-b2/
        global/
      skills/
      evidence/
      eval_logs/
      summaries/
      leaderboard.json

    private/
      eval/
      hidden_tests/

  Each worktree gets symlinks:

  .claude/notes -> .research/.../public/notes
  .claude/skills -> .research/.../public/skills
  .claude/attempts -> .research/.../public/attempts
  .claude/eval_logs -> .research/.../public/eval_logs

  And a breadcrumb:

  .agts_research_dir
  .agts_branch_id
  .agts_agent_id

  Worker Instruction Contract
  Each worker should be told:

  You own local execution for one research branch.
  You may use normal Claude Code tools.
  You may use local AGTS if useful.
  You may write notes and skills.
  You may submit evaluated attempts.
  You may not create/stop/split global branches.
  You may propose split/pivot/stop in your branch report.

  Required output after an eval or heartbeat:

  {
    "branch_id": "b12",
    "agent_id": "agent-b12-a",
    "what_changed": "...",
    "attempt_id": "...",
    "score": 0.73,
    "evidence": ["..."],
    "failed_assumptions": ["..."],
    "recommended_next_action": "continue|split|stop|verify",
    "local_agts_used": true,
    "local_agts_run_id": ".tot/runs/..."
  }

  How Local AGTS Fits
  Local AGTS should be available in three ways:

  1. Manual worker command

     agts run "Critique this planned experiment" --provider claude-sdk
  2. Pre-eval checkpoint
     Before committing an attempt:

     Run local AGTS to check whether this attempt is coherent and testable.
  3. After failure
     When eval fails or regresses:

     Run local AGTS to generate alternative explanations and recovery plans.

  The local .tot/runs artifact should be linked from the research attempt metadata:

  {
    "attempt_id": "a83",
    "local_agts_runs": [".tot/runs/20260419-..."]
  }

  That gives training data later: did local AGTS help improve attempt quality?

  Why This Is Better Than One Hybrid
  This architecture gives you clean composability:

  Claude Code alone
    can use agts as a local enhancer.

  agts-research worker
    can use Claude Code alone.

  agts-research worker + local agts
    can use tree search for hard local decisions.

  agts-research meta-controller
    can reason over durable branch outcomes, not raw worker thoughts.

  That avoids overloading one tool with two jobs.

  Implementation Phases
  I’d build it in this order:

  1. Keep current agts stable
     Do not entangle it with research orchestration.
  2. Add agts_research data models and config
     ResearchBranch, ResearchAttempt, ResearchRunState.
  3. Add shared memory and run layout
     .research/runs/<run_id>/public, private, worktrees.
  4. Add evaluator command
     agts research eval -m "...".
  5. Add worktree creation
     One branch, one worker first.
  6. Add Claude Code runtime spawning
     Start one worker process with generated instructions.
  7. Add meta-controller loop
     Summarize branches, score value, choose continue/split/stop/finalize.
  8. Add optional local AGTS toggle
     Generate worker instructions and helpers, but keep it optional.
  9. Add heartbeat
     Reflect/consolidate/pivot based on eval count and stagnation.
  10. Add multi-agent branches
     Allow multiple workers per branch or per split.

  The first useful MVP is not huge:

  agts research start -c research.yaml
    creates run
    creates root research branch
    creates worktree
    launches one Claude Code worker
    supports agts research eval
    records attempts
    summarizes branch

  Then add branching.

  Naming
  I would keep:

  agts

  for local tree search, and use:

  agts-research

  or:

  agts research

  for the meta system.

  Internally:

  agts/            # local
  agts_research/   # meta-research

  That preserves the conceptual boundary and lets agts-research depend on agts, but never the
  other way around.