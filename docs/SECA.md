# SECA — Self-Evolving Coaching Architecture (v1)

SECA is the framework underneath `llm/seca/` — a feedback loop that
adapts to individual users without retraining the underlying neural
networks.  This doc describes what SECA v1 *is*, walks the canonical
six-step loop, and pins the loop's live-vs-dormant status in this
implementation against the framework's reference description.

For HTTP schemas see [`API_CONTRACTS.md`](API_CONTRACTS.md); for the
LLM Mode-2 explainer (downstream of SECA) see
[`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## The framework in one paragraph

SECA defines a third path between static AI (strong but
non-adaptive) and self-improving AI (powerful but unstable).  The
underlying intelligence — chess engine, language model, recommender
— stays fixed.  Adaptation lives in a thin **decision layer**:
contextual bandits, lightweight embeddings, deterministic skill
trackers.  Every interaction observes a state, selects an action,
records a reward, and updates the decision-layer components in
real time.  No gradient descent over large models, no
self-modifying weights, no uncontrolled feedback loops.

The result is a system that adapts immediately, costs almost
nothing in compute, and is interpretable end-to-end: every action
choice can be traced to a state + a reward history.

---

## The v1 algorithm — six-step loop

```
                ┌───────────────────────────────┐
                │ 1. Input                      │
                │   state + user profile        │
                │   (rating, confidence,        │
                │    embedding)                 │
                └──────────────┬────────────────┘
                               ▼
                ┌───────────────────────────────┐
                │ 2. Action selection           │
                │   contextual bandit           │
                │   argmax_a Q(state, profile, a)│
                └──────────────┬────────────────┘
                               ▼
                ┌───────────────────────────────┐
                │ 3. Output                     │
                │   action + context →          │
                │   user-facing response        │
                └──────────────┬────────────────┘
                               ▼
                ┌───────────────────────────────┐
                │ 4. Feedback signal            │
                │   numerical reward            │
                │   (outcome / engagement /     │
                │    error rate)                │
                └──────────────┬────────────────┘
                               ▼
                ┌───────────────────────────────┐
                │ 5. Online update              │
                │   bandit incorporates         │
                │   (state, action, reward);    │
                │   profile (rating, conf,      │
                │   embedding) refreshed.       │
                │   No retraining, no gradient  │
                │   descent over base model.    │
                └──────────────┬────────────────┘
                               ▼
                ┌───────────────────────────────┐
                │ 6. Loop repeats               │
                │   next interaction            │
                └───────────────────────────────┘
```

The constraint that makes the loop tractable: **base intelligence is
held fixed**.  In this codebase that's Stockfish (engine) and the
LLM (Mode-2 explainer).  Adaptation never touches their weights.

---

## What's live in this implementation

The chess coach implements parts of the loop today and intentionally
defers others.  Mapping each step against the canonical algorithm:

| Step | What v1 prescribes | What this codebase ships |
|------|-------------------|--------------------------|
| **1. Input** | State + user profile (rating, confidence, embedding) | **Live.**  `seca/auth/models.Player` carries `rating`, `confidence`, `skill_vector_json`, `player_embedding`.  `seca/brain/bandit/context_builder.build_context_vector()` assembles a 6-element feature vector from rating + confidence + accuracy + weaknesses. |
| **2. Action selection** | Contextual bandit over a domain action space | **Live in shadow-warm-up mode by default; user-visible when `SECA_USE_BANDIT_COACH=1`.**  The LinUCB decision head in `seca/brain/bandit/decision.py` is wired into `/game/finish` via `seca/events/router._apply_bandit_decision`.  By default its `record_observation` runs every game (LinUCB weights warm up from real reward signals) but the user sees the deterministic `PostGameCoachController`'s pick.  When the env flag is set, `select_action`'s UCB1 choice replaces the controller's choice in the response.  The older pickle-based substrate at `seca/brain/bandit/contextual_bandit.py` remains dormant and is kept only for regression tests. |
| **3. Output** | Action + context → user-facing response | **Live.**  `seca/coach/executor.CoachExecutor` renders the chosen action into a `coach_content` payload returned by `/game/finish`. |
| **4. Feedback signal** | Numerical reward from the interaction | **Live.**  Per-game `accuracy`, `weaknesses`, and the rating delta from `SkillUpdater` together form the reward signal logged to `bandit_experiences`. |
| **5. Online update** | Bandit + auxiliary refresh; no retraining | **Live, including the bandit weights.**  `seca/skills/updater.SkillUpdater` refreshes rating, confidence, skill vector, and player embedding (`PlayerEmbeddingEncoder`) per game.  `seca/brain/bandit/decision.record_observation` updates the LinUCB sufficient statistics (closed form `A ← A + xxᵀ`, `b ← b + r·x` — no gradient step, no optimiser state) per game.  `seca/brain/bandit/experience_store.ExperienceStore` logs the same (context, action, reward) tuple to the `bandit_experiences` table for offline analysis. |
| **6. Loop repeats** | Next interaction picks up the refreshed profile | **Live.**  Subsequent `/game/finish` calls read the updated player row; `/auth/me` returns the current state to the client. |

In short: every step of the loop is implemented.  The bandit-as-
decision-maker is in **shadow-warm-up** mode by default — its LinUCB
weights update from real games but the user sees the deterministic
controller's action — and becomes user-visible when
`SECA_USE_BANDIT_COACH=1` is set.  The warm-up-then-flip design
means that by the time someone enables the bandit's selection in
production, the policy has already been calibrated against real
reward signals.  Both modes sit inside SECA v1's "lightweight
decision-layer adaptation" envelope (closed-form weight update, no
gradient descent over a learned model).

There is one further self-imposed constraint specific to this
project: **no continuous-feedback retraining of any neural model**,
period.  The framework permits lightweight online updates (bandit
weights, embeddings); this implementation goes further and requires
that the embedding refresh be deterministic too (no gradient steps
in the live runtime, ever).  See "Freeze guard" below.

---

## Live runtime layers

The directories under `llm/seca/` that participate in the live
request path:

| Layer | Module | Responsibility |
|-------|--------|----------------|
| **auth** | `seca/auth/` | Register / login / sessions / JWT issuance + sliding refresh.  Token lifecycle (incl. the `X-Auth-Token` rotation header). |
| **events** | `seca/events/` | `POST /game/finish` — stores GameEvent, runs SkillUpdater, dispatches to PostGameCoach. |
| **storage** | `seca/storage/` | Raw-sqlite tables for `games`, `moves`, `explanations`, `repertoire`. |
| **skills** | `seca/skills/` | `SkillUpdater` — translates a finished GameEvent into rating / confidence / skill-vector / embedding deltas (step 5 of the loop, auxiliary side). |
| **adaptation** | `seca/adaptation/` | Per-session ELO drift for skill-assessment games — fixed quality-delta table, in-memory state per player.  Deterministic. |
| **coach** | `seca/coach/` | `PostGameCoachController` (deterministic baseline action — user-visible by default; overridden by `bandit/decision` when `SECA_USE_BANDIT_COACH=1`) + `CoachExecutor` (renders the chosen action). |
| **analytics** | `seca/analytics/` | `AnalyticsLogger` (event log) + training recommendations derived from accumulated weakness counts. |
| **analysis** | `seca/analysis/` | `HistoricalAnalysisPipeline` — deterministic per-player roll-up over recent games.  Read-only. |
| **brain** (allowlisted only) | `seca/brain/bandit/{context_builder,experience_store,decision}` | These three modules participate in the loop today: `context_builder` assembles the feature vector, `decision` is the LinUCB head (warm-up by default, selection behind `SECA_USE_BANDIT_COACH`), `experience_store` logs each tuple for offline analysis.  Everything else under `brain/` is dormant. |
| **learning** (allowlisted only) | `seca/learning/player_embedding` | Embedding encoder + JSON serialisation, used by SkillUpdater. |
| **api** | `seca/api/` | Routers + middleware (X-API-Key, shared rate limiter). |
| **safety** | `seca/safety/` | Freeze guard — single runtime enforcement of the no-retraining rule. |
| **runtime** | `seca/runtime/` | `safe_mode.py` — `SAFE_MODE = True` constant + `assert_safe()` import-time gate. |

---

## What's dormant and why

Several `seca/` directories are *not* in the live path: `world_model/`,
`world/`, `henm/`, `closed_loop/`, `optim/`, `evolution/`, `policy/`,
`memory/`, plus most of `brain/` (everything not in the allowlist).
These hold research code for **earlier substrates of the decision
layer** (now superseded by `bandit/decision.py`) plus
**not-permitted-in-v1 self-improving variants** beyond the framework's
constraints:

- `seca/world_model/model.SkillDynamicsModel` — a 2-layer MLP that
  predicts `skill_t+1` from `(skill, action)`.  Would be the dynamics
  model for a counterfactual planner.
- `seca/brain/bandit/contextual_bandit` — an earlier pickle-backed
  LinUCB substrate that pre-dates the SQLite-backed
  `bandit/decision.py`.  Not on the freeze allowlist; referenced
  only by `test_bug_regressions.py` for legacy-class regression
  coverage.
- `seca/brain/bandit/online_update`, `seca/learning/online_learner` —
  the gradient steps that would refit the bandit / embedding
  encoder online.  These are what the framework's "no gradient
  descent over large models" rule explicitly excludes.
- `seca/closed_loop/`, `seca/evolution/`, `seca/optim/` — beyond
  v1's constraints (these explore self-improving variants the
  framework's v1 spec rules out by design).

If a contributor needs to revive any of this for the bandit-decision
step (the framework-permitted next milestone), the path is: move
the relevant module out of the freeze guard's blast radius, add
the new live module to the brain allowlist, document its
determinism guarantees, and write tests that pin them.  No silent
re-enablement.

---

## Freeze guard

`seca/safety/freeze.py` enforces the no-retraining rule at startup.
Three independent checks:

1. **Brain-tree allowlist.**  Anything under `llm.seca.brain.*` not
   on the allowlist is forbidden.  Allowlist is intentionally tiny:
   schema modules + the three modules (`context_builder`,
   `experience_store`, `decision`) the live loop actually uses.
   `decision` is permitted on the strength of its closed-form
   LinUCB update (no gradient step) — see the inline comment in
   `freeze.py:ALLOWED_BRAIN_MODULES` for the policy boundary.
2. **Forbidden module-name parts.**  Substring matches against names
   used by historical adaptive components (e.g. `brain.rl`,
   `brain.bandit.online`).
3. **Forbidden source keywords.**  Substring matches against module
   *source text*: `optimizer.step`, `loss.backward`, `.partial_fit(`,
   `train(`, `bandit.update`, etc.  Catches gradient updates anywhere
   in the seca tree even if a module is renamed around the
   allowlist.

Plus three defensive guards:

- `assert_safe_world_model(world_model)` — only the `SafeWorldModel`
  stub (a no-op `predict_next` that returns the input state
  unchanged) is allowed to be the live world model.
- `assert_safe_mode_locked()` — `SAFE_MODE` (in
  `seca/runtime/safe_mode.py`) is resolved from the `SECA_SAFE_MODE`
  env var with a default of `True`.  At startup, `SAFE_MODE=False`
  with `SECA_ENV=prod` is a hard crash; with `SECA_ENV != prod` it
  is allowed but logged as a warning so the dev configuration cannot
  be mistaken for normal operation.  The guard is needed because
  the dormant adaptive code paths gated by `if not SAFE_MODE:` use
  *lazy* imports inside route handlers — they don't show up in the
  startup module scan.
- `assert_no_background_tasks()` — `SECA_ENABLE_ONLINE_LEARNING=1`
  is treated as a deliberate bypass attempt and crashes the process.

`test_safety_freeze.py` (24 cases) pins every check against
intentional violations.

What the guard *doesn't* block: the lightweight updates v1 permits.
`SkillUpdater`'s rating / confidence / embedding refresh runs every
game; `bandit/decision.record_observation` updates the LinUCB
sufficient statistics (`A`, `b`) every game; the bandit experience
store writes every game.  The framework v1 explicitly distinguishes
"incremental decision-layer update" (allowed) from "gradient descent
on a learned model" (not allowed),
and the guard's keyword list is calibrated against the latter.

---

## Where the loop wires into the API

| Endpoint | SECA layer(s) it touches | Loop step |
|----------|--------------------------|-----------|
| `POST /auth/{register,login,logout}` | `auth/router.py`, `auth/service.py`, `auth/tokens.py` | (auth, not the loop) |
| `GET /auth/me` / `PATCH /auth/me` | `auth/router.py`; PATCH writes back into `auth/models.Player` | 1 (state read) / 5 (manual profile refresh) |
| `POST /game/start` | `storage/repo.create_game` | (lifecycle) |
| `POST /game/finish` | `events/router.py` → `events/storage.EventStorage` (game event) → `skills/updater.SkillUpdater` (rating / embedding refresh) → `coach/postgame_controller` (action selection) → `coach/executor` (output rendering) → `analytics/training_recommendations` | 1 → 4 → 5 → 2 → 3, per game |
| `POST /game/{id}/checkpoint` | `storage/repo.checkpoint_game` | (cross-device resume) |
| `GET /game/active` | `storage/repo.get_active_game` | (cross-device resume) |
| `GET /game/history` | `events/storage.EventStorage.get_recent_games` | (read-only history) |
| `GET /repertoire` | `storage/repo.list_repertoire` (+ default fallback in `server.py`) | (study material) |
| `POST /chat`, `POST /chat/stream` | upstream of SECA — handled by the LLM coach layer in `server.py` (Mode-2 explainer; see `ARCHITECTURE.md`) | (Mode-2, not the SECA loop) |

Authenticated endpoints depend on
`seca/auth/router.get_current_player`, which both validates the
session AND attaches the `X-Auth-Token` refresh header.  Sliding
session expiry extension lives in
`seca/auth/service.AuthService.get_player_by_session`.

---

## The framework beyond chess

SECA v1 is domain-agnostic.  The same six-step loop applies to:

- **Adaptive learning platforms** — state = current lesson + student
  profile; action = next exercise; reward = mastery delta.
- **Developer tooling** — state = project + code context; action =
  suggestion / refactor; reward = accept / reject signal.
- **Cognitive training** — state = recent performance + cognitive
  profile; action = next drill; reward = improvement on a held-out
  benchmark.
- **Dynamic difficulty in games** — state = player + recent
  performance; action = enemy parameters / level layout; reward =
  retention / engagement.
- **Productivity / habit formation** — state = recent actions +
  user profile; action = next prompt / nudge; reward = completion
  signal.
- **Health coaching** — state = vitals + history; action =
  workout / nutrition / sleep prescription; reward = adherence +
  progress.

The core invariant — base intelligence stays fixed, adaptation lives
in the decision layer, every action is interpretable — survives
across every domain.

---

## Why v1's constraints are features

SECA v1 deliberately rules out autonomous training, self-modifying
models, and uncontrolled feedback loops.  These are not limitations
to be patched out in v2; they are what makes v1 deployable.  In
return for the constraint, you get:

- **Deployability.**  A v1 system can ship to production today; a
  fully self-improving variant cannot.
- **Auditability.**  Every action choice is a function of a state
  + a logged reward history.  No opaque weight updates entangle
  the explanation.
- **Stability.**  No risk of catastrophic forgetting, weight
  divergence, or regression on previously-handled cases.
- **Cost.**  No retraining infrastructure, no gradient compute, no
  large-model checkpointing.

The framework's bet is that **most "self-improving" capabilities
people actually want are achievable inside the v1 envelope** — the
adaptation users notice is the rapid, real-time, per-interaction
kind, not the slow weight-update-and-redeploy kind.

---

## References

- The framework article: *SECA v1: A Practical Framework for Adaptive Intelligence*
- `CLAUDE.md` — rule #3 (autonomous RL prohibited)
- `docs/ARCHITECTURE.md` — Forbidden Changes; engine + Mode-2 layer
- `docs/API_CONTRACTS.md` — endpoint schemas
- `llm/seca/safety/freeze.py` — the enforcement layer
- `test_safety_freeze.py` — invariants pinned against violations
