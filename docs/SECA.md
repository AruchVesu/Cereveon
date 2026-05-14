# SECA — Self-Evolving Coaching Architecture

SECA is the thin adaptation layer under `llm/seca/`. It wraps two fixed
components — **Stockfish** (the chess engine, source of truth for
position evaluation) and **DeepSeek** (the language model, used only as
a language realizer for engine-derived facts) — with a small decision
layer that adapts to individual players without retraining either base
model.

For HTTP schemas see [`API_CONTRACTS.md`](API_CONTRACTS.md); for the
Mode-2 explainer pipeline downstream of SECA see
[`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## What SECA is, concretely

Three pieces of state per player, all closed-form updateable:

- **Skill state** — rating (float), confidence (float ∈ [0, 1]),
  weakness vector (skill → weight), and a small player embedding.
  Refreshed deterministically from each completed game by
  `seca/skills/updater.SkillUpdater`. No gradient steps; no neural
  retraining.
- **Bandit weights** — per-player, per-action LinUCB sufficient
  statistics `(A, b)` stored in the `bandit_weights` table. Updated
  in closed form by `seca/brain/bandit/decision.record_observation`
  (`A ← A + xxᵀ, b ← b + r·x`). Action space is the five coaching
  outcomes the controller can choose from: `NONE`, `REFLECT`,
  `DRILL`, `PUZZLE`, `PLAN_UPDATE`.
- **Context vector** — a 6-element feature vector
  (rating, confidence, accuracy, three weakness aggregates) built
  per game by `seca/brain/bandit/context_builder.build_context_vector`.

Together these make a contextual bandit with the standard LinUCB
update rule, plus a deterministic skill tracker on the side. The
"adaptation" is that update. Base intelligence — Stockfish and the
LLM — never changes.

---

## The loop

The per-game flow on `POST /game/finish`:

```
1. Input
   The request carries the finished game's PGN, the player's self-
   reported accuracy and weaknesses, and the game result.  Auth
   resolves the player; the loop reads the player's current state
   (rating, confidence, skill vector, embedding) from Postgres.

2. Action selection
   PostGameCoachController picks a deterministic action from the
   five-action set above.  When SECA_USE_BANDIT_COACH=1 is set, the
   LinUCB head in seca/brain/bandit/decision.select_action overrides
   the controller's choice; otherwise the controller's pick is what
   the user sees.

3. Output
   seca/coach/executor.CoachExecutor renders the chosen action into
   the response payload (title, description, type-specific content).

4. Feedback signal
   The rating delta from SkillUpdater + the request's accuracy +
   the request's weaknesses together form the reward signal.

5. Online update
   - SkillUpdater refreshes rating, confidence, skill vector, and
     embedding for the player row.
   - bandit_decision.record_observation updates the LinUCB
     sufficient statistics (A, b) per game.  Closed-form only.
   - ExperienceStore logs the (context, action, reward) tuple to
     bandit_experiences for offline analysis.

6. Loop repeats
   Subsequent /game/finish calls read the updated state.
   GET /auth/me returns the current rating/confidence to the
   client.
```

Step 2 is the only stochastic step (UCB1 exploration when the bandit
is in user-visible mode); every other step is deterministic given its
inputs.

### Warm-up before user-visible

`record_observation` runs on **every** `/game/finish` regardless of the
`SECA_USE_BANDIT_COACH` flag. So the bandit accumulates real reward
signals from real games even while the user sees the deterministic
controller's choice. By the time the flag is flipped in production,
the LinUCB weights have been calibrated against actual gameplay rather
than starting from cold (`A = I`, `b = 0`). This is the warm-up-then-
flip design; see `seca/events/router._apply_bandit_decision` for the
flag check.

---

## Trust property of the reward signal

The reward signal in step 4 comes directly from the Android client's
self-reported `accuracy` and `weaknesses` fields on `/game/finish`.
The server validates the request schema (accuracy ∈ [0, 1], weaknesses
is a dict with ≤ 50 keys, PGN ≤ 100k chars and parses as a valid game)
but **does not independently recompute accuracy from the submitted
PGN**. A modded client can therefore:

- inflate `accuracy` toward 1.0, which feeds `build_context_vector`
  (the third feature) and shifts every subsequent bandit context
  toward the high-accuracy regime;
- under-report `weaknesses`, shrinking the weakness-aggregate features
  and biasing action selection away from DRILL / PLAN_UPDATE.

The mitigation — server-side shallow Stockfish re-analysis of the PGN
to derive ground-truth accuracy and a canonical weakness classification
— is planned and tracked separately. Until it lands, the SECA reward
signal is **only as trustworthy as the client**, and the bandit's
context vector is correspondingly trust-bounded.

This is the only place the trust boundary discipline of
[`ARCHITECTURE.md`](ARCHITECTURE.md) (LLM is untrusted, engine truth is
trusted) does NOT extend to: the engine is the source of truth for
positions, but the SECA loop's reward signal is currently sourced from
the client, not from the engine.

---

## Live runtime layers

Directories under `llm/seca/` that participate in the live request
path:

| Layer | Module | Responsibility |
|-------|--------|----------------|
| **auth** | `seca/auth/` | Register / login / sessions / JWT + sliding refresh + rotation header. |
| **events** | `seca/events/` | `POST /game/finish` — stores GameEvent, runs SkillUpdater, dispatches to PostGameCoach. |
| **storage** | `seca/storage/` | SQLAlchemy ORM for `games`, `moves`, `explanations`, `repertoire`. |
| **skills** | `seca/skills/` | `SkillUpdater` — translates a finished GameEvent into rating / confidence / skill-vector / embedding deltas (step 5 auxiliary side). |
| **adaptation** | `seca/adaptation/` | Per-session ELO drift for skill-assessment games. Deterministic, in-memory. |
| **coach** | `seca/coach/` | `PostGameCoachController` (deterministic baseline) + `CoachExecutor` (renders the chosen action) + the Mode-2 chat / live-move / explain pipelines. |
| **analytics** | `seca/analytics/` | `AnalyticsLogger` + training recommendations from accumulated weakness counts. |
| **analysis** | `seca/analysis/` | `HistoricalAnalysisPipeline` — read-only per-player roll-up over recent games. |
| **brain** (allowlisted) | `seca/brain/bandit/{context_builder, experience_store, decision}` + `seca/brain/{models, training/models}` | Context builder, LinUCB head, experience store, plus SQLAlchemy schema modules. Everything else under `brain/` is dormant. |
| **learning** (allowlisted) | `seca/learning/{player_embedding, outcome_tracker, skill_update}` | Player embedding encoder + outcome tracker + skill-state utilities. |
| **inference** | `seca/inference/` | `POST /seca/explain` — full ESV → RAG → LLM → validators pipeline. |
| **engines** | `seca/engines/stockfish/` | Stockfish process pool + FEN move cache. |
| **safety** | `seca/safety/` | Freeze guard — runtime enforcement of the no-retraining policy. |
| **runtime** | `seca/runtime/` | `SAFE_MODE` constant + `assert_safe()` import-time gate. |

---

## What's dormant on disk and why

After the 2026-05-14 dormant-cluster cleanup pass (PR #140), the
surviving dormant code under `seca/` falls into two categories:

**Test-paired** — kept on disk because deleting requires retiring a
CI regression test in `test_bug_regressions.py`:

- `brain/meta/meta_bandit.py` — BUG-11
- `brain/bandit/contextual_bandit.py` — BUG-4a, BUG-4b
- `brain/bandit/global_bandit.py` — BUG-10
- `brain/bandit/online_update.py` — referenced by the keyword scan in
  `freeze.py`; not directly tested
- `learning/{online_learner, causal_engine, causal_impact,
  credit_assignment, performance, pipeline, trainer}.py` — referenced
  by `test_security_hardening.py` and other freeze-guard fixtures
- `skills/{trainer, skill_graph, skill_update}.py` — referenced by
  BUG-3 and adjacent tests
- `adapt.py` — BUG-9
- `curriculum/{reward, spacing}.py` — BUG-1, BUG-2

**Operational-tool-paired** — referenced via subprocess by the
standalone `seca/seca_doctor.py` diagnostic; the child Python process
runs them in isolation and never crosses the live FastAPI process's
freeze guard:

- `brain/world_model/{train_regression.py, world_model.pkl}`
- `brain/data/{build_world_model_dataset.py, world_model_dataset.csv}`

All surviving dormant code is unreachable at runtime under
`SAFE_MODE=True` and trips the freeze guard if imported into a live
process anyway. The "quarantine, not deletion" policy applies to
files that still have regression coverage; everything else has been
deleted in the Sprint 2 / PR #140 sweeps.

If a contributor wants to revive any of this code for live use, the
path is: add the relevant module to the freeze guard's allowlist
(`ALLOWED_BRAIN_MODULES` for brain paths; the source-keyword scan
will still apply), document the determinism guarantee, and write
tests that pin it. No silent re-enablement.

---

## Freeze guard

`seca/safety/freeze.py` enforces the no-retraining policy at process
startup via three independent checks:

1. **Brain-tree allowlist.** Anything under `llm.seca.brain.*` that
   is not on `ALLOWED_BRAIN_MODULES` is forbidden. The allowlist is
   intentionally tiny: SQLAlchemy schema modules plus the three
   observation-only LinUCB helpers (`context_builder`,
   `experience_store`, `decision`).

2. **Forbidden module-name parts.** Substring matches against module
   names used by historical or hypothetical adaptive components
   (`brain.rl`, `brain.bandit.online`).

3. **Forbidden source patterns.** Anchored regex patterns over module
   source text — PyTorch (`optimizer.step`, `loss.backward`,
   `import torch`, `nn.Module`), sklearn online learners
   (`.partial_fit(`), custom bandit updates (`bandit.update`,
   `bandit.save`), `def train(...)` definitions, ML-receiver
   `.update(...)` calls. Patterns are anchored to specific shapes so
   the scan covers the full `llm.*` tree without false-positives on
   generic Python idioms like `dict.update`.

Plus three defensive guards:

- `_assert_safe_world_model(world_model)` — strict type-identity check
  (`type(world_model) is SafeWorldModel`, not isinstance). Rejects
  both same-named imposters AND subclasses overriding `predict_next`.
- `_assert_safe_mode_locked()` — `SAFE_MODE=False` with
  `SECA_ENV=prod` is a hard crash; `SAFE_MODE=False` in dev is
  allowed but logged.
- `_assert_no_background_tasks()` — `SECA_ENABLE_ONLINE_LEARNING=1`
  is treated as a deliberate bypass attempt and crashes the process.

The guard runs once at FastAPI lifespan startup. A per-request
verifier with the same structural checks (`verify_runtime_safety`)
exists in `freeze.py` but is **not currently wired to any route** —
`GET /seca/status` returns the module-level `SAFE_MODE` flag only.
Either wiring the verifier in (the originally-intended design) or
deleting it (it has no live caller) is tracked as a follow-up
cleanup.

What the guard *doesn't* block: the lightweight closed-form updates
that the bandit and skill tracker perform every game. Those are the
adaptation. The guard's job is to ensure nothing heavier — gradient
descent, neural retraining, online weight fitting — ever runs in the
live process.

`test_safety_freeze.py` pins every check against intentional
violations (39 test cases).

---

## API surface

| Endpoint | SECA layer(s) it touches | Loop step |
|----------|--------------------------|-----------|
| `POST /auth/{register,login,logout}` | `auth/` | (auth, not the loop) |
| `GET /auth/me` / `PATCH /auth/me` | `auth/router.py`; PATCH writes back into `auth/models.Player` | 1 (state read) / 5 (manual profile refresh) |
| `POST /game/start` | `storage/repo.create_game` | (lifecycle) |
| `POST /game/finish` | `events/router.py` → `events/storage.EventStorage` → `skills/updater.SkillUpdater` → `coach/postgame_controller` → `coach/executor` → `analytics/training_recommendations` | 1 → 4 → 5 → 2 → 3 per game |
| `POST /game/{id}/checkpoint` | `storage/repo.checkpoint_game` | (cross-device resume) |
| `GET /game/active` | `storage/repo.get_active_game` | (cross-device resume) |
| `GET /game/history` | `events/storage.EventStorage.get_recent_games` | (read-only history) |
| `GET /repertoire` | `storage/repo.list_repertoire` (+ default fallback) | (study material) |
| `POST /seca/explain` | `inference/router.py` → ESV → RAG → LLM → validators | (Mode-2 explainer, not the SECA loop) |
| `POST /chat`, `POST /chat/stream` | `coach/chat_pipeline.py` (Mode-2 explainer) | (Mode-2, not the SECA loop) |

Authenticated endpoints depend on `seca/auth/router.get_current_player`,
which validates the session AND stashes a pending `X-Auth-Token`
rotation on `request.state`. The `commit_pending_auth_rotation`
middleware commits the rotation only on 2xx responses, so a 5xx that
followed a successful auth check does not invalidate the caller's
token.

---

## References

- `CLAUDE.md` — rule 3 (autonomous RL prohibited).
- `docs/ARCHITECTURE.md` — Mode-2 trust boundary; deterministic
  fallback; forbidden changes.
- `docs/API_CONTRACTS.md` — endpoint schemas.
- `docs/THREAT_MODEL.md` — adversary model and mitigations.
- `llm/seca/safety/freeze.py` — the enforcement layer.
- `llm/seca/brain/bandit/decision.py` — the LinUCB substrate.
- `llm/tests/test_safety_freeze.py` — invariants pinned against
  violations.
