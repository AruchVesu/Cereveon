"""Training-completion surface.

This package owns the persistence + HTTP layer for the training-XP
feature that replaced the user-visible Elo display on the Android Home
screen.  A "completion" is a single verified solve event credited to a
player; the running total is the ``Player.training_xp`` counter that
the Home Level/XP kicker reads.

Layout
------
* ``models``  — ``TrainingCompletion(Base)`` ORM + source-type constants.
* ``router``  — ``POST /training/solve`` HTTP handler.

What the caller submits
-----------------------
The endpoint trusts the caller's claim that a solve happened — engine
verification is the caller's responsibility (Phase 3 will run a move-
verify check before posting).  Idempotency by
``(player_id, source_type, source_ref)`` prevents the same puzzle from
being credited twice if the client retries.

XP is a fixed ``+10`` per verified solve at Phase 2; later phases may
make this variable by source type or difficulty.  The value lives in
one place — ``llm.seca.training.models.XP_PER_SOLVE`` — so the curve can
change without sweeping every test.
"""
