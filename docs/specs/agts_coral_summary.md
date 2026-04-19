  A strong version would not be “AGTS plus CORAL” as a loose bundle. It would separate the two
  control layers cleanly:

  Meta-AGTS
    central meta-controller over research strategy, budget, branches, and evidence

  CORAL-style substrate
    long-running agents, worktrees, evals, notes, skills, attempts, heartbeats

  The useful synthesis is:

  Tree search decides where attention and budget go.
  CORAL-style agents decide how to execute deep local research.

  Why It Makes Sense
  CORAL is excellent at long-horizon autonomous exploration because it gives agents:

  - persistent memory
  - isolated workspaces
  - evaluator feedback
  - autonomous iteration
  - shared notes and skills
  - heartbeat nudges

  But CORAL’s manager mostly manages infrastructure. It does not deeply reason over a formal
  branch graph, estimate branch value, compare hypotheses, or explicitly allocate budget between
  competing research trajectories.

  AGTS/tree search is good at:

  - explicit branch topology
  - branch summaries
  - value estimates
  - split/stop/finalize decisions
  - budgeted search
  - verifier-gated finalization
  - retrospective credit assignment

  But plain AGTS is weaker if each branch is just a short model call. Research often needs many
  cycles of reading, coding, testing, note-taking, and failed attempts. That is where CORAL’s
  substrate is much better.

  So the combined system should treat research branches as durable research programs, not just
  prompt continuations.

  Proposed Architecture
  Call it something like Research Meta-AGTS.

  It would have five layers:

  1. Research Task
     question, objective, constraints, evaluator, available tools

  2. Meta-Controller
     owns branch graph, budget, research agenda, branch value estimates

  3. Research Branches
     each branch is a persistent hypothesis / approach / research program

  4. CORAL-Style Agent Substrate
     worktrees, agents, evals, notes, skills, attempts, heartbeat interventions

  5. Evidence and Training Layer
     traces, summaries, verifier scores, citations, tests, credit labels

  A branch would no longer just be:

  {
    "branch_id": "b7",
    "mode": "tool_verify",
    "summary": "..."
  }

  It would become:

  {
    "branch_id": "b7",
    "research_question": "Can approach X outperform baseline Y?",
    "hypothesis": "...",
    "workspace": "runs/.../agents/b7",
    "agent_ids": ["agent-b7-a", "agent-b7-b"],
    "attempts": ["commit1", "commit2"],
    "notes": ["notes/b7-literature.md", "notes/b7-failures.md"],
    "skills_created": ["skills/kernel-tiling"],
    "best_score": 0.73,
    "evidence": [...],
    "open_questions": [...],
    "value_estimate": 0.61,
    "status": "active"
  }

  Control Loop
  The meta-controller would run something like:

  1. Initialize root research branch.
  2. Launch one or more branch-local agents.
  3. Agents execute CORAL-style loops:
     read → research → edit/experiment → eval → note → repeat.
  4. Periodically summarize each branch from:
     attempts, notes, diffs, evals, logs, citations, failures.
  5. Estimate branch value:
     progress, novelty, score trend, evidence quality, uncertainty, cost.
  6. Choose action:
     continue branch
     split branch into new hypotheses
     allocate more agents
     pause/stop branch
     merge evidence
     ask critic/verifier
     finalize answer/paper/system
  7. Persist everything.
  8. Learn from completed runs.

  Action Space
  For research, the action space should be richer than the current AGTS MVP:

  continue(branch)
  split(branch, hypothesis_a, hypothesis_b)
  spawn_agent(branch, role)
  pause(branch)
  stop(branch)
  merge(branch_i, branch_j)
  distill(branch)
  verify(branch)
  escalate_to_experiment(branch)
  finalize(branch)

  The important new actions are:

  - spawn_agent: add compute to a promising branch.
  - distill: turn messy notes/attempts into reusable knowledge.
  - merge: combine evidence from separate branches.
  - verify: run stricter tests, reproduce claims, check citations.
  - escalate_to_experiment: move from literature/theory to code/eval.

  Branch Modes
  Current AGTS modes are useful but too narrow for research. I’d extend them:

  literature_survey
  baseline_reproduction
  independent_hypothesis
  implementation_experiment
  ablation
  counterexample_search
  theory_check
  tool_verify
  paper_synthesis
  failure_analysis
  skill_distillation

  These are discrete enough for a controller to learn, but expressive enough for real research.

  What To Borrow From CORAL
  Directly borrow these:

  1. Worktree isolation
     Each experimental branch should have its own git worktree.
  2. Attempt records
     Every evaluated candidate should become an attempt artifact with score, feedback, commit,
     parent, branch id, and metadata.
  3. Shared memory
     Use files:

     .research/public/attempts/
     .research/public/notes/
     .research/public/skills/
     .research/public/evidence/
     .research/public/eval_logs/
  4. Private evaluator data
     Keep hidden grader/test data separate:

     .research/private/
  5. Heartbeat interventions
     Trigger branch-local prompts:
      - reflect after every eval
      - consolidate every N evals
      - pivot after stagnation
      - reproduce before finalization
  6. Agent runtime abstraction
     Support Claude Code now, but don’t bake in one runtime forever.

  What To Keep From AGTS
  Keep these:

  1. Explicit branch graph
     CORAL’s agents are autonomous, but the system doesn’t formally know the research tree. AGTS
     should.
  2. Summaries as control state
     The meta-controller should not read full logs every time. It should read branch summaries.
  3. Value model
     Estimate whether more compute on a branch is worth it.
  4. Budget-aware scheduling
     Decide where to spend model calls, wall-clock time, eval budget, and human attention.
  5. Verifier-gated finalization
     Don’t let a branch “win” just because it is confident. Require tests, reproduction,
     citations, agreement, or explicit uncertainty.
  6. Credit assignment
     Track which branches contributed evidence, failures, skills, or final results.

  The Key Design Principle
  Do not let the meta-controller micromanage agents.

  A good split is:

  Meta-controller:
    What research direction should get resources?

  Branch agents:
    How do I make progress inside this direction?

  If the controller micromanages every file edit, it loses CORAL’s advantage. If agents have
  total freedom without branch accounting, we lose AGTS’s advantage.

  Potential MVP
  A practical first version:

  agts-research run task.yaml

  Where task.yaml defines:

  task:
    name: kernel-research
    question: Improve this kernel benchmark.
    objective: minimize runtime
    constraints: Do not change public API.

  evaluator:
    type: command
    command: python eval.py

  agents:
    runtime: claude_code
    model: minimax-2.7
    max_agents: 4

  search:
    max_branches: 4
    max_branch_agents: 2
    max_evals: 40
    heartbeat:
      reflect_every: 1
      consolidate_every: 5
      pivot_after_stall: 3

  Run loop:

  - create root branch
  - create worktree
  - launch one Claude Code agent
  - agent can call agts eval
  - manager records attempts
  - after every eval, summarize branch
  - controller decides continue/split/stop
  - branch split creates a new worktree and new research brief
  - final report includes best artifact plus evidence audit

  This Would Be Stronger Than Either Alone
  The combined system would be especially good for:

  - algorithm discovery
  - kernel optimization
  - ML benchmark improvement
  - automated ablations
  - literature-backed implementation
  - design-space exploration
  - research paper drafting with real experiments

  It would be weaker for tiny tasks where one agent call is enough. The overhead only pays off
  when the problem benefits from persistent search.

  Main Risks
  The hard parts are real:

  1. Summarization quality
     If branch summaries are bad, the controller allocates budget badly.
  2. Evaluator overfitting
     Agents can learn to game public tests. You need private validation or held-out checks.
  3. Branch explosion
     Tree search plus autonomous agents can get expensive fast.
  4. Shared memory pollution
     Bad notes and bad skills can spread. Need confidence, provenance, and cleanup.
  5. Credit assignment
     A branch that fails may still contribute a key negative result. The training data must
     capture that.
  6. Concurrency
     Multiple agents writing notes/skills concurrently needs atomic writes and low-conflict
     conventions.

  Best Next Step
  For this repo, I would evolve AGTS toward this hybrid in stages:

  1. Add CORAL-like attempts, notes, skills, and evaluator artifacts.
  2. Add agts eval with commit/eval/log semantics.
  3. Add branch worktree isolation.
  4. Add heartbeat actions.
  5. Add branch summaries from attempts/notes/evals.
  6. Add meta-controller actions for split/stop/continue over persistent branches.
  7. Only then add learned value models.

  That gives us a research system with a real path to autonomy, instead of just a tree-search
  prompt wrapper.
