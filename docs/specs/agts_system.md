## 1) A concrete architecture

Use four modules.

### A. Worker

A branch-local agent that advances one reasoning trajectory.

Input:

* task (x)
* branch state (z_b)
* branch mode (m_b)

Output:

* one or a few new reasoning steps
* optional tool calls
* updated candidate answer
* branch-local self-evaluation
* branch summary

Formally:

[
z_b^{t+1} = f_\theta(x, z_b^t, m_b)
]

where (z_b^t) contains the full branch trace and local memory.

---

### B. Summarizer

Compresses the full branch state into a control-facing summary.

[
s_b^t = S_\eta(z_b^t)
]

This is critical. The supervisor should not read raw long traces. It should only read compact structured summaries.

---

### C. Branch value model

Estimates whether spending more budget on a branch is worth it.

[
q_b^t = Q_\psi(x, s_b^t)
]

Possible meanings:

* probability branch eventually yields a correct solution
* expected verifier score after more expansion
* expected utility under remaining budget

A better target is:

[
v_b^t = \mathbb{E}[R - \lambda C_{\text{future}} \mid x, s_b^t]
]

---

### D. Supervisor

Chooses which branches to continue, split, stop, or finalize.

[
u_t = \mu_\phi(x, \Sigma_t, B_t)
]

where:

* (\Sigma_t = {s_b^t})
* (B_t) is remaining budget
* (u_t) is the control action

---

## 2) The branch state you should actually store

A good practical branch object:

```json
{
  "branch_id": "b7",
  "parent_id": "b3",
  "depth": 4,
  "mode": "counterexample_search",
  "trace": [...],
  "working_memory": {
    "facts": [...],
    "assumptions": [...],
    "subgoals": [...],
    "tool_results": [...]
  },
  "candidate_answer": "...",
  "self_eval": {
    "confidence": 0.42,
    "main_risk": "assumption may be false",
    "next_best_action": "check edge case"
  },
  "cost": {
    "tokens": 2100,
    "tool_calls": 2,
    "wall_steps": 4
  },
  "status": "active"
}
```

Keep the raw trace for training, but do not feed it to the supervisor except through summarization.

---

## 3) The summary schema

The summary should be structured, short, and comparable across branches.

A good schema is:

```json
{
  "branch_id": "b7",
  "mode": "counterexample_search",
  "current_hypothesis": "...",
  "best_candidate_answer": "...",
  "evidence_found": [
    "...",
    "..."
  ],
  "open_questions": [
    "...",
    "..."
  ],
  "failure_mode": "may rely on unchecked premise",
  "progress_score": 0.58,
  "confidence": 0.42,
  "novelty": 0.71,
  "expected_remaining_steps": 3,
  "recommended_next_action": "continue",
  "recommended_split_modes": ["tool_check", "independent_rederive"]
}
```

### Why these fields matter

* **current_hypothesis**: what this branch is trying to prove or produce
* **best_candidate_answer**: current answer state
* **evidence_found**: what was actually gained
* **open_questions**: whether more compute is likely useful
* **failure_mode**: makes supervisor better at killing bad branches
* **progress_score**: branch-local estimate of progress
* **confidence**: useful but not sufficient
* **novelty**: prevents all branches collapsing to the same strategy
* **expected_remaining_steps**: helps with budget allocation
* **recommended_split_modes**: local hint, not binding

---

## 4) Branch modes

Do not let branch splitting be free-form at the start. Use a small discrete library of modes.

A practical set:

[
M = {
\texttt{direct_solve},
\texttt{decompose},
\texttt{independent_rederive},
\texttt{tool_verify},
\texttt{counterexample_search},
\texttt{assumption_stress_test},
\texttt{compress_and_finalize}
}
]

### Intuition

* **direct_solve**: just solve directly
* **decompose**: break task into subproblems
* **independent_rederive**: fresh derivation without depending on prior reasoning
* **tool_verify**: use tools, retrieval, code, search, execution
* **counterexample_search**: try to break current answer
* **assumption_stress_test**: identify weak premises
* **compress_and_finalize**: turn a good partial trajectory into a clean final answer

This makes splitting a classification problem instead of an unconstrained prompting problem.

---

## 5) Supervisor action space

For an MVP, use only:

[
u_t \in {
\texttt{continue}(b),
\texttt{split}(b,m_i,m_j),
\texttt{stop}(b),
\texttt{finalize}(b)
}
]

Ignore merge initially. It adds complexity.

### Recommended split semantics

When splitting branch (b):

* both children inherit the parent summary and relevant memory
* each child gets a different mode
* each child also gets an instruction to avoid duplicating the sibling

Example:

* child 1: “verify with tools”
* child 2: “search for disconfirming cases”

---

## 6) First version of the control policy

Do not begin with a learned supervisor. Begin with a heuristic controller, collect traces, then distill.

A strong heuristic policy is:

### Continue a branch if

* value is high
* novelty is still nontrivial
* unresolved questions remain
* cost so far is not too large

### Split a branch if

