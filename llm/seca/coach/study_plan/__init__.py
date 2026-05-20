"""Per-mistake study-plan surface (LLM coaching agent v1, phase 1 scaffold).

When ``/game/finish`` identifies a ``FirstMistake`` for the player, the
``CoachAgent`` (``llm.seca.coach.study_plan.agent``) generates a small
3-puzzle plan rooted in that one mistake, spread over a week:

* day 0 (immediately) — the exact mistake position
* day 3 — a library variant on the same theme (phase 3+)
* day 7 — a second library variant (phase 3+)

Phase 1 scope (this commit)
---------------------------
The agent is a STUB: all three puzzles point at the mistake FEN+UCI,
theme is ``"generic"``, and the LLM-written verdict is empty.  The
data model, dedup contract, scheduling, and HTTP endpoint shape are
locked here so phases 2-4 can light up the verdict text, the library
lookup, and the Android Home-screen card without re-litigating the
plumbing.

Trust posture
-------------
No LLM calls in phase 1.  No engine calls.  Pure deterministic
plumbing on top of the already-validated ``FirstMistake``.  Phase 2
adds a Mode-2-validator-gated LLM verdict.  At every phase the
verify-replay + /training/solve endpoints remain the trust anchors
for actually crediting XP — the study plan only *schedules* puzzles,
it doesn't bypass any existing verification.

Wire-name note
--------------
The study-plan wire field on /coach/plan/today is named ``today_puzzle``
not ``today_mistake``.  Unlike the legacy ``biggest_mistake`` field on
/game/finish (which kept its name across the picker-semantics flip for
backward compat), this surface is brand-new — no clients are decoding it
yet, so we get to name it after what it actually is.
"""