* value is high
* uncertainty is high
* branch is at an inflection point
* the likely benefit of diverse approaches exceeds the split cost

### Stop a branch if

* low value
* stagnation for 2–3 steps
* low novelty
* repeated unsupported confidence
* dominated by another branch

### Finalize if

* verifier score exceeds threshold
* two independent branches agree
* no high-value unresolved objection remains

---

## 7) A usable scoring function

Before you train (Q_\psi), use a hand-built utility score:

[
U_b = w_1 \cdot \text{progress}

* w_2 \cdot \text{confidence}
* w_3 \cdot \text{novelty}
* w_4 \cdot \text{verifier_partial}

- w_5 \cdot \text{cost}
- w_6 \cdot \text{risk}
  ]

You can derive the fields from the branch summary.

A branch is split when:

[
U_b > \tau_{\text{split}}
\quad \text{and} \quad
\text{strategic_uncertainty}(b) > \tau_u
]

A branch is stopped when:

[
U_b < \tau_{\text{stop}}
]

A branch is finalized when:

[
\text{verifier}(b) > \tau_{\text{final}}
]

---

## 8) Strategic uncertainty

This is the most useful splitting signal.

Define

[
p(m \mid x, s_b)
]

as the estimated distribution over best next modes. Then

[
H_b = -\sum_m p(m \mid x,s_b)\log p(m \mid x,s_b)
]

If (H_b) is high, the system is unsure which strategy is best. That is a natural point to fork.

If you do not have a learned next-mode model yet, approximate this by prompting the worker or supervisor to score candidate next modes.

---

## 9) Credit assignment

Use a two-level approach.

### Level 1: branch outcome labels

For every branch state (s_b^t), assign a retrospective label:

[
y_b^t =
\begin{cases}
1 & \text{if the branch lineage contributed to a successful final answer}\
0 & \text{otherwise}
\end{cases}
]

Train (Q_\psi) on these.

This is the simplest useful target.

---

### Level 2: marginal utility labels

For finer control, define approximate credit as:

[
c_b = R(\mathcal{T}) - R(\mathcal{T}\setminus b)
]

where (\mathcal{T}) is the full search trace.

You do not want to actually rerun everything without (b) every time, so use approximations:

* lineage credit for winning branch
* bonus credit for branches whose objections caused corrections
* penalty for expensive dead-end branches
* bonus for verifier-improving branches even if they were not finalized

This matters because many good branches do not directly “win,” but they improve the final answer by refuting bad ones.

---

## 10) Self-supervised targets

You need three learned pieces.

### A. Branch value model (Q_\psi)

Input:

* task embedding
* branch summary
* remaining budget

Target:

* eventual success under remaining budget
* or verifier-improved utility

Training example:

[
(x, s_b^t, B_t) \to \hat{v}_b^t
]

---

### B. Split policy (P_\rho(m_i,m_j \mid x,s_b))

Predicts the best pair of child modes to create.

Target:

* which split types historically led to the biggest expected gain

---

### C. Supervisor policy (\mu_\phi)

Predicts the best control action.

Target:

* imitation of your best heuristic controller at first
* then improved by offline RL or bandit-style optimization on collected traces

---

## 11) The training loop

The cleanest development path is:

### Phase 1: scripted controller

Use rules, not learning.
Collect many traces.

### Phase 2: fit models

Train:

* branch success predictor
* split recommendation model
* branch stopping model

### Phase 3: learned controller

Replace some rules with learned decisions, but keep hard safety constraints like:

* max branches
* max depth
* verifier requirement for finalize

### Phase 4: policy improvement

Use logged outcomes to improve the supervisor.

---

## 12) Minimal rollout algorithm

Here is the simplest solid version.

```python
def solve(task, budget):
    branches = [init_branch(task, mode="direct_solve")]
    trace_log = []

    while budget > 0 and len(branches) > 0:
        summaries = [summarize(b) for b in branches]
        values = [estimate_value(task, s, budget) for s in summaries]

        action = supervisor(task, summaries, values, budget)

        if action.type == "continue":
            b = get_branch(branches, action.branch_id)
            b = worker_step(task, b)
            update_branch(branches, b)
            budget -= step_cost(b)

        elif action.type == "split":
            b = get_branch(branches, action.branch_id)
            child1, child2 = split_branch(b, action.mode1, action.mode2)
            replace_branch(branches, b, [child1, child2])
            budget -= split_cost()

        elif action.type == "stop":
            remove_branch(branches, action.branch_id)

        elif action.type == "finalize":
            b = get_branch(branches, action.branch_id)
            answer = finalize_answer(task, b)
            reward = verify(task, answer)
            trace_log.append((branches, action, reward))
            return answer, trace_log

        trace_log.append(snapshot(branches, action))

    best = select_best_branch(task, branches)
    answer = finalize_answer(task, best)
    reward = verify(task, answer)
    trace_log.append((branches, "forced_finalize", reward))
    return answer, trace_log
```

---

## 13) Worker prompt design

Each worker should be told its role, limits, and what to emit.

A good worker output contract is:

```json
{
  "reasoning_delta": "...",
  "new_evidence": ["...", "..."],
  "updated_candidate_answer": "...",
  "confidence": 0.37,
  "key_risk": "...",
  "proposed_next_step": "...",
  "should_request_split": false,
  "suggested_split_modes": []
}
```

Important: the worker should not decide globally. It only reports local status.

---

## 14) Supervisor prompt design

The supervisor should receive a table of branch summaries and choose one action only.

A good supervisor input:

* task
* remaining budget
* active branch summaries
* pairwise novelty overlaps
* verifier signals
* current best candidate
* branch lineage metadata

A good supervisor output contract:

```json
{
  "action": "split",
  "branch_id": "b3",
  "mode1": "tool_verify",
  "mode2": "counterexample_search",
  "reason": "high value, unresolved uncertainty, nontrivial expected benefit from diversification"
}
```

---

## 15) Verifier design

Your system needs some notion of correctness, even if weak.

Possible verifier sources:

* exact answer check
* code execution
* retrieval grounding
* consistency between independent branches
* self-critique
* external evaluator model
* domain-specific tests

You do not need a perfect verifier. You need one that is good enough to produce a useful learning signal.

A very practical combined verifier is:

[
V = \alpha V_{\text{task}} + \beta V_{\text{agreement}} + \gamma V_{\text{tool}} + \delta V_{\text{critique}}
]

---

## 16) How to avoid branch explosion

This will be the main failure mode with a weak model.

Use hard constraints:

[
|\mathcal{B}*t| \leq B*{\max}
]

and

[
\text{depth}(b) \leq D_{\max}
]

A good initial setting:

* max active branches: 4 or 6
* max steps per branch: 4 to 8
* only one split per branch unless verifier improves

Also use dominance pruning:
stop branch (b_i) if another branch (b_j) has:

* same hypothesis
* lower risk
* higher evidence
* lower cost

---

## 17) The best first implementation path

If I were building this, I would start with this stack:

### Version 1

* same cheap model for workers and summarizer
* rule-based supervisor
* 4 branch modes
* hardcoded scoring function
* simple verifier
* JSON-only outputs

### Version 2

* learned branch value predictor
* learned split recommendation
* novelty estimator using embedding similarity
* better branch pruning

### Version 3

* learned supervisor
* branch merge or evidence sharing
* offline policy improvement
* task-specific verifier heads

---

## 18) What the data for training should look like

Each training record should be one control point:

```json
{
  "task": "...",
  "remaining_budget": 9,
  "branch_summaries": [...],
  "chosen_action": {
    "type": "continue",
    "branch_id": "b2"
  },
  "eventual_final_reward": 0.86,
  "winning_branch_id": "b5",
  "credits": {
    "b1": -0.1,
    "b2": 0.2,
    "b5": 0.8
  }
}
```

From the same traces you can derive:

* state-action pairs for supervisor learning
* branch-summary/value pairs for (Q_\psi)
* split-state/best-modes pairs for split policy training

---

## 19) A good formal framing for the whole system

You can describe the full system as:

> A budgeted partially observable control process over a dynamically evolving forest of reasoning trajectories, where a supervisor allocates inference budget by maximizing expected task reward minus computation cost.

Or even more concretely:

[
\max_{\mu, \pi, Q} ;
\mathbb{E}\left[
R(y,x) - \lambda \sum_t \sum_{b \in \mathcal{B}_t} c(b,t)
\right]
]

subject to branch transition rules:

* continue
* split
* stop
* finalize

with observations restricted to branch summaries.

---

## 20) The simplest useful recipe

If you want the shortest practical recipe:

1. Start one branch.
2. After every step, summarize it into a fixed schema.
3. Score each branch with a utility estimate.
4. Continue the best branch unless:

   * its utility is high but uncertainty is also high → split
   * its utility is low → stop
5. Finalize when verifier or branch agreement is strong.
6. Log everything.
7. Train a branch success model from the logs.
8. Replace the hand-built controller with a learned one gradually.

---

## 21) One compact pseudocode specification

```text
Given task x and compute budget B:

Initialize active branch set A = {b0}
while B > 0:
    for each b in A:
        s_b = summarize(b)
        v_b = estimate_future_utility(x, s_b, B)

    if exists b with finalize_score(b) > tau_final:
        return finalize(best such b)

    choose action u from {continue, split, stop, finalize}
        using supervisor over {(s_b, v_b)} and budget B

    apply u to update A
    decrement B by incurred cost

return finalize(argmax_b v_b)
```

---

## 22) Where the actual gain will come from

With a cheap model, most of the gain will not come from “deeper thinking.” It will come from:

* **diversification at the right moments**
* **early killing of bad branches**
* **using verification to prevent confident nonsense**
* **reusing traces to learn which branch states are promising**

That is why the branch-summary design and utility estimation matter more than fancy tree mechanics.

---

If you want, I can now turn this into a concrete code skeleton with Python classes for `Branch`, `Supervisor`, `Summarizer`, and `ValueModel`, plus example JSON prompts for worker and supervisor.
